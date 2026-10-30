"""The test bench (#134): Glowcast against a real Hue light and a camera.

Glowcast runs in a local Home Assistant per working copy (in WSL, from this
working copy) with the screen source and the real Hue output on the bench's
own entertainment area. The screen helper serves test patterns (pattern mode)
or captures a full-screen window on the monitor the camera sees (screen mode).
A USB webcam watches the monitor and the bench lights. Every run writes a
report (Markdown and JSON, numbers only) to ~/.cache/glowcast-bench/reports/.

Usage (from the repository root, in WSL on the bench's Windows computer):
    python3 scripts/bench/run.py [--suite basic|extended|loose]
                                 [--mode pattern|screen|both]
                                 [--dry-run] [--keep-images] [--wait SECONDS]
                                 [--config FILE]
    python3 scripts/bench/run.py snapshot [--config FILE]
    python3 scripts/bench/run.py pair-bridge --host ADDRESS [--force] [--config FILE]

Exit codes: 0 passed, 1 a step failed, 2 usage or configuration error,
3 skipped: the bridge streams another area, 4 skipped: another bench run
holds the lock, 5 skipped: another app streams the TV's area, 6 the bench
itself could not complete the run, 7 the calibration failed (a light or the
monitor not found, no usable exposure, too much ambient light). See scripts/README.md.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import fcntl
import json
import os
import signal
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import report as rp  # noqa: E402

HOME = Path.home() / ".cache" / "glowcast-bench"
REPORTS = HOME / "reports"
SNAPSHOTS = HOME / "snapshots"
# One bench, one bridge: one lock for every working copy.
LOCK = HOME / "bench.lock"
CONFIGS = [
    HERE / "config.local.yaml",
    Path.home() / ".config" / "glowcast-bench" / "config.local.yaml",
]

PLAN = {
    "pattern": [
        "start the helper with the cycle pattern (red, green, blue, white, black,\n"
        "    left-red-right-blue, letterbox)",
        "switch on: the bench area streams within the limit",
        "per colour: the camera sees it on each light; the delay per change",
        "black (all zero): the colours hold, the glow (if the wait allows), then the\n"
        "    lights are released and back (camera)",
        "left-red-right-blue: the lights are claimed again; each light its side\n"
        "    (skipped when the channels share x)",
        "long black glow: skipped (all zero is no picture; checked in screen mode)",
        "switch off: area stops, lights back (camera, bridge)",
        "switch on, stop the helper: tv_off, area stops, lights back",
    ],
    "screen": [
        "show a full-screen window on the monitor, start the helper on that monitor",
        "self-check: the helper's frame is the window's red; near-black is not\n"
        "    all zero",
        "switch on: the bench area streams",
        "red, green, blue, white: the colours, and the delay from the monitor to\n"
        "    the light",
        "short near-black: the colours hold",
        "long near-black: the colours hold on black, then the warm glow; the\n"
        "    area keeps streaming",
        "left-red-right-blue (skipped when the channels share x)",
        "more colour changes for the delay",
        "switch off: area stops, lights back; switch on, stop the helper: tv_off,\n"
        "    lights back",
    ],
    "extended": [
        "diagnostics contain no keys, addresses, ids or names",
        "no picture through a locked screen: skipped (unattended)",
        "screen mode: brightness, intensity and update rate changed while\n"
        "    syncing, without a new handshake",
        "screen mode: red and blue pure at brightness 20 % and 80 %",
        "screen mode: brightness changed while off is stored; Glowcast stays off",
    ],
    # Loose lights (#9): loose_suite.py; pattern mode only.
    "loose": [
        "the calibration as above (the camera lights, through the bench area)",
        "start the helper with the cycle pattern",
        "the local Home Assistant with a proxy light per loose light, under a\n"
        "    stub `hue` entry (one Hue network) or a stub `matter` entry (one\n"
        "    Thread network), mirroring the owner's light; Glowcast's loose-lights\n"
        "    output on them, stored by entity registry id",
        "the bench area does not stream (the bridge ignores commands to the\n"
        "    lights of a streaming area): checked before and during the steps",
        "each loose light's state in the owner's Home Assistant before the switch-on",
        "switch on: Glowcast syncs",
        "per colour (red, green, blue, white, letterbox): each loose light shows\n"
        "    it (a camera light with the camera, the others by their state in the\n"
        "    owner's Home Assistant)",
        "left-red-right-blue: each loose light the colour of its band (skipped for\n"
        "    a light whose band takes from both halves)",
        "black (all zero): released after the no-picture wait, each light back",
        "the rate (the local HA's light.turn_on calls): per light its interval,\n"
        "    per network at most 4 commands in any second, the fade = the actual\n"
        "    interval",
        "switch off: each loose light back (owner state; camera)",
        "switch on, stop the helper: tv_off, each loose light back",
    ],
}
END = (
    "At the end, also after Ctrl+C or a failure: the bench Glowcast off, the bench\n"
    "lights set back to their state from before the run (checked with the camera),\n"
    "the TV's Glowcast back on if the run switched it off, the workers and the\n"
    "local Home Assistant stopped."
)


def load_config(path: Path | None) -> tuple[dict[str, Any], Path]:
    import yaml

    candidates = [path] if path else CONFIGS
    for candidate in candidates:
        if candidate and candidate.exists():
            return yaml.safe_load(candidate.read_text()) or {}, candidate
    raise SystemExit(
        "No bench config: copy scripts/bench/config.example.yaml to "
        "scripts/bench/config.local.yaml (or ~/.config/glowcast-bench/) and fill it in."
    )


def ensure_environment() -> None:
    """Run in the demo's environment (Home Assistant, aiohttp, PyYAML)."""
    import local_ha

    python = local_ha.python()
    if Path(sys.prefix).resolve() == python.parent.parent.resolve():
        return
    local_ha.install()
    os.execv(str(python), [str(python), str(Path(__file__).resolve()), *sys.argv[1:]])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "command",
        nargs="?",
        default="run",
        choices=["run", "snapshot", "pair-bridge"],
    )
    parser.add_argument(
        "--suite", choices=["basic", "extended", "loose"], default="basic"
    )
    parser.add_argument("--mode", choices=["pattern", "screen", "both"], default="both")
    parser.add_argument(
        "--dry-run", action="store_true", help="print the steps, touch nothing"
    )
    parser.add_argument(
        "--keep-images", action="store_true", help="keep camera images of the run"
    )
    parser.add_argument(
        "--wait", type=float, default=0, help="wait this long for another run's lock"
    )
    parser.add_argument("--config", type=Path)
    parser.add_argument("--host", help="pair-bridge: the bridge's address")
    parser.add_argument(
        "--force", action="store_true", help="pair-bridge: replace an existing key file"
    )
    args = parser.parse_args()
    if args.command == "pair-bridge":
        ensure_environment()
        sys.exit(pair_bridge(args))
    modes = ["pattern", "screen"] if args.mode == "both" else [args.mode]
    if args.suite == "loose":
        if args.mode == "screen":
            print(
                "Configuration error: the loose suite runs in pattern mode only",
                file=sys.stderr,
            )
            sys.exit(rp.EXIT_USAGE)
        modes = ["pattern"]
    if args.dry_run:
        print(f"Dry run: suite {args.suite}, modes {', '.join(modes)}.")
        print("Nothing is touched.")
        if args.suite == "loose":
            print("\nloose lights (pattern mode):")
            for step in PLAN["loose"]:
                print(f"  - {step}")
            print("\nNeeds `loose_lights:` in the config (config.example.yaml).")
            print("\n" + END)
            print(
                "The loose lights are set back too, and their states in the\n"
                "owner's Home Assistant compared with the ones from before the run."
            )
            return
        for mode in modes:
            print(f"\n{mode} mode:")
            for step in PLAN[mode]:
                print(f"  - {step}")
        if args.suite == "extended":
            print("\nextended:")
            for step in PLAN["extended"]:
                print(f"  - {step}")
        print("\n" + END)
        return
    ensure_environment()
    try:
        cfg, _path = load_config(args.config)
        keys, token = check_config(cfg, modes, args.suite)
    except (SystemExit, ConfigError) as err:
        print(f"Configuration error: {err}", file=sys.stderr)
        sys.exit(rp.EXIT_USAGE)
    HOME.mkdir(parents=True, exist_ok=True)
    with open(LOCK, "w") as lock:
        if not take_lock(lock, args.wait):
            print("skipped: another bench run holds the lock", file=sys.stderr)
            sys.exit(rp.EXIT_LOCKED)
        if args.command == "snapshot":
            sys.exit(asyncio.run(snapshot(cfg)))
        sys.exit(
            asyncio.run(run(cfg, keys, token, args.suite, modes, args.keep_images))
        )


class ConfigError(Exception):
    """The configuration is incomplete or a file it names cannot be read."""


def check_config(
    cfg: dict[str, Any], modes: list[str], suite: str = "basic"
) -> tuple[dict[str, str], str]:
    """Check the configuration; return the bench's Hue keys and the HA token."""
    if not isinstance(cfg, dict):
        raise ConfigError("the file is not a mapping")
    for key in ("bridge_keys", "bench_area_id", "home_assistant", "lights", "helper"):
        if not cfg.get(key):
            raise ConfigError(f"`{key}` is missing")
    ha = cfg["home_assistant"]
    for key in ("url", "token_file"):
        if not ha.get(key):
            raise ConfigError(f"`home_assistant.{key}` is missing")
    lights = cfg["lights"]
    if not isinstance(lights, dict):
        raise ConfigError("`lights` must map a name to an entity_id and a region")
    for name, light in lights.items():
        if not isinstance(light, dict) or not str(
            light.get("entity_id", "")
        ).startswith("light."):
            raise ConfigError(f"`lights.{name}.entity_id` must be a light")
        if not _box(light.get("region")):
            raise ConfigError(f"`lights.{name}.region` must be [x, y, w, h]")
    if "screen" in modes:
        if not _box(cfg.get("monitor")):
            raise ConfigError("`monitor` must be [left, top, width, height]")
        if cfg.get("monitor_region") is not None and not _box(cfg["monitor_region"]):
            raise ConfigError("`monitor_region` must be [x, y, w, h]")
    if suite == "loose":
        import loose

        try:
            loose.parse(cfg)
        except ValueError as err:
            raise ConfigError(str(err)) from None
    helper = cfg["helper"]
    if not str(helper.get("pairing_code", "")).isdigit() or not helper.get("port"):
        raise ConfigError("`helper` needs a `port` and a 6-digit `pairing_code`")
    try:
        keys = json.loads(Path(cfg["bridge_keys"]).expanduser().read_text())
        token = _read_token(ha["token_file"])
    except (OSError, ValueError) as err:
        raise ConfigError(f"cannot read a key file ({type(err).__name__})") from None
    if not isinstance(keys, dict) or not all(
        keys.get(k) for k in ("host", "app_key", "client_key")
    ):
        raise ConfigError("the bridge key file needs host, app_key and client_key")
    if not token:
        raise ConfigError("the token file is empty")
    return keys, token


def _box(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 4
        and all(isinstance(v, int) for v in value)
    )


def pair_bridge(args: argparse.Namespace) -> int:
    """Pair the bench's own Hue key (link button) and write the key file.

    Writes {"host", "app_key", "client_key"} with mode 600 to the config's
    `bridge_keys` file; prints nothing secret. Never replaces an existing
    file without --force.
    """
    try:
        cfg, _path = load_config(args.config)
        target = Path(cfg["bridge_keys"]).expanduser()
    except (SystemExit, KeyError, TypeError) as err:
        print(f"Configuration error: {err}", file=sys.stderr)
        return rp.EXIT_USAGE
    if not args.host:
        print("Configuration error: --host is needed", file=sys.stderr)
        return rp.EXIT_USAGE
    if target.exists() and not args.force:
        print(
            f"{target} exists; --force replaces it (the old key stays on the bridge "
            "until you remove it in the Hue app).",
            file=sys.stderr,
        )
        return rp.EXIT_USAGE
    sys.path.insert(0, str(HERE.parent.parent))
    from custom_components.glowcast.outputs.hue_entertainment import async_pair

    print("Press the link button on the bridge now (30 s)...", flush=True)
    try:
        app_key, client_key = asyncio.run(async_pair(args.host, "Glowcast bench"))
    except Exception as err:  # noqa: BLE001
        print("Pairing failed: " + rp.describe_error(err), file=sys.stderr)
        return rp.EXIT_ERROR
    target.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps({"host": args.host, "app_key": app_key, "client_key": client_key})
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as file:
        file.write(data)
    os.chmod(target, 0o600)
    print(f"Paired; the bench's key is in {target} (mode 600).")
    return 0


def take_lock(lock: Any, wait: float) -> bool:
    deadline = time.monotonic() + wait
    while True:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(2)


async def snapshot(cfg: dict[str, Any]) -> int:
    """One camera frame with the configured regions drawn, saved locally."""
    import winproc

    SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    path = SNAPSHOTS / f"snapshot-{datetime.now(UTC):%Y%m%d-%H%M%S}.png"
    regions = {name: light["region"] for name, light in cfg["lights"].items()}
    if cfg.get("monitor_region"):
        regions["monitor"] = cfg["monitor_region"]
    import suite

    worker = winproc.Worker(
        "camera",
        [
            winproc.find_uv(cfg.get("uv") or None),
            "run",
            "--no-project",
            "-q",
            *suite.CAMERA_PACKAGES,
            "python",
            "scripts/bench/win/camera.py",
            "snapshot",
            winproc.to_windows(path),
            "--regions",
            json.dumps(regions),
            "--settings",
            json.dumps(cfg.get("camera", {})),
        ],
    )
    await worker.start()
    try:
        event = await worker.wait_event(
            lambda e: e["event"] in ("snapshot", "error"), 300
        )
    finally:
        await worker.stop(quit_command=None)
    if event["event"] != "snapshot":
        print("The camera could not be read.", file=sys.stderr)
        return rp.EXIT_ERROR
    print(f"Saved {path}")
    print("Region means (r, g, b, p95):", json.dumps(event.get("r")))
    print(
        "Delete the image when you have drawn the regions; never commit or attach it."
    )
    return 0


async def run(
    cfg: dict[str, Any],
    keys: dict[str, str],
    token: str,
    suite_name: str,
    modes: list[str],
    keep_images: bool,
) -> int:
    import aiohttp
    import clients
    import local_ha
    import suite
    import winproc

    started = datetime.now(UTC)
    folder = REPORTS / f"{started:%Y%m%d-%H%M%S}"
    folder.mkdir(parents=True, exist_ok=True)
    images = folder / "images" if keep_images else None
    if images:
        images.mkdir()
    report = rp.Report(
        suite=suite_name, modes=modes, started=f"{started:%Y-%m-%d %H:%M} UTC"
    )
    bench = suite.Bench(cfg, suite_name, report, images, keys)
    bench.tokens.append(token)
    bench.owner_token = token
    ha_cfg = cfg["home_assistant"]
    loop = asyncio.get_running_loop()
    aborted = False

    async with aiohttp.ClientSession() as session:
        bench.session = session
        bench.owner = clients.OwnerHA(
            session,
            ha_cfg["url"],
            token,
            [light["entity_id"] for light in cfg["lights"].values()]
            + [light.entity_id for light in bench.loose],
            ha_cfg.get("tv_switch"),
        )
        bench.bridge = clients.Bridge(
            session, bench.keys["host"], bench.keys["app_key"]
        )
        bench.helper = clients.Helper(session, "127.0.0.1", int(cfg["helper"]["port"]))

        async def body() -> None:
            bench.uv = await asyncio.to_thread(winproc.find_uv, cfg.get("uv") or None)
            bench.win_temp = await asyncio.to_thread(winproc.windows_temp)
            print("Preflight: the bridge's areas...", flush=True)
            await bench.preflight()
            await bench.read_pre_run()
            print("Camera...", flush=True)
            await bench.start_camera()
            print("Calibration...", flush=True)
            await bench.calibrate()
            print(
                f"Helper ({modes[0]} mode) and the local Home Assistant...", flush=True
            )
            await bench.start_helper(modes[0])
            await bench.start_ha()
            assert bench.bridge is not None
            bench.bridge.follow(cfg["bench_area_id"])
            if suite_name == "loose":
                import loose_suite

                print("loose lights...", flush=True)
                await loose_suite.run(bench)
                return
            for mode in modes:
                print(f"{mode} mode...", flush=True)
                await (
                    bench.pattern_mode() if mode == "pattern" else bench.screen_mode()
                )

        task = asyncio.create_task(body())

        def interrupt() -> None:
            nonlocal aborted
            aborted = True
            task.cancel()

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, interrupt)
        outcome: tuple[str, int] | None = None
        try:
            await task
        except asyncio.CancelledError:
            outcome = ("aborted (Ctrl+C)", rp.EXIT_FAILED)
        except suite.Skipped as err:
            outcome = (str(err), err.exit_code)
        except suite.CalibrationError as err:
            print(f"The calibration failed: {err}", file=sys.stderr)
            report.add(rp.Step("calibration failed", "-", rp.FAIL, note=str(err)))
            outcome = ("calibration failed: " + str(err), rp.EXIT_CALIBRATION)
        except (suite.BenchError, clients.NotAllowed) as err:
            # The detail goes to the terminal; the report keeps fixed texts.
            print(f"The bench could not go on: {err}", file=sys.stderr)
            report.add(
                rp.Step(
                    "the bench could not go on",
                    "-",
                    rp.FAIL,
                    note="see the run's output",
                )
            )
            outcome = ("error: the bench could not go on", rp.EXIT_ERROR)
        except Exception:  # noqa: BLE001 - always clean up and report
            import traceback

            traceback.print_exc()
            report.add(
                rp.Step(
                    "the bench could not go on",
                    "-",
                    rp.FAIL,
                    note="an unexpected error; see the run's output",
                )
            )
            outcome = ("error: the bench could not go on", rp.EXIT_ERROR)
        finally:
            print("Cleaning up...", flush=True)
            await cleanup(bench, cfg, local_ha)
        if outcome is None:
            report.finish()
        else:
            report.finish(*outcome)
            if aborted:
                report.exit_code = rp.EXIT_FAILED
    forbidden = bench.forbidden()
    (folder / "report.json").write_text(rp.scrub(report.to_json(), forbidden))
    markdown = rp.scrub(report.to_markdown(), forbidden)
    (folder / "report.md").write_text(markdown)
    # The recorded numbers, for tuning the tolerances; local, never attached.
    try:
        (folder / "raw.json").write_text(rp.scrub(json.dumps(bench.raw()), forbidden))
    except Exception as err:  # noqa: BLE001 - the report is written already
        print("Writing raw.json failed: " + rp.describe_error(err), file=sys.stderr)
    print(markdown)
    print(f"Report: {folder / 'report.md'} (and report.json)")
    return report.exit_code


def _read_token(path: str) -> str:
    return Path(path).expanduser().read_text().strip()


async def cleanup(bench: Any, cfg: dict[str, Any], local_ha: Any) -> None:
    """Every exit path, in this order: the bench Glowcast off and its area
    inactive (stopping the local Home Assistant if need be), the lights set
    back and checked with the camera, the TV's Glowcast back on if the run
    switched it off, then everything the run started stopped.

    Each part runs on its own: an error in one is printed and never skips the
    others, so the TV's Glowcast is always switched back on.
    """
    state = {"ha_stopped": False}

    async def bench_off() -> None:
        if bench.ha_start is not None and not bench.ha_start.done():
            # Ctrl+C during the start: let it finish, then stop it below.
            with contextlib.suppress(BaseException):
                await asyncio.shield(bench.ha_start)
        if bench.ha is not None:
            await bench.ha.call(
                "switch", "turn_off", {"entity_id": bench.ha.entities["switch"]}
            )

    async def area_inactive() -> None:
        if bench.ha_start is None:
            return
        inactive = await _area_inactive(bench, cfg)
        if not inactive:
            # Stopping Home Assistant stops Glowcast and its stream.
            await asyncio.to_thread(local_ha.stop)
            state["ha_stopped"] = True
            inactive = await _area_inactive(bench, cfg)
        bench.report.add(
            rp.Step(
                "end: bench Glowcast off, area inactive",
                "-",
                rp.PASS if inactive else rp.FAIL,
                note="" if inactive else "the bench area kept streaming",
            )
        )
        if not inactive:
            print("The bench area is still streaming!", file=sys.stderr)

    async def workers() -> None:
        if bench.screen is not None:
            await bench.screen.stop()
        await bench.stop_helper()

    async def lights() -> None:
        if bench.pre_states:
            await _restore(bench)

    async def tv() -> None:
        if bench.tv_off_at is not None:
            await _tv_back_on(bench, cfg)

    async def camera() -> None:
        if bench.camera is not None:
            await bench.camera.stop()

    async def clients() -> None:
        # Each on its own: one that fails never leaves another open.
        for client in (bench.ha, bench.owner, bench.bridge):
            if client is None or not hasattr(client, "close"):
                continue
            try:
                await client.close()
            except Exception as err:  # noqa: BLE001
                print(
                    "Closing a connection: " + rp.describe_error(err), file=sys.stderr
                )

    async def local() -> None:
        if not state["ha_stopped"] and bench.ha_start is not None:
            await asyncio.to_thread(local_ha.stop)

    for name, part in (
        ("switching the bench Glowcast off", bench_off),
        ("checking the bench area", area_inactive),
        ("stopping the helper and the window", workers),
        ("setting the lights back", lights),
        ("switching the TV's Glowcast back on", tv),
        ("stopping the camera", camera),
        ("closing the connections", clients),
        ("stopping the local Home Assistant", local),
    ):
        try:
            await part()
        except Exception as err:  # noqa: BLE001 - every part runs
            print(f"Cleanup, {name}: " + rp.describe_error(err), file=sys.stderr)


async def _area_inactive(bench: Any, cfg: dict[str, Any]) -> bool:
    try:
        return (
            await bench.bridge.wait_status(cfg["bench_area_id"], "inactive", 15)
            is not None
        )
    except Exception:  # noqa: BLE001
        return False


async def _restore(bench: Any) -> None:
    """Set the lights back explicitly (#142) and check them with the camera."""
    report = bench.report
    name = "end: lights set back explicitly and seen back (camera)"
    try:
        start = bench.win_now()
        await bench.restore_lights()
    except Exception as err:  # noqa: BLE001
        print(
            "Setting the lights back failed: " + rp.describe_error(err),
            file=sys.stderr,
        )
        report.add(rp.Step(name, "-", rp.FAIL, note="setting the lights back failed"))
        return
    if getattr(bench, "loose", None):
        # Judged by their state, whatever the camera: their own step.
        await _loose_end_step(bench)
    if not bench.calibrated or not bench.pre_camera:
        report.add(
            rp.Step(
                name,
                "-",
                rp.SKIP,
                note="set back, not checked: the calibration did not finish",
            )
        )
        return
    if bench.camera is None or not bench.camera.running:
        print("The camera stopped before the end of the run.", file=sys.stderr)
        report.add(
            rp.Step(
                name,
                "-",
                rp.FAIL,
                note="set back, not checked: the camera stopped during the run",
            )
        )
        return
    back = await bench.wait_restored(start)
    ok = all(v is not None for v in back.values())
    numbers: dict[str, Any] = {
        f"{bench.label[k]} back after s": v for k, v in back.items()
    }
    with contextlib.suppress(Exception):
        numbers.update(await bench.bridge_restore_numbers())
    report.add(rp.Step(name, "-", rp.PASS if ok else rp.FAIL, numbers=numbers))
    await bench.save_image("after-run")


async def _loose_end_step(bench: Any) -> None:
    """The step for the loose lights at the end: always in the report."""
    try:
        ok, numbers = await _loose_restored(bench)
    except Exception as err:  # noqa: BLE001 - the step must be reported
        print(
            "Reading the loose lights failed: " + rp.describe_error(err),
            file=sys.stderr,
        )
        ok, numbers = False, {}
    bench.report.add(
        rp.Step(
            "end: loose lights set back explicitly, state as before the run",
            "-",
            rp.PASS if ok else rp.FAIL,
            numbers=numbers,
            note="" if ok else "a light not back, or its state not read",
        )
    )


async def _loose_restored(
    bench: Any, within: float | None = None, every: float = 1.0
) -> tuple[bool, dict[str, Any]]:
    """The loose lights' states in the owner's HA as before the run, read
    until they are (Hue reports with a lag) or `within` has passed. A light
    whose state cannot be read counts as not verified."""
    import loose

    within = bench.th.restore_within_s if within is None else within
    numbers: dict[str, Any] = {}
    ok = True
    for light in bench.loose:
        before = bench.pre_states.get(light.entity_id)
        if before is None:
            continue
        label = f"{bench.loose_label[light.index]} state as before the run"
        deadline = time.monotonic() + within
        result = "not verified"
        while True:
            try:
                back, _ = loose.restored(
                    before, await bench.owner.state(light.entity_id)
                )
                result = "yes" if back else "no"
            except Exception as err:  # noqa: BLE001 - recorded, not raised
                print(
                    "Reading a loose light failed: " + rp.describe_error(err),
                    file=sys.stderr,
                )
                result = "not verified"
            if result == "yes" or time.monotonic() >= deadline:
                break
            await asyncio.sleep(every)
        numbers[label] = result
        ok &= result == "yes"
    return ok, numbers


async def _tv_back_on(bench: Any, cfg: dict[str, Any]) -> None:
    """Switch the TV's Glowcast back on and read it back."""
    switch = cfg["home_assistant"]["tv_switch"]
    state = None
    try:
        await bench.owner.call("switch", "turn_on", {"entity_id": switch})
        for _ in range(20):
            state = (await bench.owner.state(switch))["state"]
            if state == "on":
                break
            await asyncio.sleep(0.5)
    except Exception as err:  # noqa: BLE001
        print(
            "Switching the TV's Glowcast back on failed: " + rp.describe_error(err),
            file=sys.stderr,
        )
    ok = state == "on"
    bench.report.facts["TV stream interrupted for s"] = round(
        time.time() - bench.tv_off_at, 1
    )
    bench.report.add(
        rp.Step(
            "end: the TV's Glowcast back on",
            "-",
            rp.PASS if ok else rp.FAIL,
            note="" if ok else "switch it on by hand",
        )
    )
    if not ok:
        print(
            "The TV's Glowcast is NOT back on: switch it on by hand.", file=sys.stderr
        )


if __name__ == "__main__":
    main()
