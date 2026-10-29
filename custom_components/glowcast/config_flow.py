"""Setup, options and reauth flows for Glowcast (architecture 4.1 and 4.2).

Generic: each source and output type brings its own steps and its part of the
options screen (flow.py). These flows only chain them and store the result.
"""

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
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
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_BRIGHTNESS,
    CONF_INTENSITY,
    CONF_OUTPUT,
    CONF_SOURCE,
    CONF_TYPE,
    CONF_UPDATE_RATE,
    DOMAIN,
    UPDATE_RATES,
)
from .outputs import OUTPUT_STEPS, OUTPUTS, OutputType
from .settings import read_settings, write_settings
from .sources import SOURCE_STEPS, SOURCES


class GlowcastConfigFlow(*SOURCE_STEPS, *OUTPUT_STEPS, ConfigFlow, domain=DOMAIN):
    """Run the source type's steps, then the output type's, and store both."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._title = ""
        self._source: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return GlowcastOptionsFlow()

    @property
    def entry_title(self) -> str:
        """The entry's name, which the steps may use (flow.py)."""
        return self._title

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Start with the source; one source type is used without a choice."""
        (source_type,) = SOURCES.values()
        return await self._async_step(source_type.setup_step, user_input)

    async def async_source_chosen(
        self, source: dict[str, Any], title: str
    ) -> ConfigFlowResult:
        """The source's steps are done; go on with the output's."""
        self._source = source
        self._title = title
        # One output type is used without a choice (#9 adds the choice).
        (output_type,) = OUTPUTS.values()
        return await self._async_step(output_type.setup_step)

    async def async_output_chosen(
        self, data: dict[str, Any], output: dict[str, Any]
    ) -> ConfigFlowResult:
        """The output's steps are done; create the entry (architecture 5)."""
        return self.async_create_entry(
            title=self._title,
            data={CONF_SOURCE: self._source, **data},
            # No settings stored yet, so every one is its default.
            options={CONF_OUTPUT: output, **write_settings(read_settings({}))},
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Pair again after the output rejected Glowcast's key."""
        entry = self._get_reauth_entry()
        self._title = entry.title
        output_type = OUTPUTS[entry.options[CONF_OUTPUT][CONF_TYPE]]
        return await self._async_step(output_type.reauth_step)

    async def _async_step(
        self, step_id: str | None, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Run one of the types' steps."""
        return await getattr(self, f"async_step_{step_id}")(user_input)


class GlowcastOptionsFlow(OptionsFlow):
    """What follows the TV (the output type's part), then the common settings."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the settings screen."""
        options = self.config_entry.options
        output_type = OUTPUTS.get(options[CONF_OUTPUT][CONF_TYPE])
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_OUTPUT: _output(output_type, options, user_input),
                    **write_settings(read_settings(user_input)),
                }
            )

        fields: dict[vol.Marker, Any] = {}
        if output_type is not None and output_type.options_fields is not None:
            fields.update(await output_type.options_fields(self))
        settings = read_settings(options)
        fields.update(
            {
                vol.Required(CONF_BRIGHTNESS, default=settings.brightness): (
                    _percentage(1, 100)
                ),
                vol.Required(CONF_INTENSITY, default=settings.intensity): (
                    _percentage(0, 200)
                ),
                vol.Required(
                    CONF_UPDATE_RATE, default=settings.update_rate.value
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=UPDATE_RATES,
                        translation_key=CONF_UPDATE_RATE,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=vol.Schema(fields))


def _output(
    output_type: OutputType | None,
    options: Mapping[str, Any],
    user_input: Mapping[str, Any],
) -> dict[str, Any]:
    """The output's block from the options screen; kept if it has no part."""
    if output_type is None or output_type.options_output is None:
        return dict(options[CONF_OUTPUT])
    return output_type.options_output(user_input)


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
