"""Demo lights for loose lights (#9): three colour lights that only exist here.

Only for the local demo in scripts/. They keep the state they are given and
reach no device. Two of them are in the area "Living room", so the demo can
show single lights, an area and the positions. Generic names only.
"""

from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

# (unique id, name, in the living room)
LIGHTS = [
    ("demo-floor-lamp", "Floor lamp", True),
    ("demo-light-bar", "Light bar", True),
    ("demo-desk-lamp", "Desk lamp", False),
]
AREA = "Living room"


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Add the demo lights and put two of them in the living room."""
    async_add_entities([DemoLight(unique_id, name) for unique_id, name, _ in LIGHTS])
    area = ar.async_get(hass).async_get_or_create(AREA)
    registry = er.async_get(hass)
    for unique_id, _, in_area in LIGHTS:
        entity_id = registry.async_get_entity_id("light", "glowcast_demo", unique_id)
        if in_area and entity_id is not None:
            registry.async_update_entity(entity_id, area_id=area.id)


class DemoLight(LightEntity):
    """A colour light that takes every command at once."""

    _attr_should_poll = False
    _attr_color_mode = ColorMode.RGB
    _attr_supported_color_modes = {ColorMode.RGB}
    _attr_supported_features = LightEntityFeature.TRANSITION

    def __init__(self, unique_id: str, name: str) -> None:
        self._attr_unique_id = unique_id
        self._attr_name = name
        self._attr_is_on = True
        self._attr_brightness = 180
        self._attr_rgb_color = (255, 200, 140)

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._attr_is_on = True
        if ATTR_BRIGHTNESS in kwargs:
            self._attr_brightness = kwargs[ATTR_BRIGHTNESS]
        if ATTR_RGB_COLOR in kwargs:
            self._attr_rgb_color = kwargs[ATTR_RGB_COLOR]
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._attr_is_on = False
        self.async_write_ha_state()
