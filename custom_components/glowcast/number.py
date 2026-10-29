"""Brightness and intensity as configuration entities (architecture 4.3)."""

from dataclasses import replace

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_BRIGHTNESS, CONF_INTENSITY
from .entity import GlowcastSettingEntity
from .outputs import output_entities
from .runtime import GlowcastConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GlowcastConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add brightness and intensity, and the output type's own numbers."""
    async_add_entities(
        [
            GlowcastPercentage(hass, entry, CONF_BRIGHTNESS, 1, 100),
            GlowcastPercentage(hass, entry, CONF_INTENSITY, 0, 200),
            *output_entities(hass, entry, Platform.NUMBER),
        ]
    )


class GlowcastPercentage(GlowcastSettingEntity, NumberEntity):
    """A setting in whole percents; the key is also its field in Settings."""

    _attr_mode = NumberMode.SLIDER
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(
        self,
        hass: HomeAssistant,
        entry: GlowcastConfigEntry,
        key: str,
        minimum: int,
        maximum: int,
    ) -> None:
        """Initialize for one setting and its range."""
        super().__init__(hass, entry, key)
        self._key = key
        self._attr_native_min_value = minimum
        self._attr_native_max_value = maximum

    @property
    def native_value(self) -> int:
        """The stored value."""
        return getattr(self._settings, self._key)

    async def async_set_native_value(self, value: float) -> None:
        """Store a new value; a running pipeline uses it without a restart."""
        self._store(replace(self._settings, **{self._key: int(value)}))
