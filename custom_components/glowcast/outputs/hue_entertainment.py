"""Light output that streams to a Hue entertainment area (architecture 3.4)."""

import asyncio
import logging
from collections import Counter
from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Any

import aiohttp
from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_API_KEY, CONF_API_VERSION, CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.translation import async_get_translations
from homeassistant.util import color as color_util
from hue_entertainment import EntertainmentSession, HueEntertainmentAPI
from hue_entertainment.models import LightColorCommand

from ..const import CONF_OUTPUT, CONF_UNIQUE_ID, DOMAIN
from ..core.models import Channel, ChannelColor
from ..core.ports import (
    OutputAuthFailed,
    OutputBusy,
    OutputLost,
    OutputNotFound,
    OutputTooManyChannels,
    OutputUnavailable,
)
from ..devices import entry_devices
from ..issues import Issue
from .colors import at_level, average

_LOGGER = logging.getLogger(__name__)

HUE_DOMAIN = "hue"

# The output type and its keys in the entry (architecture 5): the bridge it
# paired with in `data` (only once paired), the area in `options["output"]`.
OUTPUT_HUE_ENTERTAINMENT = "hue_entertainment"
CONF_BRIDGE = "bridge"
CONF_APP_KEY = "app_key"
CONF_CLIENT_KEY = "client_key"
CONF_AREA_ID = "area_id"

# Repair issues (architecture 4.4); the keys are the ones v0.1 raised.
ISSUE_OUTPUT_MISSING = "output_missing"
ISSUE_AREA_NOT_FOUND = "area_not_found"
ISSUE_TOO_MANY_CHANNELS = "too_many_channels"
MISSING = Issue(
    ISSUE_OUTPUT_MISSING,
    "hue_entertainment_missing",
    "the bridge is not set up (or not loaded) in Home Assistant",
)
AREA_NOT_FOUND = Issue(
    ISSUE_AREA_NOT_FOUND,
    "hue_entertainment_area_not_found",
    "the chosen entertainment area no longer exists",
)
TOO_MANY_CHANNELS = Issue(
    ISSUE_TOO_MANY_CHANNELS,
    "hue_entertainment_too_many_channels",
    "the chosen entertainment area has more than 20 channels",
)
# Shown once when an entry that paired with a bridge is removed. Its text
# lives under "issues" because Home Assistant has no translation category for
# notifications; it is never raised as a repair issue.
NOTICE_BRIDGE_APP_LEFT = "bridge_app_left"
NOTICE_TRANSLATION_KEY = "hue_entertainment_bridge_app_left"
AREAS_PATH = "/clip/v2/resource/entertainment_configuration"
LIGHTS_PATH = "/clip/v2/resource/light"
ENTERTAINMENT_PATH = "/clip/v2/resource/entertainment"
AUTH_PATH = "/auth/v1"
DEVICES_PATH = "/clip/v2/resource/device"
CONFIG_PATH = "/api/config"

MAX_CHANNELS = 20  # HueStream limit per message
# The DTLS handshake alone can block for about 23 s against an unreachable bridge.
START_TIMEOUT = 30.0
STOP_TIMEOUT = 10.0
STATUS_INTERVAL = 5.0
STATUS_TIMEOUT = 4.0
REACHABLE_TIMEOUT = 10.0
AREAS_TIMEOUT = 15.0
# The hardware check (#115): every request to the bridge together, within
# the 25 s the check gives one type's probe (probes.py).
BRIDGE_PROBE_TIMEOUT = 15.0
# What the bridge's unauthenticated config tells about its hardware and
# software; never its name, MAC address or bridge id.
BRIDGE_FIELDS = ("modelid", "swversion", "apiversion", "datastoreversion")
# The check's own text for a 404 (a bridge without what it reads), not the
# start's "The entertainment area no longer exists".
PROBE_NOT_FOUND = "The Hue bridge does not have what the check asks for (HTTP 404)"
# Reading what the lights show before the stream (for the fade-in, #153) may
# not hold up the start for long; without it the fade-in starts from dark.
STATES_TIMEOUT = 5.0
# A light with only a colour temperature (no xy) starts from the sRGB colour
# of a temperature this much cooler: the bridge shows sRGB warm whites
# redder, which is why the glow is two steps cooler too (architecture 3.5, #38).
CT_CORRECTION_K = 800
# The bridge allows 20 characters before the # of a devicetype and 19 after.
DEVICE_NAME_LENGTH = 19


class HueEntertainmentOutput:
    """Streams colours to one Hue entertainment area."""

    max_rate_hz = 25.0
    dropped_parts: Mapping[str, Exception] = MappingProxyType({})  # one part

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

    async def start(self, *, retry: bool = False) -> list[Channel]:
        """Claim the area and open the stream.

        Not on a retry: read what the lights show before (for the fade-in).
        """
        if self._session is not None or self._api is not None:
            await self.stop()
            await self.close()
        host = self._bridge_host()
        try:
            async with asyncio.timeout(START_TIMEOUT):
                return await self._async_start(host, retry)
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

    async def _async_start(self, host: str, retry: bool) -> list[Channel]:
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

        starts = {} if retry else await self._async_start_colors(host, channels)
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
                start_rgb=starts.get(c["channel_id"]),
                y=c.get("position", {}).get("y"),
                light=_light_of(c),
            )
            for c in channels
        ]

    async def _async_start_colors(
        self, host: str, channels: list[dict[str, Any]]
    ) -> dict[int, tuple[float, float, float]]:
        """What each channel's light shows now, read before the stream starts.

        Read-only, from the bridge's light resources. Empty when the bridge
        does not answer in time or at all: the fade-in then starts from dark.
        """
        try:
            async with asyncio.timeout(STATES_TIMEOUT):
                renderers = await _async_clip_data(
                    self._hass, host, self._app_key, ENTERTAINMENT_PATH
                )
                lights = await _async_clip_data(
                    self._hass, host, self._app_key, LIGHTS_PATH
                )
            return _channel_start_colors(channels, renderers, lights)
        except (
            TimeoutError,
            OutputAuthFailed,
            OutputNotFound,
            OutputUnavailable,
            TypeError,
            ValueError,
            KeyError,
        ):
            _LOGGER.debug("Could not read the lights' state; they fade in from dark")
            return {}

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


def create(
    hass: HomeAssistant, data: Mapping[str, Any], options: Mapping[str, Any]
) -> HueEntertainmentOutput:
    """Build the output from a Glowcast entry's data and options (architecture 5)."""
    bridge = data[CONF_BRIDGE]
    return HueEntertainmentOutput(
        hass,
        bridge[CONF_UNIQUE_ID],
        bridge[CONF_APP_KEY],
        bridge[CONF_CLIENT_KEY],
        options[CONF_OUTPUT][CONF_AREA_ID],
    )


def is_missing(
    hass: HomeAssistant, data: Mapping[str, Any], options: Mapping[str, Any]
) -> bool:
    """Whether the referenced hue entry is removed, not loaded or cannot stream."""
    entry = _loaded_bridge(hass, data[CONF_BRIDGE][CONF_UNIQUE_ID])
    return entry is None or entry.data.get(CONF_API_VERSION) != 2


def device_id(hass: HomeAssistant, data: Mapping[str, Any]) -> str | None:
    """The bridge's device id in Home Assistant's device registry, if it exists.

    The hue integration gives the bridge's device the bridge id as the bridge
    reports it (16 characters on a v2 bridge, with `fffe` in the middle) and
    normalises it into the entry's unique id (12 characters), so both are
    compared normalised. If no identifier matches, the bridge is the device
    that most of the entry's other devices are linked through: the hue
    integration links every light, sensor, room and zone through it. Not "the
    one device without a `via_device_id`": on Home Assistant 2026.10 the
    bridge may have one too (#163).
    """
    bridge_id = _normalize_bridge_id(data[CONF_BRIDGE][CONF_UNIQUE_ID])
    devices = entry_devices(hass, HUE_DOMAIN, data[CONF_BRIDGE][CONF_UNIQUE_ID])
    for device in devices:
        if any(
            domain == HUE_DOMAIN and _normalize_bridge_id(identifier) == bridge_id
            for domain, identifier in device.identifiers
        ):
            return device.id
    own = {device.id for device in devices}
    parents = Counter(
        device.via_device_id
        for device in devices
        if device.via_device_id in own and device.via_device_id != device.id
    ).most_common(2)
    if not parents or (len(parents) == 2 and parents[0][1] == parents[1][1]):
        return None
    return parents[0][0]


def _normalize_bridge_id(bridge_id: str) -> str:
    """The bridge id as the hue integration makes its entry's unique id from it.

    The same as `normalize_bridge_id` in `aiohue` (the hue integration's
    library, not one of Glowcast's requirements): lowercase, without colons,
    and without the `fffe` that the bridge's API puts in the middle.
    """
    bridge_id = bridge_id.lower()
    if len(bridge_id) == 17 and bridge_id.count(":") == 5:
        return bridge_id.replace(":", "")
    if len(bridge_id) == 16 and bridge_id[6:10] == "fffe":
        return bridge_id[0:6] + bridge_id[-6:]
    return bridge_id


async def async_removed(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Tell the user that Glowcast's app stays on the bridge it paired with.

    The bridge has no API to remove an app (architecture 4.1); the user
    removes it in the Hue app. Text in Home Assistant's server language.
    """
    if CONF_BRIDGE not in entry.data:
        return
    strings = await async_get_translations(
        hass, hass.config.language, "issues", {DOMAIN}
    )
    prefix = f"component.{DOMAIN}.issues.{NOTICE_TRANSLATION_KEY}"
    persistent_notification.async_create(
        hass,
        strings[f"{prefix}.description"],
        title=strings[f"{prefix}.title"].replace("{name}", entry.title),
        notification_id=f"{DOMAIN}_{NOTICE_BRIDGE_APP_LEFT}_{entry.entry_id}",
    )


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


def signal_areas_read(entry_id: str) -> str:
    """The signal the options screen sends with the areas it read, so the
    entertainment-area entity shows the same list."""
    return f"glowcast_hue_areas_{entry_id}"


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


# For Home Assistant lights (#9): whether picked Hue lights could stream instead.
HINT_AREA = "area"  # they can join an entertainment area, and the bridge has one
HINT_NO_AREA = "no_area"  # they can join one, but the bridge has none yet
HINT_TIMEOUT = 10.0
# What asking a bridge through Home Assistant's hue entry can raise: then the
# bridge is left out, and nothing is refused or hinted for its lights.
_ASK_ERRORS = (TimeoutError, OutputAuthFailed, OutputNotFound, OutputUnavailable)


def _hue_bridges(
    hass: HomeAssistant, entity_ids: Iterable[str]
) -> list[tuple[ConfigEntry, dict[str, str]]]:
    """The loaded hue entries (API v2) that have some of these lights, each
    with {light unique id: entity id} of its lights among them; lights of
    other integrations are left out."""
    registry = er.async_get(hass)
    per_entry: dict[str, dict[str, str]] = {}
    for entity_id in entity_ids:
        entry = registry.async_get(entity_id)
        if entry is not None and entry.platform == HUE_DOMAIN and entry.config_entry_id:
            per_entry.setdefault(entry.config_entry_id, {})[entry.unique_id] = entity_id
    bridges: list[tuple[ConfigEntry, dict[str, str]]] = []
    for entry_id, lights in per_entry.items():
        hue_entry = hass.config_entries.async_get_entry(entry_id)
        if (
            hue_entry is not None
            and hue_entry.state is ConfigEntryState.LOADED
            and hue_entry.data.get(CONF_API_VERSION) == 2
        ):
            bridges.append((hue_entry, lights))
    return bridges


def _bridge_access(hue_entry: ConfigEntry) -> tuple[str, str]:
    """The host and key of Home Assistant's hue entry, read at runtime and
    never stored."""
    return hue_entry.data[CONF_HOST], hue_entry.data.get(CONF_API_KEY, "")


async def async_entertainment_hint(
    hass: HomeAssistant, entity_ids: Iterable[str]
) -> str | None:
    """Whether some of these lights are Hue lights that can join an area.

    HINT_AREA or HINT_NO_AREA if so, None if none of them can (other lights,
    older Hue lights or other brands' lights on a bridge), or when the bridge
    cannot be asked. Asks each bridge through Home Assistant's own hue entry
    (its host and key, read at runtime, never stored): the bridge lists which
    devices can render entertainment, and its areas.
    """
    hint: str | None = None
    for hue_entry, light_ids in _hue_bridges(hass, entity_ids):
        host, key = _bridge_access(hue_entry)
        try:
            async with asyncio.timeout(HINT_TIMEOUT):
                lights = await _async_clip_data(hass, host, key, LIGHTS_PATH)
                renderers = await _async_clip_data(hass, host, key, ENTERTAINMENT_PATH)
                areas = await _async_clip_data(hass, host, key, AREAS_PATH)
        except _ASK_ERRORS:
            continue
        if any(
            light.get("id") in light_ids
            for renderer, found in _renderer_lights(renderers, lights)
            if renderer.get("renderer")
            for light in found
        ):
            if areas:
                return HINT_AREA
            hint = HINT_NO_AREA
    return hint


async def async_area_lights(
    hass: HomeAssistant, data: Mapping[str, Any], output: Mapping[str, Any]
) -> set[str]:
    """The entity ids of Home Assistant's lights in the chosen entertainment area.

    For a combined output (#11): these lights follow the TV through the area,
    so they cannot also be Home Assistant lights. `data` has the `bridge` block and
    `output` this type's block. Asks the bridge through Home Assistant's own
    hue entry (its host and key, read at runtime, never stored): the area's
    lights, and the lights of the devices its channels stream to. Empty when
    the bridge cannot be asked; nothing is refused then.
    """
    bridge, area_id = data.get(CONF_BRIDGE), output.get(CONF_AREA_ID)
    if not bridge or not area_id:
        return set()
    hue_entry = _loaded_bridge(hass, bridge[CONF_UNIQUE_ID])
    if hue_entry is None:
        return set()
    host, key = _bridge_access(hue_entry)
    try:
        async with asyncio.timeout(HINT_TIMEOUT):
            areas = await _async_clip_data(hass, host, key, AREAS_PATH)
            area = next((item for item in areas if item.get("id") == area_id), None)
            if area is None:
                return set()
            renderers = await _async_clip_data(hass, host, key, ENTERTAINMENT_PATH)
            lights = await _async_clip_data(hass, host, key, LIGHTS_PATH)
    except _ASK_ERRORS:
        return set()
    light_ids = _area_light_ids(area, renderers, lights)
    registry = er.async_get(hass)
    return {
        entry.entity_id
        for entry in er.async_entries_for_config_entry(registry, hue_entry.entry_id)
        if entry.domain == "light" and entry.unique_id in light_ids
    }


async def async_streamed_lights(
    hass: HomeAssistant,
    entity_ids: Iterable[str],
    own_block: Mapping[str, Any] | None,
) -> dict[str, str]:
    """{entity id: area name} of these lights that are in an entertainment
    area another app streams now, for example a Hue Sync Box (#219).

    While an area streams, its bridge ignores other commands to its lights,
    so they do not follow Home Assistant lights. `own_block` is this type's
    block of the same entry (an entry with both, #11), handed over by the
    Home Assistant lights: its area is Glowcast's own and is left out.
    Only Hue lights of Home Assistant's own hue entries can be checked: each
    bridge is asked through its entry (its host and key, read at runtime,
    never stored), with GET requests only. Empty when no bridge can be asked.
    """
    own_area_id = None if own_block is None else own_block.get(CONF_AREA_ID)
    streamed: dict[str, str] = {}
    for hue_entry, entities in _hue_bridges(hass, entity_ids):
        host, key = _bridge_access(hue_entry)
        try:
            async with asyncio.timeout(HINT_TIMEOUT):
                areas = [
                    area
                    for area in await _async_clip_data(hass, host, key, AREAS_PATH)
                    if area.get("status") == "active" and area.get("id") != own_area_id
                ]
                if not areas:
                    continue
                renderers = await _async_clip_data(hass, host, key, ENTERTAINMENT_PATH)
                lights = await _async_clip_data(hass, host, key, LIGHTS_PATH)
        except _ASK_ERRORS:
            continue
        for area in areas:
            name = (area.get("metadata") or {}).get("name") or str(area.get("id"))
            for light_id in _area_light_ids(area, renderers, lights):
                if light_id in entities:
                    streamed.setdefault(entities[light_id], name)
    return streamed


def _area_light_ids(
    area: dict[str, Any],
    renderers: list[dict[str, Any]],
    lights: list[dict[str, Any]],
) -> set[Any]:
    """The bridge's ids of the lights an entertainment area streams to: its
    lights, and the lights of the devices its channels stream to."""
    light_ids = {
        service.get("rid")
        for service in area.get("light_services") or []
        if isinstance(service, dict)
    }
    services = {
        (member.get("service") or {}).get("rid")
        for channel in area.get("channels") or []
        if isinstance(channel, dict)
        for member in channel.get("members") or []
        if isinstance(member, dict)
    }
    return light_ids | {
        light.get("id")
        for renderer, found in _renderer_lights(renderers, lights)
        if renderer.get("id") in services
        for light in found
    }


async def _async_clip_data(
    hass: HomeAssistant, host: str, key: str, path: str
) -> list[dict[str, Any]]:
    _, result = await _async_clip_get(hass, host, key, path)
    data = result.get("data", []) if isinstance(result, dict) else []
    return [item for item in data if isinstance(item, dict)]


def _channel_start_colors(
    channels: list[dict[str, Any]],
    renderers: list[dict[str, Any]],
    lights: list[dict[str, Any]],
) -> dict[int, tuple[float, float, float]]:
    """{channel id: what its light shows} for the fade-in (#153).

    A channel streams to the entertainment services of its members; each one
    belongs to a device with a light. Every segment of a gradient light gets
    the light's own colour (its main colour, not the gradient's points); a
    channel of several lights gets their average. Channels whose light is not
    found are left out.
    """
    lights_of = {
        renderer.get("id"): found
        for renderer, found in _renderer_lights(renderers, lights)
    }
    starts: dict[int, tuple[float, float, float]] = {}
    for channel in channels:
        colors = [
            rgb
            for member in channel.get("members") or []
            if isinstance(member, dict)
            # A device with several lights: its last one, as before (#199).
            and (found := lights_of.get(_service(member)))
            and (rgb := _safe_light_rgb(found[-1])) is not None
        ]
        if (color := average(colors)) is not None:
            starts[channel["channel_id"]] = color
    return starts


def _renderer_lights(
    renderers: list[dict[str, Any]], lights: list[dict[str, Any]]
) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    """Each entertainment service with the lights of its device, in the
    bridge's order: an entertainment service belongs to a device, as its
    lights do."""
    of_device: dict[Any, list[dict[str, Any]]] = {}
    for light in lights:
        of_device.setdefault((light.get("owner") or {}).get("rid"), []).append(light)
    return [
        (item, of_device.get((item.get("owner") or {}).get("rid"), []))
        for item in renderers
    ]


def _service(member: dict[str, Any]) -> str | None:
    return (member.get("service") or {}).get("rid")


def _safe_light_rgb(light: dict[str, Any]) -> tuple[float, float, float] | None:
    """_light_rgb(), or None (unknown) for a resource it cannot read."""
    try:
        return _light_rgb(light)
    except (TypeError, ValueError, KeyError, ZeroDivisionError):
        return None


def _light_rgb(light: dict[str, Any]) -> tuple[float, float, float] | None:
    """A light resource's state as a colour in 0-1 as streamed, its brightness
    included: off is black, on without a colour is white.

    The colour comes from `color.xy`, which a colour light reports in
    colour-temperature mode too, with the same conversion for both modes;
    only a light without xy uses its temperature (CT_CORRECTION_K). How the
    bridge renders a streamed colour may still differ a little."""
    on = (light.get("on") or {}).get("on")
    if on is None:
        return None
    if not on:
        return 0.0, 0.0, 0.0
    temperature = light.get("color_temperature") or {}
    xy = (light.get("color") or {}).get("xy") or {}
    rgb: tuple[float, ...] = (255.0, 255.0, 255.0)
    if "x" in xy and "y" in xy:
        rgb = color_util.color_xy_to_RGB(float(xy["x"]), float(xy["y"]))
    elif temperature.get("mirek_valid") and temperature.get("mirek"):
        kelvin = color_util.color_temperature_mired_to_kelvin(temperature["mirek"])
        rgb = color_util.color_temperature_to_rgb(kelvin + CT_CORRECTION_K)
    level = float((light.get("dimming") or {}).get("brightness", 100.0)) / 100
    return at_level(rgb, level)


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


def _light_of(channel: dict[str, Any]) -> str | None:
    """The light a channel is a segment of: its one member's entertainment
    service, which every segment of a gradient light shares; None for a
    channel of several lights."""
    members = channel.get("members") or []
    if len(members) != 1:
        return None
    return (members[0].get("service") or {}).get("rid")


async def probe(
    hass: HomeAssistant,
    data: Mapping[str, Any],
    options: Mapping[str, Any],
    *,
    measure: bool,
) -> dict[str, Any]:
    """The hardware check of the bridge and the chosen area (#115).

    Read-only: GET requests only, so no stream starts, no light changes and a
    stale Glowcast stream is left alone; the same while Glowcast syncs. Only
    hardware, software and the area's layout: no names, ids or addresses.
    """
    bridge = data[CONF_BRIDGE]
    entry = _loaded_bridge(hass, bridge[CONF_UNIQUE_ID])
    if entry is None:
        return {"error": "The Hue bridge is not set up (or not loaded)"}
    host, key = entry.data[CONF_HOST], bridge[CONF_APP_KEY]
    result: dict[str, Any] = {"api_version": entry.data.get(CONF_API_VERSION)}
    try:
        async with asyncio.timeout(BRIDGE_PROBE_TIMEOUT):
            result["bridge"] = await _async_bridge_facts(hass, host)
            headers, _ = await _async_clip_get(hass, host, key, AUTH_PATH, body=False)
            areas = await _async_clip_data(hass, host, key, AREAS_PATH)
            renderers = await _async_clip_data(hass, host, key, ENTERTAINMENT_PATH)
            devices = await _async_clip_data(hass, host, key, DEVICES_PATH)
    except TimeoutError:
        result["error"] = "The Hue bridge did not respond in time"
        return result
    except OutputNotFound:
        result["error"] = PROBE_NOT_FOUND
        return result
    except (OutputAuthFailed, OutputUnavailable) as err:
        # Glowcast's own texts, without the address.
        result["error"] = str(err)
        return result
    app_id = headers.get("hue-application-id")
    result["areas"] = len(areas)
    result["streaming"] = _streamer(
        next((area for area in areas if area.get("status") == "active"), None),
        app_id,
    )
    area = next(
        (a for a in areas if a.get("id") == options[CONF_OUTPUT].get(CONF_AREA_ID)),
        None,
    )
    result["area"] = None if area is None else _area_facts(area, renderers, devices)
    if area is not None:
        result["area"]["stream"] = _streamer(
            area if area.get("status") == "active" else None, app_id
        )
    return result


async def _async_bridge_facts(hass: HomeAssistant, host: str) -> dict[str, Any]:
    """The bridge's model and versions from its unauthenticated config."""
    try:
        _, config = await _async_clip_get(hass, host, "", CONFIG_PATH)
    except OutputNotFound:
        return {"error": PROBE_NOT_FOUND}
    except (OutputAuthFailed, OutputUnavailable) as err:
        return {"error": str(err)}
    if not isinstance(config, dict):
        return {"error": "The Hue bridge sent no config"}
    return {field: config.get(field) for field in BRIDGE_FIELDS}


def _streamer(area: dict[str, Any] | None, app_id: str | None) -> str:
    """Who streams this area: "none", "glowcast" or "other_app" (never which)."""
    if area is None:
        return "none"
    streamer = (area.get("active_streamer") or {}).get("rid")
    return "glowcast" if app_id and streamer == app_id else "other_app"


def _area_facts(
    area: dict[str, Any],
    renderers: list[dict[str, Any]],
    devices: list[dict[str, Any]],
) -> dict[str, Any]:
    """The area's layout: each channel's position and, per member, the
    device's product (model id and archetype) and its segment."""
    owner = {
        renderer.get("id"): (renderer.get("owner") or {}).get("rid")
        for renderer in renderers
    }
    products = {
        device.get("id"): device.get("product_data") or {} for device in devices
    }
    channels = [c for c in area.get("channels") or [] if isinstance(c, dict)]
    return {
        "configuration_type": area.get("configuration_type"),
        "channel_count": len(channels),
        "channels": [
            {
                "channel": channel.get("channel_id"),
                "position": {
                    axis: (channel.get("position") or {}).get(axis)
                    for axis in ("x", "y", "z")
                },
                "members": [
                    {
                        "segment": member.get("index"),
                        "model_id": (
                            product := products.get(owner.get(_service(member)), {})
                        ).get("model_id"),
                        "archetype": product.get("product_archetype"),
                    }
                    for member in channel.get("members") or []
                    if isinstance(member, dict)
                ],
            }
            for channel in channels
        ],
    }
