"""Loose lights behind the switch (#9): the real pipeline, a fake TV, and the
real loose-lights output with the light services replaced by recorders."""

from datetime import timedelta

import pytest
from homeassistant.const import SERVICE_TURN_OFF, SERVICE_TURN_ON, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.glowcast.const import DOMAIN
from custom_components.glowcast.outputs import ha_lights_select

from .fakes import FakeAdapters, wait_for
from .test_config_flow import (  # noqa: F401 (fixtures)
    bridge,
    fake_api,
    other_integrations,
)
from .test_ha_lights_flow import loose_entry
from .test_ha_lights_output import add_light

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

SWITCH = "switch.glowcast_living_room_tv"
SELECT = "select.glowcast_living_room_tv_lights"


async def refreshed(hass: HomeAssistant) -> None:
    """Past the Lights entity's cooldown, so it has made its list again."""
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
    await hass.async_block_till_done()


async def test_the_list_is_made_once_per_burst_of_changes(
    hass: HomeAssistant, adapters: FakeAdapters, monkeypatch: pytest.MonkeyPatch
) -> None:
    lamp = add_light(hass, "lamp")
    entry = loose_entry(hass, {"follow": "lights", "lights": [{"entity": lamp.id}]})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    scans: list[None] = []
    real = ha_lights_select.areas_with_lights

    def counted(hass: HomeAssistant) -> dict[str, str]:
        scans.append(None)
        return real(hass)

    monkeypatch.setattr(ha_lights_select, "areas_with_lights", counted)
    area = ar.async_get(hass).async_create("Living room")
    for i in range(10):
        add_light(hass, f"new_{i}", area_id=area.id)
    # A switch, not a light: no refresh.
    er.async_get(hass).async_get_or_create("switch", "zha", "plug")
    await refreshed(hass)
    # The first change at once, the rest of the burst after the cooldown.
    assert len(scans) == 2
    assert "Living room" in hass.states.get(SELECT).attributes["options"]
    assert await hass.config_entries.async_unload(entry.entry_id)


async def switch(hass: HomeAssistant, service: str) -> None:
    await hass.services.async_call(
        "switch", service, {"entity_id": SWITCH}, blocking=True
    )


async def test_the_lights_follow_and_return_to_their_state(
    hass: HomeAssistant, adapters: FakeAdapters
) -> None:
    turn_on: list[ServiceCall] = async_mock_service(hass, "light", "turn_on")
    lamp = add_light(hass, "lamp")
    entry = loose_entry(
        hass, {"follow": "lights", "lights": [{"entity": lamp.id, "x": -1, "z": 0}]}
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    await switch(hass, SERVICE_TURN_ON)
    await wait_for(lambda: turn_on)
    assert hass.states.get(SWITCH).state == STATE_ON
    # A red picture at brightness 50 %.
    assert turn_on[0].data["rgb_color"] == [255, 0, 0]
    assert turn_on[0].data["brightness"] == 128
    # The light now shows Glowcast's red.
    hass.states.async_set(
        lamp.entity_id,
        "on",
        {"supported_color_modes": ["xy"], "color_mode": "hs", "hs_color": [0, 100]},
    )
    turn_on.clear()

    await switch(hass, SERVICE_TURN_OFF)
    await hass.async_block_till_done()
    assert hass.states.get(SWITCH).state == STATE_OFF
    assert [call.data.get("xy_color") for call in turn_on] == [[0.3, 0.3]]
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_a_removed_area_stops_with_a_repair_issue(
    hass: HomeAssistant, adapters: FakeAdapters
) -> None:
    async_mock_service(hass, "light", "turn_on")
    area = ar.async_get(hass).async_create("Living room")
    add_light(hass, "lamp", area_id=area.id)
    entry = loose_entry(hass, {"follow": "area", "area_id": area.id, "lights": []})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    ar.async_get(hass).async_delete(area.id)

    await switch(hass, SERVICE_TURN_ON)
    await wait_for(lambda: hass.states.get(SWITCH).state == STATE_OFF)
    await hass.async_block_till_done()
    issue_id = f"loose_lights_not_found_{entry.entry_id}"
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_key == "ha_lights_not_found"

    # Another choice of lights clears it.
    other = ar.async_get(hass).async_create("Kitchen")
    add_light(hass, "kitchen_lamp", area_id=other.id)
    await refreshed(hass)
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": SELECT, "option": "Kitchen"},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    assert entry.options["output"]["area_id"] == other.id
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_the_entity_offers_areas_and_groups_and_restarts_on_a_choice(
    hass: HomeAssistant, adapters: FakeAdapters
) -> None:
    async_mock_service(hass, "light", "turn_on")
    lamp = add_light(hass, "lamp")
    area = ar.async_get(hass).async_create("Living room")
    add_light(hass, "area_lamp", area_id=area.id)
    ar.async_get(hass).async_create("Empty room")
    add_light(hass, "room", "group", members=[lamp.entity_id])
    entry = loose_entry(hass, {"follow": "lights", "lights": [{"entity": lamp.id}]})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get(SELECT)
    assert state.state == "chosen_lights"
    # Only areas and groups with colour lights.
    assert state.attributes["options"] == ["chosen_lights", "Living room", "Room"]

    await switch(hass, SERVICE_TURN_ON)
    await wait_for(lambda: adapters.sources)
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": SELECT, "option": "Living room"},
        blocking=True,
    )
    await wait_for(lambda: len(adapters.sources) == 2)
    assert hass.states.get(SWITCH).state == STATE_ON
    state = hass.states.get(SELECT)
    assert state.state == "Living room"
    # "Chosen lights" is offered only while single lights are used.
    assert state.attributes["options"] == ["Living room", "Room"]
    await switch(hass, SERVICE_TURN_OFF)
    assert await hass.config_entries.async_unload(entry.entry_id)
