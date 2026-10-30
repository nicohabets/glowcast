"""Measure how fast lights follow Home Assistant's light actions (spike #125).

Talks to Home Assistant's websocket API. It reads the lights of an area (plus
--extra lights), groups them by integration ("kind"), snapshots their state
with scene.create, sends colour updates (a slowly rotating hue at a fixed
brightness) and restores the snapshot at the end, also after an error or
Ctrl+C. The snapshot scene is a dynamic one: it is gone after an HA restart
and nothing is left behind.

Per kind it runs every rate in --rates for --seconds each, once with one
action per light per tick ("per light") and once with one action on --group
("group", only for the kind of the group's members), each without and with a
`transition` equal to the interval. Per run it records the time each action
call took, errors and timeouts, the delay until HA reports a state with the
sent colour, how many sent colours were reported, and whether that delay grows
during the run. Reported colours differ a little from the sent ones (Hue
converts to xy and maps to its gamut), so each reported hue is matched to the
nearest hue sent in the seconds before.

The table uses generic labels per kind, never entity ids or names; only
--list prints entity ids, to pick the targets.

Environment: GLOWCAST_HA_URL (for example http://homeassistant.local:8123,
or a full ws:// websocket URL) and GLOWCAST_HA_TOKEN (a long-lived access
token). Needs aiohttp (in the test requirements, see scripts/README.md).

    python scripts/try_ha_lights.py --area office --list
    python scripts/try_ha_lights.py --area office --group light.office \\
        --extra light.garden_door --kind hue
"""

import argparse
import asyncio
import contextlib
import itertools
import os
import statistics
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import aiohttp

KIND_LABELS = {
    "hue": "Hue light via HA (bridge REST API)",
    "zha": "Zigbee light via ZHA",
    "mqtt": "Zigbee light via Zigbee2MQTT (MQTT)",
    "wled": "WLED strip",
    "matter": "Matter light",
    "tradfri": "IKEA light via the TRADFRI gateway",
    "esphome": "ESPHome light",
    "tuya": "Tuya Wi-Fi light",
}
COLOR_MODES = {"hs", "xy", "rgb", "rgbw", "rgbww"}
HUE_SPEED = 18.0  # degrees per second: one turn in 20 s, whatever the rate
SATURATION = 80
BRIGHTNESS = 128
CALL_TIMEOUT = 5.0
MAX_IN_FLIGHT = 10  # skip a tick (count it as backlog) above this
MAX_FAILURES = 5  # consecutive errors or timeouts before the kind is stopped
MATCH_WINDOW = 10.0  # seconds a reported hue is matched back into
SETTLE = 3.0  # seconds after the last send to collect late reports


class KindStopped(Exception):
    """A light of this kind became unavailable or stopped answering."""


@dataclass
class Sent:
    t: float
    hue: float
    seen: bool = False


@dataclass
class Run:
    label: str
    mode: str
    rate: float
    transition: bool
    entities: set[str]
    sent: list[Sent] = field(default_factory=list)
    call_times: list[float] = field(default_factory=list)
    errors: int = 0
    timeouts: int = 0
    skipped: int = 0
    reports: int = 0
    delays: list[tuple[float, float]] = field(default_factory=list)  # (t, delay)
    failures_in_row: int = 0
    stop_reason: str | None = None
    t0: float = 0.0

    def on_state(self, entity_id: str, new_state: dict | None, now: float) -> None:
        if entity_id not in self.entities:
            return
        if new_state is None or new_state["state"] == "unavailable":
            self.stop_reason = "a light became unavailable"
            return
        hs = new_state["attributes"].get("hs_color")
        if not hs or not self.sent:
            return
        self.reports += 1
        candidates = [s for s in self.sent if 0 <= now - s.t <= MATCH_WINDOW]
        if not candidates:
            return
        best = min(candidates, key=lambda s: _hue_distance(s.hue, hs[0]))
        if _hue_distance(best.hue, hs[0]) > 15:
            return  # not one of ours (yet), for example the restored colour
        best.seen = True
        self.delays.append((now - self.t0, now - best.t))


def _hue_distance(a: float, b: float) -> float:
    d = abs(a - b) % 360
    return min(d, 360 - d)


def _ms(seconds: float) -> str:
    return f"{seconds * 1000:.0f}"


def _p95(values: list[float]) -> float:
    return sorted(values)[max(0, int(len(values) * 0.95) - 1)]


class HomeAssistant:
    """A minimal websocket client: commands and state_changed events."""

    def __init__(self, session: aiohttp.ClientSession, url: str, token: str) -> None:
        self._session = session
        if not url.startswith("ws"):
            url = url.rstrip("/").replace("http", "ws", 1) + "/api/websocket"
        self._url = url
        self._token = token
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._reader: asyncio.Task | None = None
        self.on_state: Callable[[str, dict | None, float], None] | None = None

    async def connect(self) -> None:
        self._ws = await self._session.ws_connect(self._url, max_msg_size=0)
        await self._ws.receive_json()  # auth_required
        await self._ws.send_json({"type": "auth", "access_token": self._token})
        reply = await self._ws.receive_json()
        if reply["type"] != "auth_ok":
            raise SystemExit("Home Assistant refused the token")
        self._reader = asyncio.create_task(self._read())
        await self.command({"type": "subscribe_events", "event_type": "state_changed"})

    async def close(self) -> None:
        if self._reader:
            self._reader.cancel()
        if self._ws:
            await self._ws.close()

    async def _read(self) -> None:
        assert self._ws is not None
        async for message in self._ws:
            now = time.monotonic()
            data = message.json()
            for item in data if isinstance(data, list) else [data]:
                if item["type"] == "event":
                    event = item["event"]["data"]
                    if self.on_state:
                        self.on_state(event["entity_id"], event["new_state"], now)
                elif item["type"] in ("result", "pong"):
                    future = self._pending.pop(item["id"], None)
                    if future and not future.done():
                        future.set_result(item)
        for future in self._pending.values():
            future.set_exception(ConnectionError("websocket closed"))

    async def command(self, payload: dict[str, Any]) -> Any:
        assert self._ws is not None
        msg_id = next(self._ids)
        future = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = future
        await self._ws.send_json({"id": msg_id, **payload})
        result = await future
        if not result.get("success", True):
            raise RuntimeError(result["error"].get("message", "error"))
        return result.get("result")

    async def call(self, domain: str, service: str, data: dict[str, Any]) -> None:
        await self.command(
            {
                "type": "call_service",
                "domain": domain,
                "service": service,
                "service_data": data,
            }
        )


async def discover(
    ha: HomeAssistant, area: str, extra: list[str]
) -> tuple[dict[str, str], dict[str, dict]]:
    """Return {entity_id: platform} of the area's colour lights and the states."""
    areas = await ha.command({"type": "config/area_registry/list"})
    matches = [a for a in areas if area.lower() in (a["area_id"], a["name"].lower())]
    if len(matches) != 1:
        raise SystemExit(f"--area matches {len(matches)} areas, need exactly one")
    area_id = matches[0]["area_id"]
    devices = await ha.command({"type": "config/device_registry/list"})
    device_area = {d["id"]: d["area_id"] for d in devices}
    entities = await ha.command({"type": "config/entity_registry/list"})
    states = {s["entity_id"]: s for s in await ha.command({"type": "get_states"})}
    lights: dict[str, str] = {}
    for entry in entities:
        entity_id = entry["entity_id"]
        if not entity_id.startswith("light.") or entry.get("disabled_by"):
            continue
        in_area = (entry["area_id"] or device_area.get(entry["device_id"])) == area_id
        if in_area or entity_id in extra:
            lights[entity_id] = entry["platform"]
    return lights, states


def is_colour_light(state: dict | None) -> bool:
    if state is None or state["state"] == "unavailable":
        return False
    modes = set(state["attributes"].get("supported_color_modes") or [])
    return bool(modes & COLOR_MODES)


def is_group(state: dict) -> bool:
    """A light that groups others (a Hue room or zone, an HA light group)."""
    return bool(state["attributes"].get("entity_id")) or bool(
        state["attributes"].get("is_hue_group")
    )


async def run_one(
    ha: HomeAssistant, run: Run, targets: list[str], seconds: float
) -> Run:
    interval = 1 / run.rate
    in_flight: set[asyncio.Task] = set()

    async def send(entity_id: str, hue: float) -> None:
        data: dict[str, Any] = {
            "entity_id": entity_id,
            "hs_color": [hue, SATURATION],
            "brightness": BRIGHTNESS,
        }
        if run.transition:
            data["transition"] = interval
        start = time.monotonic()
        try:
            async with asyncio.timeout(CALL_TIMEOUT):
                await ha.call("light", "turn_on", data)
        except TimeoutError:
            run.timeouts += 1
            run.failures_in_row += 1
        except (RuntimeError, ConnectionError, aiohttp.ClientError):
            run.errors += 1
            run.failures_in_row += 1
        else:
            run.call_times.append(time.monotonic() - start)
            run.failures_in_row = 0
        if run.failures_in_row >= MAX_FAILURES:
            run.stop_reason = f"{MAX_FAILURES} failed calls in a row"

    ha.on_state = run.on_state
    run.t0 = time.monotonic()
    try:
        for tick in itertools.count():
            due = run.t0 + tick * interval
            if due - run.t0 >= seconds or run.stop_reason:
                break
            await asyncio.sleep(max(0.0, due - time.monotonic()))
            hue = (tick * interval * HUE_SPEED) % 360
            if len(in_flight) + len(targets) > MAX_IN_FLIGHT:
                run.skipped += 1
                continue
            now = time.monotonic()
            run.sent.append(Sent(now, hue))
            for entity_id in targets:
                task = asyncio.create_task(send(entity_id, hue))
                in_flight.add(task)
                task.add_done_callback(in_flight.discard)
        if in_flight:
            await asyncio.wait(in_flight, timeout=CALL_TIMEOUT + 1)
        await asyncio.sleep(SETTLE)
    finally:
        ha.on_state = None
        for task in in_flight:
            task.cancel()
    if run.stop_reason:
        raise KindStopped(run.stop_reason)
    return run


def row(run: Run) -> str:
    calls = run.call_times
    delays = [d for _, d in run.delays]
    seen = sum(s.seen for s in run.sent)
    half = max((t for t, _ in run.delays), default=0) / 2
    first = [d for t, d in run.delays if t < half]
    last = [d for t, d in run.delays if t >= half]
    growth = (
        f"{_ms(statistics.median(last) - statistics.median(first)):>6}"
        if first and last
        else "     -"
    )
    return (
        f"| {run.mode:<9} | {run.rate:>4g} | {'yes' if run.transition else 'no':<3} "
        f"| {len(run.sent):>4} | {run.skipped:>4} | {run.errors:>3} "
        f"| {run.timeouts:>3} "
        f"| {_ms(statistics.median(calls)) if calls else '-':>6} "
        f"| {_ms(_p95(calls)) if calls else '-':>6} "
        f"| {run.reports:>5} | {seen:>4} ({seen / max(1, len(run.sent)):>4.0%}) "
        f"| {_ms(statistics.median(delays)) if delays else '-':>6} "
        f"| {_ms(_p95(delays)) if delays else '-':>6} | {growth} |"
    )


HEADER = (
    "| mode      | rate | tr. | sent | skip | err | t/o | call ms | call p95 "
    "| reports | colours seen | delay ms | delay p95 | growth ms |\n"
    "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"
)


async def measure_kind(
    ha: HomeAssistant,
    label: str,
    lights: list[str],
    group: str | None,
    args: argparse.Namespace,
    confirm: Callable[[str], Awaitable[None]],
) -> list[str]:
    modes = [("per light", lights)] + ([("group", [group])] if group else [])
    total = len(modes) * len(args.rates) * 2 * (args.seconds + SETTLE + args.pause)
    await confirm(
        f"{label}: {len(lights)} light(s){' and a group' if group else ''}, "
        f"about {total / 60:.0f} min"
    )
    lines = [f"\n### {label} ({len(lights)} light(s))\n", HEADER]
    try:
        for mode, targets in modes:
            for transition in (False, True):
                for rate in args.rates:
                    run = Run(label, mode, rate, transition, set(lights))
                    print(
                        f"  {mode}, {rate:g}/s, transition {transition} ...", flush=True
                    )
                    await run_one(ha, run, targets, args.seconds)
                    lines.append(row(run))
                    print(lines[-1], flush=True)
                    await asyncio.sleep(args.pause)
    except KindStopped as err:
        lines.append(f"\nStopped this kind: {err}.")
        print(lines[-1], flush=True)
    return lines


async def main(args: argparse.Namespace) -> None:
    url = os.environ["GLOWCAST_HA_URL"]
    token = os.environ["GLOWCAST_HA_TOKEN"]
    async with aiohttp.ClientSession() as session:
        ha = HomeAssistant(session, url, token)
        await ha.connect()
        try:
            await _main(ha, args)
        finally:
            await ha.close()


async def _main(ha: HomeAssistant, args: argparse.Namespace) -> None:
    pings = []
    for _ in range(10):
        start = time.monotonic()
        await ha.command({"type": "ping"})
        pings.append(time.monotonic() - start)
    print(
        f"Websocket round trip: median {_ms(statistics.median(pings))} ms "
        "(included in the call and delay times)"
    )
    lights, states = await discover(ha, args.area, args.extra)
    if args.list:
        for entity_id, platform in sorted(lights.items()):
            state = states.get(entity_id)
            print(
                f"{entity_id}: {platform}, {state and state['state']}, "
                f"colour: {is_colour_light(state)}, "
                f"group: {bool(state and is_group(state))}"
            )
        if args.group:
            group = states.get(args.group)
            members = group and group["attributes"].get("entity_id")
            print(f"group {args.group}: members {members}")
        return

    group_state = states.get(args.group) if args.group else None
    if args.group and not is_colour_light(group_state):
        raise SystemExit("--group is not an available colour light")
    kinds: dict[str, list[str]] = {}
    for entity_id, platform in sorted(lights.items()):
        state = states.get(entity_id)
        if entity_id == args.group or not is_colour_light(state) or is_group(state):
            continue
        kinds.setdefault(platform, []).append(entity_id)
    if args.kind:
        kinds = {k: v for k, v in kinds.items() if k in args.kind}
    if not kinds:
        raise SystemExit("no available colour lights to measure")
    group_kind = None
    if group_state:
        members = set(group_state["attributes"].get("entity_id") or [])
        group_kind = next((k for k, v in kinds.items() if members & set(v)), None)
        if group_kind:
            # The group's unavailable members are not measured, its others are.
            kinds[group_kind] = sorted(
                set(kinds[group_kind])
                | (
                    members
                    & {
                        e
                        for e, p in lights.items()
                        if p == group_kind and is_colour_light(states.get(e))
                    }
                )
            )

    async def confirm(what: str) -> None:
        print(f"\nNext: {what}.")
        if not args.yes:
            await asyncio.to_thread(input, "Press Enter to start, Ctrl+C to stop: ")

    snapshot = sorted(itertools.chain.from_iterable(kinds.values()))
    await ha.call(
        "scene", "create", {"scene_id": args.scene, "snapshot_entities": snapshot}
    )
    print(f"Snapshot of {len(snapshot)} light(s) taken.")
    report: list[str] = []
    try:
        for kind, kind_lights in kinds.items():
            label = KIND_LABELS.get(kind, f"{kind} light via HA")
            group = args.group if kind == group_kind else None
            if group and group_state["attributes"].get("is_hue_group"):
                label += "; group = a Hue room or zone (one bridge command)"
            report += await measure_kind(ha, label, kind_lights, group, args, confirm)
            await ha.call("scene", "turn_on", {"entity_id": f"scene.{args.scene}"})
    finally:
        await ha.call("scene", "turn_on", {"entity_id": f"scene.{args.scene}"})
        print("Snapshot restored.")
        print("\n".join(report))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--area", required=True, help="area name or id")
    parser.add_argument("--extra", nargs="*", default=[], help="lights outside it")
    parser.add_argument("--group", help="a group light over lights of the area")
    parser.add_argument("--kind", nargs="*", help="only these platforms")
    parser.add_argument("--rates", nargs="*", type=float, default=[1, 2, 5, 10])
    parser.add_argument("--seconds", type=float, default=20)
    parser.add_argument("--pause", type=float, default=3)
    parser.add_argument("--scene", default="glowcast_try_ha_lights")
    parser.add_argument("--list", action="store_true", help="list lights, no sends")
    parser.add_argument("--yes", action="store_true", help="do not wait for Enter")
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main(parser.parse_args()))
