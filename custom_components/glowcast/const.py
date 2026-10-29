"""Constants for Glowcast."""

from .core.pipeline import UpdateRate

DOMAIN = "glowcast"

# Config entry data and options (architecture section 5).
CONF_SOURCE = "source"
CONF_BRIDGE = "bridge"
CONF_OUTPUT = "output"
CONF_TYPE = "type"
CONF_UNIQUE_ID = "unique_id"
CONF_APP_KEY = "app_key"
CONF_CLIENT_KEY = "client_key"
CONF_AREA_ID = "area_id"
CONF_BRIGHTNESS = "brightness"
CONF_INTENSITY = "intensity"
CONF_UPDATE_RATE = "update_rate"

SOURCE_WEBOS = "webos"
OUTPUT_HUE_ENTERTAINMENT = "hue_entertainment"

UPDATE_RATES = [rate.value for rate in UpdateRate]

DEFAULT_BRIGHTNESS = 80
DEFAULT_INTENSITY = 100
DEFAULT_UPDATE_RATE = UpdateRate.HIGH.value
