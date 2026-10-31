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
from custom_components.glowcast.sources import SOURCES, webos

from .fakes import FakeAdapters, wait_for
from .test_webos_source import FakeWebOsClient, _jpeg

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

# Realistic secret and personal values; none of them may be in the download.
TV_ID = "7d9c4f3a-1b2e-4c5d-8e6f-0a1b2c3d4e5f"
TV_NAME = "Annas woonkamer TV"
TV_HOST = "192.168.178.42"
TV_CLIENT_SECRET = "a3f1c9e07b2d4e5f8a6b1c2d3e4f5a6b"
TV_SERIAL = "305KRAB1C234"
# The bridge id as the bridge reports it, and as the hue integration
# normalises it into its entry's unique id (and so Glowcast's data).
BRIDGE_ID = "ecb5fafffe0a1b2c"
BRIDGE_UNIQUE_ID = "ecb5fa0a1b2c"
BRIDGE_RESOURCE_ID = "6c0b2f7a-3d1e-4a5b-9c8d-7e6f5a4b3c2d"
BRIDGE_HOST = "192.168.178.57"
BRIDGE_API_KEY = "Xz7pQ2mL9vR4tY8wK3nB6cF1hJ5dS0aG-eUoIi"
BRIDGE_SERIAL = "C42B1A0B2C3D"
BRIDGE_NAME = "Bridge in the cupboard"
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
    BRIDGE_UNIQUE_ID,
    BRIDGE_RESOURCE_ID,
    BRIDGE_HOST,
    BRIDGE_API_KEY,
    BRIDGE_SERIAL,
    BRIDGE_NAME,
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
    for domain, unique_id, identifiers, data, device in (
        (
            "webostv",
            TV_ID,
            {TV_ID},
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
            BRIDGE_UNIQUE_ID,
            # As the hue integration registers a v2 bridge.
            {BRIDGE_ID, BRIDGE_RESOURCE_ID},
            {"host": BRIDGE_HOST, "api_key": BRIDGE_API_KEY, "api_version": 2},
            {
                "manufacturer": "Signify Netherlands B.V.",
                "model": "Hue Bridge Pro",
                "sw_version": "1.72.2072152000",
                "serial_number": BRIDGE_SERIAL,
                "name": BRIDGE_NAME,
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
        bridge = registry.async_get_or_create(
            config_entry_id=other.entry_id,
            identifiers={(domain, identifier) for identifier in identifiers},
            **device,
        )
    # `bridge` is the hue entry's device, the loop's last; a light is linked
    # through it, as the hue integration does.
    light = registry.async_get_or_create(
        config_entry_id=others[1].entry_id,
        identifiers={("hue", "light-resource-id")},
        model="Hue gradient lightstrip",
    )
    # Both Home Assistant versions accept it here, not in async_get_or_create.
    registry.async_update_device(light.id, via_device_id=bridge.id)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=TV_NAME,
        unique_id=TV_ID,
        data={
            "source": {"type": "webos", "unique_id": TV_ID},
            "bridge": {
                "unique_id": BRIDGE_UNIQUE_ID,
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

    assert diagnostics["versions"]["glowcast"] == "0.4.0"
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
    # Home Assistant adds the integration's repair issues to the download;
    # their text has the TV's name as a placeholder.
    entry.runtime_data._raise_issue("area_not_found")
    download = await _get_diagnostics_for_config_entry(hass, hass_client, entry)
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
    assert diagnostics["versions"]["glowcast"] == "0.4.0"
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


def _remove_hue_devices(hass: HomeAssistant) -> str:
    """Remove the hue entry's devices; the entry's id."""
    registry = dr.async_get(hass)
    hue_entry_id = hass.config_entries.async_entry_for_domain_unique_id(
        "hue", BRIDGE_UNIQUE_ID
    ).entry_id
    for device in dr.async_entries_for_config_entry(registry, hue_entry_id):
        registry.async_remove_device(device.id)
    return hue_entry_id


@pytest.mark.parametrize(
    ("identifiers", "bridge_via_light"),
    [
        ({BRIDGE_ID, BRIDGE_RESOURCE_ID}, False),
        ({BRIDGE_ID, BRIDGE_RESOURCE_ID}, True),
        ({BRIDGE_ID.upper()}, True),
        ({BRIDGE_UNIQUE_ID}, False),
        ({BRIDGE_UNIQUE_ID.upper()}, True),
        ({BRIDGE_RESOURCE_ID}, False),
        ({BRIDGE_RESOURCE_ID}, True),
    ],
    ids=[
        "16 characters",
        "16 characters, every device linked",
        "16 characters in other case",
        "12 characters",
        "12 characters in other case, every device linked",
        "another identifier",
        "another identifier, every device linked",
    ],
)
async def test_the_bridge_is_found_however_hue_names_it(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    entry: Any,
    identifiers: set[str],
    bridge_via_light: bool,
) -> None:
    """Every light is linked through the bridge, as the hue integration does.

    With `bridge_via_light` the bridge is linked through a light too, so every
    device has a `via_device_id`, as on Home Assistant 2026.10 (#163).
    """
    registry = dr.async_get(hass)
    hue_entry_id = _remove_hue_devices(hass)
    bridge = registry.async_get_or_create(
        config_entry_id=hue_entry_id,
        identifiers={("hue", identifier) for identifier in identifiers},
        model="Hue Bridge Pro",
    )
    lights = [
        registry.async_get_or_create(
            config_entry_id=hue_entry_id,
            identifiers={("hue", f"light-resource-id-{n}")},
            model="Hue gradient lightstrip",
        )
        for n in range(2)
    ]
    for light in lights:
        registry.async_update_device(light.id, via_device_id=bridge.id)
    if bridge_via_light:
        registry.async_update_device(bridge.id, via_device_id=lights[0].id)
    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    assert diagnostics["output"]["device"]["model"] == "Hue Bridge Pro"


async def test_the_identifier_wins_over_a_busier_device(
    hass: HomeAssistant, hass_client: ClientSessionGenerator, entry: Any
) -> None:
    """The bridge is found by its identifier, also when another device of the
    entry has more devices linked through it: the links are only a fallback."""
    registry = dr.async_get(hass)
    hue_entry_id = _remove_hue_devices(hass)
    registry.async_get_or_create(
        config_entry_id=hue_entry_id,
        identifiers={("hue", BRIDGE_ID), ("hue", BRIDGE_RESOURCE_ID)},
        model="Hue Bridge Pro",
    )
    room = registry.async_get_or_create(
        config_entry_id=hue_entry_id,
        identifiers={("hue", "room-resource-id")},
        model="Room",
    )
    for n in range(2):
        light = registry.async_get_or_create(
            config_entry_id=hue_entry_id,
            identifiers={("hue", f"light-resource-id-{n}")},
            model="Hue gradient lightstrip",
        )
        registry.async_update_device(light.id, via_device_id=room.id)
    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    assert diagnostics["output"]["device"]["model"] == "Hue Bridge Pro"


async def test_no_bridge_among_the_hue_devices(
    hass: HomeAssistant, hass_client: ClientSessionGenerator, entry: Any
) -> None:
    """No identifier matches and no device is linked through another."""
    registry = dr.async_get(hass)
    hue_entry_id = _remove_hue_devices(hass)
    for n in range(2):
        registry.async_get_or_create(
            config_entry_id=hue_entry_id,
            identifiers={("hue", f"light-resource-id-{n}")},
            model="Hue gradient lightstrip",
        )
    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    assert diagnostics["output"] == {"type": "hue_entertainment", "device": None}


async def test_the_hardware_check_has_no_secrets_names_or_picture(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    entry: Any,
    aioclient_mock: AiohttpClientMocker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real webOS and Hue probes (#115), against a TV and a bridge that
    tell everything they know, personal details included."""
    image_url = f"https://{TV_HOST}:3001/resources/9f8e7d6c5b4a/capture.jpg"
    mac = "ec:b5:fa:0a:1b:2c"
    input_label = "Annas PlayStation"
    light_name = "Strip achter de bank"
    owner_id = "3e2d1c0b-aaaa-4bbb-8ccc-9d8e7f6a5b4c"
    personal = [*SECRETS, image_url, "9f8e7d6c5b4a", mac, input_label, light_name]
    personal.append(owner_id)
    personal.append("com.webos.app.hdmi1")  # what was on screen (#282)

    connect = FakeWebOsClient.connect

    async def connect_and_tell(client: FakeWebOsClient) -> bool:
        result = await connect(client)
        client.request_result = {"imageUri": image_url}
        client.tv_info.system = {"modelName": "OLED65G36LA", "serialNumber": TV_SERIAL}
        client.tv_info.software = {
            "product_name": "webOSTV 25",
            "major_ver": "33",
            "minor_ver": "31.68",
            "device_id": mac,
        }
        client.tv_info.hello = {
            "deviceOSReleaseVersion": "10.2.0",
            "deviceUUID": TV_ID,
        }
        client.tv_state.current_app_id = "com.webos.app.hdmi1"
        client.tv_state.inputs = {"HDMI_1": {"label": input_label}}
        return result

    FakeWebOsClient.instances = []
    monkeypatch.setattr(webos, "WebOsClient", FakeWebOsClient)
    monkeypatch.setattr(FakeWebOsClient, "connect", connect_and_tell)
    # Glowcast's own probes, not the fakes' (the fixture fakes only create).
    aioclient_mock.clear_requests()
    aioclient_mock.get(image_url, content=_jpeg())
    base = f"https://{BRIDGE_HOST}"
    aioclient_mock.get(
        f"{base}/api/config",
        json={
            "name": BRIDGE_NAME,
            "bridgeid": BRIDGE_ID.upper(),
            "mac": mac,
            "ipaddress": BRIDGE_HOST,
            "modelid": "BSB003",
            "swversion": "1972004020",
            "apiversion": "1.72.0",
        },
    )
    aioclient_mock.get(f"{base}/auth/v1", headers={"hue-application-id": owner_id})
    aioclient_mock.get(
        f"{base}/clip/v2/resource/entertainment_configuration",
        json={
            "data": [
                {
                    "id": AREA_ID,
                    "metadata": {"name": AREA_NAME},
                    "name": AREA_NAME,
                    "status": "active",
                    "active_streamer": {"rid": owner_id, "rtype": "auth_v1"},
                    "configuration_type": "screen",
                    "channels": [
                        {
                            "channel_id": 0,
                            "position": {"x": -0.8, "y": 0.8, "z": 0.0},
                            "members": [
                                {"service": {"rid": BRIDGE_RESOURCE_ID}, "index": 0}
                            ],
                        }
                    ],
                }
            ]
        },
    )
    aioclient_mock.get(
        f"{base}/clip/v2/resource/entertainment",
        json={"data": [{"id": BRIDGE_RESOURCE_ID, "owner": {"rid": owner_id}}]},
    )
    aioclient_mock.get(
        f"{base}/clip/v2/resource/device",
        json={
            "data": [
                {
                    "id": owner_id,
                    "metadata": {"name": light_name},
                    "product_data": {
                        "model_id": "LCX004",
                        "product_archetype": "hue_lightstrip_tv",
                    },
                }
            ]
        },
    )

    await entry.runtime_data.async_hardware_check()
    await wait_for(lambda: entry.runtime_data.hardware_check is not None, limit=5)
    download = await _get_diagnostics_for_config_entry(hass, hass_client, entry)
    check = download["data"]["hardware_check"]
    # It found what a report needs.
    tv = check["source"]["probe"]
    assert tv["tv"]["model"] == "OLED65G36LA"
    assert tv["tv"]["webos_version"] == "10.2.0"
    assert "app" not in tv["tv"]  # the picture was not black (#282)
    assert tv["capture"]["answered"] is True
    assert tv["capture"]["image"]["width"] == 960
    hue = check["output"]["probe"]
    assert hue["bridge"]["modelid"] == "BSB003"
    assert hue["area"]["channels"][0]["members"][0]["model_id"] == "LCX004"
    assert hue["area"]["stream"] == "glowcast"
    # And nothing personal, and no picture.
    text = json.dumps(download)
    for value in personal:
        assert value.lower() not in text.lower()
    assert "pixels" not in json.dumps(check)
    assert not re.search(r"\b\d{1,3}(\.\d{1,3}){3}\b", json.dumps(check))
    # The probe's own connection to the TV is closed.
    assert all(client.disconnected for client in FakeWebOsClient.instances)
