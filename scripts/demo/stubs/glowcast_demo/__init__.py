"""Demo stub: Glowcast syncs with a fake TV source and a fake light output.

Only for the local demo in scripts/. Never install this on a real Home Assistant:
it replaces Glowcast's webOS source and Hue entertainment output with fakes
that never open a network connection.

It replaces the `create` of the `webos` and `hue_entertainment` entries in
Glowcast's SOURCES and OUTPUTS registries. Everything else in those entries
(checks, device links, setup steps, entities) stays Glowcast's own, so the
real pipeline, runtime, entities and status run unchanged.
"""

import dataclasses
import logging

import homeassistant.helpers.config_validation as cv
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

DOMAIN = "glowcast_demo"
GLOWCAST = "glowcast"
SOURCE = "webos"
OUTPUT = "hue_entertainment"

CONFIG_SCHEMA = cv.empty_config_schema(DOMAIN)

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Swap in the fakes before any Glowcast pipeline is built."""
    # Through HA's loader: importing Glowcast imports its libraries, which
    # must not happen in the event loop.
    await (await async_get_integration(hass, GLOWCAST)).async_get_component()
    from custom_components.glowcast.outputs import OUTPUTS
    from custom_components.glowcast.sources import SOURCES

    from .fakes import FakeLightOutput, FakeTvSource

    # Changed in place: Glowcast's modules hold these same dicts.
    SOURCES[SOURCE] = dataclasses.replace(
        SOURCES[SOURCE], create=lambda hass, data, options: FakeTvSource()
    )
    OUTPUTS[OUTPUT] = dataclasses.replace(
        OUTPUTS[OUTPUT],
        create=lambda hass, data, options: FakeLightOutput(options),
    )
    # No reload needed: `start` writes a config without a Glowcast entry, and
    # a pipeline is only built when the switch is turned on.
    _LOGGER.warning("demo: fake TV source and fake light output active")
    return True
