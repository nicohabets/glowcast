"""Diagnostics for bug reports: the setup and the last status, without secrets.

Architecture 4.4. Bug reports are public, so the download never contains a
stored value of the entry (keys, ids, the area): it shows their shape, the
settings and the types instead, so a key that a later type adds cannot leak.
Devices come through the source and output types and show only hardware and
software, never a name or serial number.
"""

import dataclasses
from collections.abc import Mapping
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.loader import async_get_integration
from packaging.requirements import Requirement

from .const import CONF_OUTPUT, CONF_SOURCE, CONF_TYPE, DOMAIN
from .outputs import OUTPUTS
from .runtime import ISSUES, GlowcastConfigEntry, GlowcastData, issue_id
from .settings import read_settings
from .sources import SOURCES

# A backstop: nothing below should carry these.
TO_REDACT = {CONF_HOST, "title", "serial_number"}
# What a device tells about its hardware and software; never its name.
DEVICE_FIELDS = ("manufacturer", "model", "sw_version")


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: GlowcastConfigEntry
) -> dict[str, Any]:
    """Return the diagnostics download for one Glowcast entry."""
    data, options = entry.data, entry.options
    integration = await async_get_integration(hass, DOMAIN)
    # Not there while the entry is not loaded (disabled, failed or unloaded).
    runtime: GlowcastData | None = getattr(entry, "runtime_data", None)
    status = None if runtime is None else runtime.status
    settings = read_settings(options)
    source_type = data[CONF_SOURCE][CONF_TYPE]
    output_type = options[CONF_OUTPUT][CONF_TYPE]
    issues = ir.async_get(hass)
    diagnostics = {
        "entry": {
            "version": entry.version,
            "minor_version": entry.minor_version,
            "stored": {"data": _shape(data), "options": _shape(options)},
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
        "syncing": runtime is not None and runtime.is_on,
        "status": None if status is None else dataclasses.asdict(status),
        "repair_issues": [
            key
            for key in ISSUES
            if issues.async_get_issue(DOMAIN, issue_id(entry.entry_id, key))
        ],
    }
    return async_redact_data(diagnostics, TO_REDACT)


def _shape(value: Any) -> Any:
    """The keys of a stored mapping with the type of each value, not the value.

    The type of a block (its "type" key) is shown in "source" and "output".
    """
    if isinstance(value, Mapping):
        return {key: _shape(item) for key, item in value.items()}
    return type(value).__name__


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
