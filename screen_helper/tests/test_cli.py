"""The command: --json events, exit codes, pairing code, --unpair, mDNS, shutdown."""

from __future__ import annotations

import asyncio
import io
import json
import os
import signal
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

import pytest
from fakes import FakeGrabber, FakeZeroconf
from glowcast_screen.cli import (
    EXIT_NO_SCREEN,
    EXIT_OK,
    EXIT_PORT,
    EXIT_USAGE,
    parser,
    run,
    unpair,
)
from glowcast_screen.events import Events
from glowcast_screen.mdns import (
    SERVICE_TYPE,
    Announcer,
    announce_addresses,
    service_info,
)
from glowcast_screen.store import Store

BASE = ["--host", "127.0.0.1", "--port", "0", "--name", "Test PC"]


class Run:
    """run() in this process with the fakes; stops when told to."""

    def __init__(self, argv: list[str], config_dir: Path, **kwargs: Any) -> None:
        self.args = parser().parse_args([*argv, "--config-dir", str(config_dir)])
        self.out = io.StringIO()
        self.err = io.StringIO()
        self.stop = asyncio.Event()
        self.kwargs = kwargs
        self.task: asyncio.Task[int] | None = None

    def events(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.out.getvalue().splitlines()]

    async def start(self) -> dict[str, Any]:
        events = Events(json_mode=self.args.json, out=self.out, err=self.err)
        self.task = asyncio.create_task(
            run(self.args, events, stop=self.stop, **self.kwargs)
        )
        for _ in range(200):
            if self.task.done() or '"started"' in self.out.getvalue():
                break
            await asyncio.sleep(0.01)
        return self.events()[-1] if self.args.json and self.out.getvalue() else {}

    async def finish(self) -> int:
        assert self.task is not None
        self.stop.set()
        return await self.task


async def test_json_pattern_run(config_dir: Path) -> None:
    helper = Run(["--pattern", "red", "--json", "--non-interactive", *BASE], config_dir)
    started = await helper.start()
    assert started["event"] == "started"
    assert started["pattern"] == "red"
    assert started["mdns"] is False  # loopback: not announced
    assert started["name"] == "Test PC"
    assert started["protocol"] == 1
    assert started["port"] > 0
    assert started["screens"] == []
    assert "pairing_code" not in started
    assert await helper.finish() == EXIT_OK
    assert [e["event"] for e in helper.events()] == ["started", "stopped"]
    assert all(e["time"].endswith("Z") for e in helper.events())


async def test_a_passed_pairing_code_is_in_the_json_and_works(config_dir: Path) -> None:
    argv = [
        "--pattern",
        "red",
        "--json",
        "--non-interactive",
        "--pairing-code",
        "246810",
        *BASE,
    ]
    helper = Run(argv, config_dir)
    started = await helper.start()
    assert started["pairing_code"] == "246810"

    def pair() -> dict[str, Any]:
        request = urllib.request.Request(
            f"http://127.0.0.1:{started['port']}/v1/pair",
            data=b'{"code": "246810"}',
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=5) as resp:
            return json.load(resp)

    body = await asyncio.to_thread(pair)
    await helper.finish()
    assert body["id"] == started["id"]
    assert "paired" in [e["event"] for e in helper.events()]
    assert body["token"] not in helper.out.getvalue()


async def test_a_generated_code_stays_out_of_the_json(config_dir: Path) -> None:
    helper = Run(["--pattern", "red", "--json", *BASE], config_dir)
    await helper.start()
    await helper.finish()
    shown = helper.err.getvalue()
    assert "Pairing code: " in shown
    code = shown.split("Pairing code: ")[1][:6]
    assert code.isdigit()
    assert code not in helper.out.getvalue()


async def test_interactive_text_output_shows_the_code(config_dir: Path) -> None:
    helper = Run(["--pattern", "cycle", *BASE], config_dir)
    await helper.start()
    for _ in range(200):
        if "Pairing code:" in helper.out.getvalue():
            break
        await asyncio.sleep(0.01)
    await helper.finish()
    text = helper.out.getvalue()
    assert "test pattern cycle (every 5 s)" in text
    assert "Pairing code: " in text


async def test_non_interactive_without_a_code_shows_none(config_dir: Path) -> None:
    helper = Run(["--pattern", "red", "--non-interactive", *BASE], config_dir)
    await helper.start()
    await helper.finish()
    assert "Pairing code" not in helper.out.getvalue() + helper.err.getvalue()


async def test_screen_mode_lists_the_screens_in_started(config_dir: Path) -> None:
    argv = ["--screen", "2", "--json", "--non-interactive", *BASE]
    grabber = FakeGrabber()
    helper = Run(argv, config_dir, grabber_factory=lambda ask: grabber)
    started = await helper.start()
    assert started["screen"] == 2
    assert [s["index"] for s in started["screens"]] == [1, 2]
    assert started["pattern"] is None
    await helper.finish()
    assert grabber.closed


async def test_no_screen_access_exits_with_4(config_dir: Path) -> None:
    grabber = FakeGrabber()
    grabber.no_access = "macOS does not allow this app to record the screen"
    argv = ["--json", "--non-interactive", *BASE]
    helper = Run(argv, config_dir, grabber_factory=lambda ask: grabber)
    await helper.start()
    assert helper.task is not None
    assert await helper.task == EXIT_NO_SCREEN
    (error,) = helper.events()
    assert error["event"] == "error"
    assert error["reason"] == "no_screen_access"
    assert error["exit_code"] == EXIT_NO_SCREEN
    assert "record the screen" in error["message"]


async def test_a_port_in_use_exits_with_3(config_dir: Path) -> None:
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]
        argv = [
            "--pattern",
            "red",
            "--json",
            "--non-interactive",
            "--host",
            "127.0.0.1",
        ]
        helper = Run([*argv, "--port", str(port)], config_dir)
        await helper.start()
        assert helper.task is not None
        assert await helper.task == EXIT_PORT
    (error,) = helper.events()
    assert error["reason"] == "port_in_use"


def _unpair(argv: list[str], config_dir: Path) -> tuple[int, io.StringIO]:
    args = parser().parse_args([*argv, "--config-dir", str(config_dir)])
    out = io.StringIO()
    return unpair(args, Events(json_mode=True, out=out, err=io.StringIO())), out


def test_unpair_keeps_the_id(config_dir: Path) -> None:
    store = Store(config_dir)
    store.load()
    store.add_token()
    code, out = _unpair(["--unpair", "--json", "--non-interactive"], config_dir)
    assert code == EXIT_OK
    (event,) = [json.loads(line) for line in out.getvalue().splitlines()]
    assert event["event"] == "unpaired"
    assert event["removed"] == 1
    again = Store(config_dir)
    again.load()
    assert again.paired == 0
    assert again.helper_id == store.helper_id


@pytest.mark.parametrize("answer", ["n", "", "ctrl-c"])
def test_interactive_unpair_asks_and_keeps_on_no_or_ctrl_c(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch, answer: str
) -> None:
    store = Store(config_dir)
    store.load()
    store.add_token()

    def fake_input(prompt: str) -> str:
        assert "Remove 1 pairing(s)?" in prompt
        if answer == "ctrl-c":
            raise KeyboardInterrupt
        if not answer:
            raise EOFError
        return answer

    monkeypatch.setattr("builtins.input", fake_input)
    code, out = _unpair(["--unpair", "--json"], config_dir)
    assert code == EXIT_OK
    assert out.getvalue() == ""
    again = Store(config_dir)
    again.load()
    assert again.paired == 1


def test_interactive_unpair_removes_on_yes(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = Store(config_dir)
    store.load()
    store.add_token()
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    code, out = _unpair(["--unpair", "--json"], config_dir)
    assert code == EXIT_OK
    assert json.loads(out.getvalue())["removed"] == 1


async def test_a_host_that_does_not_resolve_exits_with_3(config_dir: Path) -> None:
    argv = ["--pattern", "red", "--json", "--non-interactive", "--port", "0"]
    helper = Run([*argv, "--host", "no-such-host.invalid"], config_dir)
    await helper.start()
    assert helper.task is not None
    assert await helper.task == EXIT_PORT
    (error,) = helper.events()
    assert error["reason"] == "bad_host"


def test_only_the_default_route_address_is_announced() -> None:
    lan = ["172.20.0.1", "192.0.2.10"]  # a virtual adapter and the real network
    assert announce_addresses(lambda: "192.0.2.10", lambda: lan) == ["192.0.2.10"]
    # Without a default route, every address.
    assert announce_addresses(lambda: None, lambda: lan) == lan


async def test_announced_on_the_network_and_withdrawn_on_stop(config_dir: Path) -> None:
    zeroconf = FakeZeroconf()
    argv = ["--pattern", "red", "--json", "--non-interactive", "--name", "Test PC"]
    argv += ["--host", "0.0.0.0", "--port", "0"]
    helper = Run(argv, config_dir, announcer=Announcer(lambda: zeroconf))
    started = await helper.start()
    assert started["mdns"] is True
    (info,) = zeroconf.registered
    assert info.type == SERVICE_TYPE
    assert info.port == started["port"]
    assert info.properties == {
        b"id": started["id"].encode(),
        b"name": b"Test PC",
        b"protocol": b"1",
    }
    assert zeroconf.unregistered == []
    await helper.finish()
    assert zeroconf.unregistered == [info]
    assert zeroconf.closed


def test_service_info_name_is_unique_and_short() -> None:
    info = service_info("0123456789abcdef" * 2, "A" * 100, 8488, ["192.0.2.10"])
    assert info.name.endswith(f" (012345).{SERVICE_TYPE}")
    assert len(info.name.split("._glowcast")[0].encode()) <= 63
    assert info.parsed_addresses() == ["192.0.2.10"]


@pytest.mark.parametrize(
    "argv",
    [
        ["--pairing-code", "12345"],
        ["--pairing-code", "abcdef"],
        ["--pattern", "purple"],
        ["--port", "70000"],
        ["--port", "-1"],
    ],
)
def test_bad_flags_exit_with_2(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        parser().parse_args(argv)
    assert exit_info.value.code == EXIT_USAGE


# The real command in a subprocess, as an agent starts it.


def _command(config_dir: Path, *argv: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "glowcast_screen",
        "--config-dir",
        str(config_dir),
        *argv,
    ]


def test_subprocess_port_in_use_exits_non_zero(config_dir: Path) -> None:
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = str(taken.getsockname()[1])
        result = subprocess.run(
            _command(
                config_dir,
                "--pattern",
                "red",
                "--json",
                "--non-interactive",
                "--host",
                "127.0.0.1",
                "--port",
                port,
            ),
            capture_output=True,
            text=True,
            timeout=60,
        )
    assert result.returncode == EXIT_PORT
    assert json.loads(result.stdout.splitlines()[-1])["reason"] == "port_in_use"


@pytest.mark.skipif(os.name == "nt", reason="SIGTERM and SIGINT are POSIX signals")
@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_subprocess_stops_cleanly_on_a_signal(
    config_dir: Path, sig: signal.Signals
) -> None:
    process = subprocess.Popen(
        _command(
            config_dir, "--pattern", "cycle", "--json", "--non-interactive", *BASE
        ),
        stdout=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    # Lines arrive one by one (flushed), before the process ends.
    first = json.loads(process.stdout.readline())
    assert first["event"] == "started"
    process.send_signal(sig)
    rest = [json.loads(line) for line in process.stdout]
    assert process.wait(timeout=30) == EXIT_OK
    assert rest[-1]["event"] == "stopped"
