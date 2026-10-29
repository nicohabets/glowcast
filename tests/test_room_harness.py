"""The room (scripts/demo/): core/ on a fake clock, and how a light is drawn.

The harness runs core/'s own pipeline; the drawing is checked on its SVG, not
rendered (resvg-py is not part of the test requirements).
"""

import importlib.util
from pathlib import Path
from types import ModuleType

from custom_components.glowcast.core.light_state import BLACK_HOLD
from custom_components.glowcast.core.pipeline import NO_PICTURE_WAIT

REPO = Path(__file__).resolve().parent.parent
CORE = REPO / "custom_components" / "glowcast" / "core"


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
