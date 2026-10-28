"""The switch that starts and stops syncing (architecture 4.3)."""

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import GlowcastConfigEntry
from .const import CONF_SOURCE, CONF_TYPE, DOMAIN
from .sources import SOURCES


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GlowcastConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the switch for this entry."""
    async_add_entities([GlowcastSwitch(hass, entry)])


class GlowcastSwitch(SwitchEntity):
    """Syncing on or off. Off after a restart: Glowcast never starts by itself."""

    _attr_has_entity_name = True
    # The device's main feature: the entity is named after the device, so its
    # id comes out as switch.glowcast_<tv name>.
    _attr_name = None
    _attr_translation_key = "sync"
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry: GlowcastConfigEntry) -> None:
        """Initialize for one Glowcast entry."""
        self._data = entry.runtime_data
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"Glowcast {entry.title}",
            entry_type=DeviceEntryType.SERVICE,
        )
        tv = SOURCES[entry.data[CONF_SOURCE][CONF_TYPE]].device_identifier(entry.data)
        if "via_device_id" in DeviceInfo.__annotations__:
            # Newer HA versions link by device id; via_device is deprecated there.
            if device := dr.async_get(hass).async_get_device(identifiers={tv}):
                self._attr_device_info["via_device_id"] = device.id
        else:
            self._attr_device_info["via_device"] = tv

    @property
    def is_on(self) -> bool:
        """Whether Glowcast is syncing."""
        return self._data.is_on

    async def async_added_to_hass(self) -> None:
        """Follow the pipeline's state, including stops it makes by itself."""
        self.async_on_remove(self._data.async_add_listener(self.async_write_ha_state))

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Start syncing."""
        await self._data.async_start()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Stop syncing and release the lights."""
        await self._data.async_stop()
