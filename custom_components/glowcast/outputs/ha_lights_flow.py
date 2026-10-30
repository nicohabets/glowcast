"""The loose-lights output type's steps (architecture 4.1 and 4.2, #9).

The same steps at setup and in the settings screen: what the lights follow
(single lights, an area or a light group), the lights or the area or group,
a hint when Hue lights could join an entertainment area instead, a position
per light (a preset, or fine-tuned), and a warning when more lights share one
Hue bridge or Thread network than it takes commands per second.
"""

from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntryBaseFlow,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from ..const import CONF_OUTPUT, CONF_TYPE
from ..core.ports import OutputNotFound, OutputUnavailable
from ..flow import dropdown
from .ha_lights import (
    AMBIENT,
    CONF_AREA_ID,
    CONF_ENTITY,
    CONF_FOLLOW,
    CONF_GROUP,
    CONF_LIGHTS,
    CONF_X,
    CONF_Z,
    FOLLOW_AREA,
    FOLLOW_GROUP,
    FOLLOW_LIGHTS,
    FOLLOWS,
    LIGHT_DOMAIN,
    OUTPUT_HA_LIGHTS,
    PRESETS,
    Light,
    areas_with_lights,
    colour_lights,
    crowded_networks,
    groups_with_lights,
    registry_id,
    resolve,
)
from .ha_lights_schedule import NETWORK_COMMANDS_PER_SECOND
from .hue_entertainment import HINT_AREA, HINT_NO_AREA, async_entertainment_hint

# Form field names.
FIELD_FOLLOW = "follow"
FIELD_LIGHTS = "lights"
FIELD_AREA = "area"
FIELD_GROUP = "group"
FIELD_POSITION = "position"
FIELD_X = "x"
FIELD_Z = "z"
CUSTOM = "custom"
# How to make an entertainment area in the Hue app (the README's section).
ENTERTAINMENT_AREA_HELP = (
    "https://github.com/nicohabets/glowcast#make-an-entertainment-area-in-the-hue-app"
)
NONE_TEXT = "-"


class HaLightsSteps(ConfigEntryBaseFlow):
    """Choose the loose lights and place each one.

    Uses only what the setup flow and the options flow both have, plus the
    hooks in flow.py (`async_output_chosen`).
    """

    # The stored output block, in the settings screen; empty at setup. Read
    # only: a mixin cannot rely on its __init__ being called, so the class
    # defaults are immutable and each flow assigns its own objects.
    _ha_lights_current: Mapping[str, Any] = MappingProxyType({})
    _ha_lights_choice: Mapping[str, Any] = MappingProxyType({})
    _ha_lights: Sequence[Light] = ()
    _ha_lights_index = 0
    # Assigned (a new dict per flow) when the lights are chosen.
    _ha_lights_positions: dict[str, tuple[float, float]]

    async def async_step_ha_lights(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """What the lights follow: single lights, an area or a light group."""
        errors: dict[str, str] = {}
        if user_input is not None:
            follow = user_input[FIELD_FOLLOW]
            available = {
                FOLLOW_LIGHTS: colour_lights,
                FOLLOW_AREA: areas_with_lights,
                FOLLOW_GROUP: groups_with_lights,
            }[follow](self.hass)
            if available:
                return await getattr(self, f"async_step_ha_lights_{follow}")()
            errors["base"] = f"ha_lights_no_{follow}"
        current = self._ha_lights_current.get(CONF_FOLLOW, FOLLOW_LIGHTS)
        return self.async_show_form(
            step_id="ha_lights",
            data_schema=vol.Schema(
                {
                    vol.Required(FIELD_FOLLOW, default=current): SelectSelector(
                        SelectSelectorConfig(
                            options=FOLLOWS,
                            translation_key="ha_lights_follow",
                            mode=SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_ha_lights_lights(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick single colour lights."""
        choices = colour_lights(self.hass)
        errors: dict[str, str] = {}
        if user_input is not None:
            ids = [
                light_id
                for entity_id in user_input.get(FIELD_LIGHTS, [])
                if entity_id in choices
                and (light_id := registry_id(self.hass, entity_id)) is not None
            ]
            if ids:
                return await self._async_ha_lights_chosen(
                    {
                        CONF_FOLLOW: FOLLOW_LIGHTS,
                        CONF_LIGHTS: [{CONF_ENTITY: light_id} for light_id in ids],
                    }
                )
            errors["base"] = "ha_lights_none_chosen"
        current = []
        if self._ha_lights_current.get(CONF_FOLLOW, FOLLOW_LIGHTS) == FOLLOW_LIGHTS:
            current = [
                light.entity_id
                for light in self._ha_lights_resolve(self._ha_lights_current)
                if light.entity_id in choices
            ]
        return self.async_show_form(
            step_id="ha_lights_lights",
            data_schema=vol.Schema(
                {
                    vol.Required(FIELD_LIGHTS, default=current): EntitySelector(
                        EntitySelectorConfig(
                            domain=LIGHT_DOMAIN,
                            multiple=True,
                            include_entities=sorted(choices),
                        )
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_ha_lights_area(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick a Home Assistant area with colour lights."""
        areas = areas_with_lights(self.hass)
        if user_input is not None and user_input.get(FIELD_AREA) in areas:
            return await self._async_ha_lights_chosen(
                {CONF_FOLLOW: FOLLOW_AREA, CONF_AREA_ID: user_input[FIELD_AREA]}
            )
        current = self._ha_lights_current.get(CONF_AREA_ID)
        return self.async_show_form(
            step_id="ha_lights_area",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        FIELD_AREA,
                        default=current if current in areas else vol.UNDEFINED,
                    ): dropdown(areas)
                }
            ),
        )

    async def async_step_ha_lights_group(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick a light group with colour lights (for example a Hue room)."""
        groups = groups_with_lights(self.hass)
        if user_input is not None and user_input.get(FIELD_GROUP) in groups:
            return await self._async_ha_lights_chosen(
                {CONF_FOLLOW: FOLLOW_GROUP, CONF_GROUP: user_input[FIELD_GROUP]}
            )
        current = self._ha_lights_current.get(CONF_GROUP)
        return self.async_show_form(
            step_id="ha_lights_group",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        FIELD_GROUP,
                        default=current if current in groups else vol.UNDEFINED,
                    ): dropdown(groups)
                }
            ),
        )

    async def async_step_ha_lights_hue_hint(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Some of the Hue lights could join the bridge's entertainment area."""
        if user_input is not None:
            return await self.async_step_ha_lights_position()
        return self.async_show_form(step_id="ha_lights_hue_hint")

    async def async_step_ha_lights_hue_no_area(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Some of the Hue lights could join an area, but the bridge has none."""
        if user_input is not None:
            return await self.async_step_ha_lights_position()
        return self.async_show_form(
            step_id="ha_lights_hue_no_area",
            description_placeholders={"link": ENTERTAINMENT_AREA_HELP},
        )

    async def async_step_ha_lights_position(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Where one light stands: a preset, or fine-tuned on the next step."""
        light = self._ha_lights[self._ha_lights_index]
        if user_input is not None:
            if user_input[FIELD_POSITION] == CUSTOM:
                return await self.async_step_ha_lights_fine_tune()
            self._ha_lights_positions[light.registry_id] = PRESETS[
                user_input[FIELD_POSITION]
            ]
            return await self._async_ha_lights_next()
        position = self._ha_lights_positions.get(light.registry_id)
        if position is None:
            default = "ambient"
        else:
            default = next(
                (name for name, value in PRESETS.items() if value == position), CUSTOM
            )
        return self.async_show_form(
            step_id="ha_lights_position",
            data_schema=vol.Schema(
                {
                    vol.Required(FIELD_POSITION, default=default): SelectSelector(
                        SelectSelectorConfig(
                            options=[*PRESETS, CUSTOM],
                            translation_key="ha_lights_position",
                            mode=SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
            description_placeholders=self._ha_lights_placeholders(light),
        )

    async def async_step_ha_lights_fine_tune(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Fine-tune one light's position: left-right and bottom-top."""
        light = self._ha_lights[self._ha_lights_index]
        if user_input is not None and FIELD_X in user_input:
            self._ha_lights_positions[light.registry_id] = (
                _clamp(user_input[FIELD_X] / 100),
                _clamp(user_input[FIELD_Z] / 100),
            )
            return await self._async_ha_lights_next()
        x, z = self._ha_lights_positions.get(light.registry_id, AMBIENT)
        return self.async_show_form(
            step_id="ha_lights_fine_tune",
            data_schema=vol.Schema(
                {
                    vol.Required(FIELD_X, default=round(x * 100)): _slider(),
                    vol.Required(FIELD_Z, default=round(z * 100)): _slider(),
                }
            ),
            description_placeholders=self._ha_lights_placeholders(light),
        )

    async def async_step_ha_lights_rate(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """More lights on one network than it takes commands per second."""
        if user_input is not None:
            return await self._async_ha_lights_done()
        count = crowded_networks(self.hass, self._ha_lights_entity_ids())
        return self.async_show_form(
            step_id="ha_lights_rate",
            description_placeholders={
                "count": str(count),
                "limit": str(NETWORK_COMMANDS_PER_SECOND),
                "seconds": f"{count / NETWORK_COMMANDS_PER_SECOND:g}",
            },
        )

    async def _async_ha_lights_chosen(self, choice: dict[str, Any]) -> ConfigFlowResult:
        """The lights are chosen: a hint for Hue lights, then the positions."""
        self._ha_lights_choice = choice
        self._ha_lights = self._ha_lights_resolve(choice)
        self._ha_lights_index = 0
        self._ha_lights_positions = {
            light[CONF_ENTITY]: (float(light[CONF_X]), float(light[CONF_Z]))
            for light in self._ha_lights_current.get(CONF_LIGHTS, [])
            if CONF_X in light
        }
        if not self._ha_lights:
            return await self._async_ha_lights_done()
        hint = await async_entertainment_hint(self.hass, self._ha_lights_entity_ids())
        if hint == HINT_AREA:
            return await self.async_step_ha_lights_hue_hint()
        if hint == HINT_NO_AREA:
            return await self.async_step_ha_lights_hue_no_area()
        return await self.async_step_ha_lights_position()

    async def _async_ha_lights_next(self) -> ConfigFlowResult:
        """The next light's position; then the warning, if any, and done."""
        self._ha_lights_index += 1
        if self._ha_lights_index < len(self._ha_lights):
            return await self.async_step_ha_lights_position()
        if crowded_networks(self.hass, self._ha_lights_entity_ids()):
            return await self.async_step_ha_lights_rate()
        return await self._async_ha_lights_done()

    async def _async_ha_lights_done(self) -> ConfigFlowResult:
        """Store the choice and a position for every light it has now.

        The positions of lights that left the area or group are dropped.
        """
        output = {
            CONF_TYPE: OUTPUT_HA_LIGHTS,
            **{k: v for k, v in self._ha_lights_choice.items() if k != CONF_LIGHTS},
            CONF_LIGHTS: [
                {
                    CONF_ENTITY: light.registry_id,
                    CONF_X: x,
                    CONF_Z: z,
                }
                for light in self._ha_lights
                for x, z in [self._ha_lights_positions.get(light.registry_id, AMBIENT)]
            ],
        }
        return await self.async_output_chosen({}, output)

    def _ha_lights_resolve(self, output: Mapping[str, Any]) -> list[Light]:
        try:
            return resolve(self.hass, output)
        except (OutputNotFound, OutputUnavailable):
            return []

    def _ha_lights_entity_ids(self) -> list[str]:
        return [light.entity_id for light in self._ha_lights]

    def _ha_lights_placeholders(self, light: Light) -> dict[str, str]:
        return {
            "light": light.name,
            "number": str(self._ha_lights_index + 1),
            "total": str(len(self._ha_lights)),
        }


class HaLightsConfigSteps(HaLightsSteps, ConfigFlow):
    """The setup steps."""


class HaLightsOptionsSteps(HaLightsSteps, OptionsFlow):
    """The settings screen's steps: with the stored lights and positions."""

    async def async_step_ha_lights_options(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Start from the stored choice; first a notice if the lights changed."""
        output = self.config_entry.options[CONF_OUTPUT]
        self._ha_lights_current = (
            dict(output) if output.get(CONF_TYPE) == OUTPUT_HA_LIGHTS else {}
        )
        if changes := self._ha_lights_changes():
            return await self.async_step_ha_lights_changes(changes=changes)
        return await self.async_step_ha_lights()

    async def async_step_ha_lights_changes(
        self,
        user_input: dict[str, Any] | None = None,
        changes: dict[str, str] | None = None,
    ) -> ConfigFlowResult:
        """The area's or group's lights changed since they were placed."""
        if user_input is not None:
            return await self.async_step_ha_lights()
        return self.async_show_form(
            step_id="ha_lights_changes", description_placeholders=changes or {}
        )

    def _ha_lights_changes(self) -> dict[str, str] | None:
        """{joined, left}: the lights to place and the lights that left."""
        current = self._ha_lights_current
        if current.get(CONF_FOLLOW) not in (FOLLOW_AREA, FOLLOW_GROUP):
            return None
        try:
            lights = resolve(self.hass, current)
        except (OutputNotFound, OutputUnavailable):
            return None
        placed = [light[CONF_ENTITY] for light in current.get(CONF_LIGHTS, [])]
        joined = [light.name for light in lights if light.registry_id not in placed]
        now = {light.registry_id for light in lights}
        registry = er.async_get(self.hass)
        left = [
            _registry_name(registry, light_id)
            for light_id in placed
            if light_id not in now
        ]
        if not joined and not left:
            return None
        return {
            "joined": ", ".join(sorted(joined)) or NONE_TEXT,
            "left": ", ".join(sorted(left)) or NONE_TEXT,
        }


def _registry_name(registry: er.EntityRegistry, light_id: str) -> str:
    entry = registry.entities.get_entry(light_id)
    if entry is None:
        return NONE_TEXT if not light_id else f"({light_id[:8]})"
    return entry.name or entry.original_name or entry.entity_id


def _slider() -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=-100,
            max=100,
            step=1,
            unit_of_measurement="%",
            mode=NumberSelectorMode.SLIDER,
        )
    )


def _clamp(value: float) -> float:
    return max(-1.0, min(1.0, float(value)))
