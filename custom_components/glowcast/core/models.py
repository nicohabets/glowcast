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
    # What the light shows before Glowcast claims it, as the output would send
    # it (0.0 - 1.0, its brightness included; black when it is off), so the
    # first colours fade in from there (architecture 3.5). None when the
    # output cannot read it: the fade-in starts from dark.
    start_rgb: tuple[float, float, float] | None = None
    # Front to back: +1 = the TV side of the room, -1 = the far side; None
    # when the output has no depth (Home Assistant lights).
    y: float | None = None
    # The light this channel is a part of, when a light has several channels
    # (the segments of a gradient lightstrip share it); None otherwise.
    light: str | None = None


@dataclass(frozen=True)
class ChannelColor:
    """The colour for one channel."""

    channel_id: str
    rgb: tuple[float, float, float]  # 0.0 - 1.0
