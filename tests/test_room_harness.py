"""The room harness (scripts/demo/room_harness.py): core/ on a fake clock.

Only the harness, not the drawing: that needs resvg-py, which is not part of
the test requirements.
"""

import importlib.util
from pathlib import Path
from types import ModuleType

from custom_components.glowcast.core.light_state import BLACK_HOLD
from custom_components.glowcast.core.pipeline import NO_PICTURE_WAIT

REPO = Path(__file__).resolve().parent.parent
CORE = REPO / "custom_components" / "glowcast" / "core"


def _harness() -> ModuleType:
    path = REPO / "scripts" / "demo" / "room_harness.py"
    spec = importlib.util.spec_from_file_location("room_harness", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_picture_then_no_picture() -> None:
    """A sample per send tick; the lights are released and stay off."""
    harness = _harness()
    scenarios = harness.scenarios(BLACK_HOLD, NO_PICTURE_WAIT)
    run = [scenarios[0], scenarios[-1]]  # sunset, then no picture

    recording = harness.record(CORE, run)

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
    assert harness.record(CORE, run) == recording
