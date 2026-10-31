"""The light test: known colours by position, to check where each light sits.

Architecture 3.5 (#182). Pure and synchronous, no asyncio and no Home
Assistant imports: the time is an argument. The test colours come from fixed
pattern frames through the colour engine, so every light gets the colour of
its position as it would from a picture, whatever the output type. Only the
position on the screen counts: a light far from the TV gets the colour of its
place too, not the blend with the whole picture it gets while syncing (#15).
"""

import dataclasses

from .color_engine import NEAR_TV, compute
from .light_state import LightState
from .models import Channel, ChannelColor, Frame

# How long each pattern shows.
STEP = 3.0
WIDTH = 64
HEIGHT = 36
# The top band of the second pattern: green above this row, white below it,
# so a light at the middle of the picture (ambient) samples only white.
GREEN_ROWS = 12

RED = (255, 0, 0)
BLUE = (0, 0, 255)
GREEN = (0, 255, 0)
WHITE = (255, 255, 255)


def pattern_frames() -> list[Frame]:
    """The test patterns in order: left red and right blue, then the top green
    and the rest (the bottom and the middle) white."""
    left_right = bytes(
        b
        for _ in range(HEIGHT)
        for x in range(WIDTH)
        for b in (RED if x < WIDTH // 2 else BLUE)
    )
    top_bottom = bytes(
        b
        for y in range(HEIGHT)
        for _ in range(WIDTH)
        for b in (GREEN if y < GREEN_ROWS else WHITE)
    )
    return [Frame(WIDTH, HEIGHT, left_right), Frame(WIDTH, HEIGHT, top_bottom)]


def pattern_colors(channels: list[Channel]) -> list[list[ChannelColor]]:
    """The engine's colours of each pattern for these channels.

    At intensity 1 (as on screen), whatever the user's intensity, so the
    colours stay recognisable. Without the depth of a channel further back
    than NEAR_TV, so it shows its place on the screen and not part of the
    whole picture (#270); a light around the TV keeps its depth, and so its
    stretch to the edges of the picture (#23), which a light with a channel
    further back never gets. Run it in the executor, like compute().
    """
    channels = [
        dataclasses.replace(channel, y=None)
        if channel.y is not None and channel.y < NEAR_TV
        else channel
        for channel in channels
    ]
    return [compute(frame, channels, 1.0) for frame in pattern_frames()]


class LightTest:
    """Which test colours to send when: each pattern for STEP seconds."""

    def __init__(self, steps: list[list[ChannelColor]], start: float) -> None:
        """Initialize with the colours of each pattern (pattern_colors()); the
        first shows from `start` on, at once (no fade)."""
        self._start = start
        self._steps: list[LightState] = []
        for colors in steps:
            state = LightState()
            state.set_target(colors, False, start)
            self._steps.append(state)

    def colors(self, now: float, brightness: float) -> list[ChannelColor] | None:
        """The colours to send at `now`, with brightness (0-1) and the glow as
        for a picture; None once the test is over."""
        index = int((now - self._start) // STEP)
        if index < 0:
            index = 0
        if index >= len(self._steps):
            return None
        return self._steps[index].colors(now, brightness)
