"""The test bench (#134): Glowcast against a real Hue light and a camera.

Glowcast runs in a local Home Assistant per working copy (in WSL, from this
working copy) with the screen source and the real Hue output on the bench's
own entertainment area. The screen helper serves test patterns (pattern mode)
or captures a full-screen window on the monitor the camera sees (screen mode).
A USB webcam watches the monitor and the bench lights. Every run writes a
report (Markdown and JSON, numbers only) to ~/.cache/glowcast-bench/reports/.

Usage (from the repository root, in WSL on the bench's Windows computer):
    python3 scripts/bench/run.py [--suite basic|extended] [--mode pattern|screen|both]
                                 [--dry-run] [--keep-images] [--wait SECONDS]
                                 [--config FILE]
    python3 scripts/bench/run.py snapshot [--config FILE]

Exit codes: 0 passed, 1 a step failed, 2 usage or configuration error,
3 skipped: the bridge streams another area, 4 skipped: another bench run
holds the lock, 5 skipped: another app streams the TV's area, 6 the bench
itself could not complete the run. See scripts/README.md.
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
        "black (all zero): the colours hold for 3 s, then the lights are released\n"
        "    and back (camera)",
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
        "near-black 2 s: the colours hold",
        "near-black 8 s: the colours hold 3 s, then the warm glow; the area keeps\n"
        "    streaming",
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
        "screen mode: brightness changed while off is stored; Glowcast stays off",
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
        "command", nargs="?", default="run", choices=["run", "snapshot"]
    )
    parser.add_argument("--suite", choices=["basic", "extended"], default="basic")
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
    args = parser.parse_args()
    modes = ["pattern", "screen"] if args.mode == "both" else [args.mode]
    if args.dry_run:
        print(f"Dry run: suite {args.suite}, modes {', '.join(modes)}.")
        print("Nothing is touched.")
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
        cfg, path = load_config(args.config)
    except SystemExit as err:
        print(err, file=sys.stderr)
        sys.exit(rp.EXIT_USAGE)
    HOME.mkdir(parents=True, exist_ok=True)
    with open(LOCK, "w") as lock:
        if not take_lock(lock, args.wait):
            print("skipped: another bench run holds the lock", file=sys.stderr)
            sys.exit(rp.EXIT_LOCKED)
        if args.command == "snapshot":
            sys.exit(asyncio.run(snapshot(cfg)))
        sys.exit(asyncio.run(run(cfg, args.suite, modes, args.keep_images)))


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
    cfg: dict[str, Any], suite_name: str, modes: list[str], keep_images: bool
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
    bench = suite.Bench(cfg, suite_name, report, images)
    ha_cfg = cfg["home_assistant"]
    token = await asyncio.to_thread(_read_token, ha_cfg["token_file"])
    loop = asyncio.get_running_loop()
    aborted = False

    async with aiohttp.ClientSession() as session:
        bench.session = session
        bench.owner = clients.OwnerHA(
            session,
            ha_cfg["url"],
            token,
            [light["entity_id"] for light in cfg["lights"].values()],
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
        except (suite.BenchError, clients.NotAllowed) as err:
            report.add(
                rp.Step("the bench could not go on", "-", rp.FAIL, note=str(err))
            )
            outcome = ("error: " + str(err), rp.EXIT_ERROR)
        except Exception as err:  # noqa: BLE001 - always clean up and report
            report.add(
                rp.Step(
                    "the bench could not go on", "-", rp.FAIL, note=type(err).__name__
                )
            )
            outcome = (f"error: {type(err).__name__}", rp.EXIT_ERROR)
            import traceback

            traceback.print_exc()
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
    # The recorded numbers, for tuning the tolerances; local, never attached.
    (folder / "raw.json").write_text(rp.scrub(json.dumps(bench.raw()), forbidden))
    markdown = rp.scrub(report.to_markdown(), forbidden)
    (folder / "report.md").write_text(markdown)
    print(markdown)
    print(f"Report: {folder / 'report.md'} (and report.json)")
    return report.exit_code


def _read_token(path: str) -> str:
    return Path(path).expanduser().read_text().strip()


async def cleanup(bench: Any, cfg: dict[str, Any], local_ha: Any) -> None:
    """Every exit path: Glowcast off, area released, lights restored and checked,
    the TV's Glowcast back on if the run switched it off, everything stopped."""
    report = bench.report
    with contextlib.suppress(Exception):
        if bench.ha is not None:
            await bench.ha.call(
                "switch", "turn_off", {"entity_id": bench.ha.entities["switch"]}
            )
    with contextlib.suppress(Exception):
        await bench.bridge.wait_status(cfg["bench_area_id"], "inactive", 15)
    if bench.screen is not None:
        with contextlib.suppress(Exception):
            await bench.screen.stop()
    with contextlib.suppress(Exception):
        await bench.stop_helper()
    if bench.pre_states:
        try:
            start = bench.win_now()
            await bench.restore_lights()
            if bench.calibrated and bench.camera is not None and bench.camera.running:
                back = await bench.wait_restored(start)
                ok = all(v is not None for v in back.values())
                numbers = {f"{k} back after s": v for k, v in back.items()}
                with contextlib.suppress(Exception):
                    numbers.update(await bench.bridge_restore_numbers())
                report.add(
                    rp.Step(
                        "end: lights set back explicitly and seen back (camera)",
                        "-",
                        rp.PASS if ok else rp.FAIL,
                        numbers=numbers,
                    )
                )
                await bench.save_image("after-run")
            else:
                report.add(
                    rp.Step(
                        "end: lights set back explicitly",
                        "-",
                        rp.PASS,
                        note="not checked with the camera (no calibration yet)",
                    )
                )
        except Exception as err:  # noqa: BLE001
            report.add(
                rp.Step(
                    "end: lights set back explicitly",
                    "-",
                    rp.FAIL,
                    note=type(err).__name__,
                )
            )
    if bench.tv_off_at is not None:
        with contextlib.suppress(Exception):
            await bench.owner.call(
                "switch", "turn_on", {"entity_id": cfg["home_assistant"]["tv_switch"]}
            )
            report.facts["TV stream interrupted for s"] = round(
                time.time() - bench.tv_off_at, 1
            )
    if bench.camera is not None:
        with contextlib.suppress(Exception):
            await bench.camera.stop()
    if bench.ha is not None:
        with contextlib.suppress(Exception):
            await bench.ha.close()
    with contextlib.suppress(Exception):
        await bench.bridge.close()
    with contextlib.suppress(Exception):
        await asyncio.to_thread(local_ha.stop)


if __name__ == "__main__":
    main()
