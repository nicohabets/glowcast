"""The switch that starts and stops syncing (architecture 4.3)."""

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import GlowcastEntity
from .runtime import GlowcastConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GlowcastConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the switch for this entry."""
    async_add_entities([GlowcastSwitch(hass, entry)])


class GlowcastSwitch(GlowcastEntity, SwitchEntity):
    """Syncing on or off. Off after a restart: Glowcast never starts by itself."""

    # The device's main feature: the entity is named after the device, so its
    # id comes out as switch.glowcast_<tv name>.
    _attr_name = None
    _attr_translation_key = "sync"

    def __init__(self, hass: HomeAssistant, entry: GlowcastConfigEntry) -> None:
        """Initialize for one Glowcast entry."""
        super().__init__(hass, entry)
        self._attr_unique_id = entry.entry_id

    @property
    def is_on(self) -> bool:
        """Whether Glowcast is syncing."""
        return self._data.is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Start syncing."""
        await self._data.async_start()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Stop syncing and release the lights."""
        await self._data.async_stop()
