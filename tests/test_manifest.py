"""Home Assistant can find and load the integration's manifest."""

from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from custom_components.glowcast.const import DOMAIN


async def test_integration_loads(
    hass: HomeAssistant, enable_custom_integrations: None
) -> None:
    """The manifest is valid for this Home Assistant version."""
    integration = await async_get_integration(hass, DOMAIN)

    assert integration.domain == DOMAIN
    assert integration.dependencies == ["webostv", "hue"]
    assert integration.requirements == ["hue-entertainment==0.1.2"]
