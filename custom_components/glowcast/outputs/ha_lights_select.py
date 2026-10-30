"""What the loose lights follow, as an entity (architecture 4.3, #9).

Provided by the loose-lights output type. The options are the Home Assistant
areas and the light groups that have colour lights, by name; while single
lights are used, it shows "chosen lights" (the lights themselves are picked in
the settings screen). Selecting an area or a group stores it in
`options["output"]`, which restarts a running pipeline on its lights; lights
that have no position yet follow "ambient".

The list is made again when areas or light entities change, when a light
appears, when Home Assistant has started (the groups' members are in their
states) and on every start, stop or options change of the entry. Registry and
state events come in bursts, so they make it again at most once per
REFRESH_COOLDOWN (the first at once): each time scans the registries.
"""

import logging
from collections.abc import Mapping
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.event import async_track_state_added_domain

from ..const import CONF_OUTPUT
from ..entity import GlowcastSettingEntity
from .ha_lights import (
    CONF_AREA_ID,
    CONF_FOLLOW,
    CONF_GROUP,
    FOLLOW_AREA,
    FOLLOW_GROUP,
    FOLLOW_LIGHTS,
    LIGHT_DOMAIN,
    areas_with_lights,
    groups_with_lights,
)

# Its unique id is f"{entry_id}_{ENTITY_KEY}", and its translation key.
ENTITY_KEY = "loose_lights"
# The option while single lights are used; translated as a state.
CHOSEN_LIGHTS = "chosen_lights"
# Seconds between two refreshes of the list after registry or state events.
REFRESH_COOLDOWN = 1.0

_LOGGER = logging.getLogger(__name__)


def entities(hass: HomeAssistant, entry: ConfigEntry) -> list[Entity]:
    """The output type's own entities on the select platform."""
    return [HaLightsSelect(hass, entry)]


class HaLightsSelect(GlowcastSettingEntity, SelectEntity):
    """The area or light group whose colour lights follow the picture."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize for one Glowcast entry."""
        super().__init__(hass, entry, ENTITY_KEY)
        # {label: (follow, area id or group registry id)}
        self._choices: dict[str, tuple[str, str]] = {}
        self._debouncer = Debouncer(
            hass,
            _LOGGER,
            cooldown=REFRESH_COOLDOWN,
            immediate=True,
            function=self._async_refresh_and_write,
        )

    @property
    def options(self) -> list[str]:
        """The areas and groups by name; "chosen lights" while single lights."""
        labels = list(self._choices)
        if self._follow == FOLLOW_LIGHTS:
            labels.insert(0, CHOSEN_LIGHTS)
        return labels

    @property
    def current_option(self) -> str | None:
        """The followed area or group; unknown if it no longer has colour lights."""
        if self._follow == FOLLOW_LIGHTS:
            return CHOSEN_LIGHTS
        output = self._entry.options[CONF_OUTPUT]
        key = output.get(CONF_AREA_ID if self._follow == FOLLOW_AREA else CONF_GROUP)
        return next(
            (
                label
                for label, choice in self._choices.items()
                if choice == (self._follow, key)
            ),
            None,
        )

    async def async_select_option(self, option: str) -> None:
        """Follow another area or group; a running pipeline restarts on it."""
        if option == CHOSEN_LIGHTS:
            return  # only offered while it is the current option
        follow, key = self._choices[option]
        output = {
            k: v
            for k, v in self._entry.options[CONF_OUTPUT].items()
            if k not in (CONF_AREA_ID, CONF_GROUP)
        }
        output[CONF_FOLLOW] = follow
        output[CONF_AREA_ID if follow == FOLLOW_AREA else CONF_GROUP] = key
        self.hass.config_entries.async_update_entry(
            self._entry, options={**self._entry.options, CONF_OUTPUT: output}
        )

    async def async_added_to_hass(self) -> None:
        """Make the list, and make it again when areas or lights change."""
        await super().async_added_to_hass()
        self._refresh()
        self.async_on_remove(self._debouncer.async_cancel)
        self.async_on_remove(
            self.hass.bus.async_listen(
                ar.EVENT_AREA_REGISTRY_UPDATED, self._async_event
            )
        )
        self.async_on_remove(
            self.hass.bus.async_listen(
                er.EVENT_ENTITY_REGISTRY_UPDATED,
                self._async_event,
                event_filter=_is_light_event,
            )
        )
        # A new light's registry entry comes before its first state.
        self.async_on_remove(
            async_track_state_added_domain(self.hass, LIGHT_DOMAIN, self._async_event)
        )
        if not self.hass.is_running:
            self.async_on_remove(
                self.hass.bus.async_listen_once(
                    EVENT_HOMEASSISTANT_STARTED, self._async_event
                )
            )

    @property
    def _follow(self) -> str:
        return self._entry.options[CONF_OUTPUT].get(CONF_FOLLOW, FOLLOW_LIGHTS)

    @callback
    def _async_runtime_changed(self) -> None:
        """A start, stop or options change: the list may have changed too."""
        self._refresh()
        self.async_write_ha_state()

    @callback
    def _async_event(self, event: Event) -> None:
        """Areas or lights changed: make the list again, once per burst."""
        self._debouncer.async_schedule_call()

    @callback
    def _async_refresh_and_write(self) -> None:
        self._refresh()
        self.async_write_ha_state()

    def _refresh(self) -> None:
        names = [
            *(
                (name, (FOLLOW_AREA, key))
                for key, name in areas_with_lights(self.hass).items()
            ),
            *(
                (name, (FOLLOW_GROUP, key))
                for key, name in groups_with_lights(self.hass).items()
            ),
        ]
        choices: dict[str, tuple[str, str]] = {}
        for name, choice in sorted(names, key=lambda item: (item[0], item[1])):
            label, number = name, 1
            while label in choices or label == CHOSEN_LIGHTS:
                number += 1
                label = f"{name} ({number})"
            choices[label] = choice
        self._choices = choices


@callback
def _is_light_event(event_data: Mapping[str, Any]) -> bool:
    """Only changes of light entities can change the list."""
    return str(event_data.get("entity_id", "")).startswith("light.")
