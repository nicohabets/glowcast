"""Tests for the Hue entertainment output, with a fake EntertainmentSession."""

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any
from unittest.mock import Mock

import aiohttp
import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from hue_entertainment.models import LightColorCommand
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.glowcast.core.models import Channel, ChannelColor
from custom_components.glowcast.core.ports import (
    OutputAuthFailed,
    OutputBusy,
    OutputLost,
    OutputNotFound,
    OutputTooManyChannels,
    OutputUnavailable,
)
from custom_components.glowcast.outputs import hue_entertainment as hue
from custom_components.glowcast.outputs.hue_entertainment import (
    HueEntertainmentOutput,
)

BRIDGE_ID = "bridge-unique-id"
HOST = "bridge.local"
APP_KEY = "app-key"
CLIENT_KEY = "00" * 16
APP_ID = "glowcast-app-id"
OTHER_APP_ID = "other-app-id"
AREA = "11111111-1111-1111-1111-111111111111"
OTHER_AREA = "22222222-2222-2222-2222-222222222222"

AUTH_URL = f"https://{HOST}/auth/v1"
AREAS_URL = f"https://{HOST}/clip/v2/resource/entertainment_configuration"

HANG = object()


def _response_error(status: int) -> aiohttp.ClientResponseError:
    return aiohttp.ClientResponseError(
        request_info=Mock(real_url="https://bridge.local"), history=(), status=status
    )


class FakeSession:
    """Behaves like hue_entertainment.EntertainmentSession."""

    instances: list["FakeSession"] = []
    start_outcome: Any = None  # None starts, an exception is raised, HANG never ends

    def __init__(
        self, host: str, app_key: str, client_key: str, *, idle_timeout: float = 10.0
    ) -> None:
        self.host = host
        self.app_key = app_key
        self.client_key = client_key
        self.idle_timeout = idle_timeout
        self.start_calls: list[tuple[str, bool]] = []
        self.streaming = False
        self.sent: list[list[LightColorCommand]] = []
        self.status: Any = ("active", APP_ID)
        self.status_calls = 0
        self.stop_calls = 0
        self.closed = False
        FakeSession.instances.append(self)

    async def start(self, area_id: str, *, stop_others: bool = True) -> None:
        self.start_calls.append((area_id, stop_others))
        if self.start_outcome is HANG:
            await asyncio.Event().wait()
        if isinstance(self.start_outcome, BaseException):
            raise self.start_outcome
        self.streaming = True

    @property
    def is_streaming(self) -> bool:
        return self.streaming

    def send(self, commands: list[LightColorCommand]) -> None:
        self.sent.append(commands)

    async def remote_status(self) -> tuple[str, str]:
        self.status_calls += 1
        if isinstance(self.status, BaseException):
            raise self.status
        return self.status

    async def stop(self) -> None:
        self.stop_calls += 1
        self.streaming = False

    async def aclose(self) -> None:
        await self.stop()
        self.closed = True


class FakeApi:
    """Behaves like hue_entertainment.HueEntertainmentAPI for stop and close."""

    instances: list["FakeApi"] = []

    def __init__(self, host: str, app_key: str | None = None) -> None:
        self.host = host
        self.stopped: list[str] = []
        self.closed = False
        FakeApi.instances.append(self)

    async def stop_entertainment(self, area_id: str) -> None:
        self.stopped.append(area_id)

    async def close(self) -> None:
        self.closed = True


def _area(
    area_id: str = AREA,
    status: str = "inactive",
    streamer: str | None = None,
    channels: int = 3,
) -> dict[str, Any]:
    return {
        "id": area_id,
        "status": status,
        "active_streamer": {"rid": streamer, "rtype": "auth_v1"} if streamer else None,
        "channels": [
            {"channel_id": i, "position": {"x": -1 + i, "y": 0.8, "z": 0.5 * i}}
            for i in range(channels)
        ],
    }


AddBridge = Callable[..., MockConfigEntry]


@pytest.fixture(autouse=True)
def fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeSession.instances = []
    FakeSession.start_outcome = None
    FakeApi.instances = []
    monkeypatch.setattr(hue, "EntertainmentSession", FakeSession)
    monkeypatch.setattr(hue, "HueEntertainmentAPI", FakeApi)


@pytest.fixture
def add_bridge(hass: HomeAssistant) -> Iterator[AddBridge]:
    """Add a hue entry; marked not loaded afterwards so HA does not unload it."""
    entries: list[MockConfigEntry] = []

    def add(
        host: str = HOST,
        api_version: int = 2,
        state: ConfigEntryState = ConfigEntryState.LOADED,
    ) -> MockConfigEntry:
        entry = MockConfigEntry(
            domain="hue",
            unique_id=BRIDGE_ID,
            data={"host": host, "api_key": "ha-key", "api_version": api_version},
            state=state,
        )
        entry.add_to_hass(hass)
        entries.append(entry)
        return entry

    yield add
    for entry in entries:
        entry.mock_state(hass, ConfigEntryState.NOT_LOADED)


def mock_bridge(
    aioclient_mock: AiohttpClientMocker,
    areas: list[dict[str, Any]] | None = None,
    host: str = HOST,
) -> None:
    aioclient_mock.get(
        f"https://{host}/auth/v1", headers={"hue-application-id": APP_ID}
    )
    aioclient_mock.get(
        f"https://{host}/clip/v2/resource/entertainment_configuration",
        json={"errors": [], "data": areas if areas is not None else [_area()]},
    )


@pytest.fixture
async def output(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> AsyncIterator[HueEntertainmentOutput]:
    """An output; aioclient_mock first, so HA's shared session is the mocked one."""
    output = HueEntertainmentOutput(hass, BRIDGE_ID, APP_KEY, CLIENT_KEY, AREA)
    yield output
    await output.stop()
    await output.close()


@pytest.fixture
async def started(
    output: HueEntertainmentOutput,
    add_bridge: AddBridge,
    aioclient_mock: AiohttpClientMocker,
) -> tuple[HueEntertainmentOutput, FakeSession]:
    add_bridge()
    mock_bridge(aioclient_mock)
    await output.start()
    return output, FakeSession.instances[-1]


# Start


async def test_start_returns_channels_and_streams(
    started: tuple[HueEntertainmentOutput, FakeSession],
) -> None:
    output, session = started
    assert (session.host, session.app_key, session.client_key) == (
        HOST,
        APP_KEY,
        CLIENT_KEY,
    )
    assert session.idle_timeout == 0  # no silent timeout after 10 s
    assert session.start_calls == [(AREA, False)]  # never stops other streams
    assert output.max_rate_hz == 25


async def test_channels_have_their_positions(
    output: HueEntertainmentOutput,
    add_bridge: AddBridge,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    add_bridge()
    mock_bridge(aioclient_mock)
    channels = await output.start()
    assert channels == [
        Channel(id="0", name="Channel 0", x=-1, z=0.0),
        Channel(id="1", name="Channel 1", x=0, z=0.5),
        Channel(id="2", name="Channel 2", x=1, z=1.0),
    ]


async def test_start_uses_the_bridge_host_from_ha(
    hass: HomeAssistant,
    output: HueEntertainmentOutput,
    add_bridge: AddBridge,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    entry = add_bridge()
    mock_bridge(aioclient_mock)
    await output.start()
    await output.stop()
    await output.close()

    hass.config_entries.async_update_entry(
        entry, data={**entry.data, "host": "new.local"}
    )
    mock_bridge(aioclient_mock, host="new.local")
    await output.start()
    assert FakeSession.instances[-1].host == "new.local"


@pytest.mark.parametrize(
    "bridge",
    [
        None,
        {"state": ConfigEntryState.SETUP_RETRY},
        {"api_version": 1},
    ],
    ids=["no entry", "not loaded", "api v1"],
)
async def test_start_without_a_usable_hue_entry(
    output: HueEntertainmentOutput, add_bridge: AddBridge, bridge: dict | None
) -> None:
    if bridge is not None:
        add_bridge(**bridge)
    with pytest.raises(OutputUnavailable):
        await output.start()
    assert FakeSession.instances == []


async def test_start_when_bridge_unreachable(
    output: HueEntertainmentOutput,
    add_bridge: AddBridge,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    add_bridge()
    aioclient_mock.get(AUTH_URL, exc=aiohttp.ClientConnectionError())
    with pytest.raises(OutputUnavailable, match="reach"):
        await output.start()


async def test_start_when_bridge_hangs(
    output: HueEntertainmentOutput,
    add_bridge: AddBridge,
    aioclient_mock: AiohttpClientMocker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hue, "START_TIMEOUT", 0.05)
    add_bridge()
    mock_bridge(aioclient_mock)
    FakeSession.start_outcome = HANG
    with pytest.raises(OutputUnavailable, match="in time"):
        await output.start()


async def test_start_when_handshake_fails(
    output: HueEntertainmentOutput,
    add_bridge: AddBridge,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    add_bridge()
    mock_bridge(aioclient_mock)
    FakeSession.start_outcome = TimeoutError("ServerHello")
    with pytest.raises(OutputUnavailable, match="stream"):
        await output.start()


async def test_too_many_channels(
    output: HueEntertainmentOutput,
    add_bridge: AddBridge,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    add_bridge()
    mock_bridge(aioclient_mock, [_area(channels=21)])
    with pytest.raises(
        OutputTooManyChannels, match="21 channels; Hue streams at most 20"
    ):
        await output.start()
    assert FakeSession.instances == []


async def test_area_removed(
    output: HueEntertainmentOutput,
    add_bridge: AddBridge,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    add_bridge()
    mock_bridge(aioclient_mock, [_area(OTHER_AREA)])
    with pytest.raises(OutputNotFound):
        await output.start()


@pytest.mark.parametrize("status", [401, 403])
async def test_key_revoked_at_start(
    output: HueEntertainmentOutput,
    add_bridge: AddBridge,
    aioclient_mock: AiohttpClientMocker,
    status: int,
) -> None:
    add_bridge()
    aioclient_mock.get(AUTH_URL, status=status)
    with pytest.raises(OutputAuthFailed):
        await output.start()


async def test_key_rejected_when_starting_the_area(
    output: HueEntertainmentOutput,
    add_bridge: AddBridge,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    add_bridge()
    mock_bridge(aioclient_mock)
    FakeSession.start_outcome = _response_error(403)
    with pytest.raises(OutputAuthFailed):
        await output.start()


# Other streams


@pytest.mark.parametrize("busy_area", [AREA, OTHER_AREA])
async def test_another_app_is_streaming(
    output: HueEntertainmentOutput,
    add_bridge: AddBridge,
    aioclient_mock: AiohttpClientMocker,
    busy_area: str,
) -> None:
    add_bridge()
    areas = [_area(), _area(OTHER_AREA)]
    areas = [
        _area(a["id"], "active", OTHER_APP_ID) if a["id"] == busy_area else a
        for a in areas
    ]
    mock_bridge(aioclient_mock, areas)
    with pytest.raises(OutputBusy):
        await output.start()
    await output.stop()
    assert FakeSession.instances == []
    assert FakeApi.instances[-1].stopped == []  # the other stream is left alone


async def test_our_own_stale_stream_is_stopped(
    output: HueEntertainmentOutput,
    add_bridge: AddBridge,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    add_bridge()
    mock_bridge(aioclient_mock, [_area(), _area(OTHER_AREA, "active", APP_ID)])
    await output.start()
    assert FakeApi.instances[-1].stopped == [OTHER_AREA]
    assert FakeSession.instances[-1].streaming


# Colours


async def test_colours_go_out_as_8_bit(
    started: tuple[HueEntertainmentOutput, FakeSession],
) -> None:
    output, session = started
    output.send(
        [
            ChannelColor("0", (0.01, 0.01, 0.01)),
            ChannelColor("1", (1.0, 0.5, 0.0)),
            ChannelColor("2", (0.0, 0.2, 1.0)),
            ChannelColor("99", (1.0, 1.0, 1.0)),  # unknown channel: ignored
        ]
    )
    assert session.sent == [
        [
            LightColorCommand(0, 3, 3, 3),
            LightColorCommand(1, 255, 128, 0),
            LightColorCommand(2, 0, 51, 255),
        ]
    ]


async def test_colours_are_clamped(
    started: tuple[HueEntertainmentOutput, FakeSession],
) -> None:
    output, session = started
    output.send([ChannelColor("0", (1.2, -0.1, 0.5))])
    assert session.sent == [[LightColorCommand(0, 255, 0, 128)]]


async def test_send_before_start_does_nothing(output: HueEntertainmentOutput) -> None:
    output.send([ChannelColor("0", (1.0, 0.0, 0.0))])


# Check


@pytest.fixture
def every_check_polls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hue, "STATUS_INTERVAL", 0)


async def test_check_while_streaming(
    every_check_polls: None,
    started: tuple[HueEntertainmentOutput, FakeSession],
) -> None:
    output, session = started
    await output.check()
    assert session.status_calls == 1


async def test_status_is_polled_every_5_seconds(
    started: tuple[HueEntertainmentOutput, FakeSession],
) -> None:
    output, session = started
    await output.check()
    await output.check()
    assert session.status_calls == 0


async def test_stream_dropped(
    started: tuple[HueEntertainmentOutput, FakeSession],
) -> None:
    output, session = started
    session.streaming = False
    with pytest.raises(OutputLost):
        await output.check()


@pytest.mark.parametrize(
    "status", [("inactive", ""), ("active", OTHER_APP_ID)], ids=["ended", "taken over"]
)
async def test_stream_ended_or_taken_over(
    every_check_polls: None,
    started: tuple[HueEntertainmentOutput, FakeSession],
    status: tuple[str, str],
) -> None:
    output, session = started
    session.status = status
    with pytest.raises(OutputLost):
        await output.check()


async def test_one_failed_status_request_is_tolerated(
    every_check_polls: None,
    started: tuple[HueEntertainmentOutput, FakeSession],
) -> None:
    output, session = started
    session.status = aiohttp.ClientConnectionError()
    await output.check()
    session.status = ("active", APP_ID)
    await output.check()  # success resets the count
    session.status = TimeoutError()
    await output.check()
    with pytest.raises(OutputLost):
        await output.check()


@pytest.mark.parametrize("status", [401, 403])
async def test_key_revoked_while_streaming(
    every_check_polls: None,
    started: tuple[HueEntertainmentOutput, FakeSession],
    status: int,
) -> None:
    output, session = started
    session.status = _response_error(status)
    with pytest.raises(OutputAuthFailed):
        await output.check()


async def test_check_before_start(output: HueEntertainmentOutput) -> None:
    with pytest.raises(OutputLost):
        await output.check()


# Stop and close


async def test_stop_releases_the_area(
    started: tuple[HueEntertainmentOutput, FakeSession],
) -> None:
    output, session = started
    await output.stop()
    assert session.stop_calls == 1
    assert FakeApi.instances[-1].stopped == [AREA]


async def test_stop_after_cancelled_start_still_releases(
    output: HueEntertainmentOutput,
    add_bridge: AddBridge,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    add_bridge()
    mock_bridge(aioclient_mock)
    FakeSession.start_outcome = HANG
    start = asyncio.create_task(output.start())
    for _ in range(20):
        await asyncio.sleep(0)
    assert FakeSession.instances  # the handshake is running
    start.cancel()
    with pytest.raises(asyncio.CancelledError):
        await start

    await output.stop()
    assert FakeApi.instances[-1].stopped == [AREA]


async def test_stop_after_timed_out_start_still_releases(
    output: HueEntertainmentOutput,
    add_bridge: AddBridge,
    aioclient_mock: AiohttpClientMocker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hue, "START_TIMEOUT", 0.05)
    add_bridge()
    mock_bridge(aioclient_mock)
    FakeSession.start_outcome = HANG
    with pytest.raises(OutputUnavailable):
        await output.start()
    await output.stop()
    assert FakeApi.instances[-1].stopped == [AREA]


async def test_stop_completes_when_the_caller_is_cancelled(
    started: tuple[HueEntertainmentOutput, FakeSession],
) -> None:
    output, _ = started
    stop = asyncio.create_task(output.stop())
    await asyncio.sleep(0)
    stop.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stop
    for _ in range(5):
        await asyncio.sleep(0)
    assert FakeApi.instances[-1].stopped == [AREA]


async def test_stop_and_close_twice(
    started: tuple[HueEntertainmentOutput, FakeSession],
) -> None:
    output, session = started
    api = FakeApi.instances[-1]
    await output.stop()
    await output.stop()
    await output.close()
    await output.close()
    assert api.stopped == [AREA]
    assert session.closed
    assert api.closed


async def test_stop_before_start(output: HueEntertainmentOutput) -> None:
    await output.stop()
    await output.close()
