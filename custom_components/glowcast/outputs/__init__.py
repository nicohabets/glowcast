"""Light outputs: adapters behind the LightOutput port."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant

from ..const import OUTPUT_HUE_ENTERTAINMENT
from ..core.ports import LightOutput
from . import hue_entertainment


@dataclass(frozen=True)
class OutputType:
    """How Glowcast builds and checks one type of output (architecture 5)."""

    # Build the output from the entry's data and options.
    create: Callable[[HomeAssistant, Mapping[str, Any], Mapping[str, Any]], LightOutput]
    # Whether the integration entry the output uses is removed or not loaded.
    is_missing: Callable[[HomeAssistant, Mapping[str, Any]], bool]


OUTPUTS = {
    OUTPUT_HUE_ENTERTAINMENT: OutputType(
        hue_entertainment.create, hue_entertainment.is_missing
    ),
}
