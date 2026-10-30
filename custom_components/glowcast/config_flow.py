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
    REAUTH_PART,
    REAUTH_SOURCE,
    UPDATE_RATES,
)
from .outputs import OUTPUT_OPTIONS_STEPS, OUTPUT_STEPS, OUTPUTS
from .outputs.blocks import output_types
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

    @property
    def chosen_outputs(self) -> list[tuple[Mapping[str, Any], Mapping[str, Any]]]:
        """No earlier output types at setup: an entry starts with one (flow.py)."""
        return []

    @property
    def combining(self) -> bool:
        """Never at setup: an entry starts with one output type (flow.py)."""
        return False

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Start with the choice of source type; one is used without a choice.

        A type without setup steps cannot be set up here. The menu shows each
        type's first step, labelled by the type's texts (menu_options).
        """
        setup_steps = [
            source_type.setup_step
            for source_type in SOURCES.values()
            if source_type.setup_step
        ]
        return await _async_menu(self, "user", setup_steps, user_input)

    async def async_source_chosen(
        self, source: dict[str, Any], title: str
    ) -> ConfigFlowResult:
        """The source's steps are done; go on with the output's."""
        self._source = source
        self._title = title
        return await self.async_step_output()

    async def async_step_output(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the output type; one is used without a choice.

        The menu shows each type's first step, labelled by the type's texts
        (menu_options).
        """
        setup_steps = [
            output_type.setup_step
            for output_type in OUTPUTS.values()
            if output_type.setup_step
        ]
        return await _async_menu(self, "output", setup_steps, user_input)

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
        """Pair again with the source or the output that rejected Glowcast's key.

        For an output, REAUTH_PART names the output type (a part of a combined
        output); without it, the first of the entry's output types that has a
        reauth step.
        """
        entry = self._get_reauth_entry()
        self._title = entry.title
        part = entry_data.get(REAUTH_PART)
        if part == REAUTH_SOURCE:
            step = SOURCES[entry.data[CONF_SOURCE][CONF_TYPE]].reauth_step
        else:
            type_ids = [part] if part in OUTPUTS else output_types(entry.options)
            step = next(
                (
                    output_type.reauth_step
                    for type_id in type_ids
                    if (output_type := OUTPUTS.get(type_id)) is not None
                    and output_type.reauth_step
                ),
                None,
            )
        if step is None:
            return self.async_abort(reason="reauth_unavailable")
        return await self._async_step(step)

    async def _async_step(
        self, step_id: str | None, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Run one of the types' steps."""
        return await getattr(self, f"async_step_{step_id}")(user_input)


class GlowcastOptionsBase(OptionsFlow):
    """The settings screen: what follows the TV, then the common settings.

    First the choice of output type, then that type's own steps (which may
    pair with the device the lights are on), then brightness, intensity and the
    update rate (architecture 4.2). A base class after the types' steps, so a
    type's steps can take part in what the flow provides (the combined output
    collects its parts' choices in `async_output_chosen`).
    """

    def __init__(self) -> None:
        """Initialize the flow."""
        self._data: dict[str, Any] = {}
        self._output: dict[str, Any] = {}

    @property
    def entry_title(self) -> str:
        """The entry's name, which the steps may use (flow.py)."""
        return self.config_entry.title

    @property
    def chosen_outputs(self) -> list[tuple[Mapping[str, Any], Mapping[str, Any]]]:
        """The data and block of the output types chosen before in this flow.

        Empty unless the combined output's steps run (flow.py).
        """
        return []

    @property
    def combining(self) -> bool:
        """Whether the combined output's steps run (flow.py); not here."""
        return False

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the output type; one is used without a choice.

        The menu shows each type's first step, labelled by the type's texts.
        Without a type that has steps, the stored output is kept.
        """
        options_steps = [
            output_type.options_step
            for output_type in OUTPUTS.values()
            if output_type.options_step
        ]
        if not options_steps:
            self._output = dict(self.config_entry.options[CONF_OUTPUT])
            return await self.async_step_settings()
        return await _async_menu(self, "init", options_steps)

    async def async_output_chosen(
        self, data: dict[str, Any], output: dict[str, Any]
    ) -> ConfigFlowResult:
        """The output's steps are done; go on with the common settings.

        `data` is what the output stores in the entry's data, for example the
        keys of a pairing; it is stored when the settings are saved.
        """
        self._data = data
        self._output = output
        return await self.async_step_settings()

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Brightness, intensity and the update rate; then store everything."""
        entry = self.config_entry
        if user_input is not None:
            if self._data and {**entry.data, **self._data} != entry.data:
                # Before the options, so the output finds its data when the
                # options change starts it.
                self.hass.config_entries.async_update_entry(
                    entry, data={**entry.data, **self._data}
                )
            return self.async_create_entry(
                data={
                    **entry.options,
                    CONF_OUTPUT: self._output,
                    **write_settings(read_settings(user_input)),
                }
            )
        settings = read_settings(entry.options)
        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(
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
            ),
        )


class GlowcastOptionsFlow(*OUTPUT_OPTIONS_STEPS, GlowcastOptionsBase):
    """The settings screen with every output type's steps."""


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


async def _async_menu(
    flow: ConfigFlow | OptionsFlow,
    step_id: str,
    steps: list[str],
    user_input: dict[str, Any] | None = None,
) -> ConfigFlowResult:
    """A menu of the types' first steps; with one type, its step without one."""
    if len(steps) == 1:
        return await getattr(flow, f"async_step_{steps[0]}")(user_input)
    return flow.async_show_menu(step_id=step_id, menu_options=steps)
