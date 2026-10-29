"""Tests for the light state: fading, the hold on black, brightness and glow.

Pure and synchronous: the time is an argument (architecture 3.5).
"""

import itertools
from typing import Any

import pytest

from custom_components.glowcast.core.light_state import BLACK_HOLD, LightState
from custom_components.glowcast.core.models import ChannelColor

RED = (1.0, 0.0, 0.0)
BLUE = (0.0, 0.0, 1.0)
BLACK = (0.0, 0.0, 0.0)
RED_SENT = (1.0, 0.0, 0.0)  # red at brightness 100 %: no glow mixed in (#43)
GLOW_ONLY = (0.10, 0.078, 0.050)  # black at brightness 100 %
WARM = (1.0, 0.78, 0.50)  # the glow's colour


def target(rgb: tuple[float, float, float], *ids: str) -> list[ChannelColor]:
    return [ChannelColor(i, rgb) for i in ids or ("1",)]


def rgb_at(lights: LightState, now: float, brightness: float = 1.0) -> Any:
    [color] = lights.colors(now, brightness)
    return color.rgb


def approx_rgb(rgb: tuple[float, float, float]) -> Any:
    return pytest.approx(rgb, abs=0.005)


def picture_then_black(black_at: float) -> LightState:
    """Red captured every second up to 2 s, then a black frame at `black_at`."""
    lights = LightState()
    for now in (0.0, 1.0, 2.0):
        lights.set_target(target(RED), False, now)
    lights.set_target(target(BLACK), True, black_at)
    return lights


# Fading


def test_nothing_to_show_before_the_first_target() -> None:
    assert LightState().colors(0.0, 1.0) == []


def test_the_first_target_is_shown_at_once() -> None:
    lights = LightState()
    lights.set_target(target(RED), False, 5.0)
    assert rgb_at(lights, 5.0) == approx_rgb(RED_SENT)


def test_fades_linearly_over_the_measured_capture_interval() -> None:
    lights = LightState()
    lights.set_target(target(RED), False, 0.0)
    lights.set_target(target(RED), False, 2.0)  # measured interval: 2 s
    lights.set_target(target(BLUE), False, 4.0)
    assert rgb_at(lights, 4.0) == approx_rgb(RED_SENT)
    assert rgb_at(lights, 4.5) == approx_rgb((0.75, 0.0, 0.25))
    assert rgb_at(lights, 5.0) == approx_rgb((0.50, 0.0, 0.50))
    assert rgb_at(lights, 6.0) == approx_rgb(BLUE)
    assert rgb_at(lights, 9.0) == approx_rgb(BLUE)  # stays there


def test_a_new_target_fades_from_where_the_lights_are() -> None:
    lights = LightState()
    lights.set_target(target(RED), False, 0.0)
    lights.set_target(target(BLUE), False, 1.0)
    assert rgb_at(lights, 1.5) == approx_rgb((0.50, 0.0, 0.50))
    lights.set_target(target(BLACK), False, 1.5)  # half way to blue
    assert rgb_at(lights, 1.5) == approx_rgb((0.50, 0.0, 0.50))
    assert rgb_at(lights, 1.75) == approx_rgb((0.25, 0.0, 0.25))


# Brightness and the minimal glow (architecture 3.5)


@pytest.mark.parametrize(
    ("brightness", "engine", "expected"),
    [
        # Black: the full warm glow, never brighter than the brightness.
        (0.80, BLACK, (0.10, 0.078, 0.050)),
        (0.40, BLACK, (0.10, 0.078, 0.050)),
        (0.08, BLACK, (0.08, 0.0624, 0.0400)),
        (0.03, BLACK, (0.03, 0.0234, 0.0150)),
        # At or above the glow level: the colour as it is, no glow (#43).
        (0.40, RED, (0.40, 0.0, 0.0)),
        (0.20, BLUE, (0.0, 0.0, 0.20)),
        (1.00, RED, (1.0, 0.0, 0.0)),
        (0.03, RED, (0.03, 0.0, 0.0)),
        (0.50, (0.6, 0.4, 0.2), (0.30, 0.20, 0.10)),
        (0.50, (0.2, 0.0, 0.0), (0.10, 0.0, 0.0)),  # exactly the glow level
        # Below the glow level: the colour plus part of the glow.
        (0.50, (0.1, 0.0, 0.0), (0.10, 0.039, 0.025)),  # half way to the level
        (1.00, (0.0, 0.025, 0.0), (0.075, 0.0835, 0.0375)),  # a quarter
    ],
)
def test_brightness_and_minimal_glow(
    brightness: float,
    engine: tuple[float, float, float],
    expected: tuple[float, float, float],
) -> None:
    lights = LightState()
    lights.set_target(target(engine), False, 0.0)
    assert rgb_at(lights, 0.0, brightness) == approx_rgb(expected)


def test_brightness_is_applied_when_the_colours_are_asked_for() -> None:
    lights = LightState()
    lights.set_target(target(RED), False, 0.0)
    assert rgb_at(lights, 0.0, 1.0) == approx_rgb(RED_SENT)
    assert rgb_at(lights, 0.0, 0.40) == approx_rgb((0.40, 0.0, 0.0))


@pytest.mark.parametrize("brightness", [1.0, 0.80, 0.20, 0.03])
def test_a_fade_from_black_to_a_bright_colour_has_no_jumps(brightness: float) -> None:
    """The glow fades out as the channel gets brighter; it never switches off."""
    lights = LightState()
    lights.set_target(target(BLACK), True, 0.0)
    lights.set_target(target(BLACK), True, 1.0)  # measured interval: 1 s
    lights.set_target(target(RED), False, 2.0)
    steps = 1000
    sent = [rgb_at(lights, 2.0 + i / steps, brightness) for i in range(steps + 1)]
    assert sent[0] == approx_rgb(tuple(min(0.10, brightness) * c for c in WARM))
    assert sent[-1] == approx_rgb((brightness, 0.0, 0.0))
    # The engine colour moves brightness / steps per step; the glow may add
    # at most as much again. A jump would be at least 10 times that.
    largest = max(
        abs(b - a)
        for before, after in itertools.pairwise(sent)
        for a, b in zip(before, after, strict=True)
    )
    assert largest <= 2 * brightness / steps + 1e-9


LEVELS = [0.0, 0.01, 0.05, 0.1, 0.2, 0.5, 1.0]


@pytest.mark.parametrize("brightness", [0.01, 0.03, 0.08, 0.10, 0.15, 0.40, 1.0])
def test_never_brighter_than_the_brightness_setting(brightness: float) -> None:
    for engine in itertools.product(LEVELS, repeat=3):
        lights = LightState()
        lights.set_target(target(engine), False, 0.0)
        assert max(rgb_at(lights, 0.0, brightness)) <= brightness + 1e-9, engine


# The hold on sudden black (#39)


def test_sudden_black_keeps_the_last_colours_for_the_hold() -> None:
    lights = picture_then_black(black_at=3.0)
    assert rgb_at(lights, 3.0) == approx_rgb(RED_SENT)
    lights.set_target(target(BLACK), True, 3.0 + BLACK_HOLD - 0.1)
    assert rgb_at(lights, 3.0 + BLACK_HOLD) == approx_rgb(RED_SENT)


def test_black_longer_than_the_hold_fades_to_the_glow() -> None:
    lights = picture_then_black(black_at=3.0)
    lights.set_target(target(BLACK), True, 6.0)
    lights.set_target(target(BLACK), True, 3.0 + BLACK_HOLD)  # the hold is over
    # It fades over the last capture interval (0.5 s).
    assert rgb_at(lights, 3.0 + BLACK_HOLD) == approx_rgb(RED_SENT)
    assert rgb_at(lights, 3.0 + BLACK_HOLD + 0.5) == approx_rgb(GLOW_ONLY)


def test_a_picture_during_the_hold_ends_it() -> None:
    lights = picture_then_black(black_at=3.0)
    lights.set_target(target(BLUE), False, 4.0)
    assert rgb_at(lights, 5.0) == approx_rgb(BLUE)
    # The next black starts a new hold.
    lights.set_target(target(BLACK), True, 5.0)
    lights.set_target(target(BLACK), True, 5.0 + BLACK_HOLD - 0.1)
    assert rgb_at(lights, 9.0) == approx_rgb(BLUE)


def test_starting_on_black_shows_the_glow_at_once() -> None:
    lights = LightState()
    lights.set_target(target(BLACK), True, 0.0)
    assert rgb_at(lights, 0.0) == approx_rgb(GLOW_ONLY)


def test_a_black_region_in_a_picture_is_not_held() -> None:
    lights = LightState()
    lights.set_target(target(RED), False, 0.0)
    # The channel's region is black, but the frame as a whole is not.
    lights.set_target(target(BLACK), False, 1.0)
    assert rgb_at(lights, 1.0) == approx_rgb(RED_SENT)
    assert rgb_at(lights, 2.0) == approx_rgb(GLOW_ONLY)


# After a retry


def test_resume_keeps_only_the_channels_that_still_exist() -> None:
    lights = LightState()
    lights.set_target(target(RED, "1", "2"), False, 0.0)
    lights.colors(0.0, 1.0)
    lights.resume({"1"})
    colors = lights.colors(1.0, 1.0)
    assert [color.channel_id for color in colors] == ["1"]
    assert colors[0].rgb == approx_rgb(RED_SENT)


def test_resume_does_not_stretch_the_next_fade_over_the_gap() -> None:
    lights = LightState()
    lights.set_target(target(RED), False, 0.0)
    lights.set_target(target(RED), False, 1.0)  # measured interval: 1 s
    lights.resume({"1"})
    lights.set_target(target(BLUE), False, 7.0)  # 6 s later, after the retry
    assert rgb_at(lights, 7.5) == approx_rgb((0.50, 0.0, 0.50))
    assert rgb_at(lights, 8.0) == approx_rgb(BLUE)
