"""Light output that streams to a Hue entertainment area (architecture 3.4)."""

import asyncio
import logging
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_API_VERSION, CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from hue_entertainment import EntertainmentSession, HueEntertainmentAPI
from hue_entertainment.models import LightColorCommand

from ..core.models import Channel, ChannelColor
from ..core.ports import (
    OutputAuthFailed,
    OutputBusy,
    OutputLost,
    OutputNotFound,
    OutputTooManyChannels,
    OutputUnavailable,
)

_LOGGER = logging.getLogger(__name__)

HUE_DOMAIN = "hue"
AREAS_PATH = "/clip/v2/resource/entertainment_configuration"
AUTH_PATH = "/auth/v1"

MAX_CHANNELS = 20  # HueStream limit per message
# The DTLS handshake alone can block for about 23 s against an unreachable bridge.
START_TIMEOUT = 30.0
STOP_TIMEOUT = 10.0
STATUS_INTERVAL = 5.0
STATUS_TIMEOUT = 4.0
REACHABLE_TIMEOUT = 10.0
AREAS_TIMEOUT = 15.0
# The bridge allows 20 characters before the # of a devicetype and 19 after.
DEVICE_NAME_LENGTH = 19


class HueEntertainmentOutput:
    """Streams colours to one Hue entertainment area."""

    max_rate_hz = 25.0

    def __init__(
        self,
        hass: HomeAssistant,
        bridge_unique_id: str,
        app_key: str,
        client_key: str,
        area_id: str,
    ) -> None:
        """Initialize for the hue config entry with this unique id."""
        self._hass = hass
        self._bridge_unique_id = bridge_unique_id
        self._app_key = app_key
        self._client_key = client_key
        self._area_id = area_id
        self._session: EntertainmentSession | None = None
        self._api: HueEntertainmentAPI | None = None
        # True once Glowcast asked the bridge to start the chosen area, so
        # stop() releases it even if start() was cancelled halfway, and never
        # touches an area that another app is streaming.
        self._claimed = False
        self._app_id = ""
        self._channel_ids: dict[str, int] = {}
        self._next_status = 0.0
        self._status_failures = 0
        self._release: asyncio.Task[None] | None = None

    async def start(self) -> list[Channel]:
        """Claim the area and open the stream."""
        if self._session is not None or self._api is not None:
            await self.stop()
            await self.close()
        host = self._bridge_host()
        try:
            async with asyncio.timeout(START_TIMEOUT):
                return await self._async_start(host)
        except TimeoutError:
            raise OutputUnavailable("The Hue bridge did not respond in time") from None

    def send(self, colors: list[ChannelColor]) -> None:
        """Queue one frame of colours; colours for unknown channels are ignored."""
        session = self._session
        if session is None:
            return
        commands = [
            LightColorCommand(channel_id, *(_to_8bit(value) for value in color.rgb))
            for color in colors
            if (channel_id := self._channel_ids.get(color.channel_id)) is not None
        ]
        session.send(commands)

    async def check(self) -> None:
        """Raise OutputLost if the stream is gone or taken over."""
        session = self._session
        if session is None or not session.is_streaming:
            raise OutputLost("The Hue stream has stopped")
        now = asyncio.get_running_loop().time()
        if now < self._next_status:
            return
        self._next_status = now + STATUS_INTERVAL
        try:
            async with asyncio.timeout(STATUS_TIMEOUT):
                status, streamer = await session.remote_status()
        except aiohttp.ClientResponseError as err:
            if err.status in (401, 403):
                raise OutputAuthFailed(
                    "The Hue bridge rejected Glowcast's key"
                ) from None
            self._status_failed()
            return
        except (aiohttp.ClientError, OSError):
            self._status_failed()
            return
        self._status_failures = 0
        if status != "active" or streamer != self._app_id:
            raise OutputLost("The Hue stream ended or another app took over")

    async def stop(self) -> None:
        """Release the area; shielded, so a cancelled caller cannot skip it."""
        if self._release is None or self._release.done():
            self._release = asyncio.ensure_future(self._async_release())
        await asyncio.shield(self._release)

    async def close(self) -> None:
        """Close the library's own HTTP sessions."""
        session, self._session = self._session, None
        api, self._api = self._api, None
        if session is not None:
            await session.aclose()
        if api is not None:
            await api.close()

    def _bridge_host(self) -> str:
        """Return the bridge's current host from HA's hue entry."""
        entry = _loaded_bridge(self._hass, self._bridge_unique_id)
        if entry is None:
            raise OutputUnavailable(
                "The Hue bridge is not set up in the Philips Hue integration "
                "(or not loaded)"
            )
        if entry.data.get(CONF_API_VERSION) != 2:
            raise OutputUnavailable("This Hue bridge cannot stream (API version 1)")
        return entry.data[CONF_HOST]

    async def _async_start(self, host: str) -> list[Channel]:
        app_id = await self._async_application_id(host)
        areas = (await self._async_get(host, AREAS_PATH))[1].get("data", [])
        area = next((a for a in areas if a.get("id") == self._area_id), None)
        if area is None:
            raise OutputNotFound("The entertainment area no longer exists")
        channels = area.get("channels", [])
        if len(channels) > MAX_CHANNELS:
            raise OutputTooManyChannels(
                f"The entertainment area has {len(channels)} channels; "
                f"Hue streams at most {MAX_CHANNELS}"
            )

        self._api = HueEntertainmentAPI(host, self._app_key)
        # A bridge streams one area at a time, so any active area blocks us.
        for other in areas:
            if other.get("status") != "active":
                continue
            streamer = (other.get("active_streamer") or {}).get("rid")
            if streamer != app_id:
                raise OutputBusy("Another app is streaming on the Hue bridge")
            _LOGGER.info("Stopping a stale Glowcast stream on the Hue bridge")
            await self._api.stop_entertainment(other["id"])

        self._session = EntertainmentSession(
            host, self._app_key, self._client_key, idle_timeout=0
        )
        self._claimed = True
        try:
            await self._session.start(self._area_id, stop_others=False)
        except aiohttp.ClientResponseError as err:
            raise _response_error(err) from None
        except (aiohttp.ClientError, OSError, ValueError):
            raise OutputUnavailable("Could not open the Hue stream") from None

        self._app_id = app_id
        self._status_failures = 0
        self._next_status = asyncio.get_running_loop().time() + STATUS_INTERVAL
        self._channel_ids = {str(c["channel_id"]): c["channel_id"] for c in channels}
        return [
            Channel(
                id=str(c["channel_id"]),
                name=f"Channel {c['channel_id']}",
                x=c.get("position", {}).get("x", 0.0),
                z=c.get("position", {}).get("z", 0.0),
            )
            for c in channels
        ]

    async def _async_application_id(self, host: str) -> str:
        """Glowcast's own application id: the bridge names it as the streamer."""
        headers, _ = await self._async_get(host, AUTH_PATH, body=False)
        if not (app_id := headers.get("hue-application-id")):
            raise OutputUnavailable("The Hue bridge did not return an application id")
        return app_id

    async def _async_get(
        self, host: str, path: str, *, body: bool = True
    ) -> tuple[Any, Any]:
        return await _async_clip_get(self._hass, host, self._app_key, path, body=body)

    async def _async_release(self) -> None:
        try:
            async with asyncio.timeout(STOP_TIMEOUT):
                if self._session is not None:
                    await self._session.stop()
                if self._claimed and self._api is not None:
                    await self._api.stop_entertainment(self._area_id)
                    self._claimed = False
        except TimeoutError:
            _LOGGER.warning("The Hue bridge did not confirm the stop in time")

    def _status_failed(self) -> None:
        self._status_failures += 1
        if self._status_failures >= 2:
            raise OutputLost("The Hue bridge stopped answering")


class PairingFailed(Exception):
    """The bridge did not hand out a key (link button not pressed in time)."""


def bridges(hass: HomeAssistant) -> dict[str, str]:
    """Return {unique id: title} of loaded hue entries that can stream (API v2)."""
    return {
        entry.unique_id: entry.title
        for entry in hass.config_entries.async_loaded_entries(HUE_DOMAIN)
        if entry.unique_id is not None and entry.data.get(CONF_API_VERSION) == 2
    }


def bridge_host(hass: HomeAssistant, bridge_unique_id: str) -> str | None:
    """Return the host of a loaded hue entry, or None if it is not loaded."""
    entry = _loaded_bridge(hass, bridge_unique_id)
    return None if entry is None else entry.data[CONF_HOST]


def _loaded_bridge(hass: HomeAssistant, bridge_unique_id: str) -> ConfigEntry | None:
    """Return the hue entry with this unique id if it is loaded."""
    entry = hass.config_entries.async_entry_for_domain_unique_id(
        HUE_DOMAIN, bridge_unique_id
    )
    if entry is None or entry.state is not ConfigEntryState.LOADED:
        return None
    return entry


async def async_bridge_reachable(hass: HomeAssistant, host: str) -> bool:
    """Return whether the bridge answers an unauthenticated request.

    pair() raises the same TimeoutError for every failure, so this tells an
    unreachable bridge apart from a link button that was not pressed.
    """
    session = async_get_clientsession(hass)
    try:
        async with asyncio.timeout(REACHABLE_TIMEOUT):
            async with session.get(f"https://{host}/api/config", ssl=False) as response:
                response.raise_for_status()
    except (TimeoutError, aiohttp.ClientError, OSError):
        return False
    return True


async def async_pair(host: str, name: str) -> tuple[str, str]:
    """Pair with the bridge; return (app_key, client_key).

    Takes up to 30 s while the bridge waits for its link button. Raises
    PairingFailed, or OutputUnavailable when the bridge drops out.
    """
    api = HueEntertainmentAPI(host)
    try:
        result = await api.pair(device_type=_device_type(name))
        return result["username"], result["clientkey"]
    except TimeoutError:
        raise PairingFailed from None
    except (aiohttp.ClientError, OSError, KeyError, TypeError, ValueError):
        # ValueError: a reply that is not valid JSON.
        raise OutputUnavailable("Could not pair with the Hue bridge") from None
    finally:
        await api.close()


async def async_areas(hass: HomeAssistant, host: str, app_key: str) -> dict[str, str]:
    """Return {area id: name} of the bridge's entertainment areas.

    One raw request: the library's area listing also fetches every device and
    light to name the channels. Raises OutputAuthFailed or OutputUnavailable.
    """
    try:
        async with asyncio.timeout(AREAS_TIMEOUT):
            _, result = await _async_clip_get(hass, host, app_key, AREAS_PATH)
    except TimeoutError:
        raise OutputUnavailable("The Hue bridge did not respond in time") from None
    except OutputNotFound:
        raise OutputUnavailable("The Hue bridge has no entertainment API") from None
    return {
        area["id"]: area.get("metadata", {}).get("name") or area["id"]
        for area in result.get("data", [])
    }


async def _async_clip_get(
    hass: HomeAssistant, host: str, app_key: str, path: str, *, body: bool = True
) -> tuple[Any, Any]:
    """GET a CLIP resource with HA's session (self-signed certificate)."""
    session = async_get_clientsession(hass)
    try:
        async with session.get(
            f"https://{host}{path}",
            headers={"hue-application-key": app_key},
            ssl=False,
        ) as response:
            response.raise_for_status()
            return response.headers, await response.json() if body else None
    except aiohttp.ClientResponseError as err:
        raise _response_error(err) from None
    except (aiohttp.ClientError, OSError, ValueError):
        raise OutputUnavailable("Could not reach the Hue bridge") from None


def _device_type(name: str) -> str:
    """The app name the bridge shows: glowcast#<name>, within the bridge's limit."""
    return f"glowcast#{name.replace('#', '').strip()[:DEVICE_NAME_LENGTH]}"


def _response_error(err: aiohttp.ClientResponseError) -> Exception:
    if err.status in (401, 403):
        return OutputAuthFailed("The Hue bridge rejected Glowcast's key")
    if err.status == 404:
        return OutputNotFound("The entertainment area no longer exists")
    return OutputUnavailable(f"The Hue bridge answered with HTTP {err.status}")


def _to_8bit(value: float) -> int:
    """Convert 0.0-1.0 to 0-255.

    Never send 16-bit values: hue-entertainment 0.1.2 shifts values above 255
    down but sends 255 or less as-is, so dark 16-bit values would flash bright.
    Upstream report: https://github.com/music-assistant/hue-entertainment/issues/5
    """
    return min(255, max(0, round(value * 255)))
