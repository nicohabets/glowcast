"""The test bench's loose lights (#9): config, rate, colours, restore, report.

No hardware: the pure parts of scripts/bench/loose.py, the proxy lights'
mapping (scripts/bench/stubs/proxy.py), the bench's stub service and the
loose suite's steps on recorded numbers.
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.glowcast.outputs import ha_lights

REPO = Path(__file__).resolve().parent.parent
BENCH = REPO / "scripts" / "bench"


def _load(name: str, path: Path) -> ModuleType:
    if str(BENCH) not in sys.path:
        sys.path.insert(0, str(BENCH))
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


loose = _load("bench_loose", BENCH / "loose.py")
loose_suite = _load("bench_loose_suite", BENCH / "loose_suite.py")
suite = _load("bench_suite_for_loose", BENCH / "suite.py")
local_ha = _load("bench_local_ha_for_loose", BENCH / "local_ha.py")
proxy = _load("bench_proxy", BENCH / "stubs" / "proxy.py")
bench_stub = _load(
    "bench_glowcast_bench", BENCH / "stubs" / "glowcast_bench" / "__init__.py"
)
rp = suite.rp

CAMERA = {
    "wall": {"entity_id": "light.desk_up", "region": [0, 0, 10, 10]},
    "spot": {"entity_id": "light.desk_spot", "region": [20, 0, 10, 10]},
}
LOOSE = [
    {"kind": "hue", "light": "wall", "position": "left"},
    {"kind": "hue", "light": "spot", "position": "right"},
    {"kind": "matter", "entity_id": "light.desk_lamp", "x": -0.8, "z": 0.25},
]


# ---- config ------------------------------------------------------------------
def test_the_config_is_parsed() -> None:
    lights = loose.parse({"lights": CAMERA, "loose_lights": LOOSE})
    assert [(li.entity_id, li.kind, li.x, li.z, li.camera) for li in lights] == [
        ("light.desk_up", "hue", -1.0, 0.0, "wall"),
        ("light.desk_spot", "hue", 1.0, 0.0, "spot"),
        ("light.desk_lamp", "matter", -0.8, 0.25, None),
    ]
    assert [li.name for li in lights] == [
        "Bench proxy 1",
        "Bench proxy 2",
        "Bench proxy 3",
    ]
    assert loose.labels(lights, {"wall": "light 1", "spot": "light 2"}) == {
        1: "light 1",
        2: "light 2",
        3: "light 3 (state)",
    }


@pytest.mark.parametrize(
    ("item", "reason"),
    [
        ({"kind": "zigbee", "entity_id": "light.a", "position": "left"}, "kind"),
        ({"kind": "hue", "entity_id": "switch.a", "position": "left"}, "a light"),
        ({"kind": "hue", "light": "porch", "position": "left"}, "one of `lights`"),
        ({"kind": "hue", "entity_id": "light.a", "position": "behind"}, "position"),
        ({"kind": "hue", "entity_id": "light.a", "x": 2, "z": 0}, "-1..1"),
        ({"kind": "hue", "entity_id": "light.a"}, "`position` or `x` and `z`"),
    ],
)
def test_a_wrong_light_is_a_config_error(item: dict[str, Any], reason: str) -> None:
    with pytest.raises(ValueError, match=reason):
        loose.parse({"lights": CAMERA, "loose_lights": [item]})


def test_a_light_in_the_area_too() -> None:
    marked = [{**LOOSE[0], "in_area": True}, *LOOSE[1:]]
    lights = loose.parse({"lights": CAMERA, "loose_lights": marked})
    assert [li.in_area for li in lights] == [True, False, False]
    assert loose.in_area(lights) == lights[0]
    assert loose.in_area(lights[1:]) is None
    assert loose.beside_area(lights) == [lights[2]]
    assert loose.area_unique_id(lights[0], {"wall": "rid-1", "spot": None}) == "rid-1"
    assert loose.area_unique_id(lights[0], {"wall": None}) is None
    assert loose.area_unique_id(lights[0], {}) is None
    assert loose.area_unique_id(lights[0], {"wall": "rid-1", "spot": "rid-1"}) is None


@pytest.mark.parametrize(
    ("items", "reason"),
    [
        ([{**LOOSE[0], "in_area": "yes"}], "true or false"),
        ([{**LOOSE[0], "kind": "matter", "in_area": True}], "kind hue"),
        ([{**LOOSE[2], "kind": "hue", "in_area": True}], "camera `light`"),
        (
            [{**LOOSE[0], "in_area": True}, {**LOOSE[1], "in_area": True}],
            "more than one",
        ),
    ],
)
def test_a_wrong_in_area_light_is_a_config_error(
    items: list[dict[str, Any]], reason: str
) -> None:
    with pytest.raises(ValueError, match=reason):
        loose.parse({"lights": CAMERA, "loose_lights": items})


def test_a_light_twice_is_a_config_error() -> None:
    twice = [LOOSE[0], {"kind": "matter", "entity_id": "light.desk_up", "x": 0, "z": 0}]
    with pytest.raises(ValueError, match="twice"):
        loose.parse({"lights": CAMERA, "loose_lights": twice})
    with pytest.raises(ValueError, match="list"):
        loose.parse({"lights": CAMERA, "loose_lights": []})


def test_the_presets_are_glowcasts() -> None:
    assert loose.PRESETS == ha_lights.PRESETS


def test_the_schedule_is_glowcasts_own() -> None:
    sch = loose.schedule()
    assert sch.network_of("hue", "e1") == "hue:e1"
    assert sch.network_of("other", "e1") is None
    assert sch.NETWORK_COMMANDS_PER_SECOND == 4


# ---- colours ---------------------------------------------------------------
def _on(**attributes: Any) -> dict[str, Any]:
    return {"state": "on", "attributes": attributes}


@pytest.mark.parametrize(
    ("state", "colour"),
    [
        (_on(hs_color=[0.0, 100.0]), "red"),
        (_on(hs_color=[358.0, 92.0]), "red"),
        (_on(hs_color=[120.5, 98.0]), "green"),
        (_on(hs_color=[235.9, 100.0]), "blue"),  # a Hue light's blue (gamut C)
        (_on(hs_color=[31.0, 6.0]), "white"),
        (_on(hs_color=[60.0, 100.0]), "other"),
        (_on(rgb_color=[0, 0, 255]), "blue"),  # no hs: from rgb
        ({"state": "off", "attributes": {}}, "off"),
        (None, "off"),
    ],
)
def test_the_colour_of_a_state(state: dict[str, Any] | None, colour: str) -> None:
    assert loose.state_colour(state) == colour


def test_a_settled_colour_is_one_colour() -> None:
    red, blue = _on(hs_color=[0, 100]), _on(hs_color=[236, 100])
    assert loose.settled_colour([red, red]) == "red"
    assert loose.settled_colour([red, blue]) is None
    assert loose.settled_colour([]) is None


@pytest.mark.parametrize(
    ("x", "z", "colour"),
    [
        (-1.0, 0.0, "red"),  # left
        (1.0, 0.0, "blue"),  # right
        (-0.8, 0.25, "red"),
        (0.0, 0.0, None),  # ambient: the middle takes from both halves
        (0.0, 1.0, None),  # top
        (-0.2, 0.0, None),
    ],
)
def test_left_red_right_blue_by_the_lights_band(
    x: float, z: float, colour: str | None
) -> None:
    light = loose.LooseLight(1, "light.a", "hue", x, z, None)
    assert loose_suite.expected_split(light) == colour


# ---- restore ---------------------------------------------------------------
WARM = _on(brightness=143, color_mode="color_temp", color_temp_kelvin=2242)
XY = _on(brightness=200, color_mode="xy", xy_color=[0.3, 0.4])


@pytest.mark.parametrize(
    ("before", "after", "ok"),
    [
        (
            WARM,
            _on(brightness=144, color_mode="color_temp", color_temp_kelvin=2247),
            True,
        ),
        (
            WARM,
            _on(brightness=150, color_mode="color_temp", color_temp_kelvin=2242),
            False,
        ),
        (WARM, _on(brightness=143, color_mode="xy", xy_color=[0.5, 0.4]), False),
        (
            WARM,
            _on(brightness=143, color_mode="color_temp", color_temp_kelvin=2500),
            False,
        ),
        (XY, _on(brightness=200, color_mode="xy", xy_color=[0.305, 0.396]), True),
        (XY, _on(brightness=200, color_mode="xy", xy_color=[0.2, 0.4]), False),
        ({"state": "off"}, {"state": "off", "attributes": {"brightness": None}}, True),
        ({"state": "off"}, WARM, False),
        (WARM, None, False),
    ],
)
def test_a_light_is_back_as_before(
    before: dict[str, Any], after: dict[str, Any] | None, ok: bool
) -> None:
    assert loose.restored(before, after)[0] is ok


def test_restored_counts_from_when_it_stays_back() -> None:
    red = _on(brightness=204, color_mode="xy", xy_color=[0.68, 0.31])
    samples = [
        (10.0, "light.a", red),
        (11.0, "light.a", WARM),
        (12.0, "light.a", red),  # claimed again for a moment
        (13.0, "light.a", WARM),
        (14.0, "light.a", WARM),
        (13.5, "light.b", red),  # another light
    ]
    assert loose.first_restored(samples, "light.a", WARM, 10.0, 15.0) == 13.0
    assert loose.first_restored(samples, "light.a", WARM, 10.0, 12.5) is None


# ---- the rate ----------------------------------------------------------------
def test_only_glowcasts_colour_commands_count() -> None:
    calls = [
        (1.0, {"entity_id": "light.p1", "rgb_color": [255, 0, 0], "transition": 1.0}),
        (1.1, {"entity_id": ["light.p2"], "rgb_color": [255, 0, 0], "transition": 1}),
        (1.2, {"entity_id": "light.p1", "xy_color": [0.3, 0.3]}),  # a restore
        (1.3, {"entity_id": "light.other", "rgb_color": [1, 2, 3], "transition": 1}),
    ]
    assert loose.commands(calls, ["light.p1", "light.p2"]) == {
        "light.p1": [(1.0, 1.0)],
        "light.p2": [(1.1, 1)],
    }


def test_windows_and_gaps() -> None:
    assert loose.max_in_window([0.0, 0.25, 0.5, 0.75, 1.0], 1.0) == 5
    assert loose.max_in_window([0.0, 0.25, 0.5, 0.75, 1.0], 0.9) == 4
    assert loose.max_in_window([], 1.0) == 0
    assert loose.min_gap([3.0, 1.0, 2.5]) == 0.5
    assert loose.min_gap([1.0]) is None


def _judge(sent: dict[str, list[tuple[float, float | None]]], network: dict) -> Any:
    label = {e: f"light {i}" for i, e in enumerate(sent, 1)}
    names = {n: "Hue network" for n in network.values() if n}
    return loose.judge_rate(sent, network, label, names, slack=0.1)


def test_once_per_second_per_light_passes() -> None:
    sent = {
        "light.p1": [(0.0, 1.0), (1.02, 1.0), (2.0, 1.0)],
        "light.p2": [(0.25, 1.0), (1.3, 1.0)],
        "light.p3": [(0.5, 1.0), (1.55, 1.0)],
    }
    result = _judge(sent, {"light.p1": "hue:a", "light.p2": "hue:a", "light.p3": None})
    assert result.ok, result.note
    assert result.numbers["Hue network lights"] == 2
    assert result.numbers["light 1 interval s"] == 1.0


def test_commands_too_close_or_a_wrong_transition_fail() -> None:
    close = _judge({"light.p1": [(0.0, 1.0), (0.5, 1.0)]}, {"light.p1": "hue:a"})
    assert not close.ok and "too close" in close.note
    fade = _judge({"light.p1": [(0.0, 0.5)]}, {"light.p1": "hue:a"})
    assert not fade.ok and "transition" in fade.note
    silent = _judge({"light.p1": []}, {"light.p1": "hue:a"})
    assert not silent.ok and "no command" in silent.note


def test_the_fade_is_the_actual_interval() -> None:
    # 1.0 s, then 1.5 s (a colour that waited a tick), then a wait of 4 s
    # (more than two intervals): its interval again.
    times = [0.0, 1.0, 2.5, 6.5]
    assert loose.expected_transitions(times, 1.0, 2.0) == [
        (1.0,),
        (1.0,),
        (1.5,),
        (1.0,),
    ]
    good = {"light.p1": list(zip(times, [1.0, 1.0, 1.5, 1.0], strict=True))}
    assert _judge(good, {"light.p1": "hue:a"}).ok
    fixed = {"light.p1": list(zip(times, [1.0, 1.0, 1.0, 1.0], strict=True))}
    result = _judge(fixed, {"light.p1": "hue:a"})
    assert not result.ok
    assert result.numbers["light 1 transition not the actual interval"] == 1
    # At the limit of the wait both fades are right.
    assert loose.expected_transitions([0.0, 2.05], 1.0, 2.0, 0.15)[1] == (2.05, 1.0)


def test_five_lights_on_a_network_share_it() -> None:
    # Five lights: each every 1.25 s (Glowcast's schedule); five commands
    # within 0.8 s are more than the network's 4 a second.
    sent = {f"light.p{i}": [(i * 0.2, 1.25)] for i in range(5)}
    result = _judge(sent, dict.fromkeys(sent, "hue:a"))
    assert result.numbers["light 1 interval s"] == 1.25
    assert not result.ok and "commands per second" in result.note
    spread = {f"light.p{i}": [(i * 0.25, 1.25)] for i in range(5)}
    assert _judge(spread, dict.fromkeys(spread, "hue:a")).ok


# ---- the proxy lights ----------------------------------------------------
def test_the_proxy_forwards_the_service_data() -> None:
    data = proxy.forward_data(
        "light.desk_lamp",
        {"xy_color": (0.3, 0.4), "brightness": 204, "transition": 1.0, "effect": "x"},
    )
    assert data == {
        "entity_id": "light.desk_lamp",
        "xy_color": [0.3, 0.4],
        "brightness": 204,
        "transition": 1.0,
    }


HUE_STATE = {
    "entity_id": "light.desk_up",
    "state": "on",
    "attributes": {
        "supported_color_modes": ["color_temp", "xy"],
        "color_mode": "color_temp",
        "brightness": 143,
        "color_temp_kelvin": 2242,
        "min_color_temp_kelvin": 2000,
        "max_color_temp_kelvin": 6535,
        "hs_color": [30.0, 60.0],
        "xy_color": [0.52, 0.41],
        "rgb_color": [255, 166, 87],
        "supported_features": 44,  # effect, flash, transition
        "friendly_name": "Desk up",
    },
}


class FakeOwner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, service: str, data: dict[str, Any]) -> None:
        self.calls.append((service, data))


async def test_a_proxy_mirrors_its_light_and_forwards_commands() -> None:
    owner = FakeOwner()
    light = proxy.ProxyLight(
        owner,
        {"unique_id": "bench-proxy-1", "name": "Bench proxy 1", "source": "light.x"},
        HUE_STATE,
    )
    assert light.supported_color_modes == {"color_temp", "xy"}
    assert light.min_color_temp_kelvin == 2000
    assert light.supported_features == (
        proxy.LightEntityFeature.TRANSITION | proxy.LightEntityFeature.FLASH
    )
    assert light.is_on and light.brightness == 143
    assert light.color_mode == "color_temp" and light.color_temp_kelvin == 2242
    assert light.name == "Bench proxy 1"  # never the owner's name
    await light.async_turn_on(xy_color=(0.68, 0.31), brightness=204, transition=1.0)
    await light.async_turn_off(transition=0)
    assert owner.calls == [
        (
            "turn_on",
            {
                "entity_id": "light.x",
                "xy_color": [0.68, 0.31],
                "brightness": 204,
                "transition": 1.0,
            },
        ),
        ("turn_off", {"entity_id": "light.x", "transition": 0}),
    ]
    light._apply({"state": "unavailable", "attributes": {}})
    assert not light.available


async def test_the_bench_stores_glowcasts_output(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain="glowcast",
        options={"output": {"type": "ha_lights", "lights": []}, "brightness": 80},
    )
    entry.add_to_hass(hass)
    assert await bench_stub.async_setup(hass, {})
    output = {"type": "ha_lights", "follow": "lights", "lights": [{"entity": "r1"}]}
    await hass.services.async_call(
        "glowcast_bench",
        "set_output",
        {"entry_id": entry.entry_id, "output": output},
        blocking=True,
    )
    assert entry.options == {"output": output, "brightness": 80}


def test_the_loose_entry_and_the_stubs(tmp_path: Path) -> None:
    entry = local_ha.loose_glowcast_entry(
        helper={"helper_id": "a" * 32, "host": "127.0.0.1", "port": 8489, "token": "t"},
        settings={"brightness": 80},
    )
    assert "bridge" not in entry["data"]
    assert entry["options"]["output"] == {
        "type": "ha_lights",
        "follow": "lights",
        "lights": [],
    }
    assert isinstance(
        ha_lights.create(None, entry["data"], entry["options"]),
        ha_lights.HaLightsOutput,
    )
    local_ha._add_loose_stubs(SimpleNamespace(config=tmp_path))
    custom = tmp_path / "custom_components"
    for name in ("hue", "matter"):
        assert (custom / name / "proxy.py").exists()
        assert (custom / name / "light.py").exists()
    assert (custom / "matter" / "manifest.json").exists()
    assert (custom / "glowcast_bench" / "__init__.py").exists()


# ---- the suite's steps on recorded numbers, and the report -----------------
def _bench() -> Any:
    cfg = {
        "lights": CAMERA,
        "loose_lights": LOOSE,
        "bench_area_id": "area-0000",
        "home_assistant": {"url": "http://192.0.2.10:8123", "tv_switch": "switch.tv"},
    }
    report = rp.Report(suite="loose", modes=["pattern"], started="-")
    bench = suite.Bench(cfg, "loose", report, None, {"host": "192.0.2.20"})
    bench.pre_states = {
        "light.desk_lamp": {**XY, "attributes": {"friendly_name": "Desk lamp"}}
    }
    bench.proxies = {
        i: {
            "id": f"registry{i:04}",
            "entity_id": f"light.bench_proxy_{i}",
            "platform": "hue" if i < 3 else "matter",
            "config_entry_id": "hueentry" if i < 3 else "matterentry",
        }
        for i in (1, 2, 3)
    }
    return bench


def test_the_rate_step_and_the_report_name_no_light() -> None:
    bench = _bench()
    calls = [
        (
            t + 0.25 * i,
            {
                "entity_id": f"light.bench_proxy_{i}",
                "rgb_color": [255, 0, 0],
                "transition": 1.0,
            },
        )
        for t in (100.0, 101.0, 102.0)
        for i in (1, 2, 3)
    ]
    bench.ha = SimpleNamespace(calls=calls)
    loose_suite.rate_step(bench, 99.0, 103.0)
    (step,) = bench.report.steps
    assert step.result == rp.PASS, step.note
    assert step.numbers["Hue network lights"] == 2
    assert step.numbers["Thread network lights"] == 1
    assert step.numbers["light 3 (state) commands"] == 3
    bench.report.finish()
    text = bench.report.to_markdown() + bench.report.to_json()
    forbidden = bench.forbidden()
    for value in (
        "light.desk_lamp",
        "light.desk_up",
        "Desk lamp",
        "registry0001",
        "http://192.0.2.10:8123",
    ):
        assert value in forbidden
    assert not [value for value in forbidden if value and value in text]


def test_a_colour_step_by_state() -> None:
    bench = _bench()
    bench.loose = [bench.loose[2]]  # the light judged by its state
    bench.offset = 0.0
    bench.camera = SimpleNamespace(lines=[])
    blue = _on(hs_color=[236.0, 100.0])
    bench.owner = SimpleNamespace(
        samples=[(t, "light.desk_lamp", blue) for t in (14.5, 15.5, 16.5)]
        + [(10.5, "light.desk_lamp", _on(hs_color=[0.0, 100.0]))]
    )
    loose_suite.colour_step(bench, "blue", {3: "blue"}, 10.0, 17.0)
    loose_suite.colour_step(bench, "red", {3: "red"}, 10.0, 17.0)
    loose_suite.colour_step(bench, loose_suite.SPLIT, {3: None}, 10.0, 17.0)
    first, second, third = bench.report.steps
    assert first.result == rp.PASS and first.numbers["light 3 (state) states"] == 3
    assert second.result == rp.FAIL
    assert third.result == rp.SKIP


def test_the_bench_checks_its_home_assistant_is_its_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inst = SimpleNamespace(config=tmp_path / "config")
    answers = {"config_dir": str(tmp_path / "config")}
    monkeypatch.setattr(local_ha.demo, "_request", lambda *a, **k: answers)
    local_ha._check_own(inst, "token")
    answers["config_dir"] = str(tmp_path / "another")
    with pytest.raises(SystemExit):
        local_ha._check_own(inst, "token")


class FakeLocalHA:
    def __init__(self, entries: list[dict[str, Any]]) -> None:
        self.entries = entries
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def registry(self) -> list[dict[str, Any]]:
        return self.entries

    async def call(self, domain: str, service: str, data: dict[str, Any]) -> None:
        self.calls.append((domain, service, data))


async def test_the_lights_are_stored_by_registry_id_with_their_positions() -> None:
    bench = _bench()
    entries = [
        {
            "id": f"registry{i:04}",
            "entity_id": f"light.bench_proxy_{i}",
            "unique_id": f"bench-proxy-{i}",
            "platform": "hue" if i < 3 else "matter",
            "config_entry_id": "hueentry" if i < 3 else "matterentry",
        }
        for i in (3, 1, 2)
    ] + [{"id": "x", "entity_id": "light.other", "unique_id": "o", "platform": "hue"}]
    bench.ha = FakeLocalHA(entries)
    await bench.place_loose_lights()
    ((domain, service, data),) = bench.ha.calls
    assert (domain, service) == ("glowcast_bench", "set_output")
    assert data["entry_id"] == local_ha.ENTRY_ID
    assert data["output"] == {
        "type": "ha_lights",
        "follow": "lights",
        "lights": [
            {"entity": "registry0001", "x": -1.0, "z": 0.0},
            {"entity": "registry0002", "x": 1.0, "z": 0.0},
            {"entity": "registry0003", "x": -0.8, "z": 0.25},
        ],
    }
    assert bench.proxies[3]["entity_id"] == "light.bench_proxy_3"


def test_the_rate_step_leaves_out_a_proxy_of_no_loose_light() -> None:
    """The combined suite's proxy of a light in the area too (#198) is not
    one of `bench.loose`: the rate step judges the loose lights only."""
    bench = _bench()
    bench.proxies[9] = {
        "id": "registry0009",
        "entity_id": "light.bench_proxy_9",
        "platform": "hue",
        "config_entry_id": "hueentry",
    }
    bench.ha = SimpleNamespace(calls=[])
    loose_suite.rate_step(bench, 99.0, 103.0)
    (step,) = bench.report.steps
    assert step.numbers["Hue network lights"] == 2
