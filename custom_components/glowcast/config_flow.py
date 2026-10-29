"""Setup, options and reauth flows for Glowcast (architecture 4.1 and 4.2)."""

import asyncio
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_APP_KEY,
    CONF_AREA_ID,
    CONF_BRIDGE,
    CONF_BRIGHTNESS,
    CONF_CLIENT_KEY,
    CONF_INTENSITY,
    CONF_OUTPUT,
    CONF_SOURCE,
    CONF_TYPE,
    CONF_UNIQUE_ID,
    CONF_UPDATE_RATE,
    DEFAULT_BRIGHTNESS,
    DEFAULT_INTENSITY,
    DEFAULT_UPDATE_RATE,
    DOMAIN,
    OUTPUT_HUE_ENTERTAINMENT,
    SOURCE_WEBOS,
    UPDATE_RATES,
)
from .core.ports import OutputAuthFailed, OutputUnavailable
from .outputs.hue_entertainment import (
    PairingFailed,
    async_areas,
    async_bridge_reachable,
    async_pair,
    bridge_host,
    bridges,
)
from .sources.webos import tvs

# Form field names; the stored data uses the keys from const.
FIELD_TV = "tv"
FIELD_BRIDGE = "bridge"
FIELD_AREA = "area"


class GlowcastConfigFlow(ConfigFlow, domain=DOMAIN):
    """Pick a TV, pair with a Hue bridge and pick an entertainment area."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._tv_unique_id = ""
        self._tv_name = ""
        self._bridge_unique_id = ""
        self._host = ""
        self._keys: tuple[str, str] = ("", "")
        self._pair_task: asyncio.Task[tuple[str, str]] | None = None
        self._error: str | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return GlowcastOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the TV; TVs that already have a Glowcast entry are not offered."""
        loaded = tvs(self.hass)
        if not loaded:
            return self.async_abort(reason="no_tv")
        configured = self._async_current_ids()
        available = {k: v for k, v in loaded.items() if k not in configured}
        if not available:
            return self.async_abort(reason="all_tvs_configured")
        if user_input is not None:
            unique_id = user_input[FIELD_TV]
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()
            self._tv_unique_id = unique_id
            self._tv_name = available[unique_id]
            return await self.async_step_bridge()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(FIELD_TV): _select(available)}),
        )

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
                self._bridge_unique_id = unique_id
                self._host = host
                return await self.async_step_link()
            errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="bridge",
            data_schema=vol.Schema({vol.Required(FIELD_BRIDGE): _select(available)}),
            errors=errors,
        )

    async def async_step_link(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the bridge's link button; shown again if pairing failed."""
        if user_input is not None:
            return await self.async_step_pair()
        return self.async_show_form(step_id="link", errors=self._pop_error())

    async def async_step_pair(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Wait (up to 30 s) while the bridge hands out a key."""
        # Always show the progress step first: HA would pass the link form's
        # input on to the next step if this one finished at once.
        if self._pair_task is None or not self._pair_task.done():
            if self._pair_task is None:
                self._pair_task = self.hass.async_create_task(
                    self._async_pair(), "glowcast hue pairing"
                )
            return self.async_show_progress(
                step_id="pair", progress_action="pair", progress_task=self._pair_task
            )
        task, self._pair_task = self._pair_task, None
        reauth = self.source == SOURCE_REAUTH
        try:
            self._keys = task.result()
        except PairingFailed:
            self._error = "button_not_pressed"
        except OutputUnavailable:
            self._error = "cannot_connect"
        if self._error:
            return self.async_show_progress_done(
                next_step_id="reauth_confirm" if reauth else "link"
            )
        return self.async_show_progress_done(
            next_step_id="reauth_save" if reauth else "area"
        )

    async def _async_pair(self) -> tuple[str, str]:
        """Pair; tell an unreachable bridge apart from an unpressed button.

        The library retries network errors until its 30 s deadline and then
        raises the same timeout as for an unpressed button.
        """
        try:
            return await async_pair(self._host, self._tv_name)
        except PairingFailed:
            if not await async_bridge_reachable(self.hass, self._host):
                raise OutputUnavailable("The Hue bridge stopped answering") from None
            raise

    async def async_step_area(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the entertainment area and create the entry.

        Without areas, or when the list cannot be read, the form stays open so
        the user can try again without pairing again (which would add another
        app on the bridge).
        """
        if user_input is not None and FIELD_AREA in user_input:
            app_key, client_key = self._keys
            return self.async_create_entry(
                title=self._tv_name,
                data={
                    CONF_SOURCE: {
                        CONF_TYPE: SOURCE_WEBOS,
                        CONF_UNIQUE_ID: self._tv_unique_id,
                    },
                    CONF_BRIDGE: {
                        CONF_UNIQUE_ID: self._bridge_unique_id,
                        CONF_APP_KEY: app_key,
                        CONF_CLIENT_KEY: client_key,
                    },
                },
                options=_options(
                    user_input[FIELD_AREA],
                    DEFAULT_BRIGHTNESS,
                    DEFAULT_INTENSITY,
                    DEFAULT_UPDATE_RATE,
                ),
            )
        try:
            areas = await async_areas(self.hass, self._host, self._keys[0])
        except (OutputAuthFailed, OutputUnavailable):
            return self.async_show_form(
                step_id="area", errors={"base": "cannot_connect"}
            )
        if not areas:
            return self.async_show_form(step_id="area", errors={"base": "no_areas"})
        return self.async_show_form(
            step_id="area",
            data_schema=vol.Schema({vol.Required(FIELD_AREA): _select(areas)}),
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start pairing again after the bridge rejected Glowcast's key."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the bridge's link button."""
        errors = self._pop_error()
        if user_input is not None:
            entry = self._get_reauth_entry()
            host = bridge_host(self.hass, entry.data[CONF_BRIDGE][CONF_UNIQUE_ID])
            if host is not None and await async_bridge_reachable(self.hass, host):
                self._host = host
                self._tv_name = entry.title
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
        app_key, client_key = self._keys
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

    @callback
    def async_remove(self) -> None:
        """Stop pairing when the user closes the flow.

        Otherwise a later button press would still add an app on the bridge.
        """
        if self._pair_task is not None:
            self._pair_task.cancel()

    def _pop_error(self) -> dict[str, str]:
        """Return the error left by a failed pairing, once."""
        error, self._error = self._error, None
        return {"base": error} if error else {}


class GlowcastOptionsFlow(OptionsFlow):
    """Change the entertainment area, brightness, intensity and update rate."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the settings screen."""
        if user_input is not None:
            return self.async_create_entry(
                data=_options(
                    user_input[FIELD_AREA],
                    user_input[CONF_BRIGHTNESS],
                    user_input[CONF_INTENSITY],
                    user_input[CONF_UPDATE_RATE],
                )
            )

        bridge = self.config_entry.data[CONF_BRIDGE]
        host = bridge_host(self.hass, bridge[CONF_UNIQUE_ID])
        if host is None:
            return self.async_abort(reason="cannot_connect")
        try:
            areas = await async_areas(self.hass, host, bridge[CONF_APP_KEY])
        except OutputAuthFailed:
            self.config_entry.async_start_reauth(self.hass)
            return self.async_abort(reason="reauth_required")
        except OutputUnavailable:
            return self.async_abort(reason="cannot_connect")
        if not areas:
            return self.async_abort(reason="no_areas")

        options = self.config_entry.options
        area_id = options[CONF_OUTPUT][CONF_AREA_ID]
        schema = vol.Schema(
            {
                # A removed area is not offered, so the user must pick another.
                vol.Required(
                    FIELD_AREA, default=area_id if area_id in areas else vol.UNDEFINED
                ): _select(areas),
                vol.Required(
                    CONF_BRIGHTNESS,
                    default=options.get(CONF_BRIGHTNESS, DEFAULT_BRIGHTNESS),
                ): _percentage(1, 100),
                vol.Required(
                    CONF_INTENSITY,
                    default=options.get(CONF_INTENSITY, DEFAULT_INTENSITY),
                ): _percentage(0, 200),
                vol.Required(
                    CONF_UPDATE_RATE,
                    default=options.get(CONF_UPDATE_RATE, DEFAULT_UPDATE_RATE),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=UPDATE_RATES,
                        translation_key=CONF_UPDATE_RATE,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)


def _options(
    area_id: str, brightness: float, intensity: float, update_rate: str
) -> dict[str, Any]:
    """Build the options as in architecture section 5."""
    return {
        CONF_OUTPUT: {CONF_TYPE: OUTPUT_HUE_ENTERTAINMENT, CONF_AREA_ID: area_id},
        # Number selectors return floats.
        CONF_BRIGHTNESS: int(brightness),
        CONF_INTENSITY: int(intensity),
        CONF_UPDATE_RATE: update_rate,
    }


def _select(choices: dict[str, str]) -> SelectSelector:
    """A dropdown of {value: label}, sorted by label."""
    return SelectSelector(
        SelectSelectorConfig(
            options=[
                SelectOptionDict(value=value, label=label)
                for value, label in sorted(choices.items(), key=lambda item: item[1])
            ],
            mode=SelectSelectorMode.DROPDOWN,
        )
    )


def _percentage(minimum: int, maximum: int) -> NumberSelector:
    """A slider in whole percents."""
    return NumberSelector(
        NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=1,
            unit_of_measurement="%",
            mode=NumberSelectorMode.SLIDER,
        )
    )
