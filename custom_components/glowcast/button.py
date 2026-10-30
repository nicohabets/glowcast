"""The buttons: the light test (#182) and the hardware check (#115);
architecture 4.3."""

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
    """Add the buttons for this entry."""
    async_add_entities(
        [GlowcastTestLights(hass, entry), GlowcastHardwareCheck(hass, entry)]
    )


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


class GlowcastHardwareCheck(GlowcastEntity, ButtonEntity):
    """Runs the hardware check: what the TV and the lights can do, for a
    hardware report; the result is in the diagnostics download (#115).

    Generic: each source and output type brings its own probe.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "hardware_check"

    def __init__(self, hass: HomeAssistant, entry: GlowcastConfigEntry) -> None:
        """Initialize for one Glowcast entry."""
        super().__init__(hass, entry)
        self._attr_unique_id = f"{entry.entry_id}_hardware_check"

    async def async_press(self) -> None:
        """Start the check; it runs on by itself for at most about 30 s."""
        await self._data.async_hardware_check()
