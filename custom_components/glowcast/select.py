"""The update rate and the liveliness as configuration entities (architecture 4.3).

What follows the TV is a select too, for some output types; the output type
provides it.
"""

from dataclasses import replace

from homeassistant.components.select import SelectEntity
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_LIVELINESS, CONF_UPDATE_RATE, LIVELINESS_LEVELS, UPDATE_RATES
from .core.liveliness import Liveliness
from .core.pipeline import UpdateRate
from .entity import GlowcastSettingEntity
from .outputs import output_entities
from .runtime import GlowcastConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GlowcastConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the update rate and the liveliness, and the output type's own selects."""
    async_add_entities(
        [
            GlowcastUpdateRate(hass, entry),
            GlowcastLiveliness(hass, entry),
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


class GlowcastLiveliness(GlowcastSettingEntity, SelectEntity):
    """Calm, Normal or Lively (#16)."""

    _attr_options = LIVELINESS_LEVELS

    def __init__(self, hass: HomeAssistant, entry: GlowcastConfigEntry) -> None:
        """Initialize for one Glowcast entry."""
        super().__init__(hass, entry, CONF_LIVELINESS)

    @property
    def current_option(self) -> str:
        """The stored level."""
        return self._settings.liveliness.value

    async def async_select_option(self, option: str) -> None:
        """Store a new level; the pipeline uses it from its next capture."""
        self._store(replace(self._settings, liveliness=Liveliness(option)))
