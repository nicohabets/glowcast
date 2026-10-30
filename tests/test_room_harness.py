"""The room (scripts/demo/): core/ on a fake clock, and how a light is drawn.

The harness runs core/'s own pipeline; the drawing is checked on its SVG, not
rendered (resvg-py is not part of the test requirements).
"""

import colorsys
import importlib.util
import itertools
import logging
from pathlib import Path
from types import ModuleType

import pytest

from custom_components.glowcast.core.light_state import BLACK_HOLD
from custom_components.glowcast.core.pipeline import LOST_RETRY_DELAY, NO_PICTURE_WAIT

REPO = Path(__file__).resolve().parent.parent
CORE = REPO / "custom_components" / "glowcast" / "core"
# Degrees of hue between two places that should not look the same.
HUE_APART = 20


def _load(name: str) -> ModuleType:
    path = REPO / "scripts" / "demo" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_a_picture_then_no_picture() -> None:
    """A sample per send tick; the lights are released and stay off."""
    harness = _load("room_harness")
    scenarios = harness.scenarios(BLACK_HOLD, NO_PICTURE_WAIT)
    run = [scenarios[0], scenarios[-1]]  # sunset, then no picture

    recording = await harness.record_async(CORE, run)

    samples = recording["samples"]
    ticks = sum(round(s.seconds * harness.SEND_HZ) for s in run)
    assert len(samples) == ticks
    channels = {id_ for id_, _, _ in harness.CHANNELS}
    # Off until the first picture claims the lights, then every light is lit.
    assert samples[0]["lights"] == {}
    sunset = [s for s in samples if s["scenario"] == 0]
    assert set(sunset[-1]["lights"]) == channels
    # Released after NO_PICTURE_WAIT of all-zero frames: every light is off.
    no_picture = [s for s in samples if s["scenario"] == 1]
    released = [s for s in no_picture if not s["lights"]]
    assert released
    assert released[0]["time"] - no_picture[0]["time"] >= NO_PICTURE_WAIT
    assert no_picture[-1]["lights"] == {}
    # The same run again gives the same colours.
    assert await harness.record_async(CORE, run) == recording


def test_a_released_light_is_drawn_off() -> None:
    """No colour (released or never claimed) or black: no glow, off colours."""
    room = _load("room")
    assert room.light(None) is None
    assert room.light([0.0, 0.0, 0.0]) is None
    colour, strength = room.light([0.1, 0.078, 0.05])  # the glow: dim, warm
    assert colour == "#FFC780"
    assert 0.3 < strength < 0.4

    off = room.room_svg({}, None)
    assert 'radialGradient id="g' not in off
    assert room.STRIP_OFF in off
    lit = room.room_svg({"left": room.light([1.0, 0.0, 0.0])}, None)
    assert 'radialGradient id="gsl"' in lit
    # Room v2's lights glow only with a colour of their own.
    wall = room.room_svg({"wall_light": room.light([1.0, 0.5, 0.8])}, None)
    assert 'radialGradient id="gwlu"' in wall
    assert 'radialGradient id="gwlu"' not in lit


async def test_loose_lights_rate_place_and_drop(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Loose lights (#196): the real ha_lights output's rate and transition,
    the place it follows, and a dropped part drawn off beside the strip."""
    harness = _load("room_harness")

    # The rate: a moving picture, yet each light gets a command at most once
    # per second, fading over its actual interval.
    moving = [harness.Scenario("moving", 4.0, harness._drifting)]
    loose = await harness.record_async(CORE, moving, lights="loose")
    lamp = [
        c
        for c in loose["commands"]
        if c["light"] == "floor_lamp" and c["service"] == "turn_on"
    ]
    assert len(lamp) >= 3
    times = [c["time"] for c in lamp]
    assert all(b - a >= 0.99 for a, b in zip(times, times[1:], strict=False))
    assert all(0.99 <= c["transition"] <= 1.1 for c in lamp)
    # The strip is not part of loose lights: it stays off.
    assert set(loose["samples"][-1]["lights"]) == set(harness.LOOSE)

    # The place: in the sunset (warm left, blue right) the lamp follows the
    # part of the picture its place says.
    sunset = [harness.scenarios(BLACK_HOLD, NO_PICTURE_WAIT)[0]]
    for place, warm in (("left", True), ("right", False)):
        recording = await harness.record_async(
            CORE, sunset, lights="loose", places={"floor_lamp": place}
        )
        r, _, b = recording["samples"][-1]["lights"]["floor_lamp"]
        assert (r > b) is warm, place

    # The drop: every loose light goes unavailable; after the check and the
    # retry that part is dropped, those lights are off, the area still follows.
    loose_ids = harness.loose_ids("combined")
    area_ids = {id_ for id_, _, _ in harness.CHANNELS}
    assert loose_ids and not area_ids & set(loose_ids)
    gone = [
        harness.Scenario(
            "gone",
            1.0 + 5.0 + LOST_RETRY_DELAY + 1.0,
            harness._drifting,
            unavailable=tuple((id_, 1.0) for id_ in loose_ids),
        )
    ]
    with caplog.at_level(logging.WARNING):
        combined = await harness.record_async(CORE, gone, lights="combined")
    samples = combined["samples"]
    early = samples[20]  # before they went
    assert set(early["lights"]) == area_ids | set(loose_ids)
    # Lost first and retried after LOST_RETRY_DELAY; dropped once that failed.
    retrying = [s["time"] for s in samples if s["retrying"] == [harness.LOOSE_PART]]
    dropped = [s["time"] for s in samples if s["dropped"] == [harness.LOOSE_PART]]
    assert retrying and dropped
    assert dropped[0] - retrying[0] >= LOST_RETRY_DELAY
    assert f"the retry of {harness.LOOSE_PART} failed" in caplog.text
    last = samples[-1]
    assert last["dropped"] == [harness.LOOSE_PART]
    assert last["retrying"] == []
    assert last["unavailable"] == sorted(loose_ids)
    assert set(last["lights"]) == area_ids
    assert last["lights"] != combined["samples"][-30]["lights"]  # still moving


async def test_every_place_shows_in_the_sunset() -> None:
    """Room v2 (#196): in the sunset no two places look the same - their hues
    differ by at least HUE_APART, brightness aside - and the two lights at the
    bottom get the same colour."""
    harness = _load("room_harness")
    sunset = [harness.scenarios(BLACK_HOLD, NO_PICTURE_WAIT)[0]]
    recording = await harness.record_async(CORE, sunset, lights="loose")
    lit = recording["samples"][-1]["lights"]
    places = {id_: place["name"] for id_, place in recording["places"].items()}
    assert places == {
        "floor_lamp": "left",
        "light_bar": "right",
        "wall_light": "top",
        "console_lamp": "bottom",
        "console_strip": "bottom",
        "table_lamp": "ambient",
    }
    hues = {
        place: colorsys.rgb_to_hsv(*lit[id_])[0] * 360
        for place, id_ in (
            ("left", "floor_lamp"),
            ("right", "light_bar"),
            ("top", "wall_light"),
            ("bottom", "console_lamp"),
            ("ambient", "table_lamp"),
        )
    }
    for (a, hue_a), (b, hue_b) in itertools.combinations(hues.items(), 2):
        apart = abs(hue_a - hue_b) % 360
        assert min(apart, 360 - apart) >= HUE_APART, (a, b, hues)
    assert lit["console_strip"] == lit["console_lamp"]


def test_a_room_says_what_is_unavailable_or_dropped() -> None:
    room = _load("room")
    assert room.notes({"unavailable": [], "retrying": [], "dropped": []}) == []
    assert room.notes({"unavailable": ["light_bar"], "retrying": ["ha_lights"]}) == [
        "Light bar unavailable",
        "Home Assistant lights lost, retrying",
    ]
    assert room.notes(
        {"unavailable": ["floor_lamp", "light_bar"], "dropped": ["ha_lights"]}
    ) == ["Floor lamp, light bar unavailable", "Home Assistant lights dropped"]
    assert room.notes({"unavailable": ["a", "b", "c"]}) == [
        "3 Home Assistant lights unavailable"
    ]


def test_a_bad_place_stops_before_the_run() -> None:
    harness = _load("room_harness")
    for values in (["floor_lamp"], ["sofa=left"]):
        with pytest.raises(harness.BadPlace):
            harness.places_of(harness.parse_places(values))
    with pytest.raises(harness.BadPlace):
        harness.place("middle", {"left": (-1.0, 0.0)})
    with pytest.raises(harness.BadPlace):
        harness.place("2,0", {"left": (-1.0, 0.0)})
