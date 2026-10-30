"""Bench stub: a flow class, so the stored entry can load."""

from homeassistant.config_entries import ConfigFlow


class StubFlow(ConfigFlow, domain="matter"):
    """Never started; the bench writes the entry itself."""

    VERSION = 1
