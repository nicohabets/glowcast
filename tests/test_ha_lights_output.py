"""The Home Assistant lights output (#9): lights, areas and groups, rates and restore.

Real Home Assistant registries and states, with the light services replaced by
recorders (no light integration runs).
"""

import asyncio
from collections.abc import Generator
from typing import Any

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.glowcast.core.light_test import STEP
from custom_components.glowcast.core.models import ChannelColor
from custom_components.glowcast.core.pipeline import run_light_test
from custom_components.glowcast.core.ports import (
    OutputLost,
    OutputNotFound,
    OutputUnavailable,
)
from custom_components.glowcast.outputs.ha_lights import (
    HaLightsOutput,
    area_lights,
    areas_with_lights,
    colour_lights,
    crowded_networks,
    group_lights,
    groups_with_lights,
    light_networks,
    resolve,
)

from .fakes import FakeClock

ON = {"color_mode": "xy", "xy_color": [0.3, 0.3], "brightness": 100}


class Clock:
    """Time only moves when the test says so."""

    def __init__(self) -> None:
        self.time = 0.0

    def __call__(self) -> float:
        return self.time


@pytest.fixture
def hue(hass: HomeAssistant) -> Generator[MockConfigEntry]:
    """A loaded hue entry (only its state: the integration does not run)."""
    entry = MockConfigEntry(
        domain="hue",
        unique_id="bridge-1",
        data={"host": "bridge.example", "api_key": "ha-key", "api_version": 2},
        state=ConfigEntryState.LOADED,
    )
    entry.add_to_hass(hass)
    yield entry
    # Not unloaded at teardown: the integration was never set up.
    entry.mock_state(hass, ConfigEntryState.NOT_LOADED)


@pytest.fixture
def matter(hass: HomeAssistant) -> Generator[MockConfigEntry]:
    entry = MockConfigEntry(domain="matter", state=ConfigEntryState.LOADED)
    entry.add_to_hass(hass)
    yield entry
    entry.mock_state(hass, ConfigEntryState.NOT_LOADED)


@pytest.fixture
def turn_on(hass: HomeAssistant) -> list[ServiceCall]:
    return async_mock_service(hass, "light", "turn_on")


@pytest.fixture
def turn_off(hass: HomeAssistant) -> list[ServiceCall]:
    return async_mock_service(hass, "light", "turn_off")


def add_light(
    hass: HomeAssistant,
    name: str,
    platform: str = "zha",
    entry: MockConfigEntry | None = None,
    *,
    state: str = "on",
    modes: tuple[str, ...] = ("xy", "color_temp"),
    area_id: str | None = None,
    members: list[str] | None = None,
) -> er.RegistryEntry:
    """A light in the entity registry with a state."""
    registry = er.async_get(hass)
    light = registry.async_get_or_create(
        "light",
        platform,
        name,
        config_entry=entry,
        suggested_object_id=name,
        original_name=name.replace("_", " ").title(),
    )
    if area_id is not None:
        light = registry.async_update_entity(light.entity_id, area_id=area_id)
    attributes: dict[str, Any] = {
        "supported_color_modes": list(modes),
        "friendly_name": name.replace("_", " ").title(),
    }
    if state == "on":
        attributes |= ON
    if members is not None:
        attributes["entity_id"] = members
    hass.states.async_set(light.entity_id, state, attributes)
    return light


def lights_output(*lights: er.RegistryEntry, **positions: tuple[float, float]):
    """The output block for single lights; positions by light name."""
    return {
        "type": "ha_lights",
        "follow": "lights",
        "lights": [
            {"entity": light.id, "x": pos[0], "z": pos[1]}
            if (pos := positions.get(light.entity_id.split(".")[1]))
            else {"entity": light.id}
            for light in lights
        ],
    }


def colors(output: HaLightsOutput, channels, rgb=(0.8, 0.0, 0.0)) -> None:
    output.send([ChannelColor(channel.id, rgb) for channel in channels])


# Choosing the lights


async def test_single_lights_become_channels_at_their_positions(
    hass: HomeAssistant,
) -> None:
    left = add_light(hass, "floor_lamp")
    other = add_light(hass, "strip")
    output = HaLightsOutput(hass, lights_output(left, other, floor_lamp=(-1, 0.0)))

    channels = await output.start()

    assert [(c.id, c.name, c.x, c.z) for c in channels] == [
        (left.id, "Floor Lamp", -1.0, 0.0),
        # Not placed yet: ambient.
        (other.id, "Strip", 0.0, 0.0),
    ]
    await output.stop()


async def test_a_light_without_colour_is_not_used(hass: HomeAssistant) -> None:
    white = add_light(hass, "white", modes=("color_temp",))
    colour = add_light(hass, "colour")
    assert set(colour_lights(hass)) == {colour.entity_id}
    output = HaLightsOutput(hass, lights_output(white, colour))
    assert [c.id for c in await output.start()] == [colour.id]
    await output.stop()


async def test_lights_are_found_by_registry_id_after_a_rename(
    hass: HomeAssistant,
) -> None:
    light = add_light(hass, "lamp")
    block = lights_output(light)
    renamed = er.async_get(hass).async_update_entity(
        light.entity_id, new_entity_id="light.renamed"
    )
    hass.states.async_set("light.renamed", "on", {"supported_color_modes": ["xy"]})
    assert [found.entity_id for found in resolve(hass, block)] == [renamed.entity_id]


async def test_an_area_uses_its_colour_lights_and_their_devices_area(
    hass: HomeAssistant,
) -> None:
    area = ar.async_get(hass).async_create("Living room")
    other = ar.async_get(hass).async_create("Kitchen")
    own = add_light(hass, "own", area_id=area.id)
    device_entry = MockConfigEntry(domain="zha")
    device_entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=device_entry.entry_id, identifiers={("zha", "bulb")}
    )
    dr.async_get(hass).async_update_device(device.id, area_id=area.id)
    via_device = add_light(hass, "via_device")
    er.async_get(hass).async_update_entity(via_device.entity_id, device_id=device.id)
    add_light(hass, "elsewhere", area_id=other.id)
    # A Hue room in the area: its members are lights of their own.
    add_light(hass, "room", "hue", area_id=area.id, members=[own.entity_id])

    assert area_lights(hass, area.id) == sorted([own.entity_id, via_device.entity_id])
    assert areas_with_lights(hass) == {area.id: "Living room", other.id: "Kitchen"}
    found = resolve(hass, {"follow": "area", "area_id": area.id})
    assert {light.entity_id for light in found} == {
        own.entity_id,
        via_device.entity_id,
    }


async def test_a_removed_area_is_not_found(hass: HomeAssistant) -> None:
    output = HaLightsOutput(hass, {"follow": "area", "area_id": "gone"})
    with pytest.raises(OutputNotFound):
        await output.start()


async def test_a_group_uses_its_members_and_groups_in_it(hass: HomeAssistant) -> None:
    one = add_light(hass, "one")
    two = add_light(hass, "two")
    three = add_light(hass, "three")
    inner = add_light(hass, "inner", "group", members=[two.entity_id, "light.outer"])
    outer = add_light(
        hass, "outer", "group", members=[one.entity_id, inner.entity_id, "switch.x"]
    )
    add_light(hass, "room", "hue", members=[three.entity_id])

    # Nested groups are expanded; a group in a loop is seen once.
    assert sorted(group_lights(hass, outer.entity_id)) == sorted(
        [one.entity_id, two.entity_id]
    )
    groups = groups_with_lights(hass)
    assert set(groups.values()) == {"Inner", "Outer", "Room"}
    found = resolve(hass, {"follow": "group", "group": outer.id})
    assert {light.entity_id for light in found} == {one.entity_id, two.entity_id}


async def test_a_removed_group_is_not_found_and_an_unavailable_one_waits(
    hass: HomeAssistant,
) -> None:
    group = add_light(hass, "room", "hue", members=[], state="unavailable")
    with pytest.raises(OutputUnavailable):
        await HaLightsOutput(hass, {"follow": "group", "group": group.id}).start()
    er.async_get(hass).async_remove(group.entity_id)
    with pytest.raises(OutputNotFound):
        await HaLightsOutput(hass, {"follow": "group", "group": group.id}).start()


async def test_unavailable_lights_are_skipped_for_the_run(hass: HomeAssistant) -> None:
    up = add_light(hass, "up")
    down = add_light(hass, "down", state="unavailable")
    output = HaLightsOutput(hass, lights_output(up, down))
    assert [c.id for c in await output.start()] == [up.id]
    await output.stop()


async def test_no_colour_lights_left_is_not_found(hass: HomeAssistant) -> None:
    area = ar.async_get(hass).async_create("Empty")
    add_light(hass, "white", modes=("onoff",), area_id=area.id)
    with pytest.raises(OutputNotFound):
        await HaLightsOutput(hass, {"follow": "area", "area_id": area.id}).start()
    gone = add_light(hass, "gone")
    block = lights_output(gone)
    er.async_get(hass).async_remove(gone.entity_id)
    with pytest.raises(OutputNotFound):
        await HaLightsOutput(hass, block).start()


async def test_only_unavailable_lights_are_unavailable(hass: HomeAssistant) -> None:
    down = add_light(hass, "down", state="unavailable")
    with pytest.raises(OutputUnavailable):
        await HaLightsOutput(hass, lights_output(down)).start()


# Networks


async def test_networks_come_from_the_lights_integration(
    hass: HomeAssistant, hue: MockConfigEntry, matter: MockConfigEntry
) -> None:
    bulb = add_light(hass, "bulb", "hue", hue)
    thread = add_light(hass, "thread", "matter", matter)
    zigbee = add_light(hass, "zigbee", "zha")
    proxy = add_light(hass, "proxy", "bench_proxy")
    room = add_light(hass, "room", "hue", hue, members=[bulb.entity_id])
    group = add_light(
        hass, "mixed", "group", members=[bulb.entity_id, thread.entity_id]
    )

    assert light_networks(hass, bulb.entity_id) == (f"hue:{hue.entry_id}",)
    assert light_networks(hass, thread.entity_id) == (f"matter:{matter.entry_id}",)
    assert light_networks(hass, zigbee.entity_id) == ()
    assert light_networks(hass, proxy.entity_id) == ()
    # A Hue room is one command on its bridge.
    assert light_networks(hass, room.entity_id) == (f"hue:{hue.entry_id}",)
    # A light group of Home Assistant commands each member.
    assert light_networks(hass, group.entity_id) == (
        f"hue:{hue.entry_id}",
        f"matter:{matter.entry_id}",
    )


async def test_five_lights_on_a_bridge_are_crowded(
    hass: HomeAssistant, hue: MockConfigEntry, matter: MockConfigEntry
) -> None:
    hue_lights = [add_light(hass, f"hue{i}", "hue", hue).entity_id for i in range(5)]
    thread = [add_light(hass, f"m{i}", "matter", matter).entity_id for i in range(4)]
    others = [add_light(hass, f"z{i}").entity_id for i in range(9)]
    assert crowded_networks(hass, hue_lights[:4] + thread + others) == 0
    assert crowded_networks(hass, hue_lights + thread) == 5


# Sending


async def test_colours_go_out_with_brightness_and_a_transition(
    hass: HomeAssistant, turn_on: list[ServiceCall]
) -> None:
    light = add_light(hass, "lamp")
    clock = Clock()
    output = HaLightsOutput(hass, lights_output(light), now=clock)
    channels = await output.start()

    colors(output, channels, (0.4, 0.2, 0.0))
    await hass.async_block_till_done()

    (call,) = turn_on
    assert call.data == {
        "entity_id": light.entity_id,
        # The colour at full; Glowcast's brightness goes into brightness.
        "rgb_color": [255, 128, 0],
        "brightness": 102,
        "transition": 1.0,
    }
    await output.stop()


async def test_each_light_at_most_once_per_second_and_only_on_a_change(
    hass: HomeAssistant, turn_on: list[ServiceCall]
) -> None:
    light = add_light(hass, "lamp")
    clock = Clock()
    output = HaLightsOutput(hass, lights_output(light), now=clock)
    channels = await output.start()

    for step in range(12):  # 3 s at 4 ticks per second
        clock.time = step * 0.25
        colors(output, channels, (0.1 * (1 + step % 2), 0.0, 0.0))
        await hass.async_block_till_done()
    assert len(turn_on) == 3

    # The same colour is not sent again.
    for step in range(12, 20):
        clock.time = step * 0.25
        colors(output, channels, (0.5, 0.5, 0.5))
        await hass.async_block_till_done()
    assert len(turn_on) == 4
    await output.stop()


async def test_never_more_than_four_commands_per_second_per_bridge(
    hass: HomeAssistant,
    turn_on: list[ServiceCall],
    hue: MockConfigEntry,
    matter: MockConfigEntry,
) -> None:
    hue_lights = [add_light(hass, f"hue{i}", "hue", hue) for i in range(6)]
    thread = [add_light(hass, f"m{i}", "matter", matter) for i in range(2)]
    clock = Clock()
    output = HaLightsOutput(hass, lights_output(*hue_lights, *thread), now=clock)
    channels = await output.start()
    sent: list[tuple[float, str, float]] = []
    for step in range(40):  # 10 s
        clock.time = step * 0.25
        before = len(turn_on)
        colors(output, channels, (0.1 + 0.01 * step, 0.0, 0.0))
        await hass.async_block_till_done()
        sent += [
            (clock.time, call.data["entity_id"], call.data["transition"])
            for call in turn_on[before:]
        ]

    hue_ids = {light.entity_id for light in hue_lights}
    hue_times = [t for t, entity_id, _ in sent if entity_id in hue_ids]
    for start in hue_times:
        assert sum(1 for t in hue_times if start <= t < start + 1) <= 4
    # Six lights share four commands: each gets one every 1.5 s, and fades so long.
    assert {tr for _, entity_id, tr in sent if entity_id in hue_ids} == {1.5}
    assert {tr for _, entity_id, tr in sent if entity_id not in hue_ids} == {1.0}
    thread_times = [t for t, entity_id, _ in sent if entity_id not in hue_ids]
    assert len(thread_times) == 20  # each Matter light once per second
    await output.stop()


async def test_an_unavailable_light_gets_no_commands(
    hass: HomeAssistant, turn_on: list[ServiceCall]
) -> None:
    light = add_light(hass, "lamp")
    output = HaLightsOutput(hass, lights_output(light), now=Clock())
    channels = await output.start()
    hass.states.async_set(light.entity_id, "unavailable")
    colors(output, channels)
    await hass.async_block_till_done()
    assert turn_on == []
    await output.stop()


async def test_check_is_throttled_and_lost_when_every_light_is_gone(
    hass: HomeAssistant,
) -> None:
    light = add_light(hass, "lamp")
    clock = Clock()
    output = HaLightsOutput(hass, lights_output(light), now=clock)
    await output.start()
    hass.states.async_set(light.entity_id, "unavailable")
    await output.check()  # not yet asked: within 5 s of the start
    clock.time = 5.0
    with pytest.raises(OutputLost):
        await output.check()
    await output.stop()


# Restoring


async def test_stop_restores_every_light_and_is_safe_twice(
    hass: HomeAssistant, turn_on: list[ServiceCall], turn_off: list[ServiceCall]
) -> None:
    was_on = add_light(hass, "was_on")
    was_off = add_light(hass, "was_off", state="off")
    output = HaLightsOutput(hass, lights_output(was_on, was_off), now=Clock())
    channels = await output.start()
    colors(output, channels)
    await hass.async_block_till_done()
    # The lights now show Glowcast's colour.
    for light in (was_on, was_off):
        hass.states.async_set(
            light.entity_id,
            "on",
            {
                "supported_color_modes": ["xy", "color_temp"],
                "color_mode": "hs",
                "hs_color": [0, 100],
                "brightness": 204,
            },
        )
    turn_on.clear()

    await output.stop()
    await output.stop()
    await output.close()

    assert [call.data["entity_id"] for call in turn_off] == [was_off.entity_id]
    (restored,) = turn_on
    assert restored.data["entity_id"] == was_on.entity_id
    assert restored.data["xy_color"] == [0.3, 0.3]
    assert restored.data["brightness"] == 100
    # Released: no more commands.
    colors(output, channels, (0.1, 0.2, 0.3))
    await hass.async_block_till_done()
    assert len(turn_on) == 1


async def test_a_command_on_its_way_is_cancelled_before_the_restore(
    hass: HomeAssistant,
) -> None:
    events: list[str] = []

    async def slow(call: ServiceCall) -> None:
        if "rgb_color" not in call.data:
            events.append("restore")
            return
        events.append("colour")
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            events.append("colour cancelled")
            raise

    hass.services.async_register("light", "turn_on", slow)
    light = add_light(hass, "lamp")
    output = HaLightsOutput(hass, lights_output(light), now=Clock())
    channels = await output.start()
    colors(output, channels)
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert events == ["colour"]
    async with asyncio.timeout(2):
        await output.stop()
    # The late colour cannot undo the restore: it ends first.
    assert events == ["colour", "colour cancelled", "restore"]


async def test_start_again_after_stop_and_close(
    hass: HomeAssistant, turn_on: list[ServiceCall]
) -> None:
    light = add_light(hass, "lamp")
    clock = Clock()
    output = HaLightsOutput(hass, lights_output(light), now=clock)
    await output.start()
    await output.stop()
    await output.close()
    channels = await output.start()
    colors(output, channels)
    await hass.async_block_till_done()
    assert len([call for call in turn_on if "rgb_color" in call.data]) == 1
    await output.stop()


async def test_a_claim_soon_after_a_release_restores_the_state_from_before(
    hass: HomeAssistant, turn_on: list[ServiceCall], turn_off: list[ServiceCall]
) -> None:
    """A light reports its restored state late (Hue: about once per second,
    #125); a claim before then must not save Glowcast's colour as its state."""
    was_off = add_light(hass, "was_off", state="off")
    output = HaLightsOutput(hass, lights_output(was_off), now=Clock())
    await output.start()
    glowcast_red = {
        "supported_color_modes": ["xy", "color_temp"],
        "color_mode": "hs",
        "hs_color": [0, 100],
        "brightness": 204,
    }
    hass.states.async_set(was_off.entity_id, "on", glowcast_red)
    await output.stop()  # no picture: released, the state not yet reported
    assert len(turn_off) == 1

    await output.start()  # the picture is back
    await output.stop()
    assert len(turn_off) == 2

    # A state reported after the release is the light's own again.
    hass.states.async_set(was_off.entity_id, "on", {**glowcast_red, **ON})
    await output.start()
    hass.states.async_set(was_off.entity_id, "on", glowcast_red)
    turn_on.clear()
    await output.stop()
    assert [call.data.get("xy_color") for call in turn_on] == [[0.3, 0.3]]
    assert len(turn_off) == 2


def approx_rgb(rgb: tuple[float, float, float]) -> Any:
    return pytest.approx(rgb, abs=0.01)


@pytest.mark.parametrize(
    ("state", "attributes", "start"),
    [
        # As Glowcast would send it: the colour scaled to full, times brightness.
        (
            "on",
            {"color_mode": "rgb", "rgb_color": [255, 128, 0], "brightness": 102},
            (0.4, 0.2, 0.0),
        ),
        (
            "on",
            {"color_mode": "hs", "hs_color": [30, 50], "brightness": 255},
            (1.0, 0.75, 0.50),
        ),
        (
            "on",
            {"color_mode": "xy", "xy_color": [0.3, 0.3], "brightness": 255},
            (0.88, 0.89, 1.0),
        ),
        # A warm white: from its colour temperature, also when it reports a
        # colour too.
        (
            "on",
            {
                "color_mode": "color_temp",
                "color_temp_kelvin": 2700,
                "rgb_color": [1, 2, 3],
                "brightness": 255,
            },
            (1.0, 0.654, 0.343),
        ),
        ("on", {"color_mode": "onoff"}, (1.0, 1.0, 1.0)),  # on, nothing else
        ("off", {}, (0.0, 0.0, 0.0)),
        ("unavailable", {}, None),
    ],
)
async def test_each_light_reports_what_it_showed_before(
    hass: HomeAssistant,
    turn_on: list[ServiceCall],
    turn_off: list[ServiceCall],
    state: str,
    attributes: dict[str, Any],
    start: tuple[float, float, float] | None,
) -> None:
    """For the fade-in after a claim (#153): the light's own state as it
    would be sent, so the first colours fade in from there."""
    lamp = add_light(hass, "lamp")
    other = add_light(hass, "other")  # so the output starts in every case
    hass.states.async_set(
        lamp.entity_id,
        state,
        {"supported_color_modes": ["xy", "color_temp"], **attributes},
    )
    output = HaLightsOutput(hass, lights_output(lamp, other), now=Clock())
    channels = await output.start()
    lamp_channel = [c for c in channels if c.id == lamp.id]
    if state == "unavailable":
        assert lamp_channel == []  # skipped for the run
        return
    [channel] = lamp_channel
    assert channel.start_rgb == approx_rgb(start)
    await output.stop()


async def test_a_claim_soon_after_a_release_fades_in_from_the_state_from_before(
    hass: HomeAssistant, turn_on: list[ServiceCall], turn_off: list[ServiceCall]
) -> None:
    """The light still shows Glowcast's colour in Home Assistant (#125)."""
    was_off = add_light(hass, "was_off", state="off")
    output = HaLightsOutput(hass, lights_output(was_off), now=Clock())
    await output.start()
    hass.states.async_set(was_off.entity_id, "on", GLOWCAST_RED)
    await output.stop()  # no picture: released, the state not yet reported
    [channel] = await output.start()  # the picture is back
    assert channel.start_rgb == (0.0, 0.0, 0.0)
    await output.stop()


async def test_a_group_starts_from_its_members_on_average(
    hass: HomeAssistant, turn_on: list[ServiceCall], turn_off: list[ServiceCall]
) -> None:
    off = add_light(hass, "member_off", state="off")
    on = add_light(hass, "member_on")
    hass.states.async_set(
        on.entity_id,
        "on",
        {
            "supported_color_modes": ["xy"],
            "color_mode": "rgb",
            "rgb_color": [255, 0, 0],
            "brightness": 255,
        },
    )
    group = add_light(hass, "room", "group", members=[off.entity_id, on.entity_id])
    output = HaLightsOutput(hass, lights_output(group), now=Clock())
    [channel] = await output.start()
    assert channel.start_rgb == approx_rgb((0.5, 0.0, 0.0))
    await output.stop()


GLOWCAST_RED = {
    "supported_color_modes": ["xy", "color_temp"],
    "color_mode": "hs",
    "hs_color": [0, 100],
    "brightness": 204,
}


async def test_a_new_output_soon_after_a_release_restores_the_state_from_before(
    hass: HomeAssistant, turn_on: list[ServiceCall], turn_off: list[ServiceCall]
) -> None:
    """A restart with new settings builds a new output: the memory of what was
    restored stays, so the light's late report is not saved as its state."""
    was_off = add_light(hass, "was_off", state="off")
    first = HaLightsOutput(hass, lights_output(was_off), now=Clock())
    await first.start()
    hass.states.async_set(was_off.entity_id, "on", GLOWCAST_RED)
    await first.stop()
    second = HaLightsOutput(hass, lights_output(was_off), now=Clock())
    await second.start()
    await second.stop()
    assert len(turn_off) == 2
    assert [call for call in turn_on if "hs_color" in call.data] == []


async def test_the_light_test_while_off_restores_then_a_switch_on_claims_again(
    hass: HomeAssistant, turn_on: list[ServiceCall], turn_off: list[ServiceCall]
) -> None:
    """The light test while Glowcast is off (#182, #270): each light its side,
    then white; the lights back as before; and a switch-on soon after, whose
    new output must not save the test's colour as the lights' state."""
    left = add_light(hass, "left")
    right = add_light(hass, "right", state="off")
    block = lights_output(left, right, left=(-1.0, 0.0), right=(1.0, 0.0))
    clock = FakeClock()
    output = HaLightsOutput(hass, block, now=clock.now)

    async def runner(func: Any, *args: Any) -> Any:
        return func(*args)

    test = asyncio.ensure_future(run_light_test(output, 1.0, runner, clock))
    await clock.advance(1.0)
    await hass.async_block_till_done()
    # The lights report the test's colour (Home Assistant's state lags).
    for light in (left, right):
        hass.states.async_set(light.entity_id, "on", GLOWCAST_RED)
    await clock.advance(2 * STEP)
    await hass.async_block_till_done()
    assert await test is None

    shown: dict[str, list[list[int]]] = {}
    for call in turn_on:
        if "rgb_color" in call.data:
            shown.setdefault(call.data["entity_id"], []).append(call.data["rgb_color"])
    assert shown[left.entity_id][0] == [255, 0, 0]
    assert shown[right.entity_id][0] == [0, 0, 255]
    for entity_id in (left.entity_id, right.entity_id):
        assert shown[entity_id][-1] == [255, 255, 255]
    # Back as before: the one that was on gets its colour, the other is off.
    restores = [call.data for call in turn_on if "rgb_color" not in call.data]
    assert restores == [
        {"entity_id": left.entity_id, "brightness": 100, "xy_color": [0.3, 0.3]}
    ]
    assert [call.data["entity_id"] for call in turn_off] == [right.entity_id]

    # Switched on before the lights report their restored state.
    turn_on.clear()
    pipeline = HaLightsOutput(hass, block, now=Clock())
    channels = await pipeline.start()
    colors(pipeline, channels)
    await hass.async_block_till_done()
    assert {call.data["entity_id"] for call in turn_on} == {
        left.entity_id,
        right.entity_id,
    }
    turn_on.clear()
    await pipeline.stop()
    await pipeline.close()
    # Still the state from before the test, not the test's colour.
    assert [call.data for call in turn_on] == [
        {"entity_id": left.entity_id, "brightness": 100, "xy_color": [0.3, 0.3]}
    ]
    assert [call.data["entity_id"] for call in turn_off] == [
        right.entity_id,
        right.entity_id,
    ]


async def test_a_stop_before_the_light_reports_its_colour_still_restores(
    hass: HomeAssistant, turn_on: list[ServiceCall], turn_off: list[ServiceCall]
) -> None:
    """Home Assistant still shows the state from before: restore anyway."""
    was_off = add_light(hass, "was_off", state="off")
    was_on = add_light(hass, "was_on")
    output = HaLightsOutput(hass, lights_output(was_off, was_on), now=Clock())
    channels = await output.start()
    colors(output, channels)
    await hass.async_block_till_done()
    turn_on.clear()
    await output.stop()
    assert [call.data["entity_id"] for call in turn_off] == [was_off.entity_id]
    assert [call.data for call in turn_on] == [
        {"entity_id": was_on.entity_id, "brightness": 100, "xy_color": [0.3, 0.3]}
    ]


@pytest.mark.parametrize("platform", ["group", "hue"])
async def test_a_group_picked_as_a_light_restores_its_members(
    hass: HomeAssistant,
    turn_on: list[ServiceCall],
    turn_off: list[ServiceCall],
    platform: str,
) -> None:
    """The group's own state is an average of its members' states."""
    off = add_light(hass, "member_off", state="off")
    on = add_light(hass, "member_on")
    group = add_light(hass, "room", platform, members=[off.entity_id, on.entity_id])
    output = HaLightsOutput(hass, lights_output(group), now=Clock())
    channels = await output.start()
    assert [channel.id for channel in channels] == [group.id]
    colors(output, channels)
    await hass.async_block_till_done()
    assert [call.data["entity_id"] for call in turn_on] == [group.entity_id]
    turn_on.clear()
    await output.stop()
    assert [call.data["entity_id"] for call in turn_off] == [off.entity_id]
    assert [call.data["entity_id"] for call in turn_on] == [on.entity_id]


async def test_a_light_group_of_home_assistant_counts_as_its_members(
    hass: HomeAssistant, hue: MockConfigEntry
) -> None:
    members = [add_light(hass, f"m{i}", "hue", hue).entity_id for i in range(3)]
    group = add_light(hass, "group", "group", members=members)
    room = add_light(hass, "room", "hue", hue, members=members)
    singles = [add_light(hass, f"s{i}", "hue", hue).entity_id for i in range(2)]
    network = f"hue:{hue.entry_id}"
    assert light_networks(hass, group.entity_id) == (network,) * 3
    # A Hue room is one command on its bridge.
    assert light_networks(hass, room.entity_id) == (network,)
    assert crowded_networks(hass, [group.entity_id, *singles]) == 5
    assert crowded_networks(hass, [room.entity_id, *singles]) == 0


async def test_a_colour_the_light_did_not_take_is_sent_again(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    calls: list[dict[str, Any]] = []

    async def flaky(call: ServiceCall) -> None:
        calls.append(dict(call.data))
        if len(calls) == 1:
            raise HomeAssistantError(f"{call.data['entity_id']} did not answer")

    hass.services.async_register("light", "turn_on", flaky)
    light = add_light(hass, "lamp")
    clock = Clock()
    output = HaLightsOutput(hass, lights_output(light), now=clock)
    channels = await output.start()
    caplog.set_level("DEBUG")
    colors(output, channels)
    await hass.async_block_till_done()
    clock.time = 1.0
    colors(output, channels)  # the same still picture
    await hass.async_block_till_done()
    assert [call.get("rgb_color") for call in calls] == [[255, 0, 0], [255, 0, 0]]
    # The error's text can name the entity: only its type is logged.
    assert "did not answer" not in caplog.text
    assert "HomeAssistantError" in caplog.text
    await output.stop()
