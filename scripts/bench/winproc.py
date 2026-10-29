"""Windows processes started from WSL: the camera, the screen window, the helper.

The bench runs in WSL (the local Home Assistant), but the webcam, the monitor
and the screen helper are on the Windows side. Windows' own `uv.exe` runs them
natively: called from WSL with the working copy as the current directory, the
Windows process gets a `\\\\wsl.localhost\\...` directory and reads the files of
the working copy. Each worker prints JSON lines on standard output, starting
with an event that carries its Windows pid, so it can be stopped with
`taskkill` (stopping `uv.exe` alone may leave its Python running).
"""

import asyncio
import contextlib
import json
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent.parent
WORKERS = Path(__file__).resolve().parent / "win"


def find_uv(configured: str | None) -> str:
    """The Windows uv.exe as a WSL path: from the config, or `where.exe uv`."""
    if configured:
        return configured
    where = shutil.which("where.exe")
    if where is None:
        raise RuntimeError("where.exe not found: the bench needs WSL on Windows")
    found = subprocess.run(
        [where, "uv"], capture_output=True, text=True, cwd="/mnt/c", check=False
    ).stdout.splitlines()
    if not found:
        raise RuntimeError(
            "Windows' uv.exe is not installed (winget install astral-sh.uv)"
        )
    return to_wsl(found[0].strip())


def windows_temp() -> str:
    """%TEMP% on the Windows side, as a Windows path."""
    out = subprocess.run(
        ["cmd.exe", "/c", "echo %TEMP%"],
        capture_output=True,
        text=True,
        cwd="/mnt/c",
        check=True,
    ).stdout.strip()
    return out


def to_wsl(path: str) -> str:
    return subprocess.run(
        ["wslpath", "-u", path], capture_output=True, text=True, check=True
    ).stdout.strip()


def to_windows(path: Path | str) -> str:
    return subprocess.run(
        ["wslpath", "-w", str(path)], capture_output=True, text=True, check=True
    ).stdout.strip()


def taskkill(pid: int) -> None:
    """Stop a Windows process and its children."""
    subprocess.run(
        ["taskkill.exe", "/T", "/F", "/PID", str(pid)],
        capture_output=True,
        cwd="/mnt/c",
        check=False,
    )


def pid_listening(port: int) -> int | None:
    """The Windows pid that listens on this TCP port, if any (`netstat -ano`)."""
    out = subprocess.run(
        ["netstat.exe", "-ano", "-p", "TCP"],
        capture_output=True,
        text=True,
        cwd="/mnt/c",
        check=False,
    ).stdout
    for line in out.splitlines():
        parts = line.split()
        if (
            len(parts) == 5
            and parts[3] == "LISTENING"
            and parts[1].rsplit(":", 1)[-1] == str(port)
        ):
            return int(parts[4])
    return None


class Worker:
    """A Windows process that speaks JSON lines; every line is kept with its arrival.

    `lines` holds every parsed line in order; `on_line` (optional) sees each
    one as it arrives. Lines that are not JSON are kept in `text`.
    """

    def __init__(
        self,
        name: str,
        args: list[str],
        *,
        env: dict[str, str] | None = None,
        on_line: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.name = name
        self.args = args
        self.env = env or {}
        self.on_line = on_line
        self.lines: list[dict[str, Any]] = []
        self.text: list[str] = []
        self.pid: int | None = None  # the Windows pid, from the first event
        self.process: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task[None] | None = None
        self._stderr: asyncio.Task[None] | None = None
        self._events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def start(self) -> None:
        env = dict(os.environ)
        env.update(self.env)
        if self.env:
            # Hand these variables to the Windows process unchanged.
            names = [n for n in self.env if n != "WSLENV"]
            wslenv = [p for p in env.get("WSLENV", "").split(":") if p]
            env["WSLENV"] = ":".join([*wslenv, *names])
        self.process = await asyncio.create_subprocess_exec(
            *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=REPO,
            env=env,
            limit=1 << 20,
        )
        self._reader = asyncio.create_task(self._read())
        self._stderr = asyncio.create_task(self._read_stderr())

    async def _read(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        async for raw in self.process.stdout:
            line = raw.decode(errors="replace").strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                self.text.append(line)
                continue
            if not isinstance(event, dict):
                continue
            event.setdefault("arrived", time.time())
            if self.pid is None and isinstance(event.get("pid"), int):
                self.pid = event["pid"]
            self.lines.append(event)
            if "event" in event:
                await self._events.put(event)
            if self.on_line is not None:
                self.on_line(event)

    async def _read_stderr(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        async for raw in self.process.stderr:
            line = raw.decode(errors="replace").rstrip()
            if line:
                self.text.append(line)

    async def wait_event(
        self, predicate: Callable[[dict[str, Any]], bool], within: float
    ) -> dict[str, Any]:
        """The next event (a line with "event") that matches, or TimeoutError."""
        async with asyncio.timeout(within):
            while True:
                if (
                    self.process is not None
                    and self.process.returncode is not None
                    and self._events.empty()
                ):
                    raise RuntimeError(
                        f"{self.name} stopped (exit {self.process.returncode}): "
                        + " | ".join(self.text[-5:])
                    )
                try:
                    event = await asyncio.wait_for(self._events.get(), 0.5)
                except TimeoutError:
                    continue
                if predicate(event):
                    return event

    def send(self, command: str) -> None:
        if self.process is None or self.process.stdin is None:
            return
        with contextlib.suppress(OSError, RuntimeError):
            self.process.stdin.write((command + "\n").encode())

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.returncode is None

    async def stop(
        self, *, quit_command: str | None = "quit", grace: float = 3
    ) -> None:
        """Ask it to quit, then kill the Windows process tree; safe to call twice."""
        if self.process is None:
            return
        if self.running and quit_command is not None:
            self.send(quit_command)
            with contextlib.suppress(OSError, RuntimeError):
                if self.process.stdin is not None:
                    self.process.stdin.close()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.process.wait(), grace)
        if self.pid is not None:
            await asyncio.to_thread(taskkill, self.pid)
        if self.running:
            self.process.kill()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.process.wait(), 5)
        for task in (self._reader, self._stderr):
            if task is not None:
                task.cancel()
