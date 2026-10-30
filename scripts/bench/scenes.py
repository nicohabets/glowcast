"""The bench's pictures in screen mode (#168): Glowcast's rest screen and
vivid scenes with a different colour on each half.

Pure: Pillow and Glowcast's core only (no Home Assistant, no OpenCV, no
numpy), so the unit tests (`tests/test_bench_scenes.py`) run it in CI. The
run renders every picture at the monitor's physical resolution into PNGs that
the screen window (`win/screen.py`) only shows, so the picture on the monitor
is exactly the image the expected colours are computed from.

The expected colour of a light is what Glowcast makes of the scene: the image
shrunk to 64x36 as the screen helper does (`downscale`, a copy of
`glowcast_screen.capture.downscale`), then the colour engine's average over
the region of each of the light's channels (their x and z from the bridge),
the mean of those channels. The camera judges a colour by its dominant
channel (`report.classify`), so `bucket` turns the expected colour into red,
green, blue or white; every scene is made so that each side is clearly one of
them, and the two sides differ.
"""

from __future__ import annotations

import dataclasses
import math
import sys
from collections.abc import Sequence
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
from custom_components.glowcast.core import color_engine  # noqa: E402
from custom_components.glowcast.core.models import Channel, Frame  # noqa: E402

RGB = tuple[float, float, float]
Colour = tuple[int, int, int]

WIDTH, HEIGHT = 64, 36  # the helper's frame (glowcast_screen.capture)
# The scenes are drawn at this size and scaled up smoothly to the monitor.
DESIGN = (320, 180)
LOGO = REPO / "custom_components" / "glowcast" / "brand" / "dark_logo@2x.png"
REST = "rest"
GLOW = "glow"
# Brand colours (docs/brand/README.md).
TILE = (16, 18, 24)
SEGMENTS = [
    (61, 123, 255),
    (106, 108, 245),
    (176, 92, 200),
    (234, 90, 166),
    (255, 143, 106),
    (255, 195, 122),
]
# The glow scene: near-black and a dimmed logo, every pixel at most this, so
# Glowcast takes it as black (core BLACK_LEVEL) but it is not all zero.
GLOW_MAX = color_engine.BLACK_LEVEL - 4
GLOW_BACKGROUND = (16, 16, 16)
# A side is one colour to the camera when its strongest channel is at least
# this many times the next (white: all channels within WHITE_RATIO). 2.5, not
# less: the camera's warm white reference lifts blue, so a pink (227, 21, 116)
# was seen blue and a green with blue at half was close (bench, #168).
CLEAR = 2.5
WHITE_RATIO = 1.25


@dataclasses.dataclass(frozen=True)
class Scene:
    """Two halves, each a vertical gradient (top, bottom) with a soft light
    in it; the halves blend over a narrow seam in the middle."""

    name: str
    left: tuple[Colour, Colour]
    right: tuple[Colour, Colour]
    # Where the soft light sits in each half (x 0..1 in the half, y 0..1).
    light: tuple[float, float] = (0.5, 0.35)


SCENES: dict[str, Scene] = {
    s.name: s
    for s in (
        # A prism: warm red on the left, blue-violet on the right.
        Scene(
            "spectrum", ((255, 40, 50), (225, 20, 90)), ((40, 70, 255), (90, 30, 230))
        ),
        # Violet sky over green light.
        Scene(
            "aurora",
            ((70, 40, 255), (25, 20, 150)),
            ((30, 255, 80), (0, 160, 55)),
            (0.6, 0.3),
        ),
        # Neon tubes: acid green and a hot red.
        Scene("neon", ((40, 255, 70), (10, 170, 60)), ((255, 30, 75), (200, 10, 60))),
        # Night sky and moonlight: deep blue and a cool white.
        Scene(
            "moonlight",
            ((30, 50, 230), (10, 20, 120)),
            ((250, 250, 255), (215, 220, 232)),
            (0.5, 0.25),
        ),
        # A low sun and an evening sky.
        Scene(
            "sunset",
            ((255, 85, 20), (230, 40, 50)),
            ((90, 50, 255), (40, 20, 150)),
            (0.5, 0.55),
        ),
        # Deep water and a green shallow.
        Scene("ocean", ((10, 80, 255), (0, 30, 140)), ((20, 245, 75), (0, 160, 55))),
        # A meadow and an amber sun.
        Scene(
            "golden-hour",
            ((90, 230, 40), (40, 150, 20)),
            ((255, 110, 0), (220, 60, 10)),
            (0.5, 0.4),
        ),
    )
}


def _lerp(a: Sequence[float], b: Sequence[float], t: float) -> list[float]:
    return [x + (y - x) * t for x, y in zip(a, b, strict=True)]


def _half(
    top: Colour, bottom: Colour, light: tuple[float, float], u: float, v: float
) -> list[float]:
    colour = _lerp(top, bottom, v)
    # A soft light: brighter (towards white by at most 15 %) near its centre,
    # kept in the same hue so the side stays one colour.
    d = math.hypot((u - light[0]) * 1.6, v - light[1])
    lift = 0.15 * math.exp(-(d * d) / 0.08)
    return _lerp(colour, [255 * c / max(max(colour), 1) for c in colour], lift)


def _design(scene: Scene) -> Image.Image:
    w, h = DESIGN
    seam = 0.04  # the halves blend over 4 % of the width around the middle
    data: list[tuple[int, int, int]] = []
    for row in range(h):
        v = (row + 0.5) / h
        for col in range(w):
            x = (col + 0.5) / w
            left = _half(*scene.left, scene.light, x * 2, v)
            right = _half(*scene.right, scene.light, x * 2 - 1, v)
            t = min(max((x - 0.5 + seam / 2) / seam, 0.0), 1.0)
            data.append(tuple(round(c) for c in _lerp(left, right, t)))  # type: ignore[misc]
    image = Image.new("RGB", DESIGN)
    image.putdata(data)
    return image


def _background(size: tuple[int, int], glow: bool) -> Image.Image:
    """The rest screen's dark tile with a soft band of the brand's segment
    colours low in the picture; for the glow scene only near-black."""
    if glow:
        return Image.new("RGB", size, GLOW_BACKGROUND)
    w, h = DESIGN
    data: list[tuple[int, int, int]] = []
    for row in range(h):
        v = (row + 0.5) / h
        for col in range(w):
            u = (col + 0.5) / w
            # The segment colours from blue on the left to warm on the right.
            p = u * (len(SEGMENTS) - 1)
            i = min(int(p), len(SEGMENTS) - 2)
            segment = _lerp(SEGMENTS[i], SEGMENTS[i + 1], p - i)
            band = 0.22 * math.exp(-((v - 0.86) ** 2) / 0.006)
            vignette = 1 - 0.35 * math.hypot(u - 0.5, v - 0.45)
            base = [c * vignette for c in TILE]
            data.append(tuple(round(c) for c in _lerp(base, segment, band)))  # type: ignore[misc]
    image = Image.new("RGB", DESIGN)
    image.putdata(data)
    return image.resize(size, Image.Resampling.BICUBIC)


def _with_logo(background: Image.Image, dim: float) -> Image.Image:
    """The wordmark logo for dark backgrounds, centred, 55 % of the width."""
    logo = Image.open(LOGO).convert("RGBA")
    width = round(background.width * 0.55)
    logo = logo.resize(
        (width, round(logo.height * width / logo.width)), Image.Resampling.LANCZOS
    )
    if dim < 1:
        r, g, b, a = logo.split()
        r, g, b = (c.point(lambda v: round(v * dim)) for c in (r, g, b))
        logo = Image.merge("RGBA", (r, g, b, a))
    canvas = background.convert("RGBA")
    canvas.alpha_composite(
        logo,
        (
            (background.width - logo.width) // 2,
            round(background.height * 0.42 - logo.height / 2),
        ),
    )
    return canvas.convert("RGB")


def render(name: str, width: int, height: int) -> Image.Image:
    """A picture at the monitor's physical resolution: the rest screen, the
    glow scene or one of SCENES."""
    if name == REST:
        return _with_logo(_background((width, height), glow=False), 1.0)
    if name == GLOW:
        image = _with_logo(_background((width, height), glow=True), GLOW_MAX / 255)
        # Rounding and scaling never lift a pixel above GLOW_MAX.
        return image.point(lambda v: min(v, GLOW_MAX))
    return _design(SCENES[name]).resize((width, height), Image.Resampling.BICUBIC)


def downscale(image: Image.Image) -> bytes:
    """64x36 RGB as the screen helper makes it (glowcast_screen.capture.downscale):
    reduce() by an integer factor first, then a box filter."""
    factor = max(1, min(image.width // (WIDTH * 4), image.height // (HEIGHT * 4)))
    if factor > 1:
        image = image.reduce(factor)
    return image.resize((WIDTH, HEIGHT), Image.Resampling.BOX).tobytes()


def expected(frame: bytes, positions: Sequence[tuple[float, float]]) -> RGB:
    """The colour Glowcast gives a light (0-255): the colour engine on the
    64x36 frame, for each of the light's channels (x, z), averaged."""
    channels = [Channel(str(i), "", x, z) for i, (x, z) in enumerate(positions)]
    colours = color_engine.compute(Frame(WIDTH, HEIGHT, frame), channels, 1.0)
    return tuple(  # type: ignore[return-value]
        round(255 * sum(c.rgb[i] for c in colours) / len(colours), 1) for i in range(3)
    )


def bucket(rgb: Sequence[float]) -> str | None:
    """red, green, blue or white, as the camera judges a light; None when the
    colour is neither clearly one channel nor white."""
    ordered = sorted(rgb[:3], reverse=True)
    if ordered[0] <= 0:
        return None
    if ordered[0] / max(ordered[2], 1.0) < WHITE_RATIO:
        return "white"
    if ordered[0] / max(ordered[1], 1.0) < CLEAR:
        return None
    return ("red", "green", "blue")[list(rgb[:3]).index(ordered[0])]


def largest_difference(a: Sequence[float], b: Sequence[float]) -> float:
    return max(abs(x - y) for x, y in zip(a[:3], b[:3], strict=True))
