"""The fakes behind Glowcast's FrameSource and LightOutput ports, for the demo.

No network: the source makes its picture in memory, the output keeps the last
colours. Built on Glowcast's core only, like the adapters they replace.
"""

import asyncio
import logging
import math
import time
from collections.abc import Mapping
from typing import Any

from custom_components.glowcast.core.models import Channel, ChannelColor, Frame

_LOGGER = logging.getLogger(__package__)

WIDTH, HEIGHT = 64, 36  # as the webOS source delivers them
# About as long as a capture from the TV takes (about 4 per second).
CAPTURE_SECONDS = 0.24
# Blobs of colour moving across the picture: (r, g, b), start x in the
# picture's widths, speed in widths per second.
BLOBS = [
    ((1.0, 0.45, 0.05), 0.0, 0.05),
    ((0.05, 0.75, 0.7), 0.35, -0.035),
    ((0.6, 0.15, 0.9), 0.7, 0.025),
]
BLOB_WIDTH = 0.18  # in the picture's widths
# Channels per entertainment area, as the fake bridge reports them
# (AREAS in fake_bridge.py; this stub cannot import it).
AREA_CHANNELS = {"area-tv": 5, "area-room": 3}


def picture(seconds: float) -> Frame:
    """The picture at this moment: the blobs over a dark background."""
    columns = []
    for x in range(WIDTH):
        rgb = [0.03, 0.03, 0.05]
        for color, start, speed in BLOBS:
            # The distance to the blob's centre, round the picture's edges.
            distance = (x / WIDTH - start - speed * seconds + 0.5) % 1.0 - 0.5
            weight = math.exp(-((distance / BLOB_WIDTH) ** 2))
            rgb = [min(1.0, c + weight * b) for c, b in zip(rgb, color, strict=True)]
        columns.append(rgb)
    pixels = bytearray()
    for y in range(HEIGHT):
        shade = 0.6 + 0.4 * y / (HEIGHT - 1)  # brighter towards the bottom
        for rgb in columns:
            pixels += bytes(round(255 * c * shade) for c in rgb)
    return Frame(width=WIDTH, height=HEIGHT, pixels=bytes(pixels))


class FakeTvSource:
    """A TV that is always on and shows the moving blobs."""

    def __init__(self) -> None:
        """Not started yet."""
        self._started = False

    async def start(self) -> None:
        """Connect: always works."""
        self._started = True

    def is_on(self) -> bool:
        """On from start() until close()."""
        return self._started

    async def capture(self) -> Frame:
        """The picture, after about as long as the TV takes."""
        await asyncio.sleep(CAPTURE_SECONDS)
        return picture(time.monotonic())

    async def close(self) -> None:
        """Disconnect; safe to call twice."""
        self._started = False


class FakeLightOutput:
    """Lights for the chosen area that accept colours and show them nowhere."""

    max_rate_hz = 25.0  # as Hue's entertainment stream

    def __init__(self, options: Mapping[str, Any]) -> None:
        """For the area in the entry's options."""
        self._area_id = str(options["output"].get("area_id", ""))
        self._claimed = False
        self.colors: list[ChannelColor] = []

    async def start(self) -> list[Channel]:
        """Claim the area's channels, spread from left to right like the bridge's."""
        count = AREA_CHANNELS.get(self._area_id, 5)
        self._claimed = True
        _LOGGER.info("demo: fake lights claimed, %d channels", count)
        return [
            Channel(id=str(c), name=f"Channel {c}", x=c / 2 - 1, z=0.0)
            for c in range(count)
        ]

    def send(self, colors: list[ChannelColor]) -> None:
        """Keep the colours."""
        self.colors = colors

    async def check(self) -> None:
        """Never lost."""

    async def stop(self) -> None:
        """Release the lights; safe to call twice."""
        if self._claimed:
            self._claimed = False
            _LOGGER.info("demo: fake lights released")

    async def close(self) -> None:
        """Nothing to close."""
