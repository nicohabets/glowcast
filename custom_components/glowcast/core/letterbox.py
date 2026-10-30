"""Letterbox bars: where the picture sits inside black bars (architecture 3.3).

Pure and synchronous, no Home Assistant imports: the time is an argument.
`measure()` finds the bars of one frame; `Letterbox` remembers them over
frames and only takes a new width once it has been stable for STABLE seconds,
so a dark scene or a subtitle does not move the lights' regions (#34).
"""

from collections.abc import Callable
from typing import NamedTuple

from .color_engine import BLACK_LEVEL, Picture, is_black
from .models import Frame

# A side's new bar width is taken once it has been measured this long.
STABLE = 2.0
# A bar is at most this share of the frame per side; a longer black run is a
# dark scene, not a bar.
MAX_BAR = 0.25
# A row or column with at most this share of lit pixels is black.
BAR_LIT_SHARE = 0.05
# Rows count as bar when the outer parts of the picture's width (this share at
# each side) are black, whatever the middle shows: subtitles sit in the
# middle of the bottom bar.
ROW_EDGE = 0.15
# Widths that differ by this many pixels count as the same, so a bar edge
# that falls halfway through a row does not keep the area moving.
TOLERANCE = 1


class Bars(NamedTuple):
    """The black bars of a frame, in pixels of the frame per side."""

    top: int = 0
    bottom: int = 0
    left: int = 0
    right: int = 0

    def picture(self, width: int, height: int) -> Picture:
        """The part of a width x height frame inside the bars."""
        return self.left, width - self.right, self.top, height - self.bottom


NO_BARS = Bars()

Side = int | None


def measure(frame: Frame) -> tuple[Side, Side, Side, Side]:
    """The bars of one frame per side (top, bottom, left, right).

    None for a side whose black run is longer than MAX_BAR: that is a dark
    scene, so it says nothing about the bars. Columns are measured first over
    the whole height; rows within the columns of the picture.
    """
    width, height = frame.width, frame.height
    pixels = frame.pixels

    def lit(col: int, row: int) -> bool:
        i = (row * width + col) * 3
        return (
            pixels[i] > BLACK_LEVEL
            or pixels[i + 1] > BLACK_LEVEL
            or pixels[i + 2] > BLACK_LEVEL
        )

    def column_black(col: int) -> bool:
        count = sum(1 for row in range(height) if lit(col, row))
        return count <= BAR_LIT_SHARE * height

    left = _run(range(width), column_black, width)
    right = _run(range(width - 1, -1, -1), column_black, width)
    col0 = left or 0
    col1 = width - (right or 0)
    edge = max(1, round(ROW_EDGE * (col1 - col0)))
    outer = [*range(col0, col0 + edge), *range(col1 - edge, col1)]

    def row_black(row: int) -> bool:
        count = sum(1 for col in outer if lit(col, row))
        return count <= BAR_LIT_SHARE * len(outer)

    top = _run(range(height), row_black, height)
    bottom = _run(range(height - 1, -1, -1), row_black, height)
    return top, bottom, left, right


def _run(lines: range, black: Callable[[int], bool], size: int) -> Side:
    """How many lines from the edge are black, or None beyond MAX_BAR."""
    limit = int(MAX_BAR * size)
    count = 0
    for line in lines:
        if not black(line):
            return count
        count += 1
        if count > limit:
            return None
    return count


class Letterbox:
    """The bars over frames: a side changes after STABLE seconds of a new width."""

    def __init__(self) -> None:
        """Start without bars: the whole frame is the picture."""
        self._bars = list(NO_BARS)
        # Per side: the new width seen, the narrowest seen meanwhile, since when.
        self._pending: list[tuple[int, int, float] | None] = [None] * 4

    @property
    def bars(self) -> Bars:
        """The bars the lights' regions use now."""
        return Bars(*self._bars)

    def update(self, frame: Frame, now: float) -> Bars:
        """Measure a frame and return the bars to use for it.

        An (almost) black frame keeps the bars as they are.
        """
        if not is_black(frame):
            for side, width in enumerate(measure(frame)):
                self._update_side(side, width, now)
        return self.bars

    def _update_side(self, side: int, width: Side, now: float) -> None:
        if width is None:
            return  # a dark scene on this side: nothing learned
        if abs(width - self._bars[side]) <= TOLERANCE:
            self._pending[side] = None
            return
        pending = self._pending[side]
        if pending is None or abs(width - pending[0]) > TOLERANCE:
            self._pending[side] = (width, width, now)
            return
        seen, narrowest, since = pending
        narrowest = min(narrowest, width)
        if now - since >= STABLE:
            # The narrowest bar seen, so no row of the picture is cut off.
            self._bars[side] = narrowest
            self._pending[side] = None
        else:
            self._pending[side] = (seen, narrowest, since)
