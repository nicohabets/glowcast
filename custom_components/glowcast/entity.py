"""The base of Glowcast's entities: one device per entry, linked to the TV's."""

from typing import TYPE_CHECKING

from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import CONF_SOURCE, CONF_TYPE, DOMAIN
from .core.pipeline import Settings
from .settings import read_settings, write_settings
from .sources import SOURCES

if TYPE_CHECKING:
    # Only for type checking: an output type's own entities (in outputs/)
    # derive from GlowcastEntity, and the runtime imports the outputs.
    from .runtime import GlowcastConfigEntry


class GlowcastEntity(Entity):
    """On the device "Glowcast <TV name>"; updated when the runtime changes."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry: "GlowcastConfigEntry") -> None:
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
        elif tv_identifier := source.device_identifier(entry.data):
            self._attr_device_info["via_device"] = tv_identifier

    async def async_added_to_hass(self) -> None:
        """Follow the runtime's state, including stops it makes by itself.

        An entity that overrides this must call super(), or it stops following.
        """
        await super().async_added_to_hass()
        self.async_on_remove(self._data.async_add_listener(self._async_runtime_changed))

    @callback
    def _async_runtime_changed(self) -> None:
        """The runtime changed: write the state."""
        self.async_write_ha_state()


class GlowcastSettingEntity(GlowcastEntity):
    """One of the settings, under Configuration on the device (architecture 4.3).

    The entry's options are the only copy of the value: it is read from them
    on every state write and stored back in them, so the options screen and
    the entities always agree.
    """

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self, hass: HomeAssistant, entry: "GlowcastConfigEntry", key: str
    ) -> None:
        """Initialize for the setting stored under `key` in the options."""
        super().__init__(hass, entry)
        self._entry = entry
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"

    @property
    def _settings(self) -> Settings:
        return read_settings(self._entry.options)

    def _store(self, settings: Settings) -> None:
        """Store the settings; the update listener passes them on."""
        self.hass.config_entries.async_update_entry(
            self._entry, options={**self._entry.options, **write_settings(settings)}
        )
