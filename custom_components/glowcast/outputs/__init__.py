"""Light outputs: adapters behind the LightOutput port."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity

from ..const import CONF_OUTPUT, CONF_TYPE, OUTPUT_HUE_ENTERTAINMENT
from ..core.ports import LightOutput
from . import hue_area_select, hue_entertainment


@dataclass(frozen=True)
class OutputType:
    """How Glowcast builds and checks one type of output (architecture 5)."""

    # Build the output from the entry's data and options.
    create: Callable[[HomeAssistant, Mapping[str, Any], Mapping[str, Any]], LightOutput]
    # Whether the integration entry the output uses is removed or not loaded.
    is_missing: Callable[[HomeAssistant, Mapping[str, Any]], bool]
    # The device registry id of the device the output drives through (for
    # example the bridge), for diagnostics.
    device_id: Callable[[HomeAssistant, Mapping[str, Any]], str | None]
    # The type's own entities on Glowcast's device, per platform: what follows
    # the TV (architecture 4.3). Only the number and select platforms ask for
    # them; a type without them gets the common settings only.
    entities: Mapping[
        Platform, Callable[[HomeAssistant, ConfigEntry], list[Entity]]
    ] = field(default_factory=dict)


OUTPUTS = {
    OUTPUT_HUE_ENTERTAINMENT: OutputType(
        hue_entertainment.create,
        hue_entertainment.is_missing,
        hue_entertainment.device_id,
        {Platform.SELECT: hue_area_select.entities},
    ),
}


def output_entities(
    hass: HomeAssistant, entry: ConfigEntry, platform: Platform
) -> list[Entity]:
    """The entities the entry's output type provides on this platform."""
    output_type = OUTPUTS[entry.options[CONF_OUTPUT][CONF_TYPE]]
    create = output_type.entities.get(platform)
    return [] if create is None else create(hass, entry)
