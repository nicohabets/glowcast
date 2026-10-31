"""How the v0.4 colour changes work together (#263, architect review 2).

Each change has tests of its own: the stretch of TV strips (#23,
test_strip_edges.py), the far blend (#15, test_far_lights.py), the weighted
region colour (#233, test_lively_colours.py), letterbox bars (#34,
test_letterbox.py), cuts (#258) and the fade-in (#153, test_light_state.py).
These tests combine them, as one frame or one run does.

The far-blend tests assert properties (a far light lies between its region's
colour and the whole picture's, a black region stays dark), not the numbers of
one formula, so they hold while the blend itself is refined (#262).
"""

from collections.abc import Callable, Sequence

import pytest

from custom_components.glowcast.core.color_engine import (
    BLACK_LEVEL,
    WEIGHT_FLOOR,
    Picture,
    channel_region,
    channel_regions,
    compute,
    is_black,
)
from custom_components.glowcast.core.letterbox import STABLE, Bars, Letterbox, measure
from custom_components.glowcast.core.light_state import (
    BLACK_HOLD,
    CUT_FADE,
    FADE_IN,
    GLOW,
    GLOW_COLOR,
    LightState,
)
from custom_components.glowcast.core.models import Channel, ChannelColor, Frame
from custom_components.glowcast.core.pipeline import _compute

WIDTH, HEIGHT = 64, 36
BAR = 5  # rows of black at the top and the bottom: a letterboxed film
RED = (255, 0, 0)
BLUE = (0, 0, 255)
GREEN = (0, 255, 0)
YELLOW = (255, 255, 0)
GREY = (128, 128, 128)
BLACK = (0, 0, 0)

Rgb = tuple[int, int, int]
Colour = tuple[float, float, float]


def make_frame(
    paint: Callable[[float, float], Rgb], top: int = 0, bottom: int = 0
) -> Frame:
    """A frame painted over the picture (u, v 0..1 inside it), with `top` and
    `bottom` rows of black bars."""
    rows = HEIGHT - top - bottom
    pixels = bytearray()
    for row in range(HEIGHT):
        for col in range(WIDTH):
            if row < top or row >= HEIGHT - bottom:
                pixels.extend(BLACK)
            else:
                pixels.extend(paint((col + 0.5) / WIDTH, (row - top + 0.5) / rows))
    return Frame(WIDTH, HEIGHT, bytes(pixels))


EDGE = 0.12


def edges(u: float, v: float) -> Rgb:
    """Each edge of the picture its own colour in the outer 12 %, grey inside:
    every pixel lit, so the whole picture has no black in it."""
    if u < EDGE:
        return RED
    if u > 1 - EDGE:
        return BLUE
    if v < EDGE:
        return GREEN
    if v > 1 - EDGE:
        return YELLOW
    return GREY


def strip(y: float) -> list[Channel]:
    """The TV gradient strip of the test area (architecture 2): four segments
    left and right of the screen, between -0.4 and 0.4."""
    return [
        Channel("s0", "s0", -0.4, -0.4, y=y, light="strip"),
        Channel("s1", "s1", -0.4, 0.0, y=y, light="strip"),
        Channel("s2", "s2", 0.4, 0.4, y=y, light="strip"),
        Channel("s3", "s3", 0.4, 0.0, y=y, light="strip"),
    ]


def without_depth(
    channels: Sequence[Channel], keep_light: bool = True
) -> list[Channel]:
    """The same channels without a depth: their region's colour only."""
    return [
        Channel(c.id, c.name, c.x, c.z, light=c.light if keep_light else None)
        for c in channels
    ]


def whole_picture(frame: Frame, picture: Picture) -> Colour:
    """The weighted colour (#233) of every lit pixel inside `picture`."""
    col0, col1, row0, row1 = picture
    total = [0.0, 0.0, 0.0]
    weight = 0.0
    for row in range(row0, row1):
        for col in range(col0, col1):
            i = (row * frame.width + col) * 3
            r, g, b = frame.pixels[i : i + 3]
            high = max(r, g, b)
            if high > BLACK_LEVEL:
                w = (WEIGHT_FLOOR + ((high - min(r, g, b)) / high) ** 2) * high
                total = [t + w * c for t, c in zip(total, (r, g, b), strict=True)]
                weight += w
    return (total[0] / weight / 255, total[1] / weight / 255, total[2] / weight / 255)


def assert_between(colour: Colour, a: Colour, b: Colour) -> None:
    for c, x, y in zip(colour, a, b, strict=True):
        assert min(x, y) - 1e-9 <= c <= max(x, y) + 1e-9, (colour, a, b)


def difference(a: Colour, b: Colour) -> float:
    return max(abs(x - y) for x, y in zip(a, b, strict=True))


def by_id(colors: list[ChannelColor]) -> dict[str, Colour]:
    return {c.channel_id: c.rgb for c in colors}


# The colour engine: stretch, far blend, weighted average and bars in one call


def test_a_realistic_area_in_one_compute_call() -> None:
    """A stretched TV strip at y 0.8, lamps at the back (y -0.9) and a film
    with letterbox bars: the strip reaches the picture's edges inside the bars
    and follows its own part directly, the lamps keep their regions and blend
    with the whole picture inside the bars."""
    frame = make_frame(edges, top=BAR, bottom=BAR)
    picture = Bars(*measure(frame)).picture(WIDTH, HEIGHT)
    assert picture == (0, WIDTH, BAR, HEIGHT - BAR)
    lamps = [
        Channel("lamp-l", "lamp-l", -0.8, 0.0, y=-0.9, light="lamp-l"),
        Channel("lamp-r", "lamp-r", 0.8, 0.0, y=-0.9, light="lamp-r"),
    ]
    area = [*strip(0.8), *lamps]

    regions = channel_regions(area, WIDTH, HEIGHT, picture)
    # The strip is stretched onto the picture inside the bars.
    assert min(r[0] for r in regions[:4]) == 0
    assert max(r[1] for r in regions[:4]) == WIDTH
    assert min(r[2] for r in regions[:4]) == BAR
    assert max(r[3] for r in regions[:4]) == HEIGHT - BAR
    # The lamps keep the linear mapping, inside the bars too.
    assert regions[4:] == channel_regions(
        without_depth(lamps, keep_light=False), WIDTH, HEIGHT, picture
    )

    colours = by_id(compute(frame, area, 1.0, picture))
    # The strip: its stretched regions, no blend (and the lamps change nothing).
    assert {i: colours[i] for i in ("s0", "s1", "s2", "s3")} == by_id(
        compute(frame, strip(0.8), 1.0, picture)
    )
    r, g, b = colours["s1"]  # reaches the red left edge
    assert r > g + 0.3 and r > b + 0.3
    r, g, b = colours["s3"]  # reaches the blue right edge
    assert b > r + 0.3 and b > g + 0.3
    # The lamps: between their region's colour and the whole picture's.
    whole = whole_picture(frame, picture)
    region = by_id(compute(frame, without_depth(lamps), 1.0, picture))
    for lamp in ("lamp-l", "lamp-r"):
        assert_between(colours[lamp], region[lamp], whole)
        assert difference(colours[lamp], region[lamp]) > 0.05, lamp


def test_a_gradient_light_at_the_back_is_blended_but_not_stretched() -> None:
    """A gradient strip at the back of the room keeps the linear mapping (not
    around the TV), and each of its channels blends with the whole picture."""
    frame = make_frame(edges)
    back = strip(-0.9)
    plain = without_depth(back, keep_light=False)
    assert channel_regions(back, WIDTH, HEIGHT) == channel_regions(plain, WIDTH, HEIGHT)

    colours = by_id(compute(frame, back, 1.0))
    region = by_id(compute(frame, plain, 1.0))
    whole = whole_picture(frame, (0, WIDTH, 0, HEIGHT))
    for channel in back:
        assert_between(colours[channel.id], region[channel.id], whole)
        assert difference(colours[channel.id], region[channel.id]) > 0.01, channel.id


def test_a_region_at_exactly_the_lit_share_gets_the_weighted_colour() -> None:
    """MIN_LIT_SHARE (5 %) lit pixels is enough for a colour, weighted (#233):
    one red and four grey of 100 pixels lean red, more than their plain mean.
    One lit pixel fewer is black."""
    size = 33  # a 10 x 10 region (REGION_SIZE), so 5 % is 5 pixels exactly
    middle = Channel("m", "m", 0.0, 0.0)
    col0, col1, row0, row1 = channel_region(middle, size, size)
    assert (col1 - col0, row1 - row0) == (10, 10)
    lit = [(col0 + i, row0) for i in range(5)]

    def frame(pixels: list[tuple[int, int]]) -> Frame:
        data = bytearray(bytes(BLACK) * size * size)
        for n, (col, row) in enumerate(pixels):
            i = (row * size + col) * 3
            data[i : i + 3] = bytes(RED if n == 0 else GREY)
        return Frame(size, size, bytes(data))

    [result] = compute(frame(lit), [middle], 1.0)
    w_red = (WEIGHT_FLOOR + 1) * 255
    w_grey = WEIGHT_FLOOR * 128 * 4
    scale = 255 * (w_red + w_grey)
    weighted = ((w_red * 255 + w_grey * 128) / scale, w_grey * 128 / scale)
    assert result.rgb == pytest.approx((weighted[0], weighted[1], weighted[1]))
    plain_red = (255 + 4 * 128) / 5 / 255
    assert result.rgb[0] - result.rgb[1] > plain_red - 128 / 255 + 0.1

    [fewer] = compute(frame(lit[:4]), [middle], 1.0)
    assert fewer.rgb == (0.0, 0.0, 0.0)


# The far blend after the hold on black: the real path, frame by frame

GLOW_ONLY = tuple(GLOW * c for c in GLOW_COLOR)  # black at brightness 100 %
# Lamps at the back of the room, left and right.
FAR_LEFT = Channel("far-l", "far-l", -0.8, 0.0, y=-0.9, light="far-l")
FAR_RIGHT = Channel("far-r", "far-r", 0.8, 0.0, y=-0.9, light="far-r")
NEAR_LEFT = Channel("near-l", "near-l", -0.8, 0.0, y=1.0, light="near-l")
NEAR_RIGHT = Channel("near-r", "near-r", 0.8, 0.0, y=1.0, light="near-r")
LAMPS = [NEAR_LEFT, NEAR_RIGHT, FAR_LEFT, FAR_RIGHT]


def element(top: int = 0, bottom: int = 0) -> Frame:
    """Black with a small white element on the left (10 x 10 pixels): fewer
    than 5 % of the frame, so a black frame, like the switch-off animation of
    a TV, but more than 5 % of the left lamps' regions."""
    rows = HEIGHT - top - bottom
    return make_frame(
        lambda u, v: (
            (220, 220, 220)
            if u * WIDTH < 10 and abs(v * rows - rows / 2) < 5
            else BLACK
        ),
        top,
        bottom,
    )


def run(frames: list[tuple[float, Frame]], until: float) -> tuple[Letterbox, dict]:
    """The pipeline's colour work (`_compute`: is_black, the letterbox and the
    colour engine) and the light state, frame by frame; the colours sent at
    `until`, by channel."""
    letterbox = Letterbox()
    lights = LightState()
    for now, frame in frames:
        colors, black, _ = _compute(frame, LAMPS, 1.0, letterbox, now)
        lights.set_target(colors, black, now)
        lights.colors(now, 1.0)
    return letterbox, by_id(lights.colors(until, 1.0))


def test_far_lights_on_a_black_frame_go_to_the_glow_after_the_hold() -> None:
    """After the hold on black, a far lamp whose region is black goes to the
    glow like the near lamps; one whose region has the element is no brighter
    than the near lamp beside it."""
    assert is_black(element())
    picture = make_frame(lambda u, v: RED if u < 0.5 else BLUE)
    frames = [(float(t), picture) for t in range(4)]
    frames += [(float(t), element()) for t in range(4, 4 + int(BLACK_HOLD) + 3)]
    end = frames[-1][0]

    _, sent = run(frames[: 4 + 2], 4 + 2)
    # During the hold: the picture's last colours, near and far alike.
    assert sent["near-r"] == pytest.approx((0.0, 0.0, 1.0))
    assert sent["far-r"][2] > 0.3

    _, sent = run(frames, end + 1)
    assert sent["near-r"] == pytest.approx(GLOW_ONLY, abs=0.005)
    assert max(sent["far-r"]) <= GLOW + 1e-9
    assert all(
        f <= n + 1e-9 for f, n in zip(sent["far-l"], sent["near-l"], strict=True)
    )


def test_far_lights_on_a_black_frame_in_a_letterboxed_film_go_to_the_glow() -> None:
    """The same with letterbox bars learned from the film before it went black."""
    film = make_frame(lambda u, v: RED if u < 0.5 else BLUE, BAR, BAR)
    frames = [(t / 2, film) for t in range(8)]  # 3.5 s, longer than STABLE
    assert frames[-1][0] > STABLE
    dark = element(BAR, BAR)
    assert is_black(dark)
    frames += [(float(t), dark) for t in range(4, 4 + int(BLACK_HOLD) + 3)]
    end = frames[-1][0]

    letterbox, sent = run(frames, end + 1)
    assert letterbox.bars == Bars(top=BAR, bottom=BAR)
    assert sent["near-r"] == pytest.approx(GLOW_ONLY, abs=0.005)
    assert max(sent["far-r"]) <= GLOW + 1e-9


# The light state: cuts with the fade-in and after a retry

WARM_WHITE = (0.8, 0.62, 0.40)  # what a light showed before the claim
RED_F = (1.0, 0.0, 0.0)
BLUE_F = (0.0, 0.0, 1.0)


def rgb_at(lights: LightState, now: float) -> Colour:
    [color] = lights.colors(now, 1.0)
    return color.rgb


def test_a_cut_during_the_fade_in_shows_at_once_inside_the_fade_in() -> None:
    """A cut in the first 2 s after a claim: the picture's own fade takes
    CUT_FADE, and the fade-in from the light's own state goes on as it was,
    neither restarted nor skipped."""
    lights = LightState()
    lights.fade_in({"1": WARM_WHITE})
    lights.set_target([ChannelColor("1", RED_F)], False, 0.0)
    rgb_at(lights, 0.5)
    lights.set_target([ChannelColor("1", RED_F)], False, 1.0)  # interval: 1 s
    rgb_at(lights, 1.1)
    lights.set_target([ChannelColor("1", BLUE_F)], False, 1.2)  # a cut
    now = 1.2 + CUT_FADE
    share = now / FADE_IN  # the fade-in started at the first target, at 0
    want = tuple(o + (b - o) * share for o, b in zip(WARM_WHITE, BLUE_F, strict=True))
    assert rgb_at(lights, now) == pytest.approx(want, abs=0.005)
    assert rgb_at(lights, FADE_IN) == pytest.approx(BLUE_F, abs=0.005)


def test_a_cut_after_a_retry_fades_over_cut_fade_from_where_the_lights_were() -> None:
    """After resume(), a scene change in the next frame is still a cut: it
    fades over CUT_FADE from the last colours, not over the old interval."""
    lights = LightState()
    lights.set_target([ChannelColor("1", RED_F)], False, 0.0)
    lights.set_target([ChannelColor("1", RED_F)], False, 1.0)  # interval: 1 s
    rgb_at(lights, 1.5)
    lights.resume({"1"})
    assert rgb_at(lights, 4.0) == pytest.approx(RED_F, abs=0.005)  # sent meanwhile
    lights.set_target([ChannelColor("1", BLUE_F)], False, 7.0)
    assert rgb_at(lights, 7.0) == pytest.approx(RED_F, abs=0.005)
    assert rgb_at(lights, 7.0 + CUT_FADE / 2) == pytest.approx(
        (0.5, 0.0, 0.5), abs=0.005
    )
    assert rgb_at(lights, 7.0 + CUT_FADE) == pytest.approx(BLUE_F, abs=0.005)
