"""The button that shows the light test (architecture 4.3, #182)."""

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import GlowcastEntity
from .runtime import GlowcastConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GlowcastConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the test button for this entry."""
    async_add_entities([GlowcastTestLights(hass, entry)])


class GlowcastTestLights(GlowcastEntity, ButtonEntity):
    """Shows known colours by position on the lights, to check where each sits.

    Generic: the runtime runs the test through whatever output the entry has.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "test_lights"

    def __init__(self, hass: HomeAssistant, entry: GlowcastConfigEntry) -> None:
        """Initialize for one Glowcast entry."""
        super().__init__(hass, entry)
        self._attr_unique_id = f"{entry.entry_id}_test_lights"

    async def async_press(self) -> None:
        """Start the light test; it runs on by itself for about 6 s."""
        await self._data.async_test_lights()
