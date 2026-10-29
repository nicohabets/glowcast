"""Demo stub for Home Assistant's hue integration: the entry loads, nothing else.

Only for the local demo in scripts/. Never install this on a real Home Assistant:
it replaces the real Philips Hue integration.
"""

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Report the bridge's entry as loaded."""
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the entry."""
    return True
