"""The bench pictures for #265 and #241 (scripts/bench/spike_scenes.py), as the
screen helper's 64x36 frame and Glowcast's core see them.

Pillow and Glowcast's core only; the screen window and the camera are not
part of CI.
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts" / "bench"))

import scenes  # noqa: E402
import spike_scenes  # noqa: E402

from custom_components.glowcast.core import letterbox  # noqa: E402
from custom_components.glowcast.core.color_engine import (  # noqa: E402
    compute,
    is_all_zero,
    is_black,
)
from custom_components.glowcast.core.models import Channel, Frame  # noqa: E402

SIZE = (1920, 1080)


def frame(image) -> Frame:
    return Frame(64, 36, scenes.downscale(image))


def test_sparks_flip_a_lights_colour_between_black_and_lit() -> None:
    """The worst case for #265: a light left of the picture gets black in
    some pictures and a colour in others, while the whole frame never counts
    as black (that would hold the colours instead)."""
    left = [Channel("left", "", -0.7, 0.6)]
    frames = [frame(spike_scenes.sparks(seed, SIZE)) for seed in range(16)]
    colours = [compute(f, left, 1.0)[0].rgb for f in frames]
    assert (0.0, 0.0, 0.0) in colours
    assert any(max(c) > 0.3 for c in colours)
    assert not any(is_black(f) for f in frames)
    assert not any(is_all_zero(f) for f in frames)


@pytest.mark.parametrize("make", [spike_scenes.starfield, spike_scenes.fireworks])
def test_busy_scenes_are_dark_but_not_empty(make) -> None:
    for seed in range(4):
        f = frame(make(seed, SIZE))
        assert not is_all_zero(f)
        assert max(f.pixels) > 40  # small sparks, averaged into 64x36 cells


def test_the_same_seed_gives_the_same_picture() -> None:
    a = spike_scenes.fireworks(3, SIZE).tobytes()
    assert a == spike_scenes.fireworks(3, SIZE).tobytes()
    assert a != spike_scenes.fireworks(4, SIZE).tobytes()


def test_the_control_film_has_bars_top_and_bottom() -> None:
    top, bottom, left, right = letterbox.measure(frame(spike_scenes.film(SIZE)))
    assert top in (4, 5)
    assert bottom in (4, 5)
    assert left == 0
    assert right == 0


@pytest.mark.parametrize(
    ("make", "rows"),
    [(spike_scenes.night_sky, (3, 4)), (spike_scenes.menu_bar, (1, 2))],
)
def test_a_dark_top_band_reads_as_a_bar(make, rows) -> None:
    """What #241 is about: one frame of a dark band near the top measures as
    a top bar, and the bottom has none."""
    top, bottom, _, _ = letterbox.measure(frame(make(SIZE)))
    assert top in rows
    assert bottom == 0


def test_the_neutral_picture_has_no_bars() -> None:
    assert letterbox.measure(frame(spike_scenes.gradient(SIZE))) == (0, 0, 0, 0)


def test_the_vignette_is_dark_at_the_edges_only() -> None:
    f = frame(spike_scenes.vignette(SIZE))
    assert not is_black(f)
    middle = (18 * 64 + 32) * 3
    assert max(f.pixels[middle : middle + 3]) > 100
    assert max(f.pixels[0:3]) < 20
