"""Light output for loose lights: any colour light through Home Assistant (#9).

Architecture 8.1. Every light gets its own position, so it becomes a channel
for the colour engine. Colours go out with `light.turn_on`, within the rate
limits of `ha_lights_schedule.py`; each light's state is saved when the lights
are claimed and restored when they are released.

The lights follow single lights, a Home Assistant area, or a light group. They
are referenced by entity registry id (stable when the user renames the entity
id), areas by area id. An area's or group's lights are resolved at every
start, so lights that joined follow "ambient" until placed, and lights that
left are no longer used.
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from types import MappingProxyType
from typing import Any

from homeassistant.components.light import (
    ColorMode,
    color_supported,
    get_supported_color_modes,
)
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import color as color_util
from homeassistant.util import dt as dt_util

from ..const import CONF_OUTPUT
from ..core.models import Channel, ChannelColor
from ..core.ports import OutputLost, OutputNotFound, OutputUnavailable
from ..issues import Issue
from .blocks import other_block
from .ha_lights_schedule import NETWORK_COMMANDS_PER_SECOND, Schedule, network_of
from .hue_entertainment import OUTPUT_HUE_ENTERTAINMENT, async_area_lights

_LOGGER = logging.getLogger(__name__)

LIGHT_DOMAIN = "light"

# The output type and its keys in `options["output"]` (architecture 5).
OUTPUT_HA_LIGHTS = "ha_lights"
CONF_FOLLOW = "follow"
FOLLOW_LIGHTS = "lights"
FOLLOW_AREA = "area"
FOLLOW_GROUP = "group"
FOLLOWS = [FOLLOW_LIGHTS, FOLLOW_AREA, FOLLOW_GROUP]
CONF_AREA_ID = "area_id"
CONF_GROUP = "group"
CONF_LIGHTS = "lights"
CONF_ENTITY = "entity"
CONF_X = "x"
CONF_Z = "z"

# Where a light stands: the presets fill in x (-1 left, +1 right) and z (-1
# bottom, +1 top), as the colour engine reads them (architecture 3.1).
# "Ambient" is the middle of the picture.
PRESETS: dict[str, tuple[float, float]] = {
    "left": (-1.0, 0.0),
    "right": (1.0, 0.0),
    "top": (0.0, 1.0),
    "bottom": (0.0, -1.0),
    "ambient": (0.0, 0.0),
}
AMBIENT = PRESETS["ambient"]

ISSUE_NO_LIGHTS = "loose_lights_not_found"
NO_LIGHTS = Issue(
    ISSUE_NO_LIGHTS,
    "ha_lights_not_found",
    "there are no colour lights to follow",
)
# Loose lights that joined the entertainment area later, in the Hue app
# (#198): left out while the area streams to them.
IN_AREA = Issue(
    "loose_lights_in_area",
    "loose_lights_in_area",
    "some loose lights are in the entertainment area too",
)

# The send loop's rhythm: one command per network per tick at most, so never
# more than 4 per second per network (the schedule checks that too).
MAX_RATE_HZ = float(NETWORK_COMMANDS_PER_SECOND)
CHECK_INTERVAL = 5.0
COMMAND_TIMEOUT = 5.0
RESTORE_TIMEOUT = 10.0
# Home Assistant's own light groups, whose members follow their commands.
GROUP_PLATFORM = "group"
# What each start left out because the entertainment area streams to it
# (#198), for the notice: {_left_out_key(): names, or None} (hass.data key).
LEFT_OUT = "glowcast_ha_lights_left_out"
# Where the states restored at a release are kept, per light entity, across
# outputs: every start of the pipeline builds a new output (hass.data key).
RESTORED = "glowcast_ha_lights_restored"
# The attribute that restores a light's colour, per colour mode.
COLOR_ATTRIBUTES = {
    ColorMode.HS: "hs_color",
    ColorMode.XY: "xy_color",
    ColorMode.RGB: "rgb_color",
    ColorMode.RGBW: "rgbw_color",
    ColorMode.RGBWW: "rgbww_color",
    ColorMode.COLOR_TEMP: "color_temp_kelvin",
}


@dataclass(frozen=True)
class Light:
    """One light Glowcast drives: a channel of its own."""

    registry_id: str
    entity_id: str
    name: str


class HaLightsOutput:
    """Drives loose lights with light.turn_on, each at its own position."""

    max_rate_hz = MAX_RATE_HZ
    dropped_parts: Mapping[str, Exception] = MappingProxyType({})  # one part

    def __init__(
        self,
        hass: HomeAssistant,
        output: Mapping[str, Any],
        now: Callable[[], float] | None = None,
        area_lights: Callable[[], Awaitable[set[str]]] | None = None,
        left_out_key: str = "",
    ) -> None:
        """Initialize for the output block in the entry's options.

        `now` is the clock in seconds (the event loop's by default).
        `area_lights` gives the entity ids of the lights an entertainment area
        in the same entry streams to (#198): they are left out, and their
        names kept under `left_out_key` for the notice.
        """
        self._hass = hass
        self._now = now or (lambda: asyncio.get_running_loop().time())
        self._output = dict(output)
        self._area_lights = area_lights
        self._left_out_key = left_out_key
        self._lights: dict[str, Light] = {}
        self._schedule: Schedule | None = None
        self._colors: dict[str, tuple[int, int, int, int]] = {}
        self._sent: dict[str, tuple[int, int, int, int]] = {}
        self._in_flight: dict[str, asyncio.Task[None]] = {}
        # Each light's state from before Glowcast claimed it (for a group, its
        # members' states); None once restored.
        self._saved: list[State] | None = None
        self._next_check = 0.0
        self._release: asyncio.Task[None] | None = None

    async def start(self, *, retry: bool = False) -> list[Channel]:
        """Resolve the lights, save their state and return them as channels.

        Unavailable lights are skipped for this run and keep their position,
        and so are lights the entertainment area of the same entry streams to
        (#198). Each light's state is read from Home Assistant, so `retry`
        changes nothing.
        Raises OutputNotFound when there are no colour lights to follow (the
        area or group was removed, or has none), OutputUnavailable when every
        one of them is unavailable or in the entertainment area.
        """
        if self._saved is not None:
            await self.stop()
        candidates = resolve(self._hass, self._output)
        in_area: list[Light] = []
        if self._area_lights is not None:
            in_area = lights_in(self._hass, candidates, await self._area_lights())
            names = ", ".join(sorted(light.name for light in in_area))
            _left_out(self._hass)[self._left_out_key] = names or None
        if in_area:
            _LOGGER.warning(
                "%s follow the picture through the entertainment area, so they "
                "are left out of the loose lights; choose the loose lights again "
                "in Glowcast's settings",
                names,
            )
            candidates = [light for light in candidates if light not in in_area]
            if not candidates:
                raise OutputUnavailable(
                    "Every loose light is in the entertainment area"
                )
        lights: list[Light] = []
        unavailable = 0
        for light in candidates:
            if _available(self._hass, light.entity_id):
                lights.append(light)
            else:
                unavailable += 1
        if not lights:
            if unavailable:
                raise OutputUnavailable("None of the lights is available")
            raise OutputNotFound("There are no colour lights to follow")
        if unavailable:
            _LOGGER.info("%d of the lights are unavailable; skipped", unavailable)

        networks = {
            light.registry_id: light_networks(self._hass, light.entity_id)
            for light in lights
        }
        crowded = {
            network: count
            for network, count in _count(networks.values()).items()
            if count > NETWORK_COMMANDS_PER_SECOND
        }
        if crowded:
            _LOGGER.warning(
                "%d lights share one Hue bridge or Thread network; each changes "
                "less often than once per second (an entertainment area works "
                "better for Hue lights)",
                max(crowded.values()),
            )
        self._saved = [
            state
            for entity_id in dict.fromkeys(
                member
                for light in lights
                for member in restore_lights(self._hass, light.entity_id)
            )
            if (state := _state_to_restore(self._hass, entity_id)) is not None
        ]
        self._lights = {light.registry_id: light for light in lights}
        self._schedule = Schedule(networks)
        self._colors = {}
        self._sent = {}
        self._next_check = self._now() + CHECK_INTERVAL
        positions = _positions(self._output)
        saved = {state.entity_id: state for state in self._saved}
        return [
            Channel(
                id=light.registry_id,
                name=light.name,
                x=positions.get(light.registry_id, AMBIENT)[0],
                z=positions.get(light.registry_id, AMBIENT)[1],
                start_rgb=_start_rgb(
                    saved.get(member)
                    for member in restore_lights(self._hass, light.entity_id)
                ),
            )
            for light in lights
        ]

    def send(self, colors: list[ChannelColor]) -> None:
        """Keep the colours; send them to the lights whose turn it is."""
        schedule = self._schedule
        if schedule is None:
            return
        for color in colors:
            if color.channel_id in self._lights:
                self._colors[color.channel_id] = _command(color.rgb)
        for registry_id in schedule.due(self._now(), self._ready):
            self._async_send(registry_id, schedule.transition(registry_id))

    async def check(self) -> None:
        """Raise OutputLost when every light has gone; throttled to every 5 s."""
        if self._schedule is None:
            raise OutputLost("The lights are released")
        now = self._now()
        if now < self._next_check:
            return
        self._next_check = now + CHECK_INTERVAL
        if not any(
            _available(self._hass, light.entity_id) for light in self._lights.values()
        ):
            raise OutputLost("None of the lights is available any more")

    async def stop(self) -> None:
        """Restore every light's state; shielded and safe to call twice."""
        if self._release is None or self._release.done():
            self._release = asyncio.ensure_future(self._async_release())
        await asyncio.shield(self._release)

    async def close(self) -> None:
        """Nothing to close: Home Assistant owns the lights' connections."""

    def _ready(self, registry_id: str) -> bool:
        """A light gets a command for a new colour, one at a time, if available."""
        color = self._colors.get(registry_id)
        return (
            color is not None
            and color != self._sent.get(registry_id)
            and registry_id not in self._in_flight
            and _available(self._hass, self._lights[registry_id].entity_id)
        )

    def _async_send(self, registry_id: str, transition: float) -> None:
        color = self._colors[registry_id]
        task = self._hass.async_create_task(
            self._async_turn_on(registry_id, color, transition),
            "glowcast light command",
            eager_start=False,
        )
        self._in_flight[registry_id] = task
        task.add_done_callback(lambda _: self._in_flight.pop(registry_id, None))

    async def _async_turn_on(
        self, registry_id: str, color: tuple[int, int, int, int], transition: float
    ) -> None:
        """Send one colour; it counts as sent only when the light took it."""
        entity_id = self._lights[registry_id].entity_id
        *rgb, brightness = color
        try:
            async with asyncio.timeout(COMMAND_TIMEOUT):
                await self._hass.services.async_call(
                    LIGHT_DOMAIN,
                    "turn_on",
                    {
                        "entity_id": entity_id,
                        "rgb_color": rgb,
                        "brightness": brightness,
                        # The light's actual interval, so a change fades.
                        "transition": transition,
                    },
                    blocking=True,
                )
        except (HomeAssistantError, TimeoutError, ValueError) as err:
            # One lost colour; it is sent again at the light's next turn. The
            # error's text can name the entity, so only its type is logged.
            _LOGGER.debug("A light did not take its colour (%s)", type(err).__name__)
            return
        self._sent[registry_id] = color

    async def _async_release(self) -> None:
        self._schedule = None
        tasks = list(self._in_flight.values())
        for task in tasks:
            task.cancel()
        # A colour that arrives after the restore would undo it.
        await asyncio.gather(*tasks, return_exceptions=True)
        self._in_flight.clear()
        saved, self._saved = self._saved, None
        if not saved:
            return
        try:
            async with asyncio.timeout(RESTORE_TIMEOUT):
                await asyncio.gather(*(_async_restore(self._hass, s) for s in saved))
        except TimeoutError:
            _LOGGER.warning("Could not restore the lights' state in time")
        restored_at = dt_util.utcnow()
        memory = _restored(self._hass)
        for state in saved:
            memory[state.entity_id] = (state, restored_at)


def create(
    hass: HomeAssistant, data: Mapping[str, Any], options: Mapping[str, Any]
) -> HaLightsOutput:
    """Build the output from a Glowcast entry's options (architecture 5).

    With an entertainment area in the same entry, the lights it streams to
    are left out (#198).
    """
    area = other_block(options, OUTPUT_HUE_ENTERTAINMENT)
    if area is None:
        return HaLightsOutput(hass, options[CONF_OUTPUT])
    return HaLightsOutput(
        hass,
        options[CONF_OUTPUT],
        area_lights=partial(async_area_lights, hass, data, area),
        left_out_key=_left_out_key(area, options[CONF_OUTPUT]),
    )


def is_missing(
    hass: HomeAssistant, data: Mapping[str, Any], options: Mapping[str, Any]
) -> bool:
    """Never: no other integration's entry is referenced.

    A removed area, group or light shows at the start, as OutputNotFound.
    """
    return False


def device_id(hass: HomeAssistant, data: Mapping[str, Any]) -> str | None:
    """No device of another integration: the lights each have their own."""
    return None


def resolve(hass: HomeAssistant, output: Mapping[str, Any]) -> list[Light]:
    """The colour lights the output follows now, whether available or not.

    Single lights: the chosen ones that still exist. An area: its colour
    lights, without the light groups in it (their members are in it already).
    A group: its colour members; groups in it are expanded too. Raises
    OutputNotFound when the area or the group no longer exists.
    """
    registry = er.async_get(hass)
    follow = output.get(CONF_FOLLOW, FOLLOW_LIGHTS)
    if follow == FOLLOW_AREA:
        area_id = output.get(CONF_AREA_ID)
        if area_id is None or ar.async_get(hass).async_get_area(area_id) is None:
            raise OutputNotFound("The area no longer exists")
        entity_ids = area_lights(hass, area_id)
    elif follow == FOLLOW_GROUP:
        group = er.async_resolve_entity_id(registry, output.get(CONF_GROUP) or "")
        if group is None:
            raise OutputNotFound("The light group no longer exists")
        state = hass.states.get(group)
        if state is None or state.state == STATE_UNAVAILABLE:
            raise OutputUnavailable("The light group is unavailable")
        entity_ids = group_lights(hass, group)
    else:
        entity_ids = [
            entity_id
            for light in output.get(CONF_LIGHTS, [])
            if (entity_id := er.async_resolve_entity_id(registry, light[CONF_ENTITY]))
        ]
    return [
        light
        for entity_id in dict.fromkeys(entity_ids)
        if (light := _light(hass, registry, entity_id)) is not None
    ]


def colour_lights(hass: HomeAssistant) -> dict[str, str]:
    """{entity id: name} of every colour light in the entity registry.

    Lights without a unique id (only in YAML) cannot be referenced by
    registry id, so they are not offered.
    """
    registry = er.async_get(hass)
    return {
        entry.entity_id: _name(hass, entry)
        for entry in registry.entities.values()
        if entry.domain == LIGHT_DOMAIN
        and not entry.disabled
        and is_colour(hass, entry.entity_id)
    }


def areas_with_lights(hass: HomeAssistant) -> dict[str, str]:
    """{area id: name} of the areas that have colour lights."""
    return {
        area.id: area.name
        for area in ar.async_get(hass).async_list_areas()
        if any(is_colour(hass, entity_id) for entity_id in area_lights(hass, area.id))
    }


def groups_with_lights(hass: HomeAssistant) -> dict[str, str]:
    """{registry id: name} of the light groups that have colour lights.

    Home Assistant's light groups and groups of other integrations (a Hue room
    or zone) list their members in the `entity_id` attribute.
    """
    registry = er.async_get(hass)
    groups: dict[str, str] = {}
    for state in hass.states.async_all(LIGHT_DOMAIN):
        if _members(state) is None:
            continue
        entry = registry.async_get(state.entity_id)
        if entry is None or entry.disabled:
            continue
        if any(
            is_colour(hass, member) for member in group_lights(hass, state.entity_id)
        ):
            groups[entry.id] = _name(hass, entry)
    return groups


async def async_lights_in_area(
    hass: HomeAssistant,
    data: Mapping[str, Any],
    area_output: Mapping[str, Any],
    output: Mapping[str, Any],
) -> str | None:
    """The names of the loose lights that are in the entertainment area too.

    For a combined output (#11): `area_output` is the entertainment area's
    block, `output` the loose lights' (a choice, or what is stored). The area
    drives its lights already, so such a light would get two colours. A
    light group counts when one of its members is in the area. None when
    there are none, or when the bridge cannot be asked.
    """
    taken = await async_area_lights(hass, data, area_output)
    if not taken:
        return None
    try:
        lights = resolve(hass, output)
    except (OutputNotFound, OutputUnavailable):
        return None
    names = sorted(light.name for light in lights_in(hass, lights, taken))
    return ", ".join(names) or None


def lights_in(
    hass: HomeAssistant, lights: Iterable[Light], taken: set[str]
) -> list[Light]:
    """The lights that are among the `taken` entity ids; a light group when
    one of its members is."""
    return [
        light
        for light in lights
        if {light.entity_id, *group_lights(hass, light.entity_id)} & taken
    ]


async def async_in_area_notice(
    hass: HomeAssistant, data: Mapping[str, Any], options: Mapping[str, Any]
) -> Mapping[str, str] | None:
    """The placeholders of IN_AREA when the last start of these loose lights
    left lights out because the entertainment area streams to them, else None
    (also when the bridge could not be asked)."""
    area = other_block(options, OUTPUT_HUE_ENTERTAINMENT)
    if area is None:
        return None
    names = _left_out(hass).get(_left_out_key(area, options[CONF_OUTPUT]))
    return None if names is None else {"lights": names}


def _left_out(hass: HomeAssistant) -> dict[str, str | None]:
    return hass.data.setdefault(LEFT_OUT, {})


def _left_out_key(area: Mapping[str, Any], output: Mapping[str, Any]) -> str:
    """The same for the same area and loose lights, whichever entry has them."""
    return json.dumps([area, output], sort_keys=True)


def area_lights(hass: HomeAssistant, area_id: str) -> list[str]:
    """The light entities in the area: their own area, or their device's.

    Light groups are left out: their members are lights of their own.
    """
    registry = er.async_get(hass)
    devices = dr.async_get(hass)
    lights = []
    for entry in registry.entities.values():
        if entry.domain != LIGHT_DOMAIN or entry.disabled:
            continue
        area = entry.area_id
        if area is None and entry.device_id is not None:
            device = devices.async_get(entry.device_id)
            area = None if device is None else device.area_id
        if area != area_id or _is_group(hass, entry):
            continue
        lights.append(entry.entity_id)
    return sorted(lights)


def group_lights(hass: HomeAssistant, entity_id: str) -> list[str]:
    """The group's member lights; a group in the group is expanded as well."""
    found: list[str] = []
    seen: set[str] = set()

    def expand(group: str) -> None:
        seen.add(group)
        state = hass.states.get(group)
        for member in (_members(state) if state is not None else None) or []:
            if member in seen or not member.startswith(f"{LIGHT_DOMAIN}."):
                continue
            member_state = hass.states.get(member)
            if member_state is not None and _members(member_state) is not None:
                expand(member)
            else:
                seen.add(member)
                found.append(member)

    expand(entity_id)
    return found


def is_colour(hass: HomeAssistant, entity_id: str) -> bool:
    """Whether the light can show colours (from its state or its registry entry)."""
    try:
        return color_supported(get_supported_color_modes(hass, entity_id))
    except HomeAssistantError:
        return False  # not a known entity


def light_networks(hass: HomeAssistant, entity_id: str) -> tuple[str, ...]:
    """The networks of the commands one command to a light causes.

    One entry per command (ha_lights_schedule.py). A Hue room or zone is one
    command on its bridge. A light group of Home Assistant sends a command to
    each member, so it lists each member's network, once per member.
    """
    registry = er.async_get(hass)
    entry = registry.async_get(entity_id)
    if entry is None:
        return ()
    if (network := network_of(entry.platform, entry.config_entry_id)) is not None:
        return (network,)
    if entry.platform != GROUP_PLATFORM:
        return ()
    networks: list[str] = []
    for member in group_lights(hass, entity_id):
        member_entry = registry.async_get(member)
        if member_entry is not None and (
            network := network_of(member_entry.platform, member_entry.config_entry_id)
        ):
            networks.append(network)
    return tuple(networks)


def restore_lights(hass: HomeAssistant, entity_id: str) -> list[str]:
    """The lights whose states are saved and restored for this light.

    For a group (a light group of Home Assistant, a Hue room or zone) its
    members: the group's own state is an average of theirs.
    """
    state = hass.states.get(entity_id)
    if state is not None and _members(state) is not None:
        return group_lights(hass, entity_id)
    return [entity_id]


def crowded_networks(hass: HomeAssistant, entity_ids: Iterable[str]) -> int:
    """The most commands per round on one network, if more than it takes per
    second; else 0. A light group of Home Assistant counts as its members."""
    counts = _count(light_networks(hass, entity_id) for entity_id in entity_ids)
    most = max(counts.values(), default=0)
    return most if most > NETWORK_COMMANDS_PER_SECOND else 0


def registry_id(hass: HomeAssistant, entity_id: str) -> str | None:
    """The entity registry id of a light, to store it by."""
    entry = er.async_get(hass).async_get(entity_id)
    return None if entry is None else entry.id


def _light(
    hass: HomeAssistant, registry: er.EntityRegistry, entity_id: str
) -> Light | None:
    entry = registry.async_get(entity_id)
    if entry is None or entry.disabled or not is_colour(hass, entity_id):
        return None
    return Light(entry.id, entity_id, _name(hass, entry))


def _name(hass: HomeAssistant, entry: er.RegistryEntry) -> str:
    if (state := hass.states.get(entry.entity_id)) is not None and (
        name := state.attributes.get("friendly_name")
    ):
        return str(name)
    return entry.name or entry.original_name or entry.entity_id


def _members(state: State) -> list[str] | None:
    """A group's member entity ids, from its `entity_id` attribute."""
    members = state.attributes.get("entity_id")
    if isinstance(members, (list, tuple, set)) and all(
        isinstance(member, str) for member in members
    ):
        return sorted(members)
    return None


def _is_group(hass: HomeAssistant, entry: er.RegistryEntry) -> bool:
    if entry.platform == GROUP_PLATFORM:
        return True
    state = hass.states.get(entry.entity_id)
    return state is not None and _members(state) is not None


def _available(hass: HomeAssistant, entity_id: str) -> bool:
    state = hass.states.get(entity_id)
    return state is not None and state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN)


def _positions(output: Mapping[str, Any]) -> dict[str, tuple[float, float]]:
    """{registry id: (x, z)} of the lights placed so far."""
    return {
        light[CONF_ENTITY]: (float(light[CONF_X]), float(light[CONF_Z]))
        for light in output.get(CONF_LIGHTS, [])
        if CONF_X in light and CONF_Z in light
    }


def _count(networks: Iterable[tuple[str, ...]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for light_networks_ in networks:
        for network in light_networks_:
            counts[network] = counts.get(network, 0) + 1
    return counts


def _command(rgb: tuple[float, float, float]) -> tuple[int, int, int, int]:
    """A colour in 0-1 as (r, g, b, brightness) for light.turn_on.

    Lights take `rgb_color` as the colour only, so the brightness that
    Glowcast's settings and the glow put into the colour goes into
    `brightness`, and the colour is scaled up to full.
    """
    peak = max(rgb)
    if peak <= 0:
        return (255, 255, 255, 1)
    r, g, b = (min(255, max(0, round(value / peak * 255))) for value in rgb)
    return (r, g, b, min(255, max(1, round(peak * 255))))


def _start_rgb(states: Iterable[State | None]) -> tuple[float, float, float] | None:
    """What a light showed before the claim, as Glowcast would send it (#153).

    From the states saved for the restore, so a light that has not reported
    its restored state yet counts with the state from before. For a group, the
    average of its members. None when none of them is on or off.
    """
    colors = [rgb for state in states if (rgb := _state_rgb(state)) is not None]
    if not colors:
        return None
    r, g, b = (sum(values) / len(colors) for values in zip(*colors, strict=True))
    return r, g, b


def _state_rgb(state: State | None) -> tuple[float, float, float] | None:
    """A light's state as a colour in 0-1, its brightness included; the
    inverse of _command(). Black when it is off."""
    if state is None or state.state not in (STATE_ON, STATE_OFF):
        return None
    if state.state == STATE_OFF:
        return 0.0, 0.0, 0.0
    attributes = state.attributes
    rgb: tuple[float, ...] = (255.0, 255.0, 255.0)  # on, without a colour
    kelvin = attributes.get("color_temp_kelvin")
    if attributes.get("color_mode") == ColorMode.COLOR_TEMP and kelvin:
        rgb = color_util.color_temperature_to_rgb(kelvin)
    elif value := attributes.get("rgb_color"):
        rgb = tuple(value)
    elif value := attributes.get("hs_color"):
        rgb = color_util.color_hs_to_RGB(*value)
    elif value := attributes.get("xy_color"):
        rgb = color_util.color_xy_to_RGB(*value)
    elif kelvin:
        rgb = color_util.color_temperature_to_rgb(kelvin)
    peak = max(rgb)
    if peak <= 0:
        return 0.0, 0.0, 0.0
    level = (attributes.get("brightness") or 255) / 255
    r, g, b = (min(1.0, max(0.0, value / peak * level)) for value in rgb)
    return r, g, b


def _restored(hass: HomeAssistant) -> dict[str, tuple[State, datetime]]:
    """{entity id: (state, when)} restored at the last release of each light."""
    return hass.data.setdefault(RESTORED, {})


def _state_to_restore(hass: HomeAssistant, entity_id: str) -> State | None:
    """A light's state to restore later: its own, not Glowcast's colour.

    Claimed again soon after a release (the picture is back, a retry, or a
    restart with new settings, which builds a new output), a light may not
    have reported its restored state yet (a Hue light reports about once per
    second, #125); then the state restored at that release is saved again.
    """
    state = hass.states.get(entity_id)
    memory = _restored(hass)
    if (restored := memory.get(entity_id)) is not None:
        if state is not None and state.last_updated <= restored[1]:
            return restored[0]
        del memory[entity_id]  # reported since: its own state again
    return state


async def _async_restore(hass: HomeAssistant, state: State) -> None:
    """Send a light back to a saved state, also when Home Assistant already
    shows that state (the light's report of Glowcast's colour may be late).
    """
    if state.state == STATE_OFF:
        service, data = "turn_off", {}
    elif state.state == STATE_ON:
        service, data = "turn_on", {}
        mode = state.attributes.get("color_mode")
        if (brightness := state.attributes.get("brightness")) is not None:
            data["brightness"] = brightness
        if (attribute := COLOR_ATTRIBUTES.get(mode)) is not None and (
            value := state.attributes.get(attribute)
        ) is not None:
            data[attribute] = value
    else:
        return  # unavailable or unknown: nothing to restore
    try:
        await hass.services.async_call(
            LIGHT_DOMAIN,
            service,
            {"entity_id": state.entity_id, **data},
            blocking=True,
        )
    except (HomeAssistantError, ValueError) as err:
        _LOGGER.warning("Could not restore a light's state (%s)", type(err).__name__)
