"""Every pattern's pixels, exactly as the README documents them, and cycle's timing."""

from __future__ import annotations

import pytest
from fakes import FakeClock
from glowcast_screen.patterns import (
    CYCLE,
    FRAME_SIZE,
    FRAMES,
    HEIGHT,
    NAMES,
    WIDTH,
    PatternSource,
)


def pixel(frame: bytes, x: int, y: int) -> tuple[int, int, int]:
    i = (y * WIDTH + x) * 3
    return frame[i], frame[i + 1], frame[i + 2]


def all_pixels(frame: bytes) -> set[tuple[int, int, int]]:
    return {pixel(frame, x, y) for y in range(HEIGHT) for x in range(WIDTH)}


def test_every_frame_is_64x36_rgb() -> None:
    assert FRAME_SIZE == 6912
    for frame in FRAMES.values():
        assert len(frame) == FRAME_SIZE


@pytest.mark.parametrize(
    ("name", "rgb"),
    [
        ("red", (255, 0, 0)),
        ("green", (0, 255, 0)),
        ("blue", (0, 0, 255)),
        ("white", (255, 255, 255)),
        ("black", (0, 0, 0)),
    ],
)
def test_solid_patterns(name: str, rgb: tuple[int, int, int]) -> None:
    assert all_pixels(FRAMES[name]) == {rgb}


def test_left_red_right_blue() -> None:
    frame = FRAMES["left-red-right-blue"]
    for y in range(HEIGHT):
        for x in range(WIDTH):
            assert pixel(frame, x, y) == ((255, 0, 0) if x < 32 else (0, 0, 255))


def test_letterbox_has_five_black_rows_top_and_bottom_around_green() -> None:
    frame = FRAMES["letterbox"]
    for y in range(HEIGHT):
        expected = (0, 0, 0) if y < 5 or y >= 31 else (0, 255, 0)
        for x in range(WIDTH):
            assert pixel(frame, x, y) == expected


def test_pattern_names() -> None:
    assert set(NAMES) == {
        "red",
        "green",
        "blue",
        "white",
        "black",
        "left-red-right-blue",
        "letterbox",
        "cycle",
    }


def test_cycle_neighbours_differ_also_at_the_wrap() -> None:
    for i, name in enumerate(CYCLE):
        assert FRAMES[name] != FRAMES[CYCLE[(i + 1) % len(CYCLE)]]


def test_fixed_pattern_never_changes() -> None:
    clock = FakeClock()
    source = PatternSource("blue", clock)
    clock.now += 1000
    assert source.showing().name == "blue"
    assert source.showing().since == clock.now - 1000
    assert source.next_change() is None
    assert source.frame() == FRAMES["blue"]


def test_cycle_steps_at_the_interval_and_reports_since() -> None:
    clock = FakeClock()
    start = clock.now
    source = PatternSource("cycle", clock, interval=5)

    assert source.showing().name == "red"
    assert source.showing().since == start
    assert source.next_change() == start + 5

    clock.now = start + 4.999
    assert source.showing().name == "red"

    clock.now = start + 5
    assert source.showing().name == "green"
    assert source.showing().since == start + 5
    assert source.frame() == FRAMES["green"]

    clock.now = start + 6 * 5 + 2
    assert source.showing().name == "letterbox"
    assert source.showing().since == start + 30

    # After the last pattern it starts again with the first.
    clock.now = start + 7 * 5
    assert source.showing().name == "red"
    assert source.showing().since == start + 35


def test_unknown_pattern_and_bad_interval() -> None:
    with pytest.raises(ValueError):
        PatternSource("purple", FakeClock())
    with pytest.raises(ValueError):
        PatternSource("cycle", FakeClock(), interval=0)
