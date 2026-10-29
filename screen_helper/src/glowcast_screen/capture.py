"""Screen capture: grab a screen with mss and shrink it to 64x36 RGB on demand."""

from __future__ import annotations

import asyncio
import ctypes
import statistics
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Protocol

from PIL import Image

from glowcast_screen.patterns import HEIGHT, WIDTH

# A frame younger than this is served again instead of grabbing a new one, so
# requests that arrive together share one grab.
MAX_AGE = 0.05
# A grab that takes longer than this answers "no picture" (the story allows 1 s).
GRAB_TIMEOUT = 1.0

MACOS_PERMISSION_STEPS = (
    "macOS does not allow this app to record the screen. Open System Settings > "
    "Privacy & Security > Screen Recording, switch it on for the app you start the "
    "helper from (for example Terminal), quit and reopen that app, then start the "
    "helper again."
)


class CaptureError(Exception):
    """A grab failed; the helper answers "no picture"."""


class NoScreenAccess(CaptureError):
    """The helper cannot capture at all: no display, no permission, no such screen."""


@dataclass(frozen=True)
class Screen:
    """A screen as `/v1/info` lists it. `index` is what `--screen` takes."""

    index: int
    width: int
    height: int
    left: int
    top: int
    primary: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "width": self.width,
            "height": self.height,
            "left": self.left,
            "top": self.top,
            "primary": self.primary,
        }


class Grabber(Protocol):
    """Grabs screens. Every method runs on the capture thread, never on the loop."""

    def open(self) -> list[Screen]:
        """Check access and list the screens; raise NoScreenAccess if not possible."""

    def grab(self, screen: Screen) -> tuple[int, int, bytes]:
        """Width, height and BGRA pixels; raise CaptureError on failure."""

    def close(self) -> None: ...


class MssGrabber:
    """The real grabber. mss is imported and created on the capture thread only."""

    def __init__(self, ask_permission: bool) -> None:
        self._ask_permission = ask_permission
        self._mss: Any = None

    def open(self) -> list[Screen]:
        if sys.platform == "darwin" and not _macos_screen_access(self._ask_permission):
            raise NoScreenAccess(MACOS_PERMISSION_STEPS)
        import mss
        from mss.exception import ScreenShotError

        try:
            self._mss = mss.mss()
            monitors = self._mss.monitors[1:]
            primary = self._mss.primary_monitor
        except ScreenShotError as err:
            raise NoScreenAccess(f"no screen to capture: {err}") from err
        return [
            Screen(
                index=i,
                width=m["width"],
                height=m["height"],
                left=m["left"],
                top=m["top"],
                primary=m is primary,
            )
            for i, m in enumerate(monitors, start=1)
        ]

    def grab(self, screen: Screen) -> tuple[int, int, bytes]:
        from mss.exception import ScreenShotError

        box = {
            "left": screen.left,
            "top": screen.top,
            "width": screen.width,
            "height": screen.height,
        }
        try:
            shot = self._mss.grab(box)
        except ScreenShotError as err:
            raise CaptureError(str(err)) from err
        return shot.width, shot.height, shot.bgra

    def close(self) -> None:
        if self._mss is not None:
            self._mss.close()
            self._mss = None


def _macos_screen_access(ask: bool) -> bool:
    """Whether macOS lets this process record the screen (macOS 10.15+).

    Without the permission a grab does not fail: it returns the desktop without
    windows, so the helper has to ask first. With `ask`, macOS shows its prompt.
    """
    try:
        cg = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
        )
        cg.CGPreflightScreenCaptureAccess.restype = ctypes.c_bool
        if cg.CGPreflightScreenCaptureAccess():
            return True
        if ask:
            cg.CGRequestScreenCaptureAccess.restype = ctypes.c_bool
            cg.CGRequestScreenCaptureAccess()
        return False
    except (OSError, AttributeError):
        # Older macOS without the check: try to capture.
        return True


def downscale(width: int, height: int, bgra: bytes) -> bytes:
    """Shrink a BGRA grab to 64x36 RGB, row-major: the whole screen, stretched."""
    image = Image.frombytes("RGB", (width, height), bgra, "raw", "BGRX")
    # reduce() first (fast integer box filter), keeping at least 4x the target size.
    factor = max(1, min(width // (WIDTH * 4), height // (HEIGHT * 4)))
    if factor > 1:
        image = image.reduce(factor)
    return image.resize((WIDTH, HEIGHT), Image.Resampling.BOX).tobytes()


class ScreenCapture:
    """Grabs the chosen screen only when a frame is asked for, on one thread.

    mss handles belong to the thread that made them (Windows, X11), so every
    grabber call runs on the same single worker thread.
    """

    def __init__(
        self,
        grabber: Grabber,
        screen: int | None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._grabber = grabber
        self._wanted = screen
        self._clock = clock
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="capture")
        self._pending: asyncio.Future[tuple[bytes, float]] | None = None
        self._last: tuple[bytes, float] | None = None
        self.screens: list[Screen] = []
        self.screen: Screen | None = None
        self.no_picture_reason: str | None = None
        self._capture_ms: list[float] = []

    async def _run[T](self, func: Callable[..., T], *args: Any) -> T:
        return await asyncio.get_running_loop().run_in_executor(
            self._executor, func, *args
        )

    async def start(self) -> None:
        """List the screens, pick one and grab once; raise NoScreenAccess on failure."""
        self.screens = await self._run(self._grabber.open)
        if not self.screens:
            raise NoScreenAccess("no screen to capture")
        if self._wanted is None:
            self.screen = next((s for s in self.screens if s.primary), self.screens[0])
        else:
            self.screen = next(
                (s for s in self.screens if s.index == self._wanted), None
            )
            if self.screen is None:
                listed = ", ".join(
                    f"{s.index} ({s.width}x{s.height})" for s in self.screens
                )
                raise NoScreenAccess(
                    f"there is no screen {self._wanted}; screens: {listed}"
                )
        try:
            await self._run(self._grab_once)
        except CaptureError as err:
            raise NoScreenAccess(f"capturing the screen failed: {err}") from err

    def _grab_once(self) -> tuple[bytes, float]:
        assert self.screen is not None
        started = time.perf_counter()
        frame = downscale(*self._grabber.grab(self.screen))
        return frame, (time.perf_counter() - started) * 1000

    async def frame(self) -> bytes | None:
        """The latest frame, or None for "no picture" (see no_picture_reason)."""
        if self._last is not None and self._clock() - self._last[1] < MAX_AGE:
            return self._last[0]
        if self._pending is None:
            self._pending = asyncio.ensure_future(self._run(self._grab_once))
            self._pending.add_done_callback(self._grabbed)
        try:
            frame, _ = await asyncio.wait_for(
                asyncio.shield(self._pending), GRAB_TIMEOUT
            )
        except TimeoutError:
            self.no_picture_reason = "capture_timeout"
            return None
        except CaptureError:
            self.no_picture_reason = "capture_failed"
            return None
        self.no_picture_reason = None
        return frame

    def _grabbed(self, future: asyncio.Future[tuple[bytes, float]]) -> None:
        self._pending = None
        if future.cancelled() or future.exception() is not None:
            return
        frame, ms = future.result()
        self._last = (frame, self._clock())
        self._capture_ms.append(ms)

    def take_capture_ms(self) -> dict[str, float] | None:
        """Median and maximum grab-and-shrink time since the last call, in ms."""
        times, self._capture_ms = self._capture_ms, []
        if not times:
            return None
        return {
            "median": round(statistics.median(times), 1),
            "max": round(max(times), 1),
        }

    async def close(self) -> None:
        if self._pending is not None:
            await asyncio.gather(self._pending, return_exceptions=True)
        await self._run(self._grabber.close)
        self._executor.shutdown(wait=True)
