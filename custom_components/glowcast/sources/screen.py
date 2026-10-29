"""Frame source for a computer screen, through Glowcast's screen helper.

The helper (`screen_helper/`, architecture 8.3) serves the screen as 64x36 RGB
frames over plain HTTP, protocol version 1. Glowcast pairs with it once and
keeps the token in its own entry's data.
"""

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

import aiohttp
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from yarl import URL

from ..const import CONF_SOURCE
from ..core.models import Frame
from ..core.ports import SourceAuthFailed, SourceUnavailable

_LOGGER = logging.getLogger(__name__)

# The source type (architecture 5).
SOURCE_SCREEN = "screen"
# The helper's id in the source block, which stays when its address changes.
CONF_HELPER_ID = "helper_id"

WIDTH, HEIGHT = 64, 36
FRAME_BYTES = WIDTH * HEIGHT * 3
# 4 frames per second is enough for a screen (product owner's decision, #110).
MAX_RATE_HZ = 4.0
# One request; two of them and the pause between fit in the 5 s of the port.
REQUEST_TIMEOUT = 2.0
CAPTURE_TIMEOUT = 5.0
# The helper hands out one grab for 50 ms; the retry asks for a new one.
RETRY_DELAY = 0.1


class _NoPicture(Exception):
    """The helper answered, but has no picture (or no good answer)."""


class ScreenFrameSource:
    """Pulls frames of a computer screen from the screen helper."""

    max_rate_hz = MAX_RATE_HZ

    def __init__(self, hass: HomeAssistant, host: str, port: int, token: str) -> None:
        """Initialize for the helper at this address, with Glowcast's token."""
        self._hass = hass
        self._base = URL.build(scheme="http", host=host, port=port)
        self._headers = {"Authorization": f"Bearer {token}"}
        self._on = False

    async def start(self) -> None:
        """Check that the helper answers and accepts the token."""
        self._on = False
        reason: str
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                async with self._get("/v1/info") as response:
                    if response.status == 401:
                        raise SourceAuthFailed(
                            "The screen helper rejected Glowcast's token; pair again"
                        )
                    if response.status == 200:
                        self._on = True
                        return
            reason = "The screen helper gave an unexpected answer"
        except TimeoutError:
            reason = "The screen helper did not respond in time"
        except (OSError, aiohttp.ClientError):
            reason = "Could not connect to the screen helper"
        raise SourceUnavailable(reason)

    def is_on(self) -> bool:
        """Return whether the helper answered with a picture, the last time."""
        return self._on

    async def capture(self) -> Frame:
        """Return the screen; ask once more before reporting no picture."""
        if not self._on:
            raise SourceUnavailable("The screen helper is not reachable")
        # Reasons are set outside the except blocks, so no error from the
        # request is chained to SourceUnavailable.
        reason: str
        try:
            async with asyncio.timeout(CAPTURE_TIMEOUT):
                try:
                    return await self._async_frame()
                except _NoPicture:
                    pass
                await asyncio.sleep(RETRY_DELAY)
                try:
                    return await self._async_frame()
                except _NoPicture:
                    pass
            reason = "The screen helper has no picture or is not reachable"
        except TimeoutError:
            reason = "The screen helper did not respond in time"
        self._on = False
        raise SourceUnavailable(reason)

    async def close(self) -> None:
        """Nothing to release: the requests use Home Assistant's session."""
        self._on = False

    async def _async_frame(self) -> Frame:
        """One frame request. Raises _NoPicture or SourceAuthFailed."""
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                async with self._get("/v1/frame") as response:
                    if response.status == 401:
                        raise SourceAuthFailed(
                            "The screen helper rejected Glowcast's token; pair again"
                        )
                    if response.status != 200:
                        # 204: no picture (locked or asleep); others: broken.
                        raise _NoPicture
                    pixels = await response.read()
        except (TimeoutError, OSError, aiohttp.ClientError):
            raise _NoPicture from None
        if len(pixels) != FRAME_BYTES:
            _LOGGER.debug("The screen helper sent %d bytes, not a frame", len(pixels))
            raise _NoPicture
        self._on = True
        return Frame(width=WIDTH, height=HEIGHT, pixels=pixels)

    def _get(self, path: str) -> Any:
        """A GET request to the helper, with the token; never logged."""
        session = async_get_clientsession(self._hass)
        return session.get(self._base.with_path(path), headers=self._headers)


def create(
    hass: HomeAssistant, data: Mapping[str, Any], options: Mapping[str, Any]
) -> ScreenFrameSource:
    """Build the source from a Glowcast entry's data (architecture 5)."""
    source = data[CONF_SOURCE]
    return ScreenFrameSource(
        hass, source[CONF_HOST], source[CONF_PORT], source[CONF_TOKEN]
    )


def is_missing(
    hass: HomeAssistant, data: Mapping[str, Any], options: Mapping[str, Any]
) -> bool:
    """Never: the helper is not an integration in Home Assistant."""
    return False


def device_identifier(data: Mapping[str, Any]) -> None:
    """None: the computer has no device in Home Assistant to link to."""
    return None


def device_id(hass: HomeAssistant, data: Mapping[str, Any]) -> None:
    """None: the computer has no device in Home Assistant."""
    return None
