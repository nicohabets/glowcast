"""Demo TV entity, so the blueprint's TV picker has a webOS TV to offer (#161).

Only for the local demo in scripts/. It is always on, shows "HDMI 1", and
reaches no TV. Glowcast itself does not use it.
"""

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Add the TV's media player."""
    assert entry.unique_id is not None
    async_add_entities([DemoTv(entry.unique_id)])


class DemoTv(MediaPlayerEntity):
    """The TV, on, with a few generic sources."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_device_class = MediaPlayerDeviceClass.TV
    _attr_state = MediaPlayerState.ON
    _attr_source = "HDMI 1"
    _attr_source_list = ["HDMI 1", "HDMI 2", "Netflix"]

    def __init__(self, unique_id: str) -> None:
        """Belong to the TV's device."""
        self._attr_unique_id = unique_id
        self._attr_device_info = DeviceInfo(identifiers={("webostv", unique_id)})
