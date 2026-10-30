"""The diagnostics download: what it shows, and that it has no secrets or names.

Runs the real pipeline with the shared fakes as TV and lights; the TV's and
the bridge's devices are registered as their integrations do.
"""

import dataclasses
import json
import re
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
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.glowcast.const import DOMAIN
from custom_components.glowcast.core.models import Channel
from custom_components.glowcast.outputs import OUTPUTS
from custom_components.glowcast.sources import SOURCES

from .fakes import FakeAdapters, wait_for

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


@pytest.fixture
async def entry(
    hass: HomeAssistant, adapters: FakeAdapters, aioclient_mock: AiohttpClientMocker
) -> Any:
    """Glowcast set up for a TV and a bridge, with their devices in HA."""
    hass.config.components.update({"webostv", "hue"})
    # The lights the output claims are named after the area.
    adapters.channels = [Channel(id="1", name=f"{AREA_NAME} 1", x=0.0, z=0.0)]
    # The entertainment-area entity reads the bridge's areas (#13).
    aioclient_mock.get(
        f"https://{BRIDGE_HOST}/clip/v2/resource/entertainment_configuration",
        json={"data": [{"id": AREA_ID, "metadata": {"name": AREA_NAME}}]},
    )
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


async def sync_until_measured(entry: Any) -> None:
    """Switch on and wait until the captures per second are measured."""
    data = entry.runtime_data
    await data.async_start()
    # The first measurement takes a second.
    await wait_for(lambda: data.status and data.status.captures_per_second, limit=3)


async def test_the_download_while_syncing(
    hass: HomeAssistant, hass_client: ClientSessionGenerator, entry: Any
) -> None:
    await sync_until_measured(entry)
    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)

    assert diagnostics["versions"]["glowcast"] == "0.2.0"
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
    assert status["black_picture"] is False  # the fake TV shows red (#14)


async def test_the_download_after_a_stop_shows_the_last_status(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    entry: Any,
    adapters: FakeAdapters,
) -> None:
    await sync_until_measured(entry)
    adapters.sources[-1].on = False  # the TV reports that it is off
    await wait_for(lambda: not entry.runtime_data.is_on)
    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)

    assert diagnostics["syncing"] is False
    status = diagnostics["status"]
    assert status["phase"] == "stopped"
    assert status["stop_reason"] == "tv_off"
    assert status["channels"] == 0  # the lights are released
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
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    entry: Any,
    adapters: FakeAdapters,
) -> None:
    await sync_until_measured(entry)
    # Glowcast knows the area's name: in its area entity and its channels.
    area = hass.states.get("select.glowcast_annas_woonkamer_tv_entertainment_area")
    assert area.state == AREA_NAME
    [output] = adapters.outputs
    assert AREA_NAME in output.channels[0].name
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
    # The stored entry shows its shape, never its values.
    stored = download["data"]["entry"]["stored"]
    assert stored["data"]["bridge"] == {
        "unique_id": "str",
        "app_key": "str",
        "client_key": "str",
    }
    assert stored["options"]["output"] == {"type": "str", "area_id": "str"}
    assert download["data"]["repair_issues"] == ["area_not_found"]


async def test_a_key_a_later_type_adds_does_not_leak(
    hass: HomeAssistant, hass_client: ClientSessionGenerator, entry: Any
) -> None:
    """Nothing is redacted by key name, so an unknown key cannot slip through."""
    options = dict(entry.options)
    options["output"] = {
        **options["output"],
        "lights": [{"entity_id": "light.annas_bedroom", "name": AREA_NAME}],
    }
    hass.config_entries.async_update_entry(entry, options=options)
    await hass.async_block_till_done()
    download = await _get_diagnostics_for_config_entry(hass, hass_client, entry)
    text = json.dumps(download)
    assert "annas_bedroom" not in text
    assert AREA_NAME not in text
    assert download["data"]["entry"]["stored"]["options"]["output"]["lights"] == "list"


async def test_the_download_while_the_entry_is_not_loaded(
    hass: HomeAssistant, hass_client: ClientSessionGenerator, entry: Any
) -> None:
    await sync_until_measured(entry)
    assert await hass.config_entries.async_unload(entry.entry_id)
    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    assert diagnostics["syncing"] is False
    assert diagnostics["status"] is None
    assert diagnostics["versions"]["glowcast"] == "0.2.0"
    assert await hass.config_entries.async_setup(entry.entry_id)  # for teardown


async def test_a_source_without_a_device(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    entry: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A source type may have no device (#66)."""
    monkeypatch.setitem(
        SOURCES,
        "webos",
        dataclasses.replace(
            SOURCES["webos"],
            device_identifier=lambda data: None,
            device_id=lambda hass, data: None,
        ),
    )
    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    assert diagnostics["source"] == {"type": "webos", "device": None}
    assert diagnostics["output"]["device"] is not None


async def test_a_screen_source_shows_no_token_address_or_id(
    hass: HomeAssistant, hass_client: ClientSessionGenerator, entry: Any
) -> None:
    """The helper's token is Glowcast's own secret (#110)."""
    token = "Zq3vX8pL1mN7cR4tY9wK2bF6hJ0dS5aG"
    helper_id = "4f0c9a1b2c3d4e5f6a7b8c9d0e1f2a3b"
    host = "192.0.2.63"
    hass.config_entries.async_update_entry(
        entry,
        data={
            **entry.data,
            "source": {
                "type": "screen",
                "helper_id": helper_id,
                "host": host,
                "port": 8488,
                "token": token,
            },
        },
    )
    download = await _get_diagnostics_for_config_entry(hass, hass_client, entry)
    text = json.dumps(download)
    for value in (token, helper_id, host, "8488"):
        assert value not in text
    assert download["data"]["source"] == {"type": "screen", "device": None}


async def test_a_refused_start_does_not_show_the_previous_stop(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    entry: Any,
    adapters: FakeAdapters,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await sync_until_measured(entry)
    adapters.sources[-1].on = False
    await wait_for(lambda: not entry.runtime_data.is_on)
    monkeypatch.setitem(
        OUTPUTS,
        "hue_entertainment",
        dataclasses.replace(
            OUTPUTS["hue_entertainment"], is_missing=lambda h, d, o: True
        ),
    )
    await entry.runtime_data.async_start()  # the bridge is gone from HA
    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    assert diagnostics["status"] is None
    assert diagnostics["repair_issues"] == ["output_missing"]


@pytest.mark.parametrize(
    "identifier",
    [BRIDGE_ID.upper(), "6c0b2f7a-3d1e-4a5b-9c8d-7e6f5a4b3c2d"],
    ids=["in other case", "another identifier"],
)
async def test_the_bridge_is_found_however_hue_names_it(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    entry: Any,
    identifier: str,
) -> None:
    registry = dr.async_get(hass)
    bridge = registry.async_get(
        OUTPUTS["hue_entertainment"].device_id(hass, entry.data)
    )
    hue_entry_id = hass.config_entries.async_entry_for_domain_unique_id(
        "hue", BRIDGE_ID
    ).entry_id
    registry.async_remove_device(bridge.id)
    bridge = registry.async_get_or_create(
        config_entry_id=hue_entry_id,
        identifiers={("hue", identifier)},
        model="Hue Bridge Pro",
    )
    light = registry.async_get_or_create(
        config_entry_id=hue_entry_id,
        identifiers={("hue", "light-resource-id")},
        model="Hue gradient lightstrip",
    )
    registry.async_update_device(light.id, via_device_id=bridge.id)
    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    assert diagnostics["output"]["device"]["model"] == "Hue Bridge Pro"
