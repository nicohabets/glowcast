"""The test bench's calibration (#134): finding the lights in the picture,
the exposure, the ambient light and left/right from the bridge.

scripts/bench/win/locate.py needs numpy only (Home Assistant brings it); the
camera itself is not part of CI.
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import numpy as np

REPO = Path(__file__).resolve().parent.parent


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


rp = _load(REPO / "scripts" / "bench" / "report.py", "bench_report")
locate = _load(REPO / "scripts" / "bench" / "win" / "locate.py", "bench_locate")
TH = rp.Thresholds()


def _frame(spots: list[tuple[int, int, int, int, int]]) -> np.ndarray:
    """A dark 720x1280 frame with bright boxes (x, y, w, h, level)."""
    frame = np.full((720, 1280, 3), 3, dtype=np.float32)
    for x, y, w, h, level in spots:
        frame[y : y + h, x : x + w] = level
    return frame


def test_a_light_is_found_where_it_brightens_the_picture() -> None:
    off = _frame([(100, 400, 300, 200, 30)])  # the monitor, the same in both
    on = _frame([(100, 400, 300, 200, 30), (470, 150, 30, 20, 250)])
    found = locate.locate(on, off, (48, 34))
    x, y, w, h = found["box"]
    assert (w, h) == (48, 34)
    assert x <= 470 and x + w >= 500 and y <= 150 and y + h >= 170
    assert found["found"] > TH.min_found


def test_a_region_lit_by_another_light_too_is_avoided() -> None:
    off = _frame([])
    # The uplight lights a wall (left), and some of what the spot lights too.
    wall = _frame([(430, 0, 40, 40, 120), (600, 300, 40, 40, 200)])
    spot = _frame([(600, 300, 40, 40, 220)])
    found = locate.locate(wall, off, (32, 34), [locate.change(spot, off)])
    assert found["box"][0] < 470  # the wall, not the spot's area


def test_nothing_changes_means_not_found() -> None:
    off = _frame([])
    assert locate.locate(off, off, (32, 34))["found"] < TH.min_found


def test_a_change_that_stays_after_the_light_is_off_does_not_count() -> None:
    """Something else brightened meanwhile (a screen): it is in `on` and in
    `after`, so the light is still found where it is."""
    off = _frame([])
    on = _frame([(470, 150, 30, 20, 250), (800, 400, 200, 200, 255)])
    after = _frame([(800, 400, 200, 200, 255)])
    found = locate.locate(on, off, (48, 34), after=after)
    assert 420 <= found["box"][0] <= 470
    assert found["contrast"] >= TH.min_contrast


def test_a_picture_that_changed_everywhere_has_no_contrast() -> None:
    off = _frame([])
    on = np.full((720, 1280, 3), 80, dtype=np.float32)
    assert locate.locate(on, off, (32, 34))["contrast"] < TH.min_contrast


# The wall's 95th percentiles from -11 to -5, recorded on the bench (30-10-2026,
# the lights in warm white at full brightness); the spot's rim clips at
# every exposure (its core). Means: the -8 reference scaled per step.
WALL_P95 = {-11: 84, -10: 133, -9: 178, -8: 215, -7: 249, -6: 255, -5: 255}
SPOT_CLIPPED = {-11: 0.18, -10: 0.25, -9: 0.33, -8: 0.42, -7: 0.55, -6: 0.68, -5: 0.8}
DARK = (1.0, 0.0, 1.0)


def _wall(e: int) -> tuple:
    white = tuple(v * 2.0 ** (e + 8) for v in (183.5, 115.0, 73.5))
    return (white, DARK, WALL_P95[e], 0.0)


def _spot(e: int) -> tuple:
    white = tuple(min(255.0, v * 2.0 ** (e + 8)) for v in (195.0, 183.0, 180.0))
    return (white, DARK, 255.0, SPOT_CLIPPED[e])


def test_the_exposure_sweep() -> None:
    sweep = {float(e): {"wall": _wall(e), "spot": _spot(e)} for e in WALL_P95}
    chosen, always = rp.choose_exposure(sweep, TH)
    assert chosen == -8.0
    assert always == ["spot"]
    # Too dark everywhere: nothing fits.
    dim = {-11.0: {"wall": ((10.0, 8.0, 5.0), DARK, 12.0, 0.0)}}
    assert rp.choose_exposure(dim, TH) == (None, [])


def test_a_clipping_spot_is_judged_on_its_rim() -> None:
    """Alone, the spot gets the darkest exposure that still lifts it well
    above dark; one that is mostly clipped is never taken."""
    sweep = {float(e): {"spot": _spot(e)} for e in WALL_P95}
    assert rp.choose_exposure(sweep, TH) == (-11.0, ["spot"])
    clipped = {float(e): {"spot": (*_spot(e)[:3], 0.9)} for e in WALL_P95}
    assert rp.choose_exposure(clipped, TH) == (None, ["spot"])
    # With the wall, the spot's rim still limits how bright it may go.
    sweep = {float(e): {"wall": _wall(e), "spot": _spot(e)} for e in WALL_P95}
    strict = rp.Thresholds(max_clipped_share=0.3)
    assert rp.choose_exposure(sweep, strict)[0] == -10.0


def test_the_pictures_before_and_after_the_calibration() -> None:
    assert rp.same_picture((113.9, 65.3, 34.4), (114.5, 65.4, 32.8), 0.12)
    assert not rp.same_picture((113.9, 65.3, 34.4), (60.0, 40.0, 20.0), 0.12)
    assert rp.same_picture((1.0, 0.0, 1.0), (3.0, 1.0, 2.0), 0.12)  # both off


def test_overlapping_regions() -> None:
    assert rp.overlap([0, 0, 40, 40], [100, 100, 40, 40]) == 0.0
    assert rp.overlap([0, 0, 40, 40], [20, 0, 40, 40]) == 0.5
    assert rp.overlap([0, 0, 40, 40], [10, 10, 10, 10]) == 1.0


def test_ambient_light() -> None:
    white = (183.5, 115.0, 73.5)
    assert rp.ambient_ratio((1.1, 0.0, 1.1), white) < 0.01  # at night
    assert rp.ambient_ratio((90.0, 70.0, 60.0), white) > TH.max_ambient  # daylight


def test_how_far_a_region_moved() -> None:
    assert rp.moved([434, 2, 32, 34], [434, 2, 32, 34]) == 0.0
    assert rp.moved([434, 2, 32, 34], [437, 6, 32, 34]) == 5.0


# The bench area on the bridge (ids shortened): two channels, one light each.
AREA = {
    "channels": [
        {
            "channel_id": 0,
            "position": {"x": -0.69272, "y": 0.89767, "z": 0.6025},
            "members": [{"service": {"rid": "svc-up", "rtype": "entertainment"}}],
        },
        {
            "channel_id": 1,
            "position": {"x": 0.69832, "y": 1.0, "z": 0.07462},
            "members": [{"service": {"rid": "svc-down", "rtype": "entertainment"}}],
        },
    ]
}
SERVICES = [
    {"id": "svc-up", "renderer_reference": {"rid": "light-up", "rtype": "light"}},
    {"id": "svc-down", "renderer_reference": {"rid": "light-down", "rtype": "light"}},
]


def test_left_and_right_come_from_the_bridge() -> None:
    xs = {
        "wall": rp.channel_x(AREA, SERVICES, "light-up"),
        "spot": rp.channel_x(AREA, SERVICES, "light-down"),
    }
    assert xs == {"wall": -0.69272, "spot": 0.69832}
    # A gradient strip in two channels sits at their mean x.
    strip = {
        "channels": [
            {"position": {"x": -0.4}, "members": [{"service": {"rid": "svc-up"}}]},
            {"position": {"x": 0.0}, "members": [{"service": {"rid": "svc-up"}}]},
        ]
    }
    assert rp.channel_x(strip, SERVICES, "light-up") == -0.2
    assert rp.sides(xs, 0.2) == {"wall": "left", "spot": "right"}
    # Both at the same x (before the product owner moved them): no sides.
    assert rp.sides({"wall": -0.30077, "spot": -0.28475}, 0.2) is None
    assert rp.sides({"wall": -0.3, "spot": None}, 0.2) is None
    assert rp.channel_x(AREA, SERVICES, "another-light") is None
