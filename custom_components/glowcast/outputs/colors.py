"""What a light shows, as Glowcast would send it: colours in 0-1 (#153).

Shared by the output types that read their lights' states for the fade-in.
"""

from collections.abc import Sequence

type RGB = tuple[float, float, float]


def at_level(rgb: Sequence[float], level: float) -> RGB:
    """A colour (any scale) at a brightness from 0 to 1: its peak becomes
    `level`, each value kept within 0-1. Black without a colour."""
    peak = max(rgb)
    if peak <= 0:
        return 0.0, 0.0, 0.0
    r, g, b = (min(1.0, max(0.0, value / peak * level)) for value in rgb)
    return r, g, b


def average(colors: Sequence[RGB]) -> RGB | None:
    """The average of these colours; None without any."""
    if not colors:
        return None
    r, g, b = (sum(values) / len(colors) for values in zip(*colors, strict=True))
    return r, g, b
