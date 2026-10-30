"""Tests for the letterbox memory: synthetic frames and the time as an argument."""

import statistics
import time
from collections.abc import Callable

from custom_components.glowcast.core.color_engine import channel_region, compute
from custom_components.glowcast.core.letterbox import (
    NO_BARS,
    STABLE,
    Bars,
    Letterbox,
    measure,
)
from custom_components.glowcast.core.models import Channel, Frame

WIDTH, HEIGHT = 64, 36
BLACK = (0, 0, 0)
RED = (255, 0, 0)
BLUE = (0, 0, 255)
GREY = (90, 90, 90)
WHITE = (240, 240, 240)

Rgb = tuple[int, int, int]


def make_frame(paint: Callable[[int, int], Rgb]) -> Frame:
    """paint(col, row) in frame pixels."""
    pixels = bytearray()
    for row in range(HEIGHT):
        for col in range(WIDTH):
            pixels.extend(paint(col, row))
    return Frame(WIDTH, HEIGHT, bytes(pixels))


def letterbox(top: int, bottom: int, picture: Rgb = GREY) -> Frame:
    return make_frame(
        lambda col, row: BLACK if row < top or row >= HEIGHT - bottom else picture
    )


def pillarbox(left: int, right: int, picture: Rgb = GREY) -> Frame:
    return make_frame(
        lambda col, row: BLACK if col < left or col >= WIDTH - right else picture
    )


def feed(box: Letterbox, frame: Frame, start: float, seconds: float) -> Bars:
    """Frames at 3 per second from `start` for `seconds`."""
    bars = box.bars
    steps = round(seconds * 3)
    for step in range(steps + 1):
        bars = box.update(frame, start + step / 3)
    return bars


# One frame


def test_no_bars() -> None:
    assert measure(make_frame(lambda col, row: GREY)) == (0, 0, 0, 0)


def test_letterbox_and_pillarbox_are_measured_per_side() -> None:
    assert measure(letterbox(5, 3)) == (5, 3, 0, 0)
    assert measure(pillarbox(8, 6)) == (0, 0, 8, 6)


def test_subtitles_in_the_bottom_bar_count_as_bar() -> None:
    """White text in the middle of the bar, as subtitles are, is still bar."""
    frame = make_frame(
        lambda col, row: (
            BLACK
            if row < 5
            else GREY
            if row < HEIGHT - 5
            else WHITE
            if row in (HEIGHT - 4, HEIGHT - 3) and 16 <= col < 48
            else BLACK
        )
    )
    assert measure(frame) == (5, 5, 0, 0)


def test_dark_noise_in_a_bar_is_still_bar() -> None:
    """Video black and JPEG noise stay at or below the black level."""
    frame = make_frame(lambda col, row: (20, 16, 24) if row < 4 else GREY)
    assert measure(frame)[0] == 4


def test_a_black_run_longer_than_a_quarter_is_not_a_bar() -> None:
    """A dark top half is a dark scene: that side says nothing."""
    assert measure(letterbox(HEIGHT // 2, 0)) == (None, 0, 0, 0)


# Over frames


def test_bars_are_taken_after_they_were_stable() -> None:
    box = Letterbox()
    frame = letterbox(5, 3)
    assert feed(box, frame, 0.0, STABLE - 0.4) == NO_BARS
    assert feed(box, frame, STABLE - 0.1, 0.4) == Bars(5, 3, 0, 0)


def test_a_new_width_follows_after_it_was_stable() -> None:
    box = Letterbox()
    feed(box, letterbox(5, 3), 0.0, 3.0)
    assert feed(box, letterbox(1, 2), 4.0, STABLE - 0.4) == Bars(5, 3, 0, 0)
    # The top bar follows (5 to 1); the bottom one (3 to 2) is within the
    # tolerance, so it stays.
    assert feed(box, letterbox(1, 2), 4.0 + STABLE, 1.0) == Bars(1, 3, 0, 0)


def test_a_short_dark_scene_does_not_move_the_picture() -> None:
    """Black top rows for less than STABLE change nothing."""
    box = Letterbox()
    feed(box, letterbox(0, 0), 0.0, 3.0)
    dark_top = make_frame(lambda col, row: BLACK if row < 7 else RED)
    feed(box, dark_top, 3.5, STABLE - 0.5)
    assert feed(box, letterbox(0, 0), 3.5 + STABLE, 3.0) == NO_BARS


def test_a_black_frame_keeps_the_bars() -> None:
    box = Letterbox()
    feed(box, letterbox(5, 5), 0.0, 3.0)
    assert feed(box, make_frame(lambda col, row: BLACK), 3.5, 10.0) == Bars(5, 5, 0, 0)


def test_subtitles_appearing_and_going_do_not_move_the_picture() -> None:
    box = Letterbox()
    plain = letterbox(5, 5)
    with_text = make_frame(
        lambda col, row: (
            WHITE
            if row == HEIGHT - 3 and 12 <= col < 52
            else BLACK
            if row < 5 or row >= HEIGHT - 5
            else GREY
        )
    )
    feed(box, plain, 0.0, 3.0)
    t = 3.0
    for _ in range(5):
        assert feed(box, with_text, t, 3.0) == Bars(5, 5, 0, 0)
        assert feed(box, plain, t + 3.0, 1.0) == Bars(5, 5, 0, 0)
        t += 4.0


def test_a_bar_edge_halfway_through_a_row_settles() -> None:
    """Widths that alternate by one pixel are one width: the narrowest."""
    box = Letterbox()
    for step in range(12):
        box.update(letterbox(4 + step % 2, 0), step / 3)
    assert box.bars == Bars(4, 0, 0, 0)


def test_positions_map_onto_the_picture_inside_the_bars() -> None:
    """-1..1 run from the picture's edges, not the frame's."""
    bars = Bars(6, 6, 8, 8)
    col0, col1, row0, row1 = bars.picture(WIDTH, HEIGHT)
    assert (col0, col1, row0, row1) == (8, 56, 6, 30)
    top = channel_region(Channel("t", "t", 0.0, 1.0), WIDTH, HEIGHT, (8, 56, 6, 30))
    bottom = channel_region(Channel("b", "b", 0.0, -1.0), WIDTH, HEIGHT, (8, 56, 6, 30))
    left = channel_region(Channel("l", "l", -1.0, 0.0), WIDTH, HEIGHT, (8, 56, 6, 30))
    assert top[2] == 6 and bottom[3] == 30
    assert left[0] == 8
    assert all(8 <= c <= 56 for c in (*top[:2], *bottom[:2], *left[:2]))


def test_a_dark_strip_next_to_the_bar_no_longer_greys_the_top_light() -> None:
    """The case of #5: a wide bar plus a dark strip of picture made the top
    light a dim grey; inside the bars it gets the picture's colour."""
    frame = make_frame(
        lambda col, row: (
            BLACK if row < 8 or row >= HEIGHT - 8 else (40, 40, 40) if row < 11 else RED
        )
    )
    top = Channel("top", "top", 0.0, 1.0)
    bars = feed(Letterbox(), frame, 0.0, 3.0)
    assert bars == Bars(8, 8, 0, 0)
    [inside] = compute(frame, [top], 1.0, bars.picture(WIDTH, HEIGHT))
    [whole] = compute(frame, [top], 1.0)
    assert whole.rgb[0] < 0.2  # a dim grey
    assert inside.rgb[0] > 0.5 > inside.rgb[2] * 2


def test_measuring_is_cheap() -> None:
    """Within the colour engine's budget with room to spare (architecture 3.3)."""
    frame = letterbox(5, 3)
    timings = []
    for _ in range(20):
        start = time.perf_counter()
        measure(frame)
        timings.append(time.perf_counter() - start)
    assert statistics.median(timings) * 1000 < 5
