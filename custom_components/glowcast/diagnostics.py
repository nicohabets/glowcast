"""Diagnostics for bug reports: the setup and the last status, without secrets.

Architecture 4.4. Bug reports are public, so keys, hosts, unique ids, serial
numbers and names that identify a home (the entry title is the TV's name, the
area id) are redacted. Devices come through the source and output types.
"""

import dataclasses
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.loader import async_get_integration
from packaging.requirements import Requirement

from .const import (
    CONF_APP_KEY,
    CONF_AREA_ID,
    CONF_CLIENT_KEY,
    CONF_OUTPUT,
    CONF_SOURCE,
    CONF_TYPE,
    CONF_UNIQUE_ID,
    DOMAIN,
)
from .outputs import OUTPUTS
from .runtime import GlowcastConfigEntry
from .settings import read_settings
from .sources import SOURCES

TO_REDACT = {
    CONF_APP_KEY,
    CONF_CLIENT_KEY,
    CONF_UNIQUE_ID,
    CONF_AREA_ID,
    CONF_HOST,
    "title",
    "serial_number",
}
# What a device tells about its hardware and software; never its name.
DEVICE_FIELDS = ("manufacturer", "model", "sw_version")


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: GlowcastConfigEntry
) -> dict[str, Any]:
    """Return the diagnostics download for one Glowcast entry."""
    data, options = entry.data, entry.options
    integration = await async_get_integration(hass, DOMAIN)
    status = entry.runtime_data.status
    settings = read_settings(options)
    source_type = data[CONF_SOURCE][CONF_TYPE]
    output_type = options[CONF_OUTPUT][CONF_TYPE]
    diagnostics = {
        "entry": {
            "title": entry.title,
            "version": entry.version,
            "minor_version": entry.minor_version,
            "data": dict(data),
            "options": dict(options),
        },
        "versions": {
            "glowcast": str(integration.version),
            "requirements": await hass.async_add_executor_job(
                _installed_versions, integration.requirements
            ),
        },
        "settings": {
            "brightness": settings.brightness,
            "intensity": settings.intensity,
            "update_rate": settings.update_rate,
        },
        "source": {
            "type": source_type,
            "device": _device(hass, SOURCES[source_type].device_id(hass, data)),
        },
        "output": {
            "type": output_type,
            "device": _device(hass, OUTPUTS[output_type].device_id(hass, data)),
        },
        "syncing": entry.runtime_data.is_on,
        "status": None if status is None else dataclasses.asdict(status),
    }
    return async_redact_data(diagnostics, TO_REDACT)


def _installed_versions(requirements: list[str]) -> dict[str, str | None]:
    """The installed version of each requirement in the manifest."""
    versions: dict[str, str | None] = {}
    for requirement in requirements:
        name = Requirement(requirement).name
        try:
            versions[name] = version(name)
        except PackageNotFoundError:
            versions[name] = None
    return versions


def _device(hass: HomeAssistant, device_id: str | None) -> dict[str, Any] | None:
    """The hardware and software of a device in Home Assistant, if it exists."""
    device = None if device_id is None else dr.async_get(hass).async_get(device_id)
    if device is None:
        return None
    return {field: getattr(device, field) for field in DEVICE_FIELDS}
