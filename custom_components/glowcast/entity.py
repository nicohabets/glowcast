"""The base of Glowcast's entities: one device per entry, linked to the TV's."""

from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import CONF_SOURCE, CONF_TYPE, DOMAIN
from .runtime import GlowcastConfigEntry
from .sources import SOURCES


class GlowcastEntity(Entity):
    """On the device "Glowcast <TV name>"; updated when the runtime changes."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry: GlowcastConfigEntry) -> None:
        """Initialize for one Glowcast entry."""
        self._data = entry.runtime_data
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"Glowcast {entry.title}",
            entry_type=DeviceEntryType.SERVICE,
        )
        source = SOURCES[entry.data[CONF_SOURCE][CONF_TYPE]]
        if "via_device_id" in DeviceInfo.__annotations__:
            # Newer HA versions link by device id; via_device is deprecated there.
            if tv_device_id := source.device_id(hass, entry.data):
                self._attr_device_info["via_device_id"] = tv_device_id
        else:
            self._attr_device_info["via_device"] = source.device_identifier(entry.data)

    async def async_added_to_hass(self) -> None:
        """Follow the runtime's state, including stops it makes by itself."""
        self.async_on_remove(self._data.async_add_listener(self.async_write_ha_state))
