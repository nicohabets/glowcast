"""Colour engine: turns a frame into one colour per channel (architecture 3.3).

Pure and synchronous, no Home Assistant imports.
"""

import colorsys

from .models import Channel, ChannelColor, Frame

# Share of the frame's width and height each channel samples.
REGION_SIZE = 0.3
# A pixel whose brightest component is at or below this (0-255) counts as
# black: letterbox bars, including video black (16) and JPEG noise.
BLACK_LEVEL = 32
# A region with fewer lit pixels than this share is black.
MIN_LIT_SHARE = 0.05

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

    One step over all channels, so a mapping that depends on the others
    (#23) has them.
    """
    return [channel_region(channel, width, height, picture) for channel in channels]


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
    """Average of the lit pixels in a region, or black."""
    col0, col1, row0, row1 = region
    pixels = frame.pixels
    stride = frame.width * 3
    red = green = blue = lit = 0
    for row in range(row0, row1):
        start = row * stride
        for i in range(start + col0 * 3, start + col1 * 3, 3):
            r, g, b = pixels[i], pixels[i + 1], pixels[i + 2]
            if r > BLACK_LEVEL or g > BLACK_LEVEL or b > BLACK_LEVEL:
                red += r
                green += g
                blue += b
                lit += 1
    if lit == 0 or lit < MIN_LIT_SHARE * (col1 - col0) * (row1 - row0):
        return BLACK
    scale = 255 * lit
    return red / scale, green / scale, blue / scale


def _saturate(
    rgb: tuple[float, float, float], intensity: float
) -> tuple[float, float, float]:
    """Scale saturation by intensity, keeping hue and brightness."""
    if rgb == BLACK or intensity == 1:
        return rgb
    hue, saturation, value = colorsys.rgb_to_hsv(*rgb)
    return colorsys.hsv_to_rgb(hue, min(1.0, saturation * intensity), value)
