"""Screen capture: grab a screen with mss and shrink it to 64x36 RGB on demand."""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import dataclasses
import queue
import statistics
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from PIL import Image

from glowcast_screen.patterns import HEIGHT, WIDTH

# A frame younger than this is served again instead of grabbing a new one, so
# requests that arrive together share one grab.
MAX_AGE = 0.05
# A grab that takes longer than this answers "no picture" (the story allows 1 s).
GRAB_TIMEOUT = 1.0
# Listing the screens at start-up that takes longer than this is no screen access.
START_TIMEOUT = 5.0
# How often the screens are listed again, so a monitor that is unplugged, plugged
# in again or changes resolution is captured with its current size and position.
SCREEN_RECHECK = 5.0
# At shutdown, a grab or close that hangs is abandoned after this long.
CLOSE_TIMEOUT = 1.0

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


class ScreenMissing(CaptureError):
    """The chosen screen is no longer there (unplugged, or asleep and removed)."""


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
        return dataclasses.asdict(self)


class Grabber(Protocol):
    """Grabs screens. Every method runs on the capture thread, never on the loop."""

    def open(self) -> list[Screen]:
        """Check access and list the screens; raise NoScreenAccess if not possible.

        Called again every SCREEN_RECHECK seconds, to see changed screens.
        """

    def grab(self, screen: Screen) -> tuple[int, int, bytes | bytearray]:
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

        # mss lists the monitors once per object: a new object sees changes.
        self.close()
        try:
            self._mss = mss.mss()
            monitors = self._mss.monitors[1:]
        except ScreenShotError as err:
            raise NoScreenAccess(f"no screen to capture: {err}") from err
        # macOS reports no is_primary; its first display is the main one, which
        # is also what mss's primary_monitor falls back to.
        marked = [bool(m.get("is_primary")) for m in monitors]
        if monitors and not any(marked):
            marked[0] = True
        return [
            Screen(
                index=i,
                width=m["width"],
                height=m["height"],
                left=m["left"],
                top=m["top"],
                primary=primary,
            )
            for i, (m, primary) in enumerate(zip(monitors, marked, strict=True), 1)
        ]

    def grab(self, screen: Screen) -> tuple[int, int, bytes | bytearray]:
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
        # raw, not bgra: bgra is a full copy of the same pixels.
        return shot.width, shot.height, shot.raw

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


def downscale(width: int, height: int, bgra: bytes | bytearray) -> bytes:
    """Shrink a BGRA grab to 64x36 RGB, row-major: the whole screen, stretched."""
    image = Image.frombuffer("RGB", (width, height), bgra, "raw", "BGRX", 0, 1)
    # reduce() first (fast integer box filter), keeping at least 4x the target size.
    factor = max(1, min(width // (WIDTH * 4), height // (HEIGHT * 4)))
    if factor > 1:
        image = image.reduce(factor)
    return image.resize((WIDTH, HEIGHT), Image.Resampling.BOX).tobytes()


class _Worker:
    """One daemon thread that runs every grabber call, in order.

    mss handles belong to the thread that made them (Windows, X11), so every
    call runs on this one thread. It is a daemon thread, so a call that never
    returns cannot keep the helper from exiting.
    """

    def __init__(self) -> None:
        self._queue: queue.SimpleQueue[Any] = queue.SimpleQueue()
        threading.Thread(target=self._loop, name="capture", daemon=True).start()

    def run(self, func: Callable[..., Any], *args: Any) -> asyncio.Future[Any]:
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._queue.put((func, args, future, loop))
        return future

    def stop(self) -> None:
        self._queue.put(None)

    def _loop(self) -> None:
        while (item := self._queue.get()) is not None:
            func, args, future, loop = item
            try:
                result = func(*args)
            except BaseException as err:  # noqa: BLE001 - handed to the waiting loop
                self._hand_over(loop, future, None, err)
            else:
                self._hand_over(loop, future, result, None)

    @staticmethod
    def _hand_over(
        loop: asyncio.AbstractEventLoop,
        future: asyncio.Future[Any],
        result: Any,
        error: BaseException | None,
    ) -> None:
        def settle() -> None:
            if future.done():  # cancelled by a timeout meanwhile
                return
            if error is not None:
                future.set_exception(error)
            else:
                future.set_result(result)

        # The loop may be closed when a call that hung returns after shutdown.
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(settle)


class ScreenCapture:
    """Grabs the chosen screen only when a frame is asked for, on one thread."""

    def __init__(
        self,
        grabber: Grabber,
        screen: int | None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._grabber = grabber
        self._wanted = screen
        self._clock = clock
        self._worker = _Worker()
        self._pending: asyncio.Future[tuple[bytes, float]] | None = None
        self._last: tuple[bytes, float] | None = None
        self._listed_at = 0.0
        self.screens: list[Screen] = []
        self.screen: Screen | None = None
        self.no_picture_reason: str | None = None
        self._capture_ms: list[float] = []

    def _pick(self, screens: list[Screen]) -> Screen | None:
        if self._wanted is None:
            return next(
                (s for s in screens if s.primary), screens[0] if screens else None
            )
        return next((s for s in screens if s.index == self._wanted), None)

    async def start(self) -> None:
        """List the screens and pick one; raise NoScreenAccess if that is not possible.

        A first grab that fails or hangs (for example a locked screen or a monitor
        that sleeps) is "no picture", not a start-up error: the helper serves and
        the picture comes back later.
        """
        try:
            screens = await asyncio.wait_for(
                self._worker.run(self._grabber.open), START_TIMEOUT
            )
        except TimeoutError:
            raise NoScreenAccess(
                f"listing the screens did not finish within {START_TIMEOUT:g} s"
            ) from None
        self._listed_at = self._clock()
        if not screens:
            raise NoScreenAccess("no screen to capture")
        self.screens, self.screen = screens, self._pick(screens)
        if self.screen is None:
            listed = ", ".join(f"{s.index} ({s.width}x{s.height})" for s in screens)
            raise NoScreenAccess(
                f"there is no screen {self._wanted}; screens: {listed}"
            )
        await self.frame()

    def _grab_once(self) -> tuple[bytes, float]:
        """On the capture thread: list the screens again if due, grab, shrink."""
        started = time.perf_counter()
        try:
            if self._clock() - self._listed_at >= SCREEN_RECHECK:
                screens = self._grabber.open()
                self._listed_at = self._clock()
                self.screens, self.screen = screens, self._pick(screens)
            if self.screen is None:
                raise ScreenMissing(f"screen {self._wanted} is not there")
            frame = downscale(*self._grabber.grab(self.screen))
        except CaptureError:
            raise
        except Exception as err:
            # Whatever else breaks in a grab is "no picture" too, never a 500.
            raise CaptureError(f"{type(err).__name__}: {err}") from err
        return frame, (time.perf_counter() - started) * 1000

    async def frame(self) -> bytes | None:
        """The latest frame, or None for "no picture" (see no_picture_reason)."""
        if self._last is not None and self._clock() - self._last[1] < MAX_AGE:
            return self._last[0]
        if self._pending is None:
            self._pending = self._worker.run(self._grab_once)
            self._pending.add_done_callback(self._grabbed)
        try:
            frame, _ = await asyncio.wait_for(
                asyncio.shield(self._pending), GRAB_TIMEOUT
            )
        except TimeoutError:
            self.no_picture_reason = "capture_timeout"
            return None
        except ScreenMissing:
            self.no_picture_reason = "screen_missing"
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
        """Close the grabber; a grab or close that hangs is abandoned, not awaited."""
        if self._pending is not None:
            await asyncio.wait({self._pending}, timeout=CLOSE_TIMEOUT)
        closing = self._worker.run(self._grabber.close)
        await asyncio.wait({closing}, timeout=CLOSE_TIMEOUT)
        if closing.done():
            closing.exception()  # retrieved: a failing close is not worth a warning
        else:
            closing.cancel()
        self._worker.stop()
