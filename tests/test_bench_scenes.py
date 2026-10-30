"""The test bench's scenes in screen mode (scripts/bench/scenes.py, #168): the
expected colour per side, from the scene image through the helper's 64x36
downscale and the colour engine's average of a light's channels.

Pillow and Glowcast's core only; the screen window and the camera are not
part of CI.
"""

import sys
from pathlib import Path

import pytest
from PIL import Image

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts" / "bench"))

import scenes  # noqa: E402
import suite  # noqa: E402

from custom_components.glowcast.core import color_engine  # noqa: E402
from custom_components.glowcast.core.models import Frame  # noqa: E402

# The bench monitor's physical resolution, and a smaller one.
SIZES = [(3840, 2160), (1920, 1080)]
# Where a light can stand on its side: (x, z) of its channels.
LEFT = [[(-0.69, 0.0)], [(-1.0, 0.5)], [(-0.45, -0.5)], [(-0.9, 0.0), (-0.6, 0.0)]]
RIGHT = [[(0.70, 0.0)]] + [[(-x, z) for x, z in p] for p in LEFT[1:]]


def _frame(name: str, size: tuple[int, int] = SIZES[0]) -> bytes:
    return scenes.downscale(scenes.render(name, *size))


def test_downscale_is_the_helpers_64x36() -> None:
    image = Image.new("RGB", (3840, 2160), (255, 0, 0))
    image.paste((0, 0, 255), (1920, 0, 3840, 2160))

    frame = scenes.downscale(image)

    assert len(frame) == 64 * 36 * 3
    row = [tuple(frame[i : i + 3]) for i in range(0, 64 * 3, 3)]
    assert row[:32] == [(255, 0, 0)] * 32
    assert row[32:] == [(0, 0, 255)] * 32


def test_expected_is_the_colour_engine_on_the_lights_channels() -> None:
    image = Image.new("RGB", (1920, 1080), (200, 0, 0))
    image.paste((0, 0, 100), (960, 0, 1920, 1080))
    frame = scenes.downscale(image)

    assert scenes.expected(frame, [(-0.7, 0.0)]) == (200.0, 0.0, 0.0)
    assert scenes.expected(frame, [(0.7, 0.0)]) == (0.0, 0.0, 100.0)
    # A gradient strip: the mean of its channels.
    assert scenes.expected(frame, [(-0.7, 0.0), (0.7, 0.0)]) == (100.0, 0.0, 50.0)


@pytest.mark.parametrize(
    ("rgb", "colour"),
    [
        ((240, 30, 70), "red"),
        ((15, 206, 112), "green"),
        ((20, 36, 179), "blue"),
        ((233, 236, 244), "white"),
        ((255, 200, 0), None),  # yellow: neither one channel nor white
        ((0, 0, 0), None),
    ],
)
def test_bucket(rgb: tuple[int, int, int], colour: str | None) -> None:
    assert scenes.bucket(rgb) == colour


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("name", list(scenes.SCENES))
def test_each_side_of_a_scene_is_clearly_one_colour(
    name: str, size: tuple[int, int]
) -> None:
    frame = _frame(name, size)

    left = {scenes.bucket(scenes.expected(frame, p)) for p in LEFT}
    right = {scenes.bucket(scenes.expected(frame, p)) for p in RIGHT}

    assert len(left) == 1 and None not in left
    assert len(right) == 1 and None not in right
    assert left != right


def test_the_bench_lights_get_the_colours_of_their_sides() -> None:
    # The bench area's lights since the calibration of #184: x -0.69 and +0.70.
    seen = {
        name: (
            scenes.bucket(scenes.expected(_frame(name), [(-0.69, 0.0)])),
            scenes.bucket(scenes.expected(_frame(name), [(0.70, 0.0)])),
        )
        for name in scenes.SCENES
    }
    assert seen == {
        "spectrum": ("red", "blue"),
        "aurora": ("blue", "green"),
        "neon": ("green", "red"),
        "moonlight": ("blue", "white"),
        "sunset": ("red", "blue"),
        "ocean": ("blue", "green"),
        "golden-hour": ("green", "red"),
    }


def test_every_light_changes_colour_at_every_scene() -> None:
    """The delay is measured on a change: no light may keep its colour from
    one scene to the next (the glow scene in between holds the colours)."""
    order = [s for s, _, kind in suite.SCREEN_SEQUENCE if kind == "scene"]
    for positions in (LEFT[0], RIGHT[0]):
        colours = [scenes.bucket(scenes.expected(_frame(s), positions)) for s in order]
        assert all(a != b for a, b in zip(colours, colours[1:], strict=False))
    assert set(order) == set(scenes.SCENES)


@pytest.mark.parametrize("size", SIZES)
def test_the_glow_scene_is_black_to_glowcast_but_not_all_zero(
    size: tuple[int, int],
) -> None:
    image = scenes.render(scenes.GLOW, *size)
    frame = Frame(64, 36, scenes.downscale(image))

    assert max(hi for _, hi in image.getextrema()) <= scenes.GLOW_MAX
    assert color_engine.is_black(frame)
    assert not color_engine.is_all_zero(frame)


def test_the_rest_screen_shows_the_logo_at_the_monitors_resolution() -> None:
    image = scenes.render(scenes.REST, 3840, 2160)

    assert image.size == (3840, 2160)
    # Dark corners, the light wordmark in the middle.
    assert max(image.getpixel((10, 10))) < 40
    middle = image.crop((960, 700, 2880, 1100))
    assert max(hi for _, hi in middle.getextrema()) > 200
