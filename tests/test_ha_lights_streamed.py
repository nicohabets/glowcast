"""Home Assistant lights in an entertainment area that another app streams (#219).

Next to a Hue Sync Box, for example: while the box streams its area, the
bridge ignores other commands to that area's lights. Glowcast never touches
the stream; it only says so with a notice when it takes over the lights.
"""

from typing import Any

import pytest
from homeassistant.const import SERVICE_TURN_OFF, SERVICE_TURN_ON, STATE_ON
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.glowcast.const import DOMAIN
from custom_components.glowcast.outputs import OUTPUTS, part_options
from custom_components.glowcast.outputs.ha_lights import async_streamed_notice

from .fakes import FakeAdapters, wait_for
from .test_combined import combined, combined_entry
from .test_config_flow import (  # noqa: F401 (fixtures)
    FakeBridge,
    area,
    bridge,
    fake_api,
    other_integrations,
)
from .test_ha_lights_flow import (  # noqa: F401 (fixtures)
    ha_lights_entry,
    hue_bridge_entry,
    no_hue_lights,
)
from .test_ha_lights_output import add_light
from .test_ha_lights_sync import switch

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

SWITCH = "switch.glowcast_living_room_tv"


def box_area(
    *lights: er.RegistryEntry, area_id: str = "area-box", status: str = "active"
) -> dict[str, Any]:
    """An area that the box streams (or not), with these Hue lights."""
    return {
        **area(area_id, "Sync Box area"),
        "status": status,
        "active_streamer": {"rid": "sync-box-app", "rtype": "auth_v1"},
        "light_services": [
            {"rid": light.unique_id, "rtype": "light"} for light in lights
        ],
    }


def lights_block(*lights: er.RegistryEntry) -> dict[str, Any]:
    return {
        "type": "ha_lights",
        "follow": "lights",
        "lights": [{"entity": light.id, "x": 0.0, "z": 0.0} for light in lights],
    }


def notice_options(block: dict[str, Any]) -> dict[str, Any]:
    return {"output": block}


async def test_a_light_in_an_area_another_app_streams_gets_the_notice(
    hass: HomeAssistant,
    bridge: FakeBridge,  # noqa: F811
    aioclient_mock: AiohttpClientMocker,
    caplog: pytest.LogCaptureFixture,
) -> None:
    gradient = add_light(hass, "gradient_strip", "hue", hue_bridge_entry(hass))
    lamp = add_light(hass, "floor_lamp", "hue", hue_bridge_entry(hass))
    other = add_light(hass, "desk_lamp")
    bridge.areas = [box_area(gradient), area("area-tv", "TV area")]

    placeholders = await async_streamed_notice(
        hass, {}, notice_options(lights_block(gradient, lamp, other))
    )

    assert placeholders == {"lights": "Gradient Strip", "areas": "Sync Box area"}
    assert "another app streams (Sync Box area)" in caplog.text
    assert "while it does: Gradient Strip" in caplog.text
    # Read only: never a start or stop of anyone's stream.
    assert {method for method, *_ in aioclient_mock.mock_calls} == {"GET"}


@pytest.mark.parametrize("status", ["inactive", None])
async def test_an_area_that_does_not_stream_gives_no_notice(
    hass: HomeAssistant,
    bridge: FakeBridge,  # noqa: F811
    status: str | None,
) -> None:
    gradient = add_light(hass, "gradient_strip", "hue", hue_bridge_entry(hass))
    streamed = box_area(gradient, status=status or "inactive")
    if status is None:
        del streamed["status"]
    bridge.areas = [streamed]

    assert (
        await async_streamed_notice(hass, {}, notice_options(lights_block(gradient)))
        is None
    )


async def test_a_hue_room_with_a_member_in_the_area_counts(
    hass: HomeAssistant,
    bridge: FakeBridge,  # noqa: F811
) -> None:
    gradient = add_light(hass, "gradient_strip", "hue", hue_bridge_entry(hass))
    lamp = add_light(hass, "floor_lamp", "hue", hue_bridge_entry(hass))
    room = add_light(
        hass,
        "living_room",
        "hue",
        hue_bridge_entry(hass),
        members=[gradient.entity_id, lamp.entity_id],
    )
    bridge.areas = [box_area(gradient)]

    assert await async_streamed_notice(
        hass, {}, notice_options(lights_block(room))
    ) == {"lights": "Living Room", "areas": "Sync Box area"}


async def test_other_lights_never_ask_a_bridge(
    hass: HomeAssistant,
    bridge: FakeBridge,  # noqa: F811
    aioclient_mock: AiohttpClientMocker,
) -> None:
    lamp = add_light(hass, "desk_lamp")
    bridge.areas = [box_area()]

    assert (
        await async_streamed_notice(hass, {}, notice_options(lights_block(lamp)))
        is None
    )
    assert aioclient_mock.mock_calls == []


async def test_no_notice_when_the_bridge_cannot_be_asked(
    hass: HomeAssistant,
    bridge: FakeBridge,  # noqa: F811
) -> None:
    gradient = add_light(hass, "gradient_strip", "hue", hue_bridge_entry(hass))
    bridge.areas = [box_area(gradient)]
    bridge.reachable = False

    assert (
        await async_streamed_notice(hass, {}, notice_options(lights_block(gradient)))
        is None
    )


async def test_unavailable_lights_are_not_named(
    hass: HomeAssistant,
    bridge: FakeBridge,  # noqa: F811
) -> None:
    gradient = add_light(
        hass, "gradient_strip", "hue", hue_bridge_entry(hass), state="unavailable"
    )
    bridge.areas = [box_area(gradient)]

    assert (
        await async_streamed_notice(hass, {}, notice_options(lights_block(gradient)))
        is None
    )


async def test_glowcasts_own_area_is_not_another_app(
    hass: HomeAssistant,
    bridge: FakeBridge,  # noqa: F811
) -> None:
    """With both together, the entry's own area is #198's notice, not this one."""
    gradient = add_light(hass, "gradient_strip", "hue", hue_bridge_entry(hass))
    lamp = add_light(hass, "floor_lamp", "hue", hue_bridge_entry(hass))
    output = combined(lamp)
    output["parts"][1]["lights"].append({"entity": gradient.id, "x": 1.0, "z": 0.0})
    entry = combined_entry(hass, output)
    options = part_options(entry.options, output["parts"][1])
    bridge.areas = [box_area(gradient, area_id="area-tv")]
    assert await async_streamed_notice(hass, entry.data, options) is None

    # Another area, which another app streams, is.
    bridge.areas = [box_area(lamp), area("area-tv", "TV area")]
    assert await async_streamed_notice(hass, entry.data, options) == {
        "lights": "Floor Lamp",
        "areas": "Sync Box area",
    }


def streamed_issue(hass: HomeAssistant, entry_id: str) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(
        DOMAIN, f"ha_lights_streamed_elsewhere_{entry_id}"
    )


async def test_switch_on_next_to_a_box_raises_the_notice_and_drives_the_rest(
    hass: HomeAssistant,
    adapters: FakeAdapters,
    bridge: FakeBridge,  # noqa: F811
) -> None:
    turn_on: list[ServiceCall] = []

    async def record(call: ServiceCall) -> None:
        turn_on.append(call)

    hass.services.async_register("light", "turn_on", record)
    gradient = add_light(hass, "gradient_strip", "hue", hue_bridge_entry(hass))
    lamp = add_light(hass, "desk_lamp")
    entry = ha_lights_entry(hass, lights_block(gradient, lamp))
    bridge.areas = [box_area(gradient)]
    assert OUTPUTS["ha_lights"].notices[1].issue.key == "ha_lights_streamed_elsewhere"
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    await switch(hass, SERVICE_TURN_ON)
    await wait_for(lambda: streamed_issue(hass, entry.entry_id) is not None)

    issue = streamed_issue(hass, entry.entry_id)
    assert issue.translation_key == "ha_lights_streamed_elsewhere"
    assert issue.translation_placeholders == {
        "name": entry.title,
        "lights": "Gradient Strip",
        "areas": "Sync Box area",
    }
    # Glowcast keeps syncing, and the other light follows.
    assert hass.states.get(SWITCH).state == STATE_ON
    await wait_for(
        lambda: any(lamp.entity_id in str(call.data["entity_id"]) for call in turn_on)
    )
    await switch(hass, SERVICE_TURN_OFF)
    await hass.async_block_till_done()
    # Still there while off: nothing was fixed yet.
    assert streamed_issue(hass, entry.entry_id) is not None

    # The box stopped: the next start clears it.
    bridge.areas = [box_area(gradient, status="inactive")]
    await switch(hass, SERVICE_TURN_ON)
    await wait_for(lambda: streamed_issue(hass, entry.entry_id) is None)
    await switch(hass, SERVICE_TURN_OFF)
    assert await hass.config_entries.async_unload(entry.entry_id)
