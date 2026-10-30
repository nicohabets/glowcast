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
# The helper's port unless it was started with another one.
DEFAULT_PORT = 8488

WIDTH, HEIGHT = 64, 36
FRAME_BYTES = WIDTH * HEIGHT * 3
# 4 frames per second is enough for a screen (product owner's decision, #110).
MAX_RATE_HZ = 4.0
# One request; two of them and the pause between fit in the 5 s of the port.
REQUEST_TIMEOUT = 2.0
CAPTURE_TIMEOUT = 5.0
# The helper hands out one grab for 50 ms; the retry asks for a new one.
RETRY_DELAY = 0.1
# A wrong pairing code costs 1 s at the helper, one attempt at a time.
PAIR_TIMEOUT = 10.0
# No picture for this long after a frame turns the source off (product owner's
# decision, #110), so a few slow grabs on a busy computer do not stop syncing.
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
        self._host = host
        self._port = port
        self._token = token
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
        # Raises SourceAuthFailed (401) or SourceUnavailable itself.
        info = await async_info(self._hass, self._host, self._port, self._token)
        if info.get("id") != self._helper_id:
            raise SourceUnavailable(
                "Another screen helper or service answers at this address"
            )
        if info.get("protocol") != PROTOCOL:
            raise SourceUnavailable("The screen helper speaks another protocol version")
        self._on = True

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
            _url(self._host, self._port, path), headers=_auth(self._token)
        ) as response:
            yield response


class PairingRefused(Exception):
    """The helper refused the pairing code.

    `reason` is the helper's error: "wrong_code", or "pairing_unavailable"
    when it has no code (started with --non-interactive).
    """

    def __init__(self, reason: str) -> None:
        """Initialize with the helper's reason."""
        super().__init__(reason)
        self.reason = reason


async def async_reachable(hass: HomeAssistant, host: str, port: int) -> bool:
    """Whether something answers HTTP at this address.

    Without a token the helper answers 401, which counts as an answer.
    """
    try:
        async with asyncio.timeout(REQUEST_TIMEOUT):
            async with async_get_clientsession(hass).get(_url(host, port, "/v1/info")):
                return True
    except (TimeoutError, OSError, ValueError, aiohttp.ClientError):
        # ValueError: an address that is not a valid host name.
        return False


async def async_pair(
    hass: HomeAssistant, host: str, port: int, code: str
) -> tuple[str, str]:
    """Pair with the helper's pairing code; return (token, helper id).

    Raises PairingRefused for a refused code, SourceUnavailable when the helper
    cannot be reached or gives an unexpected answer.
    """
    reason: str
    try:
        async with asyncio.timeout(PAIR_TIMEOUT):
            async with async_get_clientsession(hass).post(
                _url(host, port, "/v1/pair"), json={"code": code}
            ) as response:
                status = response.status
                answer = await response.json(content_type=None)
        if status == 403 and isinstance(answer, dict):
            raise PairingRefused(str(answer.get("error")))
        if status == 400:
            # A code the helper cannot read is a wrong code too.
            raise PairingRefused("wrong_code")
        if (
            status == 200
            and isinstance(answer, dict)
            and isinstance(answer.get("token"), str)
            and isinstance(answer.get("id"), str)
        ):
            return answer["token"], answer["id"]
        reason = "The screen helper gave an unexpected answer"
    except TimeoutError:
        reason = "The screen helper did not respond in time"
    except (OSError, ValueError, aiohttp.ClientError):
        # ValueError: an answer that is not JSON.
        reason = "Could not connect to the screen helper"
    raise SourceUnavailable(reason)


async def async_info(
    hass: HomeAssistant, host: str, port: int, token: str
) -> dict[str, Any]:
    """The helper's /v1/info, asked with Glowcast's token.

    Raises SourceAuthFailed for a rejected token, SourceUnavailable when the
    helper cannot be reached or gives an unexpected answer.
    """
    reason: str
    try:
        async with asyncio.timeout(REQUEST_TIMEOUT):
            async with async_get_clientsession(hass).get(
                _url(host, port, "/v1/info"),
                headers=_auth(token),
            ) as response:
                if response.status == 401:
                    raise SourceAuthFailed(
                        "The screen helper rejected Glowcast's token; pair again"
                    )
                info = await response.json() if response.status == 200 else None
        if isinstance(info, dict):
            return info
        reason = "The screen helper gave an unexpected answer"
    except TimeoutError:
        reason = "The screen helper did not respond in time"
    except (OSError, ValueError, aiohttp.ClientError):
        # ValueError: an answer that is not JSON.
        reason = "Could not connect to the screen helper"
    raise SourceUnavailable(reason)


def _url(host: str, port: int, path: str) -> URL:
    """The helper's URL for this path."""
    return URL.build(scheme="http", host=host, port=port, path=path)


def _auth(token: str) -> dict[str, str]:
    """The header with Glowcast's token; never logged."""
    return {"Authorization": f"Bearer {token}"}


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


def device_id(hass: HomeAssistant, data: Mapping[str, Any]) -> None:
    """None: the computer has no device in Home Assistant."""
    return None
