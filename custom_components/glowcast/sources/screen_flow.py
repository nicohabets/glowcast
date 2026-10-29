"""The screen source type's steps: find the helper and pair (architecture 4.1).

Setup: Home Assistant's zeroconf discovery finds the helper
(`_glowcast._tcp.local.`), or the user types its address; then the pairing code
the helper shows. Reauth: a new pairing code. The helper's id is the entry's
unique id, so a helper found at a new address updates its entry.
"""

import re
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_TOKEN
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from ..const import CONF_SOURCE, CONF_TYPE, DOMAIN
from ..core.ports import SourceAuthFailed, SourceUnavailable
from .screen import (
    CONF_HELPER_ID,
    DEFAULT_PORT,
    PROTOCOL,
    SOURCE_SCREEN,
    PairingRefused,
    async_info,
    async_pair,
    async_reachable,
)

FIELD_CODE = "code"
# The 6 digits the helper shows; spaces around them are ignored.
_CODE = re.compile(r"[0-9]{6}")  # ASCII only: \d also matches other digits


class ScreenSteps(ConfigFlow):
    """Find the screen helper and pair with its code."""

    _screen_host = ""
    _screen_port = DEFAULT_PORT
    # The helper id from discovery; empty when the address was typed.
    _screen_helper_id = ""
    _screen_name = ""

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """A helper announced itself; update its entry, or offer to set it up."""
        helper_id = discovery_info.properties.get("id")
        if not helper_id or discovery_info.port is None:
            return self.async_abort(reason="screen_unsupported")
        if discovery_info.properties.get("protocol") != str(PROTOCOL):
            return self.async_abort(reason="screen_unsupported")
        self._screen_host = str(discovery_info.ip_address)
        self._screen_port = discovery_info.port
        self._screen_helper_id = helper_id
        self._screen_name = discovery_info.properties.get("name") or helper_id[:6]
        await self.async_set_unique_id(helper_id)
        self._screen_abort_if_configured("screen_already_configured")
        self.context["title_placeholders"] = {"name": self._screen_name}
        return await self.async_step_screen_code()

    async def async_step_screen(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Type the helper's address, for a network where discovery fails."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = user_input[CONF_PORT]
            if await async_reachable(self.hass, host, port):
                self._screen_host, self._screen_port = host, port
                return await self.async_step_screen_code()
            errors["base"] = "screen_cannot_connect"
        return self.async_show_form(
            step_id="screen",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(CONF_HOST): str,
                        vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.All(
                            vol.Coerce(int), vol.Range(min=1, max=65535)
                        ),
                    }
                ),
                user_input,
            ),
            errors=errors,
        )

    async def async_step_screen_code(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the pairing code; the source's steps are done."""
        errors: dict[str, str] = {}
        if user_input is not None:
            paired = await self._async_screen_pair(user_input, errors)
            if paired is not None:
                token, info = paired
                await self.async_set_unique_id(info["id"], raise_on_progress=False)
                self._screen_abort_if_configured("screen_already_configured", token)
                name = str(info.get("name") or self._screen_name)
                return await self.async_source_chosen(
                    {
                        CONF_TYPE: SOURCE_SCREEN,
                        CONF_HELPER_ID: info["id"],
                        CONF_HOST: self._screen_host,
                        CONF_PORT: self._screen_port,
                        CONF_TOKEN: token,
                    },
                    name,
                )
        return self.async_show_form(
            step_id="screen_code",
            data_schema=vol.Schema({vol.Required(FIELD_CODE): str}),
            description_placeholders={
                "computer": self._screen_name or self._screen_host
            },
            errors=errors,
        )

    async def async_step_screen_reauth(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pair again with a new code; all settings stay as they are.

        No reload: the source reads its token from the entry at every start.
        """
        entry = self._get_reauth_entry()
        source = entry.data[CONF_SOURCE]
        self._screen_host = source[CONF_HOST]
        self._screen_port = source[CONF_PORT]
        self._screen_helper_id = source[CONF_HELPER_ID]
        errors: dict[str, str] = {}
        if user_input is not None:
            paired = await self._async_screen_pair(user_input, errors)
            if paired is not None:
                token, _ = paired
                self.hass.config_entries.async_update_entry(
                    entry,
                    data={**entry.data, CONF_SOURCE: {**source, CONF_TOKEN: token}},
                )
                return self.async_abort(reason="screen_reauth_successful")
        return self.async_show_form(
            step_id="screen_reauth",
            data_schema=vol.Schema({vol.Required(FIELD_CODE): str}),
            description_placeholders={"computer": entry.title},
            errors=errors,
        )

    async def _async_screen_pair(
        self, user_input: dict[str, Any], errors: dict[str, str]
    ) -> tuple[str, dict[str, Any]] | None:
        """Pair with the code; return the token and the helper's info.

        On a problem, sets the form's error and returns None, so the user can
        try again. A helper with another id than the one discovered (or the
        one of the entry) is another helper at that address.
        """
        code = user_input[FIELD_CODE].strip()
        if not _CODE.fullmatch(code):
            errors["base"] = "screen_wrong_code"
            return None
        try:
            token, helper_id = await async_pair(
                self.hass, self._screen_host, self._screen_port, code
            )
            if self._screen_helper_id and helper_id != self._screen_helper_id:
                errors["base"] = "screen_other_helper"
                return None
            info = await async_info(
                self.hass, self._screen_host, self._screen_port, token
            )
        except PairingRefused as refused:
            if refused.reason == "pairing_unavailable":
                errors["base"] = "screen_pairing_unavailable"
            else:
                errors["base"] = "screen_wrong_code"
            return None
        except (SourceAuthFailed, SourceUnavailable):
            errors["base"] = "screen_cannot_connect"
            return None
        if info.get("id") != helper_id or info.get("protocol") != PROTOCOL:
            errors["base"] = "screen_unsupported"
            return None
        return token, info

    def _screen_abort_if_configured(
        self, reason: str, token: str | None = None
    ) -> None:
        """Abort if the helper has an entry; keep its address current.

        A token from a pairing that just worked (with the helper's id checked)
        is stored too, so it is not thrown away. The address and token are
        read at every start, so the entry is not reloaded, which would stop a
        running sync.
        """
        entry = self.hass.config_entries.async_entry_for_domain_unique_id(
            DOMAIN, self.unique_id or ""
        )
        updates = None
        if entry is not None and CONF_SOURCE in entry.data:
            updates = {
                CONF_SOURCE: {
                    **entry.data[CONF_SOURCE],
                    CONF_HOST: self._screen_host,
                    CONF_PORT: self._screen_port,
                    **({CONF_TOKEN: token} if token is not None else {}),
                }
            }
        self._abort_if_unique_id_configured(
            updates=updates, reload_on_update=False, error=reason
        )
