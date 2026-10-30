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
from homeassistant.core import Context, HomeAssistant, ServiceCall
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

    assert inputs.keys() == {
        "tv",
        "glowcast_switch",
        "also_stop",
        "only_after_dark",
        "sources",
    }
    assert inputs["tv"]["selector"] == {
        "entity": {"filter": [{"domain": "media_player", "integration": "webostv"}]}
    }
    assert inputs["glowcast_switch"]["selector"] == {
        "entity": {"filter": [{"domain": "switch", "integration": "glowcast"}]}
    }
    assert inputs["also_stop"]["selector"] == {"boolean": None}
    assert inputs["also_stop"]["default"] is False
    assert inputs["only_after_dark"]["selector"] == {"boolean": None}
    assert inputs["only_after_dark"]["default"] is False
    assert inputs["sources"]["selector"] == {"text": {"multiple": True}}
    assert inputs["sources"]["default"] == []


async def _setup_automation(
    hass: HomeAssistant,
    tmp_path: Path,
    tv_state: str,
    tv_attributes: dict[str, str] | None = None,
    **inputs: object,
) -> tuple[list[ServiceCall], list[ServiceCall]]:
    """Make an automation from the blueprint; return the turn_on/turn_off calls."""
    hass.config.config_dir = str(tmp_path)
    target = tmp_path / "blueprints" / "automation" / BLUEPRINT_PATH
    target.parent.mkdir(parents=True)
    shutil.copy(BLUEPRINT_FILE, target)
    hass.states.async_set(TV, tv_state, tv_attributes)
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


# Only after dark and chosen sources (#161). Here the switch's actions write
# its state with the call's context, as Glowcast's switch does when it starts
# or stops, so "started by this automation" can be checked.


def _switch_service(hass: HomeAssistant, service: str) -> list[ServiceCall]:
    calls: list[ServiceCall] = []
    state = STATE_ON if service == "turn_on" else STATE_OFF

    async def handle(call: ServiceCall) -> None:
        calls.append(call)
        hass.states.async_set(SWITCH, state, context=call.context)

    hass.services.async_register("switch", service, handle)
    return calls


async def _setup_with_switch(
    hass: HomeAssistant,
    tmp_path: Path,
    tv_state: str,
    tv_attributes: dict[str, str] | None = None,
    **inputs: object,
) -> tuple[list[ServiceCall], list[ServiceCall]]:
    """As _setup_automation, with a switch that follows its actions."""
    await _setup_automation(hass, tmp_path, tv_state, tv_attributes, **inputs)
    hass.states.async_set(SWITCH, STATE_OFF)
    return _switch_service(hass, "turn_on"), _switch_service(hass, "turn_off")


async def _set_tv(
    hass: HomeAssistant, state: str, attributes: dict[str, object] | None = None
) -> None:
    hass.states.async_set(TV, state, attributes)
    await hass.async_block_till_done()


def _switched(calls: list[ServiceCall]) -> list[list[str]]:
    return [call.data["entity_id"] for call in calls]


SOURCES = ["HDMI 2", "Netflix"]


@pytest.mark.parametrize(
    ("sun", "starts"),
    [("above_horizon", False), ("below_horizon", True), (None, False)],
)
async def test_only_after_dark(
    hass: HomeAssistant, tmp_path: Path, sun: str | None, starts: bool
) -> None:
    if sun is not None:
        hass.states.async_set("sun.sun", sun)
    turn_on, _ = await _setup_with_switch(
        hass, tmp_path, STATE_OFF, only_after_dark=True
    )

    await _set_tv(hass, STATE_ON)

    assert _switched(turn_on) == ([[SWITCH]] if starts else [])


async def test_the_sun_does_not_count_by_default(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    hass.states.async_set("sun.sun", "above_horizon")
    turn_on, _ = await _setup_with_switch(hass, tmp_path, STATE_OFF)

    await _set_tv(hass, STATE_ON)

    assert _switched(turn_on) == [[SWITCH]]


@pytest.mark.parametrize(
    ("source", "starts"), [("HDMI 2", True), ("Netflix", True), ("HDMI 1", False)]
)
async def test_tv_on_starts_only_for_a_chosen_source(
    hass: HomeAssistant, tmp_path: Path, source: str, starts: bool
) -> None:
    turn_on, turn_off = await _setup_with_switch(
        hass, tmp_path, STATE_OFF, sources=SOURCES
    )

    # The source comes with "on"; then the TV reports more: no second start.
    await _set_tv(hass, STATE_ON, {"source": source})
    await _set_tv(hass, STATE_ON, {"source": source, "volume_level": 0.2})

    assert _switched(turn_on) == ([[SWITCH]] if starts else [])
    assert turn_off == []


async def test_starts_once_when_the_source_comes_after_on(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    turn_on, turn_off = await _setup_with_switch(
        hass, tmp_path, STATE_OFF, sources=SOURCES
    )

    await _set_tv(hass, STATE_ON)
    assert turn_on == []
    await _set_tv(hass, STATE_ON, {"source": "HDMI 2"})
    await _set_tv(hass, STATE_ON, {"source": "Netflix"})  # also chosen

    assert _switched(turn_on) == [[SWITCH]]
    assert turn_off == []


async def test_starts_when_switching_to_a_chosen_source(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    turn_on, turn_off = await _setup_with_switch(
        hass, tmp_path, STATE_ON, {"source": "HDMI 1"}, sources=SOURCES
    )

    await _set_tv(hass, STATE_ON, {"source": "Netflix"})

    assert _switched(turn_on) == [[SWITCH]]
    assert turn_off == []


async def test_switching_to_a_chosen_source_by_day_with_only_after_dark(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    hass.states.async_set("sun.sun", "above_horizon")
    turn_on, _ = await _setup_with_switch(
        hass,
        tmp_path,
        STATE_ON,
        {"source": "HDMI 1"},
        sources=SOURCES,
        only_after_dark=True,
    )

    await _set_tv(hass, STATE_ON, {"source": "Netflix"})

    assert turn_on == []


async def test_source_changes_do_nothing_without_chosen_sources(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    turn_on, turn_off = await _setup_with_switch(
        hass, tmp_path, STATE_ON, {"source": "HDMI 1"}, also_stop=True
    )
    hass.states.async_set(SWITCH, STATE_ON)

    await _set_tv(hass, STATE_ON, {"source": "Netflix"})

    assert turn_on == []
    assert turn_off == []


@pytest.mark.parametrize(("also_stop", "stops"), [(True, True), (False, False)])
async def test_another_source_turns_off_what_this_automation_started(
    hass: HomeAssistant, tmp_path: Path, also_stop: bool, stops: bool
) -> None:
    turn_on, turn_off = await _setup_with_switch(
        hass, tmp_path, STATE_OFF, sources=SOURCES, also_stop=also_stop
    )
    await _set_tv(hass, STATE_ON, {"source": "HDMI 2"})
    assert _switched(turn_on) == [[SWITCH]]

    await _set_tv(hass, STATE_ON, {"source": "HDMI 1"})

    assert _switched(turn_off) == ([[SWITCH]] if stops else [])


async def test_another_source_leaves_a_switch_turned_on_by_hand(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    turn_on, turn_off = await _setup_with_switch(
        hass, tmp_path, STATE_ON, {"source": "HDMI 1"}, sources=SOURCES, also_stop=True
    )
    hass.states.async_set(SWITCH, STATE_ON, context=Context())  # from a dashboard

    await _set_tv(hass, STATE_ON, {"source": "HDMI 3"})

    assert turn_on == []
    assert turn_off == []


async def test_another_source_leaves_a_switch_turned_on_again_by_hand(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    _, turn_off = await _setup_with_switch(
        hass, tmp_path, STATE_OFF, sources=SOURCES, also_stop=True
    )
    await _set_tv(hass, STATE_ON, {"source": "HDMI 2"})
    hass.states.async_set(SWITCH, STATE_OFF, context=Context())
    hass.states.async_set(SWITCH, STATE_ON, context=Context())

    await _set_tv(hass, STATE_ON, {"source": "HDMI 1"})

    assert turn_off == []


async def test_back_and_forth_between_sources(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    turn_on, turn_off = await _setup_with_switch(
        hass, tmp_path, STATE_OFF, sources=SOURCES, also_stop=True
    )
    await _set_tv(hass, STATE_ON, {"source": "HDMI 2"})  # starts
    await _set_tv(hass, STATE_ON, {"source": "HDMI 1"})  # stops
    await _set_tv(hass, STATE_ON, {"source": "HDMI 3"})  # nothing to do
    await _set_tv(hass, STATE_ON, {"source": "Netflix"})  # starts again
    await _set_tv(hass, STATE_ON, {"source": "HDMI 1"})  # stops again

    assert len(turn_on) == 2
    assert len(turn_off) == 2


async def test_tv_off_stops_also_with_chosen_sources(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    _, turn_off = await _setup_with_switch(
        hass, tmp_path, STATE_ON, {"source": "HDMI 2"}, sources=SOURCES, also_stop=True
    )
    hass.states.async_set(SWITCH, STATE_ON)

    await _set_tv(hass, STATE_OFF)

    assert _switched(turn_off) == [[SWITCH]]


async def test_restart_with_the_tv_on_does_not_start_with_chosen_sources(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    turn_on, _ = await _setup_with_switch(
        hass, tmp_path, STATE_UNKNOWN, sources=SOURCES
    )

    await _set_tv(hass, STATE_ON, {"source": "HDMI 2"})

    assert turn_on == []
