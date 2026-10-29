"""The update rate as a configuration entity (architecture 4.3).

What follows the TV is a select too, for some output types; the output type
provides it.
"""

from dataclasses import replace

from homeassistant.components.select import SelectEntity
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_UPDATE_RATE, UPDATE_RATES
from .core.pipeline import UpdateRate
from .entity import GlowcastSettingEntity
from .outputs import output_entities
from .runtime import GlowcastConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GlowcastConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the update rate, and the output type's own selects."""
    async_add_entities(
        [
            GlowcastUpdateRate(hass, entry),
            *output_entities(hass, entry, Platform.SELECT),
        ]
    )


class GlowcastUpdateRate(GlowcastSettingEntity, SelectEntity):
    """Low, Medium, High or Maximum (architecture 3.6)."""

    _attr_options = UPDATE_RATES

    def __init__(self, hass: HomeAssistant, entry: GlowcastConfigEntry) -> None:
        """Initialize for one Glowcast entry."""
        super().__init__(hass, entry, CONF_UPDATE_RATE)

    @property
    def current_option(self) -> str:
        """The stored rate."""
        return self._settings.update_rate.value

    async def async_select_option(self, option: str) -> None:
        """Store a new rate; the capture loop uses it from its next capture."""
        self._store(replace(self._settings, update_rate=UpdateRate(option)))
