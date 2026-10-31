"""Pictures for bench checks of the cut fade on busy content (#265) and of
dark bands near an edge in the letterbox memory (#241).

Pure: Pillow only, made in code (no clips), so `tests/test_bench_spike_scenes.py`
runs them in CI. Each function returns a picture of the given size for the
bench's full-screen window (`win/screen.py`); the screen helper shrinks it to
64x36.

#265, busy content on a dark background, a new picture several times per
second (pass a different `seed`):
- `sparks`: the synthetic worst case. Single bright pixels of the helper's
  64x36 frame on black, with a lit share drawn from 2-8 % per picture, so the
  share of each light's region (and of the whole frame) hovers around the
  colour engine's 5 % threshold for black and flips from picture to picture.
- `starfield`: the same stars every picture, each twinkling.
- `fireworks`: one to three bursts of coloured sparks on a night sky.

#241, a lit picture that changes colour from top to bottom, so a light's
region that moves up or down changes colour:
- `gradient`: the neutral picture (no dark band);
- `night_sky`: a dark band over the top 10 %;
- `vignette`: dark edges and corners around a lit centre;
- `menu_bar`: a computer screen, a dark menu bar over the top 4 %;
- `film`: the control, a 2.39:1 picture with real black bars.
"""

from __future__ import annotations

import math
import random

from PIL import Image, ImageDraw, ImageFilter

FRAME = (64, 36)  # the helper's frame (glowcast_screen.capture)
SPARK_COLOURS = [(255, 240, 200), (255, 80, 40), (90, 160, 255)]
STAR_COLOURS = [(255, 255, 255), (200, 220, 255), (255, 230, 190)]
FIREWORK_COLOURS = [
    (255, 60, 60),
    (60, 255, 120),
    (255, 200, 40),
    (120, 120, 255),
    (255, 80, 220),
]
# Dark, but not all zero (an all-zero frame is "no picture", #68).
NIGHT = (8, 8, 8)
TOP = (255, 40, 160)
BOTTOM = (40, 120, 255)


def sparks(seed: int, size: tuple[int, int]) -> Image.Image:
    """Bright single pixels of the 64x36 frame on black, a lit share of 2-8 %."""
    rnd = random.Random(seed)
    share = rnd.uniform(0.02, 0.08)
    pixels = [
        rnd.choice(SPARK_COLOURS) if rnd.random() < share else (0, 0, 0)
        for _ in range(FRAME[0] * FRAME[1])
    ]
    small = Image.new("RGB", FRAME)
    small.putdata(pixels)
    return small.resize(size, Image.Resampling.NEAREST)


def starfield(seed: int, size: tuple[int, int]) -> Image.Image:
    """The same stars every picture; `seed` sets how bright each one twinkles."""
    w, h = size
    stars = random.Random(1)
    twinkle = random.Random(seed)
    image = Image.new("RGB", size, (2, 3, 8))
    draw = ImageDraw.Draw(image)
    unit = w / 3840
    for _ in range(260):
        x, y = stars.randrange(w), stars.randrange(h)
        base = stars.uniform(0.3, 1.0)
        radius = stars.choice([6, 8, 10, 14, 22]) * unit
        colour = stars.choice(STAR_COLOURS)
        level = base * twinkle.uniform(0.35, 1.0)
        draw.ellipse(
            [x - radius, y - radius, x + radius, y + radius],
            fill=tuple(int(level * c) for c in colour),
        )
    return image.filter(ImageFilter.GaussianBlur(3 * unit))


def fireworks(seed: int, size: tuple[int, int]) -> Image.Image:
    """One to three bursts of coloured sparks on a night sky."""
    w, h = size
    rnd = random.Random(seed)
    unit = w / 3840
    image = Image.new("RGB", size, (4, 4, 10))
    draw = ImageDraw.Draw(image)
    for _ in range(rnd.randint(1, 3)):
        cx = rnd.uniform(0.1, 0.9) * w
        cy = rnd.uniform(0.1, 0.65) * h
        radius = rnd.uniform(150, 600) * unit
        colour = rnd.choice(FIREWORK_COLOURS)
        fade = rnd.uniform(0.4, 1.0)
        for _ in range(140):
            angle = rnd.uniform(0, 2 * math.pi)
            r = radius * rnd.uniform(0.6, 1.0)
            x, y = cx + r * math.cos(angle), cy + r * math.sin(angle)
            s = rnd.choice([5, 7, 9]) * unit
            draw.ellipse(
                [x - s, y - s, x + s, y + s], fill=tuple(int(fade * c) for c in colour)
            )
    return image.filter(ImageFilter.GaussianBlur(2 * unit))


def gradient(
    size: tuple[int, int],
    top: tuple[int, int, int] = TOP,
    bottom: tuple[int, int, int] = BOTTOM,
) -> Image.Image:
    """A lit picture whose colour changes from top to bottom."""
    w, h = size
    column = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / (h - 1)
        column.putpixel(
            (0, y),
            tuple(int(a + (b - a) * t) for a, b in zip(top, bottom, strict=True)),
        )
    return column.resize(size)


def night_sky(size: tuple[int, int]) -> Image.Image:
    """The gradient with a dark band over the top 10 %."""
    image = gradient(size)
    ImageDraw.Draw(image).rectangle([0, 0, size[0], int(size[1] * 0.10)], fill=NIGHT)
    return image


def vignette(size: tuple[int, int]) -> Image.Image:
    """The gradient in a lit oval, dark towards the edges and corners."""
    w, h = size
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).ellipse([w * 0.12, h * 0.12, w * 0.88, h * 0.88], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(120 * w / 3840))
    return Image.composite(gradient(size), Image.new("RGB", size, (6, 6, 6)), mask)


def menu_bar(size: tuple[int, int]) -> Image.Image:
    """A light window on a blue desktop, with a dark menu bar over the top 4 %."""
    w, h = size
    unit = w / 3840
    image = Image.new("RGB", size, (40, 110, 200))
    draw = ImageDraw.Draw(image)
    draw.rectangle(
        [300 * unit, 300 * unit, w - 300 * unit, h - 200 * unit], fill=(235, 235, 230)
    )
    draw.rectangle(
        [300 * unit, 300 * unit, w - 300 * unit, 420 * unit], fill=(200, 70, 60)
    )
    draw.rectangle([0, 0, w, int(h * 0.04)], fill=(22, 22, 24))
    return image


def film(size: tuple[int, int]) -> Image.Image:
    """The control: a 2.39:1 picture between real black bars."""
    w, h = size
    image = gradient(size, top=(250, 150, 40), bottom=(40, 200, 120))
    bar = int((h - w / 2.39) / 2)
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, w, bar], fill=(0, 0, 0))
    draw.rectangle([0, h - bar, w, h], fill=(0, 0, 0))
    return image
