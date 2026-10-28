"""Core data types shared by sources, the colour engine and outputs."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Frame:
    """A downscaled picture from the TV."""

    width: int
    height: int
    pixels: bytes  # packed RGB, row-major, already downscaled (for example 64x36)
