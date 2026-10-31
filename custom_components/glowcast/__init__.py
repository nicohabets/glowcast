"""Glowcast: lights follow the picture on a TV."""

from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .outputs import OUTPUTS
from .outputs.blocks import output_types
from .runtime import GlowcastConfigEntry, GlowcastData, async_remove_issues

PLATFORMS = [
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup_entry(hass: HomeAssistant, entry: GlowcastConfigEntry) -> bool:
    """Set up Glowcast from a config entry; syncing is off until switched on."""
    _remove_other_output_entities(hass, entry)
    entry.runtime_data = data = GlowcastData(hass, entry)

    async def _async_ha_stop(event: Event) -> None:
        # HA does not unload entries on shutdown. It cancels background tasks
        # first, which makes the pipeline release the lights; this waits for
        # that release as a backstop. A start, light test or hardware check
        # that comes after it does nothing (#282).
        await data.async_close()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_ha_stop)
    )
    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: GlowcastConfigEntry) -> bool:
    """Stop syncing, release the lights and unload; nothing starts after it."""
    await entry.runtime_data.async_close()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: GlowcastConfigEntry) -> None:
    """Remove the entry's repair issues; the output says what the user cleans up."""
    async_remove_issues(hass, entry.entry_id)
    # For example an app that stays on the device the lights are paired with.
    # Every type: the entry may have paired with one before it changed type.
    for output_type in OUTPUTS.values():
        if output_type.async_removed is not None:
            await output_type.async_removed(hass, entry)


def _remove_other_output_entities(
    hass: HomeAssistant, entry: GlowcastConfigEntry
) -> None:
    """Remove the entities of output types the entry no longer uses.

    After a change of output type (architecture 4.2) the previous type's
    entities would otherwise stay as orphans in the entity registry.
    """
    current = output_types(entry.options)  # the parts of a combined output
    registry = er.async_get(hass)
    for type_id, output_type in OUTPUTS.items():
        if type_id in current:
            continue
        for key in output_type.entity_keys:
            for platform in output_type.entities:
                entity_id = registry.async_get_entity_id(
                    platform, DOMAIN, f"{entry.entry_id}_{key}"
                )
                if entity_id is not None:
                    registry.async_remove(entity_id)


async def _async_entry_updated(hass: HomeAssistant, entry: GlowcastConfigEntry) -> None:
    """New settings reach a running pipeline without an entry reload."""
    await entry.runtime_data.async_options_updated()
