"""Frame source for LG webOS TVs, reusing Home Assistant's webostv pairing."""

import asyncio
import dataclasses
import io
import logging
import math
import statistics
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

import aiohttp
from aiowebostv import WebOsClient, WebOsTvState
from aiowebostv.exceptions import (
    WebOsTvError,
    WebOsTvPairError,
    WebOsTvResponseTypeError,
    WebOsTvServiceNotFoundError,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_CLIENT_SECRET, CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from PIL import Image

from ..const import CONF_SOURCE, CONF_UNIQUE_ID, HARDWARE_REPORT_URL
from ..core.color_engine import is_all_zero
from ..core.models import Frame
from ..core.pipeline import StopReason
from ..core.ports import SourceNotSupported, SourceUnavailable
from ..devices import entry_devices
from ..issues import Issue

_LOGGER = logging.getLogger(__name__)

WEBOSTV_DOMAIN = "webostv"

# The source type (architecture 5).
SOURCE_WEBOS = "webos"

# Repair issue (architecture 4.4); the key is the one v0.1 raised.
MISSING = Issue(
    "source_missing",
    "webos_missing",
    "the TV is not set up (or not loaded) in Home Assistant",
)

# Where a user whose TV refuses the capture can report it, and read more (#214).
TROUBLESHOOTING_URL = (
    "https://nicohabets.github.io/glowcast/docs/troubleshooting/"
    "#the-tv-does-not-allow-screenshots"
)
_REFUSED_PLACEHOLDERS = {
    "report_url": HARDWARE_REPORT_URL,
    "help_url": TROUBLESHOOTING_URL,
}
# Repair issues when the TV refuses the screenshot call (#214); never rename
# the keys.
NOT_SUPPORTED = Issue(
    "webos_not_supported",
    "webos_not_supported",
    "the TV does not offer the screenshot call Glowcast uses",
    _REFUSED_PLACEHOLDERS,
)
NOT_PERMITTED = Issue(
    "webos_not_permitted",
    "webos_not_permitted",
    "the TV does not permit the screenshot call Glowcast uses",
    _REFUSED_PLACEHOLDERS,
)
STOP_ISSUES = {
    StopReason.SOURCE_NOT_SUPPORTED: NOT_SUPPORTED,
    StopReason.SOURCE_NOT_PERMITTED: NOT_PERMITTED,
}
# aiowebostv's error text when the TV refuses a call for lack of permission.
INSUFFICIENT_PERMISSIONS = "401 insufficient permissions"

FRAME_SIZE = (64, 36)
# aiowebostv waits up to 10-20 s for single replies; the pipeline's startup
# grace is 10 s, so bound the whole connect.
START_TIMEOUT = 8.0
RECONNECT_TIMEOUT = 8.0
CAPTURE_TIMEOUT = 5.0

# The hardware check (#115): at most this many captures, started within this
# window; it ends after this many failed captures in a row. With the connect
# (START_TIMEOUT) and one capture (CAPTURE_TIMEOUT) it stays within 25 s.
PROBE_CAPTURES = 10
PROBE_WINDOW = 12.0
PROBE_FAILURES = 3

CAPTURE_URI = "tv/executeOneShot"
# The size is what the tested G3 returns anyway (it ignores the size); an older
# capture service (webOS 4) refuses a full-screen capture without one (#221).
CAPTURE_PAYLOAD = {
    "path": "/tmp/capture.jpg",
    "method": "DISPLAY",
    "format": "JPG",
    "width": 960,
    "height": 540,
}


class WebOsFrameSource:
    """Captures the TV picture through webOS's local screenshot call."""

    # No limit of its own: Maximum is as fast as the TV answers.
    max_rate_hz = math.inf

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
        except (OSError, aiohttp.ClientError, aiohttp.WSMessageTypeError, WebOsTvError):
            # A TV in standby can accept the websocket and close it at once,
            # which aiohttp reports as WSMessageTypeError (a TypeError).
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
        frame, _ = await self._async_capture()
        return frame

    async def _async_capture(self) -> tuple[Frame, "ImageFacts"]:
        """Take a screenshot: the downscaled frame, and what the TV sent.

        Raises SourceNotSupported or SourceUnavailable as capture() does; the
        hardware check (#115) also reads the image facts.
        """
        client = self._client
        if client is None or not self.is_on():
            raise SourceUnavailable("The TV is off or not connected")

        # Reasons are set outside the except blocks so the original error, whose
        # text can contain the image URL, is not chained to SourceUnavailable.
        reason: str
        not_permitted: bool | None = None  # set when the TV refuses the call
        try:
            async with asyncio.timeout(CAPTURE_TIMEOUT):
                if client.connection is None:
                    await self._async_wait_for_reconnect(client)
                response = await client.request(CAPTURE_URI, CAPTURE_PAYLOAD)
                data = await self._async_download(response["imageUri"])
                return await self._hass.async_add_executor_job(_decode_with_facts, data)
        except TimeoutError:
            reason = "The capture timed out"
        except WebOsTvServiceNotFoundError:
            # "404 no such service or method": this TV has no such call (#214).
            reason = "The TV does not offer the screenshot call"
            not_permitted = False
        except WebOsTvResponseTypeError as err:
            if _is_insufficient_permissions(err):
                reason = "The TV does not permit the screenshot call"
                not_permitted = True
            else:
                reason = "The TV refused the capture"
        except WebOsTvError:
            reason = "The TV refused the capture"
        except (aiohttp.ClientError, KeyError, TypeError):
            reason = "Could not download the capture"
        except (OSError, ValueError):
            reason = "Could not read the capture"
        if not_permitted is not None:
            raise SourceNotSupported(reason, not_permitted=not_permitted)
        raise SourceUnavailable(reason)

    def tv_facts(self) -> dict[str, Any]:
        """What the connected TV says about itself, for the hardware check.

        Only these fields, never whole answers: they also hold the TV's
        serial number, its unique id and a MAC-like device id.
        """
        client = self._client
        if client is None:
            return {}
        info, state = client.tv_info, client.tv_state
        return {
            "model": info.system.get("modelName"),
            "product": info.software.get("product_name"),
            "firmware": _version(
                info.software.get("major_ver"), info.software.get("minor_ver")
            ),
            "webos_version": info.hello.get("deviceOSReleaseVersion"),
            "os_version": info.hello.get("deviceOSVersion"),
            "is_on": state.is_on,
            "is_screen_on": state.is_screen_on,
            # An app id or an input's app (com.webos.app.hdmi1); never the
            # labels, which the user can name.
            "app": state.current_app_id,
        }

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
            except (
                TimeoutError,
                OSError,
                aiohttp.ClientError,
                aiohttp.WSMessageTypeError,
                WebOsTvError,
            ):
                _LOGGER.warning("Lost the connection to the TV and could not reconnect")
                self._lost = True
                return
            _LOGGER.debug("Reconnected to the TV")


def create(
    hass: HomeAssistant, data: Mapping[str, Any], options: Mapping[str, Any]
) -> WebOsFrameSource:
    """Build the source from a Glowcast entry's data (architecture 5)."""
    return WebOsFrameSource(hass, data[CONF_SOURCE][CONF_UNIQUE_ID])


def is_missing(
    hass: HomeAssistant, data: Mapping[str, Any], options: Mapping[str, Any]
) -> bool:
    """Whether the referenced webostv entry is removed or not loaded."""
    entry = hass.config_entries.async_entry_for_domain_unique_id(
        WEBOSTV_DOMAIN, data[CONF_SOURCE][CONF_UNIQUE_ID]
    )
    return entry is None or entry.state is not ConfigEntryState.LOADED


async def probe(
    hass: HomeAssistant,
    data: Mapping[str, Any],
    options: Mapping[str, Any],
    *,
    measure: bool,
) -> dict[str, Any]:
    """The hardware check of the TV (#115): what it says about itself and,
    when `measure`, how its screenshot call answers.

    Its own connection, closed at the end. Not while Glowcast syncs
    (`measure` false): a second capture next to the pipeline's could make
    the pipeline's fail. No pixels, image URLs, names or ids are kept.
    """
    source = WebOsFrameSource(hass, data[CONF_SOURCE][CONF_UNIQUE_ID])
    try:
        try:
            await source.start()
        except SourceUnavailable as err:
            return {"connected": False, "error": str(err)}
        result: dict[str, Any] = {"connected": True, "tv": source.tv_facts()}
        if measure:
            result["capture"] = await _async_measure(source)
        return result
    finally:
        with suppress(Exception):
            await source.close()


async def _async_measure(source: WebOsFrameSource) -> dict[str, Any]:
    """A few captures in a row: whether the TV answers, and how fast."""
    loop = asyncio.get_running_loop()
    begin = loop.time()
    seconds: list[float] = []
    failures: dict[str, int] = {}
    refused: str | None = None
    facts: ImageFacts | None = None
    black = in_a_row = 0
    while len(seconds) < PROBE_CAPTURES and loop.time() - begin < PROBE_WINDOW:
        started = loop.time()
        try:
            frame, facts = await source._async_capture()
        except SourceNotSupported as err:
            refused = "not_permitted" if err.not_permitted else "not_supported"
            break
        except SourceUnavailable as err:
            # Glowcast's own reasons; never the library's error text.
            failures[str(err)] = failures.get(str(err), 0) + 1
            in_a_row += 1
            if in_a_row >= PROBE_FAILURES:
                break
            continue
        seconds.append(loop.time() - started)
        in_a_row = 0
        black += is_all_zero(frame)
    elapsed = loop.time() - begin
    return {
        # True: it gave a picture; False: it refused the call (#214); None:
        # not known (only other failures, for example the TV is off).
        "answered": True if seconds else False if refused else None,
        "refused": refused,
        "failures": failures,
        "image": None if facts is None else dataclasses.asdict(facts),
        "captures": len(seconds),
        "seconds_per_capture": (
            {
                "min": round(min(seconds), 3),
                "median": round(statistics.median(seconds), 3),
                "max": round(max(seconds), 3),
            }
            if seconds
            else None
        ),
        "captures_per_second": (
            round(len(seconds) / elapsed, 2) if seconds and elapsed > 0 else None
        ),
        "black_captures": black,
        "all_black": bool(seconds) and black == len(seconds),
    }


def device_identifier(data: Mapping[str, Any]) -> tuple[str, str]:
    """The TV's device in Home Assistant, to link Glowcast's device to."""
    return (WEBOSTV_DOMAIN, data[CONF_SOURCE][CONF_UNIQUE_ID])


def device_id(hass: HomeAssistant, data: Mapping[str, Any]) -> str | None:
    """The TV device's id in Home Assistant's device registry, if it exists."""
    identifier = device_identifier(data)
    devices = entry_devices(hass, WEBOSTV_DOMAIN, data[CONF_SOURCE][CONF_UNIQUE_ID])
    return next(
        (device.id for device in devices if identifier in device.identifiers), None
    )


def tvs(hass: HomeAssistant) -> dict[str, str]:
    """Return {unique id: title} of loaded webostv entries."""
    return {
        entry.unique_id: entry.title
        for entry in hass.config_entries.async_loaded_entries(WEBOSTV_DOMAIN)
        if entry.unique_id is not None
    }


def _is_insufficient_permissions(error: WebOsTvResponseTypeError) -> bool:
    """Whether the TV refused the call for lack of permission.

    aiowebostv (0.7.4 to 0.10.0) raises this error with the TV's whole error
    response as its argument; only its "error" field is read.
    """
    response = error.args[0] if error.args else None
    return (
        isinstance(response, Mapping)
        and response.get("error") == INSUFFICIENT_PERMISSIONS
    )


@dataclass(frozen=True)
class ImageFacts:
    """What the TV sent, from the image's header: never its pixels."""

    format: str | None
    width: int
    height: int
    bytes: int


def _decode(data: bytes) -> Frame:
    """Decode a JPEG and downscale it to FRAME_SIZE (runs in the executor)."""
    return _decode_with_facts(data)[0]


def _decode_with_facts(data: bytes) -> tuple[Frame, ImageFacts]:
    """Decode and downscale, and read the format and size from the header
    (runs in the executor)."""
    with Image.open(io.BytesIO(data)) as image:
        facts = ImageFacts(image.format, image.width, image.height, len(data))
        image.draft("RGB", FRAME_SIZE)
        small = image.convert("RGB").resize(FRAME_SIZE, Image.Resampling.BOX)
    frame = Frame(width=small.width, height=small.height, pixels=small.tobytes())
    return frame, facts


def _version(major: Any, minor: Any) -> str | None:
    """The firmware version as Home Assistant's webostv integration shows it."""
    return f"{major}.{minor}" if major and minor else None
