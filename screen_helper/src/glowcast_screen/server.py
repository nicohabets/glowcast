"""The HTTP side of protocol version 1 (architecture 8.3): pair, frame, info."""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from aiohttp import web

from glowcast_screen import PROTOCOL, __version__
from glowcast_screen.capture import ScreenCapture
from glowcast_screen.events import Events, timestamp
from glowcast_screen.patterns import HEIGHT, WIDTH, PatternSource
from glowcast_screen.store import Store

# A wrong pairing code costs this long, one attempt at a time, so guessing the
# million codes takes days.
PAIR_DELAY = 1.0
# A client that has not asked for anything this long is "connected" again later.
CLIENT_IDLE = 30.0
STATS_INTERVAL = 1.0


class Picture(Protocol):
    """Where frames come from: a test pattern or a screen."""

    no_picture_reason: str | None

    async def frame(self) -> bytes | None: ...

    def info(self) -> dict[str, Any]: ...

    def take_capture_ms(self) -> dict[str, float] | None: ...


class PatternPicture:
    def __init__(self, source: PatternSource) -> None:
        self.source = source
        self.no_picture_reason: str | None = None

    async def frame(self) -> bytes | None:
        return self.source.frame()

    def info(self) -> dict[str, Any]:
        return {"screens": [], "screen": None, "pattern": pattern_info(self.source)}

    def take_capture_ms(self) -> dict[str, float] | None:
        return None


class ScreenPicture:
    def __init__(self, capture: ScreenCapture) -> None:
        self.capture = capture

    @property
    def no_picture_reason(self) -> str | None:
        return self.capture.no_picture_reason

    async def frame(self) -> bytes | None:
        return await self.capture.frame()

    def info(self) -> dict[str, Any]:
        screen = self.capture.screen
        return {
            "screens": [s.as_dict() for s in self.capture.screens],
            "screen": screen.index if screen else None,
            "pattern": None,
        }

    def take_capture_ms(self) -> dict[str, float] | None:
        return self.capture.take_capture_ms()


def pattern_info(source: PatternSource) -> dict[str, Any]:
    showing = source.showing()
    info: dict[str, Any] = {
        "name": source.name,
        "showing": showing.name,
        "since": timestamp(showing.since),
    }
    if source.name == "cycle":
        info["interval"] = source.interval
    return info


class HelperServer:
    def __init__(
        self,
        *,
        store: Store,
        picture: Picture,
        events: Events,
        name: str,
        pairing_code: str | None,
        pair_delay: float = PAIR_DELAY,
        stats_interval: float = STATS_INTERVAL,
        pattern_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.store = store
        self.picture = picture
        self.events = events
        self.name = name
        self._pairing_code = pairing_code
        self._pair_delay = pair_delay
        self._pair_lock = asyncio.Lock()
        self._stats_interval = stats_interval
        self._pattern_sleep = pattern_sleep
        self._served = 0
        self._no_picture = False
        self._clients: dict[str, float] = {}
        self.app = web.Application(middlewares=[self._auth])
        self.app.router.add_post("/v1/pair", self._pair)
        self.app.router.add_get("/v1/frame", self._frame)
        self.app.router.add_get("/v1/info", self._info)
        self._tasks: list[asyncio.Task[None]] = []

    def info(self) -> dict[str, Any]:
        return {
            "id": self.store.helper_id,
            "name": self.name,
            "protocol": PROTOCOL,
            "version": __version__,
            "width": WIDTH,
            "height": HEIGHT,
            **self.picture.info(),
            "no_picture": self.picture.no_picture_reason,
        }

    @web.middleware
    async def _auth(
        self,
        request: web.Request,
        handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
    ) -> web.StreamResponse:
        if request.path == "/v1/pair":
            return await handler(request)
        scheme, _, token = request.headers.get("Authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not token or not self.store.is_valid(token):
            return web.json_response(
                {"error": "unauthorized"},
                status=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        self._seen(request.remote or "")
        return await handler(request)

    def _seen(self, client: str) -> None:
        now = time.monotonic()
        last = self._clients.get(client)
        self._clients[client] = now
        if last is None or now - last > CLIENT_IDLE:
            self.events.emit(
                "client_connected", f"Client {client} connected", client=client
            )

    async def _pair(self, request: web.Request) -> web.Response:
        client = request.remote or ""
        try:
            body = await request.json()
            code = body["code"]
            if not isinstance(code, str):
                raise TypeError
        except (ValueError, KeyError, TypeError):
            return web.json_response({"error": "bad_request"}, status=400)
        if self._pairing_code is None:
            return web.json_response({"error": "pairing_unavailable"}, status=403)
        async with self._pair_lock:
            if not hmac.compare_digest(code.encode(), self._pairing_code.encode()):
                await asyncio.sleep(self._pair_delay)
                self.events.emit(
                    "pairing_failed", f"Wrong pairing code from {client}", client=client
                )
                return web.json_response({"error": "wrong_code"}, status=403)
            token = self.store.add_token()
        self.events.emit("paired", f"Paired with {client}", client=client)
        return web.json_response({"token": token, "id": self.store.helper_id})

    async def _frame(self, request: web.Request) -> web.Response:
        frame = await self.picture.frame()
        if frame is None:
            if not self._no_picture:
                self._no_picture = True
                reason = self.picture.no_picture_reason
                self.events.emit("no_picture", f"No picture ({reason})", reason=reason)
            return web.Response(status=204, headers={"Cache-Control": "no-store"})
        if self._no_picture:
            self._no_picture = False
            self.events.emit("picture", "The picture is back")
        self._served += 1
        return web.Response(
            body=frame,
            content_type="application/octet-stream",
            headers={"Cache-Control": "no-store"},
        )

    async def _info(self, request: web.Request) -> web.Response:
        return web.json_response(self.info())

    def start_reporting(self) -> None:
        """Start the frames-per-second events and, for cycle, the pattern events."""
        self._tasks.append(asyncio.create_task(self._report_frames()))
        if (
            isinstance(self.picture, PatternPicture)
            and self.picture.source.name == "cycle"
        ):
            self._tasks.append(
                asyncio.create_task(self._report_patterns(self.picture.source))
            )

    async def stop_reporting(self) -> None:
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _report_frames(self) -> None:
        while True:
            await asyncio.sleep(self._stats_interval)
            served, self._served = self._served, 0
            if not served:
                continue
            fps = round(served / self._stats_interval, 1)
            capture_ms = self.picture.take_capture_ms()
            text = f"Served {fps} frames/s"
            if capture_ms:
                text += (
                    f" (capture median {capture_ms['median']} ms,"
                    f" max {capture_ms['max']} ms)"
                )
            self.events.emit("frames", text, fps=fps, capture_ms=capture_ms)

    async def _report_patterns(self, source: PatternSource) -> None:
        # The source's clock drives the cycle; the loop sleeps on its own clock,
        # which may run a little faster, so a wake-up just before the change
        # sleeps again instead of reporting the same pattern twice.
        reported: float | None = None
        while True:
            showing = source.showing()
            if showing.since != reported:
                reported = showing.since
                self.events.emit(
                    "pattern",
                    f"Showing {showing.name}",
                    showing=showing.name,
                    since=timestamp(showing.since),
                )
            next_change = source.next_change()
            assert next_change is not None
            await self._pattern_sleep(max(0.0, next_change - source.now()) + 0.001)
