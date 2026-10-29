"""Synthetic test frames: known pixels without a screen or a display.

The pixel values are part of the documentation (README "Test patterns"), so
end-to-end tests (#112) and the test bench (#134) can check colours against them.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

WIDTH = 64
HEIGHT = 36
FRAME_SIZE = WIDTH * HEIGHT * 3

RED = (255, 0, 0)
GREEN = (0, 255, 0)
BLUE = (0, 0, 255)
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)

# letterbox: a 2.39:1 film on a 16:9 screen has bars of about 12.8 % (4.6 rows);
# rows 0-4 and 31-35 are black, rows 5-30 are the picture.
LETTERBOX_BAR_ROWS = 5
# Green, so that letterbox looks different at the lights from both its neighbours
# in the cycle (left-red-right-blue before it, red after it).
LETTERBOX_PICTURE = GREEN


def _solid(rgb: tuple[int, int, int]) -> bytes:
    return bytes(rgb) * (WIDTH * HEIGHT)


def _left_red_right_blue() -> bytes:
    half = WIDTH // 2
    return (bytes(RED) * half + bytes(BLUE) * half) * HEIGHT


def _letterbox() -> bytes:
    bar = bytes(BLACK) * (WIDTH * LETTERBOX_BAR_ROWS)
    picture = bytes(LETTERBOX_PICTURE) * (WIDTH * (HEIGHT - 2 * LETTERBOX_BAR_ROWS))
    return bar + picture + bar


FRAMES: dict[str, bytes] = {
    "red": _solid(RED),
    "green": _solid(GREEN),
    "blue": _solid(BLUE),
    "white": _solid(WHITE),
    "black": _solid(BLACK),
    "left-red-right-blue": _left_red_right_blue(),
    "letterbox": _letterbox(),
}

# The order cycle steps through; no two neighbours (also at the wrap) look alike.
CYCLE = ("red", "green", "blue", "white", "black", "left-red-right-blue", "letterbox")
NAMES = (*FRAMES, "cycle")
DEFAULT_INTERVAL = 5.0


@dataclass(frozen=True)
class Showing:
    """The pattern on show and the clock time it started."""

    name: str
    since: float


class PatternSource:
    """Serves one pattern, or steps through CYCLE every `interval` seconds."""

    def __init__(
        self, name: str, clock: Callable[[], float], interval: float = DEFAULT_INTERVAL
    ) -> None:
        if name not in NAMES:
            raise ValueError(f"unknown pattern {name!r}")
        if interval <= 0:
            raise ValueError("the interval must be positive")
        self.name = name
        self.interval = interval
        self._clock = clock
        self._start = clock()

    def showing(self) -> Showing:
        if self.name != "cycle":
            return Showing(self.name, self._start)
        step = math.floor((self._clock() - self._start) / self.interval)
        return Showing(CYCLE[step % len(CYCLE)], self._start + step * self.interval)

    def next_change(self) -> float | None:
        """Clock time of the next step of cycle, or None for a fixed pattern."""
        if self.name != "cycle":
            return None
        return self.showing().since + self.interval

    def frame(self) -> bytes:
        return FRAMES[self.showing().name]
