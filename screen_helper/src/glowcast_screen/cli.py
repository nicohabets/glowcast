"""The `glowcast-screen` command."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import ipaddress
import re
import secrets
import signal
import socket
import sys
import time
from collections.abc import Callable
from pathlib import Path

from aiohttp import web

from glowcast_screen import PROTOCOL, __version__
from glowcast_screen.capture import Grabber, MssGrabber, NoScreenAccess, ScreenCapture
from glowcast_screen.events import Events
from glowcast_screen.mdns import (
    SERVICE_TYPE,
    Announcer,
    announce_addresses,
    service_info,
)
from glowcast_screen.patterns import DEFAULT_INTERVAL, NAMES, PatternSource
from glowcast_screen.server import HelperServer, PatternPicture, Picture, ScreenPicture
from glowcast_screen.store import Store, default_directory

DEFAULT_PORT = 8488

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2  # argparse's own code for a wrong flag
EXIT_PORT = 3
EXIT_NO_SCREEN = 4


def _pairing_code(value: str) -> str:
    if not re.fullmatch(r"\d{6}", value):
        raise argparse.ArgumentTypeError("the pairing code is 6 digits")
    return value


def _port(value: str) -> int:
    number = int(value)
    if not 0 <= number <= 65535:
        raise argparse.ArgumentTypeError("a port is 0-65535")
    return number


def _positive(value: str) -> float:
    number = float(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be more than 0")
    return number


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="glowcast-screen",
        description="Serve this computer's screen, or a test pattern, to Glowcast.",
    )
    p.add_argument(
        "--pattern", choices=NAMES, help="serve a test pattern instead of the screen"
    )
    p.add_argument(
        "--pattern-interval",
        type=_positive,
        default=DEFAULT_INTERVAL,
        metavar="SECONDS",
        help=f"seconds per pattern for --pattern cycle (default {DEFAULT_INTERVAL:g})",
    )
    p.add_argument(
        "--screen",
        type=int,
        metavar="N",
        help="the screen to capture (default: the main screen)",
    )
    p.add_argument(
        "--port",
        type=_port,
        default=DEFAULT_PORT,
        help=f"HTTP port (default {DEFAULT_PORT})",
    )
    p.add_argument(
        "--host",
        default="0.0.0.0",
        help="address to listen on (default: all; a loopback address is not announced)",
    )
    p.add_argument(
        "--name", help="the name Home Assistant shows (default: the computer's name)"
    )
    p.add_argument(
        "--pairing-code",
        type=_pairing_code,
        metavar="CODE",
        help="a fixed 6-digit pairing code",
    )
    p.add_argument(
        "--non-interactive",
        action="store_true",
        help="never ask anything; for agents and scripts",
    )
    p.add_argument("--json", action="store_true", help="print one JSON line per event")
    p.add_argument("--unpair", action="store_true", help="remove all pairings and exit")
    p.add_argument(
        "--config-dir",
        type=Path,
        help=f"where the id and the pairings live (default {default_directory()})",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def _fail(events: Events, code: int, reason: str, message: str) -> int:
    if events.json_mode:
        events.emit("error", message, reason=reason, message=message, exit_code=code)
    else:
        print(f"Error: {message}", file=sys.stderr, flush=True)
    return code


def _is_loopback(host: str) -> bool:
    with contextlib.suppress(ValueError):
        return ipaddress.ip_address(host).is_loopback
    return host == "localhost"


def _load_store(args: argparse.Namespace, events: Events) -> Store | int:
    store = Store(args.config_dir or default_directory())
    try:
        store.load()
    except (OSError, ValueError) as err:
        return _fail(
            events, EXIT_ERROR, "config", f"cannot read or write {store.path}: {err}"
        )
    return store


def unpair(args: argparse.Namespace, events: Events) -> int:
    """--unpair, without an event loop: Ctrl+C at the question simply stops it."""
    store = _load_store(args, events)
    if isinstance(store, int):
        return store
    if not args.non_interactive and store.paired:
        try:
            answer = input(
                f"Remove {store.paired} pairing(s)? Glowcast then asks for a new code."
                " [y/N] "
            )
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer.strip().lower() not in ("y", "yes"):
            events.say("Nothing removed.")
            return EXIT_OK
    try:
        removed = store.unpair()
    except OSError as err:
        return _fail(events, EXIT_ERROR, "config", f"cannot write {store.path}: {err}")
    events.emit("unpaired", f"Removed {removed} pairing(s)", removed=removed)
    return EXIT_OK


async def run(
    args: argparse.Namespace,
    events: Events,
    grabber_factory: Callable[[bool], Grabber] = MssGrabber,
    announcer: Announcer | None = None,
    stop: asyncio.Event | None = None,
) -> int:
    interactive = not args.non_interactive
    # Before anything else, so a signal during the start also stops cleanly.
    stop = stop or _stop_on_signals()
    store = _load_store(args, events)
    if isinstance(store, int):
        return store

    name = args.name or socket.gethostname().split(".")[0] or "Glowcast screen"
    picture: Picture
    capture: ScreenCapture | None = None
    if args.pattern:
        picture = PatternPicture(
            PatternSource(args.pattern, time.time, args.pattern_interval)
        )
    else:
        capture = ScreenCapture(grabber_factory(interactive), args.screen)
        try:
            await capture.start()
        except NoScreenAccess as err:
            await capture.close()
            return _fail(events, EXIT_NO_SCREEN, "no_screen_access", str(err))
        picture = ScreenPicture(capture)

    # The code is a secret: shown to the person at the keyboard, and only put
    # into the JSON output when the caller chose it.
    code = args.pairing_code or (
        f"{secrets.randbelow(10**6):06d}" if interactive else None
    )
    server = HelperServer(
        store=store, picture=picture, events=events, name=name, pairing_code=code
    )
    runner = web.AppRunner(server.app, access_log=None, handle_signals=False)
    await runner.setup()
    announcer = announcer or Announcer()
    try:
        site = web.TCPSite(runner, args.host, args.port)
        try:
            await site.start()
        except socket.gaierror as err:
            return _fail(
                events, EXIT_PORT, "bad_host", f"cannot use {args.host!r}: {err}"
            )
        except OSError as err:
            return _fail(
                events,
                EXIT_PORT,
                "port_in_use",
                f"cannot listen on {args.host}:{args.port}: {err}",
            )
        port = runner.addresses[0][1]

        announced = False
        if not _is_loopback(args.host):
            addresses = [args.host] if args.host != "0.0.0.0" else announce_addresses()
            try:
                await announcer.start(
                    service_info(store.helper_id, name, port, addresses)
                )
                announced = True
            except Exception as err:  # noqa: BLE001 - serving still works without mDNS
                await announcer.stop()
                events.emit(
                    "mdns_failed",
                    f"Not announced on the network: {err}",
                    error=str(err),
                )

        info = server.info()
        fields = {
            "host": args.host,
            "port": port,
            "id": info["id"],
            "name": name,
            "protocol": PROTOCOL,
            "version": __version__,
            "pattern": args.pattern,
            "screen": info["screen"],
            "screens": info["screens"],
            "mdns": announced,
            "paired": store.paired,
        }
        if args.pairing_code:
            fields["pairing_code"] = args.pairing_code
        events.emit("started", _banner(args, info, port, announced), **fields)
        server.start_reporting()
        if interactive:
            events.say(f"Pairing code: {code}  (enter it in Home Assistant)")
            events.say("Press Ctrl+C to stop.")

        await stop.wait()
    finally:
        await server.stop_reporting()
        await announcer.stop()
        await runner.cleanup()
        if capture is not None:
            await capture.close()
    events.emit("stopped", "Stopped")
    return EXIT_OK


def _banner(args: argparse.Namespace, info: dict, port: int, announced: bool) -> str:
    if args.pattern:
        what = f"test pattern {args.pattern}"
        if args.pattern == "cycle":
            what += f" (every {args.pattern_interval:g} s)"
    else:
        screen = next(s for s in info["screens"] if s["index"] == info["screen"])
        what = (
            f"screen {screen['index']} of {len(info['screens'])}"
            f" ({screen['width']}x{screen['height']}"
            f"{', main' if screen['primary'] else ''})"
        )
    lines = [
        f"Glowcast screen helper {__version__}",
        f"  Name:    {info['name']}",
        f"  Picture: {what}",
        f"  Listens: {args.host}:{port}"
        + (f", announced as {SERVICE_TYPE}" if announced else ", not announced"),
    ]
    if not args.pattern:
        lines += ["  Screens:"] + [
            f"    {s['index']}: {s['width']}x{s['height']} at {s['left']},{s['top']}"
            + (" (main)" if s["primary"] else "")
            for s in info["screens"]
        ]
    return "\n".join(lines)


def _stop_on_signals() -> asyncio.Event:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            # Windows: no add_signal_handler; wake the loop from the handler.
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))
    return stop


def main(argv: list[str] | None = None) -> None:
    args = parser().parse_args(argv)
    events = Events(json_mode=args.json)
    if args.unpair:
        sys.exit(unpair(args, events))
    try:
        code = asyncio.run(run(args, events))
    except KeyboardInterrupt:
        code = EXIT_OK
    sys.exit(code)
