"""Setup, options and reauth flows, with a fake bridge and no network."""

import asyncio
import dataclasses
import json
from collections.abc import Generator
from ipaddress import ip_address
from pathlib import Path
from typing import Any
from unittest.mock import patch

import aiohttp
import pytest
from homeassistant.config_entries import SOURCE_USER, SOURCE_ZEROCONF, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowHandler, FlowResultType, InvalidData
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)
from yarl import URL

from custom_components.glowcast.const import DOMAIN, REAUTH_PART, REAUTH_SOURCE
from custom_components.glowcast.outputs.hue_entertainment import _device_type
from custom_components.glowcast.sources import SOURCES

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

TV_ID = "tv-unique-id"
BRIDGE_ID = "bridge-unique-id"
HOST = "bridge.example"
CONFIG_URL = f"https://{HOST}/api/config"
AREAS_URL = f"https://{HOST}/clip/v2/resource/entertainment_configuration"
TRANSLATIONS = Path(__file__).parent.parent / "custom_components/glowcast/translations"


# A pair() result that waits for the link button (a few seconds, so a test that
# fails does not hang) unless cancelled.
WAIT_FOR_BUTTON = object()


class FakeHueAPI:
    """Stands in for hue_entertainment.HueEntertainmentAPI (pairing only)."""

    pair_results: list[Any] = []
    instances: list["FakeHueAPI"] = []

    def __init__(self, host: str, app_key: str | None = None) -> None:
        self.host = host
        self.app_key = app_key
        self.device_type = ""
        self.closed = False
        self.closed_event = asyncio.Event()
        self.cancelled = False
        FakeHueAPI.instances.append(self)

    async def pair(self, device_type: str) -> dict[str, str]:
        self.device_type = device_type
        result = FakeHueAPI.pair_results.pop(0)
        if result is WAIT_FOR_BUTTON:
            try:
                await asyncio.sleep(3)
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        if isinstance(result, Exception):
            raise result
        return result

    async def close(self) -> None:
        self.closed = True
        self.closed_event.set()


PAIRED = {"username": "new-app-key", "clientkey": "new-client-key"}


def area(area_id: str, name: str) -> dict[str, Any]:
    """An entertainment_configuration resource as the bridge returns it."""
    return {
        "id": area_id,
        "type": "entertainment_configuration",
        "metadata": {"name": name},
    }


class FakeBridge:
    """The bridge's HTTP side: the unauthenticated check and the area list."""

    def __init__(self) -> None:
        self.reachable = True
        self.areas = [area("area-tv", "TV area"), area("area-other", "Other area")]
        self.areas_status = 200

    async def config(self, method: str, url: URL, data: Any) -> Any:
        if not self.reachable:
            raise aiohttp.ClientConnectionError
        return AiohttpClientMockResponse(method, url, json={"name": "Hue Bridge"})

    async def area_list(self, method: str, url: URL, data: Any) -> Any:
        if not self.reachable:
            raise aiohttp.ClientConnectionError
        return AiohttpClientMockResponse(
            method, url, status=self.areas_status, json={"data": self.areas}
        )


@pytest.fixture(autouse=True)
def bridge(aioclient_mock: AiohttpClientMocker) -> FakeBridge:
    """A bridge that answers; tests change its state."""
    fake = FakeBridge()
    aioclient_mock.get(CONFIG_URL, side_effect=fake.config)
    aioclient_mock.get(AREAS_URL, side_effect=fake.area_list)
    return fake


def area_list_keys(aioclient_mock: AiohttpClientMocker) -> list[str]:
    """The application keys the area list was requested with."""
    return [
        headers["hue-application-key"]
        for _, url, _, headers in aioclient_mock.mock_calls
        if str(url) == AREAS_URL
    ]


@pytest.fixture(autouse=True)
def fake_api() -> Generator[type[FakeHueAPI]]:
    """Replace the Hue library's pairing."""
    FakeHueAPI.pair_results = [PAIRED]
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


def glowcast_entry(
    hass: HomeAssistant, settings: dict[str, Any] | None = None
) -> MockConfigEntry:
    """An existing Glowcast entry with changed settings (or the given ones)."""
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
            **(
                {"brightness": 50, "intensity": 150, "update_rate": "low"}
                if settings is None
                else settings
            ),
        },
    )
    entry.add_to_hass(hass)
    return entry


async def start_tv(hass: HomeAssistant) -> dict[str, Any]:
    """Start the setup flow and choose the TV in the menu of source types."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "user"
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "tv"}
    )


async def start_to_link(hass: HomeAssistant) -> str:
    """Pick the TV and the bridge; return the flow id at the link step."""
    result = await start_tv(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "tv"
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


async def test_happy_path(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
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

    (pairing,) = FakeHueAPI.instances
    assert pairing.host == HOST
    assert pairing.device_type == "glowcast#Living room TV"
    assert pairing.closed
    # Read by the flow, then by the entertainment-area entity (#13).
    assert area_list_keys(aioclient_mock) == ["new-app-key", "new-app-key"]


async def test_already_configured(hass: HomeAssistant) -> None:
    """A TV with a Glowcast entry is not offered; with none left, setup stops."""
    glowcast_entry(hass)
    result = await start_tv(hass)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "all_tvs_configured"


async def test_configured_tv_not_offered(hass: HomeAssistant) -> None:
    """Only TVs without a Glowcast entry are in the list."""
    glowcast_entry(hass)
    other = MockConfigEntry(
        domain="webostv",
        title="Bedroom TV",
        unique_id="other-tv",
        data={"host": "tv2.example", "client_secret": "tv-secret"},
        state=ConfigEntryState.LOADED,
    )
    other.add_to_hass(hass)
    result = await start_tv(hass)

    options = result["data_schema"].schema["tv"].config["options"]
    assert [option["value"] for option in options] == ["other-tv"]
    other.mock_state(hass, ConfigEntryState.NOT_LOADED)


async def test_same_tv_in_two_setups(hass: HomeAssistant) -> None:
    """A second setup for a TV that is already being set up stops."""
    first = await start_tv(hass)
    second = await start_tv(hass)
    await hass.config_entries.flow.async_configure(first["flow_id"], {"tv": TV_ID})
    result = await hass.config_entries.flow.async_configure(
        second["flow_id"], {"tv": TV_ID}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_in_progress"


async def test_no_tv(hass: HomeAssistant) -> None:
    """Without a loaded webostv entry, setup says what to set up first."""
    tv = hass.config_entries.async_entry_for_domain_unique_id("webostv", TV_ID)
    tv.mock_state(hass, ConfigEntryState.NOT_LOADED)
    result = await start_tv(hass)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_tv"


async def test_no_api_v2_bridge(hass: HomeAssistant) -> None:
    """A bridge that cannot stream (API v1) is not offered."""
    bridge = hass.config_entries.async_entry_for_domain_unique_id("hue", BRIDGE_ID)
    hass.config_entries.async_update_entry(
        bridge, data={**bridge.data, "api_version": 1}
    )
    result = await start_tv(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"tv": TV_ID}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_bridge"


async def test_bridge_unreachable(hass: HomeAssistant, bridge: FakeBridge) -> None:
    """An unreachable bridge is reported before pairing starts."""
    bridge.reachable = False
    result = await start_tv(hass)
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


async def test_bridge_drops_out_during_pairing(
    hass: HomeAssistant, bridge: FakeBridge
) -> None:
    """A bridge gone during pairing says so, not "button not pressed".

    The library retries network errors until its deadline and then raises the
    same timeout as for an unpressed button, so the flow checks the bridge.
    """
    FakeHueAPI.pair_results = [TimeoutError("Cannot connect"), PAIRED]
    flow_id = await start_to_link(hass)
    bridge.reachable = False

    result = await press_and_wait(hass, flow_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "link"
    assert result["errors"] == {"base": "cannot_connect"}
    assert FakeHueAPI.instances[0].closed

    bridge.reachable = True
    result = await press_and_wait(hass, flow_id)
    assert result["step_id"] == "area"


async def test_malformed_pairing_reply(hass: HomeAssistant) -> None:
    """A reply that is not JSON does not crash the flow."""
    FakeHueAPI.pair_results = [ValueError("not JSON"), PAIRED]
    flow_id = await start_to_link(hass)

    result = await press_and_wait(hass, flow_id)
    assert result["step_id"] == "link"
    assert result["errors"] == {"base": "cannot_connect"}

    result = await press_and_wait(hass, flow_id)
    assert result["step_id"] == "area"


async def test_closing_the_flow_stops_pairing(hass: HomeAssistant) -> None:
    """Closing setup during the progress step cancels the pairing.

    Home Assistant also cancels a flow's progress task; that is switched off
    here so the test covers the flow's own async_remove().
    """
    FakeHueAPI.pair_results = [WAIT_FOR_BUTTON]
    flow_id = await start_to_link(hass)
    result = await hass.config_entries.flow.async_configure(flow_id, {})
    assert result["type"] is FlowResultType.SHOW_PROGRESS

    with patch.object(FlowHandler, "async_cancel_progress_task", lambda self: None):
        hass.config_entries.flow.async_abort(flow_id)
    pairing = FakeHueAPI.instances[0]
    async with asyncio.timeout(1):
        await pairing.closed_event.wait()

    assert pairing.cancelled
    assert pairing.closed
    assert hass.config_entries.async_entries(DOMAIN) == []


async def test_no_areas(hass: HomeAssistant, bridge: FakeBridge) -> None:
    """Without areas the screen explains what to do; Submit looks again."""
    bridge.areas = []
    flow_id = await start_to_link(hass)
    result = await press_and_wait(hass, flow_id)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "area"
    assert result["errors"] == {"base": "no_areas"}

    bridge.areas = [area("area-new", "New area")]
    result = await hass.config_entries.flow.async_configure(flow_id, {})
    assert result["step_id"] == "area"
    assert not result["errors"]
    result = await hass.config_entries.flow.async_configure(
        flow_id, {"area": "area-new"}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"]["output"]["area_id"] == "area-new"
    assert result["data"]["bridge"]["app_key"] == "new-app-key"
    # Paired once: the retry did not create another app on the bridge.
    assert len(FakeHueAPI.instances) == 1


async def test_area_list_fails_after_pairing(
    hass: HomeAssistant, bridge: FakeBridge
) -> None:
    """A failed area list keeps the pairing; Submit tries again."""
    flow_id = await start_to_link(hass)
    bridge.areas_status = 503
    result = await press_and_wait(hass, flow_id)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "area"
    assert result["errors"] == {"base": "cannot_connect"}

    bridge.areas_status = 200
    result = await hass.config_entries.flow.async_configure(flow_id, {})
    assert result["step_id"] == "area"
    result = await hass.config_entries.flow.async_configure(
        flow_id, {"area": "area-tv"}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert len(FakeHueAPI.instances) == 1


async def test_reauth_for_the_source_runs_its_own_step(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A source that rejects Glowcast's key never leads to the bridge's
    pairing, which would add another app on the bridge (#110)."""
    entry = glowcast_entry(hass)
    result = await entry.start_reauth_flow(hass, data={REAUTH_PART: REAUTH_SOURCE})
    # The webOS source has no reauth step.
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_unavailable"
    assert FakeHueAPI.instances == []

    monkeypatch.setitem(
        SOURCES, "webos", dataclasses.replace(SOURCES["webos"], reauth_step="tv")
    )
    result = await entry.start_reauth_flow(hass, data={REAUTH_PART: REAUTH_SOURCE})
    # The source's step ran (this entry's TV is the only one), not the bridge's.
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "all_tvs_configured"
    assert FakeHueAPI.instances == []


async def test_the_first_screen_offers_each_source_type(hass: HomeAssistant) -> None:
    """A menu with each type's first step: the TV, then the computer screen."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "user"
    assert list(result["menu_options"]) == ["tv", "screen"]


async def test_a_source_without_setup_steps_is_not_offered(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With one type left that has setup steps, its first step is the first
    screen, without a menu."""
    monkeypatch.setitem(
        SOURCES, "screen", dataclasses.replace(SOURCES["screen"], setup_step=None)
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "tv"
    assert list(result["data_schema"].schema) == ["tv"]


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


async def test_options_flow(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The settings screen shows the current values and stores whole numbers."""
    entry = glowcast_entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    schema = {str(key): key for key in result["data_schema"].schema}
    assert schema["area"].default() == "area-tv"
    assert schema["brightness"].default() == 50
    assert schema["update_rate"].default() == "low"
    assert area_list_keys(aioclient_mock) == ["old-app-key"]

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


async def test_options_flow_of_an_older_entry(hass: HomeAssistant) -> None:
    """Missing settings and an unknown update rate show their defaults."""
    entry = glowcast_entry(hass, {"brightness": 50, "update_rate": "turbo"})
    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] is FlowResultType.FORM
    schema = {str(key): key for key in result["data_schema"].schema}
    assert schema["brightness"].default() == 50
    assert schema["intensity"].default() == 100
    assert schema["update_rate"].default() == "high"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"area": "area-tv", "brightness": 50, "intensity": 100, "update_rate": "high"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["intensity"] == 100
    assert entry.options["update_rate"] == "high"


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


async def test_options_flow_key_revoked(
    hass: HomeAssistant, bridge: FakeBridge
) -> None:
    """A revoked key says so and starts the reauth flow."""
    entry = glowcast_entry(hass)
    bridge.areas_status = 403
    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_required"
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]
    assert flows[0]["context"]["entry_id"] == entry.entry_id


async def test_options_flow_bridge_unreachable(
    hass: HomeAssistant, bridge: FakeBridge
) -> None:
    """Without the bridge the area list cannot be shown."""
    entry = glowcast_entry(hass)
    bridge.reachable = False
    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


async def test_setup_and_unload(hass: HomeAssistant) -> None:
    """The entry loads and unloads."""
    entry = glowcast_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.LOADED
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_reauth_of_a_loaded_entry_does_not_reload_it(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """The entry has an update listener (#7): reauth only stores the keys."""
    entry = glowcast_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    FakeHueAPI.pair_results = [PAIRED]

    result = await entry.start_reauth_flow(hass)
    result = await press_and_wait(hass, result["flow_id"])
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data["bridge"]["app_key"] == "new-app-key"
    assert entry.state is ConfigEntryState.LOADED
    assert "should use it for scheduling a reload" not in caplog.text
    assert await hass.config_entries.async_unload(entry.entry_id)


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


# The screen source's steps (#109): discovery, the address, the pairing code.

HELPER_ID = "4f0c9a1b2c3d4e5f6a7b8c9d0e1f2a3b"
PC_HOST = "192.0.2.10"
PC_TOKEN = "Zq3vX8pL1mN7cR4tY9wK2bF6hJ0dS5aG"
CODE = "123456"
PAIR_URL = f"http://{PC_HOST}:8488/v1/pair"
INFO_URL = f"http://{PC_HOST}:8488/v1/info"


class FakeHelperHTTP:
    """The screen helper's HTTP side (protocol 1): pairing and its info."""

    def __init__(self) -> None:
        self.reachable = True
        # None: the helper has no code (--non-interactive).
        self.code: str | None = CODE
        self.helper_id = HELPER_ID
        self.protocol = 1
        self.pair_requests = 0

    async def pair(self, method: str, url: URL, data: Any) -> Any:
        self.pair_requests += 1
        if not self.reachable:
            raise aiohttp.ClientConnectionError
        if self.code is None:
            return self._json(method, url, 403, {"error": "pairing_unavailable"})
        if data != {"code": self.code}:
            return self._json(method, url, 403, {"error": "wrong_code"})
        return self._json(method, url, 200, {"token": PC_TOKEN, "id": self.helper_id})

    async def info(self, method: str, url: URL, data: Any) -> Any:
        if not self.reachable:
            raise aiohttp.ClientConnectionError
        return self._json(
            method,
            url,
            200,
            {"id": self.helper_id, "name": "Test PC", "protocol": self.protocol},
        )

    @staticmethod
    def _json(method: str, url: URL, status: int, body: Any) -> Any:
        return AiohttpClientMockResponse(method, url, status=status, json=body)


@pytest.fixture
def screen_helper(aioclient_mock: AiohttpClientMocker) -> FakeHelperHTTP:
    """A helper at PC_HOST that answers; tests change its state."""
    fake = FakeHelperHTTP()
    aioclient_mock.post(PAIR_URL, side_effect=fake.pair)
    aioclient_mock.get(INFO_URL, side_effect=fake.info)
    return fake


def discovery(host: str = PC_HOST, **properties: str) -> ZeroconfServiceInfo:
    """What Home Assistant's zeroconf discovery hands the flow for a helper."""
    return ZeroconfServiceInfo(
        ip_address=ip_address(host),
        ip_addresses=[ip_address(host)],
        port=8488,
        hostname="test-pc.local.",
        type="_glowcast._tcp.local.",
        name="Test PC (4f0c9a)._glowcast._tcp.local.",
        properties={"id": HELPER_ID, "name": "Test PC", "protocol": "1"} | properties,
    )


def screen_entry(hass: HomeAssistant, host: str = PC_HOST) -> MockConfigEntry:
    """An existing Glowcast entry for the helper, with changed settings."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test PC",
        unique_id=HELPER_ID,
        version=1,
        data={
            "source": {
                "type": "screen",
                "helper_id": HELPER_ID,
                "host": host,
                "port": 8488,
                "token": "old-token",
            },
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


async def discover(hass: HomeAssistant, info: ZeroconfServiceInfo) -> dict[str, Any]:
    """Let Home Assistant's discovery start the flow."""
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=info
    )


async def code(hass: HomeAssistant, flow_id: str, value: str) -> dict[str, Any]:
    """Enter a pairing code."""
    return await hass.config_entries.flow.async_configure(flow_id, {"code": value})


async def through_the_output(hass: HomeAssistant, result: dict[str, Any]) -> Any:
    """From the bridge step to the created entry, as for a TV."""
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "bridge"
    flow_id = result["flow_id"]
    result = await hass.config_entries.flow.async_configure(
        flow_id, {"bridge": BRIDGE_ID}
    )
    assert result["step_id"] == "link"
    result = await press_and_wait(hass, flow_id)
    assert result["step_id"] == "area"
    result = await hass.config_entries.flow.async_configure(
        flow_id, {"area": "area-tv"}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    return result


SCREEN_SOURCE = {
    "type": "screen",
    "helper_id": HELPER_ID,
    "host": PC_HOST,
    "port": 8488,
    "token": PC_TOKEN,
}


async def test_a_discovered_helper_is_set_up_with_its_code(
    hass: HomeAssistant,
    screen_helper: FakeHelperHTTP,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Discovered: Glowcast - Test PC; Add asks the code, then the output."""
    result = await discover(hass, discovery())
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "screen_code"
    assert result["description_placeholders"] == {"computer": "Test PC"}
    [flow] = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert flow["context"]["title_placeholders"] == {"name": "Test PC"}
    assert flow["context"]["unique_id"] == HELPER_ID
    flow_id = result["flow_id"]

    # A wrong code says so, and the same form lets the user try again.
    result = await code(hass, flow_id, "654321")
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "screen_code"
    assert result["errors"] == {"base": "screen_wrong_code"}

    result = await through_the_output(hass, await code(hass, flow_id, f" {CODE} "))
    assert result["title"] == "Test PC"
    assert result["data"] == {
        "source": SCREEN_SOURCE,
        "bridge": {
            "unique_id": BRIDGE_ID,
            "app_key": "new-app-key",
            "client_key": "new-client-key",
        },
    }
    assert result["options"]["output"] == {
        "type": "hue_entertainment",
        "area_id": "area-tv",
    }
    assert result["result"].unique_id == HELPER_ID
    assert FakeHueAPI.instances[0].device_type == "glowcast#Test PC"
    # The info was asked with the new token.
    [info_headers] = [
        headers
        for _, url, _, headers in aioclient_mock.mock_calls
        if str(url) == INFO_URL
    ]
    assert info_headers == {"Authorization": f"Bearer {PC_TOKEN}"}


async def test_a_code_that_is_not_6_digits_is_not_sent(
    hass: HomeAssistant, screen_helper: FakeHelperHTTP
) -> None:
    result = await discover(hass, discovery())
    result = await code(hass, result["flow_id"], "12 34")
    assert result["errors"] == {"base": "screen_wrong_code"}
    assert screen_helper.pair_requests == 0


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"reachable": False}, "screen_cannot_connect"),
        ({"code": None}, "screen_pairing_unavailable"),
        ({"helper_id": "another-helper"}, "screen_other_helper"),
        ({"protocol": 2}, "screen_unsupported"),
    ],
)
async def test_pairing_problems_keep_the_form_open(
    hass: HomeAssistant,
    screen_helper: FakeHelperHTTP,
    change: dict[str, Any],
    error: str,
) -> None:
    """Each problem has its own text; the next try with a good helper works."""
    result = await discover(hass, discovery())
    flow_id = result["flow_id"]
    for name, value in change.items():
        setattr(screen_helper, name, value)
    result = await code(hass, flow_id, CODE)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "screen_code"
    assert result["errors"] == {"base": error}

    for name, value in vars(FakeHelperHTTP()).items():
        setattr(screen_helper, name, value)
    result = await code(hass, flow_id, CODE)
    assert result["step_id"] == "bridge"


@pytest.mark.parametrize(
    "properties",
    [{"id": ""}, {"protocol": "2"}],
)
async def test_a_discovery_glowcast_cannot_use_is_not_shown(
    hass: HomeAssistant, properties: dict[str, str]
) -> None:
    result = await discover(hass, discovery(**properties))
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "screen_unsupported"


async def test_a_helper_with_an_entry_updates_its_address(
    hass: HomeAssistant, adapters: Any
) -> None:
    """Found again at a new address: no new discovery, the address is kept
    current, the token and settings stay, and the entry is not reloaded."""
    entry = screen_entry(hass, host="192.0.2.99")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    options = dict(entry.options)

    with patch.object(hass.config_entries, "async_reload") as reload:
        result = await discover(hass, discovery())
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "screen_already_configured"
    assert entry.data["source"] == {**SCREEN_SOURCE, "token": "old-token"}
    assert entry.data["bridge"]["app_key"] == "old-app-key"
    assert entry.options == options
    reload.assert_not_called()
    assert entry.state is ConfigEntryState.LOADED
    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN) == []
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_a_helper_discovered_twice_is_shown_once(hass: HomeAssistant) -> None:
    await discover(hass, discovery())
    result = await discover(hass, discovery())
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_in_progress"
    assert len(hass.config_entries.flow.async_progress_by_handler(DOMAIN)) == 1


async def start_screen(hass: HomeAssistant) -> dict[str, Any]:
    """Start the setup flow and choose the computer screen."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "screen"}
    )


async def test_a_helper_set_up_by_hand(
    hass: HomeAssistant, screen_helper: FakeHelperHTTP
) -> None:
    """Without discovery: the address and port, then the code, then the output."""
    result = await start_screen(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "screen"
    schema = {str(key): key for key in result["data_schema"].schema}
    assert schema["port"].default() == 8488
    flow_id = result["flow_id"]

    screen_helper.reachable = False
    result = await hass.config_entries.flow.async_configure(
        flow_id, {"host": PC_HOST, "port": 8488}
    )
    assert result["step_id"] == "screen"
    assert result["errors"] == {"base": "screen_cannot_connect"}

    screen_helper.reachable = True
    result = await hass.config_entries.flow.async_configure(
        flow_id, {"host": f" {PC_HOST} ", "port": 8488}
    )
    assert result["step_id"] == "screen_code"
    assert result["description_placeholders"] == {"computer": PC_HOST}

    result = await through_the_output(hass, await code(hass, flow_id, CODE))
    assert result["title"] == "Test PC"
    assert result["data"]["source"] == SCREEN_SOURCE
    assert result["result"].unique_id == HELPER_ID


async def test_a_helper_by_hand_alongside_its_discovery(
    hass: HomeAssistant, screen_helper: FakeHelperHTTP
) -> None:
    """Typing the address of a helper that is also discovered works; the
    discovery goes away once the entry is made."""
    await discover(hass, discovery())
    result = await start_screen(hass)
    flow_id = result["flow_id"]
    await hass.config_entries.flow.async_configure(
        flow_id, {"host": PC_HOST, "port": 8488}
    )
    result = await through_the_output(hass, await code(hass, flow_id, CODE))
    assert result["result"].unique_id == HELPER_ID
    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN) == []


async def test_a_helper_by_hand_that_has_an_entry(
    hass: HomeAssistant, screen_helper: FakeHelperHTTP
) -> None:
    """Typed again: no second entry; the typed address is kept."""
    entry = screen_entry(hass, host="192.0.2.99")
    result = await start_screen(hass)
    flow_id = result["flow_id"]
    await hass.config_entries.flow.async_configure(
        flow_id, {"host": PC_HOST, "port": 8488}
    )
    result = await code(hass, flow_id, CODE)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "screen_already_configured"
    assert entry.data["source"]["host"] == PC_HOST
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_the_tv_setup_is_unchanged(hass: HomeAssistant) -> None:
    """Choosing the TV shows the same TV screen as before."""
    result = await start_tv(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "tv"
    assert list(result["data_schema"].schema) == ["tv"]


async def test_screen_reauth_asks_a_new_code(
    hass: HomeAssistant, screen_helper: FakeHelperHTTP
) -> None:
    """A rejected token: a new code stores the new token, all else is kept,
    and the bridge is never paired again."""
    entry = screen_entry(hass)
    options = dict(entry.options)
    result = await entry.start_reauth_flow(hass, data={REAUTH_PART: REAUTH_SOURCE})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "screen_reauth"
    assert result["description_placeholders"]["computer"] == "Test PC"
    flow_id = result["flow_id"]

    result = await code(hass, flow_id, "654321")
    assert result["step_id"] == "screen_reauth"
    assert result["errors"] == {"base": "screen_wrong_code"}

    screen_helper.helper_id = "another-helper"
    result = await code(hass, flow_id, CODE)
    assert result["errors"] == {"base": "screen_other_helper"}

    screen_helper.helper_id = HELPER_ID
    result = await code(hass, flow_id, CODE)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "screen_reauth_successful"
    assert entry.data["source"] == SCREEN_SOURCE
    assert entry.data["bridge"]["app_key"] == "old-app-key"
    assert entry.options == options
    assert entry.title == "Test PC"
    assert FakeHueAPI.instances == []


def test_the_manifest_asks_for_the_helpers_discovery() -> None:
    manifest = json.loads((TRANSLATIONS.parent / "manifest.json").read_text())
    assert manifest["zeroconf"] == ["_glowcast._tcp.local."]


def test_every_step_and_error_has_a_text() -> None:
    """The screen steps' texts exist, with the placeholders they use."""
    config = json.loads((TRANSLATIONS / "en.json").read_text())["config"]
    assert config["flow_title"] == "Glowcast - {name}"
    assert set(config["step"]["user"]["menu_options"]) == {"tv", "screen"}
    for step in ("screen_code", "screen_reauth"):
        assert "{computer}" in config["step"][step]["description"]
    for error in (
        "screen_cannot_connect",
        "screen_wrong_code",
        "screen_pairing_unavailable",
        "screen_other_helper",
        "screen_unsupported",
    ):
        assert error in config["error"]
    assert config["error"]["screen_wrong_code"] == (
        "The code is not correct; check the code the helper shows."
    )
    for reason in (
        "screen_already_configured",
        "screen_reauth_successful",
        "screen_unsupported",
    ):
        assert reason in config["abort"]


async def test_a_helper_found_at_a_new_address_during_its_reauth(
    hass: HomeAssistant, screen_helper: FakeHelperHTTP
) -> None:
    """The open reauth flow has the helper's unique id too; the new address
    is still stored, so the reauth pairs at the new address."""
    entry = screen_entry(hass, host="192.0.2.99")
    result = await entry.start_reauth_flow(hass, data={REAUTH_PART: REAUTH_SOURCE})
    flow_id = result["flow_id"]

    result = await discover(hass, discovery())
    assert result["type"] is FlowResultType.ABORT
    assert entry.data["source"]["host"] == PC_HOST

    result = await code(hass, flow_id, CODE)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "screen_reauth_successful"
    assert entry.data["source"] == SCREEN_SOURCE


async def test_screen_reauth_of_a_loaded_entry_does_not_reload_it(
    hass: HomeAssistant,
    screen_helper: FakeHelperHTTP,
    adapters: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The new token is read at the next start; the entry is not reloaded."""
    entry = screen_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await entry.start_reauth_flow(hass, data={REAUTH_PART: REAUTH_SOURCE})
    result = await code(hass, result["flow_id"], CODE)
    await hass.async_block_till_done()

    assert result["reason"] == "screen_reauth_successful"
    assert entry.data["source"]["token"] == PC_TOKEN
    assert entry.state is ConfigEntryState.LOADED
    assert "should use it for scheduling a reload" not in caplog.text
    assert await hass.config_entries.async_unload(entry.entry_id)
