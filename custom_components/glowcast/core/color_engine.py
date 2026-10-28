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


def channel_region(
    channel: Channel, width: int, height: int
) -> tuple[int, int, int, int]:
    """Return the part of the frame a channel samples: col0, col1, row0, row1.

    x and z map linearly from -1..1 to the frame edges (z = +1 is the top
    row). The region is centred there and moved inwards where it would cross
    an edge, so it always covers REGION_SIZE of the frame.
    """
    x = min(max(channel.x, -1.0), 1.0)
    z = min(max(channel.z, -1.0), 1.0)
    col0, col1 = _band((x + 1) / 2, width)
    row0, row1 = _band((1 - z) / 2, height)
    return col0, col1, row0, row1


def compute(
    frame: Frame, channels: list[Channel], intensity: float
) -> list[ChannelColor]:
    """Return a colour per channel, in the order of `channels`.

    intensity scales saturation: 0 = grey/white, 1 = as on screen, 2 = boosted.
    """
    return [
        ChannelColor(channel.id, _saturate(_average(frame, channel), intensity))
        for channel in channels
    ]


def _band(centre: float, size: int) -> tuple[int, int]:
    """A REGION_SIZE band around centre (0..1), kept inside 0..size."""
    length = max(1, round(REGION_SIZE * size))
    start = round(centre * size - length / 2)
    start = min(max(start, 0), size - length)
    return start, start + length


def _average(frame: Frame, channel: Channel) -> tuple[float, float, float]:
    """Average of the lit pixels in the channel's region, or black."""
    col0, col1, row0, row1 = channel_region(channel, frame.width, frame.height)
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
