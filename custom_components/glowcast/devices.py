"""What the source and output types share to find their devices.

Each type knows its own integration and which of the entry's devices is
its own; this only finds the entry's devices.
"""

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr


def entry_devices(
    hass: HomeAssistant, domain: str, unique_id: str
) -> list[dr.DeviceEntry]:
    """The devices of the `domain` entry with this unique id; none if it is gone."""
    entry = hass.config_entries.async_entry_for_domain_unique_id(domain, unique_id)
    if entry is None:
        return []
    return dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
