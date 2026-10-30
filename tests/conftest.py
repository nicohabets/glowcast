"""Shared test notes and fixtures.

Import from `custom_components.glowcast` at module level in each test file: Home
Assistant's test config ships its own `custom_components` package, and the one
imported first wins.

Tests that need Home Assistant to load Glowcast as an integration request the
`enable_custom_integrations` fixture themselves; `core/` tests use plain fakes
and no Home Assistant test instance (architecture D13).
"""

import dataclasses
from collections.abc import Generator

import pytest
from homeassistant.core import HomeAssistant

from custom_components.glowcast import runtime
from custom_components.glowcast.core import light_state
from custom_components.glowcast.outputs import OUTPUTS
from custom_components.glowcast.sources import SOURCES

from .fakes import FakeAdapters, FakePipeline


@pytest.fixture
def no_fade_in(monkeypatch: pytest.MonkeyPatch) -> None:
    """The first colours after a claim at once, without the fade-in (#153).

    For tests about something else that look at the first colours; the
    fade-in has tests of its own (test_light_state.py, test_pipeline.py).
    """
    monkeypatch.setattr(light_state, "FADE_IN", 0.0)


@pytest.fixture
def adapters(monkeypatch: pytest.MonkeyPatch) -> FakeAdapters:
    """Fake TV and lights; the real adapters are never built.

    The types keep their device lookups, entities and flows.
    """
    fakes = FakeAdapters()
    monkeypatch.setitem(
        SOURCES,
        "webos",
        dataclasses.replace(
            SOURCES["webos"],
            create=lambda hass, data, options: fakes.source(),
            is_missing=lambda hass, data, options: fakes.source_missing,
        ),
    )
    monkeypatch.setitem(
        OUTPUTS,
        "hue_entertainment",
        dataclasses.replace(
            OUTPUTS["hue_entertainment"],
            create=lambda hass, data, options: fakes.output(),
            is_missing=lambda hass, data, options: fakes.output_missing,
        ),
    )
    return fakes


@pytest.fixture
def fakes(
    hass: HomeAssistant, adapters: FakeAdapters, monkeypatch: pytest.MonkeyPatch
) -> Generator[FakeAdapters]:
    """Fake TV and lights, and a fake pipeline."""
    FakePipeline.instances = []
    FakePipeline.start_error = None
    monkeypatch.setattr(runtime, "Pipeline", FakePipeline)
    yield adapters
    # Before Home Assistant's teardown (this fixture needs `hass`): a pipeline
    # the runtime lost track of would make the unload wait for it forever,
    # hiding the test's own failure.
    for pipeline in FakePipeline.instances:
        pipeline.stop()
