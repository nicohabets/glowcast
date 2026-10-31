"""The settings as configuration entities on Glowcast's device (#13).

The pipeline is the fake one from fakes.py, except in the end-to-end test.
The bridge is faked at the HTTP level, as in the config flow tests, so the
entertainment-area entity is the real one the Hue output type provides.
"""

import asyncio
from collections.abc import Generator
from datetime import timedelta
from typing import Any

import pytest
from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import (
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNKNOWN,
    EntityCategory,
    Platform,
)
from homeassistant.core import CoreState, HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    mock_restore_cache_with_extra_data,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.glowcast import runtime
from custom_components.glowcast.const import DOMAIN
from custom_components.glowcast.core.liveliness import Liveliness
from custom_components.glowcast.core.pipeline import (
    Settings,
    UpdateRate,
)
from custom_components.glowcast.outputs import (
    OUTPUTS,
    OutputType,
    hue_area_select,
    hue_entertainment,
)

from .fakes import FakeAdapters, FakePipeline
from .test_config_flow import AREAS_URL, HOST, FakeBridge, area

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

TV_ID = "tv-unique-id"
BRIDGE_ID = "bridge-unique-id"
SWITCH = "switch.glowcast_living_room_tv"
BRIGHTNESS = "number.glowcast_living_room_tv_brightness"
INTENSITY = "number.glowcast_living_room_tv_intensity"
UPDATE_RATE = "select.glowcast_living_room_tv_update_rate"
LIVELINESS = "select.glowcast_living_room_tv_liveliness"
AREA = "select.glowcast_living_room_tv_entertainment_area"
LIVELY = Liveliness.LIVELY
OPTIONS = {
    "output": {"type": "hue_entertainment", "area_id": "area-tv"},
    "brightness": 60,
    "intensity": 120,
    "update_rate": "medium",
    "liveliness": "lively",
}


@pytest.fixture(autouse=True)
def bridge(aioclient_mock: AiohttpClientMocker) -> FakeBridge:
    """The bridge's area list over HTTP; tests change its state."""
    fake = FakeBridge()
    aioclient_mock.get(AREAS_URL, side_effect=fake.area_list)
    return fake


@pytest.fixture(autouse=True)
def hue_entry(hass: HomeAssistant) -> Generator[MockConfigEntry]:
    """A loaded TV and bridge; the real integrations are never set up."""
    hass.config.components.update({"webostv", "hue"})
    tv = MockConfigEntry(
        domain="webostv",
        title="Living room TV",
        unique_id=TV_ID,
        state=ConfigEntryState.LOADED,
    )
    hue = MockConfigEntry(
        domain="hue",
        title="Hue Bridge",
        unique_id=BRIDGE_ID,
        data={"host": HOST, "api_key": "ha-key", "api_version": 2},
        state=ConfigEntryState.LOADED,
    )
    tv.add_to_hass(hass)
    hue.add_to_hass(hass)
    yield hue
    for entry in (tv, hue):
        entry.mock_state(hass, ConfigEntryState.NOT_LOADED)


async def settle(hass: HomeAssistant) -> None:
    """Let background tasks run (the pipeline and the area list).

    Not wait_background_tasks: a running pipeline never finishes.
    """
    for _ in range(10):
        await hass.async_block_till_done()
        await asyncio.sleep(0)


async def setup(
    hass: HomeAssistant, options: dict[str, Any] | None = None
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Living room TV",
        unique_id=TV_ID,
        data={
            "source": {"type": "webos", "unique_id": TV_ID},
            "bridge": {
                "unique_id": BRIDGE_ID,
                "app_key": "app-key",
                "client_key": "client-key",
            },
        },
        options=OPTIONS if options is None else options,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await settle(hass)
    return entry


async def call(hass: HomeAssistant, domain: str, service: str, **data: Any) -> None:
    await hass.services.async_call(domain, service, data, blocking=True)
    await settle(hass)


async def set_number(hass: HomeAssistant, entity_id: str, value: float) -> None:
    await call(hass, "number", "set_value", entity_id=entity_id, value=value)


async def select(hass: HomeAssistant, entity_id: str, option: str) -> None:
    await call(hass, "select", "select_option", entity_id=entity_id, option=option)


async def switch(hass: HomeAssistant, service: str) -> None:
    await call(hass, "switch", service, entity_id=SWITCH)


def state(hass: HomeAssistant, entity_id: str) -> str:
    return hass.states.get(entity_id).state


def area_reads(aioclient_mock: AiohttpClientMocker) -> int:
    return sum(1 for _, url, _, _ in aioclient_mock.mock_calls if str(url) == AREAS_URL)


def raise_issue(hass: HomeAssistant, entry: ConfigEntry, key: str) -> None:
    ir.async_create_issue(
        hass,
        DOMAIN,
        runtime.issue_id(entry.entry_id, key),
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key=key,
    )


def has_issue(hass: HomeAssistant, entry: ConfigEntry, key: str) -> bool:
    issue_id = runtime.issue_id(entry.entry_id, key)
    return ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None


# The entities


@pytest.mark.parametrize(
    ("language", "names"),
    [
        (
            "en",
            {
                BRIGHTNESS: "Brightness",
                INTENSITY: "Intensity",
                UPDATE_RATE: "Update rate",
                LIVELINESS: "Liveliness",
                AREA: "Entertainment area",
            },
        ),
        (
            "nl",
            {
                BRIGHTNESS: "Helderheid",
                INTENSITY: "Intensiteit",
                UPDATE_RATE: "Updatefrequentie",
                LIVELINESS: "Levendigheid",
                AREA: "Entertainmentruimte",
            },
        ),
    ],
)
async def test_the_entities(
    hass: HomeAssistant, fakes: FakeAdapters, language: str, names: dict[str, str]
) -> None:
    """Five configuration entities next to the switch, with translated names."""
    hass.config.language = language
    entry = await setup(hass)
    registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(registry, entry.entry_id)
    by_key = {
        entity.unique_id.removeprefix(entry.entry_id): entity for entity in entities
    }
    assert registry.async_get(SWITCH).entity_category is None  # under Controls
    for entity_id, name in names.items():
        # In Home Assistant's language; so are new entity ids.
        entity = by_key["_" + entity_id.rsplit("living_room_tv_", 1)[1]]
        assert entity.entity_category is EntityCategory.CONFIG
        assert entity.has_entity_name
        assert entity.translation_key == entity_id.rsplit("living_room_tv_", 1)[1]
        state = hass.states.get(entity.entity_id)
        assert state.name == f"Glowcast Living room TV {name}"
    assert {entity.unique_id for entity in entities} == {
        entry.entry_id,  # the switch, as in v0.1
        f"{entry.entry_id}_brightness",
        f"{entry.entry_id}_intensity",
        f"{entry.entry_id}_update_rate",
        f"{entry.entry_id}_liveliness",
        f"{entry.entry_id}_entertainment_area",
        f"{entry.entry_id}_status",  # the sensors (#14)
        f"{entry.entry_id}_captures_per_second",
    }
    assert len({entity.device_id for entity in entities}) == 1


async def test_the_values_and_ranges(hass: HomeAssistant, fakes: FakeAdapters) -> None:
    await setup(hass)
    brightness = hass.states.get(BRIGHTNESS)
    assert brightness.state == "60"
    assert brightness.attributes["min"] == 1
    assert brightness.attributes["max"] == 100
    assert brightness.attributes["step"] == 1
    assert brightness.attributes["mode"] == "slider"
    assert brightness.attributes["unit_of_measurement"] == "%"
    intensity = hass.states.get(INTENSITY)
    assert intensity.state == "120"
    assert intensity.attributes["min"] == 0
    assert intensity.attributes["max"] == 200
    assert intensity.attributes["mode"] == "slider"
    update_rate = hass.states.get(UPDATE_RATE)
    assert update_rate.state == "medium"
    assert update_rate.attributes["options"] == ["low", "medium", "high", "maximum"]
    liveliness = hass.states.get(LIVELINESS)
    assert liveliness.state == "lively"
    assert liveliness.attributes["options"] == ["calm", "normal", "lively"]
    area_select = hass.states.get(AREA)
    assert area_select.state == "TV area"
    assert area_select.attributes["options"] == ["Other area", "TV area"]


async def test_an_older_entry_gets_the_entities_and_keeps_its_switch(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    """A v0.1.x entry without the settings keys: defaults, no migration."""
    entry = await setup(
        hass, {"output": {"type": "hue_entertainment", "area_id": "area-tv"}}
    )
    assert entry.version == 1
    assert state(hass, SWITCH) == STATE_OFF
    assert er.async_get(hass).async_get(SWITCH).unique_id == entry.entry_id
    assert state(hass, BRIGHTNESS) == "80"
    assert state(hass, INTENSITY) == "100"
    assert state(hass, UPDATE_RATE) == "high"
    assert state(hass, LIVELINESS) == "normal"  # v0.4 (#16)
    assert state(hass, AREA) == "TV area"
    assert entry.options == {
        "output": {"type": "hue_entertainment", "area_id": "area-tv"}
    }  # nothing written until a value changes


# Changing a value


@pytest.mark.parametrize(
    ("entity_id", "key", "value", "expected"),
    [
        (BRIGHTNESS, "brightness", 30, Settings(30, 120, UpdateRate.MEDIUM, LIVELY)),
        (INTENSITY, "intensity", 0, Settings(60, 0, UpdateRate.MEDIUM, LIVELY)),
        (
            UPDATE_RATE,
            "update_rate",
            "maximum",
            Settings(60, 120, UpdateRate.MAXIMUM, LIVELY),
        ),
        (
            LIVELINESS,
            "liveliness",
            "calm",
            Settings(60, 120, UpdateRate.MEDIUM, Liveliness.CALM),
        ),
    ],
)
async def test_a_setting_while_syncing_reaches_the_pipeline_without_a_restart(
    hass: HomeAssistant,
    fakes: FakeAdapters,
    entity_id: str,
    key: str,
    value: Any,
    expected: Settings,
) -> None:
    entry = await setup(hass)
    await switch(hass, SERVICE_TURN_ON)
    switch_states: list[str] = []
    hass.bus.async_listen(
        "state_changed",
        lambda event: (
            switch_states.append(event.data["new_state"].state)
            if event.data["entity_id"] == SWITCH
            else None
        ),
    )
    if entity_id in (UPDATE_RATE, LIVELINESS):
        await select(hass, entity_id, value)
    else:
        await set_number(hass, entity_id, value)

    [running] = FakePipeline.instances
    assert running.updates == [expected]
    assert fakes.log == ["source.start", "output.start"]  # no new claim
    assert state(hass, SWITCH) == STATE_ON
    assert STATE_OFF not in switch_states
    assert state(hass, entity_id) == str(value)
    assert entry.options[key] == value
    await switch(hass, SERVICE_TURN_OFF)


async def test_a_new_area_while_syncing_restarts_the_pipeline(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    entry = await setup(hass)
    await switch(hass, SERVICE_TURN_ON)
    await select(hass, AREA, "Other area")

    assert entry.options["output"] == {
        "type": "hue_entertainment",
        "area_id": "area-other",
    }
    first, second = FakePipeline.instances
    assert first.updates == []
    assert fakes.log.count("output.stop") == 1
    assert fakes.log[-1] == "output.start"
    assert state(hass, SWITCH) == STATE_ON
    assert state(hass, AREA) == "Other area"
    await switch(hass, SERVICE_TURN_OFF)


async def test_a_value_while_off_is_stored_and_used_at_the_next_start(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    entry = await setup(hass)
    await set_number(hass, BRIGHTNESS, 24.6)  # rounded to whole percents
    await select(hass, AREA, "Other area")
    assert FakePipeline.instances == []
    assert state(hass, SWITCH) == STATE_OFF
    assert entry.options["brightness"] == 25

    await switch(hass, SERVICE_TURN_ON)
    [started] = FakePipeline.instances
    assert started.settings.brightness == 25
    assert started.updates == []
    await switch(hass, SERVICE_TURN_OFF)


async def test_a_value_survives_a_restart(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    entry = await setup(hass)
    await set_number(hass, INTENSITY, 150)
    await select(hass, LIVELINESS, "calm")
    assert await hass.config_entries.async_reload(entry.entry_id)
    await settle(hass)
    assert state(hass, INTENSITY) == "150"
    assert state(hass, LIVELINESS) == "calm"


@pytest.mark.usefixtures("no_fade_in")
async def test_the_whole_chain_with_the_real_pipeline(
    hass: HomeAssistant, adapters: FakeAdapters
) -> None:
    """Entity, options, listener, pipeline: the lights dim, the stream stays."""
    await setup(hass, {**OPTIONS, "brightness": 100})
    await switch(hass, SERVICE_TURN_ON)
    await asyncio.sleep(0.2)
    [output] = adapters.outputs
    assert output.rgb_at(-1) == pytest.approx((1.0, 0.0, 0.0), abs=0.005)

    await set_number(hass, BRIGHTNESS, 40)
    await asyncio.sleep(0.2)  # a few send ticks
    assert output.rgb_at(-1) == pytest.approx((0.40, 0.0, 0.0), abs=0.005)
    assert adapters.log == ["source.start", "output.start"]
    assert state(hass, SWITCH) == STATE_ON
    await switch(hass, SERVICE_TURN_OFF)
    assert adapters.log[-3:] == ["output.stop", "output.close", "source.close"]


# One source of truth


def form_default(result: dict[str, Any], field: str) -> Any:
    [key] = [key for key in result["data_schema"].schema if key == field]
    return key.default()


async def test_the_options_screen_shows_what_an_entity_set(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    entry = await setup(hass)
    await set_number(hass, BRIGHTNESS, 33)
    await select(hass, UPDATE_RATE, "low")
    await select(hass, LIVELINESS, "calm")
    await select(hass, AREA, "Other area")
    result = await open_area_options(hass, entry)
    assert form_default(result, "area") == "area-other"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"area": "area-other"}
    )
    assert form_default(result, "brightness") == 33
    assert form_default(result, "update_rate") == "low"
    assert form_default(result, "liveliness") == "calm"


async def test_the_entities_show_what_the_options_screen_saved(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    entry = await setup(hass)
    await switch(hass, SERVICE_TURN_ON)
    result = await open_area_options(hass, entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"area": "area-tv"}
    )
    await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "brightness": 45,
            "intensity": 90,
            "update_rate": "high",
            "liveliness": "normal",
        },
    )
    await settle(hass)
    assert state(hass, BRIGHTNESS) == "45"
    assert state(hass, INTENSITY) == "90"
    assert state(hass, UPDATE_RATE) == "high"
    assert state(hass, LIVELINESS) == "normal"
    # Without a new area the options screen no longer restarts the stream.
    [running] = FakePipeline.instances
    assert running.updates == [Settings(45, 90, UpdateRate.HIGH, Liveliness.NORMAL)]
    assert state(hass, SWITCH) == STATE_ON
    await switch(hass, SERVICE_TURN_OFF)


# The entertainment area


async def test_a_removed_area_shows_unknown_and_choosing_another_clears_the_issue(
    hass: HomeAssistant, fakes: FakeAdapters, bridge: FakeBridge
) -> None:
    bridge.areas = [area("area-other", "Other area")]
    entry = await setup(hass)
    assert state(hass, AREA) == STATE_UNKNOWN
    assert hass.states.get(AREA).attributes["options"] == ["Other area"]
    # Raised at switch-on, as before (test_init covers how).
    raise_issue(hass, entry, hue_entertainment.ISSUE_AREA_NOT_FOUND)

    await select(hass, AREA, "Other area")
    assert state(hass, AREA) == "Other area"
    assert not has_issue(hass, entry, hue_entertainment.ISSUE_AREA_NOT_FOUND)
    assert FakePipeline.instances == []  # off: nothing starts


async def test_an_area_removed_while_running_shows_unknown_after_the_stop(
    hass: HomeAssistant, fakes: FakeAdapters, bridge: FakeBridge
) -> None:
    entry = await setup(hass)
    assert state(hass, AREA) == "TV area"
    bridge.areas = [area("area-other", "Other area")]  # removed in the Hue app
    FakePipeline.start_error = runtime.StopReason.OUTPUT_NOT_FOUND
    await switch(hass, SERVICE_TURN_ON)
    assert state(hass, SWITCH) == STATE_OFF
    assert has_issue(hass, entry, hue_entertainment.ISSUE_AREA_NOT_FOUND)
    assert state(hass, AREA) == STATE_UNKNOWN

    await select(hass, AREA, "Other area")
    assert not has_issue(hass, entry, hue_entertainment.ISSUE_AREA_NOT_FOUND)


async def test_another_setting_leaves_the_area_issue_standing(
    hass: HomeAssistant, fakes: FakeAdapters, bridge: FakeBridge
) -> None:
    bridge.areas = [area("area-other", "Other area")]
    entry = await setup(hass)
    raise_issue(hass, entry, hue_entertainment.ISSUE_AREA_NOT_FOUND)
    await set_number(hass, BRIGHTNESS, 50)
    assert has_issue(hass, entry, hue_entertainment.ISSUE_AREA_NOT_FOUND)


async def test_two_areas_with_the_same_name(
    hass: HomeAssistant, fakes: FakeAdapters, bridge: FakeBridge
) -> None:
    bridge.areas = [
        area("area-b", "Living room"),
        area("area-tv", "TV area"),
        area("area-a", "Living room"),
    ]
    entry = await setup(hass)
    assert hass.states.get(AREA).attributes["options"] == [
        "Living room",
        "Living room (2)",
        "TV area",
    ]
    await select(hass, AREA, "Living room (2)")
    assert entry.options["output"]["area_id"] == "area-b"
    await select(hass, AREA, "Living room")
    assert entry.options["output"]["area_id"] == "area-a"


async def test_areas_with_too_many_channels_are_listed(
    hass: HomeAssistant, fakes: FakeAdapters, bridge: FakeBridge
) -> None:
    """The too_many_channels repair issue handles them at switch-on."""
    big = {**area("area-big", "Big area"), "channels": [{}] * 25}
    bridge.areas = [*bridge.areas, big]
    await setup(hass)
    assert "Big area" in hass.states.get(AREA).attributes["options"]


async def test_bridge_unreachable_at_start_shows_the_restored_area(
    hass: HomeAssistant,
    fakes: FakeAdapters,
    bridge: FakeBridge,
    hue_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """The hue entry retries its setup; the last list comes from before."""
    hue_entry.mock_state(hass, ConfigEntryState.SETUP_RETRY)
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(AREA, "TV area"),
                {"areas": {"area-tv": "TV area", "area-other": "Other area"}},
            )
        ],
    )
    await setup(hass)
    assert state(hass, AREA) == "TV area"
    assert area_reads(aioclient_mock) == 0  # nothing to ask until hue is loaded

    bridge.areas = [*bridge.areas, area("area-new", "New area")]
    hue_entry.mock_state(hass, ConfigEntryState.LOADED)
    await settle(hass)
    assert area_reads(aioclient_mock) == 1
    assert state(hass, AREA) == "TV area"
    assert "New area" in hass.states.get(AREA).attributes["options"]


async def test_bridge_unreachable_without_a_restored_list(
    hass: HomeAssistant, fakes: FakeAdapters, hue_entry: MockConfigEntry
) -> None:
    """The first start after the update, with the bridge down: no name known."""
    hue_entry.mock_state(hass, ConfigEntryState.SETUP_RETRY)
    entry = await setup(hass)
    assert state(hass, AREA) == STATE_UNKNOWN
    assert entry.options["output"]["area_id"] == "area-tv"  # kept


async def test_a_failed_read_is_retried_with_a_backoff(
    hass: HomeAssistant,
    fakes: FakeAdapters,
    bridge: FakeBridge,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    bridge.reachable = False
    await setup(hass)
    assert area_reads(aioclient_mock) == 1
    assert hass.states.get(AREA).attributes["options"] == []

    now = dt_util.utcnow()
    async_fire_time_changed(hass, now + timedelta(seconds=31))
    await settle(hass)
    assert area_reads(aioclient_mock) == 2  # after 30 s

    bridge.reachable = True
    async_fire_time_changed(hass, now + timedelta(seconds=31 + 61))
    await settle(hass)
    assert area_reads(aioclient_mock) == 3
    assert state(hass, AREA) == "TV area"
    async_fire_time_changed(hass, now + timedelta(hours=1))
    await settle(hass)
    assert area_reads(aioclient_mock) == 3  # no polling once read


class Timers:
    """Stands in for async_call_later: records the delays, fires on demand."""

    def __init__(self) -> None:
        self.delays: list[float] = []
        self.pending: list[Any] = []

    def call_later(self, hass: HomeAssistant, delay: float, action: Any) -> Any:
        self.delays.append(delay)
        self.pending.append(action)
        return lambda: self.pending.remove(action)

    async def fire(self, hass: HomeAssistant) -> None:
        action = self.pending.pop(0)
        action(dt_util.utcnow())
        await settle(hass)


@pytest.fixture
def timers(monkeypatch: pytest.MonkeyPatch) -> Timers:
    fake = Timers()
    monkeypatch.setattr(hue_area_select, "async_call_later", fake.call_later)
    return fake


async def test_the_backoff_doubles_up_to_ten_minutes_and_resets(
    hass: HomeAssistant,
    fakes: FakeAdapters,
    bridge: FakeBridge,
    hue_entry: MockConfigEntry,
    timers: Timers,
) -> None:
    bridge.reachable = False
    await setup(hass)
    for _ in range(6):
        await timers.fire(hass)
    assert timers.delays == [30, 60, 120, 240, 480, 600, 600]
    # The hue entry was loaded again: the pending retry is replaced by a read
    # at once, and the next failure waits 30 s again.
    hue_entry.mock_state(hass, ConfigEntryState.LOADED)
    await settle(hass)
    assert len(timers.pending) == 1
    assert timers.delays[-1] == 30


async def test_a_list_from_the_options_screen_cancels_a_pending_retry(
    hass: HomeAssistant, fakes: FakeAdapters, bridge: FakeBridge, timers: Timers
) -> None:
    bridge.reachable = False
    entry = await setup(hass)
    assert len(timers.pending) == 1
    bridge.reachable = True
    await open_area_options(hass, entry)
    await settle(hass)
    assert timers.pending == []
    assert state(hass, AREA) == "TV area"


async def test_no_read_when_the_entry_unloads_or_home_assistant_stops(
    hass: HomeAssistant,
    fakes: FakeAdapters,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    entry = await setup(hass)
    await switch(hass, SERVICE_TURN_ON)
    reads = area_reads(aioclient_mock)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await settle(hass)
    assert area_reads(aioclient_mock) == reads

    assert await hass.config_entries.async_setup(entry.entry_id)
    await settle(hass)
    await switch(hass, SERVICE_TURN_ON)
    reads = area_reads(aioclient_mock)
    hass.set_state(CoreState.stopping)
    await entry.runtime_data.async_stop()
    await settle(hass)
    assert area_reads(aioclient_mock) == reads
    hass.set_state(CoreState.running)


async def test_a_rejected_key_keeps_the_area_and_starts_no_reauth(
    hass: HomeAssistant,
    fakes: FakeAdapters,
    bridge: FakeBridge,
    caplog: pytest.LogCaptureFixture,
) -> None:
    bridge.areas_status = 403
    mock_restore_cache_with_extra_data(
        hass, [(State(AREA, "TV area"), {"areas": {"area-tv": "TV area"}})]
    )
    entry = await setup(hass)
    assert state(hass, AREA) == "TV area"
    assert not list(entry.async_get_active_flows(hass, {"reauth"}))
    await set_number(hass, BRIGHTNESS, 50)  # reads the list again
    assert caplog.text.count("rejected Glowcast's key") == 1
    assert "app-key" not in caplog.text


async def test_opening_the_options_screen_refreshes_the_list(
    hass: HomeAssistant, fakes: FakeAdapters, bridge: FakeBridge
) -> None:
    entry = await setup(hass)
    bridge.areas = [*bridge.areas, area("area-new", "New area")]
    await open_area_options(hass, entry)
    await settle(hass)
    assert "New area" in hass.states.get(AREA).attributes["options"]


async def open_area_options(
    hass: HomeAssistant, entry: MockConfigEntry
) -> dict[str, Any]:
    """Open the settings screen and choose the entertainment area (#9)."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "hue_options"}
    )


# The output type decides what follows the TV


class FakeFollow(NumberEntity):
    """What a later output type might provide (on the number platform)."""

    _attr_has_entity_name = True
    _attr_name = "Follow"
    _attr_native_value = 1

    def __init__(self, entry: ConfigEntry) -> None:
        self._attr_unique_id = f"{entry.entry_id}_follow"


@pytest.mark.parametrize(
    ("provided", "expected"),
    [
        ({}, set()),
        ({Platform.NUMBER: lambda hass, entry: [FakeFollow(entry)]}, {"number"}),
    ],
)
async def test_the_output_type_provides_what_follows_the_tv(
    hass: HomeAssistant,
    fakes: FakeAdapters,
    monkeypatch: pytest.MonkeyPatch,
    provided: dict[Platform, Any],
    expected: set[str],
) -> None:
    """The generic platforms add only what the type provides."""
    monkeypatch.setitem(
        OUTPUTS,
        "other_lights",
        OutputType(
            create=lambda hass, data, options: fakes.output(),
            is_missing=lambda hass, data, options: False,
            device_id=lambda hass, data: None,
            entities=provided,
        ),
    )
    entry = await setup(hass, {**OPTIONS, "output": {"type": "other_lights"}})
    registry = er.async_get(hass)
    unique_ids = {
        entity.unique_id: entity.domain
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    follow = {
        domain
        for unique_id, domain in unique_ids.items()
        if unique_id == f"{entry.entry_id}_follow"
    }
    assert follow == expected
    assert f"{entry.entry_id}_entertainment_area" not in unique_ids
    # The common settings, the switch and the two sensors (#14).
    assert len(unique_ids) == 7 + len(expected)


async def test_an_unknown_output_type_still_gets_the_common_settings(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    """For example an entry written by a later version, after a downgrade."""
    entry = await setup(hass, {**OPTIONS, "output": {"type": "from_the_future"}})
    domains = sorted(
        entity.domain
        for entity in er.async_entries_for_config_entry(
            er.async_get(hass), entry.entry_id
        )
    )
    assert domains == [
        "number",
        "number",
        "select",
        "select",
        "sensor",
        "sensor",
        "switch",
    ]


def test_an_output_type_without_its_own_entities() -> None:
    """The hook is optional, so a new type needs no change to this story's code."""
    output_type = OutputType(
        create=lambda hass, data, options: None,
        is_missing=lambda hass, data, options: False,
        device_id=lambda hass, data: None,
    )
    assert output_type.entities == {}
    assert set(OUTPUTS["hue_entertainment"].entities) == {Platform.SELECT}
