"""Constants for Glowcast."""

from .core.liveliness import Liveliness
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
CONF_LIVELINESS = "liveliness"

# In the data of a reauth flow: which part rejected Glowcast's key. Without
# it, the output did.
REAUTH_PART = "glowcast_reauth_part"
REAUTH_SOURCE = "source"

UPDATE_RATES = [rate.value for rate in UpdateRate]
LIVELINESS_LEVELS = [level.value for level in Liveliness]

DEFAULT_BRIGHTNESS = 80
DEFAULT_INTENSITY = 100
DEFAULT_UPDATE_RATE = UpdateRate.HIGH.value
# Normal is v0.4, so an entry from before the setting keeps its lights (#16).
DEFAULT_LIVELINESS = Liveliness.NORMAL.value
