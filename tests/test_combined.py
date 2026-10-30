"""An entertainment area and loose lights in one entry (#11).

The settings screen that combines them, the stored block, the entities of
both parts, a light in both, and how the runtime reacts to a part that is
dropped while the rest keeps running. The parts are fakes; the combined
output itself is tested in test_composite_output.py.
"""

import asyncio
import dataclasses
from typing import Any

import pytest
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID, SERVICE_TURN_OFF, SERVICE_TURN_ON
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.glowcast import runtime
from custom_components.glowcast.const import DOMAIN, REAUTH_PART
from custom_components.glowcast.core.models import Channel
from custom_components.glowcast.core.pipeline import (
    MonotonicClock,
    Phase,
    PipelineStatus,
    StopReason,
)
from custom_components.glowcast.core.ports import OutputBusy
from custom_components.glowcast.outputs import OUTPUTS
from custom_components.glowcast.sources import SOURCES

from .fakes import FakeOutput, FakePipeline, FakeSource, wait_for
from .test_config_flow import (  # noqa: F401 (fixtures)
    AREAS_URL,
    BRIDGE_ID,
    HOST,
    TV_ID,
    FakeBridge,
    area,
    bridge,
    fake_api,
    glowcast_entry,
    other_integrations,
)
from .test_ha_lights_flow import (  # noqa: F401 (fixtures)
    hue_bridge_entry,
    no_hue_lights,
    options,
    options_configure,
    save_settings,
)
from .test_ha_lights_output import add_light

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

SWITCH = "switch.glowcast_living_room_tv"
STATUS = "sensor.glowcast_living_room_tv_status"
AREA_SELECT = "select.glowcast_living_room_tv_entertainment_area"
LIGHTS_SELECT = "select.glowcast_living_room_tv_lights"


def combined(lamp: er.RegistryEntry, area_id: str = "area-tv") -> dict[str, Any]:
    return {
        "type": "combined",
        "parts": [
            {"type": "hue_entertainment", "area_id": area_id},
            {
                "type": "ha_lights",
                "follow": "lights",
                "lights": [{"entity": lamp.id, "x": -1.0, "z": 0.0}],
            },
        ],
    }


def combined_entry(hass: HomeAssistant, output: dict[str, Any]) -> MockConfigEntry:
    entry = glowcast_entry(hass)
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "output": output}
    )
    return entry


async def settle(hass: HomeAssistant) -> None:
    for _ in range(10):
        await hass.async_block_till_done()
        await asyncio.sleep(0)


async def switch(hass: HomeAssistant, service: str) -> None:
    await hass.services.async_call(
        "switch", service, {ATTR_ENTITY_ID: SWITCH}, blocking=True
    )
    await settle(hass)


def issues(hass: HomeAssistant, entry: MockConfigEntry) -> set[str]:
    suffix = f"_{entry.entry_id}"
    return {
        issue_id.removesuffix(suffix)
        for (domain, issue_id) in ir.async_get(hass).issues
        if domain == DOMAIN
    }


class Parts:
    """Fake outputs for both types; the real ones are never built."""

    def __init__(self) -> None:
        self.area_log: list[str] = []
        self.lights_log: list[str] = []
        self.areas: list[FakeOutput] = []
        self.lights: list[FakeOutput] = []
        self.area_missing = False
        self.area_start_errors: list[Any] = []

    def area(self) -> FakeOutput:
        output = FakeOutput(
            MonotonicClock(),
            self.area_log,
            channels=[Channel("0", "left", -1, 0), Channel("1", "right", 1, 0)],
        )
        output.start_errors = list(self.area_start_errors)
        self.areas.append(output)
        return output

    def loose(self) -> FakeOutput:
        output = FakeOutput(MonotonicClock(), self.lights_log)
        output.max_rate_hz = 4.0
        self.lights.append(output)
        return output


@pytest.fixture
def parts(monkeypatch: pytest.MonkeyPatch) -> Parts:
    fakes = Parts()
    monkeypatch.setitem(
        OUTPUTS,
        "hue_entertainment",
        dataclasses.replace(
            OUTPUTS["hue_entertainment"],
            create=lambda hass, data, options: fakes.area(),
            is_missing=lambda hass, data, options: fakes.area_missing,
        ),
    )
    monkeypatch.setitem(
        OUTPUTS,
        "ha_lights",
        dataclasses.replace(
            OUTPUTS["ha_lights"], create=lambda hass, data, options: fakes.loose()
        ),
    )
    monkeypatch.setitem(
        SOURCES,
        "webos",
        dataclasses.replace(
            SOURCES["webos"],
            create=lambda hass, data, options: FakeSource(
                MonotonicClock(), [], capture_time=0
            ),
            is_missing=lambda hass, data, options: False,
        ),
    )
    return fakes


@pytest.fixture
def fake_pipeline(monkeypatch: pytest.MonkeyPatch) -> Any:
    FakePipeline.instances = []
    FakePipeline.start_error = None
    monkeypatch.setattr(runtime, "Pipeline", FakePipeline)
    yield
    for pipeline in FakePipeline.instances:
        pipeline.stop()


# The settings screen


async def test_add_loose_lights_to_an_entry_with_an_area(hass: HomeAssistant) -> None:
    lamp = add_light(hass, "floor_lamp")
    entry = glowcast_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    bridge_block = dict(entry.data["bridge"])

    result = await options(hass, entry, "combined_options")
    # First the area, with the stored one chosen, then the loose lights.
    assert result["step_id"] == "area"
    schema = result["data_schema"].schema
    assert next(iter(schema)).default() == "area-tv"
    result = await options_configure(hass, result, {"area": "area-tv"})
    assert result["step_id"] == "ha_lights"
    result = await options_configure(hass, result, {"follow": "lights"})
    result = await options_configure(hass, result, {"lights": [lamp.entity_id]})
    result = await options_configure(hass, result, {"position": "left"})
    result = await save_settings(hass, result)
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["output"] == combined(lamp)
    assert entry.data["bridge"] == bridge_block
    # Another combination of types reloads the entry: both entities now.
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get(AREA_SELECT).state == "TV area"
    assert hass.states.get(LIGHTS_SELECT).state == "chosen_lights"

    # Again: both parts start from what is stored.
    result = await options(hass, entry, "combined_options")
    assert next(iter(result["data_schema"].schema)).default() == "area-tv"
    result = await options_configure(hass, result, {"area": "area-other"})
    assert result["step_id"] == "ha_lights"
    result = await options_configure(hass, result, {"follow": "lights"})
    assert next(iter(result["data_schema"].schema)).default() == [lamp.entity_id]
    result = await options_configure(hass, result, {"lights": [lamp.entity_id]})
    assert result["step_id"] == "ha_lights_position"
    result = await options_configure(hass, result, {"position": "left"})
    result = await save_settings(hass, result)
    await hass.async_block_till_done()
    assert entry.options["output"] == combined(lamp, "area-other")

    # Back to the area alone.
    result = await options(hass, entry, "hue_options")
    result = await options_configure(hass, result, {"area": "area-tv"})
    result = await save_settings(hass, result)
    await hass.async_block_till_done()
    assert entry.options["output"] == {
        "type": "hue_entertainment",
        "area_id": "area-tv",
    }
    assert er.async_get(hass).async_get(LIGHTS_SELECT) is None
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_loose_lights_entry_pairs_and_adds_an_area(hass: HomeAssistant) -> None:
    lamp = add_light(hass, "floor_lamp")
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Living room TV",
        unique_id=TV_ID,
        data={"source": {"type": "webos", "unique_id": TV_ID}},
        options={
            "output": {
                "type": "ha_lights",
                "follow": "lights",
                "lights": [{"entity": lamp.id, "x": 1.0, "z": 0.0}],
            },
            "brightness": 50,
        },
    )
    entry.add_to_hass(hass)
    result = await options(hass, entry, "combined_options")
    assert result["step_id"] == "bridge"
    result = await options_configure(hass, result, {"bridge": BRIDGE_ID})
    result = await options_configure(hass, result, {})
    await hass.async_block_till_done()
    result = await hass.config_entries.options.async_configure(result["flow_id"])
    assert result["step_id"] == "area"
    result = await options_configure(hass, result, {"area": "area-tv"})
    result = await options_configure(hass, result, {"follow": "lights"})
    result = await options_configure(hass, result, {"lights": [lamp.entity_id]})
    # The stored position is the default.
    assert next(iter(result["data_schema"].schema)).default() == "right"
    result = await options_configure(hass, result, {"position": "right"})
    result = await save_settings(hass, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.data["bridge"]["app_key"] == "new-app-key"
    assert entry.options["output"]["parts"][0] == {
        "type": "hue_entertainment",
        "area_id": "area-tv",
    }


def area_with(
    bridge: FakeBridge,  # noqa: F811
    *lights: er.RegistryEntry,
) -> None:
    """The bridge's TV area streams to these Hue lights."""
    bridge.areas = [
        {
            **area("area-tv", "TV area"),
            "light_services": [
                {"rid": light.unique_id, "rtype": "light"} for light in lights
            ],
        },
        area("area-other", "Other area"),
    ]


async def to_loose_lights_of_combined(
    hass: HomeAssistant, entry: MockConfigEntry, follow: str
) -> dict[str, Any]:
    result = await options(hass, entry, "combined_options")
    result = await options_configure(hass, result, {"area": "area-tv"})
    return await options_configure(hass, result, {"follow": follow})


async def test_a_hue_light_in_the_area_cannot_be_a_loose_light(
    hass: HomeAssistant,
    bridge: FakeBridge,  # noqa: F811
) -> None:
    in_area = add_light(hass, "tv_strip", "hue", hue_bridge_entry(hass))
    lamp = add_light(hass, "floor_lamp")
    area_with(bridge, in_area)
    entry = glowcast_entry(hass)

    result = await to_loose_lights_of_combined(hass, entry, "lights")
    result = await options_configure(
        hass, result, {"lights": [in_area.entity_id, lamp.entity_id]}
    )
    assert result["step_id"] == "ha_lights_lights"
    assert result["errors"] == {"base": "ha_lights_in_area"}
    assert result["description_placeholders"] == {"lights": "Tv Strip"}
    # Without it, it goes on.
    result = await options_configure(hass, result, {"lights": [lamp.entity_id]})
    assert result["step_id"] == "ha_lights_position"


async def test_an_area_or_group_with_a_light_of_the_area_is_refused(
    hass: HomeAssistant,
    bridge: FakeBridge,  # noqa: F811
) -> None:
    room = ar.async_get(hass).async_create("Living room")
    in_area = add_light(
        hass, "tv_strip", "hue", hue_bridge_entry(hass), area_id=room.id
    )
    add_light(hass, "floor_lamp", area_id=room.id)
    group = add_light(hass, "tv_room", "group", members=[in_area.entity_id])
    area_with(bridge, in_area)
    entry = glowcast_entry(hass)

    result = await to_loose_lights_of_combined(hass, entry, "area")
    result = await options_configure(hass, result, {"area": room.id})
    assert result["errors"] == {"base": "ha_lights_in_area"}
    assert result["description_placeholders"] == {"lights": "Tv Strip"}
    hass.config_entries.options.async_abort(result["flow_id"])

    result = await to_loose_lights_of_combined(hass, entry, "group")
    result = await options_configure(hass, result, {"group": group.id})
    assert result["errors"] == {"base": "ha_lights_in_area"}


async def test_an_unreachable_bridge_refuses_nothing(
    hass: HomeAssistant,
    bridge: FakeBridge,  # noqa: F811
    aioclient_mock: AiohttpClientMocker,
) -> None:
    in_area = add_light(hass, "tv_strip", "hue", hue_bridge_entry(hass))
    area_with(bridge, in_area)
    entry = glowcast_entry(hass)
    result = await options(hass, entry, "combined_options")
    result = await options_configure(hass, result, {"area": "area-tv"})
    bridge.reachable = False
    result = await options_configure(hass, result, {"follow": "lights"})
    result = await options_configure(hass, result, {"lights": [in_area.entity_id]})
    assert result["step_id"] == "ha_lights_position"


async def test_loose_lights_alone_are_not_checked_against_an_area(
    hass: HomeAssistant,
    bridge: FakeBridge,  # noqa: F811
) -> None:
    """Only a combination drives the area's lights twice."""
    in_area = add_light(hass, "tv_strip", "hue", hue_bridge_entry(hass))
    area_with(bridge, in_area)
    entry = glowcast_entry(hass)
    result = await options(hass, entry, "ha_lights_options")
    result = await options_configure(hass, result, {"follow": "lights"})
    result = await options_configure(hass, result, {"lights": [in_area.entity_id]})
    assert result["step_id"] != "ha_lights_lights"


async def test_the_entities_refuse_a_light_in_both(
    hass: HomeAssistant,
    bridge: FakeBridge,  # noqa: F811
) -> None:
    room = ar.async_get(hass).async_create("Living room")
    in_area = add_light(
        hass, "tv_strip", "hue", hue_bridge_entry(hass), area_id=room.id
    )
    lamp = add_light(hass, "floor_lamp")
    entry = combined_entry(hass, combined(lamp, "area-tv"))
    area_with(bridge, in_area)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    with pytest.raises(ServiceValidationError) as raised:
        await hass.services.async_call(
            "select",
            "select_option",
            {ATTR_ENTITY_ID: LIGHTS_SELECT, "option": "Living room"},
            blocking=True,
        )
    assert raised.value.translation_key == "lights_in_area"
    assert raised.value.translation_placeholders == {"lights": "Tv Strip"}
    assert entry.options["output"] == combined(lamp, "area-tv")

    # The area select stores inside the combined block.
    await hass.services.async_call(
        "select",
        "select_option",
        {ATTR_ENTITY_ID: AREA_SELECT, "option": "Other area"},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert entry.options["output"] == combined(lamp, "area-other")

    # With the lights in the area chosen as loose lights, the area is refused.
    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            "output": {
                "type": "combined",
                "parts": [
                    {"type": "hue_entertainment", "area_id": "area-other"},
                    {"type": "ha_lights", "follow": "area", "area_id": room.id},
                ],
            },
        },
    )
    await hass.async_block_till_done()
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "select",
            "select_option",
            {ATTR_ENTITY_ID: AREA_SELECT, "option": "TV area"},
            blocking=True,
        )
    assert await hass.config_entries.async_unload(entry.entry_id)


# The runtime, with fake parts


async def test_both_parts_follow_through_the_real_pipeline(
    hass: HomeAssistant, parts: Parts
) -> None:
    lamp = add_light(hass, "floor_lamp")
    entry = combined_entry(hass, combined(lamp))
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await switch(hass, SERVICE_TURN_ON)
    [area_part], [lights_part] = parts.areas, parts.lights
    await wait_for(lambda: len(area_part.sent) > 10 and lights_part.sent)
    # Each part gets its own channels, at its own rate.
    assert {c.channel_id for c in area_part.sent[-1][1]} == {"0", "1"}
    assert {c.channel_id for c in lights_part.sent[-1][1]} == {"1"}
    assert len(area_part.sent) > 3 * len(lights_part.sent)
    await switch(hass, SERVICE_TURN_OFF)
    assert parts.area_log[-2:] == ["output.stop", "output.close"]
    assert parts.lights_log[-2:] == ["output.stop", "output.close"]
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_a_part_that_cannot_start_is_shown_and_the_rest_runs(
    hass: HomeAssistant, parts: Parts
) -> None:
    lamp = add_light(hass, "floor_lamp")
    parts.area_start_errors = [OutputBusy("another app streams")]
    entry = combined_entry(hass, combined(lamp))
    assert await hass.config_entries.async_setup(entry.entry_id)
    await switch(hass, SERVICE_TURN_ON)
    await wait_for(lambda: parts.lights and parts.lights[0].sent)
    await wait_for(lambda: hass.states.get(STATUS).attributes.get("dropped_outputs"))
    state = hass.states.get(STATUS)
    assert state.state == "streaming"
    assert state.attributes["dropped_outputs"] == {"hue_entertainment": "output_busy"}
    assert hass.states.get(SWITCH).state == "on"
    await switch(hass, SERVICE_TURN_OFF)
    assert "dropped_outputs" not in hass.states.get(STATUS).attributes
    # The part that did not start is released too.
    assert parts.area_log == ["output.start", "output.stop", "output.close"]
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_a_missing_integration_drops_its_part(
    hass: HomeAssistant, parts: Parts, fake_pipeline: Any
) -> None:
    lamp = add_light(hass, "floor_lamp")
    parts.area_missing = True
    entry = combined_entry(hass, combined(lamp))
    assert await hass.config_entries.async_setup(entry.entry_id)
    await switch(hass, SERVICE_TURN_ON)
    assert hass.states.get(SWITCH).state == "on"
    assert issues(hass, entry) == {"output_missing"}
    [pipeline] = FakePipeline.instances
    assert list(pipeline.output.dropped_parts) == ["hue_entertainment"]
    assert parts.areas == []  # never built
    pipeline.on_status(
        PipelineStatus(
            Phase.STREAMING,
            channels=1,
            dropped_parts={"hue_entertainment": StopReason.OUTPUT_UNAVAILABLE},
        )
    )
    await settle(hass)
    assert issues(hass, entry) == {"output_missing"}  # kept while it is missing
    await switch(hass, SERVICE_TURN_OFF)


async def test_every_part_missing_does_not_start(
    hass: HomeAssistant,
    parts: Parts,
    fake_pipeline: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lamp = add_light(hass, "floor_lamp")
    parts.area_missing = True
    monkeypatch.setitem(
        OUTPUTS,
        "ha_lights",
        dataclasses.replace(
            OUTPUTS["ha_lights"], is_missing=lambda hass, data, options: True
        ),
    )
    entry = combined_entry(hass, combined(lamp))
    assert await hass.config_entries.async_setup(entry.entry_id)
    await switch(hass, SERVICE_TURN_ON)
    assert hass.states.get(SWITCH).state == "off"
    assert FakePipeline.instances == []
    assert issues(hass, entry) == {"output_missing"}


async def test_a_dropped_part_gets_its_own_follow_up(
    hass: HomeAssistant, parts: Parts, fake_pipeline: Any
) -> None:
    lamp = add_light(hass, "floor_lamp")
    entry = combined_entry(hass, combined(lamp))
    assert await hass.config_entries.async_setup(entry.entry_id)
    await switch(hass, SERVICE_TURN_ON)
    [pipeline] = FakePipeline.instances

    # The loose lights are gone: their repair issue, while the area runs on.
    dropped = {"ha_lights": StopReason.OUTPUT_NOT_FOUND}
    pipeline.on_status(PipelineStatus(Phase.STREAMING, 3, dropped_parts=dropped))
    await settle(hass)
    assert issues(hass, entry) == {"loose_lights_not_found"}
    assert hass.states.get(STATUS).attributes["dropped_outputs"] == {
        "ha_lights": "output_not_found"
    }
    # Later reports keep it, and raise it only once.
    pipeline.on_status(
        PipelineStatus(
            Phase.STREAMING, 3, captures_per_second=3.0, dropped_parts=dropped
        )
    )
    await settle(hass)
    assert issues(hass, entry) == {"loose_lights_not_found"}

    # The bridge rejects the key: reauth for the area, the switch stays on.
    dropped = {**dropped, "hue_entertainment": StopReason.AUTH_FAILED}
    pipeline.on_status(PipelineStatus(Phase.STREAMING, 3, dropped_parts=dropped))
    await settle(hass)
    [flow] = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert flow["context"]["source"] == SOURCE_REAUTH
    assert flow["step_id"] == "reauth_confirm"
    assert hass.states.get(SWITCH).state == "on"
    await switch(hass, SERVICE_TURN_OFF)
    hass.config_entries.flow.async_abort(flow["flow_id"])

    # The next start clears the issue once the lights are claimed.
    await switch(hass, SERVICE_TURN_ON)
    FakePipeline.instances[-1].on_status(PipelineStatus(Phase.STREAMING, 3))
    await settle(hass)
    assert issues(hass, entry) == set()
    await switch(hass, SERVICE_TURN_OFF)


async def test_the_reauth_of_a_part_reaches_its_step(
    hass: HomeAssistant, parts: Parts, monkeypatch: pytest.MonkeyPatch
) -> None:
    lamp = add_light(hass, "floor_lamp")
    entry = combined_entry(hass, combined(lamp))
    for data in ({REAUTH_PART: "hue_entertainment"}, {}):
        result = await entry.start_reauth_flow(hass, data=data)
        assert result["step_id"] == "reauth_confirm"
        hass.config_entries.flow.async_abort(result["flow_id"])


async def test_a_change_within_the_parts_restarts_only_the_pipeline(
    hass: HomeAssistant, parts: Parts, fake_pipeline: Any
) -> None:
    lamp = add_light(hass, "floor_lamp")
    entry = combined_entry(hass, combined(lamp))
    assert await hass.config_entries.async_setup(entry.entry_id)
    await switch(hass, SERVICE_TURN_ON)
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "output": combined(lamp, "area-other")}
    )
    await settle(hass)
    assert len(FakePipeline.instances) == 2
    assert hass.states.get(SWITCH).state == "on"
    assert entry.state is ConfigEntryState.LOADED
    await switch(hass, SERVICE_TURN_OFF)


async def test_diagnostics_show_each_part(
    hass: HomeAssistant, hass_client: ClientSessionGenerator, parts: Parts
) -> None:
    lamp = add_light(hass, "floor_lamp")
    entry = combined_entry(hass, combined(lamp))
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    assert diagnostics["output"]["type"] == "combined"
    assert [part["type"] for part in diagnostics["output"]["parts"]] == [
        "hue_entertainment",
        "ha_lights",
    ]
    assert HOST not in str(diagnostics)


async def test_a_missing_part_is_logged_only_while_syncing(
    hass: HomeAssistant,
    parts: Parts,
    fake_pipeline: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    lamp = add_light(hass, "floor_lamp")
    parts.area_missing = True
    entry = combined_entry(hass, combined(lamp))
    assert await hass.config_entries.async_setup(entry.entry_id)
    await switch(hass, SERVICE_TURN_ON)
    assert caplog.text.count("syncs without some of its lights") == 1
    FakePipeline.instances[-1].finish(StopReason.OUTPUT_UNAVAILABLE)
    await settle(hass)
    assert hass.states.get(SWITCH).state == "off"
    # Stopped: the stop is logged, not "syncs without" again.
    assert caplog.text.count("syncs without some of its lights") == 1
    assert issues(hass, entry) == {"output_missing"}
