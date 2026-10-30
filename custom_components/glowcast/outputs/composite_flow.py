"""The combined output's steps in the settings screen (#11, architecture 4.2).

Generic: it runs the settings steps of every other output type in turn, in
the order of the registry, each with its own stored block as the default, and
stores their blocks as one combined block. Each type's steps end, as on their
own, with `async_output_chosen` (flow.py); this mixin collects them until the
last one is done.
"""

from collections.abc import Mapping
from typing import Any

from homeassistant.config_entries import ConfigFlowResult, OptionsFlow

from .blocks import OUTPUT_COMBINED, combine


class CombinedOptionsSteps(OptionsFlow):
    """Every other output type's settings steps, then one combined block."""

    _combined_steps: list[str]
    _combined_chosen: list[tuple[dict[str, Any], dict[str, Any]]]
    _combined_collecting = False

    async def async_step_combined_options(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Start with the first type's steps."""
        from . import OUTPUTS  # the registry imports this module

        self._combined_steps = [
            output_type.options_step
            for type_id, output_type in OUTPUTS.items()
            if type_id != OUTPUT_COMBINED and output_type.options_step
        ]
        self._combined_chosen = []
        self._combined_collecting = True
        return await self._async_combined_next()

    @property
    def chosen_outputs(self) -> list[tuple[Mapping[str, Any], Mapping[str, Any]]]:
        """The data and block each earlier type's steps handed in this flow."""
        if not self._combined_collecting:
            return []
        return list(self._combined_chosen)

    @property
    def combining(self) -> bool:
        """Whether the parts' steps run now (flow.py)."""
        return self._combined_collecting

    async def async_output_chosen(
        self, data: dict[str, Any], output: dict[str, Any]
    ) -> ConfigFlowResult:
        """Collect one type's choice; after the last, hand on the combination."""
        if not self._combined_collecting:
            return await super().async_output_chosen(data, output)  # type: ignore[misc]
        self._combined_chosen.append((data, output))
        if self._combined_steps:
            return await self._async_combined_next()
        self._combined_collecting = False
        merged: dict[str, Any] = {}
        for part_data, _ in self._combined_chosen:
            merged.update(part_data)
        return await super().async_output_chosen(  # type: ignore[misc]
            merged, combine([block for _, block in self._combined_chosen])
        )

    async def _async_combined_next(self) -> ConfigFlowResult:
        step = self._combined_steps.pop(0)
        return await getattr(self, f"async_step_{step}")()
