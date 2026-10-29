"""Demo stub: a flow class, so the stored entry can load."""

from homeassistant.config_entries import ConfigFlow


class StubFlow(ConfigFlow, domain="webostv"):
    """Never started; the demo writes the entry itself."""

    VERSION = 1
