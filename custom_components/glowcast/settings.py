"""The user's settings in the entry's options (architecture section 5).

The only place that reads `Settings` from `options` or writes them back.
"""

import logging
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

_LOGGER = logging.getLogger(__name__)

# Unknown update rates already logged; the settings are read at every start.
_warned: set[str] = set()


def read_settings(options: Mapping[str, Any]) -> Settings:
    """Read the settings; a missing key (an older entry) gets its default."""
    return Settings(
        # Number selectors return floats.
        brightness=int(options.get(CONF_BRIGHTNESS, DEFAULT_BRIGHTNESS)),
        intensity=int(options.get(CONF_INTENSITY, DEFAULT_INTENSITY)),
        update_rate=_update_rate(options.get(CONF_UPDATE_RATE, DEFAULT_UPDATE_RATE)),
    )


def write_settings(settings: Settings) -> dict[str, Any]:
    """The settings as they are stored in `options`."""
    return {
        CONF_BRIGHTNESS: settings.brightness,
        CONF_INTENSITY: settings.intensity,
        CONF_UPDATE_RATE: settings.update_rate.value,
    }


def _update_rate(value: Any) -> UpdateRate:
    """The stored rate; an unknown one (renamed or removed later) gets the default."""
    try:
        return UpdateRate(value)
    except ValueError:
        if str(value) not in _warned:
            _warned.add(str(value))
            _LOGGER.warning(
                "Unknown update rate %r in Glowcast's settings; using %s",
                value,
                DEFAULT_UPDATE_RATE,
            )
        return UpdateRate(DEFAULT_UPDATE_RATE)
