"""The hardware check (#115): the button, the generic code and its result.

The source and output types' probes are fakes here; the webOS and Hue probes
have their own tests (test_webos_source.py, test_hue_output.py), and the
diagnostics test runs them for real with personal values.
"""

import asyncio
import dataclasses
from typing import Any

import pytest
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from custom_components.glowcast import probes
from custom_components.glowcast.const import DOMAIN
from custom_components.glowcast.hardware_check import notification_id
from custom_components.glowcast.outputs import OUTPUTS
from custom_components.glowcast.sources import SOURCES

from .fakes import FakeAdapters, FakePipeline, wait_for
from .test_init import notices, settle, setup, state, switch, tv_device  # noqa: F401

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

BUTTON = "button.glowcast_living_room_tv_run_hardware_check"


class FakeProbe:
    """A type's probe: records its calls; `result`, an exception, or a wait."""

    def __init__(self, result: Any = None) -> None:
        self.result = {"found": "something"} if result is None else result
        self.calls: list[dict[str, Any]] = []
        self.release: asyncio.Event | None = None

    async def __call__(
        self, hass: HomeAssistant, data: Any, options: Any, *, measure: bool
    ) -> dict[str, Any]:
        self.calls.append({"options": options, "measure": measure})
        if self.release is not None:
            await self.release.wait()
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


@pytest.fixture
def source_probe(monkeypatch: pytest.MonkeyPatch, fakes: FakeAdapters) -> FakeProbe:
    probe = FakeProbe({"tv": "facts"})
    monkeypatch.setitem(
        SOURCES, "webos", dataclasses.replace(SOURCES["webos"], probe=probe)
    )
    return probe


@pytest.fixture
def output_probe(monkeypatch: pytest.MonkeyPatch, fakes: FakeAdapters) -> FakeProbe:
    probe = FakeProbe({"bridge": "facts"})
    monkeypatch.setitem(
        OUTPUTS,
        "hue_entertainment",
        dataclasses.replace(OUTPUTS["hue_entertainment"], probe=probe),
    )
    return probe


async def press(hass: HomeAssistant) -> None:
    await hass.services.async_call(
        "button", "press", {ATTR_ENTITY_ID: BUTTON}, blocking=True
    )
    await settle(hass)


async def test_the_button_on_the_device(
    hass: HomeAssistant, fakes: FakeAdapters
) -> None:
    entry = await setup(hass)
    entity = er.async_get(hass).async_get(BUTTON)
    assert entity.unique_id == f"{entry.entry_id}_hardware_check"
    assert entity.translation_key == "hardware_check"
    assert entity.entity_category == "diagnostic"
    switch_entity = er.async_get(hass).async_get("switch.glowcast_living_room_tv")
    assert entity.device_id == switch_entity.device_id
    assert hass.states.get(BUTTON).name == "Glowcast Living room TV Run hardware check"


async def test_dutch_name(hass: HomeAssistant, fakes: FakeAdapters) -> None:
    hass.config.language = "nl"
    await setup(hass)
    state = next(
        s for s in hass.states.async_all("button") if "hardware" in s.entity_id
    )
    assert state.name == "Glowcast Living room TV Hardwarecontrole uitvoeren"


async def test_off_it_measures_and_tells_where_the_result_is(
    hass: HomeAssistant,
    fakes: FakeAdapters,
    source_probe: FakeProbe,
    output_probe: FakeProbe,
) -> None:
    entry = await setup(hass)
    await press(hass)
    await wait_for(lambda: entry.runtime_data.hardware_check is not None)
    result = entry.runtime_data.hardware_check
    assert source_probe.calls[0]["measure"] is True
    assert output_probe.calls[0]["measure"] is True
    assert result["syncing"] is False
    assert result["pipeline"] is None
    assert result["source"] == {"type": "webos", "probe": {"tv": "facts"}}
    assert result["output"] == {
        "type": "hue_entertainment",
        "probe": {"bridge": "facts"},
    }
    assert result["seconds"] >= 0
    # Read-only: no pipeline, no light claimed, the switch unchanged.
    assert fakes.log == []
    assert FakePipeline.instances == []
    assert state(hass) == STATE_OFF
    await settle(hass)
    notice = notices(hass)[notification_id(entry.entry_id)]
    assert "Living room TV" in notice["title"]
    assert "Download diagnostics" in notice["message"]
    assert "template=hardware-report.md" in notice["message"]


async def test_while_syncing_it_does_not_capture(
    hass: HomeAssistant,
    fakes: FakeAdapters,
    source_probe: FakeProbe,
    output_probe: FakeProbe,
) -> None:
    entry = await setup(hass)
    await switch(hass, "turn_on")
    await press(hass)
    await wait_for(lambda: entry.runtime_data.hardware_check is not None)
    result = entry.runtime_data.hardware_check
    assert source_probe.calls[0]["measure"] is False
    assert result["syncing"] is True
    assert result["pipeline"]["phase"] == "streaming"
    # The lights stay with the pipeline.
    assert state(hass) == STATE_ON
    assert "output.stop" not in fakes.log
    [pipeline] = FakePipeline.instances
    assert not pipeline._stop.is_set()


async def test_a_press_during_a_check_does_nothing(
    hass: HomeAssistant,
    fakes: FakeAdapters,
    source_probe: FakeProbe,
    output_probe: FakeProbe,
) -> None:
    entry = await setup(hass)
    source_probe.release = asyncio.Event()
    await press(hass)
    await press(hass)
    assert len(source_probe.calls) == 1
    source_probe.release.set()
    await wait_for(lambda: entry.runtime_data.hardware_check is not None)
    await press(hass)
    await wait_for(lambda: len(source_probe.calls) == 2)


async def test_a_switch_on_ends_a_check_that_captures(
    hass: HomeAssistant,
    fakes: FakeAdapters,
    source_probe: FakeProbe,
    output_probe: FakeProbe,
) -> None:
    entry = await setup(hass)
    source_probe.release = asyncio.Event()
    await press(hass)
    await switch(hass, "turn_on")
    assert entry.runtime_data.hardware_check == {"interrupted": "started syncing"}
    assert state(hass) == STATE_ON
    await settle(hass)
    assert notification_id(entry.entry_id) not in notices(hass)


async def test_a_failing_probe_is_recorded_and_the_check_finishes(
    hass: HomeAssistant,
    fakes: FakeAdapters,
    source_probe: FakeProbe,
    output_probe: FakeProbe,
) -> None:
    entry = await setup(hass)
    # The text may hold an address: only the error's type is kept.
    source_probe.result = ValueError("http://192.168.1.20/secret")
    await press(hass)
    await wait_for(lambda: entry.runtime_data.hardware_check is not None)
    result = entry.runtime_data.hardware_check
    assert result["source"] == {"type": "webos", "probe": None, "error": "ValueError"}
    assert result["output"]["probe"] == {"bridge": "facts"}
    assert ir.async_get(hass).issues == {}
    await settle(hass)
    assert notification_id(entry.entry_id) in notices(hass)


async def test_a_probe_that_hangs_times_out(
    hass: HomeAssistant,
    fakes: FakeAdapters,
    source_probe: FakeProbe,
    output_probe: FakeProbe,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(probes, "PROBE_TIMEOUT", 0.05)
    entry = await setup(hass)
    output_probe.release = asyncio.Event()  # never set
    await press(hass)
    await wait_for(lambda: entry.runtime_data.hardware_check is not None)
    result = entry.runtime_data.hardware_check
    assert result["output"] == {
        "type": "hue_entertainment",
        "probe": None,
        "error": "timeout",
    }
    assert result["source"]["probe"] == {"tv": "facts"}


async def test_an_integration_that_is_not_set_up(
    hass: HomeAssistant,
    fakes: FakeAdapters,
    source_probe: FakeProbe,
    output_probe: FakeProbe,
) -> None:
    entry = await setup(hass)
    fakes.source_missing = True
    await press(hass)
    await wait_for(lambda: entry.runtime_data.hardware_check is not None)
    assert entry.runtime_data.hardware_check["source"] == {
        "type": "webos",
        "probe": None,
        "error": "the integration it uses is not set up",
    }
    assert source_probe.calls == []
    # No repair issue from the check.
    assert ir.async_get(hass).issues == {}


async def test_combined_probes_each_part(
    hass: HomeAssistant,
    fakes: FakeAdapters,
    source_probe: FakeProbe,
    output_probe: FakeProbe,
) -> None:
    """Home Assistant lights have no probe."""
    options = {
        "output": {
            "type": "combined",
            "parts": [
                {"type": "hue_entertainment", "area_id": "area-tv"},
                {"type": "ha_lights", "follow": "lights", "lights": []},
            ],
        }
    }
    entry = await setup(hass, options)
    await press(hass)
    await wait_for(lambda: entry.runtime_data.hardware_check is not None)
    assert entry.runtime_data.hardware_check["output"] == {
        "type": "combined",
        "probe": {
            "parts": [
                {"type": "hue_entertainment", "probe": {"bridge": "facts"}},
                {"type": "ha_lights", "probe": None},
            ]
        },
    }
    # The Hue part sees its own block only.
    assert output_probe.calls[0]["options"]["output"] == {
        "type": "hue_entertainment",
        "area_id": "area-tv",
    }


async def test_unload_ends_a_check(
    hass: HomeAssistant,
    fakes: FakeAdapters,
    source_probe: FakeProbe,
    output_probe: FakeProbe,
) -> None:
    entry = await setup(hass)
    source_probe.release = asyncio.Event()
    await press(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await settle(hass)
    assert notification_id(entry.entry_id) not in notices(hass)
