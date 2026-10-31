"""The Test lights button in Home Assistant (#182).

The fake TV and lights from fakes.py; the light test itself is the real one,
with short steps.
"""

import asyncio
import logging

import pytest
from homeassistant.const import (
    ATTR_ENTITY_ID,
    EVENT_HOMEASSISTANT_STOP,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.glowcast.core import light_test
from custom_components.glowcast.core.models import Channel
from custom_components.glowcast.core.ports import OutputBusy

from .fakes import FakeAdapters, FakeOutput, FakePipeline, wait_for
from .test_init import (  # noqa: F401
    BRIDGE_ID,
    issues,
    settle,
    setup,
    state,
    stop_home_assistant,
    switch,
    tv_device,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

BUTTON = "button.glowcast_living_room_tv_test_lights"
STATUS = "sensor.glowcast_living_room_tv_status"
RELEASED = ["output.start", "output.stop", "output.close"]


@pytest.fixture
def short_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    """Steps of 0.1 s, so a whole test takes 0.2 s."""
    monkeypatch.setattr(light_test, "STEP", 0.1)


async def press(hass: HomeAssistant) -> None:
    await hass.services.async_call(
        "button", "press", {ATTR_ENTITY_ID: BUTTON}, blocking=True
    )
    await settle(hass)


async def test_the_button_on_the_device(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    entry = await setup(hass)
    entity = er.async_get(hass).async_get(BUTTON)
    assert entity.unique_id == f"{entry.entry_id}_test_lights"
    assert entity.translation_key == "test_lights"
    assert entity.entity_category == "config"
    switch_entity = er.async_get(hass).async_get("switch.glowcast_living_room_tv")
    assert entity.device_id == switch_entity.device_id
    assert hass.states.get(BUTTON).name == "Glowcast Living room TV Test lights"


@pytest.mark.usefixtures("short_steps")
async def test_off_the_lights_are_claimed_for_the_test_only(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    await setup(hass)
    status = hass.states.get(STATUS).state
    await press(hass)
    await wait_for(lambda: fakes.log == RELEASED)
    [output] = fakes.outputs
    assert output.sent  # the test colours
    # No picture source, no pipeline, and the switch and status unchanged.
    assert "source.start" not in fakes.log
    assert FakePipeline.instances == []
    assert state(hass) == STATE_OFF
    assert hass.states.get(STATUS).state == status


@pytest.mark.usefixtures("short_steps")
async def test_off_the_test_uses_the_brightness(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    fakes.channels = [Channel(id="1", name="left", x=-1.0, z=0.0)]
    await setup(hass)  # brightness 60
    await press(hass)
    await wait_for(lambda: fakes.log == RELEASED)
    [output] = fakes.outputs
    [color] = output.sent[0][1]
    assert color.rgb == pytest.approx((0.6, 0.0, 0.0), abs=0.01)  # left: red


async def test_a_second_press_during_the_test_does_nothing(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    entry = await setup(hass)
    await press(hass)
    await press(hass)
    assert fakes.log == ["output.start"]
    assert len(fakes.outputs) == 1
    await hass.config_entries.async_unload(entry.entry_id)


async def test_busy_does_nothing_and_says_why(
    hass: HomeAssistant,
    fakes: FakeAdapters,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    build = fakes.output

    def busy() -> FakeOutput:
        output = build()
        output.start_errors = [OutputBusy("another app streams")]
        return output

    monkeypatch.setattr(fakes, "output", busy)
    await setup(hass)
    with caplog.at_level(logging.WARNING):
        await press(hass)
        await wait_for(lambda: fakes.log == RELEASED)
    [output] = fakes.outputs
    assert output.sent == []
    assert (
        "Glowcast Living room TV could not test the lights: "
        "another app is streaming to the lights"
    ) in caplog.text
    assert state(hass) == STATE_OFF


async def test_busy_for_another_glowcast_setup_names_it_in_the_log(
    hass: HomeAssistant,
    fakes: FakeAdapters,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The busy check of the output type (#299) names the other setup in the
    log line; no repair issue, as for any light test that cannot start."""
    build = fakes.output

    def busy() -> FakeOutput:
        output = build()
        output.start_errors = [OutputBusy("another Glowcast setup streams")]
        return output

    monkeypatch.setattr(fakes, "output", busy)
    entry = await setup(hass)
    hass.data["glowcast_hue_busy_by"] = {(BRIDGE_ID, "app-key"): "Bedroom TV"}
    with caplog.at_level(logging.WARNING):
        await press(hass)
        await wait_for(lambda: fakes.log == RELEASED)
    assert (
        "Glowcast Living room TV could not test the lights: "
        "Glowcast Bedroom TV is streaming to the same Hue bridge"
    ) in caplog.text
    assert issues(hass, entry) == set()
    assert state(hass) == STATE_OFF


async def test_lights_not_set_up_does_nothing_and_says_why(
    hass: HomeAssistant, fakes: FakeAdapters, caplog: pytest.LogCaptureFixture
) -> None:
    await setup(hass)
    fakes.output_missing = True
    with caplog.at_level(logging.WARNING):
        await press(hass)
    assert fakes.log == []
    assert "Glowcast Living room TV cannot test the lights" in caplog.text


async def test_switching_on_during_the_test_releases_it_first(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    """The test's lights are released before syncing claims them."""
    await setup(hass)
    await press(hass)
    assert fakes.log == ["output.start"]
    await switch(hass, SERVICE_TURN_ON)
    assert fakes.log == [*RELEASED, "source.start", "output.start"]
    assert state(hass) == STATE_ON
    [pipeline] = FakePipeline.instances
    assert pipeline.tests == 0


async def test_a_press_while_switching_on_ends_no_second_test(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    """A press while the switch-on waits for the test's release starts no new
    test: only the pipeline claims the lights after it (review of #182)."""
    entry = await setup(hass)
    await press(hass)
    start = hass.async_create_task(entry.runtime_data.async_start())
    await asyncio.sleep(0)  # the start now waits for the test's release
    await entry.runtime_data.async_test_lights()
    await start
    await settle(hass)
    assert fakes.log == [*RELEASED, "source.start", "output.start"]
    assert len(fakes.outputs) == 2  # the test's and the pipeline's


async def test_switching_off_while_a_switch_on_waits_for_the_test_wins(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    """The last of start and stop wins, also while the start waits for the
    test's release (#269)."""
    entry = await setup(hass)
    await press(hass)
    start = hass.async_create_task(entry.runtime_data.async_start())
    await asyncio.sleep(0)  # the start now waits for the test's release
    await entry.runtime_data.async_stop()
    assert fakes.log == RELEASED  # the stop waited for the release
    await start
    await settle(hass)
    assert FakePipeline.instances == []
    assert state(hass) == STATE_OFF


async def test_unloading_while_a_switch_on_waits_for_the_test_starts_nothing(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    """No pipeline streams after an unload with nothing left to stop it
    (#269)."""
    entry = await setup(hass)
    await press(hass)
    start = hass.async_create_task(entry.runtime_data.async_start())
    await asyncio.sleep(0)  # the start now waits for the test's release
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert fakes.log == RELEASED
    await start
    await settle(hass)
    assert FakePipeline.instances == []
    assert fakes.log == RELEASED


async def test_two_switch_ons_during_the_test_both_wait_for_its_release(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    """A second switch-on does not claim the lights while the first one
    still waits for the test's release (#269)."""
    entry = await setup(hass)
    await press(hass)
    first = hass.async_create_task(entry.runtime_data.async_start())
    second = hass.async_create_task(entry.runtime_data.async_start())
    await first
    await second
    await settle(hass)
    assert fakes.log == [*RELEASED, "source.start", "output.start"]
    assert len(FakePipeline.instances) == 1
    assert state(hass) == STATE_ON


async def test_unloading_during_the_test_releases_the_lights(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    entry = await setup(hass)
    await press(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert fakes.log == RELEASED


async def test_home_assistant_stopping_during_the_test_releases_the_lights(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    await setup(hass)
    await press(hass)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await settle(hass)
    await wait_for(lambda: fakes.log == RELEASED)


@pytest.mark.parametrize("ending", ["unload", "home_assistant_stops"])
async def test_no_light_test_after_unload_or_home_assistant_stopping(
    hass: HomeAssistant, fakes: FakeAdapters, ending: str
) -> None:
    """A press after the lights were released claims nothing: no stop would
    follow to release them again (#282)."""
    entry = await setup(hass)
    data = entry.runtime_data
    if ending == "unload":
        assert await hass.config_entries.async_unload(entry.entry_id)
    else:
        stop_home_assistant(hass)
        await settle(hass)
    await data.async_test_lights()
    await settle(hass)
    assert fakes.log == []
    hass.set_state(CoreState.running)


async def test_while_syncing_the_running_pipeline_shows_the_test(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    await setup(hass)
    await switch(hass, SERVICE_TURN_ON)
    await press(hass)
    [pipeline] = FakePipeline.instances
    assert pipeline.tests == 1
    # No new claim, and the switch stays on.
    assert fakes.log == ["source.start", "output.start"]
    assert state(hass) == STATE_ON
    pipeline.testing = True  # a test shows now
    await press(hass)
    assert pipeline.tests == 1


async def test_while_syncing_with_the_lights_released_it_says_so(
    hass: HomeAssistant,
    fakes: FakeAdapters,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await setup(hass)
    await switch(hass, SERVICE_TURN_ON)
    [pipeline] = FakePipeline.instances

    async def refused() -> bool:
        return False

    monkeypatch.setattr(pipeline, "async_test", refused)
    with caplog.at_level(logging.INFO):
        await press(hass)
    assert "cannot test the lights now" in caplog.text
    assert len(fakes.outputs) == 1


async def test_the_real_pipeline_shows_the_test_while_syncing(
    hass: HomeAssistant, fakes: FakeAdapters, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The button, the runtime and the real pipeline: no second claim."""
    from custom_components.glowcast import runtime
    from custom_components.glowcast.core.pipeline import Pipeline

    monkeypatch.setattr(runtime, "Pipeline", Pipeline)
    entry = await setup(hass)
    await switch(hass, SERVICE_TURN_ON)
    await wait_for(lambda: fakes.outputs and fakes.outputs[0].sent)
    await asyncio.sleep(0.1)
    await press(hass)
    data = entry.runtime_data
    assert data._pipeline is not None and data._pipeline.testing
    assert fakes.log.count("output.start") == 1
    await switch(hass, "turn_off")
