"""Setup, options and reauth flows, with a fake bridge and no network."""

import json
from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from hue_entertainment.models import EntertainmentArea
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.glowcast.const import DOMAIN
from custom_components.glowcast.outputs.hue_entertainment import _device_type

TV_ID = "tv-unique-id"
BRIDGE_ID = "bridge-unique-id"
HOST = "bridge.example"
CONFIG_URL = f"https://{HOST}/api/config"
TRANSLATIONS = Path(__file__).parent.parent / "custom_components/glowcast/translations"


class FakeHueAPI:
    """Stands in for hue_entertainment.HueEntertainmentAPI."""

    pair_results: list[Any] = []
    areas: list[EntertainmentArea] = []
    instances: list["FakeHueAPI"] = []

    def __init__(self, host: str, app_key: str | None = None) -> None:
        self.host = host
        self.app_key = app_key
        self.device_type = ""
        self.closed = False
        FakeHueAPI.instances.append(self)

    async def pair(self, device_type: str) -> dict[str, str]:
        self.device_type = device_type
        result = FakeHueAPI.pair_results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    async def get_entertainment_areas(self) -> list[EntertainmentArea]:
        return FakeHueAPI.areas

    async def close(self) -> None:
        self.closed = True


PAIRED = {"username": "new-app-key", "clientkey": "new-client-key"}


@pytest.fixture(autouse=True)
def fake_api() -> Generator[type[FakeHueAPI]]:
    """Replace the Hue library and reset the fake bridge."""
    FakeHueAPI.pair_results = [PAIRED]
    FakeHueAPI.areas = [
        EntertainmentArea(id="area-tv", name="TV area"),
        EntertainmentArea(id="area-other", name="Other area"),
    ]
    FakeHueAPI.instances = []
    with patch(
        "custom_components.glowcast.outputs.hue_entertainment.HueEntertainmentAPI",
        FakeHueAPI,
    ):
        yield FakeHueAPI


@pytest.fixture(autouse=True)
def other_integrations(hass: HomeAssistant) -> Generator[None]:
    """A loaded TV and bridge; the real integrations are never set up."""
    hass.config.components.update({"webostv", "hue"})
    tv = MockConfigEntry(
        domain="webostv",
        title="Living room TV",
        unique_id=TV_ID,
        data={"host": "tv.example", "client_secret": "tv-secret"},
        state=ConfigEntryState.LOADED,
    )
    bridge = MockConfigEntry(
        domain="hue",
        title="Hue Bridge",
        unique_id=BRIDGE_ID,
        data={"host": HOST, "api_key": "ha-key", "api_version": 2},
        state=ConfigEntryState.LOADED,
    )
    tv.add_to_hass(hass)
    bridge.add_to_hass(hass)
    yield
    # Keep Home Assistant from unloading integrations that were never set up.
    for entry in (tv, bridge):
        entry.mock_state(hass, ConfigEntryState.NOT_LOADED)


@pytest.fixture(autouse=True)
def bridge_answers(aioclient_mock: AiohttpClientMocker) -> None:
    """The bridge answers the unauthenticated request."""
    aioclient_mock.get(CONFIG_URL, json={"name": "Hue Bridge"})


def glowcast_entry(hass: HomeAssistant) -> MockConfigEntry:
    """An existing Glowcast entry with changed settings."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Living room TV",
        unique_id=TV_ID,
        version=1,
        data={
            "source": {"type": "webos", "unique_id": TV_ID},
            "bridge": {
                "unique_id": BRIDGE_ID,
                "app_key": "old-app-key",
                "client_key": "old-client-key",
            },
        },
        options={
            "output": {"type": "hue_entertainment", "area_id": "area-tv"},
            "brightness": 50,
            "intensity": 150,
            "update_rate": "low",
        },
    )
    entry.add_to_hass(hass)
    return entry


async def start_to_link(hass: HomeAssistant) -> str:
    """Pick the TV and the bridge; return the flow id at the link step."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"tv": TV_ID}
    )
    assert result["step_id"] == "bridge"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"bridge": BRIDGE_ID}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "link"
    return result["flow_id"]


async def press_and_wait(hass: HomeAssistant, flow_id: str) -> dict[str, Any]:
    """Submit the link form and return the step after the progress step."""
    result = await hass.config_entries.flow.async_configure(flow_id, {})
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    assert result["progress_action"] == "pair"
    await hass.async_block_till_done()
    return await hass.config_entries.flow.async_configure(flow_id)


async def test_happy_path(hass: HomeAssistant) -> None:
    """TV, bridge, link button, area: the entry stores section 5's data model."""
    flow_id = await start_to_link(hass)
    result = await press_and_wait(hass, flow_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "area"

    result = await hass.config_entries.flow.async_configure(
        flow_id, {"area": "area-tv"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Living room TV"
    assert result["data"] == {
        "source": {"type": "webos", "unique_id": TV_ID},
        "bridge": {
            "unique_id": BRIDGE_ID,
            "app_key": "new-app-key",
            "client_key": "new-client-key",
        },
    }
    assert result["options"] == {
        "output": {"type": "hue_entertainment", "area_id": "area-tv"},
        "brightness": 80,
        "intensity": 100,
        "update_rate": "high",
    }
    entry = result["result"]
    assert entry.unique_id == TV_ID
    assert entry.version == 1
    assert entry.state is ConfigEntryState.LOADED

    pairing, listing = FakeHueAPI.instances
    assert pairing.host == HOST
    assert pairing.device_type == "glowcast#Living room TV"
    assert listing.app_key == "new-app-key"
    assert pairing.closed
    assert listing.closed


async def test_already_configured(hass: HomeAssistant) -> None:
    """A second Glowcast entry for the same TV aborts."""
    glowcast_entry(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"tv": TV_ID}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_no_tv(hass: HomeAssistant) -> None:
    """Without a loaded webostv entry, setup says what to set up first."""
    tv = hass.config_entries.async_entry_for_domain_unique_id("webostv", TV_ID)
    tv.mock_state(hass, ConfigEntryState.NOT_LOADED)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_tv"


async def test_no_api_v2_bridge(hass: HomeAssistant) -> None:
    """A bridge that cannot stream (API v1) is not offered."""
    bridge = hass.config_entries.async_entry_for_domain_unique_id("hue", BRIDGE_ID)
    hass.config_entries.async_update_entry(
        bridge, data={**bridge.data, "api_version": 1}
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"tv": TV_ID}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_bridge"


async def test_bridge_unreachable(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An unreachable bridge is reported before pairing starts."""
    aioclient_mock.clear_requests()
    aioclient_mock.get(CONFIG_URL, exc=TimeoutError)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"tv": TV_ID}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"bridge": BRIDGE_ID}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "bridge"
    assert result["errors"] == {"base": "cannot_connect"}
    assert FakeHueAPI.instances == []


async def test_button_not_pressed(hass: HomeAssistant) -> None:
    """A failed pairing returns to the link form; the next try continues."""
    FakeHueAPI.pair_results = [TimeoutError("button"), PAIRED]
    flow_id = await start_to_link(hass)

    result = await press_and_wait(hass, flow_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "link"
    assert result["errors"] == {"base": "button_not_pressed"}
    assert FakeHueAPI.instances[0].closed

    result = await press_and_wait(hass, flow_id)
    assert result["step_id"] == "area"


async def test_no_areas(hass: HomeAssistant) -> None:
    """A bridge without entertainment areas stops setup with an explanation."""
    FakeHueAPI.areas = []
    flow_id = await start_to_link(hass)
    result = await press_and_wait(hass, flow_id)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_areas"
    assert all(api.closed for api in FakeHueAPI.instances)


async def test_reauth(hass: HomeAssistant) -> None:
    """Reauth stores the new keys and keeps every setting."""
    entry = glowcast_entry(hass)
    options = dict(entry.options)
    FakeHueAPI.pair_results = [TimeoutError("button"), PAIRED]

    result = await entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    flow_id = result["flow_id"]

    result = await press_and_wait(hass, flow_id)
    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {"base": "button_not_pressed"}

    result = await press_and_wait(hass, flow_id)
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data["bridge"] == {
        "unique_id": BRIDGE_ID,
        "app_key": "new-app-key",
        "client_key": "new-client-key",
    }
    assert entry.data["source"] == {"type": "webos", "unique_id": TV_ID}
    assert entry.options == options
    assert FakeHueAPI.instances[-1].device_type == "glowcast#Living room TV"


async def test_options_flow(hass: HomeAssistant) -> None:
    """The settings screen shows the current values and stores whole numbers."""
    entry = glowcast_entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    schema = {str(key): key for key in result["data_schema"].schema}
    assert schema["area"].default() == "area-tv"
    assert schema["brightness"].default() == 50
    assert schema["update_rate"].default() == "low"
    assert FakeHueAPI.instances[0].app_key == "old-app-key"
    assert FakeHueAPI.instances[0].closed

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "area": "area-other",
            "brightness": 30.0,
            "intensity": 0.0,
            "update_rate": "maximum",
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {
        "output": {"type": "hue_entertainment", "area_id": "area-other"},
        "brightness": 30,
        "intensity": 0,
        "update_rate": "maximum",
    }
    assert isinstance(entry.options["brightness"], int)


async def test_options_flow_rejects_out_of_range(hass: HomeAssistant) -> None:
    """Brightness stays within 1-100 % and intensity within 0-200 %."""
    entry = glowcast_entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    for bad in ({"brightness": 0}, {"intensity": 201}):
        with pytest.raises(InvalidData):
            await hass.config_entries.options.async_configure(
                result["flow_id"],
                {
                    "area": "area-tv",
                    "brightness": 80,
                    "intensity": 100,
                    "update_rate": "high",
                }
                | bad,
            )


async def test_options_flow_bridge_unreachable(hass: HomeAssistant) -> None:
    """Without the bridge the area list cannot be shown."""
    entry = glowcast_entry(hass)

    async def unreachable(self: FakeHueAPI) -> list[EntertainmentArea]:
        raise TimeoutError

    with patch.object(FakeHueAPI, "get_entertainment_areas", unreachable):
        result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"
    assert FakeHueAPI.instances[0].closed


async def test_setup_and_unload(hass: HomeAssistant) -> None:
    """The entry loads and unloads."""
    entry = glowcast_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.LOADED
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


def test_device_type_fits_the_bridge() -> None:
    """The bridge allows 20 characters before the # and 19 after."""
    device_type = _device_type("A very long television name #2")
    app, name = device_type.split("#")
    assert app == "glowcast"
    assert len(name) <= 19
    assert name == "A very long televis"


def test_translations_match() -> None:
    """Dutch has exactly the same keys as English."""

    def keys(node: Any, prefix: str = "") -> set[str]:
        if not isinstance(node, dict):
            return {prefix}
        return set().union(*(keys(v, f"{prefix}/{k}") for k, v in node.items()))

    english = json.loads((TRANSLATIONS / "en.json").read_text())
    dutch = json.loads((TRANSLATIONS / "nl.json").read_text())
    assert keys(english) == keys(dutch)
