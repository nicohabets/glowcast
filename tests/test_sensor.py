"""The status and the captures per second on Glowcast's device (#14).

The pipeline is the fake one from fakes.py: each test makes it report the
status it needs. One test runs the real pipeline end to end.
"""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import (
    EVENT_STATE_CHANGED,
    EVENT_STATE_REPORTED,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_UNKNOWN,
    EntityCategory,
)
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.translation import async_get_translations

from custom_components.glowcast.const import DOMAIN
from custom_components.glowcast.core.pipeline import Phase, PipelineStatus, StopReason
from custom_components.glowcast.sensor import Status, status_of

from .fakes import DARK, FakeAdapters, FakePipeline, solid, wait_for
from .test_entities import (  # noqa: F401 (bridge and hue_entry are fixtures)
    AREA,
    OPTIONS,
    SWITCH,
    bridge,
    call,
    hue_entry,
    select,
    settle,
    setup,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

STATUS = "sensor.glowcast_living_room_tv_status"
RATE = "sensor.glowcast_living_room_tv_capture_rate"
TRANSLATIONS = Path(__file__).parent.parent / "custom_components/glowcast/translations"


async def switch(hass: HomeAssistant, service: str) -> None:
    await call(hass, "switch", service, entity_id=SWITCH)


def report(hass: HomeAssistant, status: PipelineStatus) -> None:
    """The running pipeline reports a status."""
    FakePipeline.instances[-1].on_status(status)


async def enable_rate(hass: HomeAssistant, entry: Any) -> None:
    """Enable the captures per second, as a user does on the device page."""
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_captures_per_second"
    )
    registry.async_update_entity(entity_id, disabled_by=None)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await settle(hass)


def status(hass: HomeAssistant) -> str:
    return hass.states.get(STATUS).state


def status_changes(hass: HomeAssistant) -> list[str]:
    """The status sensor's values from now on, in order."""
    seen: list[str] = []

    @callback
    def changed(event: Event) -> None:
        if event.data["entity_id"] == STATUS:
            seen.append(event.data["new_state"].state)

    hass.bus.async_listen(EVENT_STATE_CHANGED, changed)
    return seen


# The value for each status


@pytest.mark.parametrize(
    ("pipeline", "syncing", "expected"),
    [
        (None, False, Status.IDLE),
        (None, True, Status.STARTING),
        (PipelineStatus(Phase.STARTING), True, Status.STARTING),
        (PipelineStatus(Phase.RETRYING, channels=1), True, Status.STARTING),
        (PipelineStatus(Phase.STREAMING, channels=1), True, Status.STREAMING),
        (
            PipelineStatus(Phase.STREAMING, channels=1, black_picture=True),
            True,
            Status.BLACK_PICTURE,
        ),
        (PipelineStatus(Phase.NO_PICTURE), True, Status.NO_PICTURE),
        *(
            (
                PipelineStatus(Phase.STOPPED, stop_reason=reason),
                False,
                {StopReason.USER: Status.IDLE, StopReason.TV_OFF: Status.TV_OFF}.get(
                    reason, Status.ERROR
                ),
            )
            for reason in StopReason
        ),
        # Switched on again (or restarted for a new area) before the new
        # pipeline's first report: not the old stop.
        (PipelineStatus(Phase.STOPPED, stop_reason=StopReason.USER), True, "starting"),
        (
            PipelineStatus(Phase.STOPPED, stop_reason=StopReason.TV_OFF),
            True,
            "starting",
        ),
    ],
)
def test_the_value_for_each_status(
    pipeline: PipelineStatus | None, syncing: bool, expected: Status
) -> None:
    assert status_of(pipeline, syncing) == expected


async def test_idle_before_the_first_start(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    await setup(hass)
    state = hass.states.get(STATUS)
    assert state.state == "idle"
    assert "stop_reason" not in state.attributes
    assert state.attributes["options"] == [
        "idle",
        "starting",
        "streaming",
        "black_picture",
        "no_picture",
        "tv_off",
        "error",
    ]
    assert state.attributes["device_class"] == SensorDeviceClass.ENUM


async def test_every_phase_while_syncing_then_idle_after_switching_off(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    await setup(hass)
    await switch(hass, SERVICE_TURN_ON)
    assert status(hass) == "streaming"
    report(hass, PipelineStatus(Phase.STREAMING, channels=1, black_picture=True))
    assert status(hass) == "black_picture"
    report(hass, PipelineStatus(Phase.STREAMING, channels=1))
    assert status(hass) == "streaming"
    report(hass, PipelineStatus(Phase.RETRYING, channels=1))
    assert status(hass) == "starting"
    report(hass, PipelineStatus(Phase.STREAMING, channels=1))
    report(hass, PipelineStatus(Phase.NO_PICTURE))
    assert status(hass) == "no_picture"
    report(hass, PipelineStatus(Phase.STARTING))
    assert status(hass) == "starting"

    await switch(hass, SERVICE_TURN_OFF)
    state = hass.states.get(STATUS)
    assert state.state == "idle"
    assert "stop_reason" not in state.attributes


async def test_starting_until_the_lights_are_claimed(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    await setup(hass)
    seen = status_changes(hass)
    await switch(hass, SERVICE_TURN_ON)
    assert seen == ["starting", "streaming"]
    await switch(hass, SERVICE_TURN_OFF)


async def test_tv_off_with_its_reason(hass: HomeAssistant, fakes: FakeAdapters) -> None:
    await setup(hass)
    await switch(hass, SERVICE_TURN_ON)
    FakePipeline.instances[-1].finish(StopReason.TV_OFF)
    await settle(hass)
    state = hass.states.get(STATUS)
    assert state.state == "tv_off"
    assert state.attributes["stop_reason"] == "tv_off"


@pytest.mark.parametrize(
    "reason",
    [StopReason.OUTPUT_LOST, StopReason.OUTPUT_NOT_FOUND, StopReason.AUTH_FAILED],
)
async def test_an_error_with_its_reason(
    hass: HomeAssistant, fakes: FakeAdapters, reason: StopReason
) -> None:
    await setup(hass)
    FakePipeline.start_error = reason
    await switch(hass, SERVICE_TURN_ON)
    state = hass.states.get(STATUS)
    assert state.state == "error"
    assert state.attributes["stop_reason"] == reason.value

    await switch(hass, SERVICE_TURN_ON)  # the next start clears it
    state = hass.states.get(STATUS)
    assert state.state == "streaming"
    assert "stop_reason" not in state.attributes
    await switch(hass, SERVICE_TURN_OFF)


async def test_a_refused_start_shows_idle(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    """The TV is not set up: no pipeline runs and the repair issue says why."""
    await setup(hass)
    await switch(hass, SERVICE_TURN_ON)
    FakePipeline.instances[-1].finish(StopReason.TV_OFF)
    await settle(hass)
    fakes.source_missing = True
    await switch(hass, SERVICE_TURN_ON)
    state = hass.states.get(STATUS)
    assert state.state == "idle"
    assert "stop_reason" not in state.attributes


async def test_a_new_area_while_syncing_shows_starting_not_idle(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    await setup(hass)
    await switch(hass, SERVICE_TURN_ON)
    seen = status_changes(hass)
    await select(hass, AREA, "Other area")
    assert len(FakePipeline.instances) == 2  # restarted
    assert seen == ["starting", "streaming"]
    await switch(hass, SERVICE_TURN_OFF)


async def test_states_are_written_only_when_they_change(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    entry = await setup(hass)
    await enable_rate(hass, entry)
    await switch(hass, SERVICE_TURN_ON)
    written: list[str] = []

    @callback
    def count(event: Event) -> None:
        written.append(event.data["entity_id"])

    @callback
    def every(data: Any) -> bool:
        return True

    hass.bus.async_listen(EVENT_STATE_CHANGED, count)
    hass.bus.async_listen(EVENT_STATE_REPORTED, count, event_filter=every)
    # Once a second while streaming: the same status, a rate that rounds the same.
    for rate in (3.41, 3.43, 3.38, 3.36):
        report(hass, PipelineStatus(Phase.STREAMING, 1, captures_per_second=rate))
    await hass.async_block_till_done()
    assert written == [RATE]  # unknown, then 3.4 once; nothing else
    assert hass.states.get(RATE).state == "3.4"
    await switch(hass, SERVICE_TURN_OFF)


# The captures per second


async def test_the_captures_per_second_are_disabled_by_default(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    entry = await setup(hass)
    registry = er.async_get(hass)
    rate = registry.async_get(RATE)
    assert rate.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert rate.entity_category is EntityCategory.DIAGNOSTIC
    assert rate.unique_id == f"{entry.entry_id}_captures_per_second"
    assert hass.states.get(RATE) is None


async def test_the_captures_per_second_once_enabled(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    entry = await setup(hass)
    await enable_rate(hass, entry)
    state = hass.states.get(RATE)
    assert state.state == STATE_UNKNOWN  # never measured
    assert state.attributes["unit_of_measurement"] == "captures/s"
    assert "state_class" not in state.attributes

    await switch(hass, SERVICE_TURN_ON)
    assert hass.states.get(RATE).state == STATE_UNKNOWN  # not measured yet
    report(hass, PipelineStatus(Phase.STREAMING, 1, captures_per_second=4.06))
    assert hass.states.get(RATE).state == "4.1"
    # A retry clears the measurement until the new stream is measured (#105).
    report(hass, PipelineStatus(Phase.RETRYING, 1, captures_per_second=4.06))
    assert hass.states.get(RATE).state == STATE_UNKNOWN
    report(hass, PipelineStatus(Phase.STREAMING, channels=1))
    assert hass.states.get(RATE).state == STATE_UNKNOWN
    report(hass, PipelineStatus(Phase.STREAMING, 1, captures_per_second=3.0))
    assert hass.states.get(RATE).state == "3.0"
    report(hass, PipelineStatus(Phase.NO_PICTURE))
    assert hass.states.get(RATE).state == STATE_UNKNOWN
    report(hass, PipelineStatus(Phase.STREAMING, 1, captures_per_second=3.0))
    # After a stop the pipeline keeps the last measurement; nothing is captured.
    await switch(hass, SERVICE_TURN_OFF)
    assert hass.states.get(RATE).state == STATE_UNKNOWN


async def test_the_real_pipeline_end_to_end(
    hass: HomeAssistant, adapters: FakeAdapters
) -> None:
    """Switch on, measured rate, a dark scene, switch off."""
    entry = await setup(hass, {**OPTIONS, "update_rate": "maximum"})
    await enable_rate(hass, entry)
    await switch(hass, SERVICE_TURN_ON)
    await wait_for(lambda: status(hass) == "streaming")
    await wait_for(lambda: hass.states.get(RATE).state != STATE_UNKNOWN, limit=3)
    assert float(hass.states.get(RATE).state) > 0
    adapters.sources[-1].default = solid(DARK)
    await wait_for(lambda: status(hass) == "black_picture")
    await switch(hass, SERVICE_TURN_OFF)
    assert status(hass) == "idle"
    await asyncio.sleep(0)


# Names, translations and ids


@pytest.mark.parametrize(
    ("language", "names"),
    [
        ("en", {STATUS: "Status", RATE: "Capture rate"}),
        ("nl", {STATUS: "Status", RATE: "Leessnelheid"}),
    ],
)
async def test_names_in_home_assistants_language(
    hass: HomeAssistant,
    fakes: FakeAdapters,
    language: str,
    names: dict[str, str],
) -> None:
    hass.config.language = language
    entry = await setup(hass)
    registry = er.async_get(hass)
    by_key = {
        entity.unique_id.removeprefix(entry.entry_id): entity
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    for entity_id, key in ((STATUS, "_status"), (RATE, "_captures_per_second")):
        entity = by_key[key]
        assert entity.has_entity_name
        assert entity.translation_key == key[1:]
        assert entity.original_name == names[entity_id]
    await enable_rate(hass, entry)
    rate = registry.async_get(by_key["_captures_per_second"].entity_id)
    # Home Assistant takes a translated unit from English in every language.
    unit = hass.states.get(rate.entity_id).attributes["unit_of_measurement"]
    assert unit == "captures/s"


@pytest.mark.parametrize("language", ["en", "nl"])
async def test_every_value_and_reason_is_translated(
    hass: HomeAssistant, language: str
) -> None:
    translations = await async_get_translations(hass, language, "entity", {DOMAIN})
    prefix = f"component.{DOMAIN}.entity.sensor.status"
    for value in Status:
        assert translations[f"{prefix}.state.{value}"]
    for reason in StopReason:
        assert translations[f"{prefix}.state_attributes.stop_reason.state.{reason}"]
    texts = json.loads((TRANSLATIONS / f"{language}.json").read_text())
    states = texts["entity"]["sensor"]["status"]["state"]
    assert set(states) == {value.value for value in Status}
