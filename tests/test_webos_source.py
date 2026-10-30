"""Tests for the webOS frame source, with a fake aiowebostv client."""

import asyncio
import io
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import suppress
from typing import Any

import aiohttp
import pytest
from aiowebostv import WebOsClient, WebOsTvInfo, WebOsTvState
from aiowebostv.exceptions import (
    WebOsTvCommandError,
    WebOsTvPairError,
    WebOsTvResponseTypeError,
    WebOsTvServiceNotFoundError,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from PIL import Image
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.glowcast.core.ports import SourceNotSupported, SourceUnavailable
from custom_components.glowcast.sources import webos
from custom_components.glowcast.sources.webos import WebOsFrameSource

UNIQUE_ID = "tv-unique-id"
HOST = "tv.local"
CLIENT_KEY = "client-key"
IMAGE_URL = "https://tv.local:3001/resources/secret-hash/capture.jpg"

POWER_ON = {"state": "Active"}
POWER_SCREEN_OFF = {"state": "Screen Off"}
POWER_STANDBY = {"state": "Active Standby"}

HANG = object()


class FakeWebOsClient:
    """Behaves like aiowebostv.WebOsClient for the calls the source makes.

    `connect_outcomes` scripts each connect() call: None connects, an exception
    is raised, HANG never finishes, and a Future holds the call until the test
    resolves it (with None to connect, or with an exception). `drop()` mirrors
    aiowebostv's closeout: the connection goes, the state is cleared, callbacks
    run with the cleared state, and only then does the connect task end.
    """

    instances: list["FakeWebOsClient"] = []
    connect_outcomes: list[Any] = []

    def __init__(
        self, host: str, client_key: str | None = None, client_session: Any = None
    ) -> None:
        self.host = host
        self.client_key = client_key
        self.client_session = client_session
        self.connection: object | None = None
        self.connect_task: asyncio.Task[None] | None = None
        self.tv_state = WebOsTvState()
        self.tv_info = WebOsTvInfo()
        self.callbacks: list[Callable[[WebOsTvState], Any]] = []
        self.power_state = POWER_ON
        self.request_result: Any = {"imageUri": IMAGE_URL}
        self.payloads: list[dict[str, Any]] = []
        self.connect_calls = 0
        self.disconnected = False
        self._dropped = asyncio.Event()
        FakeWebOsClient.instances.append(self)

    async def register_state_update_callback(self, callback: Callable) -> None:
        self.callbacks.append(callback)

    def unregister_state_update_callback(self, callback: Callable) -> None:
        if callback in self.callbacks:
            self.callbacks.remove(callback)

    async def connect(self) -> bool:
        self.connect_calls += 1
        outcome = self.connect_outcomes.pop(0) if self.connect_outcomes else None
        ready: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self.connect_task = asyncio.create_task(self._handler(outcome, ready))
        return await ready

    async def _handler(self, outcome: Any, ready: asyncio.Future[bool]) -> None:
        try:
            if outcome is HANG:
                await asyncio.Event().wait()
            if isinstance(outcome, asyncio.Future):
                try:
                    outcome = await outcome
                except Exception as err:  # noqa: BLE001 - becomes the outcome
                    outcome = err
            if isinstance(outcome, BaseException):
                ready.set_exception(outcome)
                return
            self._dropped = asyncio.Event()
            self.connection = object()
            await self.set_power_state(self.power_state)
            ready.set_result(True)
            await self._dropped.wait()
        finally:
            self.connection = None
            self.tv_state.clear()
            for callback in self.callbacks:
                await callback(self.tv_state)

    async def set_power_state(self, power_state: dict[str, str]) -> None:
        """Simulate a power-state update from the TV."""
        self.power_state = power_state
        self.tv_state.power_state = power_state
        self.tv_state.is_on = power_state["state"] not in ("Active Standby",)
        self.tv_state.is_screen_on = (
            self.tv_state.is_on and power_state["state"] != "Screen Off"
        )
        for callback in self.callbacks:
            await callback(self.tv_state)

    async def drop(self) -> None:
        """Simulate the websocket dropping."""
        self._dropped.set()
        await asyncio.sleep(0)

    async def request(self, uri: str, payload: dict[str, Any]) -> dict[str, Any]:
        assert uri == "tv/executeOneShot"
        assert payload["method"] == "DISPLAY"
        self.payloads.append(payload)
        if self.request_result is HANG:
            await asyncio.Event().wait()
        if isinstance(self.request_result, BaseException):
            raise self.request_result
        return self.request_result

    async def disconnect(self) -> None:
        self.disconnected = True
        if self.connect_task is not None and not self.connect_task.done():
            self.connect_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.connect_task


def _jpeg(width: int = 960, height: int = 540, *, black: bool = False) -> bytes:
    """A 960x540 JPEG: red left half, blue right half (or all black)."""
    image = Image.new("RGB", (width, height), (0, 0, 0) if black else (255, 0, 0))
    if not black:
        image.paste((0, 0, 255), (width // 2, 0, width, height))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG")
    return buffer.getvalue()


AddTvEntry = Callable[..., MockConfigEntry]


@pytest.fixture
def add_tv_entry(hass: HomeAssistant) -> Iterator[AddTvEntry]:
    """Add a webostv entry; marked not loaded afterwards so HA does not unload it."""
    entries: list[MockConfigEntry] = []

    def add(
        host: str = HOST,
        key: str = CLIENT_KEY,
        state: ConfigEntryState = ConfigEntryState.LOADED,
    ) -> MockConfigEntry:
        entry = MockConfigEntry(
            domain="webostv",
            unique_id=UNIQUE_ID,
            data={"host": host, "client_secret": key},
            state=state,
        )
        entry.add_to_hass(hass)
        entries.append(entry)
        return entry

    yield add
    for entry in entries:
        entry.mock_state(hass, ConfigEntryState.NOT_LOADED)


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


@pytest.fixture(autouse=True)
def fake_client(monkeypatch: pytest.MonkeyPatch) -> type[FakeWebOsClient]:
    FakeWebOsClient.instances = []
    FakeWebOsClient.connect_outcomes = []
    monkeypatch.setattr(webos, "WebOsClient", FakeWebOsClient)
    return FakeWebOsClient


@pytest.fixture
async def source(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> AsyncIterator[WebOsFrameSource]:
    """A source; aioclient_mock first, so HA's shared session is the mocked one."""
    source = WebOsFrameSource(hass, UNIQUE_ID)
    yield source
    await source.close()


@pytest.fixture
async def started(
    source: WebOsFrameSource, add_tv_entry: AddTvEntry
) -> tuple[WebOsFrameSource, FakeWebOsClient]:
    add_tv_entry()
    await source.start()
    return source, FakeWebOsClient.instances[-1]


# Start


async def test_start_uses_the_webostv_entry_and_ha_session(
    hass: HomeAssistant, started: tuple[WebOsFrameSource, FakeWebOsClient]
) -> None:
    source, client = started
    assert client.host == HOST
    assert client.client_key == CLIENT_KEY
    assert client.client_session is async_get_clientsession(hass)
    assert source.is_on()


async def test_start_without_entry(source: WebOsFrameSource) -> None:
    with pytest.raises(SourceUnavailable, match="LG webOS TV integration"):
        await source.start()
    assert FakeWebOsClient.instances == []


async def test_start_with_entry_not_loaded(
    source: WebOsFrameSource, add_tv_entry: AddTvEntry
) -> None:
    add_tv_entry(state=ConfigEntryState.SETUP_RETRY)
    with pytest.raises(SourceUnavailable, match="LG webOS TV integration"):
        await source.start()


async def test_start_after_tv_removed_and_re_added(
    hass: HomeAssistant, source: WebOsFrameSource, add_tv_entry: AddTvEntry
) -> None:
    old = add_tv_entry()
    await source.start()
    await source.close()
    old.mock_state(hass, ConfigEntryState.NOT_LOADED)
    await hass.config_entries.async_remove(old.entry_id)
    add_tv_entry(host="new.local", key="new-key")

    await source.start()

    client = FakeWebOsClient.instances[-1]
    assert (client.host, client.client_key) == ("new.local", "new-key")


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (TimeoutError(), "did not respond"),
        (OSError("unreachable"), "Could not connect"),
        (WebOsTvPairError("rejected"), "rejected the pairing"),
        # A TV in standby accepts the websocket and closes it with code 1008.
        (
            aiohttp.WSMessageTypeError("Received message 8:1008 is not TEXT"),
            "Could not connect",
        ),
    ],
)
async def test_start_when_tv_unreachable(
    source: WebOsFrameSource,
    add_tv_entry: AddTvEntry,
    error: Exception,
    reason: str,
) -> None:
    add_tv_entry()
    FakeWebOsClient.connect_outcomes = [error]
    with pytest.raises(SourceUnavailable, match=reason):
        await source.start()
    assert FakeWebOsClient.instances[-1].disconnected
    assert not source.is_on()


async def test_start_hangs(
    source: WebOsFrameSource,
    add_tv_entry: AddTvEntry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(webos, "START_TIMEOUT", 0.05)
    add_tv_entry()
    FakeWebOsClient.connect_outcomes = [HANG]
    with pytest.raises(SourceUnavailable, match="did not respond"):
        await source.start()
    assert FakeWebOsClient.instances[-1].disconnected


async def test_start_again_after_failure_and_after_close(
    source: WebOsFrameSource, add_tv_entry: AddTvEntry
) -> None:
    add_tv_entry()
    FakeWebOsClient.connect_outcomes = [OSError()]
    with pytest.raises(SourceUnavailable):
        await source.start()
    await source.start()
    assert source.is_on()
    await source.close()
    await source.close()
    assert not source.is_on()
    assert FakeWebOsClient.instances[-1].disconnected
    await source.start()
    assert source.is_on()


# Power state


async def test_is_on_follows_the_tv_power_state(
    started: tuple[WebOsFrameSource, FakeWebOsClient],
) -> None:
    source, client = started
    await client.set_power_state(POWER_SCREEN_OFF)
    assert not source.is_on()
    await client.set_power_state(POWER_ON)
    assert source.is_on()
    await client.set_power_state(POWER_STANDBY)
    assert not source.is_on()


# Reconnect


async def test_drop_reconnects_and_stays_on(
    started: tuple[WebOsFrameSource, FakeWebOsClient],
) -> None:
    source, client = started
    FakeWebOsClient.connect_outcomes = [HANG]
    await client.drop()
    await _settle()

    # Reconnect in progress: aiowebostv has cleared its state, the source has not.
    assert client.connect_calls == 2
    assert not client.tv_state.is_on
    assert source.is_on()


async def test_drop_then_reconnect_succeeds(
    started: tuple[WebOsFrameSource, FakeWebOsClient],
) -> None:
    source, client = started
    await client.drop()
    await _settle()
    assert client.connect_calls == 2
    assert client.connection is not None
    assert source.is_on()


async def test_drop_then_reconnect_fails(
    started: tuple[WebOsFrameSource, FakeWebOsClient],
) -> None:
    source, client = started
    FakeWebOsClient.connect_outcomes = [OSError("gone")]
    await client.drop()
    await _settle()
    assert client.connect_calls == 2
    assert not source.is_on()
    with pytest.raises(SourceUnavailable):
        await source.capture()


async def test_reconnect_times_out(
    started: tuple[WebOsFrameSource, FakeWebOsClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, client = started
    monkeypatch.setattr(webos, "RECONNECT_TIMEOUT", 0.05)
    FakeWebOsClient.connect_outcomes = [HANG]
    await client.drop()
    await asyncio.sleep(0.1)
    assert not source.is_on()


# Capture


async def test_capture_during_reconnect_waits_for_it(
    started: tuple[WebOsFrameSource, FakeWebOsClient],
    aioclient_mock: AiohttpClientMocker,
) -> None:
    source, client = started
    aioclient_mock.get(IMAGE_URL, content=_jpeg())
    reconnect: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    FakeWebOsClient.connect_outcomes = [reconnect]
    await client.drop()
    await _settle()

    capture = asyncio.create_task(source.capture())
    await _settle()
    assert not capture.done()

    reconnect.set_result(None)
    frame = await capture
    assert (frame.width, frame.height) == (64, 36)


async def test_capture_during_failed_reconnect(
    started: tuple[WebOsFrameSource, FakeWebOsClient],
) -> None:
    source, client = started
    reconnect: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    FakeWebOsClient.connect_outcomes = [reconnect]
    await client.drop()
    await _settle()

    capture = asyncio.create_task(source.capture())
    await _settle()
    assert not capture.done()

    reconnect.set_exception(OSError("gone"))
    with pytest.raises(SourceUnavailable, match="Lost the connection"):
        await capture
    assert not source.is_on()


async def test_capture_during_reconnect_times_out(
    started: tuple[WebOsFrameSource, FakeWebOsClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, client = started
    monkeypatch.setattr(webos, "CAPTURE_TIMEOUT", 0.05)
    FakeWebOsClient.connect_outcomes = [HANG]
    await client.drop()
    await _settle()
    with pytest.raises(SourceUnavailable, match="timed out"):
        await source.capture()


async def test_capture_returns_a_downscaled_frame(
    started: tuple[WebOsFrameSource, FakeWebOsClient],
    aioclient_mock: AiohttpClientMocker,
) -> None:
    source, _ = started
    aioclient_mock.get(IMAGE_URL, content=_jpeg())

    frame = await source.capture()

    assert (frame.width, frame.height) == (64, 36)
    assert len(frame.pixels) == 64 * 36 * 3
    row = 18 * 64 * 3
    left = frame.pixels[row + 3 * 5 : row + 3 * 5 + 3]
    right = frame.pixels[row + 3 * 58 : row + 3 * 58 + 3]
    assert left[0] > 200 and left[2] < 50
    assert right[2] > 200 and right[0] < 50


async def test_capture_asks_for_a_picture_size(
    started: tuple[WebOsFrameSource, FakeWebOsClient],
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Older TVs refuse a full-screen capture without a size (#221)."""
    source, client = started
    aioclient_mock.get(IMAGE_URL, content=_jpeg())

    await source.capture()

    assert client.payloads == [
        {
            "path": "/tmp/capture.jpg",
            "method": "DISPLAY",
            "format": "JPG",
            "width": 960,
            "height": 540,
        }
    ]


async def test_capture_when_tv_off(
    started: tuple[WebOsFrameSource, FakeWebOsClient],
) -> None:
    source, client = started
    await client.set_power_state(POWER_STANDBY)
    with pytest.raises(SourceUnavailable, match="off or not connected"):
        await source.capture()


async def test_capture_before_start(source: WebOsFrameSource) -> None:
    with pytest.raises(SourceUnavailable, match="off or not connected"):
        await source.capture()


async def test_capture_hangs(
    started: tuple[WebOsFrameSource, FakeWebOsClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, client = started
    monkeypatch.setattr(webos, "CAPTURE_TIMEOUT", 0.05)
    client.request_result = HANG
    with pytest.raises(SourceUnavailable, match="timed out"):
        await source.capture()


async def test_capture_refused_by_tv(
    started: tuple[WebOsFrameSource, FakeWebOsClient],
) -> None:
    source, client = started
    client.request_result = WebOsTvCommandError("Request failed with response {...}")
    with pytest.raises(SourceUnavailable, match="refused"):
        await source.capture()


def _tv_error(error: str) -> Exception:
    """The error aiowebostv itself raises for the TV's error response."""
    response = {"type": "error", "id": 7, "error": error, "payload": {}}
    try:
        WebOsClient._parse_response(None, response)  # type: ignore[arg-type]
    except Exception as err:  # noqa: BLE001 - the error under test
        return err
    raise AssertionError("aiowebostv accepted an error response")


@pytest.mark.parametrize(
    ("error", "not_permitted", "match"),
    [
        ("404 no such service or method", False, "does not offer"),
        ("401 insufficient permissions", True, "does not permit"),
    ],
)
async def test_capture_not_supported_by_tv(
    started: tuple[WebOsFrameSource, FakeWebOsClient],
    error: str,
    not_permitted: bool,
    match: str,
) -> None:
    """A TV that does not know or permit the call is not a passing failure (#214)."""
    source, client = started
    client.request_result = _tv_error(error)
    with pytest.raises(SourceNotSupported, match=match) as err:
        await source.capture()
    assert err.value.not_permitted is not_permitted
    assert not isinstance(err.value, SourceUnavailable)
    assert err.value.__cause__ is None
    assert err.value.__context__ is None
    assert source.is_on()  # the TV is on; it only refuses the call


@pytest.mark.parametrize(
    "result",
    [
        _tv_error("500 Application error"),
        _tv_error("401 something else"),
        WebOsTvCommandError("Request failed with response {...}"),
    ],
)
async def test_other_refusals_are_passing_failures(
    started: tuple[WebOsFrameSource, FakeWebOsClient], result: Exception
) -> None:
    """Only the two refusals of #214 stop at once; the rest is as before."""
    source, client = started
    client.request_result = result
    with pytest.raises(SourceUnavailable, match="refused"):
        await source.capture()


async def test_capture_download_fails_without_leaking_the_url(
    started: tuple[WebOsFrameSource, FakeWebOsClient],
    aioclient_mock: AiohttpClientMocker,
) -> None:
    source, _ = started
    aioclient_mock.get(IMAGE_URL, status=404)
    with pytest.raises(SourceUnavailable, match="download") as err:
        await source.capture()
    assert "secret-hash" not in str(err.value)
    assert err.value.__cause__ is None
    assert err.value.__context__ is None


async def test_capture_with_a_broken_jpeg(
    started: tuple[WebOsFrameSource, FakeWebOsClient],
    aioclient_mock: AiohttpClientMocker,
) -> None:
    source, _ = started
    aioclient_mock.get(IMAGE_URL, content=b"not a jpeg")
    with pytest.raises(SourceUnavailable, match="read"):
        await source.capture()


async def test_capture_without_image_uri(
    started: tuple[WebOsFrameSource, FakeWebOsClient],
) -> None:
    source, client = started
    client.request_result = {"returnValue": True}
    with pytest.raises(SourceUnavailable, match="download"):
        await source.capture()


# The hardware check's probe (#115)

DATA = {"source": {"type": "webos", "unique_id": UNIQUE_ID}}


def tell(client: FakeWebOsClient) -> None:
    """What a TV says about itself, personal fields included."""
    client.tv_info.system = {"modelName": "OLED55C1", "serialNumber": "105KRXX0001"}
    client.tv_info.software = {
        "product_name": "webOSTV 6.0",
        "major_ver": "03",
        "minor_ver": "40.12",
        "device_id": "a8:23:fe:00:11:22",
    }
    client.tv_info.hello = {
        "deviceOSVersion": "6.4.0",
        "deviceOSReleaseVersion": "6.0.0",
        "deviceUUID": "6f1c0de2-aaaa-bbbb-cccc-0123456789ab",
    }
    client.tv_state.current_app_id = "com.webos.app.hdmi2"
    client.tv_state.inputs = {"HDMI_2": {"label": "Annas PlayStation"}}


def every_client(monkeypatch: pytest.MonkeyPatch, change: Callable) -> None:
    """Apply `change` to every fake client once it is made."""
    original = FakeWebOsClient.__init__

    def init(self: FakeWebOsClient, *args: Any, **kwargs: Any) -> None:
        original(self, *args, **kwargs)
        change(self)

    monkeypatch.setattr(FakeWebOsClient, "__init__", init)


@pytest.fixture
def telling(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every fake TV tells its facts once connected."""
    connect = FakeWebOsClient.connect

    async def connect_and_tell(self: FakeWebOsClient) -> bool:
        result = await connect(self)
        tell(self)
        return result

    monkeypatch.setattr(FakeWebOsClient, "connect", connect_and_tell)


@pytest.mark.usefixtures("telling")
async def test_probe_measures_the_captures(
    hass: HomeAssistant, add_tv_entry: AddTvEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    add_tv_entry()
    aioclient_mock.get(IMAGE_URL, content=_jpeg())
    result = await webos.probe(hass, DATA, {}, measure=True)

    assert result["connected"] is True
    assert result["tv"] == {
        "model": "OLED55C1",
        "product": "webOSTV 6.0",
        "firmware": "03.40.12",
        "webos_version": "6.0.0",
        "os_version": "6.4.0",
        "is_on": True,
        "is_screen_on": True,
        "app": "com.webos.app.hdmi2",
    }
    capture = result["capture"]
    assert capture["answered"] is True
    assert capture["refused"] is None
    assert capture["captures"] == webos.PROBE_CAPTURES
    assert capture["image"] == {
        "format": "JPEG",
        "width": 960,
        "height": 540,
        "bytes": len(_jpeg()),
    }
    timing = capture["seconds_per_capture"]
    assert 0 <= timing["min"] <= timing["median"] <= timing["max"]
    assert capture["captures_per_second"] > 0
    assert capture["black_captures"] == 0
    assert capture["all_black"] is False
    # Its own connection, closed at the end.
    [client] = FakeWebOsClient.instances
    assert client.disconnected
    text = repr(result)
    for personal in ("105KRXX0001", "a8:23:fe", "6f1c0de2", "Annas", "secret-hash"):
        assert personal not in text


async def test_probe_counts_black_captures(
    hass: HomeAssistant, add_tv_entry: AddTvEntry, aioclient_mock: AiohttpClientMocker
) -> None:
    """A protected app gives all-black captures (#8)."""
    add_tv_entry()
    aioclient_mock.get(IMAGE_URL, content=_jpeg(64, 36, black=True))
    capture = (await webos.probe(hass, DATA, {}, measure=True))["capture"]
    assert capture["black_captures"] == capture["captures"] > 0
    assert capture["all_black"] is True
    assert capture["image"]["width"] == 64


@pytest.mark.parametrize(
    ("error", "refused"),
    [
        (WebOsTvServiceNotFoundError("404 no such service or method"), "not_supported"),
        (
            WebOsTvResponseTypeError(
                {"type": "error", "error": "401 insufficient permissions"}
            ),
            "not_permitted",
        ),
    ],
)
async def test_probe_records_a_refusal(
    hass: HomeAssistant,
    add_tv_entry: AddTvEntry,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    refused: str,
) -> None:
    add_tv_entry()
    every_client(monkeypatch, lambda client: setattr(client, "request_result", error))
    capture = (await webos.probe(hass, DATA, {}, measure=True))["capture"]
    assert capture["answered"] is False
    assert capture["refused"] == refused
    assert capture["captures"] == 0
    assert capture["seconds_per_capture"] is None


async def test_probe_with_the_screen_off(
    hass: HomeAssistant, add_tv_entry: AddTvEntry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failed captures end the measurement; whether the TV would answer is
    not known."""
    add_tv_entry()
    every_client(
        monkeypatch, lambda client: setattr(client, "power_state", POWER_SCREEN_OFF)
    )
    result = await webos.probe(hass, DATA, {}, measure=True)
    capture = result["capture"]
    assert capture["answered"] is None
    assert capture["failures"] == {"The TV is off or not connected": 3}
    assert result["tv"]["is_screen_on"] is False


async def test_probe_when_the_tv_cannot_be_reached(
    hass: HomeAssistant, add_tv_entry: AddTvEntry, fake_client: type[FakeWebOsClient]
) -> None:
    add_tv_entry()
    fake_client.connect_outcomes = [OSError("unreachable 192.168.1.20")]
    result = await webos.probe(hass, DATA, {}, measure=True)
    assert result == {"connected": False, "error": "Could not connect to the TV"}


async def test_probe_while_syncing_does_not_capture(
    hass: HomeAssistant, add_tv_entry: AddTvEntry
) -> None:
    add_tv_entry()
    result = await webos.probe(hass, DATA, {}, measure=False)
    assert result["connected"] is True
    assert "capture" not in result
    [client] = FakeWebOsClient.instances
    assert client.payloads == []
