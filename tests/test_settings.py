"""Reading the settings from the options and writing them back."""

import logging

import pytest

from custom_components.glowcast import settings as settings_module
from custom_components.glowcast.const import (
    DEFAULT_BRIGHTNESS,
    DEFAULT_INTENSITY,
    DEFAULT_LIVELINESS,
    DEFAULT_UPDATE_RATE,
    LIVELINESS_LEVELS,
    UPDATE_RATES,
)
from custom_components.glowcast.core.liveliness import Liveliness
from custom_components.glowcast.core.pipeline import Settings, UpdateRate
from custom_components.glowcast.settings import read_settings, write_settings

STORED = {
    "brightness": 60,
    "intensity": 120,
    "update_rate": "medium",
    "liveliness": "lively",
}


def test_read_and_write_round_trip() -> None:
    settings = read_settings(STORED)
    assert settings == Settings(60, 120, UpdateRate.MEDIUM, Liveliness.LIVELY)
    assert write_settings(settings) == STORED
    assert read_settings(write_settings(settings)) == settings


def test_other_options_are_not_settings() -> None:
    options = {"output": {"type": "hue_entertainment", "area_id": "area"}, **STORED}
    assert read_settings(options) == read_settings(STORED)
    assert write_settings(read_settings(options)) == STORED


def test_missing_keys_get_their_defaults() -> None:
    assert read_settings({}) == Settings(
        DEFAULT_BRIGHTNESS,
        DEFAULT_INTENSITY,
        UpdateRate(DEFAULT_UPDATE_RATE),
        Liveliness(DEFAULT_LIVELINESS),
    )


def test_an_entry_from_before_the_liveliness_is_normal() -> None:
    """No migration (#16): without the key the level is Normal, v0.4."""
    old = {"brightness": 60, "intensity": 120, "update_rate": "medium"}
    assert read_settings(old).liveliness is Liveliness.NORMAL
    assert write_settings(read_settings(old))["liveliness"] == "normal"


def test_an_unknown_liveliness_gets_the_default(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings_module, "_warned", set())
    with caplog.at_level(logging.WARNING):
        settings = read_settings({**STORED, "liveliness": "wild"})
        read_settings({**STORED, "liveliness": "wild"})
    assert settings.liveliness is Liveliness.NORMAL
    assert caplog.text.count("wild") == 1


def test_an_unknown_update_rate_gets_the_default(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """For example a rate renamed or removed by a later version."""
    monkeypatch.setattr(settings_module, "_warned", set())
    with caplog.at_level(logging.WARNING):
        settings = read_settings({**STORED, "update_rate": "turbo"})
        # Read again at every switch-on and restart: warned only once.
        read_settings({**STORED, "update_rate": "turbo"})
    assert settings == Settings(
        60, 120, UpdateRate(DEFAULT_UPDATE_RATE), Liveliness.LIVELY
    )
    assert caplog.text.count("turbo") == 1


def test_the_form_s_floats_are_stored_as_whole_numbers() -> None:
    stored = write_settings(
        read_settings({"brightness": 30.0, "intensity": 0.0, "update_rate": "low"})
    )
    assert stored == {
        "brightness": 30,
        "intensity": 0,
        "update_rate": "low",
        "liveliness": "normal",
    }
    assert all(type(stored[key]) is int for key in ("brightness", "intensity"))
    assert type(stored["update_rate"]) is str
    assert type(stored["liveliness"]) is str


def test_the_update_rates_offered_come_from_the_enum() -> None:
    """In the order of the options screen's dropdown, as before."""
    assert UPDATE_RATES == ["low", "medium", "high", "maximum"]
    assert all(UpdateRate(rate) for rate in UPDATE_RATES)


def test_the_levels_offered_come_from_the_enum() -> None:
    """Calm, Normal, Lively, in the order of the dropdown (#16)."""
    assert LIVELINESS_LEVELS == ["calm", "normal", "lively"]
    assert all(Liveliness(level) for level in LIVELINESS_LEVELS)
