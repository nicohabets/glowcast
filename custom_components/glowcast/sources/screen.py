"""Frame source for a computer screen, through Glowcast's screen helper.

The helper (`screen_helper/`, architecture 8.3) serves the screen as 64x36 RGB
frames over plain HTTP, protocol version 1. Glowcast pairs with it once and
keeps the token in its own entry's data.
"""

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
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
# The helper's protocol version this source speaks (architecture 8.3).
PROTOCOL = 1

WIDTH, HEIGHT = 64, 36
FRAME_BYTES = WIDTH * HEIGHT * 3
# 4 frames per second is enough for a screen (product owner's decision, #110).
MAX_RATE_HZ = 4.0
# One request; two of them and the pause between fit in the 5 s of the port.
REQUEST_TIMEOUT = 2.0
CAPTURE_TIMEOUT = 5.0
# The helper hands out one grab for 50 ms; the retry asks for a new one.
RETRY_DELAY = 0.1
# No picture for this long after a frame turns the source off (product owner's
# decision, #110), like the 3 s wait for an all-zero picture (#68), so a few
# slow grabs on a busy computer do not stop syncing.
NO_PICTURE_WAIT = 3.0


class _NoPicture(Exception):
    """The helper answered, but has no picture (or no good answer)."""


class ScreenFrameSource:
    """Pulls frames of a computer screen from the screen helper."""

    max_rate_hz = MAX_RATE_HZ

    def __init__(
        self,
        hass: HomeAssistant,
        helper_id: str,
        host: str,
        port: int,
        token: str,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initialize for the helper with this id at this address, with
        Glowcast's token; `now` is the clock (a fake one in tests)."""
        self._hass = hass
        self._now = now
        self._helper_id = helper_id
        self._base = URL.build(scheme="http", host=host, port=port)
        self._headers = {"Authorization": f"Bearer {token}"}
        self._on = False
        # A frame came since start(): only then does no picture turn it off,
        # so the pipeline's startup grace keeps asking while the computer
        # wakes up or unlocks.
        self._delivered = False
        # When the no picture after the last frame began; None with a picture.
        self._no_picture_since: float | None = None

    async def start(self) -> None:
        """Check that Glowcast's helper answers and accepts the token.

        Frames are only asked from the helper with the stored id that speaks
        protocol 1, not from another helper or service at that address.
        """
        self._on = False
        self._delivered = False
        self._no_picture_since = None
        reason: str
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                async with self._get("/v1/info") as response:
                    if response.status == 401:
                        raise SourceAuthFailed(
                            "The screen helper rejected Glowcast's token; pair again"
                        )
                    info = await response.json() if response.status == 200 else None
            if not isinstance(info, dict):
                reason = "The screen helper gave an unexpected answer"
            elif info.get("id") != self._helper_id:
                reason = "Another screen helper or service answers at this address"
            elif info.get("protocol") != PROTOCOL:
                reason = "The screen helper speaks another protocol version"
            else:
                self._on = True
                return
        except TimeoutError:
            reason = "The screen helper did not respond in time"
        except (OSError, ValueError, aiohttp.ClientError):
            # ValueError: an answer that is not JSON.
            reason = "Could not connect to the screen helper"
        raise SourceUnavailable(reason)

    def is_on(self) -> bool:
        """Return whether the helper answers with a picture.

        False after start() failed, or once a failed capture ends
        NO_PICTURE_WAIT or more after the start of the first failed capture
        in a row, after a frame came (no picture or no answer).
        """
        return self._on

    async def capture(self) -> Frame:
        """Return the screen; ask once more before reporting no picture."""
        if not self._on:
            raise SourceUnavailable("The screen helper is not reachable")
        # The wait counts from the start of the first failed capture, so one
        # that hangs for 4 s already counts as more than NO_PICTURE_WAIT.
        begin = self._now()
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
        if self._delivered:
            if self._no_picture_since is None:
                self._no_picture_since = begin
            if self._now() - self._no_picture_since >= NO_PICTURE_WAIT:
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
                    if response.content_length != FRAME_BYTES:
                        # Not read: an oversized body never reaches memory.
                        _LOGGER.debug(
                            "The screen helper sent %s bytes, not a frame",
                            response.content_length,
                        )
                        raise _NoPicture
                    pixels = await response.read()
        except (TimeoutError, OSError, aiohttp.ClientError):
            raise _NoPicture from None
        if len(pixels) != FRAME_BYTES:
            _LOGGER.debug("The screen helper sent %d bytes, not a frame", len(pixels))
            raise _NoPicture
        self._on = self._delivered = True
        self._no_picture_since = None
        return Frame(width=WIDTH, height=HEIGHT, pixels=pixels)

    @asynccontextmanager
    async def _get(self, path: str) -> AsyncIterator[aiohttp.ClientResponse]:
        """A GET request to the helper, with the token; never logged."""
        session = async_get_clientsession(self._hass)
        async with session.get(
            self._base.with_path(path), headers=self._headers
        ) as response:
            yield response


def create(
    hass: HomeAssistant, data: Mapping[str, Any], options: Mapping[str, Any]
) -> ScreenFrameSource:
    """Build the source from a Glowcast entry's data (architecture 5)."""
    source = data[CONF_SOURCE]
    return ScreenFrameSource(
        hass,
        source[CONF_HELPER_ID],
        source[CONF_HOST],
        source[CONF_PORT],
        source[CONF_TOKEN],
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
