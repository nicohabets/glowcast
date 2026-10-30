"""The test bench's judgement (scripts/bench/report.py) on recorded numbers.

The bench itself needs the hardware and is not run in CI; its report logic
is. The camera means below are recorded on the bench (#134): a wall lit by an
uplight ("wall") and a spot's rim ("spot"), 1280x720 at exposure -8.
"""

import importlib.util
import json
import math
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parent.parent


def _load() -> ModuleType:
    path = REPO / "scripts" / "bench" / "report.py"
    spec = importlib.util.spec_from_file_location("bench_report", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses look the module up while the class is made.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


rp = _load()
TH = rp.Thresholds()

# Recorded on the bench (29-10-2026): the references with the lights off
# ("dark"), in warm white 2242 K at full brightness ("white") and in 6500 K
# ("cool").
WALL = rp.Calibration(
    dark=(1.1, 0.0, 1.1), white=(183.5, 115.0, 73.5), cool=(129.8, 183.8, 225.9)
)
SPOT = rp.Calibration(
    dark=(1.0, 0.0, 1.0), white=(195.2, 183.3, 179.9), cool=(203.6, 206.4, 214.3)
)
# Settled means through Glowcast at brightness 80 % (screen mode); the glow
# 5.5-7.7 s into a near-black picture.
WALL_SEEN = {
    "red": (124.3, 0.6, 1.9),
    "green": (12.1, 76.8, 31.0),
    "blue": (1.5, 47.7, 223.0),
    "white": (71.7, 108.7, 140.6),
    "glow": (3.2, 1.9, 1.7),
}
SPOT_SEEN = {
    "red": (169.6, 31.0, 42.0),
    "green": (90.9, 160.9, 128.0),
    "blue": (101.4, 154.9, 192.4),
    "white": (175.9, 179.4, 184.8),
    "glow": (72.2, 63.3, 59.7),
}
# The lights' own state before the run: warm white 2242 K at 143 of 255.
WALL_BEFORE = (113.9, 65.3, 34.4)
SPOT_BEFORE = (171.7, 161.4, 150.2)
LIGHTS = [(WALL, WALL_SEEN), (SPOT, SPOT_SEEN)]


@pytest.mark.parametrize("colour", ["red", "green", "blue", "white"])
@pytest.mark.parametrize(("cal", "seen"), LIGHTS)
def test_the_colours_are_told_apart(cal, seen, colour: str) -> None:
    assert rp.matches_colour(colour, seen[colour], cal, TH)
    for other in {"red", "green", "blue", "white"} - {colour}:
        assert not rp.matches_colour(other, seen[colour], cal, TH)
    assert not rp.matches_colour(colour, cal.dark, cal, TH)


def test_glowcasts_white_is_blue_against_the_warm_reference() -> None:
    """Why Glowcast's white is judged against the cool reference too."""
    warm_only = rp.Calibration(dark=WALL.dark, white=WALL.white)
    assert rp.classify(WALL_SEEN["white"], warm_only, TH) == "blue"
    assert rp.classify(WALL_SEEN["white"], WALL, TH) == "white"
    assert rp.classify(WALL.white, WALL, TH) == "white"
    assert rp.classify(WALL.dark, WALL, TH) == "off"


@pytest.mark.parametrize(("cal", "seen"), LIGHTS)
def test_the_glow_is_dim_warm_and_not_off(cal, seen) -> None:
    assert rp.is_glow(seen["glow"], cal, TH, before=seen["red"])
    assert not rp.is_glow(cal.dark, cal, TH)  # off is not the glow
    assert not rp.is_glow(seen["red"], cal, TH, before=seen["red"])  # not dimmer
    assert not rp.is_glow(seen["blue"], cal, TH)


def test_a_dim_blue_is_not_the_glow() -> None:
    assert not rp.is_glow((2.0, 3.0, 9.0), WALL, TH)


def test_the_restore_is_judged_against_the_picture_before_the_run() -> None:
    assert rp.difference(WALL_BEFORE, (114.5, 65.4, 32.8), WALL) <= TH.same_picture
    assert rp.difference(SPOT_BEFORE, (171.2, 161.7, 151.6), SPOT) <= TH.same_picture
    # Glowcast's colours and its glow are not the lights' own state.
    for (cal, seen), before in zip(LIGHTS, (WALL_BEFORE, SPOT_BEFORE), strict=True):
        for colour in ("white", "glow", "red"):
            assert rp.difference(before, seen[colour], cal) > TH.same_picture


def _frames(start: float, values: list[tuple[float, float, float]], fps: float = 50):
    return [(start + i / fps, {"wall": v}) for i, v in enumerate(values)]


def test_the_crossing_is_halfway_between_the_settled_colours() -> None:
    red, green = WALL_SEEN["red"], WALL_SEEN["green"]
    # 10 frames red, a fade over 5 frames, then green.
    fade = [
        tuple(r + (g - r) * k / 5 for r, g in zip(red, green, strict=True))
        for k in range(1, 6)
    ]
    samples = _frames(100.0, [red] * 10 + fade + [green] * 10)
    t = rp.crossing(samples, "wall", red, green, 100.0, 101.0)
    # Frame 12 is 3/5 of the way: the first at or past halfway.
    assert t == pytest.approx(100.0 + 12 / 50)


def test_a_single_noisy_frame_is_not_a_crossing() -> None:
    red, green = WALL_SEEN["red"], WALL_SEEN["green"]
    samples = _frames(0.0, [red] * 5 + [green] + [red] * 5 + [green] * 5)
    assert rp.crossing(samples, "wall", red, green, 0.0, 1.0) == pytest.approx(11 / 50)


def test_no_crossing_without_a_change() -> None:
    red = WALL_SEEN["red"]
    assert rp.crossing(_frames(0.0, [red] * 10), "wall", red, red, 0.0, 1.0) is None


def test_the_restore_needs_the_picture_back_for_half_a_second() -> None:
    before = WALL_BEFORE
    red = WALL_SEEN["red"]
    samples = _frames(10.0, [red] * 50 + [before] * 10 + [red] * 5 + [before] * 40)
    t = rp.first_match(samples, "wall", before, WALL, TH, 10.0, 13.0)
    assert t == pytest.approx(10.0 + 65 / 50)
    assert (
        rp.first_match(_frames(0.0, [red] * 100), "wall", before, WALL, TH, 0, 2)
        is None
    )


def test_a_hold_allows_noise_but_not_another_colour() -> None:
    red = WALL_SEEN["red"]
    noisy = [(red[0] + (2 if i % 2 else -2), red[1], red[2]) for i in range(50)]
    held, worst = rp.stays(_frames(0.0, noisy), "wall", red, WALL, TH, 0.0, 1.0)
    assert held and worst < 0.05
    faded = noisy[:25] + [(50.0, 0.5, 1.0)] * 25
    held, _ = rp.stays(_frames(0.0, faded), "wall", red, WALL, TH, 0.0, 1.0)
    assert not held
    held, worst = rp.stays([], "wall", red, WALL, TH, 0.0, 1.0)
    assert not held and math.isnan(worst)


def test_the_bridge_state_rule() -> None:
    before = {"on": True, "brightness": 56.1, "xy": [0.574, 0.388], "mirek": 446}
    assert rp.bridge_restored(before, {**before, "brightness": 55.5}) == (True, [])
    assert rp.bridge_restored(before, {**before, "mirek": 153}) == (
        False,
        ["colour temperature"],
    )
    colour = {"on": True, "brightness": 80.0, "xy": [0.3, 0.3], "mirek": None}
    assert rp.bridge_restored(colour, {**colour, "xy": [0.304, 0.3]})[0]
    assert rp.bridge_restored(colour, {**colour, "xy": [0.31, 0.3]}) == (False, ["xy"])
    assert rp.bridge_restored(colour, {**colour, "on": False}) == (False, ["on"])
    assert rp.bridge_restored({"on": False}, {"on": False, "brightness": 3})[0]


def test_delay_statistics() -> None:
    stats = rp.delay_stats([300, 100, 200, 400, 500])
    assert stats == {
        "count": 5,
        "median": 300,
        "min": 100,
        "p10": 140,
        "p90": 460,
        "max": 500,
    }
    assert rp.delay_stats([]) == {"count": 0}


def test_the_report_has_numbers_only_and_scrubs_private_values() -> None:
    report = rp.Report(suite="basic", modes=["pattern"], started="2026-10-29 22:00 UTC")
    report.add(
        rp.Step("red: the lights show red", "pattern", rp.PASS, numbers={"x": 1})
    )
    report.add(rp.Step("switch off", "pattern", rp.FAIL, seconds=1.234))
    report.delays["pattern / all lights"] = rp.delay_stats([250.0, 300.0])
    report.finish()
    assert (report.outcome, report.exit_code) == ("failed", rp.EXIT_FAILED)
    markdown = report.to_markdown()
    assert "| pattern | switch off | fail | 1.23 |" in markdown
    assert "| pattern / all lights | 2 | 275 |" in markdown
    data = json.loads(report.to_json())
    assert data["steps"][0]["numbers"] == {"x": 1}
    text = rp.scrub(
        "streamed to light.desk_spot at 192.0.2.10",
        ["light.desk_spot", "192.0.2.10", ""],
    )
    assert text == "streamed to [removed] at [removed]"


def test_skips_and_their_exit_codes_differ() -> None:
    report = rp.Report(suite="basic", modes=["pattern"], started="-")
    report.finish("skipped: the bridge is streaming another area", rp.EXIT_OTHER_AREA)
    assert report.exit_code == 3
    codes = [
        rp.EXIT_PASSED,
        rp.EXIT_FAILED,
        rp.EXIT_USAGE,
        rp.EXIT_OTHER_AREA,
        rp.EXIT_LOCKED,
        rp.EXIT_TV_AREA_OTHER_APP,
        rp.EXIT_ERROR,
    ]
    assert len(set(codes)) == len(codes)


def test_purity_against_the_cool_reference() -> None:
    assert rp.impurity(WALL_SEEN["red"], WALL, "red") < 0.05
    # The camera sees some green in a wall lit blue: still under the limit.
    assert rp.impurity(WALL_SEEN["blue"], WALL, "blue") < TH.max_impurity
    # Warm white mixed into blue (the lilac of #43) raises the red share.
    lilac = tuple(
        b + 0.3 * (w - d)
        for b, w, d in zip(WALL_SEEN["blue"], WALL.white, WALL.dark, strict=True)
    )
    assert rp.impurity(lilac, WALL, "blue") > TH.max_impurity


def test_an_http_error_is_printed_without_its_headers() -> None:
    """repr() of an HTTP error carries the request's headers, and the token."""
    from aiohttp import ClientResponseError, RequestInfo
    from multidict import CIMultiDict, CIMultiDictProxy
    from yarl import URL

    token = "secret-long-lived-token"
    url = URL("http://192.0.2.10:8123/api/services/light/turn_on")
    headers = CIMultiDictProxy(CIMultiDict({"Authorization": f"Bearer {token}"}))
    err = ClientResponseError(
        RequestInfo(url, "POST", headers, url), (), status=401, message="Unauthorized"
    )
    assert token in repr(err)  # why describe_error exists
    text = rp.describe_error(err)
    assert token not in text
    assert text == "ClientResponseError (status 401)"
    assert rp.describe_error(ValueError("x")) == "ValueError"
