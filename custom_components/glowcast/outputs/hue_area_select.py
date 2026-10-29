"""The entertainment area as an entity: what follows the TV (architecture 4.3).

Provided by the Hue entertainment-area output type. The options are the
bridge's areas by name; the choice is stored as the area's id in
`options["output"]["area_id"]`.

The list is read from the bridge in the background: at start, when the hue
entry is (re)loaded, when the options screen has read it, and with a backoff
after a failed read. The last list is restored after a restart, so the stored
area is shown by name while the bridge cannot be reached.
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import (
    SIGNAL_CONFIG_ENTRY_CHANGED,
    ConfigEntry,
    ConfigEntryChange,
    ConfigEntryState,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.restore_state import (
    ExtraStoredData,
    RestoredExtraData,
    RestoreEntity,
)

from ..const import CONF_APP_KEY, CONF_AREA_ID, CONF_BRIDGE, CONF_OUTPUT, CONF_UNIQUE_ID
from ..core.ports import OutputAuthFailed, OutputUnavailable
from ..entity import GlowcastEntity
from .hue_entertainment import HUE_DOMAIN, async_areas, bridge_host, signal_areas_read

_LOGGER = logging.getLogger(__name__)

# Seconds before reading the list again after a failure, doubled each time.
RETRY_FIRST = 30.0
RETRY_MAX = 600.0


def entities(hass: HomeAssistant, entry: ConfigEntry) -> list[Entity]:
    """The output type's own entities on the select platform."""
    return [HueAreaSelect(hass, entry)]


class HueAreaSelect(GlowcastEntity, SelectEntity, RestoreEntity):
    """The entertainment area whose lights follow the picture."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "entertainment_area"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize for one Glowcast entry."""
        super().__init__(hass, entry)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_entertainment_area"
        # {area id: name}: read from the bridge, or restored from before a
        # restart. Not a setting: the chosen id stays in the options only.
        self._areas: dict[str, str] = {}
        self._read = False  # read from the bridge since this entity was added
        self._reading: asyncio.Task[None] | None = None
        self._retry_delay = RETRY_FIRST
        self._cancel_retry: Callable[[], None] | None = None
        self._rejected_logged = False

    @property
    def options(self) -> list[str]:
        """The areas' names, sorted."""
        return list(_labels(self._areas).values())

    @property
    def current_option(self) -> str | None:
        """The stored area; None (unknown) if it is not in the bridge's list."""
        return _labels(self._areas).get(self._area_id)

    @property
    def extra_restore_state_data(self) -> ExtraStoredData:
        """The last list, to show the stored area after a restart."""
        return RestoredExtraData({"areas": self._areas})

    async def async_select_option(self, option: str) -> None:
        """Store the area's id; a running pipeline restarts on it."""
        area_id = next(
            i for i, label in _labels(self._areas).items() if label == option
        )
        options = self._entry.options
        self.hass.config_entries.async_update_entry(
            self._entry,
            options={
                **options,
                CONF_OUTPUT: {**options[CONF_OUTPUT], CONF_AREA_ID: area_id},
            },
        )

    async def async_added_to_hass(self) -> None:
        """Restore the last list, then read the bridge's list."""
        await super().async_added_to_hass()
        if (last := await self.async_get_last_extra_data()) is not None:
            areas = last.as_dict().get("areas")
            if isinstance(areas, dict):
                self._areas = {str(k): str(v) for k, v in areas.items()}
        self.async_on_remove(self._data.async_add_listener(self._async_changed))
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_CONFIG_ENTRY_CHANGED, self._async_entry_changed
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_areas_read(self._entry.entry_id),
                self._async_set_areas,
            )
        )
        self.async_on_remove(self._async_cancel)
        self._async_read()

    @property
    def _area_id(self) -> str | None:
        return self._entry.options[CONF_OUTPUT].get(CONF_AREA_ID)

    @callback
    def _async_changed(self) -> None:
        """The options or the switch changed: read the list if it may be stale.

        Also after every stop, for example because the area was removed in
        the Hue app; not while syncing, unless the stored area is unknown.
        """
        if not self._data.is_on or not self._read or self._area_id not in self._areas:
            self._async_read()

    @callback
    def _async_entry_changed(
        self, change: ConfigEntryChange, entry: ConfigEntry
    ) -> None:
        """The bridge's hue entry was loaded (again): it answers now."""
        if (
            change is ConfigEntryChange.UPDATED
            and entry.domain == HUE_DOMAIN
            and entry.unique_id == self._entry.data[CONF_BRIDGE][CONF_UNIQUE_ID]
            and entry.state is ConfigEntryState.LOADED
        ):
            self._async_cancel_retry()
            self._retry_delay = RETRY_FIRST
            self._async_read()

    @callback
    def _async_read(self) -> None:
        """Read the list in the background, unless a read or a retry is pending."""
        # Done already if it did not have to wait: tasks start eagerly.
        reading = self._reading is not None and not self._reading.done()
        if reading or self._cancel_retry is not None:
            return
        self._reading = self._entry.async_create_background_task(
            self.hass, self._async_read_areas(), "glowcast hue areas"
        )

    async def _async_read_areas(self) -> None:
        bridge = self._entry.data[CONF_BRIDGE]
        host = bridge_host(self.hass, bridge[CONF_UNIQUE_ID])
        if host is None:
            return  # the hue entry is not loaded; read again once it is
        try:
            areas = await async_areas(self.hass, host, bridge[CONF_APP_KEY])
        except OutputAuthFailed:
            # Reauth starts at switch-on and in the options screen, not here.
            if not self._rejected_logged:
                self._rejected_logged = True
                _LOGGER.warning(
                    "Glowcast %s cannot read the entertainment areas: "
                    "the Hue bridge rejected Glowcast's key",
                    self._entry.title,
                )
            return
        except OutputUnavailable:
            self._async_retry_later()
            return
        self._async_set_areas(areas)

    @callback
    def _async_set_areas(self, areas: dict[str, str]) -> None:
        self._areas = dict(areas)
        self._read = True
        self._rejected_logged = False
        self._retry_delay = RETRY_FIRST
        self.async_write_ha_state()

    @callback
    def _async_retry_later(self) -> None:
        self._cancel_retry = async_call_later(
            self.hass, self._retry_delay, self._async_retry
        )
        self._retry_delay = min(self._retry_delay * 2, RETRY_MAX)

    @callback
    def _async_retry(self, _now: datetime) -> None:
        self._cancel_retry = None
        self._async_read()

    @callback
    def _async_cancel_retry(self) -> None:
        if self._cancel_retry is not None:
            self._cancel_retry()
            self._cancel_retry = None

    @callback
    def _async_cancel(self) -> None:
        self._async_cancel_retry()
        if self._reading is not None and not self._reading.done():
            self._reading.cancel()


def _labels(areas: dict[str, Any]) -> dict[str, str]:
    """{area id: label}, sorted by name; a name used twice gets a number.

    A select option is a name, and a bridge can have two areas with the same
    name. Ordered by name and then id, so the labels stay the same.
    """
    labels: dict[str, str] = {}
    used: set[str] = set()
    for area_id, name in sorted(areas.items(), key=lambda item: (item[1], item[0])):
        label, number = name, 1
        while label in used:
            number += 1
            label = f"{name} ({number})"
        used.add(label)
        labels[area_id] = label
    return labels
