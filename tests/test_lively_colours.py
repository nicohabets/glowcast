"""Tests for the weighted region colour: livelier colours (#233, spike #40)."""

import colorsys
import math
import random
from collections.abc import Callable

from custom_components.glowcast.core.color_engine import compute
from custom_components.glowcast.core.models import Channel, Frame

WIDTH, HEIGHT = 64, 36
MIDDLE = Channel("m", "m", 0.0, 0.0)

Rgb = tuple[int, int, int]


def make_frame(paint: Callable[[float, float], Rgb]) -> Frame:
    pixels = bytearray()
    for row in range(HEIGHT):
        for col in range(WIDTH):
            pixels.extend(paint((col + 0.5) / WIDTH, (row + 0.5) / HEIGHT))
    return Frame(WIDTH, HEIGHT, bytes(pixels))


def hsv(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    return colorsys.rgb_to_hsv(*rgb)


def plain_average(frame: Frame) -> tuple[float, float, float]:
    """The lit pixels' plain average in the middle channel's region (the
    colour before #233)."""
    from custom_components.glowcast.core.color_engine import (
        BLACK_LEVEL,
        channel_region,
    )

    col0, col1, row0, row1 = channel_region(MIDDLE, WIDTH, HEIGHT)
    total = [0, 0, 0]
    lit = 0
    for row in range(row0, row1):
        for col in range(col0, col1):
            i = (row * WIDTH + col) * 3
            p = frame.pixels[i : i + 3]
            if max(p) > BLACK_LEVEL:
                lit += 1
                for k in range(3):
                    total[k] += p[k]
    return tuple(c / lit / 255 for c in total)


def colour(frame: Frame) -> tuple[float, float, float]:
    [result] = compute(frame, [MIDDLE], 1.0)
    return result.rgb


def test_a_small_colourful_object_leads_a_dull_region() -> None:
    """A purple spotlight on a dim stage: purple, brighter and more saturated."""
    frame = make_frame(
        lambda u, v: (
            (160, 60, 230) if math.hypot(u - 0.5, v - 0.5) < 0.08 else (40, 40, 46)
        )
    )
    weighted, average = hsv(colour(frame)), hsv(plain_average(frame))
    assert abs(weighted[0] - hsv((160 / 255, 60 / 255, 230 / 255))[0]) < 0.03
    assert weighted[1] > average[1] + 0.15
    assert weighted[2] > average[2] + 0.15


def test_one_colour_is_that_colour() -> None:
    frame = make_frame(lambda u, v: (200, 120, 40))
    assert colour(frame) == tuple(
        __import__("pytest").approx(c / 255, abs=0.005) for c in (200, 120, 40)
    )


def test_a_smooth_gradient_stays_near_its_average() -> None:
    frame = make_frame(
        lambda u, v: (round(240 - 120 * u), round(120 + 60 * v), round(60 + 150 * u))
    )
    w, a = colour(frame), plain_average(frame)
    assert max(abs(x - y) for x, y in zip(w, a, strict=True)) < 0.04


def test_white_with_a_small_red_object_stays_nearly_white() -> None:
    """A red object on a tenth of the region tints white, it does not turn it red."""
    frame = make_frame(
        lambda u, v: (
            (200, 30, 35) if 0.48 < u < 0.54 and 0.45 < v < 0.6 else (235, 238, 245)
        )
    )
    assert hsv(colour(frame))[1] <= 0.15


def test_grey_stays_grey() -> None:
    frame = make_frame(lambda u, v: (90 + int(40 * u),) * 3)
    r, g, b = colour(frame)
    assert r == g == b


def test_noise_does_not_make_the_colour_jump() -> None:
    """Two colours sharing a region about equally, with JPEG-like noise: the
    weighted average moves a little between frames, it does not flip."""
    rng = random.Random(7)
    base = make_frame(lambda u, v: (230, 40, 200) if u < 0.5 else (30, 220, 230))
    previous = None
    for _ in range(10):
        noisy = bytes(min(255, max(0, p + rng.randint(-10, 10))) for p in base.pixels)
        rgb = colour(Frame(WIDTH, HEIGHT, noisy))
        if previous is not None:
            assert (
                max(abs(x - y) for x, y in zip(rgb, previous, strict=True)) < 10 / 255
            )
        previous = rgb


def test_black_rules_are_unchanged() -> None:
    """Near-black pixels are skipped and a region below the lit share is black."""
    frame = make_frame(lambda u, v: (20, 18, 25))
    assert colour(frame) == (0.0, 0.0, 0.0)
    sparse = make_frame(
        lambda u, v: (
            (255, 0, 0)
            if (u, v) == (0.5 + 0.5 / WIDTH, 0.5 + 0.5 / HEIGHT)
            else (0, 0, 0)
        )
    )
    assert colour(sparse) == (0.0, 0.0, 0.0)
