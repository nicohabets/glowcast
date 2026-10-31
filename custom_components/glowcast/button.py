"""The buttons: the light test (#182) and the hardware check (#115);
architecture 4.3."""

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_SOURCE, CONF_TYPE, DOMAIN
from .entity import GlowcastEntity
from .outputs import OUTPUTS
from .outputs.blocks import output_types
from .runtime import GlowcastConfigEntry
from .sources import SOURCES

HARDWARE_CHECK_KEY = "hardware_check"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GlowcastConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the buttons for this entry.

    The hardware check only when it can report something: when the source
    type or an output type the entry uses has a probe (#270). Otherwise a
    button left from before a change of type is removed, so no orphan stays.
    """
    buttons: list[ButtonEntity] = [GlowcastTestLights(hass, entry)]
    if has_probe(entry):
        buttons.append(GlowcastHardwareCheck(hass, entry))
    else:
        registry = er.async_get(hass)
        entity_id = registry.async_get_entity_id(
            Platform.BUTTON, DOMAIN, f"{entry.entry_id}_{HARDWARE_CHECK_KEY}"
        )
        if entity_id is not None:
            registry.async_remove(entity_id)
    async_add_entities(buttons)


def has_probe(entry: GlowcastConfigEntry) -> bool:
    """Whether the entry's source type or one of its output types (each part
    of a combined output) has a probe for the hardware check."""
    kinds = [SOURCES.get(entry.data[CONF_SOURCE][CONF_TYPE])]
    kinds += [OUTPUTS.get(type_id) for type_id in output_types(entry.options)]
    return any(kind is not None and kind.probe is not None for kind in kinds)


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
    """Runs the hardware check: what the picture source and the lights can
    do, for a hardware report; the result is in the diagnostics download (#115).

    Generic: each source and output type brings its own probe; the button is
    only there when at least one of them has one.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "hardware_check"

    def __init__(self, hass: HomeAssistant, entry: GlowcastConfigEntry) -> None:
        """Initialize for one Glowcast entry."""
        super().__init__(hass, entry)
        self._attr_unique_id = f"{entry.entry_id}_{HARDWARE_CHECK_KEY}"

    async def async_press(self) -> None:
        """Start the check; it runs on by itself for at most about 30 s."""
        await self._data.async_hardware_check()
