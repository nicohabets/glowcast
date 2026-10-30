"""Colour engine: turns a frame into one colour per channel (architecture 3.3).

Pure and synchronous, no Home Assistant imports.
"""

import colorsys
import dataclasses

from .models import Channel, ChannelColor, Frame

# Share of the frame's width and height each channel samples.
REGION_SIZE = 0.3
# A pixel whose brightest component is at or below this (0-255) counts as
# black: letterbox bars, including video black (16) and JPEG noise.
BLACK_LEVEL = 32
# A region with fewer lit pixels than this share is black.
MIN_LIT_SHARE = 0.05
# A lit pixel counts in its region's colour with (WEIGHT_FLOOR + saturation²)
# x value (#233): the floor gives grey and white a say, so a small coloured
# object only tints a white scene (spike #40: 0.5 kept the gain on mixed
# scenes and left snow with a red jacket nearly white).
WEIGHT_FLOOR = 0.5
# A light with several channels at or above this depth (y) is around the TV,
# when they are all there and on both sides of the centre (x), like a gradient
# lightstrip behind it: its channels are stretched so its outermost ones reach
# the edges of the picture (#23).
NEAR_TV = 0.5
# Along an axis where a light's channels all sit closer to the centre than
# this (all at the middle height, say), they are left where they are.
MIN_SPREAD = 0.1

BLACK = (0.0, 0.0, 0.0)

# A part of the frame: col0, col1, row0, row1.
Picture = tuple[int, int, int, int]


def is_black(frame: Frame) -> bool:
    """Whether the whole frame is (almost) black: fewer than MIN_LIT_SHARE lit pixels.

    A small bright element, such as a TV's switch-off animation, does not
    count as a picture here, even if it makes one channel's region lit.
    """
    pixels = frame.pixels
    lit = 0
    for i in range(0, len(pixels), 3):
        if (
            pixels[i] > BLACK_LEVEL
            or pixels[i + 1] > BLACK_LEVEL
            or pixels[i + 2] > BLACK_LEVEL
        ):
            lit += 1
    return lit < MIN_LIT_SHARE * frame.width * frame.height


def is_all_zero(frame: Frame) -> bool:
    """Whether every pixel is 0: no picture at all.

    A protected app in the TV's own apps delivers exactly this; a real picture
    only for a moment, at a hard cut or an input switch (see "Verified facts").
    """
    return not any(frame.pixels)


def channel_region(
    channel: Channel, width: int, height: int, picture: Picture | None = None
) -> tuple[int, int, int, int]:
    """Return the part of the frame a channel samples: col0, col1, row0, row1.

    x and z map linearly from -1..1 to the edges of the picture (z = +1 is
    its top row): the whole frame, or `picture` (col0, col1, row0, row1), the
    part inside letterbox bars (#34). The region is centred there and moved
    inwards where it would cross an edge, so it always covers REGION_SIZE of
    the picture.
    """
    col0, col1, row0, row1 = picture or (0, width, 0, height)
    x = min(max(channel.x, -1.0), 1.0)
    z = min(max(channel.z, -1.0), 1.0)
    return (*_band((x + 1) / 2, col0, col1), *_band((1 - z) / 2, row0, row1))


def channel_regions(
    channels: list[Channel], width: int, height: int, picture: Picture | None = None
) -> list[tuple[int, int, int, int]]:
    """The region of every channel, in the order of `channels`.

    One step over all channels, because a light around the TV is mapped by
    the spread of its own channels (`_stretched`, #23).
    """
    return [
        channel_region(channel, width, height, picture)
        for channel in _stretched(channels)
    ]


def _stretched(channels: list[Channel]) -> list[Channel]:
    """The channels, with those of each light around the TV stretched.

    A gradient lightstrip behind the TV has its channels close to the centre
    (between -0.4 and 0.4 in a real area, see "Verified facts"), so with the
    linear mapping they would sample about 30 % in from the edges. Such a
    light has several channels, all on the TV side (y at least NEAR_TV) and
    some left and some right of the centre (x); it is decided for the whole
    light, so a strip from the TV to the back of the room is left as it is.
    Each such light is scaled from the centre until its outermost channel
    reaches the edge, per axis; a light that reaches the edge already is left
    as it is, never pulled in. Other lights keep their positions.
    """
    lights: dict[str, list[int]] = {}
    for index, channel in enumerate(channels):
        if channel.light is not None:
            lights.setdefault(channel.light, []).append(index)
    stretched = list(channels)
    for indexes in lights.values():
        light = [channels[i] for i in indexes]
        if (
            len(light) < 2
            or any(c.y is None or c.y < NEAR_TV for c in light)
            or not min(c.x for c in light) < 0 < max(c.x for c in light)
        ):
            continue
        reach_x = max(abs(c.x) for c in light)
        reach_z = max(abs(c.z) for c in light)
        for i, channel in zip(indexes, light, strict=True):
            stretched[i] = dataclasses.replace(
                channel,
                x=channel.x / reach_x if MIN_SPREAD <= reach_x < 1 else channel.x,
                z=channel.z / reach_z if MIN_SPREAD <= reach_z < 1 else channel.z,
            )
    return stretched


def compute(
    frame: Frame,
    channels: list[Channel],
    intensity: float,
    picture: Picture | None = None,
) -> list[ChannelColor]:
    """Return a colour per channel, in the order of `channels`.

    intensity scales saturation: 0 = grey/white, 1 = as on screen, 2 = boosted.
    `picture` is the part of the frame inside letterbox bars, if any.
    """
    regions = channel_regions(channels, frame.width, frame.height, picture)
    return [
        ChannelColor(channel.id, _saturate(_average(frame, region), intensity))
        for channel, region in zip(channels, regions, strict=True)
    ]


def _band(centre: float, start: int, end: int) -> tuple[int, int]:
    """A REGION_SIZE band around centre (0..1 of start..end), kept inside it."""
    size = end - start
    length = max(1, round(REGION_SIZE * size))
    first = round(start + centre * size - length / 2)
    first = min(max(first, start), end - length)
    return first, first + length


def _average(
    frame: Frame, region: tuple[int, int, int, int]
) -> tuple[float, float, float]:
    """The colour of the lit pixels in a region, or black.

    A weighted average (#233, spike #40): each lit pixel counts with
    (WEIGHT_FLOOR + saturation²) x value. Colourful pixels lead and dark ones
    give way, so a mixed region shows its colour (spotlights on a dark stage,
    signs in a city at night); bright white keeps its say, so a small coloured
    object only tints a white scene; a region of one colour or a smooth
    gradient gives about its average. With high the largest component and
    spread = high - lowest, (F + s²) x v is (F + (spread / high)²) x high / 255.
    """
    col0, col1, row0, row1 = region
    pixels = frame.pixels
    stride = frame.width * 3
    red = green = blue = weight = 0.0
    lit = 0
    for row in range(row0, row1):
        start = row * stride
        for i in range(start + col0 * 3, start + col1 * 3, 3):
            r, g, b = pixels[i], pixels[i + 1], pixels[i + 2]
            high = max(r, g, b)
            if high > BLACK_LEVEL:
                spread = (high - min(r, g, b)) / high
                w = (WEIGHT_FLOOR + spread * spread) * high
                red += w * r
                green += w * g
                blue += w * b
                weight += w
                lit += 1
    if lit == 0 or lit < MIN_LIT_SHARE * (col1 - col0) * (row1 - row0):
        return BLACK
    scale = 255 * weight
    return red / scale, green / scale, blue / scale


def _saturate(
    rgb: tuple[float, float, float], intensity: float
) -> tuple[float, float, float]:
    """Scale saturation by intensity, keeping hue and brightness."""
    if rgb == BLACK or intensity == 1:
        return rgb
    hue, saturation, value = colorsys.rgb_to_hsv(*rgb)
    return colorsys.hsv_to_rgb(hue, min(1.0, saturation * intensity), value)
