"""Fakes for the helper's tests: a clock, a grabber, zeroconf. No screen, no network."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from typing import Any

from glowcast_screen.capture import NoScreenAccess, Screen


class FakeClock:
    def __init__(self, now: float = 1_800_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def bgra_image(
    width: int, height: int, pixel: Callable[[int, int], tuple[int, int, int]]
) -> bytes:
    """A BGRA grab as mss returns it, from an RGB function of (x, y)."""
    out = bytearray()
    for y in range(height):
        for x in range(width):
            r, g, b = pixel(x, y)
            out += bytes((b, g, r, 255))
    return bytes(out)


def left_red_right_blue(width: int, height: int) -> bytes:
    return bgra_image(
        width, height, lambda x, y: (255, 0, 0) if x < width // 2 else (0, 0, 255)
    )


class FakeGrabber:
    """Two screens: an ultrawide main screen and a second monitor to its right."""

    def __init__(self, screens: list[Screen] | None = None) -> None:
        self.screens = screens or [
            Screen(index=1, width=512, height=216, left=0, top=0, primary=True),
            Screen(index=2, width=256, height=144, left=512, top=0, primary=False),
        ]
        self.grabs: list[int] = []
        self.threads: set[int] = set()
        self.fail: Exception | None = None
        self.no_access: str | None = None
        self.block: threading.Event | None = None
        self.closed = False

    def open(self) -> list[Screen]:
        self.threads.add(threading.get_ident())
        if self.no_access:
            raise NoScreenAccess(self.no_access)
        return self.screens

    def grab(self, screen: Screen) -> tuple[int, int, bytes]:
        self.threads.add(threading.get_ident())
        self.grabs.append(screen.index)
        if self.block is not None:
            self.block.wait(5)
        if self.fail is not None:
            raise self.fail
        # Screen 1 is all red, screen 2 left red and right blue.
        if screen.index == 1:
            data = bgra_image(screen.width, screen.height, lambda x, y: (255, 0, 0))
        else:
            data = left_red_right_blue(screen.width, screen.height)
        return screen.width, screen.height, data

    def close(self) -> None:
        self.closed = True


class FakeZeroconf:
    """Records what is announced and withdrawn; sends nothing."""

    def __init__(self) -> None:
        self.registered: list[Any] = []
        self.unregistered: list[Any] = []
        self.closed = False

    async def async_register_service(self, info: Any, **kwargs: Any) -> Awaitable[None]:
        self.registered.append(info)
        return asyncio.sleep(0)

    async def async_unregister_service(self, info: Any) -> Awaitable[None]:
        self.unregistered.append(info)
        return asyncio.sleep(0)

    async def async_close(self) -> None:
        self.closed = True
