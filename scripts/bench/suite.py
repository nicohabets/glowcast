"""The bench's steps: drive Glowcast in the local Home Assistant, watch the
bridge and the camera, and judge with report.py.

Clocks: the camera frames, the helper's pattern changes and the screen
window's pictures are on the Windows clock; Home Assistant's events and the
bridge's polls are on the WSL clock. `Bench.offset` (Windows minus WSL,
measured with pings to the camera worker) converts between them where a
comparison needs it; the delays use Windows times only.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import getpass
import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import aiohttp
import clients
import local_ha
import loose
import report as rp
import winproc
from report import FAIL, PASS, SKIP, Step

# Glowcast's own timing from the working copy (core/ imports no Home Assistant).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from custom_components.glowcast.core.light_state import BLACK_HOLD  # noqa: E402
from custom_components.glowcast.core.pipeline import NO_PICTURE_WAIT  # noqa: E402

TIMING = rp.Timing(no_picture_wait=NO_PICTURE_WAIT, black_hold=BLACK_HOLD)
CAMERA_PACKAGES = ["--with", "opencv-python==5.0.0.93", "--with", "numpy==2.5.4"]
CYCLE = ("red", "green", "blue", "white", "black", "left-red-right-blue", "letterbox")
# What each helper pattern should show on the lights.
PATTERN_COLOURS = {
    "red": "red",
    "green": "green",
    "blue": "blue",
    "white": "white",
    "letterbox": "green",
}
NEAR_BLACK = "#101010"  # black to Glowcast (under its black level 32), not all zero
SCREEN_SEQUENCE: list[tuple[str, float, str]] = [
    # (picture, seconds, what it checks)
    ("#ff0000", 5, "red"),
    ("#00ff00", 5, "green"),
    ("#0000ff", 5, "blue"),
    ("#ffffff", 5, "white"),
    (NEAR_BLACK, TIMING.short_black, "short-black"),
    ("#ff0000", 5, "red"),
    (NEAR_BLACK, TIMING.long_black, "long-black"),
    ("split #ff0000 #0000ff", 6, "left-red-right-blue"),
    ("#00ff00", 5, "green"),
    ("#ff0000", 5, "red"),
    ("#0000ff", 5, "blue"),
    ("#ffffff", 5, "white"),
]
SETTLE = 2.0  # seconds after a change before a colour counts as settled
SAME_X = 0.2  # channels closer than this in x cannot show left and right
# The exposures the calibration tries (log2 seconds, DirectShow).
EXPOSURES = (-11.0, -10.0, -9.0, -8.0, -7.0, -6.0, -5.0)


def iso_to_epoch(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


class BenchError(Exception):
    """The bench itself could not go on (not a failure of Glowcast)."""


class CalibrationError(Exception):
    """The calibration failed: a light or the monitor not found, no usable
    exposure, or too much ambient light. The detail goes to the terminal."""


class Skipped(Exception):
    """The run is skipped (another area streams), with an exit code."""

    def __init__(self, message: str, exit_code: int) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class Bench:
    def __init__(
        self,
        cfg: dict[str, Any],
        suite: str,
        report: rp.Report,
        keep_images: Path | None,
        keys: dict[str, str],
    ) -> None:
        self.cfg = cfg
        self.suite = suite
        self.report = report
        self.keep_images = keep_images
        self.th = rp.Thresholds(**cfg.get("thresholds", {}))
        self.lights: dict[str, dict[str, Any]] = cfg["lights"]
        self.regions = {name: light["region"] for name, light in self.lights.items()}
        if cfg.get("monitor_region"):
            self.regions["monitor"] = cfg["monitor_region"]
        self.keys = keys
        # Lights are "light 1", "light 2" ... in reports, never their config names.
        self.label = {name: f"light {i}" for i, name in enumerate(self.lights, 1)}
        self.session: aiohttp.ClientSession | None = None
        self.owner: clients.OwnerHA | None = None
        self.bridge: clients.Bridge | None = None
        self.helper: clients.Helper | None = None
        self.ha: clients.LocalHA | None = None
        self.camera: winproc.Worker | None = None
        self.screen: winproc.Worker | None = None
        self.helper_proc: winproc.Worker | None = None
        self.uv = ""
        self.win_temp = ""
        self.offset = 0.0
        self.cal: dict[str, rp.Calibration] = {}
        self.pre_camera: dict[str, rp.RGB] = {}
        self.pre_states: dict[str, dict[str, Any]] = {}
        self.pre_bridge: dict[str, dict[str, Any]] = {}
        self.light_rids: list[str] = []
        self.channel_xs: list[float] = []
        self.app_id = ""
        self.tv_off_at: float | None = None
        self.screen_index: int | None = None
        self.calibrated = False
        self.helper_mode: str | None = None
        # The local Home Assistant's start (a thread), kept so that a Ctrl+C
        # during it can wait for it and stop it.
        self.ha_start: asyncio.Future[Any] | None = None
        # Screen-mode changes timed by the window instead of the camera.
        self.window_timed = 0
        # The bench area and the bridge's entertainment services (left/right).
        self.bench_area: dict[str, Any] = {}
        # Each light's side from the bridge ("left"/"right"), or None, and why.
        self.sides: dict[str, str] | None = None
        self.sides_note = "the calibration did not get that far"
        # The exposure from the config (the pre-run picture) and the chosen one.
        self.configured_exposure = float(cfg.get("camera", {}).get("exposure", -8))
        self.exposure: float | None = None
        # Both Home Assistant tokens (the product owner's and the local one).
        self.tokens: list[str] = []
        # Picture changes on the Windows clock: [mode, picture, time].
        self.changes: list[list[Any]] = []
        # Loose lights (#9, suite `loose`): the config, their labels, their
        # proxies in the local HA (index -> entity id, registry entry) and
        # the owner's token for the proxies (written into the private storage).
        self.loose: list[loose.LooseLight] = (
            loose.parse(cfg) if suite == "loose" else []
        )
        self.loose_label: dict[int, str] = loose.labels(self.loose, self.label)
        self.proxies: dict[int, dict[str, Any]] = {}
        self.owner_token = ""

    # ---- the private values the report must never contain -------------
    def raw(self) -> dict[str, Any]:
        """The run's recorded numbers (local only): camera means, picture changes,
        Glowcast's status and the area's status, all with their times."""
        return {
            "offset": self.offset,
            "calibration": {k: dataclasses.asdict(v) for k, v in self.cal.items()},
            "pre_camera": self.pre_camera,
            "camera": [
                [line["t"], line["r"]]
                for line in (self.camera.lines if self.camera else [])
                if "r" in line and "t" in line
            ],
            "changes": self.changes,
            "status": [[t, k, s] for t, k, s, _ in (self.ha.events if self.ha else [])],
            "area": [
                [t, s["status"]]
                for t, s in (self.bridge.samples if self.bridge else [])
            ],
        }

    def forbidden(self) -> list[str]:
        ha = self.cfg["home_assistant"]
        values = [
            self.keys.get("host", ""),
            self.keys.get("app_key", ""),
            self.keys.get("client_key", ""),
            self.cfg.get("bench_area_id", ""),
            self.cfg.get("tv_area_id", ""),
            ha.get("url", ""),
            ha.get("tv_switch") or "",
            self.app_id,
            *[light["entity_id"] for light in self.lights.values()],
            *[light.entity_id for light in self.loose],
            *[
                str(state.get("attributes", {}).get("friendly_name") or "")
                for state in self.pre_states.values()
            ],
            *[str(p.get("id", "")) for p in self.proxies.values()],
            *self.light_rids,
            str(Path.home()),
            getpass.getuser(),
            *self.tokens,
        ]
        if self.helper is not None:
            values += [self.helper.token or "", self.helper.helper_id or ""]
        return values

    # ---- time ------------------------------------------------------------
    def win(self, wsl_time: float) -> float:
        return wsl_time + self.offset

    def wsl(self, win_time: float) -> float:
        return win_time - self.offset

    def win_now(self) -> float:
        return time.time() + self.offset

    async def sleep_until_win(self, win_time: float) -> None:
        await asyncio.sleep(max(0.0, win_time - self.win_now()))

    # ---- camera ----------------------------------------------------------
    def samples(self) -> list[rp.Sample]:
        assert self.camera is not None
        return [
            (line["t"], {k: tuple(v[:3]) for k, v in line["r"].items()})  # type: ignore[misc]
            for line in self.camera.lines
            if "r" in line and "t" in line
        ]

    def p95(self, region: str, start: float, end: float, index: int = 3) -> float:
        """The median over the frames of a region's p95 (index 3) or its share
        of clipped pixels (index 4)."""
        assert self.camera is not None
        values = [
            line["r"][region][index]
            for line in self.camera.lines
            if "r" in line and "t" in line and start <= line["t"] <= end
        ]
        return statistics.median(values) if values else float("nan")

    async def measure(self, seconds: float) -> dict[str, rp.RGB]:
        """The mean per region over the next `seconds` of camera frames."""
        start = self.win_now()
        await asyncio.sleep(seconds + 0.15)
        samples = self.samples()
        out = {}
        for region in self.regions:
            mean = rp.settled(samples, region, start, start + seconds)
            if mean is None:
                raise BenchError("the camera delivered no frames")
            out[region] = mean
        return out

    async def start_camera(self) -> None:
        self.camera = winproc.Worker(
            "camera",
            [
                self.uv,
                "run",
                "--no-project",
                "-q",
                *CAMERA_PACKAGES,
                "python",
                "scripts/bench/win/camera.py",
                "log",
                "--regions",
                json.dumps(self.regions),
                "--settings",
                json.dumps(self.cfg.get("camera", {})),
            ],
        )
        await self.camera.start()
        started = await self.camera.wait_event(
            lambda e: e["event"] in ("started", "error"), 300
        )
        if started["event"] == "error":
            raise BenchError("the camera could not be opened")
        await asyncio.sleep(1)
        # Windows minus WSL clock, from the ping with the shortest round trip.
        best: tuple[float, float] | None = None
        for _ in range(7):
            sent = time.time()
            self.camera.send("ping")
            pong = await self.camera.wait_event(lambda e: e["event"] == "pong", 5)
            back = time.time()
            rtt = back - sent
            if best is None or rtt < best[0]:
                best = (rtt, pong["t"] - (sent + back) / 2)
        assert best is not None
        self.offset = best[1]
        fps = self._fps()
        self.report.facts["camera frames per second"] = round(fps, 1)
        self.report.facts["clock offset Windows - WSL (ms)"] = round(
            self.offset * 1000, 1
        )
        self.report.facts["clock offset round trip (ms)"] = round(best[0] * 1000, 1)

    def _fps(self) -> float:
        times = [s[0] for s in self.samples()[-60:]]
        if len(times) < 2:
            return 0.0
        return (len(times) - 1) / (times[-1] - times[0])

    async def save_image(self, name: str) -> None:
        if self.keep_images is None or self.camera is None:
            return
        path = self.keep_images / f"{name}.png"
        self.camera.send(f"snapshot {winproc.to_windows(path)}")
        with contextlib.suppress(TimeoutError, RuntimeError):
            await self.camera.wait_event(lambda e: e["event"] == "snapshot", 5)

    # ---- preflight -------------------------------------------------------
    async def preflight(self) -> None:
        assert self.bridge is not None and self.owner is not None
        self.app_id = await self.bridge.application_id()
        areas = await self.bridge.areas()
        bench = next((a for a in areas if a["id"] == self.cfg["bench_area_id"]), None)
        if bench is None:
            raise BenchError("the bench area is not on the bridge")
        self.bench_area = bench
        self.light_rids = [s["rid"] for s in bench.get("light_services", [])]
        self.channel_xs = [
            c.get("position", {}).get("x", 0.0) for c in bench.get("channels", [])
        ]
        self.report.facts["bench area channels"] = len(self.channel_xs)
        tv_active = False
        for area in areas:
            if area.get("status") != "active":
                continue
            streamer = (area.get("active_streamer") or {}).get("rid")
            if area["id"] == self.cfg["bench_area_id"] and streamer == self.app_id:
                continue  # a stale bench stream: Glowcast stops its own app's stream
            if area["id"] == self.cfg.get("tv_area_id"):
                if streamer == self.app_id:
                    raise Skipped(
                        "configuration error: the TV's area streams with the bench's "
                        "Hue key; the bench needs a key of its own",
                        rp.EXIT_USAGE,
                    )
                tv_active = True
                continue
            raise Skipped(
                "skipped: the bridge is streaming another area", rp.EXIT_OTHER_AREA
            )
        if tv_active:
            switch = self.cfg["home_assistant"].get("tv_switch")
            on = switch and (await self.owner.state(switch))["state"] == "on"
            if not on:
                raise Skipped(
                    "skipped: another app streams the TV's area "
                    "(the bench does not stop it)",
                    rp.EXIT_TV_AREA_OTHER_APP,
                )
            self.tv_off_at = time.time()
            await self.owner.call("switch", "turn_off", {"entity_id": switch})
            if (
                await self.bridge.wait_status(self.cfg["tv_area_id"], "inactive", 15)
                is None
            ):
                raise BenchError(
                    "the TV's area kept streaming after its Glowcast was switched off"
                )
            self.report.facts["TV stream interrupted"] = "yes"

    async def read_pre_run(self) -> None:
        assert self.owner is not None and self.bridge is not None
        for entity in dict.fromkeys(
            [light["entity_id"] for light in self.lights.values()]
            + [light.entity_id for light in self.loose]
        ):
            self.pre_states[entity] = await self.owner.state(entity)
        for rid in self.light_rids:
            self.pre_bridge[rid] = await self.bridge.light(rid)

    async def set_light(self, name: str, kelvin: int | None) -> None:
        """One bench light at full brightness in this white, or off (None)."""
        assert self.owner is not None
        entity = self.lights[name]["entity_id"]
        if kelvin is None:
            await self.owner.call(
                "light", "turn_off", {"entity_id": entity, "transition": 0}
            )
            return
        await self.owner.call(
            "light",
            "turn_on",
            {
                "entity_id": entity,
                "brightness": 255,
                "color_temp_kelvin": kelvin,
                "transition": 0,
            },
        )

    async def set_lights(self, kelvin: int | None) -> None:
        for name in self.lights:
            await self.set_light(name, kelvin)

    async def camera_command(self, command: str, event: str) -> dict[str, Any]:
        assert self.camera is not None
        self.camera.send(command)
        return await self.camera.wait_event(lambda e: e["event"] == event, 15)

    async def grab(self, name: str) -> None:
        assert self.camera is not None
        self.camera.send("grab " + json.dumps({"name": name, "frames": 12}))
        await self.camera.wait_event(
            lambda e: e["event"] == "grabbed" and e["name"] == name, 15
        )

    async def start_screen(self) -> None:
        if self.screen is not None:
            return
        monitor = self.cfg["monitor"]
        self.screen = winproc.Worker(
            "screen",
            [
                self.uv,
                "run",
                "--no-project",
                "-q",
                "python",
                "scripts/bench/win/screen.py",
                "--left",
                str(monitor[0]),
                "--top",
                str(monitor[1]),
                "--width",
                str(monitor[2]),
                "--height",
                str(monitor[3]),
            ],
        )
        await self.screen.start()
        await self.screen.wait_event(lambda e: e["event"] == "started", 120)

    async def stop_screen(self) -> None:
        if self.screen is not None:
            await self.screen.stop()
            self.screen = None

    async def calibrate(self) -> None:
        """Every run starts here: the lights' picture before anything is
        touched, where the lights and the monitor are, the exposure, the
        ambient light, the white, cool and dark references and each light's
        side; then the lights back, checked against that first picture."""
        await self.grab("pre-run")
        await self.locate_regions()
        await self.pick_exposure()
        await self.references()

    async def locate_regions(self) -> None:
        """Find each light by switching it on alone, and the monitor by flashing
        the window: the camera worker compares the frames (win/locate.py)."""
        assert self.bridge is not None
        kelvin = self.cfg.get("calibration_kelvin", 2700)
        monitor = "monitor" in self.regions and bool(self.cfg.get("monitor"))
        if "monitor" in self.regions and not monitor:
            self.report.calibration["monitor region"] = (
                "not located: no monitor in the config"
            )
        if monitor:
            await self.start_screen()
            await self.show("#000000")
        await self.set_lights(None)
        await asyncio.sleep(2.5)
        await self.grab("off")
        rid_of: dict[str, str | None] = {}
        for name in self.lights:
            await self.set_light(name, kelvin)
            await asyncio.sleep(2.5)
            await self.grab(f"on-{name}")
            on = [
                rid for rid in self.light_rids if (await self.bridge.light(rid))["on"]
            ]
            rid_of[name] = on[0] if len(on) == 1 else None
            await self.set_light(name, None)
            await asyncio.sleep(2.0)
            await self.grab(f"after-{name}")
        targets: dict[str, Any] = {
            name: {
                "on": f"on-{name}",
                "off": "off",
                "after": f"after-{name}",
                "size": self.regions[name][2:],
                "others": [f"on-{o}" for o in self.lights if o != name],
            }
            for name in self.lights
        }
        if monitor:
            await self.show("#ffffff")
            await asyncio.sleep(1.0)
            await self.grab("on-monitor")
            await self.show("#000000")
            await asyncio.sleep(1.0)
            await self.grab("after-monitor")
            targets["monitor"] = {
                "on": "on-monitor",
                "off": "off",
                "after": "after-monitor",
                "size": self.regions["monitor"][2:],
                "others": [f"on-{n}" for n in self.lights],
            }
            await self.stop_screen()
        located = await self.camera_command("locate " + json.dumps(targets), "located")
        found_regions: dict[str, list[int]] = {}
        missing = []
        cal = self.report.calibration
        for name, result in located["targets"].items():
            label = "monitor" if name == "monitor" else self.label[name]
            moved = rp.moved(self.regions[name], result["box"])
            cal[f"{label} found (mean change per pixel)"] = result["found"]
            cal[f"{label} contrast (to the median change)"] = result["contrast"]
            cal[f"{label} region"] = result["box"]
            cal[f"{label} moved (px)"] = moved
            if name != "monitor" and moved > self.th.warn_moved_px:
                cal[f"{label} warning"] = "moved far: check the camera"
            if (
                result["found"] < self.th.min_found
                or result["contrast"] < self.th.min_contrast
            ):
                missing.append(label)
            found_regions[name] = result["box"]
        names = list(found_regions)
        crowded = [
            (a, b)
            for i, a in enumerate(names)
            for b in names[i + 1 :]
            if rp.overlap(found_regions[a], found_regions[b]) > self.th.max_overlap
        ]
        if missing or crowded:
            print(
                f"Not found in the camera picture: {', '.join(missing) or '-'}; "
                f"regions on top of each other: {len(crowded)} "
                "(camera moved or blocked, or a light does not respond?)",
                file=sys.stderr,
            )
            raise CalibrationError("a light or the monitor was not found")
        self.regions.update(found_regions)
        await self.camera_command("regions " + json.dumps(self.regions), "regions")
        self.map_sides(rid_of, await self.bridge.entertainment())

    def map_sides(
        self, rid_of: dict[str, str | None], services: list[dict[str, Any]]
    ) -> None:
        """Left and right from the bridge: each light's x in the area, for the
        bridge light that came on with it. When the area's channels sit apart,
        a light that cannot be matched fails the calibration rather than
        quietly skipping left-red-right-blue."""
        consistent = None not in rid_of.values() and len(set(rid_of.values())) == len(
            rid_of
        )
        spread = rp.channel_spread(self.bench_area)
        cal = self.report.calibration
        cal["light, bridge light and region match"] = "yes" if consistent else "no"
        if not consistent:
            if spread >= SAME_X:
                print(
                    "Each light must turn exactly one of the area's bridge lights "
                    "on; it did not, so left and right cannot be told.",
                    file=sys.stderr,
                )
                raise CalibrationError("the lights could not be matched to the bridge")
            self.sides = None
            self.sides_note = "the area's channels sit at the same x on the bridge"
            return
        xs = {
            name: rp.channel_x(self.bench_area, services, rid)
            for name, rid in rid_of.items()
            if rid is not None
        }
        self.sides = rp.sides(xs, SAME_X)
        self.sides_note = (
            "the lights sit at the same x on the bridge"
            if any(x is not None for x in xs.values())
            else "the lights are not in the area's channels"
        )
        for name, x in xs.items():
            cal[f"{self.label[name]} channel x"] = x
            cal[f"{self.label[name]} side"] = (self.sides or {}).get(name, "-")

    def camera_command_nowait(self, command: str) -> None:
        assert self.camera is not None
        self.camera.send(command)

    async def sweep(self) -> dict[float, dict[str, tuple[rp.RGB, float, float]]]:
        """Per exposure, each light region's mean, p95 and clipped share."""
        out: dict[float, dict[str, tuple[rp.RGB, float, float]]] = {}
        for exposure in EXPOSURES:
            await self.camera_command(f"exposure {exposure}", "exposure")
            await asyncio.sleep(0.8)
            start = self.win_now()
            means = await self.measure(0.5)
            out[exposure] = {
                name: (
                    means[name],
                    self.p95(name, start, start + 0.5),
                    self.p95(name, start, start + 0.5, index=4),
                )
                for name in self.lights
            }
        return out

    async def pick_exposure(self) -> None:
        """The fixed exposure for the run (rp.choose_exposure); white balance
        stays fixed."""
        kelvin = self.cfg.get("calibration_kelvin", 2700)
        await self.set_lights(kelvin)
        await asyncio.sleep(2.5)
        white = await self.sweep()
        await self.set_lights(None)
        await asyncio.sleep(2.5)
        dark = await self.sweep()
        table = {
            e: {
                name: (
                    white[e][name][0],
                    dark[e][name][0],
                    white[e][name][1],
                    white[e][name][2],
                )
                for name in self.lights
            }
            for e in EXPOSURES
        }
        chosen, always = rp.choose_exposure(table, self.th)
        cal = self.report.calibration
        for e in EXPOSURES:
            cal[f"exposure {e:g}: white p95"] = [
                round(table[e][n][2]) for n in self.lights
            ]
            cal[f"exposure {e:g}: clipped share"] = [
                round(table[e][n][3], 2) for n in self.lights
            ]
        cal["clips at every exposure (judged on its rim)"] = (
            ", ".join(self.label[n] for n in always) or "none"
        )
        if chosen is None:
            print("No exposure keeps every light in range.", file=sys.stderr)
            raise CalibrationError("no usable exposure")
        self.exposure = chosen
        cal["exposure (log2 s)"] = chosen
        cal["white balance (K), fixed"] = self.cfg.get("camera", {}).get(
            "wb_temperature", 4000
        )

    async def set_exposure(self, exposure: float) -> None:
        await self.camera_command(f"exposure {exposure}", "exposure")
        await asyncio.sleep(0.8)

    async def references(self) -> None:
        """White (warm), cool and dark references at the chosen exposure and
        the ambient light; then the lights back, checked against the picture
        from before the run (at the configured exposure), and their picture
        at the chosen exposure as the reference for every restore."""
        assert self.exposure is not None
        await self.set_exposure(self.exposure)
        await self.set_lights(self.cfg.get("calibration_kelvin", 2700))
        await asyncio.sleep(2.5)
        white = await self.measure(1.0)
        await self.set_lights(self.cfg.get("cool_kelvin", 6500))
        await asyncio.sleep(2.5)
        cool = await self.measure(1.0)
        await self.set_lights(None)
        await asyncio.sleep(2.5)
        dark = await self.measure(1.0)
        cal = {
            name: rp.Calibration(dark=dark[name], white=white[name], cool=cool[name])
            for name in self.lights
        }
        ambient = {
            name: rp.ambient_ratio(dark[name], white[name]) for name in self.lights
        }
        for name, ratio in ambient.items():
            self.report.calibration[f"{self.label[name]} ambient / white"] = round(
                ratio, 3
            )
        too_bright = [n for n, r in ambient.items() if r > self.th.max_ambient]
        # Back to the state before the run, seen at the configured exposure.
        await self.restore_lights()
        await self.set_exposure(self.configured_exposure)
        await asyncio.sleep(2.5)
        await self.grab("restored")
        before = (
            await self.camera_command(
                "means "
                + json.dumps({"grab": "pre-run", "regions": self.light_regions()}),
                "means",
            )
        )["values"]
        after = (
            await self.camera_command(
                "means "
                + json.dumps({"grab": "restored", "regions": self.light_regions()}),
                "means",
            )
        )["values"]
        self.camera_command_nowait("forget")
        back = {
            n: rp.same_picture(before[n], after[n], self.th.same_picture)
            for n in self.lights
        }
        await self.set_exposure(self.exposure)
        first = await self.measure(1.0)
        second = await self.measure(1.0)
        steady = all(
            rp.difference(first[n], second[n], cal[n]) <= self.th.same_picture
            for n in self.lights
        )
        self.cal = cal
        self.pre_camera = {name: second[name] for name in self.lights}
        # Only now can a restore be checked with the camera.
        self.calibrated = True
        await self.save_image("pre-run")
        numbers: dict[str, Any] = {
            f"{self.label[n]} pre-run level": round(cal[n].level(self.pre_camera[n]), 3)
            for n in self.lights
        }
        numbers.update(
            {
                f"{self.label[n]} back as before the run": "yes" if back[n] else "no"
                for n in self.lights
            }
        )
        problem = (
            "too much ambient light"
            if too_bright
            else "the lights did not come back to their state before the run"
            if not all(back.values())
            else "the lights' own picture is not steady"
            if not steady
            else ""
        )
        self.report.add(
            Step(
                "calibration: camera position, exposure, ambient light, references",
                "-",
                FAIL if problem else PASS,
                numbers=numbers,
                note=problem,
            )
        )
        if too_bright:
            print(
                "Too much ambient light (daylight?): "
                + ", ".join(f"{self.label[n]} {ambient[n]:.2f}" for n in too_bright),
                file=sys.stderr,
            )
        if problem:
            raise CalibrationError(problem)

    def light_regions(self) -> dict[str, list[int]]:
        return {name: self.regions[name] for name in self.lights}

    async def restore_lights(self) -> None:
        """Put the bench lights explicitly back in their pre-run state (#142)."""
        assert self.owner is not None
        for state in self.pre_states.values():
            service, data = clients.restore_data(state)
            await self.owner.call("light", service, data)

    async def wait_restored(
        self, since_win: float, within: float | None = None
    ) -> dict[str, float | None]:
        """Per light: seconds from `since_win` until the camera sees the
        pre-run picture."""
        within = within or self.th.restore_within_s
        await self.sleep_until_win(since_win + within + 0.6)
        samples = self.samples()
        out: dict[str, float | None] = {}
        for name in self.lights:
            if name not in self.pre_camera or name not in self.cal:
                out[name] = None  # no reference: cannot be seen back
                continue
            t = rp.first_match(
                samples,
                name,
                self.pre_camera[name],
                self.cal[name],
                self.th,
                since_win,
                since_win + within + 0.6,
            )
            out[name] = None if t is None else round(max(0.0, t - since_win), 2)
        return out

    async def bridge_restore_numbers(self) -> dict[str, Any]:
        assert self.bridge is not None
        bad = 0
        for rid in self.light_rids:
            ok, _ = rp.bridge_restored(
                self.pre_bridge[rid], await self.bridge.light(rid)
            )
            bad += not ok
        return {
            "bridge lights as before": (
                f"{len(self.light_rids) - bad}/{len(self.light_rids)}"
            )
        }

    # ---- the helper ------------------------------------------------------
    def helper_args(self, mode: str, screen: int | None) -> list[str]:
        h = self.cfg["helper"]
        args = [
            self.uv,
            "run",
            "-q",
            "--project",
            "screen_helper",
            "glowcast-screen",
            "--name",
            "BenchPC",
            "--host",
            "127.0.0.1",
            "--port",
            str(h["port"]),
            "--non-interactive",
            "--pairing-code",
            str(h["pairing_code"]),
            "--config-dir",
            self.win_temp + "\\glowcast-bench\\helper",
            "--json",
        ]
        if mode == "pattern":
            args += [
                "--pattern",
                "cycle",
                "--pattern-interval",
                str(h.get("pattern_interval", TIMING.pattern_interval)),
            ]
        elif screen is not None:
            args += ["--screen", str(screen)]
        return args

    async def start_helper(self, mode: str) -> None:
        await self.stop_helper()
        port = int(self.cfg["helper"]["port"])
        if await asyncio.to_thread(winproc.pid_listening, port) is not None:
            # Not ours: the bench never stops a process it did not start.
            raise BenchError("something else listens on the helper's port on Windows")
        env = {
            "UV_PROJECT_ENVIRONMENT": self.win_temp + "\\glowcast-bench\\helper-venv"
        }
        screen = self.screen_index if mode == "screen" else None
        self.helper_proc = winproc.Worker(
            "helper", self.helper_args(mode, screen), env=env
        )
        await self.helper_proc.start()
        started = await self.helper_proc.wait_event(
            lambda e: e["event"] in ("started", "error"), 300
        )
        if started["event"] == "error":
            print(f"The helper did not start: {started.get('reason')}", file=sys.stderr)
            raise BenchError("the helper did not start")
        if mode == "screen" and self.screen_index is None:
            left, top = self.cfg["monitor"][:2]
            match = [
                s["index"]
                for s in started.get("screens", [])
                if s["left"] == left and s["top"] == top
            ]
            if not match:
                raise BenchError("the helper does not list the configured monitor")
            self.screen_index = match[0]
            if started.get("screen") != self.screen_index:
                await self.start_helper(mode)
                return
        self.helper_mode = mode
        assert self.helper is not None
        if self.helper.token is None:
            await self.helper.pair(str(self.cfg["helper"]["pairing_code"]))

    async def stop_helper(self) -> float | None:
        """Stop the run's own helper; the WSL time it was gone, or None.

        Only the helper this run started: its port was free before the start,
        so what listens there now is its Python under its uv.exe.
        """
        if self.helper_proc is None:
            return None
        port = int(self.cfg["helper"]["port"])
        pid = await asyncio.to_thread(winproc.pid_listening, port)
        stopped = None
        if pid is not None:
            await asyncio.to_thread(winproc.taskkill, pid)
            stopped = time.time()
        await self.helper_proc.stop(quit_command=None)
        self.helper_proc = None
        self.helper_mode = None
        return stopped

    # ---- the local Home Assistant ----------------------------------------
    async def start_ha(self) -> None:
        assert self.helper is not None and self.session is not None
        port = int(self.cfg["helper"]["port"])
        helper = {
            "helper_id": self.helper.helper_id,
            "host": "127.0.0.1",
            "port": port,
            "token": self.helper.token,
        }
        settings = {"brightness": 80, "intensity": 100, "update_rate": "high"}
        proxies: dict[str, dict[str, Any]] | None = None
        if self.loose:
            # Glowcast's loose-lights output on proxy lights (stubs/proxy.py).
            entry = local_ha.loose_glowcast_entry(helper=helper, settings=settings)
            proxies = {}
            for kind in loose.KINDS:
                lights = [
                    {
                        "unique_id": light.unique_id,
                        "name": light.name,
                        "source": light.entity_id,
                    }
                    for light in self.loose
                    if light.kind == kind
                ]
                if lights:
                    proxies[kind] = local_ha.proxy_block(
                        self.cfg["home_assistant"]["url"], self.owner_token, lights
                    )
        else:
            entry = local_ha.glowcast_entry(
                helper=helper,
                app_key=self.keys["app_key"],
                client_key=self.keys["client_key"],
                area_id=self.cfg["bench_area_id"],
                settings=settings,
            )
        self.ha_start = asyncio.ensure_future(
            asyncio.to_thread(local_ha.start, self.keys["host"], entry, proxies)
        )
        try:
            inst, token = await asyncio.shield(self.ha_start)
        except SystemExit as err:
            print(f"The local Home Assistant did not start: {err}", file=sys.stderr)
            raise BenchError("the local Home Assistant did not start") from None
        self.tokens.append(token)
        self.ha = clients.LocalHA(self.session, inst.url, token)
        for _ in range(60):
            found = await self.ha.find_entities(local_ha.ENTRY_ID)
            if {
                "switch",
                "status",
                "brightness",
                "intensity",
                "update_rate",
            } <= found.keys():
                break
            await asyncio.sleep(1)
        else:
            raise BenchError(
                "Glowcast's entities did not appear in the local Home Assistant"
            )
        if self.loose:
            await self.place_loose_lights()
        await self.ha.follow()

    async def place_loose_lights(self) -> None:
        """Find the proxy lights in the local HA's entity registry and store
        them, with their positions, as Glowcast's loose-lights output."""
        assert self.ha is not None
        wanted = {light.unique_id: light for light in self.loose}
        for _ in range(60):
            found = {
                entry["unique_id"]: entry
                for entry in await self.ha.registry()
                if entry.get("platform") in loose.KINDS
                and entry.get("unique_id") in wanted
            }
            if found.keys() == wanted.keys():
                break
            await asyncio.sleep(1)
        else:
            raise BenchError("the proxy lights did not appear in the local HA")
        self.proxies = {wanted[u].index: entry for u, entry in found.items()}
        output = {
            "type": "ha_lights",
            "follow": "lights",
            "lights": [
                {
                    "entity": self.proxies[light.index]["id"],
                    "x": light.x,
                    "z": light.z,
                }
                for light in self.loose
            ],
        }
        await self.ha.call(
            "glowcast_bench",
            "set_output",
            {"entry_id": local_ha.ENTRY_ID, "output": output},
        )

    # ---- switching -------------------------------------------------------
    async def switch_on(
        self, mode: str, *, name: str = "switch on: the bench area streams"
    ) -> bool:
        assert self.ha is not None and self.bridge is not None
        start = time.time()
        await self.ha.call(
            "switch", "turn_on", {"entity_id": self.ha.entities["switch"]}
        )
        active = await self.bridge.wait_status(
            self.cfg["bench_area_id"], "active", self.th.stream_within_s
        )
        status = await self.ha.wait_state(
            "status", {"streaming", "black_picture", "no_picture"}, 5
        )
        ok = active is not None and status is not None
        self.report.add(
            Step(
                name,
                mode,
                PASS if ok else FAIL,
                seconds=None if active is None else active - start,
                numbers={"limit s": self.th.stream_within_s},
                note="" if ok else "the area did not stream in time",
            )
        )
        return ok

    async def check_stop(self, mode: str, name: str, how: str) -> None:
        """Stop Glowcast (switch off, or the helper gone); check the release
        and the restore."""
        assert self.ha is not None and self.bridge is not None
        if how == "switch":
            start = time.time()
            await self.ha.call(
                "switch", "turn_off", {"entity_id": self.ha.entities["switch"]}
            )
        else:
            stopped = await self.stop_helper()
            start = stopped or time.time()
        if self.screen is not None:
            self.screen.send(
                "#000000"
            )  # the monitor dark while the camera checks the lights
        expected = "idle" if how == "switch" else "tv_off"
        status = await self.ha.wait_state("status", {expected}, 20)
        inactive = await self.bridge.wait_status(
            self.cfg["bench_area_id"], "inactive", 15
        )
        # The lights come back after the release (#142: about 1.7 s).
        released = 0.0 if inactive is None else inactive - start
        back = await self.wait_restored(
            self.win(start), released + self.th.restore_within_s
        )
        numbers: dict[str, Any] = {}
        reason_ok = True
        if how == "helper":
            attrs = (await self.ha.state("status"))["attributes"]
            reason_ok = attrs.get("stop_reason") == "tv_off"
            numbers["stop reason"] = attrs.get("stop_reason")
            numbers["switch"] = (await self.ha.state("switch"))["state"]
        if inactive is not None:
            numbers["area inactive after s"] = round(inactive - start, 2)
        for light, seconds in back.items():
            numbers[f"{self.label[light]} back after s"] = seconds
        numbers.update(await self.bridge_restore_numbers())
        ok = (
            status is not None
            and inactive is not None
            and reason_ok
            and all(v is not None for v in back.values())
        )
        problems = []
        if status is None:
            problems.append(f"status never {expected}")
        if not reason_ok:
            problems.append("wrong stop reason")
        if inactive is None:
            problems.append("area kept streaming")
        if any(v is None for v in back.values()):
            problems.append("a light did not come back (camera)")
        self.report.add(
            Step(
                name,
                mode,
                PASS if ok else FAIL,
                seconds=None if status is None else status[1] - start,
                numbers=numbers,
                note="; ".join(problems),
            )
        )

    # ---- judging colours ---------------------------------------------------
    def colour_step(
        self, mode: str, label: str, expected: str, start: float, end: float
    ) -> dict[str, rp.RGB]:
        samples = self.samples()
        means: dict[str, rp.RGB] = {}
        numbers: dict[str, Any] = {}
        ok = True
        for name in self.lights:
            mean = rp.settled(samples, name, start + SETTLE, end - 0.3)
            if mean is None:
                ok = False
                continue
            means[name] = mean
            cal = self.cal[name]
            good = rp.matches_colour(expected, mean, cal, self.th)
            ok &= good
            numbers[f"{self.label[name]} seen"] = rp.classify(mean, cal, self.th)
            numbers[f"{self.label[name]} level"] = round(cal.level(mean), 2)
            numbers[f"{self.label[name]} n"] = [
                round(v, 2) for v in cal.normalised(mean)
            ]
            if expected == "white":
                numbers[f"{self.label[name]} n cool"] = [
                    round(v, 2) for v in cal.cool_normalised(mean)
                ]
        self.report.add(
            Step(
                f"{label}: the lights show {expected}",
                mode,
                PASS if ok else FAIL,
                numbers=numbers,
                note="" if ok else "another colour than expected",
            )
        )
        return means

    def add_delays(self, key: str, delays: list[float]) -> None:
        stats = rp.delay_stats(delays)
        self.report.delays[key] = stats

    def delays_between(
        self,
        prev: dict[str, rp.RGB],
        now: dict[str, rp.RGB],
        change: float,
        end: float,
        out: dict[str, list[float]],
    ) -> None:
        samples = self.samples()
        for name in self.lights:
            if name not in prev or name not in now:
                continue
            if (
                rp.difference(prev[name], now[name], self.cal[name])
                < self.th.min_change
            ):
                continue
            t = rp.crossing(samples, name, prev[name], now[name], change, end)
            if t is not None:
                out.setdefault(name, []).append((t - change) * 1000)

    def positions_differ(self) -> bool:
        """Whether the lights' channels sit at different x (from the bridge)."""
        return self.sides is not None

    def left_right_step(self, mode: str, start: float, end: float) -> None:
        if not self.positions_differ():
            self.report.add(
                Step(
                    "left-red-right-blue: each light the colour of its side",
                    mode,
                    SKIP,
                    note=self.sides_note,
                )
            )
            return
        samples = self.samples()
        ok = True
        numbers: dict[str, Any] = {}
        assert self.sides is not None
        for name in self.lights:
            mean = rp.settled(samples, name, start + SETTLE + 2, end - 0.3)
            expected = "red" if self.sides[name] == "left" else "blue"
            good = mean is not None and rp.matches_colour(
                expected, mean, self.cal[name], self.th
            )
            ok &= good
            numbers[f"{self.label[name]} {self.sides[name]} seen"] = (
                None if mean is None else rp.classify(mean, self.cal[name], self.th)
            )
        self.report.add(
            Step(
                "left-red-right-blue: each light the colour of its side",
                mode,
                PASS if ok else FAIL,
                numbers=numbers,
            )
        )

    def hold_step(
        self, mode: str, label: str, ref: dict[str, rp.RGB], start: float, end: float
    ) -> bool:
        samples = self.samples()
        ok = True
        numbers: dict[str, Any] = {}
        for name in self.lights:
            if name not in ref:
                ok = False
                continue
            held, worst = rp.stays(
                samples, name, ref[name], self.cal[name], self.th, start, end
            )
            ok &= held
            numbers[f"{self.label[name]} largest change"] = round(worst, 3)
        self.report.add(
            Step(
                label,
                mode,
                PASS if ok else FAIL,
                seconds=end - start,
                numbers=numbers,
                note="" if ok else "the colours changed during the hold",
            )
        )
        return ok

    def streamed_throughout(self, start_wsl: float, end_wsl: float) -> tuple[bool, int]:
        """Whether the area stayed active with one streamer (no new
        handshake), and the number of polls."""
        assert self.bridge is not None
        statuses = self.bridge.statuses(start_wsl, end_wsl)
        streamers = {s["streamer"] for s in statuses}
        ok = (
            bool(statuses)
            and all(s["status"] == "active" for s in statuses)
            and len(streamers) == 1
        )
        return ok, len(statuses)

    # ---- pattern mode ------------------------------------------------------
    def pattern_changes(self) -> list[tuple[str, float]]:
        assert self.helper_proc is not None
        return [
            (e["showing"], iso_to_epoch(e["since"]))
            for e in self.helper_proc.lines
            if e.get("event") == "pattern"
        ]

    async def pattern_mode(self) -> None:
        mode = "pattern"
        interval = float(
            self.cfg["helper"].get("pattern_interval", TIMING.pattern_interval)
        )
        cycles = int(self.cfg.get("pattern_cycles", 2))
        await self.start_helper(mode)
        await self.wait_for_red(interval)
        if not await self.switch_on(mode):
            return
        # From the next red, `cycles` whole cycles, plus the red after them.
        showing, since = self.pattern_changes()[-1]
        index = CYCLE.index(showing)
        first_red = since + ((len(CYCLE) - index) % len(CYCLE)) * interval
        while first_red < self.win_now() + 1:
            first_red += len(CYCLE) * interval
        end = first_red + cycles * len(CYCLE) * interval + interval
        await self.sleep_until_win(end + 0.5)
        steps = [
            (n, s)
            for n, s in self.pattern_changes()
            if first_red - interval - 0.1 <= s < end - 0.1
        ]
        self.changes += [["pattern", n, t] for n, t in steps]
        delays: dict[str, list[float]] = {}
        prev: dict[str, rp.RGB] | None = None
        prev_name = ""
        for (name, start), nxt in zip(steps, [*steps[1:], (None, end)], strict=True):
            stop = nxt[1]
            if start < first_red - 0.1:
                # The step before the first red: only a baseline for its delay.
                prev = {
                    k: v
                    for k in self.lights
                    if (v := rp.settled(self.samples(), k, start + SETTLE, stop - 0.3))
                }
                prev_name = name
                continue
            if name in PATTERN_COLOURS:
                means = self.colour_step(mode, name, PATTERN_COLOURS[name], start, stop)
                if prev is not None and prev_name != "black":
                    self.delays_between(prev, means, start, stop, delays)
                prev = means
            elif name == "black":
                self.black_release_step(mode, prev or {}, start, stop)
                prev = None
            elif name == "left-red-right-blue":
                self.claim_step(mode, start, stop)
                self.left_right_step(mode, start, stop)
                prev = {
                    k: v
                    for k in self.lights
                    if (
                        v := rp.settled(
                            self.samples(), k, start + SETTLE + 2, stop - 0.3
                        )
                    )
                }
            prev_name = name
        all_delays = [d for values in delays.values() for d in values]
        for name, values in delays.items():
            self.add_delays(f"{mode} / {self.label[name]}", values)
        self.delay_step(mode, all_delays)
        if TIMING.glow_before_release is None:
            self.report.add(
                Step(
                    "long black: the warm glow after the hold on black",
                    mode,
                    SKIP,
                    note="the no-picture wait releases the lights before the "
                    "glow: checked in screen mode",
                )
            )
        await self.extended_steps(mode)
        await self.wait_for_colour(interval)
        await self.check_stop(
            mode, "switch off: area stops, lights back (camera)", "switch"
        )
        await self.wait_for_red(interval)
        if await self.switch_on(mode, name="switch on again"):
            await self.wait_for_colour(interval)
            await self.check_stop(
                mode,
                "helper stopped: tv_off, area stops, lights back (camera)",
                "helper",
            )

    async def wait_for_red(self, interval: float) -> None:
        """Until the cycle starts red: three colours before its black (all zero)."""
        for _ in range(int(len(CYCLE) * interval * 10) + 20):
            showing, since = self.pattern_changes()[-1]
            if showing == "red" and self.win_now() - since < 1.0:
                return
            await asyncio.sleep(0.1)

    async def wait_for_colour(self, interval: float) -> None:
        """Until the cycle has shown red, green or blue for 2 s (the lights claimed)."""
        for _ in range(int(len(CYCLE) * interval * 4) + 8):
            showing, since = self.pattern_changes()[-1]
            if (
                showing in ("red", "green", "blue")
                and 2.0 <= self.win_now() - since <= interval - 1.5
            ):
                return
            await asyncio.sleep(0.25)

    def delay_step(self, mode: str, delays: list[float]) -> None:
        stats = rp.delay_stats(delays)
        self.add_delays(f"{mode} / all lights", delays)
        ok = stats["count"] > 0 and stats["median"] <= self.th.max_delay_ms
        self.report.add(
            Step(
                "delay from picture to light",
                mode,
                PASS if ok else FAIL,
                numbers={
                    **stats,
                    **(
                        {"changes timed by the window": self.window_timed}
                        if mode == "screen"
                        else {}
                    ),
                },
                note=(
                    ("" if ok else "no delay measured or the median is over the limit")
                    or (
                        "includes the camera's own latency (about 75 ms)"
                        if mode == "pattern"
                        else "monitor and light in the same frames"
                    )
                ),
            )
        )

    def black_release_step(
        self, mode: str, prev: dict[str, rp.RGB], start: float, stop: float
    ) -> None:
        """The helper's all-zero black: the colours hold during the no-picture
        wait, then the lights are released and back."""
        assert self.ha is not None
        self.hold_step(
            mode,
            "black (all zero), during the hold on black: the colours hold",
            prev,
            start + 0.3,
            start + TIMING.release_hold_end,
        )
        released = self.ha.first("status", "no_picture", self.wsl(start))
        samples = self.samples()
        numbers: dict[str, Any] = {"no-picture wait s": TIMING.no_picture_wait}
        ok = released is not None and self.win(released) < stop
        if released is not None:
            numbers["released after s"] = round(self.win(released) - start, 2)
        assert self.bridge is not None
        statuses = self.bridge.statuses(
            self.wsl(start + TIMING.released_by), self.wsl(stop - 0.2)
        )
        inactive = bool(statuses) and all(s["status"] == "inactive" for s in statuses)
        ok &= inactive
        for name in self.lights:
            t = rp.first_match(
                samples,
                name,
                self.pre_camera[name],
                self.cal[name],
                self.th,
                start + TIMING.back_from,
                stop,
            )
            numbers[f"{self.label[name]} back after s"] = (
                None if t is None else round(t - start, 2)
            )
            ok &= t is not None
        self.black_glow_step(mode, prev, start)
        self.report.add(
            Step(
                "no picture (all-zero black past the no-picture wait): "
                "lights released and back (camera)",
                mode,
                PASS if ok else FAIL,
                numbers=numbers,
                note=""
                if ok
                else "not released, area still streaming, or a light not back",
            )
        )

    def claim_step(self, mode: str, start: float, stop: float) -> None:
        """After no picture, the first picture claims the lights again."""
        assert self.bridge is not None
        active = self.bridge.first("active", self.wsl(start))
        ok = active is not None and self.win(active) < stop
        self.report.add(
            Step(
                "picture back after no picture: the lights are claimed again",
                mode,
                PASS if ok else FAIL,
                seconds=None if active is None else self.win(active) - start,
                note="" if ok else "the area did not stream again",
            )
        )

    # ---- screen mode -------------------------------------------------------
    async def show(self, picture: str) -> float:
        assert self.screen is not None
        self.screen.send(picture)
        shown = await self.screen.wait_event(
            lambda e: e["event"] == "shown" and e["picture"] == picture, 5
        )
        self.changes.append(["screen", picture, shown["t"]])
        return shown["t"]

    async def screen_self_check(self, mode: str) -> bool:
        assert self.helper is not None
        await self.show("#ff0000")
        await asyncio.sleep(0.5)
        status, pixels = await self.helper.frame()
        red = sum(
            1
            for i in range(0, len(pixels), 3)
            if pixels[i] > 200 and pixels[i + 1] < 60 and pixels[i + 2] < 60
        ) / max(len(pixels) // 3, 1)
        await self.show(NEAR_BLACK)
        await asyncio.sleep(0.5)
        status_b, dark = await self.helper.frame()
        near_black = status_b == 200 and any(dark) and max(dark) <= 32
        ok = status == 200 and red >= 0.98 and near_black
        self.report.add(
            Step(
                "screen capture: the helper sees the window "
                "(red; near-black not all zero)",
                mode,
                PASS if ok else FAIL,
                numbers={
                    "red share": round(red, 3),
                    "near-black max": max(dark) if dark else None,
                },
                note=""
                if ok
                else "the helper does not capture the bench window as shown",
            )
        )
        return ok

    async def screen_mode(self) -> None:
        mode = "screen"
        await self.start_screen()
        await self.start_helper(mode)
        if not await self.screen_self_check(mode):
            return
        await self.show("#ff0000")
        if not await self.switch_on(mode):
            return
        await asyncio.sleep(4)
        shown: list[
            tuple[str, str, float, float]
        ] = []  # picture, kind, Windows time, seconds
        for picture, seconds, kind in SCREEN_SEQUENCE:
            t = await self.show(picture)
            shown.append((picture, kind, t, seconds))
            await self.sleep_until_win(t + seconds)
        end = self.win_now()
        await asyncio.sleep(0.5)
        delays: dict[str, list[float]] = {}
        prev: dict[str, rp.RGB] | None = None
        prev_monitor: rp.RGB | None = None
        samples = self.samples()
        for i, (_picture, kind, t, _seconds) in enumerate(shown):
            stop = shown[i + 1][2] if i + 1 < len(shown) else end
            # The change as the camera saw it on the monitor; else the window's time.
            monitor_now = (
                rp.settled(samples, "monitor", t + 1.0, stop - 0.2)
                if "monitor" in self.regions
                else None
            )
            change = t
            if monitor_now is not None and prev_monitor is not None:
                seen = rp.crossing(
                    samples, "monitor", prev_monitor, monitor_now, t - 0.1, t + 1.0
                )
                if seen is not None:
                    change = seen
                else:
                    self.window_timed += 1
            prev_monitor = monitor_now
            if kind in ("red", "green", "blue", "white"):
                means = self.colour_step(mode, kind, kind, t, stop)
                if prev is not None:
                    self.delays_between(prev, means, change, stop, delays)
                prev = means
            elif kind == "short-black":
                self.hold_step(
                    mode,
                    "short black (shorter than the hold, not all zero): colours hold",
                    prev or {},
                    t + 0.3,
                    stop - 0.05,
                )
            elif kind == "long-black":
                self.glow_step(mode, prev or {}, t, stop)
                prev = None
            elif kind == "left-red-right-blue":
                self.left_right_step(mode, t, stop)
                prev = {
                    k: v
                    for k in self.lights
                    if (v := rp.settled(samples, k, t + SETTLE, stop - 0.3))
                }
        for name, values in delays.items():
            self.add_delays(f"{mode} / {self.label[name]}", values)
        self.delay_step(mode, [d for values in delays.values() for d in values])
        await self.extended_steps(mode)
        await self.check_stop(
            mode, "switch off: area stops, lights back (camera)", "switch"
        )
        await self.show("#ff0000")
        if await self.switch_on(mode, name="switch on again"):
            await asyncio.sleep(3)
            await self.check_stop(
                mode,
                "helper stopped: tv_off, area stops, lights back (camera)",
                "helper",
            )
        await self.screen.stop()
        self.screen = None

    def black_glow_step(self, mode: str, prev: dict[str, rp.RGB], start: float) -> None:
        """All-zero black with a wait longer than the hold: the glow shows
        before the release (architecture 3.5, "No picture")."""
        window = TIMING.glow_before_release
        if window is None:
            return
        samples = self.samples()
        numbers: dict[str, Any] = {}
        ok = True
        for name in self.lights:
            glow = rp.settled(samples, name, start + window[0], start + window[1])
            if glow is None or name not in prev:
                ok = False
                continue
            cal = self.cal[name]
            ok &= rp.is_glow(glow, cal, self.th, prev[name])
            numbers[f"{self.label[name]} glow level"] = round(cal.level(glow), 3)
        self.report.add(
            Step(
                "black (all zero), after the hold: the warm glow until the release",
                mode,
                PASS if ok else FAIL,
                numbers=numbers,
                note="" if ok else "no glow before the release",
            )
        )

    def glow_step(
        self, mode: str, prev: dict[str, rp.RGB], start: float, stop: float
    ) -> None:
        """Near-black (not all zero): the hold on black, then the dim warm
        glow, still streaming."""
        assert self.ha is not None
        self.hold_step(
            mode,
            "long black, during the hold on black: the colours hold",
            prev,
            start + 0.3,
            start + TIMING.glow_hold_end,
        )
        samples = self.samples()
        numbers: dict[str, Any] = {}
        ok = True
        for name in self.lights:
            glow = rp.settled(
                samples, name, start + TIMING.glow_settled_from, stop - 0.3
            )
            if glow is None or name not in prev:
                ok = False
                continue
            cal = self.cal[name]
            good = rp.is_glow(glow, cal, self.th, prev[name])
            ok &= good
            numbers[f"{self.label[name]} glow level"] = round(cal.level(glow), 3)
            numbers[f"{self.label[name]} glow n"] = [
                round(v, 2) for v in cal.normalised(glow)
            ]
            t = rp.crossing(samples, name, prev[name], glow, start, stop)
            numbers[f"{self.label[name]} glow after s"] = (
                None if t is None else round(t - start, 2)
            )
            low, high = TIMING.glow_window
            ok &= t is not None and low <= t - start <= high
        streaming, polls = self.streamed_throughout(self.wsl(start), self.wsl(stop))
        ok &= streaming
        black = "black_picture" in self.ha.states_of(
            "status", self.wsl(start), self.wsl(stop)
        )
        ok &= black
        numbers["status black_picture"] = "yes" if black else "no"
        self.report.add(
            Step(
                "long black (not all zero): the warm glow after the hold on black",
                mode,
                PASS if ok else FAIL,
                numbers=numbers,
                note="" if ok else "no glow, wrong timing, or the stream was released",
            )
        )

    # ---- the extended suite ----------------------------------------------
    async def extended_steps(self, mode: str) -> None:
        if self.suite != "extended":
            return
        await self.diagnostics_step(mode)
        self.report.add(
            Step(
                "no picture through a locked screen (204), and back",
                mode,
                SKIP,
                note="it would lock the computer, which nobody can unlock in an "
                "unattended run; the release and the claim are checked with "
                "all-zero black in pattern mode",
            )
        )
        if mode != "screen":
            self.report.add(
                Step(
                    "settings changed while syncing",
                    mode,
                    SKIP,
                    note="needs a steady picture: checked in screen mode",
                )
            )
            return
        await self.purity_step(mode)
        await self.settings_while_syncing(mode)
        await self.setting_while_off(mode)

    async def purity_step(self, mode: str) -> None:
        """Red and blue stay pure at brightness 20 % and 80 % (#43, #129).

        Judged on the lights marked `pure: true` in the config (a lit wall; a
        spot's clipped rim cannot show purity).
        """
        name = "red and blue pure at brightness 20 % and 80 %"
        pure = [n for n, light in self.lights.items() if light.get("pure")]
        if not pure:
            self.report.add(
                Step(name, mode, SKIP, note="no light is marked pure in the config")
            )
            return
        shares: dict[tuple[str, int, str], float] = {}
        for brightness in (20, 80):
            await self.set_number("brightness", brightness)
            for picture, colour in (("#ff0000", "red"), ("#0000ff", "blue")):
                await self.show(picture)
                await asyncio.sleep(3)
                seen = await self.measure(1.0)
                for light in pure:
                    shares[(light, brightness, colour)] = rp.impurity(
                        seen[light], self.cal[light], colour
                    )
        await self.set_number("brightness", 80)
        numbers: dict[str, Any] = {}
        ok = True
        for light in pure:
            for colour in ("red", "blue"):
                low = shares[(light, 20, colour)]
                high = shares[(light, 80, colour)]
                numbers[f"{self.label[light]} {colour} other share 20 %"] = round(
                    low, 3
                )
                numbers[f"{self.label[light]} {colour} other share 80 %"] = round(
                    high, 3
                )
                ok &= low <= self.th.max_impurity and high <= self.th.max_impurity
                ok &= low <= high + self.th.impurity_margin
        self.report.add(
            Step(
                name,
                mode,
                PASS if ok else FAIL,
                numbers=numbers,
                note="" if ok else "another colour mixed in (for example warm white)",
            )
        )

    async def diagnostics_step(self, mode: str) -> None:
        assert self.ha is not None
        download = await self.ha.get(
            f"/api/diagnostics/config_entry/{local_ha.ENTRY_ID}"
        )
        # Keys, addresses and ids nowhere in the download; the home folder and
        # the user name not in Glowcast's part (Home Assistant's own system
        # information around it names them).
        local = {str(Path.home()), getpass.getuser()}
        whole = json.dumps(download)
        own = json.dumps(download.get("data"))
        found = [
            value
            for value in self.forbidden()
            if value and (value in own or (value not in local and value in whole))
        ]
        if found:
            print(f"Private values in the diagnostics: {len(found)}", file=sys.stderr)
        self.report.add(
            Step(
                "diagnostics contain no keys, addresses, ids or names",
                mode,
                PASS if not found else FAIL,
                numbers={"private values found": len(found)},
            )
        )

    async def set_number(self, key: str, value: float) -> None:
        assert self.ha is not None
        await self.ha.call(
            "number", "set_value", {"entity_id": self.ha.entities[key], "value": value}
        )

    async def set_rate(self, value: str) -> None:
        assert self.ha is not None
        await self.ha.call(
            "select",
            "select_option",
            {"entity_id": self.ha.entities["update_rate"], "option": value},
        )

    async def settings_while_syncing(self, mode: str) -> None:
        assert self.ha is not None
        await self.show("#ff0000")
        await asyncio.sleep(4)
        before = await self.measure(1.0)
        checks: list[tuple[str, Any]] = [
            ("brightness", 40),
            ("intensity", 50),
            ("update_rate", "low"),
        ]
        for key, value in checks:
            start = time.time()
            if key == "update_rate":
                await self.set_rate(value)
            else:
                await self.set_number(key, value)
            await asyncio.sleep(6 if key == "update_rate" else 3)
            diag = await self.ha.diagnostics(local_ha.ENTRY_ID)
            numbers: dict[str, Any] = {"setting": diag["settings"][key]}
            ok = str(diag["settings"][key]) == str(value)
            if key == "brightness":
                after = await self.measure(1.0)
                ratios = [
                    self.cal[n].level(after[n])
                    / max(self.cal[n].level(before[n]), 1e-3)
                    for n in self.lights
                ]
                numbers["level ratio"] = [round(r, 2) for r in ratios]
                ok &= all(0.2 <= r <= 0.85 for r in ratios)
            if key == "update_rate":
                rate = (diag.get("status") or {}).get("captures_per_second")
                numbers["captures per second"] = rate
                ok &= rate is not None and rate <= 1.5
            streaming, polls = self.streamed_throughout(start, time.time())
            restarted = (
                "starting" in self.ha.states_of("status", start, time.time())[1:]
            )
            ok &= streaming and not restarted
            numbers["bridge polls, all one stream"] = (
                f"{polls} {'yes' if streaming else 'no'}"
            )
            self.report.add(
                Step(
                    f"{key} changed while syncing: applied without a new handshake",
                    mode,
                    PASS if ok else FAIL,
                    numbers=numbers,
                )
            )
        await self.set_number("brightness", 80)
        await self.set_number("intensity", 100)
        await self.set_rate("high")
        await asyncio.sleep(2)

    async def setting_while_off(self, mode: str) -> None:
        assert self.ha is not None and self.bridge is not None
        await self.ha.call(
            "switch", "turn_off", {"entity_id": self.ha.entities["switch"]}
        )
        await self.bridge.wait_status(self.cfg["bench_area_id"], "inactive", 15)
        start = time.time()
        await self.set_number("brightness", 60)
        await asyncio.sleep(3)
        diag = await self.ha.diagnostics(local_ha.ENTRY_ID)
        switch = (await self.ha.state("switch"))["state"]
        statuses = self.bridge.statuses(start, time.time())
        stayed_off = switch == "off" and all(
            s["status"] == "inactive" for s in statuses
        )
        ok = diag["settings"]["brightness"] == 60 and stayed_off
        self.report.add(
            Step(
                "brightness changed while off: stored, Glowcast stays off",
                mode,
                PASS if ok else FAIL,
                numbers={"setting": diag["settings"]["brightness"], "switch": switch},
            )
        )
        await self.set_number("brightness", 80)
        await asyncio.sleep(1)
        await self.switch_on(mode, name="switch on after the change while off")
