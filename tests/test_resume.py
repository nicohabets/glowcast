"""Continue syncing after Home Assistant restarts (#183).

The switch stores whether Glowcast synced when Home Assistant stopped; after
the restart it starts again by itself, but only when the picture source is
on. A restart is the restore cache of HA's test framework; the stop is what
HA really does (cancel the background tasks while stopping).
"""

import asyncio

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    EVENT_STATE_CHANGED,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import CoreState, Event, HomeAssistant, State, callback
from homeassistant.helpers import restore_state as rs
from pytest_homeassistant_custom_component.common import (
    mock_restore_cache_with_extra_data,
)

from custom_components.glowcast import runtime
from custom_components.glowcast.core.pipeline import StopReason
from custom_components.glowcast.core.ports import (
    SourceNotSupported,
    SourceUnavailable,
)

from .fakes import FakeAdapters, FakePipeline, wait_for
from .test_hardware_check import FakeProbe, output_probe, source_probe  # noqa: F401
from .test_init import (  # noqa: F401
    ENTITY,
    issues,
    settle,
    setup,
    state,
    stop_home_assistant,
    switch,
    tv_device,
)

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


@pytest.mark.usefixtures("quick")
async def test_stays_off_when_the_tv_cannot_be_reached(
    hass: HomeAssistant, fakes: FakeAdapters, caplog: pytest.LogCaptureFixture
) -> None:
    """As webOS does when the TV is off: start() raises."""
    fakes.source_start_error = SourceUnavailable("the TV is off")
    restored(hass, True)
    await setup(hass)
    await wait_for(lambda: "did not continue" in caplog.text)
    assert state(hass) == STATE_OFF
    assert FakePipeline.instances == []
    assert fakes.log.count("source.start") == fakes.log.count("source.close") > 1


@pytest.mark.usefixtures("quick")
async def test_stays_off_when_the_screen_is_locked(
    hass: HomeAssistant, fakes: FakeAdapters, caplog: pytest.LogCaptureFixture
) -> None:
    """As the screen helper does for a locked screen: it answers, no picture."""
    fakes.source_picture = False
    restored(hass, True)
    await setup(hass)
    await wait_for(lambda: "did not continue" in caplog.text)
    assert state(hass) == STATE_OFF
    assert FakePipeline.instances == []
    assert fakes.sources[0].captures == 1


@pytest.mark.usefixtures("quick")
async def test_not_looked_for_while_its_integration_is_not_loaded(
    hass: HomeAssistant, fakes: FakeAdapters, caplog: pytest.LogCaptureFixture
) -> None:
    fakes.source_missing = True
    restored(hass, True)
    await setup(hass)
    await wait_for(lambda: "did not continue" in caplog.text)
    assert fakes.sources == []
    assert state(hass) == STATE_OFF


async def test_switching_before_home_assistant_has_started_ends_it(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    restored(hass, True)
    hass.set_state(CoreState.starting)
    await setup(hass)
    await switch(hass, SERVICE_TURN_ON)  # a startup automation, say
    await switch(hass, SERVICE_TURN_OFF)
    hass.set_state(CoreState.running)
    hass.bus.async_fire("homeassistant_started")
    await settle(hass)
    assert state(hass) == STATE_OFF
    assert len(FakePipeline.instances) == 1  # only the one switched on


@pytest.mark.usefixtures("quick")
async def test_an_unexpected_error_counts_as_off(
    hass: HomeAssistant, fakes: FakeAdapters, caplog: pytest.LogCaptureFixture
) -> None:
    fakes.source_start_error = RuntimeError("a bug in a source")
    restored(hass, True)
    await setup(hass)
    await wait_for(lambda: "did not continue" in caplog.text)
    assert fakes.log.count("source.start") > 1  # looked again after the error
    assert state(hass) == STATE_OFF


@pytest.mark.usefixtures("quick")
async def test_a_refusing_tv_is_started_and_stops_with_its_repair_issue(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    """A TV that refuses the screenshot call (for example after a firmware
    update while Home Assistant was down) counts as on: the normal start
    ends on the refusal after the startup grace, with the source type's
    repair issue, instead of looking again for minutes (#253)."""
    fakes.source_refusal = SourceNotSupported("refused", not_permitted=True)
    # Stands in for the real start's grace ending on the refusal.
    FakePipeline.start_error = StopReason.SOURCE_NOT_PERMITTED
    states: list[str] = []

    @callback
    def changed(event: Event) -> None:
        if event.data["entity_id"] == ENTITY:
            states.append(event.data["new_state"].state)

    hass.bus.async_listen(EVENT_STATE_CHANGED, changed)
    restored(hass, True)
    entry = await setup(hass)
    await wait_for(lambda: issues(hass, entry) == {"webos_not_permitted"})
    await settle(hass)
    assert fakes.sources[0].captures == 1  # looked once, not for minutes
    assert fakes.log[:3] == ["source.start", "source.close", "source.start"]
    assert len(FakePipeline.instances) == 1
    assert states[-2:] == [STATE_ON, STATE_OFF]
    status = entry.runtime_data.status
    assert status is not None
    assert status.stop_reason is StopReason.SOURCE_NOT_PERMITTED
    assert stored_resume(hass) is False  # a stop by itself is not resumed


# Captures at once (#269)


async def test_a_switch_on_ends_a_look_first(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    """A look in progress is closed before the pipeline connects, and no
    look follows."""
    fakes.source_gate = asyncio.Event()
    restored(hass, True)
    await setup(hass)
    await wait_for(lambda: fakes.log == ["source.start"])  # the look waits
    await switch(hass, SERVICE_TURN_ON)
    assert fakes.log == [
        "source.start",
        "source.close",
        "source.start",
        "output.start",
    ]
    fakes.source_gate.set()
    await settle(hass)
    assert fakes.log.count("source.start") == 2
    assert len(FakePipeline.instances) == 1
    assert state(hass) == STATE_ON
    await switch(hass, SERVICE_TURN_OFF)


async def test_a_switch_off_ends_a_look(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    fakes.source_gate = asyncio.Event()
    restored(hass, True)
    await setup(hass)
    await wait_for(lambda: fakes.log == ["source.start"])
    await switch(hass, SERVICE_TURN_OFF)
    assert fakes.log == ["source.start", "source.close"]
    fakes.source_gate.set()
    await settle(hass)
    assert FakePipeline.instances == []
    assert state(hass) == STATE_OFF


@pytest.mark.usefixtures("quick")
async def test_an_unload_ends_a_look(hass: HomeAssistant, fakes: FakeAdapters) -> None:
    """The look's connection is closed, and no look or start follows (#282)."""
    fakes.source_gate = asyncio.Event()
    restored(hass, True)
    entry = await setup(hass)
    await wait_for(lambda: fakes.log == ["source.start"])  # the look waits
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert fakes.log == ["source.start", "source.close"]
    fakes.source_gate.set()
    await asyncio.sleep(0.05)  # several intervals
    await settle(hass)
    assert fakes.log == ["source.start", "source.close"]
    assert FakePipeline.instances == []
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_home_assistant_stopping_ends_a_resume_that_waits_for_a_test(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    """Home Assistant stops in its real order while the resume's start waits
    for a light test's release: its cancelling of the resume goes through
    instead of being taken for the test's end (#282). The test's lights are
    released, and nothing claims them after."""
    fakes.source_gate = asyncio.Event()
    fakes.output_stop_gate = asyncio.Event()
    restored(hass, True)
    entry = await setup(hass)
    try:
        await wait_for(lambda: fakes.log == ["source.start"])  # the look waits
        await entry.runtime_data.async_test_lights()
        await wait_for(lambda: fakes.log == ["source.start", "output.start"])
        [resume] = [
            task
            for task in hass._background_tasks
            if isinstance(task, asyncio.Task) and task.get_name().endswith(" resume")
        ]
        fakes.source_gate.set()  # the TV is on: the resume starts
        await wait_for(lambda: fakes.log[-1] == "source.close")
        await asyncio.sleep(0.01)  # the start now waits for the test's release
        assert entry.runtime_data._transition.locked()
        stop_home_assistant(hass)
        await asyncio.sleep(0.01)
        assert resume.cancelled()
        fakes.output_stop_gate.set()
        await settle(hass)
        assert fakes.log == [
            "source.start",
            "output.start",
            "source.close",
            "output.stop",
            "output.close",
        ]
        assert FakePipeline.instances == []
    finally:
        fakes.source_gate.set()
        fakes.output_stop_gate.set()
        hass.set_state(CoreState.running)


@pytest.mark.usefixtures("quick")
async def test_no_look_while_a_check_measures(
    hass: HomeAssistant,
    fakes: FakeAdapters,
    source_probe: FakeProbe,  # noqa: F811
    output_probe: FakeProbe,  # noqa: F811
) -> None:
    """The check captures from the source; a look waits until it is done."""
    restored(hass, True)
    hass.set_state(CoreState.starting)
    entry = await setup(hass)
    source_probe.release = asyncio.Event()
    await entry.runtime_data.async_hardware_check()
    await wait_for(lambda: len(source_probe.calls) == 1)
    assert source_probe.calls[0]["measure"] is True
    hass.set_state(CoreState.running)
    hass.bus.async_fire("homeassistant_started")
    await asyncio.sleep(0.05)  # several intervals
    assert fakes.log == []
    source_probe.release.set()
    await wait_for(lambda: state(hass) == STATE_ON)
    assert len(FakePipeline.instances) == 1
    await switch(hass, SERVICE_TURN_OFF)


@pytest.mark.usefixtures("quick")
async def test_a_check_that_measures_ends_a_look_first(
    hass: HomeAssistant,
    fakes: FakeAdapters,
    source_probe: FakeProbe,  # noqa: F811
    output_probe: FakeProbe,  # noqa: F811
) -> None:
    """The look is closed before the check captures; the next one follows
    after the check."""
    fakes.source_gate = asyncio.Event()
    restored(hass, True)
    entry = await setup(hass)
    await wait_for(lambda: fakes.log == ["source.start"])  # the look waits
    source_probe.release = asyncio.Event()
    await entry.runtime_data.async_hardware_check()
    await wait_for(lambda: len(source_probe.calls) == 1)
    assert source_probe.calls[0]["measure"] is True
    assert fakes.log == ["source.start", "source.close"]
    await asyncio.sleep(0.05)  # several intervals
    assert fakes.log == ["source.start", "source.close"]
    fakes.source_gate.set()
    source_probe.release.set()
    await wait_for(lambda: state(hass) == STATE_ON)
    assert len(FakePipeline.instances) == 1
    await switch(hass, SERVICE_TURN_OFF)
