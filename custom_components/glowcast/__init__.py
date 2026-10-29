"""Glowcast: Hue lights follow the picture on an LG webOS TV."""

from homeassistant.components import persistent_notification
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.translation import async_get_translations

from .const import DOMAIN
from .runtime import ISSUES, GlowcastConfigEntry, GlowcastData, issue_id

PLATFORMS = [Platform.NUMBER, Platform.SELECT, Platform.SWITCH]

# Shown once when the entry is removed. Its text lives under "issues" because
# Home Assistant has no translation category for notifications; it is never
# raised as a repair issue.
NOTICE_BRIDGE_APP_LEFT = "bridge_app_left"


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
    """Remove the entry's repair issues; tell the user its app stays on the bridge."""
    for key in ISSUES:
        ir.async_delete_issue(hass, DOMAIN, issue_id(entry.entry_id, key))
    # The bridge has no API to remove an app (architecture 4.1); the user
    # removes it in the Hue app. Text in Home Assistant's server language.
    strings = await async_get_translations(
        hass, hass.config.language, "issues", {DOMAIN}
    )
    prefix = f"component.{DOMAIN}.issues.{NOTICE_BRIDGE_APP_LEFT}"
    persistent_notification.async_create(
        hass,
        strings[f"{prefix}.description"],
        title=strings[f"{prefix}.title"].replace("{name}", entry.title),
        notification_id=f"{DOMAIN}_{NOTICE_BRIDGE_APP_LEFT}_{entry.entry_id}",
    )


async def _async_entry_updated(hass: HomeAssistant, entry: GlowcastConfigEntry) -> None:
    """New settings reach a running pipeline without an entry reload."""
    await entry.runtime_data.async_options_updated()
