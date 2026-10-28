"""Shared test notes.

Import from `custom_components.glowcast` at module level in each test file: Home
Assistant's test config ships its own `custom_components` package, and the one
imported first wins.

Tests that need Home Assistant to load Glowcast as an integration request the
`enable_custom_integrations` fixture themselves; `core/` tests use plain fakes
and no Home Assistant test instance (architecture D13).
"""
