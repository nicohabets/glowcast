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

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import (
    SIGNAL_CONFIG_ENTRY_CHANGED,
    ConfigEntry,
    ConfigEntryChange,
    ConfigEntryState,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.restore_state import (
    ExtraStoredData,
    RestoredExtraData,
    RestoreEntity,
)

from ..const import CONF_TYPE, CONF_UNIQUE_ID, DOMAIN
from ..core.ports import OutputAuthFailed, OutputUnavailable
from ..entity import GlowcastSettingEntity
from .blocks import output_block, with_output_block
from .ha_lights import OUTPUT_HA_LIGHTS, async_lights_in_area
from .hue_entertainment import (
    CONF_APP_KEY,
    CONF_AREA_ID,
    CONF_BRIDGE,
    HUE_DOMAIN,
    OUTPUT_HUE_ENTERTAINMENT,
    async_areas,
    bridge_host,
    signal_areas_read,
)
from .labels import unique_labels

_LOGGER = logging.getLogger(__name__)

# Seconds before reading the list again after a failure, doubled each time.
RETRY_FIRST = 30.0
RETRY_MAX = 600.0
# Its unique id is f"{entry_id}_{ENTITY_KEY}", and its translation key.
ENTITY_KEY = "entertainment_area"


def entities(hass: HomeAssistant, entry: ConfigEntry) -> list[Entity]:
    """The output type's own entities on the select platform."""
    return [HueAreaSelect(hass, entry)]


class HueAreaSelect(GlowcastSettingEntity, SelectEntity, RestoreEntity):
    """The entertainment area whose lights follow the picture."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize for one Glowcast entry."""
        super().__init__(hass, entry, ENTITY_KEY)
        # {area id: name}: read from the bridge, or restored from before a
        # restart, and {area id: label} made from it. Not a setting: the
        # chosen id stays in the options only.
        self._areas: dict[str, str] = {}
        self._labels: dict[str, str] = {}
        self._read = False  # read from the bridge since this entity was added
        self._reading: asyncio.Task[None] | None = None
        self._retry_delay = RETRY_FIRST
        self._cancel_retry: Callable[[], None] | None = None
        self._rejected_logged = False

    @property
    def options(self) -> list[str]:
        """The areas' names, sorted."""
        return list(self._labels.values())

    @property
    def current_option(self) -> str | None:
        """The stored area; None (unknown) if it is not in the bridge's list."""
        return self._labels.get(self._area_id)

    @property
    def extra_restore_state_data(self) -> ExtraStoredData:
        """The last list, to show the stored area after a restart."""
        return RestoredExtraData({"areas": self._areas})

    async def async_select_option(self, option: str) -> None:
        """Store the area's id; a running pipeline restarts on it."""
        area_id = next(i for i, label in self._labels.items() if label == option)
        block = output_block(self._entry.options, OUTPUT_HUE_ENTERTAINMENT) or {
            CONF_TYPE: OUTPUT_HUE_ENTERTAINMENT
        }
        lights = output_block(self._entry.options, OUTPUT_HA_LIGHTS)
        if lights is not None and (
            names := await async_lights_in_area(
                self.hass, self._entry.data, {**block, CONF_AREA_ID: area_id}, lights
            )
        ):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="lights_in_area",
                translation_placeholders={"lights": names},
            )
        self.hass.config_entries.async_update_entry(
            self._entry,
            options=with_output_block(
                self._entry.options, {**block, CONF_AREA_ID: area_id}
            ),
        )

    async def async_added_to_hass(self) -> None:
        """Restore the last list, then read the bridge's list."""
        await super().async_added_to_hass()
        if (last := await self.async_get_last_extra_data()) is not None:
            areas = last.as_dict().get("areas")
            if isinstance(areas, dict):
                self._use({str(k): str(v) for k, v in areas.items()})
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
        block = output_block(self._entry.options, OUTPUT_HUE_ENTERTAINMENT)
        return None if block is None else block.get(CONF_AREA_ID)

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
        """Read the list in the background, unless a read or a retry is pending.

        Not for the stop when Home Assistant stops or the entry unloads.
        """
        # Done already if it did not have to wait: tasks start eagerly.
        reading = self._reading is not None and not self._reading.done()
        if reading or self._cancel_retry is not None:
            return
        if (
            self.hass.is_stopping
            or self._entry.state is ConfigEntryState.UNLOAD_IN_PROGRESS
        ):
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
        """A list read from the bridge, here or by the options screen."""
        self._use(dict(areas))
        self._read = True
        self._rejected_logged = False
        self._async_cancel_retry()
        self._retry_delay = RETRY_FIRST
        self.async_write_ha_state()

    def _use(self, areas: dict[str, str]) -> None:
        self._areas = areas
        self._labels = unique_labels(areas)

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
