"""Lights far from the TV follow more softly (#15): blended with the whole picture."""

from collections.abc import Callable

import pytest

from custom_components.glowcast.core.color_engine import (
    FAR_BLEND,
    NEAR_TV,
    WEIGHT_FLOOR,
    compute,
)
from custom_components.glowcast.core.models import Channel, Frame

WIDTH, HEIGHT = 64, 36
RED = (255, 0, 0)
BLUE = (0, 0, 255)
BLACK = (0, 0, 0)

Rgb = tuple[int, int, int]
Colour = tuple[float, float, float]


def make_frame(paint: Callable[[int, int], Rgb]) -> Frame:
    pixels = bytearray()
    for row in range(HEIGHT):
        for col in range(WIDTH):
            pixels.extend(paint(col, row))
    return Frame(WIDTH, HEIGHT, bytes(pixels))


# A light at x = -1 samples the left 19 columns (REGION_SIZE of 64): red there,
# blue elsewhere. Red and blue pixels weigh the same, so the whole picture's
# colour is their share of the columns.
LEFT_COLUMNS = 19
FRAME = make_frame(lambda col, row: RED if col < LEFT_COLUMNS else BLUE)
WHOLE = (LEFT_COLUMNS / WIDTH, 0.0, (WIDTH - LEFT_COLUMNS) / WIDTH)


def left(y: float | None) -> Channel:
    return Channel("l", "l", -1.0, 0.0, y=y)


def colour(channel: Channel, frame: Frame = FRAME, **kwargs: object) -> Colour:
    [result] = compute(frame, [channel], 1.0, **kwargs)
    return result.rgb


def mix(a: Colour, b: Colour, share: float) -> Colour:
    return (
        a[0] + (b[0] - a[0]) * share,
        a[1] + (b[1] - a[1]) * share,
        a[2] + (b[2] - a[2]) * share,
    )


def test_a_light_near_the_tv_follows_its_part_directly() -> None:
    assert colour(left(1.0)) == (1.0, 0.0, 0.0)
    assert colour(left(NEAR_TV)) == (1.0, 0.0, 0.0)


def test_a_light_at_the_back_blends_with_the_whole_picture() -> None:
    far = colour(left(-1.0))
    assert FAR_BLEND == 0.5
    assert far == pytest.approx(mix((1.0, 0.0, 0.0), WHOLE, FAR_BLEND))
    # Further back than the back wall is the same.
    assert colour(left(-1.5)) == far


def test_the_blend_grows_linearly_with_the_distance_from_the_tv() -> None:
    """A sixth (about 17 %) in the middle of the room, y = 0."""
    for y, share in ((0.5, 0.0), (0.0, 1 / 6), (-0.5, 1 / 3), (-1.0, 0.5)):
        assert colour(left(y)) == pytest.approx(mix((1.0, 0.0, 0.0), WHOLE, share)), y


def test_lights_without_a_depth_behave_as_before() -> None:
    """Home Assistant lights have no depth: no blend."""
    assert colour(left(None)) == (1.0, 0.0, 0.0)
    assert colour(Channel("l", "l", -1.0, 0.0)) == (1.0, 0.0, 0.0)


def test_a_far_light_does_not_change_the_others() -> None:
    near, far, plain = left(1.0), Channel("f", "f", -1.0, 0.0, y=-1.0), left(None)
    colours = compute(FRAME, [near, far, plain], 1.0)
    assert [c.rgb for c in colours] == [
        (1.0, 0.0, 0.0),
        colour(far),
        (1.0, 0.0, 0.0),
    ]


def test_the_whole_picture_is_weighted_as_the_regions() -> None:
    """Red on the left, grey elsewhere: the grey counts with WEIGHT_FLOOR x
    value, the red with (WEIGHT_FLOOR + 1) x value (#233), not one each."""
    grey = (128, 128, 128)
    frame = make_frame(lambda col, row: RED if col < LEFT_COLUMNS else grey)
    w_red = (WEIGHT_FLOOR + 1) * 255 * LEFT_COLUMNS
    w_grey = WEIGHT_FLOOR * 128 * (WIDTH - LEFT_COLUMNS)
    total = 255 * (w_red + w_grey)
    whole = (
        (w_red * 255 + w_grey * 128) / total,
        w_grey * 128 / total,
        w_grey * 128 / total,
    )
    assert colour(left(-1.0), frame) == pytest.approx(
        mix((1.0, 0.0, 0.0), whole, FAR_BLEND)
    )


def test_a_far_light_on_a_black_region_takes_part_of_the_picture() -> None:
    frame = make_frame(lambda col, row: BLACK if col < LEFT_COLUMNS else BLUE)
    assert colour(left(1.0), frame) == (0.0, 0.0, 0.0)
    whole = (0.0, 0.0, 1.0)  # the black pixels are skipped
    assert colour(left(-1.0), frame) == pytest.approx(
        mix((0.0, 0.0, 0.0), whole, FAR_BLEND)
    )


def test_a_black_picture_stays_black() -> None:
    frame = make_frame(lambda col, row: BLACK)
    assert colour(left(-1.0), frame) == (0.0, 0.0, 0.0)


def test_the_whole_picture_is_the_picture_inside_the_bars() -> None:
    """Only the part inside `picture` counts: the red outside it does not."""
    frame = make_frame(lambda col, row: BLUE if 5 <= row < HEIGHT - 5 else RED)
    picture = (0, WIDTH, 5, HEIGHT - 5)
    assert colour(left(-1.0), frame, picture=picture) == pytest.approx((0.0, 0.0, 1.0))
    # Without it, the red rows would count in the whole picture.
    assert colour(left(-1.0), frame)[0] > 0.1


def test_intensity_applies_to_the_blended_colour() -> None:
    """Intensity 0 turns the blended colour grey at its own brightness."""
    blended = colour(left(-1.0))
    [grey] = compute(FRAME, [left(-1.0)], 0.0)
    assert grey.rgb == pytest.approx((max(blended),) * 3)
