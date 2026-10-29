"""The diagnostics download: what it shows, and that it has no secrets or names.

Runs the real pipeline with the shared fakes as TV and lights; the TV's and
the bridge's devices are registered as their integrations do.
"""

import asyncio
import dataclasses
import json
import re
from collections.abc import Callable
from typing import Any

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    _get_diagnostics_for_config_entry,
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.glowcast.const import DOMAIN
from custom_components.glowcast.core.pipeline import MonotonicClock
from custom_components.glowcast.outputs import OUTPUTS
from custom_components.glowcast.sources import SOURCES

from .fakes import FakeOutput, FakeSource

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

# Realistic secret and personal values; none of them may be in the download.
TV_ID = "7d9c4f3a-1b2e-4c5d-8e6f-0a1b2c3d4e5f"
TV_NAME = "Annas woonkamer TV"
TV_HOST = "192.168.178.42"
TV_CLIENT_SECRET = "a3f1c9e07b2d4e5f8a6b1c2d3e4f5a6b"
TV_SERIAL = "305KRAB1C234"
BRIDGE_ID = "ecb5fafffe0a1b2c"
BRIDGE_HOST = "192.168.178.57"
BRIDGE_API_KEY = "Xz7pQ2mL9vR4tY8wK3nB6cF1hJ5dS0aG-eUoIi"
BRIDGE_SERIAL = "C42B1A0B2C3D"
APP_KEY = "kq9VbN2xM7cL4pR8tW1yZ6sD3fG0hJ5aE-uIoPq"
CLIENT_KEY = "0F1E2D3C4B5A69788796A5B4C3D2E1F0"
AREA_ID = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
AREA_NAME = "Woonkamer bank"
SECRETS = [
    TV_ID,
    TV_NAME,
    TV_HOST,
    TV_CLIENT_SECRET,
    TV_SERIAL,
    BRIDGE_ID,
    BRIDGE_HOST,
    BRIDGE_API_KEY,
    BRIDGE_SERIAL,
    APP_KEY,
    CLIENT_KEY,
    AREA_ID,
    AREA_NAME,
]


class Lights:
    """The fake TV and lights the registries build, on the real clock."""

    def __init__(self) -> None:
        self.log: list[str] = []
        self.sources: list[FakeSource] = []

    def source(self) -> FakeSource:
        source = FakeSource(MonotonicClock(), self.log, capture_time=0)
        self.sources.append(source)
        return source


@pytest.fixture
def lights(monkeypatch: pytest.MonkeyPatch) -> Lights:
    """Fake source and output; the device lookups stay real."""
    lights = Lights()
    monkeypatch.setitem(
        SOURCES,
        "webos",
        dataclasses.replace(
            SOURCES["webos"],
            create=lambda hass, data, options: lights.source(),
            is_missing=lambda hass, data: False,
        ),
    )
    monkeypatch.setitem(
        OUTPUTS,
        "hue_entertainment",
        dataclasses.replace(
            OUTPUTS["hue_entertainment"],
            create=lambda hass, data, options: FakeOutput(MonotonicClock(), lights.log),
            is_missing=lambda hass, data: False,
        ),
    )
    return lights


@pytest.fixture
async def entry(hass: HomeAssistant, lights: Lights) -> Any:
    """Glowcast set up for a TV and a bridge, with their devices in HA."""
    hass.config.components.update({"webostv", "hue"})
    registry = dr.async_get(hass)
    others = []
    for domain, unique_id, data, device in (
        (
            "webostv",
            TV_ID,
            {"host": TV_HOST, "client_secret": TV_CLIENT_SECRET},
            {
                "manufacturer": "LG",
                "model": "OLED65G36LA",
                "sw_version": "23.20.67",
                "serial_number": TV_SERIAL,
                "name": TV_NAME,
            },
        ),
        (
            "hue",
            BRIDGE_ID,
            {"host": BRIDGE_HOST, "api_key": BRIDGE_API_KEY, "api_version": 2},
            {
                "manufacturer": "Signify Netherlands B.V.",
                "model": "Hue Bridge Pro",
                "sw_version": "1.72.2072152000",
                "serial_number": BRIDGE_SERIAL,
                "name": "Hue Bridge Pro",
            },
        ),
    ):
        other = MockConfigEntry(
            domain=domain,
            title=TV_NAME if domain == "webostv" else "Hue Bridge Pro",
            unique_id=unique_id,
            data=data,
            state=ConfigEntryState.LOADED,
        )
        other.add_to_hass(hass)
        others.append(other)
        registry.async_get_or_create(
            config_entry_id=other.entry_id,
            identifiers={(domain, unique_id)},
            **device,
        )
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=TV_NAME,
        unique_id=TV_ID,
        data={
            "source": {"type": "webos", "unique_id": TV_ID},
            "bridge": {
                "unique_id": BRIDGE_ID,
                "app_key": APP_KEY,
                "client_key": CLIENT_KEY,
            },
        },
        options={
            "output": {"type": "hue_entertainment", "area_id": AREA_ID},
            "brightness": 60,
            "intensity": 120,
            "update_rate": "maximum",
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    yield entry
    await entry.runtime_data.async_stop()
    for other in others:
        other.mock_state(hass, ConfigEntryState.NOT_LOADED)


async def wait_for(condition: Callable[[], Any], limit: float = 3.0) -> None:
    """Wait until `condition()` holds; the real pipeline runs on the real clock."""
    for _ in range(round(limit / 0.01)):
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"not reached within {limit} s")


async def sync_until_measured(entry: Any) -> None:
    """Switch on and wait until the captures per second are measured."""
    data = entry.runtime_data
    await data.async_start()
    await wait_for(lambda: data.status and data.status.captures_per_second)


async def test_the_download_while_syncing(
    hass: HomeAssistant, hass_client: ClientSessionGenerator, entry: Any
) -> None:
    await sync_until_measured(entry)
    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)

    assert diagnostics["versions"]["glowcast"] == "0.1.1"
    assert diagnostics["versions"]["requirements"] == {"hue-entertainment": "0.1.2"}
    assert diagnostics["settings"] == {
        "brightness": 60,
        "intensity": 120,
        "update_rate": "maximum",
    }
    assert diagnostics["source"] == {
        "type": "webos",
        "device": {
            "manufacturer": "LG",
            "model": "OLED65G36LA",
            "sw_version": "23.20.67",
        },
    }
    assert diagnostics["output"] == {
        "type": "hue_entertainment",
        "device": {
            "manufacturer": "Signify Netherlands B.V.",
            "model": "Hue Bridge Pro",
            "sw_version": "1.72.2072152000",
        },
    }
    assert diagnostics["syncing"] is True
    status = diagnostics["status"]
    assert status["phase"] == "streaming"
    assert status["channels"] == 1
    assert status["stop_reason"] is None
    assert status["captures_per_second"] > 0


async def test_the_download_after_a_stop_shows_the_last_status(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    entry: Any,
    lights: Lights,
) -> None:
    await sync_until_measured(entry)
    lights.sources[-1].on = False  # the TV reports that it is off
    await wait_for(lambda: not entry.runtime_data.is_on)
    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)

    assert diagnostics["syncing"] is False
    status = diagnostics["status"]
    assert status["phase"] == "stopped"
    assert status["stop_reason"] == "tv_off"
    assert status["channels"] == 1
    assert status["captures_per_second"] > 0  # the last measurement


async def test_the_download_before_the_first_start(
    hass: HomeAssistant, hass_client: ClientSessionGenerator, entry: Any
) -> None:
    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    assert diagnostics["syncing"] is False
    assert diagnostics["status"] is None


async def test_a_device_that_is_not_in_home_assistant(
    hass: HomeAssistant, hass_client: ClientSessionGenerator, entry: Any
) -> None:
    registry = dr.async_get(hass)
    registry.async_remove_device(
        OUTPUTS["hue_entertainment"].device_id(hass, entry.data)
    )
    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    assert diagnostics["output"] == {"type": "hue_entertainment", "device": None}


async def test_no_secrets_or_names_anywhere_in_the_download(
    hass: HomeAssistant, hass_client: ClientSessionGenerator, entry: Any
) -> None:
    await sync_until_measured(entry)
    # Newer Home Assistant adds the integration's repair issues to the
    # download; their text has the TV's name as a placeholder.
    entry.runtime_data._raise_issue("area_not_found")
    download = await _get_diagnostics_for_config_entry(hass, hass_client, entry)
    if "issues" in download:  # not on 2025.7.2
        assert len(download["issues"]) == 1
    text = json.dumps(download)
    for value in SECRETS:
        assert value.lower() not in text.lower()
    # Glowcast's part; Home Assistant's system info has version numbers that
    # look like addresses (the kernel's, for example).
    assert not re.search(r"\b\d{1,3}(\.\d{1,3}){3}\b", json.dumps(download["data"]))
    entry_data = download["data"]["entry"]
    assert entry_data["title"] == "**REDACTED**"
    assert entry_data["data"]["bridge"] == {
        "unique_id": "**REDACTED**",
        "app_key": "**REDACTED**",
        "client_key": "**REDACTED**",
    }
    assert entry_data["options"]["output"]["area_id"] == "**REDACTED**"
