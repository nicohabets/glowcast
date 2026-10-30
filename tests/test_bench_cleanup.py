"""The test bench's cleanup and left/right mapping on a fake bench (#134).

No hardware: run.py's cleanup and suite.py's side mapping run on plain fakes.
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO = Path(__file__).resolve().parent.parent
BENCH = REPO / "scripts" / "bench"


def _load(name: str) -> ModuleType:
    if str(BENCH) not in sys.path:
        sys.path.insert(0, str(BENCH))
    spec = importlib.util.spec_from_file_location(f"bench_{name}", BENCH / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


run = _load("run")
suite = _load("suite")
rp = run.rp


class FakeOwner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def call(self, domain: str, service: str, data: dict[str, Any]) -> None:
        self.calls.append((domain, service, data["entity_id"]))

    async def state(self, entity_id: str) -> dict[str, Any]:
        return {"state": "on"}


class FakeBench:
    """What cleanup touches, before the calibration finished."""

    def __init__(self) -> None:
        self.report = rp.Report(suite="basic", modes=["pattern"], started="-")
        self.owner = FakeOwner()
        self.ha = None
        self.ha_start = None
        self.screen = None
        self.camera = None
        self.bridge = self
        self.pre_states = {"light.desk_spot": {"entity_id": "light.desk_spot"}}
        self.pre_camera: dict[str, Any] = {}
        self.calibrated = False
        self.tv_off_at = 0.0
        self.label = {"spot": "light 1"}
        self.restored = False

    def win_now(self) -> float:
        return 0.0

    async def restore_lights(self) -> None:
        self.restored = True

    async def wait_restored(self, since: float) -> dict[str, Any]:
        raise KeyError("spot")  # what happened without a pre-run picture

    async def stop_helper(self) -> None:
        raise RuntimeError("the helper would not stop")

    async def close(self) -> None:
        pass


CFG = {"bench_area_id": "area", "home_assistant": {"tv_switch": "switch.tv"}}


async def test_restore_without_a_pre_run_picture_is_skipped() -> None:
    bench = FakeBench()
    await run._restore(bench)
    (step,) = bench.report.steps
    assert bench.restored
    assert step.result == rp.SKIP
    assert step.note == "set back, not checked: the calibration did not finish"


async def test_an_error_in_cleanup_never_skips_the_tv_switch_back() -> None:
    bench = FakeBench()
    bench.calibrated = True  # set too early: the camera check would fail
    bench.pre_camera = {"spot": (1.0, 1.0, 1.0)}
    bench.camera = type("Camera", (), {"running": True})()

    async def stop() -> None:
        raise RuntimeError("camera")

    bench.camera.stop = stop
    await run.cleanup(bench, CFG, local_ha=None)
    assert ("switch", "turn_on", "switch.tv") in bench.owner.calls
    assert any(s.name == "end: the TV's Glowcast back on" for s in bench.report.steps)


def _bench_for_sides() -> Any:
    bench = suite.Bench.__new__(suite.Bench)
    bench.report = rp.Report(suite="basic", modes=["pattern"], started="-")
    bench.label = {"wall": "light 1", "spot": "light 2"}
    bench.bench_area = {
        "channels": [
            {
                "position": {"x": -0.69},
                "members": [{"service": {"rid": "svc-up"}}],
            },
            {
                "position": {"x": 0.70},
                "members": [{"service": {"rid": "svc-down"}}],
            },
        ]
    }
    return bench


SERVICES = [
    {"id": "svc-up", "renderer_reference": {"rid": "light-up"}},
    {"id": "svc-down", "renderer_reference": {"rid": "light-down"}},
]


def test_sides_come_from_the_bridge() -> None:
    bench = _bench_for_sides()
    bench.map_sides({"wall": "light-up", "spot": "light-down"}, SERVICES)
    assert bench.sides == {"wall": "left", "spot": "right"}
    assert bench.report.calibration["light, bridge light and region match"] == "yes"


@pytest.mark.parametrize(
    "rid_of",
    [
        {"wall": None, "spot": "light-down"},  # no bridge light came on
        {"wall": "light-up", "spot": "light-up"},  # the same one twice
    ],
)
def test_an_unmatched_light_fails_when_the_channels_sit_apart(rid_of) -> None:
    bench = _bench_for_sides()
    with pytest.raises(suite.CalibrationError):
        bench.map_sides(rid_of, SERVICES)


def test_an_unmatched_light_skips_left_right_when_the_channels_share_x() -> None:
    bench = _bench_for_sides()
    for channel in bench.bench_area["channels"]:
        channel["position"]["x"] = -0.3
    bench.map_sides({"wall": None, "spot": "light-down"}, SERVICES)
    assert bench.sides is None
    assert "same x" in bench.sides_note
