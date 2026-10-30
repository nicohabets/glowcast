"""The "Start Glowcast when the TV turns on" blueprint.

The automation is made from the blueprint file through Home Assistant's own
blueprint loading. The TV is a plain state and the switch's actions are
recorded services, so Glowcast itself is not set up.
"""

import shutil
from pathlib import Path

import pytest
from homeassistant.components.automation.config import AUTOMATION_BLUEPRINT_SCHEMA
from homeassistant.components.blueprint import Blueprint
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.setup import async_setup_component
from homeassistant.util.yaml import load_yaml_dict
from pytest_homeassistant_custom_component.common import async_mock_service

BLUEPRINT_PATH = "glowcast/start_with_tv.yaml"
BLUEPRINT_FILE = (
    Path(__file__).parent.parent / "blueprints" / "automation" / BLUEPRINT_PATH
)
TV = "media_player.living_room_tv"
SWITCH = "switch.glowcast_living_room_tv"


def _blueprint() -> Blueprint:
    return Blueprint(
        load_yaml_dict(BLUEPRINT_FILE),
        expected_domain="automation",
        schema=AUTOMATION_BLUEPRINT_SCHEMA,
    )


def test_blueprint_is_valid() -> None:
    blueprint = _blueprint()

    assert blueprint.validate() is None
    assert blueprint.name == "Start Glowcast when the TV turns on"
    assert blueprint.metadata["source_url"] == (
        "https://github.com/nicohabets/glowcast/blob/main/blueprints/automation/"
        "glowcast/start_with_tv.yaml"
    )
    assert blueprint.metadata["homeassistant"] == {"min_version": "2026.8.3"}


def test_inputs_offer_webos_tvs_and_glowcast_switches() -> None:
    # The file as written: Home Assistant fills in selector defaults that
    # differ between its versions.
    inputs = load_yaml_dict(BLUEPRINT_FILE)["blueprint"]["input"]

    assert inputs.keys() == {"tv", "glowcast_switch", "also_stop"}
    assert inputs["tv"]["selector"] == {
        "entity": {"filter": [{"domain": "media_player", "integration": "webostv"}]}
    }
    assert inputs["glowcast_switch"]["selector"] == {
        "entity": {"filter": [{"domain": "switch", "integration": "glowcast"}]}
    }
    assert inputs["also_stop"]["selector"] == {"boolean": None}
    assert inputs["also_stop"]["default"] is False


async def _setup_automation(
    hass: HomeAssistant, tmp_path: Path, tv_state: str, **inputs: bool
) -> tuple[list[ServiceCall], list[ServiceCall]]:
    """Make an automation from the blueprint; return the turn_on/turn_off calls."""
    hass.config.config_dir = str(tmp_path)
    target = tmp_path / "blueprints" / "automation" / BLUEPRINT_PATH
    target.parent.mkdir(parents=True)
    shutil.copy(BLUEPRINT_FILE, target)
    hass.states.async_set(TV, tv_state)
    turn_on = async_mock_service(hass, "switch", "turn_on")
    turn_off = async_mock_service(hass, "switch", "turn_off")
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "use_blueprint": {
                    "path": BLUEPRINT_PATH,
                    "input": {"tv": TV, "glowcast_switch": SWITCH, **inputs},
                }
            }
        },
    )
    await hass.async_block_till_done()
    return turn_on, turn_off


@pytest.mark.parametrize(
    ("from_state", "to_state", "attributes", "starts"),
    [
        (STATE_OFF, STATE_ON, {}, True),
        (STATE_UNAVAILABLE, STATE_ON, {}, True),
        (STATE_UNKNOWN, STATE_ON, {}, False),
        (STATE_ON, STATE_ON, {"source": "HDMI 1"}, False),
    ],
)
async def test_switch_turns_on_when_the_tv_turns_on(
    hass: HomeAssistant,
    tmp_path: Path,
    from_state: str,
    to_state: str,
    attributes: dict[str, str],
    starts: bool,
) -> None:
    turn_on, turn_off = await _setup_automation(hass, tmp_path, from_state)

    hass.states.async_set(TV, to_state, attributes)
    await hass.async_block_till_done()

    assert [call.data["entity_id"] for call in turn_on] == (
        [[SWITCH]] if starts else []
    )
    assert turn_off == []


@pytest.mark.parametrize(("also_stop", "stops"), [(True, True), (False, False)])
async def test_switch_turns_off_with_the_tv_only_when_asked(
    hass: HomeAssistant, tmp_path: Path, also_stop: bool, stops: bool
) -> None:
    turn_on, turn_off = await _setup_automation(
        hass, tmp_path, STATE_ON, also_stop=also_stop
    )

    hass.states.async_set(TV, STATE_OFF)
    await hass.async_block_till_done()

    assert [call.data["entity_id"] for call in turn_off] == (
        [[SWITCH]] if stops else []
    )
    assert turn_on == []


async def test_also_stop_is_off_by_default(hass: HomeAssistant, tmp_path: Path) -> None:
    _, turn_off = await _setup_automation(hass, tmp_path, STATE_ON)

    hass.states.async_set(TV, STATE_OFF)
    await hass.async_block_till_done()

    assert turn_off == []
