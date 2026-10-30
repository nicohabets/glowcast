"""End-to-end stub: hue-entertainment's stream goes to the fake bridge.

Only for the end-to-end run in scripts/ (`demo.py e2e`). Never install this on
a real Home Assistant.

hue-entertainment always opens its DTLS stream on UDP port 2100 of the
bridge's host. The demo's bridge is `127.0.0.1:<port>` (HTTPS on a free port,
not 443), so this stub changes only that address: the library's connection to
`127.0.0.1:<port>` goes to the fake bridge's DTLS port on 127.0.0.1 instead.
Glowcast's Hue output and the library's REST calls, handshake, encryption and
HueStream messages run unchanged. Any other host is refused, so the run never
streams to a real bridge.
"""

import logging

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

DOMAIN = "glowcast_e2e"
LOOPBACK = "127.0.0.1"

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {vol.Required("bridge"): cv.string, vol.Required("dtls_port"): cv.port}
        )
    },
    extra=vol.ALLOW_EXTRA,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Point the library's DTLS connection at the fake bridge."""
    bridge: str = config[DOMAIN]["bridge"]
    dtls_port: int = config[DOMAIN]["dtls_port"]
    # Imported in the executor: the library's imports must not block the loop.
    dtls = await hass.async_add_executor_job(_dtls_module)
    connection = dtls._DtlsConnection  # noqa: SLF001 - the one seam needed
    original = connection.__init__

    def redirected(self, host: str, port: int, identity: str, psk: bytes) -> None:
        if host != bridge:
            raise OSError(f"e2e: refusing a DTLS stream to {host}")
        original(self, LOOPBACK, dtls_port, identity, psk)

    connection.__init__ = redirected
    _LOGGER.warning("e2e: the Hue stream goes to the fake bridge on 127.0.0.1")
    return True


def _dtls_module():  # noqa: ANN202 - a module
    from hue_entertainment import dtls

    return dtls
