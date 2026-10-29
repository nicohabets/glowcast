"""Setup, options and reauth flows, with a fake bridge and no network."""

import asyncio
import json
from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import aiohttp
import pytest
from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowHandler, FlowResultType, InvalidData
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)
from yarl import URL

from custom_components.glowcast.const import DOMAIN
from custom_components.glowcast.outputs.hue_entertainment import _device_type

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
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

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
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    options = result["data_schema"].schema["tv"].config["options"]
    assert [option["value"] for option in options] == ["other-tv"]
    other.mock_state(hass, ConfigEntryState.NOT_LOADED)


async def test_same_tv_in_two_setups(hass: HomeAssistant) -> None:
    """A second setup for a TV that is already being set up stops."""
    first = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    second = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
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


async def test_bridge_unreachable(hass: HomeAssistant, bridge: FakeBridge) -> None:
    """An unreachable bridge is reported before pairing starts."""
    bridge.reachable = False
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
