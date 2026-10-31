"""Tests for the liveliness levels: Calm, Normal and Lively (#16).

Normal is exactly v0.4: the colour engine and the light state give the same
numbers, to the last bit, as before the setting existed (V04_DIGEST was taken
on main before #16).
"""

import colorsys
import hashlib
import math
import random
from collections.abc import Callable
from typing import Any

import pytest

from custom_components.glowcast.core.color_engine import (
    FAR_BLEND,
    WEIGHT_FLOOR,
    compute,
)
from custom_components.glowcast.core.light_state import (
    CUT,
    CUT_FADE,
    LightState,
)
from custom_components.glowcast.core.liveliness import Liveliness, Profile
from custom_components.glowcast.core.models import Channel, ChannelColor, Frame

WIDTH, HEIGHT = 64, 36

Rgb = tuple[int, int, int]


def make_frame(paint: Callable[[float, float], Rgb]) -> Frame:
    pixels = bytearray()
    for row in range(HEIGHT):
        for col in range(WIDTH):
            pixels.extend(paint((col + 0.5) / WIDTH, (row + 0.5) / HEIGHT))
    return Frame(WIDTH, HEIGHT, bytes(pixels))


def _concert(x: float, y: float) -> Rgb:
    """Two spotlights on a dark stage: a mixed picture (#233)."""
    if math.hypot(x - 0.3, (y - 0.35) * 0.6) < 0.12:
        return 160, 60, 230
    if math.hypot(x - 0.72, (y - 0.35) * 0.6) < 0.1:
        return 250, 140, 30
    return 36, 34, 42


def _sunset(x: float, y: float) -> Rgb:
    if y < 0.58:
        return round(255 - 185 * x), round(140 - 50 * x), round(60 + 170 * x)
    return round(190 - 170 * x), 150, round(75 + 115 * x)


def _noisy(paint: Callable[[float, float], Rgb], seed: int) -> Frame:
    noise = random.Random(seed)
    return make_frame(
        lambda x, y: tuple(  # type: ignore[return-value]
            min(255, max(0, c + noise.randint(-10, 10))) for c in paint(x, y)
        )
    )


FRAMES = [
    make_frame(_concert),
    make_frame(_sunset),
    _noisy(_sunset, 1),
    make_frame(lambda x, y: (255, 255, 255) if x > 0.45 else (230, 20, 20)),
]
# The room's channels: a gradient strip around the TV, lamps at the sides, a
# lamp at the back, one without a depth (a Home Assistant light).
CHANNELS = [
    Channel("left", "left", -0.4, 0.0, y=0.8, light="strip"),
    Channel("top", "top", 0.0, 0.4, y=0.8, light="strip"),
    Channel("right", "right", 0.4, 0.0, y=0.8, light="strip"),
    Channel("lamp", "lamp", -1.0, -0.5, y=0.6, light="lamp"),
    Channel("back", "back", -1.0, -0.3, y=-0.8, light="back"),
    Channel("middle", "middle", 0.0, 0.0, y=0.0, light="middle"),
    Channel("ha", "ha", 1.0, 0.0),
]
# Targets for the light state: small steps (no cut), a cut, a drift, a black
# frame (the hold), and back.
SEQUENCE: list[tuple[float, tuple[float, float, float], bool]] = [
    (0.0, (0.8, 0.2, 0.2), False),
    (0.33, (0.75, 0.25, 0.2), False),
    (0.66, (0.1, 0.2, 0.9), False),  # a cut
    (1.0, (0.15, 0.25, 0.85), False),
    (1.33, (0.32, 0.3, 0.7), False),  # 0.17: a cut only when lively
    (1.66, (0.0, 0.0, 0.0), True),  # sudden black: held
    (6.0, (0.0, 0.0, 0.0), True),  # past the hold: to the glow
    (6.33, (0.9, 0.6, 0.1), False),
]


def v04_outputs(
    compute_kwargs: dict[str, Any] | None = None,
    target_kwargs: dict[str, Any] | None = None,
) -> str:
    """A digest of what the colour engine and the light state give on fixed
    frames and targets; the keyword arguments are a level's knobs."""
    out: list[Any] = []
    for frame in FRAMES:
        for intensity in (1.0, 1.5):
            out.append(
                [
                    c.rgb
                    for c in compute(
                        frame, CHANNELS, intensity, None, **(compute_kwargs or {})
                    )
                ]
            )
        out.append(
            [
                c.rgb
                for c in compute(
                    frame, CHANNELS, 1.0, (0, 64, 4, 32), **(compute_kwargs or {})
                )
            ]
        )
    lights = LightState()
    lights.fade_in({"a": (0.2, 0.2, 0.2), "b": None})
    for at, rgb, black in SEQUENCE:
        lights.set_target(
            [ChannelColor("a", rgb), ChannelColor("b", rgb[::-1])],
            black,
            at,
            **(target_kwargs or {}),
        )
        for step in range(9):
            now = at + step * 0.04
            out.append([c.rgb for c in lights.colors(now, 0.8)])
    return hashlib.sha256(repr(out).encode()).hexdigest()


# The digest of v04_outputs() on main before #16 (v0.4).
V04_DIGEST = "0d9df6c41f8ae56c234bbf6d68d9f6c86427d971b537e8ab854ac4ff07807e7c"


def knobs(level: Liveliness) -> tuple[dict[str, Any], dict[str, Any]]:
    """A level's knobs as the pipeline passes them: to compute, to set_target."""
    p = level.profile
    return (
        {"weight_floor": p.weight_floor, "far_blend": p.far_blend},
        {"fade_intervals": p.fade_intervals, "cut": p.cut, "cut_fade": p.cut_fade},
    )


def colours(frame: Frame, level: Liveliness) -> list[tuple[float, float, float]]:
    return [c.rgb for c in compute(frame, CHANNELS, 1.0, **knobs(level)[0])]


def saturation(rgb: tuple[float, float, float]) -> float:
    return colorsys.rgb_to_hsv(*rgb)[1]


# Normal is v0.4


def test_without_a_level_the_core_is_v04() -> None:
    assert v04_outputs() == V04_DIGEST


def test_normal_is_v04_to_the_last_bit() -> None:
    assert v04_outputs(*knobs(Liveliness.NORMAL)) == V04_DIGEST


def test_normal_is_the_cores_own_constants() -> None:
    assert Liveliness.NORMAL.profile == Profile(
        fade_intervals=1.0,
        cut=CUT,
        cut_fade=CUT_FADE,
        weight_floor=WEIGHT_FLOOR,
        far_blend=FAR_BLEND,
    )


def test_the_other_levels_differ() -> None:
    assert v04_outputs(*knobs(Liveliness.CALM)) != V04_DIGEST
    assert v04_outputs(*knobs(Liveliness.LIVELY)) != V04_DIGEST


def test_the_levels_are_stored_as_lower_case_words() -> None:
    assert [level.value for level in Liveliness] == ["calm", "normal", "lively"]


# The knobs go one way from Calm to Lively


def test_the_knobs_go_from_calm_to_lively() -> None:
    calm, normal, lively = (level.profile for level in Liveliness)
    assert calm.fade_intervals > normal.fade_intervals == lively.fade_intervals
    assert calm.cut is None  # a cut fades like everything else
    assert normal.cut is not None and lively.cut is not None
    assert lively.cut < normal.cut
    assert lively.cut_fade < normal.cut_fade
    assert calm.weight_floor > normal.weight_floor > lively.weight_floor
    assert calm.far_blend > normal.far_blend > lively.far_blend


# Colours


def test_calm_colours_are_softer_and_lively_ones_more_saturated() -> None:
    """On a mixed picture (spotlights on a dark stage) the colourful pixels
    weigh less at Calm and more at Lively."""
    stage = make_frame(_concert)
    mean = {
        level: sum(saturation(rgb) for rgb in colours(stage, level))
        for level in Liveliness
    }
    assert mean[Liveliness.CALM] < mean[Liveliness.NORMAL] < mean[Liveliness.LIVELY]


def test_lights_at_the_back_follow_the_whole_picture_more_at_calm() -> None:
    """The lamp at the back (y -0.8) on a picture that is red on its side
    and blue elsewhere: more blue at Calm, more red at Lively."""
    frame = make_frame(lambda x, y: (230, 20, 20) if x < 0.3 else (20, 20, 230))
    back = CHANNELS.index(next(c for c in CHANNELS if c.id == "back"))
    blue = {level: colours(frame, level)[back][2] for level in Liveliness}
    assert blue[Liveliness.CALM] > blue[Liveliness.NORMAL] > blue[Liveliness.LIVELY]


def test_lights_without_a_depth_only_change_by_the_weight() -> None:
    """A Home Assistant light (no depth) is never blended: with the same
    floor, every level gives it the same colour."""
    frame = make_frame(_concert)
    ha = [c for c in CHANNELS if c.y is None]
    for level in Liveliness:
        p = level.profile
        assert compute(
            frame, ha, 1.0, weight_floor=p.weight_floor, far_blend=p.far_blend
        ) == compute(frame, ha, 1.0, weight_floor=p.weight_floor, far_blend=0.0)


# The jitter limit of spike #40: no flicker on a still picture with noise. The
# chosen method (Normal) had at most 5 of 255 there, and the methods that
# flickered 90 and 196; in this test's frames Normal has about 7.
JITTER_LIMIT = 10 / 255


@pytest.mark.parametrize("level", list(Liveliness))
@pytest.mark.parametrize("paint", [_concert, _sunset], ids=["concert", "sunset"])
def test_no_flicker_on_a_noisy_still(
    level: Liveliness, paint: Callable[[float, float], Rgb]
) -> None:
    runs = [colours(_noisy(paint, seed), level) for seed in range(10)]
    jitter = max(
        max(run[ch][k] for run in runs) - min(run[ch][k] for run in runs)
        for ch in range(len(CHANNELS))
        for k in range(3)
    )
    assert jitter <= JITTER_LIMIT
    # Far below a cut, so noise never makes a fast fade.
    if level.profile.cut is not None:
        assert jitter < level.profile.cut / 3


# Fades


def fade(
    level: Liveliness,
    start: tuple[float, float, float],
    end: tuple[float, float, float],
) -> Callable[[float], float]:
    """Captures every second: `start` twice, then `end` at 2 s. Returns the
    first component at a time after 2 s."""
    lights = LightState()
    target_kwargs = knobs(level)[1]
    for at, rgb in ((0.0, start), (1.0, start), (2.0, end)):
        lights.set_target([ChannelColor("1", rgb)], False, at, **target_kwargs)

    def at(seconds: float) -> float:
        return lights.colors(2.0 + seconds, 1.0)[0].rgb[0]

    return at


def test_calm_fades_over_two_capture_intervals() -> None:
    at = fade(Liveliness.CALM, (0.8, 0.2, 0.2), (0.6, 0.2, 0.2))
    assert at(1.0) == pytest.approx(0.7)  # halfway after one interval
    assert at(2.0) == pytest.approx(0.6)


def test_calm_has_no_fast_cut() -> None:
    """A jump from red to blue fades like everything else at Calm."""
    at = fade(Liveliness.CALM, (1.0, 0.0, 0.0), (0.0, 0.0, 1.0))
    assert at(0.5) == pytest.approx(0.75)
    assert at(1.0) == pytest.approx(0.5)


@pytest.mark.parametrize("level", [Liveliness.NORMAL, Liveliness.LIVELY])
def test_a_big_jump_is_a_cut(level: Liveliness) -> None:
    at = fade(level, (1.0, 0.0, 0.0), (0.0, 0.0, 1.0))
    cut_fade = level.profile.cut_fade
    assert at(cut_fade / 2) == pytest.approx(0.5)
    assert at(cut_fade) == pytest.approx(0.0)


def test_lively_cuts_are_faster() -> None:
    normal = fade(Liveliness.NORMAL, (1.0, 0.0, 0.0), (0.0, 0.0, 1.0))
    lively = fade(Liveliness.LIVELY, (1.0, 0.0, 0.0), (0.0, 0.0, 1.0))
    # At the first send after the frame (40 ms at 25 per second).
    assert lively(0.04) < normal(0.04)


def test_a_smaller_jump_is_a_cut_only_at_lively() -> None:
    """A jump of 0.2: between Lively's cut and Normal's."""
    normal = fade(Liveliness.NORMAL, (0.8, 0.2, 0.2), (0.6, 0.2, 0.2))
    lively = fade(Liveliness.LIVELY, (0.8, 0.2, 0.2), (0.6, 0.2, 0.2))
    assert normal(0.5) == pytest.approx(0.7)  # over the capture interval
    assert lively(Liveliness.LIVELY.profile.cut_fade) == pytest.approx(0.6)


@pytest.mark.parametrize("level", list(Liveliness))
def test_the_hold_on_black_is_the_same_at_every_level(level: Liveliness) -> None:
    lights = LightState()
    target_kwargs = knobs(level)[1]
    for now in (0.0, 1.0, 2.0):
        lights.set_target(
            [ChannelColor("1", (1.0, 0.0, 0.0))], False, now, **target_kwargs
        )
    lights.set_target([ChannelColor("1", (0.0, 0.0, 0.0))], True, 3.0, **target_kwargs)
    assert lights.colors(6.4, 1.0)[0].rgb == pytest.approx((1.0, 0.0, 0.0))


def test_a_new_level_applies_from_the_next_target() -> None:
    """The fade in progress keeps its own cut fade; the next target uses the
    new level's."""
    lights = LightState()
    normal, lively = knobs(Liveliness.NORMAL)[1], knobs(Liveliness.LIVELY)[1]
    lights.set_target([ChannelColor("1", (1.0, 0.0, 0.0))], False, 0.0, **normal)
    lights.set_target([ChannelColor("1", (1.0, 0.0, 0.0))], False, 1.0, **normal)
    lights.set_target([ChannelColor("1", (0.0, 0.0, 1.0))], False, 2.0, **normal)
    assert lights.colors(2.04, 1.0)[0].rgb[0] == pytest.approx(0.5)
    lights.set_target([ChannelColor("1", (1.0, 0.0, 0.0))], False, 3.0, **lively)
    assert lights.colors(3.04, 1.0)[0].rgb[0] == pytest.approx(1.0)
