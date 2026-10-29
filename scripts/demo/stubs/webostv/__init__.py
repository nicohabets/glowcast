"""Demo stub for Home Assistant's webostv integration: a loaded entry and its device.

Only for the local demo in scripts/. Never install this on a real Home Assistant:
it replaces the real LG webOS Smart TV integration.
"""

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Report the TV's entry as loaded and register the TV's device."""
    assert entry.unique_id is not None
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("webostv", entry.unique_id)},
        name=entry.title,
        manufacturer="LG",
        model="OLED TV",
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the entry."""
    return True
