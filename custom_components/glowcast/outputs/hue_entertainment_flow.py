"""The Hue entertainment-area output type's steps (architecture 4.1 and 4.2).

Setup: pick the bridge, press its link button, pair, pick the area. Reauth:
press the link button again. The options screen: the area.
"""

import asyncio
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntryBaseFlow,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import AbortFlow
from homeassistant.helpers.dispatcher import async_dispatcher_send

from ..const import CONF_OUTPUT, CONF_TYPE, CONF_UNIQUE_ID
from ..core.ports import OutputAuthFailed, OutputUnavailable
from ..flow import dropdown
from .hue_entertainment import (
    CONF_APP_KEY,
    CONF_AREA_ID,
    CONF_BRIDGE,
    CONF_CLIENT_KEY,
    OUTPUT_HUE_ENTERTAINMENT,
    PairingFailed,
    async_areas,
    async_bridge_reachable,
    async_pair,
    bridge_host,
    bridges,
    signal_areas_read,
)

# Form field names; the stored data uses the keys from hue_entertainment.
FIELD_BRIDGE = "bridge"
FIELD_AREA = "area"


class HueEntertainmentSteps(ConfigEntryBaseFlow):
    """Pair with a Hue bridge and pick an entertainment area.

    Uses only what the setup flow and the options flow both have, so the
    options flow can run these steps too, to pair from the settings (#9).
    """

    _hue_bridge_unique_id = ""
    _hue_host = ""
    _hue_keys: tuple[str, str] = ("", "")
    _hue_pair_task: asyncio.Task[tuple[str, str]] | None = None
    _hue_error: str | None = None
    # Where pairing goes on: the step after a new key, and the step that asks
    # for the link button again when pairing failed.
    _hue_paired_step = "area"
    _hue_retry_step = "link"

    async def async_step_bridge(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the Hue bridge and check that it answers."""
        available = bridges(self.hass)
        if not available:
            return self.async_abort(reason="no_bridge")
        errors: dict[str, str] = {}
        if user_input is not None:
            unique_id = user_input[FIELD_BRIDGE]
            host = bridge_host(self.hass, unique_id)
            if host is not None and await async_bridge_reachable(self.hass, host):
                self._hue_bridge_unique_id = unique_id
                self._hue_host = host
                self._hue_paired_step, self._hue_retry_step = "area", "link"
                return await self.async_step_link()
            errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="bridge",
            data_schema=vol.Schema({vol.Required(FIELD_BRIDGE): dropdown(available)}),
            errors=errors,
        )

    async def async_step_link(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the bridge's link button; shown again if pairing failed."""
        if user_input is not None:
            return await self.async_step_pair()
        return self.async_show_form(step_id="link", errors=self._hue_pop_error())

    async def async_step_pair(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Wait (up to 30 s) while the bridge hands out a key."""
        # Always show the progress step first: HA would pass the link form's
        # input on to the next step if this one finished at once.
        if self._hue_pair_task is None or not self._hue_pair_task.done():
            if self._hue_pair_task is None:
                self._hue_pair_task = self.hass.async_create_task(
                    self._async_hue_pair(), "glowcast hue pairing"
                )
            return self.async_show_progress(
                step_id="pair",
                progress_action="pair",
                progress_task=self._hue_pair_task,
            )
        task, self._hue_pair_task = self._hue_pair_task, None
        try:
            self._hue_keys = task.result()
        except PairingFailed:
            self._hue_error = "button_not_pressed"
        except OutputUnavailable:
            self._hue_error = "cannot_connect"
        if self._hue_error:
            return self.async_show_progress_done(next_step_id=self._hue_retry_step)
        return self.async_show_progress_done(next_step_id=self._hue_paired_step)

    async def _async_hue_pair(self) -> tuple[str, str]:
        """Pair; tell an unreachable bridge apart from an unpressed button.

        The library retries network errors until its 30 s deadline and then
        raises the same timeout as for an unpressed button.
        """
        try:
            return await async_pair(self._hue_host, self.entry_title)
        except PairingFailed:
            if not await async_bridge_reachable(self.hass, self._hue_host):
                raise OutputUnavailable("The Hue bridge stopped answering") from None
            raise

    async def async_step_area(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the entertainment area; the output's steps are done.

        Without areas, or when the list cannot be read, the form stays open so
        the user can try again without pairing again (which would add another
        app on the bridge).
        """
        if user_input is not None and FIELD_AREA in user_input:
            app_key, client_key = self._hue_keys
            return await self.async_output_chosen(
                {
                    CONF_BRIDGE: {
                        CONF_UNIQUE_ID: self._hue_bridge_unique_id,
                        CONF_APP_KEY: app_key,
                        CONF_CLIENT_KEY: client_key,
                    },
                },
                options_output(user_input),
            )
        try:
            areas = await async_areas(self.hass, self._hue_host, self._hue_keys[0])
        except (OutputAuthFailed, OutputUnavailable):
            return self.async_show_form(
                step_id="area", errors={"base": "cannot_connect"}
            )
        if not areas:
            return self.async_show_form(step_id="area", errors={"base": "no_areas"})
        return self.async_show_form(
            step_id="area",
            data_schema=vol.Schema({vol.Required(FIELD_AREA): dropdown(areas)}),
        )

    @callback
    def async_remove(self) -> None:
        """Stop pairing when the user closes the flow.

        Otherwise a later button press would still add an app on the bridge.
        """
        if self._hue_pair_task is not None:
            self._hue_pair_task.cancel()
        super().async_remove()

    def _hue_pop_error(self) -> dict[str, str]:
        """Return the error left by a failed pairing, once."""
        error, self._hue_error = self._hue_error, None
        return {"base": error} if error else {}


class HueEntertainmentConfigSteps(HueEntertainmentSteps, ConfigFlow):
    """The setup steps, and pairing again when the bridge rejects the key."""

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the bridge's link button."""
        errors = self._hue_pop_error()
        if user_input is not None:
            entry = self._get_reauth_entry()
            host = bridge_host(self.hass, entry.data[CONF_BRIDGE][CONF_UNIQUE_ID])
            if host is not None and await async_bridge_reachable(self.hass, host):
                self._hue_host = host
                self._hue_paired_step = "reauth_save"
                self._hue_retry_step = "reauth_confirm"
                return await self.async_step_pair()
            errors["base"] = "cannot_connect"
        return self.async_show_form(step_id="reauth_confirm", errors=errors)

    async def async_step_reauth_save(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Store the new keys; all settings stay as they are.

        No reload: the entry has an update listener (#7), and the keys are read
        from the entry at every start, so the next switch-on uses them.
        """
        entry = self._get_reauth_entry()
        app_key, client_key = self._hue_keys
        self.hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                CONF_BRIDGE: {
                    **entry.data[CONF_BRIDGE],
                    CONF_APP_KEY: app_key,
                    CONF_CLIENT_KEY: client_key,
                },
            },
        )
        return self.async_abort(reason="reauth_successful")


async def async_options_fields(flow: OptionsFlow) -> dict[vol.Marker, Any]:
    """The area on the options screen, from the bridge's current list.

    Raises AbortFlow when the list cannot be read. Tells the area entity the
    list it read, so both show the same areas.
    """
    entry = flow.config_entry
    bridge = entry.data[CONF_BRIDGE]
    host = bridge_host(flow.hass, bridge[CONF_UNIQUE_ID])
    if host is None:
        raise AbortFlow("cannot_connect")
    try:
        areas = await async_areas(flow.hass, host, bridge[CONF_APP_KEY])
    except OutputAuthFailed:
        entry.async_start_reauth(flow.hass)
        raise AbortFlow("reauth_required") from None
    except OutputUnavailable:
        raise AbortFlow("cannot_connect") from None
    async_dispatcher_send(flow.hass, signal_areas_read(entry.entry_id), areas)
    if not areas:
        raise AbortFlow("no_areas")
    area_id = entry.options[CONF_OUTPUT][CONF_AREA_ID]
    return {
        # A removed area is not offered, so the user must pick another.
        vol.Required(
            FIELD_AREA, default=area_id if area_id in areas else vol.UNDEFINED
        ): dropdown(areas),
    }


def options_output(user_input: Mapping[str, Any]) -> dict[str, Any]:
    """The output's block in `options` (architecture 5) from the chosen area."""
    return {CONF_TYPE: OUTPUT_HUE_ENTERTAINMENT, CONF_AREA_ID: user_input[FIELD_AREA]}
