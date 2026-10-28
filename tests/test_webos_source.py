"""Tests for the webOS frame source, with a fake aiowebostv client."""

import asyncio
import io
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import suppress
from typing import Any

import pytest
from aiowebostv import WebOsTvState
from aiowebostv.exceptions import WebOsTvCommandError, WebOsTvPairError
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from PIL import Image
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.glowcast.core.ports import SourceUnavailable
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
        self.callbacks: list[Callable[[WebOsTvState], Any]] = []
        self.power_state = POWER_ON
        self.request_result: Any = {"imageUri": IMAGE_URL}
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


def _jpeg(width: int = 960, height: int = 540) -> bytes:
    """A 960x540 JPEG: red left half, blue right half."""
    image = Image.new("RGB", (width, height), (255, 0, 0))
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
