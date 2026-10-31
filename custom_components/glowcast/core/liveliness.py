"""Liveliness: how lively the lights react, Calm, Normal or Lively (#16).

One setting for the trade-offs the spikes measured: a soft fade against a
fast cut (#35), the colourful pixels' weight against flicker (#40), and how
much lights at the back blend with the whole picture (#15). Normal is
exactly v0.4: its profile is the colour engine's and the light state's own
constants. Pure, no Home Assistant imports.
"""

from dataclasses import dataclass
from enum import StrEnum

from .color_engine import FAR_BLEND, WEIGHT_FLOOR
from .light_state import CUT, CUT_FADE


@dataclass(frozen=True)
class Profile:
    """The knobs of one level."""

    # The fade between captures takes this many capture intervals.
    fade_intervals: float
    # A channel that changes by more than `cut` (the largest change of one
    # component, 0-1) fades over `cut_fade` seconds; None: no fast cut, a cut
    # fades like everything else.
    cut: float | None
    cut_fade: float
    # A lit pixel counts with (weight_floor + saturation²) x value: a higher
    # floor is closer to the plain average, softer colours.
    weight_floor: float
    # The share of the whole picture's colour at the back wall (#15).
    far_blend: float


class Liveliness(StrEnum):
    """How lively the lights react."""

    CALM = "calm"
    NORMAL = "normal"
    LIVELY = "lively"

    @property
    def profile(self) -> Profile:
        """The level's knobs."""
        return _PROFILES[self]


_PROFILES = {
    # Every change fades over about two capture intervals, cuts too; softer
    # colours, and lights at the back follow the whole picture more.
    Liveliness.CALM: Profile(
        fade_intervals=2.0,
        cut=None,
        cut_fade=CUT_FADE,
        weight_floor=1.0,
        far_blend=0.75,
    ),
    # v0.4, unchanged.
    Liveliness.NORMAL: Profile(
        fade_intervals=1.0,
        cut=CUT,
        cut_fade=CUT_FADE,
        weight_floor=WEIGHT_FLOOR,
        far_blend=FAR_BLEND,
    ),
    # Smaller jumps count as cuts and show faster; the colourful pixels count
    # even more, and lights at the back follow their own part more.
    Liveliness.LIVELY: Profile(
        fade_intervals=1.0, cut=0.18, cut_fade=0.04, weight_floor=0.2, far_blend=0.3
    ),
}
