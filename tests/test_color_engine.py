"""Tests for the colour engine: synthetic frames, no hardware."""

import colorsys
import statistics
import time
from collections.abc import Callable

import pytest

from custom_components.glowcast.core.color_engine import (
    channel_region,
    compute,
    is_all_zero,
    is_black,
)
from custom_components.glowcast.core.models import Channel, ChannelColor, Frame

WIDTH, HEIGHT = 64, 36

RED = (255, 0, 0)
BLUE = (0, 0, 255)
GREEN = (0, 255, 0)
YELLOW = (255, 255, 0)
BLACK = (0, 0, 0)
GREY = (128, 128, 128)

Rgb = tuple[int, int, int]


def make_frame(paint: Callable[[float, float], Rgb]) -> Frame:
    """Build a frame; paint(u, v) gets the pixel centre, 0..1 from left / top."""
    pixels = bytearray()
    for row in range(HEIGHT):
        for col in range(WIDTH):
            pixels.extend(paint((col + 0.5) / WIDTH, (row + 0.5) / HEIGHT))
    return Frame(width=WIDTH, height=HEIGHT, pixels=bytes(pixels))


def solid(rgb: Rgb) -> Frame:
    return make_frame(lambda u, v: rgb)


def channel(x: float, z: float, id_: str = "c") -> Channel:
    return Channel(id=id_, name=id_, x=x, z=z)


def colour_of(frame: Frame, ch: Channel, intensity: float = 1.0) -> tuple:
    [result] = compute(frame, [ch], intensity)
    assert result.channel_id == ch.id
    return result.rgb


def assert_close(rgb: tuple, expected: Rgb, tolerance: float = 0.02) -> None:
    assert rgb == pytest.approx(tuple(c / 255 for c in expected), abs=tolerance)


# Position


def test_left_and_right_differ() -> None:
    frame = make_frame(lambda u, v: RED if u < 0.5 else BLUE)
    assert_close(colour_of(frame, channel(-1, 0)), RED)
    assert_close(colour_of(frame, channel(1, 0)), BLUE)


def test_top_and_bottom_differ() -> None:
    frame = make_frame(lambda u, v: GREEN if v < 0.5 else YELLOW)
    assert_close(colour_of(frame, channel(0, 1)), GREEN)
    assert_close(colour_of(frame, channel(0, -1)), YELLOW)


def test_region_is_30_percent_and_stays_inside_the_frame() -> None:
    for x in (-1, -0.5, 0, 0.5, 1):
        for z in (-1, 0, 1):
            col0, col1, row0, row1 = channel_region(channel(x, z), WIDTH, HEIGHT)
            assert 0 <= col0 < col1 <= WIDTH
            assert 0 <= row0 < row1 <= HEIGHT
            assert col1 - col0 == round(0.3 * WIDTH)
            assert row1 - row0 == round(0.3 * HEIGHT)
    # Linear mapping: the centre of the screen samples the centre.
    col0, col1, row0, row1 = channel_region(channel(0, 0), WIDTH, HEIGHT)
    assert (col0 + col1) / 2 == pytest.approx(WIDTH / 2, abs=1)
    assert (row0 + row1) / 2 == pytest.approx(HEIGHT / 2, abs=1)


def test_positions_outside_the_range_are_clamped() -> None:
    assert channel_region(channel(-3, 5), WIDTH, HEIGHT) == channel_region(
        channel(-1, 1), WIDTH, HEIGHT
    )


def test_output_keeps_channel_order_and_ids() -> None:
    channels = [channel(1, 0, "right"), channel(-1, 0, "left"), channel(0, 0, "mid")]
    result = compute(solid(GREY), channels, 1.0)
    assert [c.channel_id for c in result] == ["right", "left", "mid"]
    assert all(isinstance(c, ChannelColor) for c in result)


def test_no_channels() -> None:
    assert compute(solid(GREY), [], 1.0) == []


# Black


def test_letterbox_bars_do_not_darken_the_result() -> None:
    # A 2.39:1 film on a 16:9 screen: bars of about 12.8 % at top and bottom.
    bar = (1 - (16 / 9) / 2.39) / 2
    frame = make_frame(lambda u, v: BLACK if v < bar or v > 1 - bar else RED)
    for ch in (channel(0, 1), channel(0, -1), channel(-1, 1), channel(1, -1)):
        assert_close(colour_of(frame, ch), RED)


def test_near_black_noise_in_the_bars_is_ignored() -> None:
    frame = make_frame(lambda u, v: (12, 14, 10) if v < 0.13 else BLUE)
    assert_close(colour_of(frame, channel(0, 1)), BLUE)


def test_black_region_is_black() -> None:
    assert colour_of(solid(BLACK), channel(0, 0)) == (0.0, 0.0, 0.0)


def test_almost_black_region_is_black() -> None:
    # A few bright pixels (well under the threshold share) in a dark region.
    frame = make_frame(lambda u, v: RED if u < 0.01 and v < 0.05 else (8, 8, 8))
    assert colour_of(frame, channel(-1, 1)) == (0.0, 0.0, 0.0)


def test_dark_picture_that_is_not_black_keeps_its_colour() -> None:
    assert_close(colour_of(solid((60, 20, 20)), channel(0, 0)), (60, 20, 20))


def test_whole_frame_black() -> None:
    assert is_black(solid(BLACK))
    assert is_black(solid((20, 24, 18)))  # video black and noise
    assert not is_black(solid((60, 20, 20)))  # dark, but a picture


def test_a_small_bright_element_does_not_make_the_frame_a_picture() -> None:
    # 4 % of the frame lit (under the 5 % share), all in one corner.
    frame = make_frame(lambda u, v: (230, 230, 230) if u < 0.2 and v < 0.2 else BLACK)
    assert is_black(frame)
    # ... even though that corner's channel is not black.
    assert colour_of(frame, channel(-1, 1)) != (0.0, 0.0, 0.0)


def test_letterboxed_film_is_not_black() -> None:
    frame = make_frame(lambda u, v: BLACK if v < 0.13 or v > 0.87 else (90, 60, 40))
    assert not is_black(frame)


def test_all_zero_is_no_picture() -> None:
    assert is_all_zero(solid(BLACK))  # what a protected app delivers (#68)
    assert not is_all_zero(solid((1, 0, 0)))  # the darkest scene still counts
    # One lit pixel is enough: subtitles or player controls over no picture.
    assert not is_all_zero(
        make_frame(lambda u, v: (200, 200, 200) if u < 0.02 else BLACK)
    )


# Intensity


def test_intensity_scales_saturation_and_keeps_hue() -> None:
    # A partly saturated orange, so a boost is visible.
    frame = solid((200, 150, 120))
    h0, s0, v0 = colorsys.rgb_to_hsv(200 / 255, 150 / 255, 120 / 255)

    r, g, b = colour_of(frame, channel(0, 0), 0.0)
    assert r == pytest.approx(g) == pytest.approx(b)
    assert max(r, g, b) == pytest.approx(v0, abs=0.01)

    as_on_screen = colour_of(frame, channel(0, 0), 1.0)
    assert_close(as_on_screen, (200, 150, 120))

    h2, s2, v2 = colorsys.rgb_to_hsv(*colour_of(frame, channel(0, 0), 2.0))
    assert h2 == pytest.approx(h0, abs=0.01)
    assert s2 == pytest.approx(min(1.0, 2 * s0), abs=0.01)
    assert v2 == pytest.approx(v0, abs=0.01)


def test_intensity_saturation_is_capped() -> None:
    _, s, _ = colorsys.rgb_to_hsv(*colour_of(solid((255, 60, 60)), channel(0, 0), 2.0))
    assert s == pytest.approx(1.0)


def test_intensity_keeps_black_black() -> None:
    assert colour_of(solid(BLACK), channel(0, 0), 2.0) == (0.0, 0.0, 0.0)


# Performance


def test_sixteen_channels_on_a_64x36_frame_within_budget() -> None:
    frame = make_frame(lambda u, v: (int(u * 255), int(v * 255), 128))
    channels = [
        channel(-1 + 2 * (i % 4) / 3, -1 + 2 * (i // 4) / 3, str(i)) for i in range(16)
    ]
    timings = []
    for _ in range(20):
        start = time.perf_counter()
        compute(frame, channels, 1.5)
        timings.append(time.perf_counter() - start)
    median_ms = statistics.median(timings) * 1000
    assert median_ms < 20, f"compute() took {median_ms:.1f} ms (median of 20)"


# Real channel positions (architecture section 2, decision D12)


def test_tv_strip_channels_sample_the_inner_area() -> None:
    """TV gradient-strip channels sit between -0.4 and 0.4, so without a depth
    and a light to stretch them by (#23, `test_strip_edges.py`) they miss the edges.

    The four corners of that range are assumed positions; section 2 records
    only the range. Each screen edge has its own colour in the outer 12 %; the
    channels' regions run from about 15 % to 45 % in from the edges.
    """
    edge = 0.12
    frame = make_frame(
        lambda u, v: (
            RED
            if u < edge
            else BLUE
            if u > 1 - edge
            else GREEN
            if v < edge
            else YELLOW
            if v > 1 - edge
            else GREY
        )
    )
    for x in (-0.4, 0.4):
        for z in (-0.4, 0.4):
            ch = channel(x, z)
            col0, col1, row0, row1 = channel_region(ch, WIDTH, HEIGHT)
            assert col0 >= edge * WIDTH and col1 <= (1 - edge) * WIDTH
            assert row0 >= edge * HEIGHT and row1 <= (1 - edge) * HEIGHT
            assert_close(colour_of(frame, ch), GREY)
