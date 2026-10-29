"""The user's settings in the entry's options (architecture section 5).

The only place that reads `Settings` from `options` or writes them back.
"""

from collections.abc import Mapping
from typing import Any

from .const import (
    CONF_BRIGHTNESS,
    CONF_INTENSITY,
    CONF_UPDATE_RATE,
    DEFAULT_BRIGHTNESS,
    DEFAULT_INTENSITY,
    DEFAULT_UPDATE_RATE,
)
from .core.pipeline import Settings, UpdateRate


def read_settings(options: Mapping[str, Any]) -> Settings:
    """Read the settings; a missing key (an older entry) gets its default."""
    return Settings(
        # Number selectors return floats.
        brightness=int(options.get(CONF_BRIGHTNESS, DEFAULT_BRIGHTNESS)),
        intensity=int(options.get(CONF_INTENSITY, DEFAULT_INTENSITY)),
        update_rate=UpdateRate(options.get(CONF_UPDATE_RATE, DEFAULT_UPDATE_RATE)),
    )


def write_settings(settings: Settings) -> dict[str, Any]:
    """The settings as they are stored in `options`."""
    return {
        CONF_BRIGHTNESS: settings.brightness,
        CONF_INTENSITY: settings.intensity,
        CONF_UPDATE_RATE: settings.update_rate.value,
    }
