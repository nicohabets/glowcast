"""Continue syncing after Home Assistant restarts (#183).

The switch stores whether Glowcast synced when Home Assistant stopped; after
the restart it starts again by itself, but only when the picture source is
on. A restart is the restore cache of HA's test framework; the stop is what
HA really does (cancel the background tasks while stopping).
"""

import pytest
from homeassistant.const import SERVICE_TURN_OFF, SERVICE_TURN_ON, STATE_OFF, STATE_ON
from homeassistant.core import CoreState, HomeAssistant, State
from homeassistant.helpers import restore_state as rs
from pytest_homeassistant_custom_component.common import (
    mock_restore_cache_with_extra_data,
)

from custom_components.glowcast import runtime
from custom_components.glowcast.core.pipeline import StopReason

from .fakes import FakeAdapters, FakePipeline, wait_for
from .test_init import ENTITY, settle, setup, state, switch, tv_device  # noqa: F401

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


def restored(hass: HomeAssistant, resume: bool) -> None:
    """The switch's state as stored when Home Assistant stopped."""
    mock_restore_cache_with_extra_data(
        hass, [(State(ENTITY, STATE_OFF), {"resume": resume})]
    )


def stored_resume(hass: HomeAssistant) -> bool | None:
    """What the switch would store if Home Assistant stopped now."""
    for stored in rs.async_get(hass).async_get_stored_states():
        if stored.state.entity_id == ENTITY:
            return stored.extra_data.as_dict()["resume"]
    return None


@pytest.fixture
def quick(monkeypatch: pytest.MonkeyPatch) -> None:
    """Look for the source every 10 ms for 0.3 s instead of minutes."""
    monkeypatch.setattr(runtime, "RESUME_INTERVAL", 0.01)
    monkeypatch.setattr(runtime, "RESUME_WINDOW", 0.3)


# What the switch stores


async def test_syncing_when_home_assistant_stops_is_stored(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    entry = await setup(hass)
    await switch(hass, SERVICE_TURN_ON)
    assert stored_resume(hass) is False  # the dump every 15 minutes
    hass.set_state(CoreState.stopping)
    # Before and after the pipeline's stop: the dump may come at either.
    assert stored_resume(hass) is True
    entry.runtime_data._task.cancel("Home Assistant is stopping")
    await settle(hass)
    assert state(hass) == STATE_OFF
    assert stored_resume(hass) is True
    hass.set_state(CoreState.running)


async def test_off_when_home_assistant_stops_is_stored(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    await setup(hass)
    await switch(hass, SERVICE_TURN_ON)
    await switch(hass, SERVICE_TURN_OFF)  # by the user
    hass.set_state(CoreState.stopping)
    assert stored_resume(hass) is False
    hass.set_state(CoreState.running)


async def test_a_stop_by_itself_is_not_resumed(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    await setup(hass)
    await switch(hass, SERVICE_TURN_ON)
    FakePipeline.instances[0].finish(StopReason.TV_OFF)
    await settle(hass)
    hass.set_state(CoreState.stopping)
    assert stored_resume(hass) is False
    hass.set_state(CoreState.running)


# After the restart


async def test_resumes_when_the_tv_is_on(
    hass: HomeAssistant, fakes: FakeAdapters, caplog: pytest.LogCaptureFixture
) -> None:
    restored(hass, True)
    await setup(hass)
    await wait_for(lambda: state(hass) == STATE_ON)
    assert len(FakePipeline.instances) == 1
    # Looked for the TV once (start and close), then the normal start.
    assert fakes.log[:3] == ["source.start", "source.close", "source.start"]
    assert "continues after Home Assistant's restart" in caplog.text
    await switch(hass, SERVICE_TURN_OFF)


@pytest.mark.usefixtures("quick")
async def test_stays_off_when_it_was_off(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    restored(hass, False)
    await setup(hass)
    await settle(hass)
    assert state(hass) == STATE_OFF
    assert fakes.log == []  # the TV is not even looked for
    assert FakePipeline.instances == []


@pytest.mark.usefixtures("quick")
async def test_stays_off_when_the_tv_is_off(
    hass: HomeAssistant, fakes: FakeAdapters, caplog: pytest.LogCaptureFixture
) -> None:
    fakes.source_on = False
    restored(hass, True)
    await setup(hass)
    await wait_for(lambda: "did not continue" in caplog.text)
    assert state(hass) == STATE_OFF
    assert FakePipeline.instances == []
    assert fakes.log.count("source.start") == fakes.log.count("source.close") > 1
    # The automation starts it when the TV turns on, as before.
    fakes.source_on = True
    await switch(hass, SERVICE_TURN_ON)
    assert state(hass) == STATE_ON
    await switch(hass, SERVICE_TURN_OFF)


async def test_resumes_when_the_tv_turns_on_soon(
    hass: HomeAssistant, fakes: FakeAdapters, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runtime, "RESUME_INTERVAL", 0.01)
    fakes.source_on = False
    restored(hass, True)
    await setup(hass)
    await wait_for(lambda: fakes.log.count("source.close") >= 2)
    fakes.source_on = True
    await wait_for(lambda: state(hass) == STATE_ON)
    assert len(FakePipeline.instances) == 1
    await switch(hass, SERVICE_TURN_OFF)


async def test_switching_off_meanwhile_ends_it(
    hass: HomeAssistant, fakes: FakeAdapters, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runtime, "RESUME_INTERVAL", 0.01)
    fakes.source_on = False
    restored(hass, True)
    await setup(hass)
    await wait_for(lambda: fakes.log.count("source.close") >= 2)
    await switch(hass, SERVICE_TURN_OFF)
    looks = fakes.log.count("source.start")
    fakes.source_on = True
    await settle(hass)
    assert fakes.log.count("source.start") == looks
    assert state(hass) == STATE_OFF
    assert FakePipeline.instances == []


async def test_waits_until_home_assistant_has_started(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    restored(hass, True)
    hass.set_state(CoreState.starting)
    await setup(hass)
    assert fakes.log == []
    hass.set_state(CoreState.running)
    hass.bus.async_fire("homeassistant_started")
    await wait_for(lambda: state(hass) == STATE_ON)
    await switch(hass, SERVICE_TURN_OFF)
