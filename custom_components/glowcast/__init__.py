"""Glowcast: Hue lights follow the picture on an LG webOS TV."""

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant


@dataclass
class GlowcastData:
    """What a loaded Glowcast entry holds while Home Assistant runs."""


type GlowcastConfigEntry = ConfigEntry[GlowcastData]


async def async_setup_entry(hass: HomeAssistant, entry: GlowcastConfigEntry) -> bool:
    """Set up Glowcast from a config entry."""
    entry.runtime_data = GlowcastData()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: GlowcastConfigEntry) -> bool:
    """Unload a config entry."""
    return True
