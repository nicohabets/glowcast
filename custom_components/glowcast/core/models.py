"""Core data types shared by sources, the colour engine and outputs."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Frame:
    """A downscaled picture from the TV."""

    width: int
    height: int
    pixels: bytes  # packed RGB, row-major, already downscaled (for example 64x36)


@dataclass(frozen=True)
class Channel:
    """A light (or a segment of one) with a position relative to the screen."""

    id: str  # stable id, for example the Hue channel id
    name: str
    x: float  # -1 = left of the screen, +1 = right
    z: float  # -1 = floor / bottom of the picture, +1 = ceiling / top


@dataclass(frozen=True)
class ChannelColor:
    """The colour for one channel."""

    channel_id: str
    rgb: tuple[float, float, float]  # 0.0 - 1.0
