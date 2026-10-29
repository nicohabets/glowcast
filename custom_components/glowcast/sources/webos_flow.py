"""The webOS source type's setup step: pick the TV (architecture 4.1)."""

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from ..const import CONF_TYPE, CONF_UNIQUE_ID
from ..flow import dropdown
from .webos import SOURCE_WEBOS, tvs

FIELD_TV = "tv"


class WebOsSteps(ConfigFlow):
    """Pick a TV of the LG webOS TV integration; its unique id is the entry's."""

    async def async_step_tv(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the TV; TVs that already have a Glowcast entry are not offered.

        Shown as the flow's first screen ("user"): the only source type needs
        no choice before it.
        """
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
            return await self.async_source_chosen(
                {CONF_TYPE: SOURCE_WEBOS, CONF_UNIQUE_ID: unique_id},
                available[unique_id],
            )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(FIELD_TV): dropdown(available)}),
        )

    async def async_source_chosen(
        self, source: dict[str, Any], title: str
    ) -> ConfigFlowResult:
        """Provided by the flow that runs these steps (flow.py)."""
        raise NotImplementedError
