"""The switch that starts and stops syncing (architecture 4.3)."""

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import (
    ExtraStoredData,
    RestoredExtraData,
    RestoreEntity,
)

from .entity import GlowcastEntity
from .runtime import GlowcastConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GlowcastConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the switch for this entry."""
    async_add_entities([GlowcastSwitch(hass, entry)])


# In the switch's stored state: whether it synced when Home Assistant stopped.
RESUME = "resume"


class GlowcastSwitch(GlowcastEntity, SwitchEntity, RestoreEntity):
    """Syncing on or off. After a restart it starts by itself only when it
    synced when Home Assistant stopped, and the picture source is on (#183)."""

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

    @property
    def extra_restore_state_data(self) -> ExtraStoredData:
        """Whether to start again after the restart; stored when Home
        Assistant stops (and every 15 minutes, while it is not stopping)."""
        return RestoredExtraData({RESUME: self._data.syncing_at_stop})

    async def async_added_to_hass(self) -> None:
        """Start again if it synced when Home Assistant stopped."""
        await super().async_added_to_hass()
        last = await self.async_get_last_extra_data()
        if last is not None and last.as_dict().get(RESUME) is True:
            self._data.async_resume()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Start syncing."""
        await self._data.async_start()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Stop syncing and release the lights."""
        await self._data.async_stop()
