"""Light outputs: adapters behind the LightOutput port."""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity

from ..const import CONF_OUTPUT, CONF_TYPE
from ..core.pipeline import StopReason
from ..core.ports import LightOutput
from ..issues import Issue
from . import (
    ha_lights,
    ha_lights_flow,
    ha_lights_select,
    hue_area_select,
    hue_entertainment,
    hue_entertainment_flow,
)


@dataclass(frozen=True)
class OutputType:
    """How Glowcast builds and checks one type of output (architecture 5)."""

    # Build the output from the entry's data and options.
    create: Callable[[HomeAssistant, Mapping[str, Any], Mapping[str, Any]], LightOutput]
    # Whether the integration entry the output uses is removed or not loaded,
    # from the entry's data and options.
    is_missing: Callable[[HomeAssistant, Mapping[str, Any], Mapping[str, Any]], bool]
    # The device registry id of the device the output drives through (for
    # example the bridge), for diagnostics.
    device_id: Callable[[HomeAssistant, Mapping[str, Any]], str | None]
    # The type's own entities on Glowcast's device, per platform: what follows
    # the TV (architecture 4.3). Only the number and select platforms ask for
    # them; a type without them gets the common settings only.
    entities: Mapping[
        Platform, Callable[[HomeAssistant, ConfigEntry], list[Entity]]
    ] = field(default_factory=dict)
    # The type's steps in the setup flow (flow.py): a mixin of async_step_
    # methods, the step that starts them at setup, and the one that starts
    # pairing again when the output rejects Glowcast's key.
    steps: type[ConfigFlow] | None = None
    setup_step: str | None = None
    reauth_step: str | None = None
    # The type's steps in the settings screen: a mixin of async_step_ methods
    # for the options flow, and the step that starts them. They end, as at
    # setup, with `async_output_chosen` (flow.py).
    options_steps: type[OptionsFlow] | None = None
    options_step: str | None = None
    # The keys of the type's own entities: their unique ids are
    # f"{entry_id}_{key}". When an entry changes to another type, the entities
    # of the previous one are removed with these keys (architecture 4.2).
    entity_keys: tuple[str, ...] = ()
    # The repair issue when the integration entry it uses is missing, and the
    # ones for stop reasons the user must fix in the settings.
    missing: Issue | None = None
    stop_issues: Mapping[StopReason, Issue] = field(default_factory=dict)
    # Run when the Glowcast entry is removed: what the user must clean up.
    async_removed: Callable[[HomeAssistant, ConfigEntry], Awaitable[None]] | None = None


OUTPUTS = {
    hue_entertainment.OUTPUT_HUE_ENTERTAINMENT: OutputType(
        hue_entertainment.create,
        hue_entertainment.is_missing,
        hue_entertainment.device_id,
        {Platform.SELECT: hue_area_select.entities},
        steps=hue_entertainment_flow.HueEntertainmentConfigSteps,
        setup_step="bridge",
        reauth_step="reauth_confirm",
        options_steps=hue_entertainment_flow.HueEntertainmentOptionsSteps,
        options_step="hue_options",
        entity_keys=(hue_area_select.ENTITY_KEY,),
        missing=hue_entertainment.MISSING,
        stop_issues={
            StopReason.OUTPUT_TOO_MANY_CHANNELS: hue_entertainment.TOO_MANY_CHANNELS,
            StopReason.OUTPUT_NOT_FOUND: hue_entertainment.AREA_NOT_FOUND,
        },
        async_removed=hue_entertainment.async_removed,
    ),
    # Loose lights: any colour light through Home Assistant (#9).
    ha_lights.OUTPUT_HA_LIGHTS: OutputType(
        ha_lights.create,
        ha_lights.is_missing,
        ha_lights.device_id,
        {Platform.SELECT: ha_lights_select.entities},
        steps=ha_lights_flow.HaLightsConfigSteps,
        setup_step="ha_lights",
        options_steps=ha_lights_flow.HaLightsOptionsSteps,
        options_step="ha_lights_options",
        entity_keys=(ha_lights_select.ENTITY_KEY,),
        stop_issues={StopReason.OUTPUT_NOT_FOUND: ha_lights.NO_LIGHTS},
    ),
}

# Every output type's setup steps, for the one registered flow, and its
# steps in the settings screen, for the options flow.
OUTPUT_STEPS = tuple(
    output_type.steps for output_type in OUTPUTS.values() if output_type.steps
)
OUTPUT_OPTIONS_STEPS = tuple(
    output_type.options_steps
    for output_type in OUTPUTS.values()
    if output_type.options_steps
)


def output_entities(
    hass: HomeAssistant, entry: ConfigEntry, platform: Platform
) -> list[Entity]:
    """The entities the entry's output type provides on this platform.

    None for a type this version does not know, so the common settings still
    appear.
    """
    output_type = OUTPUTS.get(entry.options[CONF_OUTPUT][CONF_TYPE])
    create = None if output_type is None else output_type.entities.get(platform)
    return [] if create is None else create(hass, entry)
