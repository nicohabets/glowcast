"""The test bench's combined suite (#11): an area and loose lights in one entry.

No hardware: the pure parts (scripts/bench/combined.py), the entry and the
output block the bench writes, the bench's availability service, and the
part-failing steps on a fake bench with scripted answers.
"""

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from homeassistant.core import HomeAssistant

from custom_components.glowcast.outputs import OUTPUTS

from .test_bench_loose import (
    BENCH,
    CAMERA,
    LOOSE,
    _bench,
    _load,
    bench_stub,
    local_ha,
    loose,
    proxy,
    rp,
    suite,
)

combined = _load("bench_combined", BENCH / "combined.py")
combined_suite = _load("bench_combined_suite", BENCH / "combined_suite.py")
run = _load("bench_run_for_combined", BENCH / "run.py")


# ---- pure parts ------------------------------------------------------------
def test_the_dropped_parts_from_the_status() -> None:
    assert combined.dropped({"dropped_outputs": {"ha_lights": "output_lost"}}) == {
        "ha_lights": "output_lost"
    }
    assert combined.dropped({}) == {}
    assert combined.dropped(None) == {}
    assert combined.dropped({"dropped_outputs": "x"}) == {}


def test_commands_after_a_time() -> None:
    calls = [
        (1.0, {"entity_id": "light.p3", "rgb_color": [1, 2, 3], "transition": 1.0}),
        (5.0, {"entity_id": "light.p3", "rgb_color": [1, 2, 3], "transition": 1.0}),
        (6.0, {"entity_id": "light.p3", "xy_color": [0.3, 0.3]}),  # a restore
        (7.0, {"entity_id": ["light.p1"], "rgb_color": [1, 2, 3], "transition": 1}),
    ]
    assert combined.commands_after(calls, ["light.p3"], 2.0) == 1
    assert combined.commands_after(calls, ["light.p3", "light.p1"], 2.0) == 2
    assert combined.commands_after(calls, ["light.p3"], 0.0, 4.0) == 1


def test_one_stream_all_along() -> None:
    active = {"status": "active", "streamer": "app"}
    assert combined.one_stream([active, active]) == (True, 2)
    assert not combined.one_stream([active, {"status": "inactive"}])[0]
    assert not combined.one_stream([active, {**active, "streamer": "other"}])[0]
    assert not combined.one_stream([])[0]


# ---- config, entry and output block ----------------------------------------
def test_the_combined_suite_drives_the_lights_beside_the_area() -> None:
    lights = loose.beside_area(loose.parse({"lights": CAMERA, "loose_lights": LOOSE}))
    assert [light.entity_id for light in lights] == ["light.desk_lamp"]


def test_the_combined_suite_needs_a_light_beside_the_area(tmp_path: Path) -> None:
    cfg = {
        "bridge_keys": str(tmp_path / "keys.json"),
        "bench_area_id": "area",
        "home_assistant": {"url": "http://192.0.2.10:8123", "token_file": "t"},
        "lights": CAMERA,
        "helper": {"port": 8489, "pairing_code": "123456"},
        "loose_lights": LOOSE[:2],  # only the area's own lights
    }
    with pytest.raises(run.ConfigError, match="beside the bench area"):
        run.check_config(cfg, ["pattern"], "combined")


def test_the_combined_entry() -> None:
    entry = local_ha.combined_glowcast_entry(
        helper={"helper_id": "a" * 32, "host": "127.0.0.1", "port": 8489, "token": "t"},
        app_key="app-key",
        client_key="00" * 16,
        area_id="area-id",
        settings={"brightness": 80},
    )
    assert entry["data"]["bridge"]["unique_id"] == local_ha.BRIDGE_UNIQUE_ID
    output = entry["options"]["output"]
    assert output["type"] == "combined" and output["type"] in OUTPUTS
    assert output["parts"] == [
        {"type": "hue_entertainment", "area_id": "area-id"},
        {"type": "ha_lights", "follow": "lights", "lights": []},
    ]
    assert all(part["type"] in OUTPUTS for part in output["parts"])


class FakeLocalHA:
    def __init__(self, entries: list[dict[str, Any]]) -> None:
        self.entries = entries
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def registry(self) -> list[dict[str, Any]]:
        return self.entries

    async def call(self, domain: str, service: str, data: dict[str, Any]) -> None:
        self.calls.append((domain, service, data))


def _combined_bench() -> Any:
    cfg = {
        "lights": CAMERA,
        "loose_lights": LOOSE,
        "bench_area_id": "area-0000",
        "home_assistant": {"url": "http://192.0.2.10:8123", "tv_switch": "switch.tv"},
        "helper": {"port": 8489, "pairing_code": "123456"},
    }
    report = rp.Report(suite="combined", modes=["pattern"], started="-")
    return suite.Bench(cfg, "combined", report, None, {"host": "192.0.2.20"})


async def test_the_lights_go_into_the_combined_block() -> None:
    bench = _combined_bench()
    assert [light.index for light in bench.loose] == [3]
    assert bench.loose_label == {3: "light 3 (state)"}
    bench.ha = FakeLocalHA(
        [
            {
                "id": "registry0003",
                "entity_id": "light.bench_proxy_3",
                "unique_id": "bench-proxy-3",
                "platform": "matter",
                "config_entry_id": "matterentry",
            }
        ]
    )
    await bench.place_loose_lights()
    ((_, _, data),) = bench.ha.calls
    assert data["output"] == {
        "type": "combined",
        "parts": [
            {"type": "hue_entertainment", "area_id": "area-0000"},
            {
                "type": "ha_lights",
                "follow": "lights",
                "lights": [{"entity": "registry0003", "x": -0.8, "z": 0.25}],
            },
        ],
    }


# ---- the bench's availability service --------------------------------------
async def test_a_proxy_is_made_unavailable_only_here(hass: HomeAssistant) -> None:
    held: list[bool] = []
    light = SimpleNamespace(set_unavailable=held.append)
    hass.data[proxy.LIGHTS] = {"light.bench_proxy_3": light}
    assert await bench_stub.async_setup(hass, {})
    for available in (False, True):
        await hass.services.async_call(
            "glowcast_bench",
            "set_available",
            {"entity_id": "light.bench_proxy_3", "available": available},
            blocking=True,
        )
    assert held == [True, False]
    with pytest.raises(Exception, match="no such proxy light"):
        await hass.services.async_call(
            "glowcast_bench",
            "set_available",
            {"entity_id": "light.other", "available": False},
            blocking=True,
        )
    assert bench_stub.PROXY_LIGHTS == proxy.LIGHTS


def test_a_held_proxy_is_unavailable() -> None:
    light = proxy.ProxyLight(
        None,
        {"unique_id": "bench-proxy-1", "name": "Bench proxy 1", "source": "light.x"},
        {"state": "on", "attributes": {"supported_color_modes": ["xy"]}},
    )
    assert light.available
    light._held = True
    assert not light.available


# ---- the part-failing steps on a fake bench --------------------------------
class ScriptedHA:
    """The local HA as Glowcast behaves: lost when unavailable, back after the
    retry the first time, dropped through the retry the second time."""

    def __init__(self) -> None:
        self.calls: list[tuple[float, dict[str, Any]]] = []
        self.losses = 0
        self.attribute: dict[str, str] = {}

    async def call(self, domain: str, service: str, data: dict[str, Any]) -> None:
        assert (domain, service) == ("glowcast_bench", "set_available")
        if not data["available"]:
            self.losses += 1
            self.reads = 0
            self.attribute = {"ha_lights": "output_lost"}
        elif self.losses == 1:
            self.attribute = {}
            self.calls.append(
                (
                    time.time(),
                    {
                        "entity_id": data["entity_id"],
                        "rgb_color": [255, 0, 0],
                        "transition": 1.0,
                    },
                )
            )
        else:
            self.attribute = {"ha_lights": "output_unavailable"}

    async def state(self, key: str) -> dict[str, Any]:
        self.reads = getattr(self, "reads", 0) + 1
        if self.losses == 2 and self.reads > 1:  # the retry failed meanwhile
            self.attribute = {"ha_lights": "output_unavailable"}
        return {"state": "streaming", "attributes": {"dropped_outputs": self.attribute}}


async def _no_wait(seconds: float) -> None:
    await asyncio.sleep(0)


async def test_a_part_failing_and_the_area_streaming_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(combined_suite, "sleep", _no_wait)
    monkeypatch.setattr(combined_suite, "LOST_AGAIN_WINDOW", 0.0)
    bench = _bench()
    bench.loose = [bench.loose[2]]
    bench.ha = ScriptedHA()
    active = {"status": "active", "streamer": "app"}
    bench.bridge = SimpleNamespace(statuses=lambda start, end: [active, active])
    await combined_suite.part_fails(bench)
    lost, back, through = bench.report.steps
    assert lost.result == rp.PASS and lost.numbers["reason"] == "output_lost"
    assert back.result == rp.PASS and back.numbers["commands after the retry"] == 1
    assert through.result == rp.PASS
    assert through.numbers["reason after the retry"] == "output_unavailable"
    assert through.numbers["still dropped once available again"] == "yes"
    text = bench.report.to_markdown()
    assert not [v for v in bench.forbidden() if v and v in text]


async def test_a_released_area_fails_the_part_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(combined_suite, "sleep", _no_wait)
    monkeypatch.setattr(combined_suite, "LOST_AGAIN_WINDOW", 0.0)
    bench = _bench()
    bench.loose = [bench.loose[2]]
    bench.ha = ScriptedHA()
    inactive = {"status": "inactive", "streamer": None}
    bench.bridge = SimpleNamespace(statuses=lambda start, end: [inactive])
    await combined_suite.part_fails(bench)
    _, back, through = bench.report.steps
    assert back.result == rp.FAIL and back.numbers["area one stream"] == "no"
    assert through.result == rp.FAIL
