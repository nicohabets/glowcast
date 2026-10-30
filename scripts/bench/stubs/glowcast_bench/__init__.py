"""Bench stub: a service that sets a Glowcast entry's output block.

Only for the test bench in scripts/bench/. The loose-lights output stores its
lights by entity registry id, which exists only once Home Assistant has added
the proxy lights; the bench reads them then and stores the block with
`glowcast_bench.set_output`, as Glowcast's options flow would (the same
output type, so Glowcast takes it without a reload).
"""

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.typing import ConfigType

DOMAIN = "glowcast_bench"
CONFIG_SCHEMA = cv.empty_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register `glowcast_bench.set_output`."""

    async def set_output(call: ServiceCall) -> None:
        entry = hass.config_entries.async_get_entry(call.data["entry_id"])
        if entry is None or entry.domain != "glowcast":
            raise HomeAssistantError("no such Glowcast entry")
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, "output": dict(call.data["output"])}
        )

    hass.services.async_register(
        DOMAIN,
        "set_output",
        set_output,
        vol.Schema({vol.Required("entry_id"): str, vol.Required("output"): dict}),
    )
    return True
