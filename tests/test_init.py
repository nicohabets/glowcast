"""The switch and the pipeline's lifecycle in Home Assistant.

Most tests use a fake pipeline for exact control over its lifecycle; the last
ones run the real pipeline behind the switch, with the shared fakes as TV and
lights.
"""

import asyncio
import dataclasses
import re
from collections.abc import Generator
from typing import Any

import pytest
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import (
    ATTR_ENTITY_ID,
    EVENT_HOMEASSISTANT_STOP,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_get_persistent_notifications,
)

from custom_components.glowcast.const import (
    DEFAULT_BRIGHTNESS,
    DEFAULT_INTENSITY,
    DEFAULT_UPDATE_RATE,
    DOMAIN,
    REAUTH_PART,
    REAUTH_SOURCE,
)
from custom_components.glowcast.core.pipeline import (
    Phase,
    PipelineStatus,
    Settings,
    StopReason,
    UpdateRate,
)
from custom_components.glowcast.outputs import OUTPUTS
from custom_components.glowcast.sources import SOURCES

from .fakes import CLEANUP, FakeAdapters, FakePipeline, wait_for

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

TV_ID = "tv-unique-id"
BRIDGE_ID = "bridge-unique-id"
ENTITY = "switch.glowcast_living_room_tv"
OPTIONS = {
    "output": {"type": "hue_entertainment", "area_id": "area"},
    "brightness": 60,
    "intensity": 120,
    "update_rate": "medium",
}


@pytest.fixture(autouse=True)
def tv_device(hass: HomeAssistant) -> Generator[dr.DeviceEntry]:
    """The TV as HA's webostv integration registers it; never really set up."""
    hass.config.components.update({"webostv", "hue"})
    tv = MockConfigEntry(
        domain="webostv",
        title="Living room TV",
        unique_id=TV_ID,
        state=ConfigEntryState.LOADED,
    )
    tv.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=tv.entry_id, identifiers={("webostv", TV_ID)}, name="TV"
    )
    yield device
    tv.mock_state(hass, ConfigEntryState.NOT_LOADED)


async def settle(hass: HomeAssistant) -> None:
    """Let the pipeline's background task run; HA does not wait for it."""
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


async def switch(hass: HomeAssistant, service: str) -> None:
    await hass.services.async_call(
        "switch", service, {ATTR_ENTITY_ID: ENTITY}, blocking=True
    )
    await settle(hass)


def state(hass: HomeAssistant) -> str:
    return hass.states.get(ENTITY).state


def issues(hass: HomeAssistant, entry: MockConfigEntry) -> set[str]:
    suffix = f"_{entry.entry_id}"
    return {
        issue_id.removesuffix(suffix)
        for (domain, issue_id) in ir.async_get(hass).issues
        if domain == DOMAIN
    }


# The entity


async def test_entity_and_device(
    hass: HomeAssistant, fakes: FakeAdapters, tv_device: dr.DeviceEntry
) -> None:
    entry = await setup(hass)
    entity = er.async_get(hass).async_get(ENTITY)
    assert entity is not None
    assert entity.unique_id == entry.entry_id
    device = dr.async_get(hass).async_get(entity.device_id)
    assert device.name == "Glowcast Living room TV"
    assert device.via_device_id == tv_device.id
    assert state(hass) == STATE_OFF


async def test_a_source_without_a_device(
    hass: HomeAssistant, fakes: FakeAdapters, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A source type may have no device to link Glowcast's device to (#66)."""
    monkeypatch.setitem(
        SOURCES,
        "webos",
        dataclasses.replace(
            SOURCES["webos"],
            device_identifier=lambda data: None,
            device_id=lambda hass, data: None,
        ),
    )
    entry = await setup(hass)
    [device] = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert device.name == "Glowcast Living room TV"
    assert device.via_device_id is None
    await switch(hass, SERVICE_TURN_ON)
    assert state(hass) == STATE_ON
    await switch(hass, SERVICE_TURN_OFF)


async def test_the_switch_keeps_its_ids_and_device(
    hass: HomeAssistant, fakes: FakeAdapters, tv_device: dr.DeviceEntry
) -> None:
    """As in v0.1, so automations, dashboards and history keep working."""
    entry = await setup(hass)
    [entity] = [
        entity
        for entity in er.async_entries_for_config_entry(
            er.async_get(hass), entry.entry_id
        )
        if entity.domain == "switch"
    ]
    assert entity.entity_id == ENTITY
    assert entity.unique_id == entry.entry_id
    assert entity.platform == DOMAIN
    assert entity.original_name is None
    assert entity.translation_key == "sync"
    [device] = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert entity.device_id == device.id
    assert device.identifiers == {(DOMAIN, entry.entry_id)}
    assert device.name == "Glowcast Living room TV"
    assert device.entry_type is dr.DeviceEntryType.SERVICE
    assert device.via_device_id == tv_device.id


async def test_off_after_a_restart(hass: HomeAssistant, fakes: FakeAdapters) -> None:
    entry = await setup(hass)
    await switch(hass, SERVICE_TURN_ON)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await settle(hass)
    assert state(hass) == STATE_OFF
    assert len(FakePipeline.instances) == 1  # never started by itself


# On and off


async def test_switch_on_starts_the_pipeline(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    await setup(hass)
    await switch(hass, SERVICE_TURN_ON)
    assert state(hass) == STATE_ON
    [pipeline] = FakePipeline.instances
    assert pipeline.settings.brightness == 60
    assert pipeline.settings.intensity == 120
    assert pipeline.settings.update_rate is UpdateRate.MEDIUM
    assert pipeline.runner == hass.async_add_executor_job
    assert fakes.log == ["source.start", "output.start"]
    await switch(hass, SERVICE_TURN_OFF)


async def test_switch_off_releases_the_lights(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    await setup(hass)
    await switch(hass, SERVICE_TURN_ON)
    await hass.services.async_call(
        "switch", SERVICE_TURN_OFF, {ATTR_ENTITY_ID: ENTITY}, blocking=True
    )
    # Released before the service call returns.
    assert fakes.log[-3:] == ["output.stop", "output.close", "source.close"]
    assert state(hass) == STATE_OFF


async def test_switch_on_while_the_tv_is_off(
    hass: HomeAssistant, fakes: FakeAdapters, caplog: pytest.LogCaptureFixture
) -> None:
    await setup(hass)
    FakePipeline.start_error = StopReason.TV_OFF
    await switch(hass, SERVICE_TURN_ON)
    assert state(hass) == STATE_OFF
    assert "output.start" not in fakes.log
    assert "the picture source is off or not reachable" in caplog.text


# Settings


@pytest.mark.parametrize(
    ("key", "default"),
    [
        ("brightness", Settings(DEFAULT_BRIGHTNESS, 120, UpdateRate.MEDIUM)),
        ("intensity", Settings(60, DEFAULT_INTENSITY, UpdateRate.MEDIUM)),
        ("update_rate", Settings(60, 120, UpdateRate(DEFAULT_UPDATE_RATE))),
    ],
)
async def test_a_missing_setting_starts_with_its_default(
    hass: HomeAssistant, fakes: FakeAdapters, key: str, default: Settings
) -> None:
    """An entry from an older version may lack a key added later."""
    await setup(hass, {k: v for k, v in OPTIONS.items() if k != key})
    await switch(hass, SERVICE_TURN_ON)
    assert state(hass) == STATE_ON
    [pipeline] = FakePipeline.instances
    assert pipeline.settings == default
    await switch(hass, SERVICE_TURN_OFF)


async def test_new_settings_while_syncing_reach_the_pipeline_without_a_restart(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    """Brightness, intensity and update rate: no new claim of the lights (#13)."""
    entry = await setup(hass)
    await switch(hass, SERVICE_TURN_ON)
    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            "brightness": 30,
            "intensity": 0,
            "update_rate": "maximum",
        },
    )
    await settle(hass)

    [pipeline] = FakePipeline.instances
    assert pipeline.updates == [Settings(30, 0, UpdateRate.MAXIMUM)]
    assert fakes.log == ["source.start", "output.start"]
    assert state(hass) == STATE_ON
    await switch(hass, SERVICE_TURN_OFF)


async def test_new_data_while_syncing_does_not_restart(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    """New keys from reauth are read at the next start; the stream keeps going."""
    entry = await setup(hass)
    await switch(hass, SERVICE_TURN_ON)
    hass.config_entries.async_update_entry(
        entry,
        data={**entry.data, "bridge": {**entry.data["bridge"], "app_key": "new"}},
    )
    await settle(hass)
    [pipeline] = FakePipeline.instances
    assert fakes.log == ["source.start", "output.start"]
    assert state(hass) == STATE_ON
    await switch(hass, SERVICE_TURN_OFF)


async def test_a_new_output_while_syncing_restarts_only_the_pipeline(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    """What follows the TV changed: the pipeline restarts, the entry stays."""
    entry = await setup(hass)
    await switch(hass, SERVICE_TURN_ON)
    states: list[str] = []
    hass.bus.async_listen(
        "state_changed",
        lambda event: (
            states.append(event.data["new_state"].state)
            if event.data["entity_id"] == ENTITY
            else None
        ),
    )
    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            "output": {"type": "hue_entertainment", "area_id": "other"},
            "brightness": 30,
        },
    )
    await settle(hass)

    first, second = FakePipeline.instances
    assert fakes.log.count("output.stop") == 1  # the first one was released
    assert first.updates == []
    assert second.settings.brightness == 30
    assert state(hass) == STATE_ON
    assert STATE_OFF not in states  # the switch stayed on
    assert entry.state is ConfigEntryState.LOADED
    await switch(hass, SERVICE_TURN_OFF)


async def test_switching_off_during_a_restart_wins(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    entry = await setup(hass)
    await switch(hass, SERVICE_TURN_ON)
    data = entry.runtime_data
    restart = asyncio.ensure_future(data.async_restart())
    await asyncio.sleep(0)  # the restart is stopping the old pipeline
    await data.async_stop()
    await restart
    await settle(hass)
    assert len(FakePipeline.instances) == 1  # not started again
    assert state(hass) == STATE_OFF


async def test_a_restart_keeps_the_new_pipelines_status(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    entry = await setup(hass)
    await switch(hass, SERVICE_TURN_ON)
    data = entry.runtime_data
    await data.async_restart()
    await settle(hass)
    first, _ = FakePipeline.instances
    assert data.status == PipelineStatus(Phase.STREAMING, channels=1)
    # A late report from the old pipeline must not overwrite the new status.
    first.on_status(FakePipeline.stopped(StopReason.OUTPUT_LOST))
    await settle(hass)
    assert data.status == PipelineStatus(Phase.STREAMING, channels=1)
    assert state(hass) == STATE_ON
    await switch(hass, SERVICE_TURN_OFF)


async def test_new_settings_while_off_start_nothing(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    entry = await setup(hass)
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "brightness": 30}
    )
    await settle(hass)
    assert FakePipeline.instances == []
    assert state(hass) == STATE_OFF


# Stopping by itself


@pytest.mark.parametrize(
    "reason",
    [
        StopReason.TV_OFF,
        StopReason.OUTPUT_BUSY,
        StopReason.OUTPUT_LOST,
        StopReason.OUTPUT_UNAVAILABLE,
        StopReason.SOURCE_UNAVAILABLE,
    ],
)
async def test_stopping_by_itself_turns_the_switch_off(
    hass: HomeAssistant,
    fakes: FakeAdapters,
    reason: StopReason,
    caplog: pytest.LogCaptureFixture,
) -> None:
    entry = await setup(hass)
    await switch(hass, SERVICE_TURN_ON)
    FakePipeline.instances[-1].finish(reason)
    await settle(hass)
    assert state(hass) == STATE_OFF
    assert fakes.log[-1] == "source.close"
    assert issues(hass, entry) == set()
    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN) == []
    assert caplog.text.count("Glowcast Living room TV stopped") == 1


async def test_the_last_status_is_kept_until_the_next_start(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    entry = await setup(hass)
    data = entry.runtime_data
    assert data.status is None  # never started
    await switch(hass, SERVICE_TURN_ON)
    assert data.status == PipelineStatus(Phase.STREAMING, channels=1)
    FakePipeline.instances[-1].finish(StopReason.OUTPUT_UNAVAILABLE)
    await settle(hass)
    assert data.status == FakePipeline.stopped(StopReason.OUTPUT_UNAVAILABLE)
    await switch(hass, SERVICE_TURN_ON)
    assert data.status == PipelineStatus(Phase.STREAMING, channels=1)
    await switch(hass, SERVICE_TURN_OFF)
    assert data.status == FakePipeline.stopped(StopReason.USER)


async def test_switching_off_during_a_restart_keeps_the_stop(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    entry = await setup(hass)
    await switch(hass, SERVICE_TURN_ON)
    data = entry.runtime_data
    restart = asyncio.ensure_future(data.async_restart())
    await asyncio.sleep(0)
    await data.async_stop()
    await restart
    await settle(hass)
    assert data.status == FakePipeline.stopped(StopReason.USER)


async def test_a_stop_without_a_reason_still_turns_the_switch_off(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    await setup(hass)
    await switch(hass, SERVICE_TURN_ON)
    pipeline = FakePipeline.instances[-1]
    pipeline.on_status(PipelineStatus(Phase.STOPPED))
    await settle(hass)
    assert state(hass) == STATE_OFF
    pipeline.stop()  # the fake itself still runs
    await settle(hass)


async def test_auth_failed_starts_reauth(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    await setup(hass)
    await switch(hass, SERVICE_TURN_ON)
    FakePipeline.instances[-1].finish(StopReason.AUTH_FAILED)
    await settle(hass)
    assert state(hass) == STATE_OFF
    [flow] = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert flow["context"]["source"] == SOURCE_REAUTH


async def test_the_source_reauth_reaches_the_flow_with_its_part(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    """Home Assistant's own reauth start passes the part on: the flow runs
    the source's step (the webOS source has none), never the output's
    pairing."""
    await setup(hass)
    await switch(hass, SERVICE_TURN_ON)
    FakePipeline.instances[-1].finish(StopReason.SOURCE_AUTH_FAILED)
    await settle(hass)
    assert state(hass) == STATE_OFF
    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN) == []


async def test_the_source_rejecting_its_key_starts_its_reauth(
    hass: HomeAssistant, fakes: FakeAdapters, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not the output's reauth: the source pairs again (#110)."""
    entry = await setup(hass)
    started: list[dict[str, Any] | None] = []
    monkeypatch.setattr(
        type(entry),
        "async_start_reauth",
        lambda self, hass, context=None, data=None: started.append(data),
    )
    await switch(hass, SERVICE_TURN_ON)
    FakePipeline.instances[-1].finish(StopReason.SOURCE_AUTH_FAILED)
    await settle(hass)
    assert state(hass) == STATE_OFF
    assert started == [{REAUTH_PART: REAUTH_SOURCE}]


@pytest.mark.parametrize(
    ("reason", "issue"),
    [
        (StopReason.OUTPUT_TOO_MANY_CHANNELS, "too_many_channels"),
        (StopReason.OUTPUT_NOT_FOUND, "area_not_found"),
    ],
)
async def test_settings_problems_raise_a_repair_issue_until_a_start_works(
    hass: HomeAssistant, fakes: FakeAdapters, reason: StopReason, issue: str
) -> None:
    entry = await setup(hass)
    FakePipeline.start_error = reason
    await switch(hass, SERVICE_TURN_ON)
    assert state(hass) == STATE_OFF
    assert issues(hass, entry) == {issue}

    await switch(hass, SERVICE_TURN_ON)
    assert state(hass) == STATE_ON
    assert issues(hass, entry) == set()
    await switch(hass, SERVICE_TURN_OFF)


async def test_a_successful_retry_clears_the_repair_issues(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    entry = await setup(hass)
    await switch(hass, SERVICE_TURN_ON)
    entry.runtime_data._raise_issue("area_not_found")
    pipeline = FakePipeline.instances[-1]
    pipeline.on_status(PipelineStatus(Phase.RETRYING, channels=1))
    await settle(hass)
    assert issues(hass, entry) == {"area_not_found"}
    pipeline.on_status(PipelineStatus(Phase.STREAMING, channels=1))
    await settle(hass)
    assert issues(hass, entry) == set()
    await switch(hass, SERVICE_TURN_OFF)


async def test_a_new_capture_rate_is_not_a_new_claim(
    hass: HomeAssistant, fakes: FakeAdapters, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The captures per second arrive once a second; issues are cleared once."""
    entry = await setup(hass)
    await switch(hass, SERVICE_TURN_ON)
    cleared: list[None] = []
    monkeypatch.setattr(
        entry.runtime_data, "async_remove_issues", lambda: cleared.append(None)
    )
    pipeline = FakePipeline.instances[-1]
    for rate in (3.9, 4.0, 4.1):
        pipeline.on_status(
            PipelineStatus(Phase.STREAMING, channels=1, captures_per_second=rate)
        )
    assert cleared == []
    assert entry.runtime_data.status.captures_per_second == 4.1
    await switch(hass, SERVICE_TURN_OFF)


@pytest.mark.parametrize(
    ("missing", "issue"),
    [("source_missing", "source_missing"), ("output_missing", "output_missing")],
)
async def test_tv_or_bridge_gone_from_ha(
    hass: HomeAssistant, fakes: FakeAdapters, missing: str, issue: str
) -> None:
    entry = await setup(hass)
    setattr(fakes, missing, True)
    await switch(hass, SERVICE_TURN_ON)
    assert state(hass) == STATE_OFF
    assert FakePipeline.instances == []  # nothing was started
    assert issues(hass, entry) == {issue}

    assert entry.runtime_data.status is None  # no pipeline ran

    setattr(fakes, missing, False)
    await switch(hass, SERVICE_TURN_ON)
    assert state(hass) == STATE_ON
    assert issues(hass, entry) == set()
    await switch(hass, SERVICE_TURN_OFF)


async def test_tv_removed_while_syncing(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    entry = await setup(hass)
    await switch(hass, SERVICE_TURN_ON)
    fakes.source_missing = True
    FakePipeline.instances[-1].finish(StopReason.TV_OFF)
    await settle(hass)
    assert issues(hass, entry) == {"source_missing"}


async def test_removing_the_entry_removes_its_issues(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    entry = await setup(hass)
    FakePipeline.start_error = StopReason.OUTPUT_NOT_FOUND
    await switch(hass, SERVICE_TURN_ON)
    assert issues(hass, entry) == {"area_not_found"}
    await hass.config_entries.async_remove(entry.entry_id)
    await settle(hass)
    assert issues(hass, entry) == set()


def notices(hass: HomeAssistant) -> dict[str, dict[str, Any]]:
    """Glowcast's persistent notifications, by id."""
    return {
        notification_id: notification
        for notification_id, notification in (
            async_get_persistent_notifications(hass).items()
        )
        if notification_id.startswith(f"{DOMAIN}_")
    }


async def test_removing_the_entry_tells_how_to_remove_the_bridge_app(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    entry = await setup(hass)
    await hass.config_entries.async_remove(entry.entry_id)
    await settle(hass)
    notice_id = f"{DOMAIN}_bridge_app_left_{entry.entry_id}"
    assert list(notices(hass)) == [notice_id]
    notice = notices(hass)[notice_id]
    assert notice["title"] == (
        "Glowcast Living room TV: remove its app from the Hue bridge"
    )
    assert "Settings → Apps → Network-based" in notice["message"]
    assert "**glowcast**" in notice["message"]


async def test_the_removal_notice_comes_from_the_output_type(
    hass: HomeAssistant, fakes: FakeAdapters, monkeypatch: pytest.MonkeyPatch
) -> None:
    removed: list[str] = []

    async def async_removed(hass: HomeAssistant, entry: MockConfigEntry) -> None:
        removed.append(entry.entry_id)

    monkeypatch.setitem(
        OUTPUTS,
        "hue_entertainment",
        dataclasses.replace(OUTPUTS["hue_entertainment"], async_removed=async_removed),
    )
    entry = await setup(hass)
    await hass.config_entries.async_remove(entry.entry_id)
    await settle(hass)
    assert removed == [entry.entry_id]
    assert notices(hass) == {}


async def test_no_removal_notice_without_a_paired_bridge(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    """Only an entry that paired with a bridge left an app on it."""
    entry = await setup(hass)
    hass.config_entries.async_update_entry(entry, data={"source": entry.data["source"]})
    await hass.config_entries.async_remove(entry.entry_id)
    await settle(hass)
    assert notices(hass) == {}


async def test_the_removal_notice_in_dutch(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    hass.config.language = "nl"
    entry = await setup(hass)
    await hass.config_entries.async_remove(entry.entry_id)
    await settle(hass)
    (notice,) = notices(hass).values()
    assert notice["title"] == (
        "Glowcast Living room TV: verwijder ook de app van de Hue-bridge"
    )
    assert "Instellingen → Toepassingen → Netwerk-gebaseerd" in notice["message"]


async def test_reload_and_stop_show_no_removal_notice(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    entry = await setup(hass)
    await switch(hass, SERVICE_TURN_ON)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await settle(hass)
    await switch(hass, SERVICE_TURN_ON)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await settle(hass)
    assert fakes.log[-3:] == ["output.stop", "output.close", "source.close"]
    assert notices(hass) == {}


async def test_two_removed_entries_give_two_notices(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    first = await setup(hass)
    second = MockConfigEntry(
        domain=DOMAIN,
        title="Bedroom TV",
        unique_id="other-tv-unique-id",
        data=dict(first.data),
        options=dict(first.options),
    )
    second.add_to_hass(hass)
    await hass.config_entries.async_remove(first.entry_id)
    await hass.config_entries.async_remove(second.entry_id)
    await settle(hass)
    titles = {notice_id: notice["title"] for notice_id, notice in notices(hass).items()}
    assert titles == {
        f"{DOMAIN}_bridge_app_left_{first.entry_id}": (
            "Glowcast Living room TV: remove its app from the Hue bridge"
        ),
        f"{DOMAIN}_bridge_app_left_{second.entry_id}": (
            "Glowcast Bedroom TV: remove its app from the Hue bridge"
        ),
    }


async def test_the_removal_notice_has_no_secrets(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    entry = await setup(hass)
    bridge = entry.data["bridge"]
    await hass.config_entries.async_remove(entry.entry_id)
    await settle(hass)
    (notice,) = notices(hass).values()
    text = f"{notice['title']}\n{notice['message']}"
    for value in (bridge["app_key"], bridge["client_key"], BRIDGE_ID, TV_ID):
        assert value not in text
    assert entry.entry_id not in text
    assert not re.search(r"\b\d{1,3}(\.\d{1,3}){3}\b", text)
    assert "://" not in text


# Unload and Home Assistant stopping


async def test_unload_while_syncing_releases_the_lights(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    entry = await setup(hass)
    await switch(hass, SERVICE_TURN_ON)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert fakes.log[-3:] == ["output.stop", "output.close", "source.close"]
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_home_assistant_shutdown_cancels_and_releases(
    hass: HomeAssistant, fakes: FakeAdapters, caplog: pytest.LogCaptureFixture
) -> None:
    """What HA really does: cancel background tasks, then fire the stop event."""
    entry = await setup(hass)
    await switch(hass, SERVICE_TURN_ON)
    hass.set_state(CoreState.stopping)
    entry.runtime_data._task.cancel("Home Assistant is stopping")
    await settle(hass)
    assert fakes.log[-3:] == ["output.stop", "output.close", "source.close"]
    assert state(hass) == STATE_OFF
    assert "stopped: Home Assistant is stopping" in caplog.text
    hass.set_state(CoreState.running)


async def test_home_assistant_stop_releases_the_lights(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    await setup(hass)
    await switch(hass, SERVICE_TURN_ON)
    # HA waits for the stop listener (a normal task, not a background one)
    # before it shuts down; the lights are released by the time it finishes.
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await settle(hass)
    assert fakes.log[-3:] == ["output.stop", "output.close", "source.close"]
    assert state(hass) == STATE_OFF


# The real pipeline behind the switch


async def test_the_real_pipeline_behind_the_switch(
    hass: HomeAssistant, adapters: FakeAdapters
) -> None:
    await setup(hass)
    await switch(hass, SERVICE_TURN_ON)
    [output] = adapters.outputs
    await wait_for(lambda: output.sent)
    assert state(hass) == STATE_ON
    # A red picture at brightness 60 %.
    assert output.rgb_at(-1) == pytest.approx((0.60, 0.0, 0.0), abs=0.005)

    await switch(hass, SERVICE_TURN_OFF)
    assert adapters.log == ["source.start", "output.start", *CLEANUP]
    assert state(hass) == STATE_OFF


async def test_the_real_pipeline_stopping_by_itself_turns_the_switch_off(
    hass: HomeAssistant, adapters: FakeAdapters, caplog: pytest.LogCaptureFixture
) -> None:
    await setup(hass)
    await switch(hass, SERVICE_TURN_ON)
    [source], [output] = adapters.sources, adapters.outputs
    await wait_for(lambda: output.sent)

    source.on = False  # the TV reports that it is off
    await wait_for(lambda: state(hass) == STATE_OFF)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert adapters.log == ["source.start", "output.start", *CLEANUP]
    assert "stopped: the picture source is off or not reachable" in caplog.text
