"""Glowcast: lights follow the picture on a TV."""

from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant

from .const import CONF_OUTPUT, CONF_TYPE
from .outputs import OUTPUTS
from .runtime import GlowcastConfigEntry, GlowcastData, async_remove_issues

PLATFORMS = [Platform.NUMBER, Platform.SELECT, Platform.SWITCH]


async def async_setup_entry(hass: HomeAssistant, entry: GlowcastConfigEntry) -> bool:
    """Set up Glowcast from a config entry; syncing is off until switched on."""
    entry.runtime_data = data = GlowcastData(hass, entry)

    async def _async_ha_stop(event: Event) -> None:
        # HA does not unload entries on shutdown. It cancels background tasks
        # first, which makes the pipeline release the lights; this waits for
        # that release as a backstop.
        await data.async_stop()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_ha_stop)
    )
    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: GlowcastConfigEntry) -> bool:
    """Stop syncing, release the lights and unload."""
    await entry.runtime_data.async_stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: GlowcastConfigEntry) -> None:
    """Remove the entry's repair issues; the output says what the user cleans up."""
    async_remove_issues(hass, entry.entry_id)
    # For example an app that stays on the device the lights are paired with.
    output_type = OUTPUTS.get(entry.options[CONF_OUTPUT][CONF_TYPE])
    if output_type is not None and output_type.async_removed is not None:
        await output_type.async_removed(hass, entry)


async def _async_entry_updated(hass: HomeAssistant, entry: GlowcastConfigEntry) -> None:
    """New settings reach a running pipeline without an entry reload."""
    await entry.runtime_data.async_options_updated()
