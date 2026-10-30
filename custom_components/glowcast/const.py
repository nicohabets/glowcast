"""Constants for Glowcast."""

from .core.pipeline import UpdateRate

DOMAIN = "glowcast"

# Config entry data and options (architecture section 5).
CONF_SOURCE = "source"
CONF_OUTPUT = "output"
CONF_TYPE = "type"
CONF_UNIQUE_ID = "unique_id"
CONF_BRIGHTNESS = "brightness"
CONF_INTENSITY = "intensity"
CONF_UPDATE_RATE = "update_rate"

# In the data of a reauth flow: which part rejected Glowcast's key. Without
# it, the output did.
REAUTH_PART = "glowcast_reauth_part"
REAUTH_SOURCE = "source"

# Where a user reports how Glowcast works with their TV (#60, #214, #115).
HARDWARE_REPORT_URL = (
    "https://github.com/nicohabets/glowcast/issues/new?template=hardware-report.md"
)

UPDATE_RATES = [rate.value for rate in UpdateRate]

DEFAULT_BRIGHTNESS = 80
DEFAULT_INTENSITY = 100
DEFAULT_UPDATE_RATE = UpdateRate.HIGH.value
