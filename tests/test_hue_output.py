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
    _light_of,
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
    lights: list[dict[str, Any]] | None = None,
    renderers: list[dict[str, Any]] | None = None,
    states_status: int = 200,
) -> None:
    aioclient_mock.get(
        f"https://{host}/auth/v1", headers={"hue-application-id": APP_ID}
    )
    aioclient_mock.get(
        f"https://{host}/clip/v2/resource/entertainment_configuration",
        json={"errors": [], "data": areas if areas is not None else [_area()]},
    )
    # What each light shows before the stream (#153).
    aioclient_mock.get(
        f"https://{host}/clip/v2/resource/light",
        status=states_status,
        json={"errors": [], "data": lights or []},
    )
    aioclient_mock.get(
        f"https://{host}/clip/v2/resource/entertainment",
        status=states_status,
        json={"errors": [], "data": renderers or []},
    )


def _renderer(number: int) -> dict[str, Any]:
    """The entertainment service of device `number`."""
    return {"id": f"ent-{number}", "owner": {"rid": f"dev-{number}"}, "renderer": True}


def _light(number: int, **state: Any) -> dict[str, Any]:
    """The light of device `number`, in the state given."""
    return {"id": f"light-{number}", "owner": {"rid": f"dev-{number}"}, **state}


def _member(number: int, index: int = 0) -> dict[str, Any]:
    return {
        "service": {"rid": f"ent-{number}", "rtype": "entertainment"},
        "index": index,
    }


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
        Channel(id="0", name="Channel 0", x=-1, z=0.0, y=0.8),
        Channel(id="1", name="Channel 1", x=0, z=0.5, y=0.8),
        Channel(id="2", name="Channel 2", x=1, z=1.0, y=0.8),
    ]


async def test_channels_of_one_gradient_light_share_it(
    output: HueEntertainmentOutput,
    add_bridge: AddBridge,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Two segments of one strip get its entertainment service as `light`
    (#23); a position without a depth gives y=None."""
    add_bridge()
    channels = [
        {
            "channel_id": 0,
            "position": {"x": -0.4, "y": 0.8, "z": 0.0},
            "members": [_member(4, 0)],
        },
        {
            "channel_id": 1,
            "position": {"x": 0.4, "y": 0.8, "z": 0.0},
            "members": [_member(4, 1)],
        },
        {"channel_id": 2, "position": {"x": 0.9, "z": 0.5}, "members": [_member(5)]},
    ]
    mock_bridge(aioclient_mock, [{**_area(), "channels": channels}])
    got = await output.start()
    assert [(c.light, c.y) for c in got] == [
        ("ent-4", 0.8),
        ("ent-4", 0.8),
        ("ent-5", None),
    ]


def test_segments_of_one_light_share_it() -> None:
    """The segments of a gradient light have their light's entertainment
    service as `light` (#23); a channel of several lights has none."""
    assert _light_of({"members": [_member(4, 0)]}) == "ent-4"
    assert _light_of({"members": [_member(4, 2)]}) == "ent-4"
    assert _light_of({"members": [_member(1), _member(2)]}) is None
    assert _light_of({"members": []}) is None
    assert _light_of({}) is None


def approx_rgb(rgb: tuple[float, float, float]) -> Any:
    return pytest.approx(rgb, abs=0.01)


async def test_channels_report_what_their_lights_showed_before(
    output: HueEntertainmentOutput,
    add_bridge: AddBridge,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Read before the stream starts, for the fade-in (#153)."""
    add_bridge()
    channels = [
        {"channel_id": 0, "members": [_member(1)]},  # a warm white at half
        {"channel_id": 1, "members": [_member(2)]},  # a colour at full
        {"channel_id": 2, "members": [_member(3)]},  # off
        # A gradient strip: every segment starts from the light's own colour.
        {"channel_id": 3, "members": [_member(4, 0)]},
        {"channel_id": 4, "members": [_member(4, 1)]},
        {"channel_id": 5, "members": [_member(9)]},  # not found: unknown
        {"channel_id": 6, "members": []},
        {"channel_id": 7, "members": [_member(5)]},  # a colour temperature only
        {"channel_id": 8, "members": [_member(6)]},  # no brightness: unknown
        {"channel_id": 9, "members": [_member(7)]},  # no x: unknown
    ]
    mock_bridge(
        aioclient_mock,
        [{**_area(), "channels": channels}],
        renderers=[_renderer(n) for n in (1, 2, 3, 4, 5, 6, 7)],
        lights=[
            _light(
                1,
                on={"on": True},
                dimming={"brightness": 50.0},
                # In colour-temperature mode a colour light reports the xy
                # of its temperature too (here 2700 K).
                color={"xy": {"x": 0.4578, "y": 0.4101}},
                color_temperature={"mirek": 370, "mirek_valid": True},
            ),
            _light(
                2,
                on={"on": True},
                dimming={"brightness": 100.0},
                color={"xy": {"x": 0.3, "y": 0.3}},
                color_temperature={"mirek": None, "mirek_valid": False},
            ),
            _light(3, on={"on": False}, dimming={"brightness": 80.0}),
            _light(
                4,
                on={"on": True},
                dimming={"brightness": 100.0},
                color={"xy": {"x": 0.3, "y": 0.3}},
            ),
            _light(
                5,
                on={"on": True},
                dimming={"brightness": 100.0},
                color_temperature={"mirek": 370, "mirek_valid": True},
            ),
            _light(
                6,
                on={"on": True},
                dimming={"brightness": None},
                color={"xy": {"x": 0.3, "y": 0.3}},
            ),
            _light(
                7,
                on={"on": True},
                dimming={"brightness": 100.0},
                color={"xy": {"x": None, "y": 0.3}},
            ),
        ],
    )
    started = await output.start()
    starts = [channel.start_rgb for channel in started]
    # From its xy, as a colour light in xy mode: 2700 K's xy is (1.0, 0.808,
    # 0.471) at full, here at half; not sRGB 2700 K (1.0, 0.654, 0.343), which
    # the bridge shows redder (architecture 3.5, #38).
    assert starts[0] == approx_rgb((0.5, 0.404, 0.236))
    assert starts[1] == approx_rgb((0.882, 0.894, 1.0))
    assert starts[2] == (0.0, 0.0, 0.0)
    assert starts[3] == starts[4] == approx_rgb((0.882, 0.894, 1.0))
    assert starts[5:7] == [None, None]
    # Without xy: sRGB of the temperature about 800 K cooler, as the glow.
    assert starts[7] == approx_rgb((1.0, 0.755, 0.552))
    assert starts[8:] == [None, None]
    # Read before the stream starts, never written.
    assert {method for method, *_ in aioclient_mock.mock_calls} == {"GET"}


async def test_a_retry_does_not_read_the_light_states(
    output: HueEntertainmentOutput,
    add_bridge: AddBridge,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """The lights come back with the picture's colours, without a fade-in,
    so a retry does not wait for the read."""
    add_bridge()
    mock_bridge(aioclient_mock)
    await output.start()
    await output.stop()
    await output.close()
    aioclient_mock.mock_calls.clear()
    channels = await output.start(retry=True)
    assert [channel.start_rgb for channel in channels] == [None, None, None]
    assert not any(
        "resource/light" in str(url) for _, url, *_ in aioclient_mock.mock_calls
    )
    assert not any(
        str(url).endswith("resource/entertainment")
        for _, url, *_ in aioclient_mock.mock_calls
    )


@pytest.mark.parametrize("status", [404, 500])
async def test_unreadable_light_states_start_without_them(
    output: HueEntertainmentOutput,
    add_bridge: AddBridge,
    aioclient_mock: AiohttpClientMocker,
    status: int,
) -> None:
    """The fade-in then starts from dark; the stream starts as always."""
    add_bridge()
    mock_bridge(
        aioclient_mock,
        [{**_area(), "channels": [{"channel_id": 0, "members": [_member(1)]}]}],
        states_status=status,
    )
    [channel] = await output.start()
    assert channel.start_rgb is None
    assert FakeSession.instances[-1].streaming


async def test_light_states_that_hang_do_not_hold_the_start(
    output: HueEntertainmentOutput,
    add_bridge: AddBridge,
    aioclient_mock: AiohttpClientMocker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_bridge()
    mock_bridge(aioclient_mock)

    async def hang(*args: Any, **kwargs: Any) -> Any:
        await asyncio.Event().wait()

    monkeypatch.setattr(hue, "STATES_TIMEOUT", 0.01)
    monkeypatch.setattr(hue, "_async_clip_data", hang)
    channels = await output.start()
    assert [channel.start_rgb for channel in channels] == [None, None, None]
    assert FakeSession.instances[-1].streaming


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
