"""The "Follow a Hue Sync Box" blueprint (#219).

The automation is made from the blueprint file through Home Assistant's own
blueprint loading. The box's "syncing" entity and the TV are plain states and
the switch's actions are recorded services, so Glowcast itself is not set up.
"""

import shutil
from pathlib import Path
from typing import Any

import pytest
from homeassistant.components.automation.config import AUTOMATION_BLUEPRINT_SCHEMA
from homeassistant.components.blueprint import Blueprint
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.setup import async_setup_component
from homeassistant.util.yaml import load_yaml_dict
from pytest_homeassistant_custom_component.common import async_mock_service

BLUEPRINT_PATH = "glowcast/follow_sync_box.yaml"
BLUEPRINT_FILE = (
    Path(__file__).parent.parent / "blueprints" / "automation" / BLUEPRINT_PATH
)
LIGHT_SYNC = "switch.sync_box_light_sync"
AREA_SENSOR = "binary_sensor.tv_area_entertainment_configuration"
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
    assert blueprint.name == "Follow a Hue Sync Box"
    assert blueprint.metadata["source_url"] == (
        "https://github.com/nicohabets/glowcast/blob/main/blueprints/automation/"
        "glowcast/follow_sync_box.yaml"
    )
    assert blueprint.metadata["homeassistant"] == {"min_version": "2026.8.3"}


def test_inputs_offer_any_switch_or_binary_sensor_and_an_optional_tv() -> None:
    # The file as written: Home Assistant fills in selector defaults that
    # differ between its versions.
    inputs = load_yaml_dict(BLUEPRINT_FILE)["blueprint"]["input"]

    assert inputs.keys() == {"syncing", "glowcast_switch", "tv"}
    assert inputs["syncing"]["selector"] == {
        "entity": {"filter": [{"domain": ["switch", "binary_sensor"]}]}
    }
    assert "default" not in inputs["syncing"]
    assert inputs["glowcast_switch"]["selector"] == {
        "entity": {"filter": [{"domain": "switch", "integration": "glowcast"}]}
    }
    assert inputs["tv"]["selector"] == {
        "entity": {"filter": [{"domain": "media_player"}]}
    }
    assert inputs["tv"]["default"] == ""


async def _setup_automation(
    hass: HomeAssistant,
    tmp_path: Path,
    syncing_state: str,
    syncing: str = LIGHT_SYNC,
    **inputs: Any,
) -> tuple[list[ServiceCall], list[ServiceCall]]:
    """Make an automation from the blueprint; return the turn_on/turn_off calls."""
    hass.config.config_dir = str(tmp_path)
    target = tmp_path / "blueprints" / "automation" / BLUEPRINT_PATH
    target.parent.mkdir(parents=True)
    shutil.copy(BLUEPRINT_FILE, target)
    hass.states.async_set(syncing, syncing_state)
    turn_on = async_mock_service(hass, "switch", "turn_on")
    turn_off = async_mock_service(hass, "switch", "turn_off")
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "use_blueprint": {
                    "path": BLUEPRINT_PATH,
                    "input": {
                        "syncing": syncing,
                        "glowcast_switch": SWITCH,
                        **inputs,
                    },
                }
            }
        },
    )
    await hass.async_block_till_done()
    return turn_on, turn_off


def _targets(calls: list[ServiceCall]) -> list[Any]:
    return [call.data["entity_id"] for call in calls]


@pytest.mark.parametrize("syncing", [LIGHT_SYNC, AREA_SENSOR])
@pytest.mark.parametrize(
    ("from_state", "to_state", "attributes", "starts"),
    [
        (STATE_OFF, STATE_ON, {}, True),
        (STATE_UNAVAILABLE, STATE_ON, {}, True),
        # Home Assistant starts while the box syncs: not a start of the box.
        (STATE_UNKNOWN, STATE_ON, {}, False),
        (STATE_ON, STATE_ON, {"mode": "game"}, False),
    ],
)
async def test_switch_turns_on_when_the_box_starts_syncing(
    hass: HomeAssistant,
    tmp_path: Path,
    syncing: str,
    from_state: str,
    to_state: str,
    attributes: dict[str, str],
    starts: bool,
) -> None:
    turn_on, turn_off = await _setup_automation(
        hass, tmp_path, from_state, syncing=syncing
    )

    hass.states.async_set(syncing, to_state, attributes)
    await hass.async_block_till_done()

    assert _targets(turn_on) == ([[SWITCH]] if starts else [])
    assert turn_off == []


@pytest.mark.parametrize("syncing", [LIGHT_SYNC, AREA_SENSOR])
async def test_switch_turns_off_when_the_box_stops_syncing(
    hass: HomeAssistant, tmp_path: Path, syncing: str
) -> None:
    turn_on, turn_off = await _setup_automation(
        hass, tmp_path, STATE_ON, syncing=syncing
    )

    hass.states.async_set(syncing, STATE_OFF)
    await hass.async_block_till_done()

    assert _targets(turn_off) == [[SWITCH]]
    assert turn_on == []


@pytest.mark.parametrize("to_state", [STATE_UNAVAILABLE, STATE_UNKNOWN])
async def test_a_drop_of_the_box_does_not_stop_glowcast(
    hass: HomeAssistant, tmp_path: Path, to_state: str
) -> None:
    turn_on, turn_off = await _setup_automation(hass, tmp_path, STATE_ON)

    hass.states.async_set(LIGHT_SYNC, to_state)
    await hass.async_block_till_done()

    assert turn_off == []
    assert turn_on == []


@pytest.mark.parametrize(
    ("tv_state", "starts"),
    [
        (STATE_ON, True),
        ("playing", True),
        (STATE_OFF, False),
        ("standby", False),
        (STATE_UNAVAILABLE, False),
    ],
)
async def test_with_a_tv_it_starts_only_while_the_tv_is_on(
    hass: HomeAssistant, tmp_path: Path, tv_state: str, starts: bool
) -> None:
    hass.states.async_set(TV, tv_state)
    turn_on, _ = await _setup_automation(hass, tmp_path, STATE_OFF, tv=TV)

    hass.states.async_set(LIGHT_SYNC, STATE_ON)
    await hass.async_block_till_done()

    assert _targets(turn_on) == ([[SWITCH]] if starts else [])


async def test_with_a_tv_that_is_off_it_still_stops(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    hass.states.async_set(TV, STATE_OFF)
    _, turn_off = await _setup_automation(hass, tmp_path, STATE_ON, tv=TV)

    hass.states.async_set(LIGHT_SYNC, STATE_OFF)
    await hass.async_block_till_done()

    assert _targets(turn_off) == [[SWITCH]]


async def test_without_a_tv_it_starts_whatever_the_tvs_do(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    hass.states.async_set(TV, STATE_OFF)
    turn_on, _ = await _setup_automation(hass, tmp_path, STATE_OFF)

    hass.states.async_set(LIGHT_SYNC, STATE_ON)
    await hass.async_block_till_done()

    assert _targets(turn_on) == [[SWITCH]]
