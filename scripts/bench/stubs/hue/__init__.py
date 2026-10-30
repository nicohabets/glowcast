"""Bench stub for Home Assistant's hue integration: the demo's stub (the entry
loads), plus proxy lights when the entry's data has them (proxy.py).

Only for the test bench in scripts/bench/. Never install this on a real Home
Assistant: it replaces the real Philips Hue integration. The bench copies it,
with proxy.py and light.py, over the demo's stub in its own instance only.
"""

from .proxy import async_setup_entry, async_unload_entry

__all__ = ["async_setup_entry", "async_unload_entry"]
