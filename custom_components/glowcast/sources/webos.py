"""Frame source for LG webOS TVs, reusing Home Assistant's webostv pairing."""

import asyncio
import io
import logging
from collections.abc import Mapping
from contextlib import suppress
from typing import Any

import aiohttp
from aiowebostv import WebOsClient, WebOsTvState
from aiowebostv.exceptions import WebOsTvError, WebOsTvPairError
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_CLIENT_SECRET, CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from PIL import Image

from ..const import CONF_SOURCE, CONF_UNIQUE_ID
from ..core.models import Frame
from ..core.ports import SourceUnavailable

_LOGGER = logging.getLogger(__name__)

WEBOSTV_DOMAIN = "webostv"

FRAME_SIZE = (64, 36)
# aiowebostv waits up to 10-20 s for single replies; the pipeline's startup
# grace is 10 s, so bound the whole connect.
START_TIMEOUT = 8.0
RECONNECT_TIMEOUT = 8.0
CAPTURE_TIMEOUT = 5.0

CAPTURE_URI = "tv/executeOneShot"
CAPTURE_PAYLOAD = {"path": "/tmp/capture.jpg", "method": "DISPLAY", "format": "JPG"}


class WebOsFrameSource:
    """Captures the TV picture through webOS's local screenshot call."""

    def __init__(self, hass: HomeAssistant, unique_id: str) -> None:
        """Initialize for the webostv config entry with this unique id."""
        self._hass = hass
        self._unique_id = unique_id
        self._client: WebOsClient | None = None
        self._watcher: asyncio.Task[None] | None = None
        self._last_on = False
        self._lost = False
        self._connected = asyncio.Event()

    async def start(self) -> None:
        """Connect to the TV with the credentials of HA's webostv entry."""
        await self.close()

        entry = self._hass.config_entries.async_entry_for_domain_unique_id(
            WEBOSTV_DOMAIN, self._unique_id
        )
        if entry is None or entry.state is not ConfigEntryState.LOADED:
            raise SourceUnavailable(
                "The TV is not set up in the LG webOS TV integration (or not loaded)"
            )

        client = WebOsClient(
            entry.data[CONF_HOST],
            client_key=entry.data[CONF_CLIENT_SECRET],
            client_session=async_get_clientsession(self._hass),
        )
        self._client = client
        self._last_on = False
        self._lost = False
        await client.register_state_update_callback(self._async_state_updated)

        reason: str | None = None
        try:
            async with asyncio.timeout(START_TIMEOUT):
                await client.connect()
        except TimeoutError:
            reason = "The TV did not respond in time"
        except WebOsTvPairError:
            reason = "The TV rejected the pairing of the LG webOS TV integration"
        except (OSError, aiohttp.ClientError, WebOsTvError):
            reason = "Could not connect to the TV"
        if reason is not None:
            await self.close()
            raise SourceUnavailable(reason)

        self._watcher = self._hass.async_create_background_task(
            self._async_watch_connection(client), "glowcast webos connection"
        )

    def is_on(self) -> bool:
        """Return whether the TV is on with the screen on."""
        return not self._lost and self._last_on

    async def capture(self) -> Frame:
        """Take a screenshot and return it downscaled."""
        client = self._client
        if client is None or not self.is_on():
            raise SourceUnavailable("The TV is off or not connected")

        # Reasons are set outside the except blocks so the original error, whose
        # text can contain the image URL, is not chained to SourceUnavailable.
        reason: str
        try:
            async with asyncio.timeout(CAPTURE_TIMEOUT):
                if client.connection is None:
                    await self._async_wait_for_reconnect(client)
                response = await client.request(CAPTURE_URI, CAPTURE_PAYLOAD)
                data = await self._async_download(response["imageUri"])
                return await self._hass.async_add_executor_job(_decode, data)
        except TimeoutError:
            reason = "The capture timed out"
        except WebOsTvError:
            reason = "The TV refused the capture"
        except (aiohttp.ClientError, KeyError, TypeError):
            reason = "Could not download the capture"
        except (OSError, ValueError):
            reason = "Could not read the capture"
        raise SourceUnavailable(reason)

    async def close(self) -> None:
        """Stop watching the connection and disconnect."""
        watcher, self._watcher = self._watcher, None
        if watcher is not None:
            watcher.cancel()
            with suppress(asyncio.CancelledError):
                await watcher

        self._last_on = False
        client, self._client = self._client, None
        if client is not None:
            client.unregister_state_update_callback(self._async_state_updated)
            await client.disconnect()

    async def _async_wait_for_reconnect(self, client: WebOsClient) -> None:
        """Wait for the reconnect in progress, so it gets its chance first."""
        watcher = self._watcher
        if watcher is None or watcher.done():
            raise SourceUnavailable("The TV is off or not connected")
        # Only set again by a state update on a live connection.
        self._connected.clear()
        waiter = asyncio.ensure_future(self._connected.wait())
        try:
            await asyncio.wait({waiter, watcher}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            waiter.cancel()
        if client.connection is None or not self.is_on():
            raise SourceUnavailable("Lost the connection to the TV")

    async def _async_download(self, url: str) -> bytes:
        """Download the screenshot; the TV uses a self-signed certificate."""
        session = async_get_clientsession(self._hass)
        async with session.get(url, ssl=False) as response:
            response.raise_for_status()
            return await response.read()

    async def _async_state_updated(self, state: WebOsTvState) -> None:
        """Remember the TV's power state from real updates.

        aiowebostv clears its state and calls back once more when the websocket
        drops; by then the connection is gone, so that call is ignored and the
        last known state holds while the source reconnects.
        """
        if self._client is not None and self._client.connection is not None:
            self._last_on = state.is_on and state.is_screen_on
            self._connected.set()

    async def _async_watch_connection(self, client: WebOsClient) -> None:
        """Reconnect once whenever the websocket drops."""
        while True:
            task: asyncio.Task[Any] | None = client.connect_task
            if task is not None:
                # Wait until aiowebostv has fully closed the old connection;
                # connect() would report the old one as still connected.
                await asyncio.wait({task})
            try:
                async with asyncio.timeout(RECONNECT_TIMEOUT):
                    await client.connect()
            except (TimeoutError, OSError, aiohttp.ClientError, WebOsTvError):
                _LOGGER.warning("Lost the connection to the TV and could not reconnect")
                self._lost = True
                return
            _LOGGER.debug("Reconnected to the TV")


def create(
    hass: HomeAssistant, data: Mapping[str, Any], options: Mapping[str, Any]
) -> WebOsFrameSource:
    """Build the source from a Glowcast entry's data (architecture 5)."""
    return WebOsFrameSource(hass, data[CONF_SOURCE][CONF_UNIQUE_ID])


def is_missing(hass: HomeAssistant, data: Mapping[str, Any]) -> bool:
    """Whether the referenced webostv entry is removed or not loaded."""
    entry = hass.config_entries.async_entry_for_domain_unique_id(
        WEBOSTV_DOMAIN, data[CONF_SOURCE][CONF_UNIQUE_ID]
    )
    return entry is None or entry.state is not ConfigEntryState.LOADED


def device_identifier(data: Mapping[str, Any]) -> tuple[str, str]:
    """The TV's device in Home Assistant, to link Glowcast's device to."""
    return (WEBOSTV_DOMAIN, data[CONF_SOURCE][CONF_UNIQUE_ID])


def device_id(hass: HomeAssistant, data: Mapping[str, Any]) -> str | None:
    """The TV device's id in Home Assistant's device registry, if it exists."""
    entry = hass.config_entries.async_entry_for_domain_unique_id(
        WEBOSTV_DOMAIN, data[CONF_SOURCE][CONF_UNIQUE_ID]
    )
    if entry is None:
        return None
    registry = dr.async_get(hass)
    identifier = device_identifier(data)
    if hasattr(registry, "async_get_device_by_identifier"):
        # Newer HA: identifiers are only unique within a config entry.
        device = registry.async_get_device_by_identifier(identifier, entry.entry_id)
    else:
        device = registry.async_get_device(identifiers={identifier})
    return device.id if device else None


def tvs(hass: HomeAssistant) -> dict[str, str]:
    """Return {unique id: title} of loaded webostv entries."""
    return {
        entry.unique_id: entry.title
        for entry in hass.config_entries.async_loaded_entries(WEBOSTV_DOMAIN)
        if entry.unique_id is not None
    }


def _decode(data: bytes) -> Frame:
    """Decode a JPEG and downscale it to FRAME_SIZE (runs in the executor)."""
    with Image.open(io.BytesIO(data)) as image:
        image.draft("RGB", FRAME_SIZE)
        small = image.convert("RGB").resize(FRAME_SIZE, Image.Resampling.BOX)
    return Frame(width=small.width, height=small.height, pixels=small.tobytes())
