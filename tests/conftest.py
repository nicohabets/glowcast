"""Shared test fixtures.

Import from `custom_components.glowcast` at module level in each test file: Home
Assistant's test config ships its own `custom_components` package, and the one
imported first wins.
"""

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let Home Assistant load integrations from custom_components/."""
