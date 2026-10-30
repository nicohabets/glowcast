"""Tests for TV-strip segments following the edges of the picture (#23)."""

from collections.abc import Callable

import pytest

from custom_components.glowcast.core.color_engine import (
    NEAR_TV,
    channel_regions,
    compute,
)
from custom_components.glowcast.core.models import Channel, Frame

WIDTH, HEIGHT = 64, 36
RED = (255, 0, 0)
BLUE = (0, 0, 255)
GREEN = (0, 255, 0)
YELLOW = (255, 255, 0)
GREY = (128, 128, 128)

Rgb = tuple[int, int, int]


def make_frame(paint: Callable[[float, float], Rgb]) -> Frame:
    pixels = bytearray()
    for row in range(HEIGHT):
        for col in range(WIDTH):
            pixels.extend(paint((col + 0.5) / WIDTH, (row + 0.5) / HEIGHT))
    return Frame(WIDTH, HEIGHT, bytes(pixels))


EDGE = 0.12
# Each edge of the picture its own colour in the outer 12 %, grey inside.
EDGES = make_frame(
    lambda u, v: (
        RED
        if u < EDGE
        else BLUE
        if u > 1 - EDGE
        else GREEN
        if v < EDGE
        else YELLOW
        if v > 1 - EDGE
        else GREY
    )
)


def strip(y: float = 0.8, light: str | None = "strip") -> list[Channel]:
    """The TV gradient strip of the test area (architecture 2): four segments
    left and right of the screen, between -0.4 and 0.4."""
    return [
        Channel("0", "0", -0.4, -0.4, y=y, light=light),
        Channel("1", "1", -0.4, 0.0, y=y, light=light),
        Channel("2", "2", 0.4, 0.4, y=y, light=light),
        Channel("3", "3", 0.4, 0.0, y=y, light=light),
    ]


def colours(channels: list[Channel]) -> dict[str, tuple[float, float, float]]:
    return {c.channel_id: c.rgb for c in compute(EDGES, channels, 1.0)}


def test_the_strip_s_outer_segments_show_the_edge_colours() -> None:
    got = colours(strip())
    # The middle segments of each side reach the left and right edges: their
    # edge's colour leads (a region is 30 % wide, the edge band 12 %).
    r, g, b = got["1"]
    assert r > g + 0.3 and r > b + 0.3
    r, g, b = got["3"]
    assert b > r + 0.3 and b > g + 0.3
    # The corner segments see their side's edge (and the corner's).
    assert got["0"][0] > got["0"][2] and got["2"][2] > got["2"][0]


def test_regions_of_the_strip_reach_the_picture_edges() -> None:
    regions = channel_regions(strip(), WIDTH, HEIGHT)
    assert min(r[0] for r in regions) == 0
    assert max(r[1] for r in regions) == WIDTH
    assert min(r[2] for r in regions) == 0
    assert max(r[3] for r in regions) == HEIGHT


def test_without_stretching_the_strip_samples_the_inner_area() -> None:
    """A strip whose depth is unknown keeps the linear mapping (D12)."""
    for channels in (
        strip(light=None),
        [Channel(c.id, c.name, c.x, c.z, light="strip") for c in strip()],
    ):
        for rgb in colours(channels).values():
            assert rgb == pytest.approx((128 / 255,) * 3, abs=0.02)


def test_a_gradient_light_far_from_the_tv_is_not_stretched() -> None:
    far = strip(y=NEAR_TV - 0.6)
    assert channel_regions(far, WIDTH, HEIGHT) == channel_regions(
        [Channel(c.id, c.name, c.x, c.z) for c in far], WIDTH, HEIGHT
    )


def test_other_lights_behave_as_before() -> None:
    """A lamp of its own next to the TV, and a lamp far away, keep their regions."""
    lamp = Channel("lamp", "lamp", 0.6, -0.5, y=0.9, light="lamp")
    corner = Channel("corner", "corner", -0.9, 0.3, y=-0.95, light="corner")
    plain = [Channel(c.id, c.name, c.x, c.z) for c in (lamp, corner)]
    mixed = [*strip(), lamp, corner]
    assert channel_regions(mixed, WIDTH, HEIGHT)[4:] == channel_regions(
        plain, WIDTH, HEIGHT
    )


def test_a_strip_along_the_top_reaches_the_top_and_both_sides() -> None:
    top = [
        Channel(str(i), str(i), x, 0.3, y=0.8, light="bar")
        for i, x in enumerate((-0.3, -0.1, 0.1, 0.3))
    ]
    regions = channel_regions(top, WIDTH, HEIGHT)
    assert regions[0][0] == 0 and regions[-1][1] == WIDTH
    assert all(r[2] == 0 for r in regions)


def test_an_axis_without_spread_is_left_alone() -> None:
    """Segments all at the middle height (z = 0) stay at the middle."""
    middle = [
        Channel(str(i), str(i), x, 0.0, y=0.8, light="bar")
        for i, x in enumerate((-0.3, 0.3))
    ]
    plain = channel_regions([Channel("p", "p", 0.0, 0.0)], WIDTH, HEIGHT)[0]
    assert all(r[2:] == plain[2:] for r in channel_regions(middle, WIDTH, HEIGHT))


def test_the_stretch_maps_onto_the_picture_inside_letterbox_bars() -> None:
    picture = (0, WIDTH, 5, HEIGHT - 5)
    regions = channel_regions(strip(), WIDTH, HEIGHT, picture)
    assert min(r[2] for r in regions) == 5
    assert max(r[3] for r in regions) == HEIGHT - 5


def test_a_light_beyond_the_edge_is_not_pulled_in() -> None:
    """Positions outside -1..1 stay where they are (clamped as before)."""
    wide = [
        Channel(str(i), str(i), x, 0.0, y=0.8, light="wide")
        for i, x in enumerate((-1.2, 1.2))
    ]
    plain = [Channel(c.id, c.name, c.x, c.z) for c in wide]
    assert channel_regions(wide, WIDTH, HEIGHT) == channel_regions(plain, WIDTH, HEIGHT)


def plain(channels: list[Channel]) -> list[Channel]:
    """The same positions without depth or light: the linear mapping."""
    return [Channel(c.id, c.name, c.x, c.z) for c in channels]


def test_a_strip_at_exactly_near_tv_is_stretched() -> None:
    regions = channel_regions(strip(y=NEAR_TV), WIDTH, HEIGHT)
    assert min(r[0] for r in regions) == 0
    assert max(r[1] for r in regions) == WIDTH


def test_a_strip_from_the_tv_to_the_back_is_not_stretched() -> None:
    """Decided for the whole light: with some channels behind NEAR_TV, none
    of them is stretched, so the strip does not jump halfway along."""
    channels = [
        Channel(str(i), str(i), x, 0.0, y=y, light="long")
        for i, (x, y) in enumerate(((-0.4, 0.8), (-0.2, 0.6), (0.2, 0.2), (0.4, -0.4)))
    ]
    assert channel_regions(channels, WIDTH, HEIGHT) == channel_regions(
        plain(channels), WIDTH, HEIGHT
    )


def test_a_lamp_beside_the_tv_and_a_tube_on_one_side_are_not_stretched() -> None:
    """Gradient lights near the TV whose channels are all on one side of the
    centre are not around the screen (#23: they behave as before)."""
    lamp = [
        Channel("l0", "l0", 0.6, 0.6, y=0.9, light="lamp"),
        Channel("l1", "l1", 0.6, -0.6, y=0.9, light="lamp"),
    ]
    tube = [
        Channel(f"t{i}", f"t{i}", -0.5, z, y=0.8, light="tube")
        for i, z in enumerate((-0.3, 0.0, 0.3))
    ]
    channels = [*lamp, *tube]
    assert channel_regions(channels, WIDTH, HEIGHT) == channel_regions(
        plain(channels), WIDTH, HEIGHT
    )


def test_an_off_centre_strip_is_stretched_from_the_centre() -> None:
    """Scaled from the centre, not re-centred: the side further out reaches
    its edge, the other one comes as far out in proportion."""
    channels = [
        Channel("0", "0", -0.3, 0.0, y=0.8, light="strip"),
        Channel("1", "1", 0.5, 0.0, y=0.8, light="strip"),
    ]
    expected = [Channel("0", "0", -0.6, 0.0), Channel("1", "1", 1.0, 0.0)]
    regions = channel_regions(channels, WIDTH, HEIGHT)
    assert regions == channel_regions(expected, WIDTH, HEIGHT)
    assert regions[1][1] == WIDTH and regions[0][0] > 0
