"""Bench stub for Home Assistant's matter integration: proxy lights of a
Thread network when the entry's data has them (proxy.py).

Only for the test bench in scripts/bench/. Never install this on a real Home
Assistant: it replaces the real Matter integration. The bench copies it, with
proxy.py, into its own instance only.
"""

from .proxy import async_setup_entry, async_unload_entry

__all__ = ["async_setup_entry", "async_unload_entry"]
