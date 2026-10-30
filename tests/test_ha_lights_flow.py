"""Loose lights in the setup and the settings screen (#9).

Choosing the output, the lights, an area or a group, the positions, the hints
for Hue lights and the warning for crowded networks; switching between the
output types in the settings, with pairing and the kept bridge block.
"""

from typing import Any

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.glowcast.const import DOMAIN

from .test_config_flow import (  # noqa: F401 (fixtures)
    BRIDGE_ID,
    HOST,
    TV_ID,
    FakeBridge,
    bridge,
    choose_output,
    fake_api,
    glowcast_entry,
    other_integrations,
    press_and_wait,
    start_tv,
)
from .test_ha_lights_output import add_light

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

LIGHTS_URL = f"https://{HOST}/clip/v2/resource/light"
ENTERTAINMENT_URL = f"https://{HOST}/clip/v2/resource/entertainment"


@pytest.fixture(autouse=True)
def no_hue_lights(aioclient_mock: AiohttpClientMocker) -> None:
    """The bridge's lights for the hint: none can join an area by default."""
    aioclient_mock.get(LIGHTS_URL, json={"data": []})
    aioclient_mock.get(ENTERTAINMENT_URL, json={"data": []})


def hue_bridge_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = hass.config_entries.async_entry_for_domain_unique_id("hue", BRIDGE_ID)
    assert entry is not None
    return entry


async def to_loose_lights(hass: HomeAssistant) -> dict[str, Any]:
    """Setup: the TV, then Loose lights in the output menu."""
    result = await start_tv(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"tv": TV_ID}
    )
    return await choose_output(hass, result, "ha_lights")


async def configure(hass: HomeAssistant, result: dict[str, Any], data: Any) -> Any:
    return await hass.config_entries.flow.async_configure(result["flow_id"], data)


async def test_set_up_with_single_lights_and_no_bridge(hass: HomeAssistant) -> None:
    left = add_light(hass, "floor_lamp")
    right = add_light(hass, "light_bar")
    add_light(hass, "white_bulb", modes=("color_temp",))
    result = await to_loose_lights(hass)
    assert result["step_id"] == "ha_lights"

    result = await configure(hass, result, {"follow": "lights"})
    assert result["step_id"] == "ha_lights_lights"
    selector = result["data_schema"].schema["lights"].config
    # Only colour lights are offered.
    assert selector["include_entities"] == [left.entity_id, right.entity_id]
    result = await configure(hass, result, {"lights": []})
    assert result["errors"] == {"base": "ha_lights_none_chosen"}

    result = await configure(
        hass, result, {"lights": [left.entity_id, right.entity_id]}
    )
    assert result["step_id"] == "ha_lights_position"
    assert result["description_placeholders"] == {
        "light": "Floor Lamp",
        "number": "1",
        "total": "2",
    }
    result = await configure(hass, result, {"position": "left"})
    assert result["description_placeholders"]["light"] == "Light Bar"
    result = await configure(hass, result, {"position": "custom"})
    assert result["step_id"] == "ha_lights_fine_tune"
    result = await configure(hass, result, {"x": 80, "z": -30})
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    # No bridge was asked for or paired.
    assert result["data"] == {"source": {"type": "webos", "unique_id": TV_ID}}
    assert result["options"]["output"] == {
        "type": "ha_lights",
        "follow": "lights",
        "lights": [
            {"entity": left.id, "x": -1.0, "z": 0.0},
            {"entity": right.id, "x": 0.8, "z": -0.3},
        ],
    }
    entry = result["result"]
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("select.glowcast_living_room_tv_lights").state == (
        "chosen_lights"
    )
    assert hass.states.get("select.glowcast_living_room_tv_entertainment_area") is None
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_set_up_with_an_area_places_each_light(hass: HomeAssistant) -> None:
    area = ar.async_get(hass).async_create("Living room")
    one = add_light(hass, "a_lamp", area_id=area.id)
    two = add_light(hass, "b_lamp", area_id=area.id)
    result = await to_loose_lights(hass)
    result = await configure(hass, result, {"follow": "area"})
    assert result["step_id"] == "ha_lights_area"
    result = await configure(hass, result, {"area": area.id})
    result = await configure(hass, result, {"position": "top"})
    result = await configure(hass, result, {"position": "ambient"})
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"]["output"] == {
        "type": "ha_lights",
        "follow": "area",
        "area_id": area.id,
        "lights": [
            {"entity": one.id, "x": 0.0, "z": 1.0},
            {"entity": two.id, "x": 0.0, "z": 0.0},
        ],
    }
    assert hass.states.get("select.glowcast_living_room_tv_lights").state == (
        "Living room"
    )
    assert await hass.config_entries.async_unload(result["result"].entry_id)


async def test_set_up_with_a_light_group(hass: HomeAssistant) -> None:
    one = add_light(hass, "one")
    group = add_light(hass, "room", "group", members=[one.entity_id])
    result = await to_loose_lights(hass)
    result = await configure(hass, result, {"follow": "group"})
    assert result["step_id"] == "ha_lights_group"
    result = await configure(hass, result, {"group": group.id})
    result = await configure(hass, result, {"position": "right"})
    await hass.async_block_till_done()
    assert result["options"]["output"]["group"] == group.id
    assert result["options"]["output"]["lights"] == [
        {"entity": one.id, "x": 1.0, "z": 0.0}
    ]
    assert await hass.config_entries.async_unload(result["result"].entry_id)


@pytest.mark.parametrize("follow", ["lights", "area", "group"])
async def test_nothing_to_follow_says_so(hass: HomeAssistant, follow: str) -> None:
    add_light(hass, "white", modes=("onoff",))
    result = await to_loose_lights(hass)
    result = await configure(hass, result, {"follow": follow})
    assert result["step_id"] == "ha_lights"
    assert result["errors"] == {"base": f"ha_lights_no_{follow}"}


def hue_lights(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, count: int
) -> list[er.RegistryEntry]:
    """Hue lights of the bridge that can join an entertainment area."""
    lights = [
        add_light(hass, f"hue_{i}", "hue", hue_bridge_entry(hass)) for i in range(count)
    ]
    aioclient_mock.clear_requests()
    aioclient_mock.get(
        LIGHTS_URL,
        json={
            "data": [
                {"id": light.unique_id, "owner": {"rid": f"device-{i}"}}
                for i, light in enumerate(lights)
            ]
        },
    )
    aioclient_mock.get(
        ENTERTAINMENT_URL,
        json={
            "data": [
                {"owner": {"rid": f"device-{i}"}, "renderer": True}
                for i in range(count)
            ]
        },
    )
    return lights


async def test_hue_lights_get_the_hint_and_crowded_ones_the_warning(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    bridge: FakeBridge,  # noqa: F811
) -> None:
    lights = hue_lights(hass, aioclient_mock, 5)
    aioclient_mock.get(
        f"https://{HOST}/clip/v2/resource/entertainment_configuration",
        side_effect=bridge.area_list,
    )
    result = await to_loose_lights(hass)
    result = await configure(hass, result, {"follow": "lights"})
    result = await configure(
        hass, result, {"lights": [light.entity_id for light in lights]}
    )
    assert result["step_id"] == "ha_lights_hue_hint"
    # Asked with Home Assistant's own key for the bridge.
    assert {
        headers["hue-application-key"] for _, _, _, headers in aioclient_mock.mock_calls
    } == {"ha-key"}
    result = await configure(hass, result, {})
    for _ in lights:
        result = await configure(hass, result, {"position": "ambient"})
    assert result["step_id"] == "ha_lights_rate"
    assert result["description_placeholders"] == {
        "count": "5",
        "limit": "4",
        "seconds": "1.25",
    }
    result = await configure(hass, result, {})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert await hass.config_entries.async_unload(result["result"].entry_id)


async def test_hue_lights_without_an_area_get_the_how_to(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    bridge: FakeBridge,  # noqa: F811
) -> None:
    (light,) = hue_lights(hass, aioclient_mock, 1)
    bridge.areas = []
    aioclient_mock.get(
        f"https://{HOST}/clip/v2/resource/entertainment_configuration",
        side_effect=bridge.area_list,
    )
    result = await to_loose_lights(hass)
    result = await configure(hass, result, {"follow": "lights"})
    result = await configure(hass, result, {"lights": [light.entity_id]})
    assert result["step_id"] == "ha_lights_hue_no_area"
    assert (
        "github.com/nicohabets/glowcast" in result["description_placeholders"]["link"]
    )


async def test_other_lights_and_an_unreachable_bridge_get_no_hint(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    bridge: FakeBridge,  # noqa: F811
) -> None:
    other = add_light(hass, "zigbee")
    (light,) = hue_lights(hass, aioclient_mock, 1)
    bridge.reachable = False
    aioclient_mock.get(
        f"https://{HOST}/clip/v2/resource/entertainment_configuration",
        side_effect=bridge.area_list,
    )
    for picked in (other, light):
        result = await to_loose_lights(hass)
        result = await configure(hass, result, {"follow": "lights"})
        result = await configure(hass, result, {"lights": [picked.entity_id]})
        assert result["step_id"] == "ha_lights_position"
        hass.config_entries.flow.async_abort(result["flow_id"])


# The settings screen


def loose_entry(
    hass: HomeAssistant, output: dict[str, Any], data: dict[str, Any] | None = None
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Living room TV",
        unique_id=TV_ID,
        data={"source": {"type": "webos", "unique_id": TV_ID}, **(data or {})},
        options={"output": {"type": "ha_lights", **output}, "brightness": 50},
    )
    entry.add_to_hass(hass)
    return entry


async def options(hass: HomeAssistant, entry: MockConfigEntry, step: str) -> Any:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": step}
    )


async def options_configure(
    hass: HomeAssistant, result: dict[str, Any], data: Any
) -> Any:
    return await hass.config_entries.options.async_configure(result["flow_id"], data)


async def save_settings(hass: HomeAssistant, result: dict[str, Any]) -> Any:
    assert result["step_id"] == "settings"
    return await options_configure(
        hass, result, {"brightness": 50, "intensity": 100, "update_rate": "high"}
    )


async def test_switch_from_an_area_to_loose_lights_and_back_keeps_the_bridge(
    hass: HomeAssistant,
) -> None:
    lamp = add_light(hass, "lamp")
    entry = glowcast_entry(hass)
    bridge_block = dict(entry.data["bridge"])
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    area_select = "select.glowcast_living_room_tv_entertainment_area"
    assert registry.async_get(area_select) is not None

    result = await options(hass, entry, "ha_lights_options")
    result = await options_configure(hass, result, {"follow": "lights"})
    result = await options_configure(hass, result, {"lights": [lamp.entity_id]})
    result = await options_configure(hass, result, {"position": "left"})
    result = await save_settings(hass, result)
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["output"]["type"] == "ha_lights"
    assert entry.data["bridge"] == bridge_block
    # The entry reloaded: the type's entities changed with it.
    assert entry.state is ConfigEntryState.LOADED
    assert registry.async_get(area_select) is None
    assert hass.states.get(area_select) is None
    assert hass.states.get("select.glowcast_living_room_tv_lights") is not None

    # Back to the area: no new pairing, the bridge's list straight away.
    result = await options(hass, entry, "hue_options")
    assert result["step_id"] == "area"
    result = await options_configure(hass, result, {"area": "area-tv"})
    result = await save_settings(hass, result)
    await hass.async_block_till_done()
    assert entry.options["output"] == {
        "type": "hue_entertainment",
        "area_id": "area-tv",
    }
    assert entry.data["bridge"] == bridge_block
    assert registry.async_get("select.glowcast_living_room_tv_lights") is None
    assert hass.states.get(area_select) is not None
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_an_entry_without_a_bridge_pairs_from_the_settings(
    hass: HomeAssistant,
) -> None:
    lamp = add_light(hass, "lamp")
    entry = loose_entry(hass, {"follow": "lights", "lights": [{"entity": lamp.id}]})
    result = await options(hass, entry, "hue_options")
    assert result["step_id"] == "bridge"
    result = await options_configure(hass, result, {"bridge": BRIDGE_ID})
    assert result["step_id"] == "link"
    result = await options_configure(hass, result, {})
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    await hass.async_block_till_done()
    result = await hass.config_entries.options.async_configure(result["flow_id"])
    assert result["step_id"] == "area"
    result = await options_configure(hass, result, {"area": "area-other"})
    result = await save_settings(hass, result)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.data["bridge"] == {
        "unique_id": BRIDGE_ID,
        "app_key": "new-app-key",
        "client_key": "new-client-key",
    }
    assert entry.options["output"] == {
        "type": "hue_entertainment",
        "area_id": "area-other",
    }


async def test_the_settings_show_the_stored_lights_and_positions(
    hass: HomeAssistant,
) -> None:
    lamp = add_light(hass, "lamp")
    other = add_light(hass, "other")
    entry = loose_entry(
        hass,
        {"follow": "lights", "lights": [{"entity": lamp.id, "x": 0.5, "z": 0.25}]},
    )
    result = await options(hass, entry, "ha_lights_options")
    schema = {str(key): key for key in result["data_schema"].schema}
    assert schema["follow"].default() == "lights"
    result = await options_configure(hass, result, {"follow": "lights"})
    schema = {str(key): key for key in result["data_schema"].schema}
    assert schema["lights"].default() == [lamp.entity_id]
    result = await options_configure(
        hass, result, {"lights": [lamp.entity_id, other.entity_id]}
    )
    schema = {str(key): key for key in result["data_schema"].schema}
    assert schema["position"].default() == "custom"
    result = await options_configure(hass, result, {"position": "custom"})
    schema = {str(key): key for key in result["data_schema"].schema}
    assert schema["x"].default() == 50
    assert schema["z"].default() == 25
    result = await options_configure(hass, result, {"x": 50, "z": 25})
    schema = {str(key): key for key in result["data_schema"].schema}
    assert schema["position"].default() == "ambient"  # a new light


async def test_lights_that_joined_or_left_show_a_notice(hass: HomeAssistant) -> None:
    area = ar.async_get(hass).async_create("Living room")
    stays = add_light(hass, "stays", area_id=area.id)
    joined = add_light(hass, "joined", area_id=area.id)
    left = add_light(hass, "left_lamp")
    entry = loose_entry(
        hass,
        {
            "follow": "area",
            "area_id": area.id,
            "lights": [
                {"entity": stays.id, "x": -1.0, "z": 0.0},
                {"entity": left.id, "x": 1.0, "z": 0.0},
            ],
        },
    )
    result = await options(hass, entry, "ha_lights_options")
    assert result["step_id"] == "ha_lights_changes"
    assert result["description_placeholders"] == {
        "joined": "Joined",
        "left": "Left Lamp",
    }
    result = await options_configure(hass, result, {})
    result = await options_configure(hass, result, {"follow": "area"})
    result = await options_configure(hass, result, {"area": area.id})
    result = await options_configure(hass, result, {"position": "right"})
    result = await options_configure(hass, result, {"position": "left"})
    result = await save_settings(hass, result)
    # The position of the light that left is removed.
    assert entry.options["output"]["lights"] == [
        {"entity": joined.id, "x": 1.0, "z": 0.0},
        {"entity": stays.id, "x": -1.0, "z": 0.0},
    ]


# Removing an entry


async def test_an_entry_that_never_paired_leaves_no_notice(
    hass: HomeAssistant,
) -> None:
    lamp = add_light(hass, "lamp")
    entry = loose_entry(hass, {"follow": "lights", "lights": [{"entity": lamp.id}]})
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    notifications = hass.data.get("persistent_notification", {})
    assert not any("bridge_app_left" in key for key in notifications)


async def test_an_entry_that_paired_and_switched_still_gets_the_notice(
    hass: HomeAssistant,
) -> None:
    lamp = add_light(hass, "lamp")
    entry = loose_entry(
        hass,
        {"follow": "lights", "lights": [{"entity": lamp.id}]},
        {"bridge": {"unique_id": BRIDGE_ID, "app_key": "k", "client_key": "c"}},
    )
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    notifications = hass.data.get("persistent_notification", {})
    assert f"glowcast_bridge_app_left_{entry.entry_id}" in notifications
