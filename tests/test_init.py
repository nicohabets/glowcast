"""The switch and the pipeline's lifecycle in Home Assistant, with a fake pipeline."""

import asyncio
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
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.glowcast as glowcast
from custom_components.glowcast.const import DOMAIN
from custom_components.glowcast.core.models import Channel, ChannelColor
from custom_components.glowcast.core.pipeline import StopReason, UpdateRate
from custom_components.glowcast.outputs import OUTPUTS, OutputType
from custom_components.glowcast.sources import SOURCES, SourceType

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

TV_ID = "tv-unique-id"
BRIDGE_ID = "bridge-unique-id"
ENTITY = "switch.glowcast_living_room_tv"


class FakeSource:
    def __init__(self, log: list[str]) -> None:
        self.log = log

    async def start(self) -> None:
        self.log.append("source.start")

    def is_on(self) -> bool:
        return True

    async def capture(self) -> Any:
        raise NotImplementedError

    async def close(self) -> None:
        self.log.append("source.close")


class FakeOutput:
    max_rate_hz = 25.0

    def __init__(self, log: list[str]) -> None:
        self.log = log

    async def start(self) -> list[Channel]:
        self.log.append("output.start")
        return []

    def send(self, colors: list[ChannelColor]) -> None:
        pass

    async def check(self) -> None:
        pass

    async def stop(self) -> None:
        await asyncio.sleep(0)  # releasing takes a moment
        self.log.append("output.stop")

    async def close(self) -> None:
        self.log.append("output.close")


class FakePipeline:
    """Uses the source and output like the real pipeline, without timing.

    `start_error` makes the next pipeline stop before the lights are claimed;
    finish() makes a running one stop by itself.
    """

    instances: list["FakePipeline"] = []
    start_error: StopReason | None = None

    def __init__(
        self,
        source: FakeSource,
        output: Any,
        settings: Any,
        runner: Any,
        clock: Any,
        on_stopped: Any,
    ) -> None:
        self.source = source
        self.output = output
        self.settings = settings
        self.runner = runner
        self.on_stopped = on_stopped
        self.reason = StopReason.USER
        self._stop = asyncio.Event()
        FakePipeline.instances.append(self)

    async def run(self) -> None:
        try:
            await self.source.start()
            if (error := FakePipeline.start_error) is not None:
                FakePipeline.start_error = None
                self.reason = error
            else:
                await self.output.start()
                await self._stop.wait()
        except asyncio.CancelledError:
            # Like the real pipeline: release, report USER, re-raise.
            await asyncio.shield(self._release())
            self.on_stopped(StopReason.USER)
            raise
        await self._release()
        self.on_stopped(self.reason)

    async def _release(self) -> None:
        await self.output.stop()
        await self.output.close()
        await self.source.close()

    def stop(self) -> None:
        self._stop.set()

    def finish(self, reason: StopReason) -> None:
        self.reason = reason
        self._stop.set()


class Fakes:
    def __init__(self) -> None:
        self.log: list[str] = []
        self.source_missing = False
        self.output_missing = False


@pytest.fixture
def fakes(monkeypatch: pytest.MonkeyPatch) -> Fakes:
    """Fake source, output and pipeline; the real adapters are never built."""
    fakes = Fakes()
    FakePipeline.instances = []
    FakePipeline.start_error = None
    monkeypatch.setattr(glowcast, "Pipeline", FakePipeline)
    monkeypatch.setitem(
        SOURCES,
        "webos",
        SourceType(
            create=lambda hass, data, options: FakeSource(fakes.log),
            is_missing=lambda hass, data: fakes.source_missing,
            device_identifier=lambda data: ("webostv", data["source"]["unique_id"]),
        ),
    )
    monkeypatch.setitem(
        OUTPUTS,
        "hue_entertainment",
        OutputType(
            create=lambda hass, data, options: FakeOutput(fakes.log),
            is_missing=lambda hass, data: fakes.output_missing,
        ),
    )
    return fakes


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


async def setup(hass: HomeAssistant) -> MockConfigEntry:
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
        options={
            "output": {"type": "hue_entertainment", "area_id": "area"},
            "brightness": 60,
            "intensity": 120,
            "update_rate": "medium",
        },
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
    hass: HomeAssistant, fakes: Fakes, tv_device: dr.DeviceEntry
) -> None:
    entry = await setup(hass)
    entity = er.async_get(hass).async_get(ENTITY)
    assert entity is not None
    assert entity.unique_id == entry.entry_id
    device = dr.async_get(hass).async_get(entity.device_id)
    assert device.name == "Glowcast Living room TV"
    assert device.via_device_id == tv_device.id
    assert state(hass) == STATE_OFF


async def test_off_after_a_restart(hass: HomeAssistant, fakes: Fakes) -> None:
    entry = await setup(hass)
    await switch(hass, SERVICE_TURN_ON)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await settle(hass)
    assert state(hass) == STATE_OFF
    assert len(FakePipeline.instances) == 1  # never started by itself


# On and off


async def test_switch_on_starts_the_pipeline(hass: HomeAssistant, fakes: Fakes) -> None:
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
    hass: HomeAssistant, fakes: Fakes
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
    hass: HomeAssistant, fakes: Fakes, caplog: pytest.LogCaptureFixture
) -> None:
    await setup(hass)
    FakePipeline.start_error = StopReason.TV_OFF
    await switch(hass, SERVICE_TURN_ON)
    assert state(hass) == STATE_OFF
    assert "output.start" not in fakes.log
    assert "the TV is off or not reachable" in caplog.text


# Settings


async def test_new_settings_while_syncing_restart_only_the_pipeline(
    hass: HomeAssistant, fakes: Fakes
) -> None:
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
        entry, options={**entry.options, "brightness": 30}
    )
    await settle(hass)

    first, second = FakePipeline.instances
    assert fakes.log.count("output.stop") == 1  # the first one was released
    assert second.settings.brightness == 30
    assert state(hass) == STATE_ON
    assert STATE_OFF not in states  # the switch stayed on
    assert entry.state is ConfigEntryState.LOADED
    await switch(hass, SERVICE_TURN_OFF)


async def test_new_settings_while_off_start_nothing(
    hass: HomeAssistant, fakes: Fakes
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
    fakes: Fakes,
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


async def test_auth_failed_starts_reauth(hass: HomeAssistant, fakes: Fakes) -> None:
    await setup(hass)
    await switch(hass, SERVICE_TURN_ON)
    FakePipeline.instances[-1].finish(StopReason.AUTH_FAILED)
    await settle(hass)
    assert state(hass) == STATE_OFF
    [flow] = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert flow["context"]["source"] == SOURCE_REAUTH


@pytest.mark.parametrize(
    ("reason", "issue"),
    [
        (StopReason.OUTPUT_TOO_MANY_CHANNELS, "too_many_channels"),
        (StopReason.OUTPUT_NOT_FOUND, "area_not_found"),
    ],
)
async def test_settings_problems_raise_a_repair_issue_until_a_start_works(
    hass: HomeAssistant, fakes: Fakes, reason: StopReason, issue: str
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


@pytest.mark.parametrize(
    ("missing", "issue"),
    [("source_missing", "source_missing"), ("output_missing", "output_missing")],
)
async def test_tv_or_bridge_gone_from_ha(
    hass: HomeAssistant, fakes: Fakes, missing: str, issue: str
) -> None:
    entry = await setup(hass)
    setattr(fakes, missing, True)
    await switch(hass, SERVICE_TURN_ON)
    assert state(hass) == STATE_OFF
    assert FakePipeline.instances == []  # nothing was started
    assert issues(hass, entry) == {issue}

    setattr(fakes, missing, False)
    await switch(hass, SERVICE_TURN_ON)
    assert state(hass) == STATE_ON
    assert issues(hass, entry) == set()
    await switch(hass, SERVICE_TURN_OFF)


async def test_tv_removed_while_syncing(hass: HomeAssistant, fakes: Fakes) -> None:
    entry = await setup(hass)
    await switch(hass, SERVICE_TURN_ON)
    fakes.source_missing = True
    FakePipeline.instances[-1].finish(StopReason.TV_OFF)
    await settle(hass)
    assert issues(hass, entry) == {"source_missing"}


async def test_removing_the_entry_removes_its_issues(
    hass: HomeAssistant, fakes: Fakes
) -> None:
    entry = await setup(hass)
    FakePipeline.start_error = StopReason.OUTPUT_NOT_FOUND
    await switch(hass, SERVICE_TURN_ON)
    assert issues(hass, entry) == {"area_not_found"}
    await hass.config_entries.async_remove(entry.entry_id)
    await settle(hass)
    assert issues(hass, entry) == set()


# Unload and Home Assistant stopping


async def test_unload_while_syncing_releases_the_lights(
    hass: HomeAssistant, fakes: Fakes
) -> None:
    entry = await setup(hass)
    await switch(hass, SERVICE_TURN_ON)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert fakes.log[-3:] == ["output.stop", "output.close", "source.close"]
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_home_assistant_shutdown_cancels_and_releases(
    hass: HomeAssistant, fakes: Fakes, caplog: pytest.LogCaptureFixture
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
    hass: HomeAssistant, fakes: Fakes
) -> None:
    await setup(hass)
    await switch(hass, SERVICE_TURN_ON)
    # HA waits for the stop listener (a normal task, not a background one)
    # before it shuts down; the lights are released by the time it finishes.
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await settle(hass)
    assert fakes.log[-3:] == ["output.stop", "output.close", "source.close"]
    assert state(hass) == STATE_OFF
