"""Demo stub: Glowcast syncs with a fake TV source and a fake light output.

Only for the local demo in scripts/. Never install this on a real Home Assistant:
it replaces Glowcast's webOS source and Hue entertainment output with fakes
that never open a network connection.

It replaces the `create` of the `webos` and `hue_entertainment` entries in
Glowcast's SOURCES and OUTPUTS registries. Everything else in those entries
(checks, device links, setup steps, entities) stays Glowcast's own, so the
real pipeline, runtime, entities and status run unchanged.

It also has a service, `glowcast_demo.discover_screen_helper`, that hands
Glowcast a zeroconf discovery of a screen helper, as Home Assistant's zeroconf
integration would. The demo loads no discovery integration (nothing is looked
for on the network); the recorder calls this service for a helper it started
on 127.0.0.1, which does not announce itself.
"""

import dataclasses
import logging
from ipaddress import ip_address

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import discovery_flow
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
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

    async def discover_screen_helper(call: ServiceCall) -> None:
        host = ip_address(call.data["host"])
        discovery_flow.async_create_flow(
            hass,
            GLOWCAST,
            {"source": "zeroconf"},
            ZeroconfServiceInfo(
                ip_address=host,
                ip_addresses=[host],
                port=call.data["port"],
                hostname="demo-helper.local.",
                type="_glowcast._tcp.local.",
                name=f"{call.data['name']}._glowcast._tcp.local.",
                properties={
                    "id": call.data["id"],
                    "name": call.data["name"],
                    "protocol": "1",
                },
            ),
        )

    hass.services.async_register(
        DOMAIN,
        "discover_screen_helper",
        discover_screen_helper,
        vol.Schema(
            {
                vol.Required("host"): str,
                vol.Required("port"): int,
                vol.Required("id"): str,
                vol.Required("name"): str,
            }
        ),
    )
    return True
