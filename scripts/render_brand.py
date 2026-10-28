# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "fonttools==4.68.0",
#     "uharfbuzz==0.57.0",
#     "resvg-py==0.5.0",
#     "pyoxipng==9.1.1",
#     "pillow==13.0.0",
# ]
# ///
"""Build Glowcast's brand images from the sources in docs/brand/.

With --font, first rebuilds docs/brand/logo.svg and logo-dark.svg: the icon
followed by the wordmark "Glowcast" in Outfit SemiBold, converted to outlines.
Then renders the PNGs in custom_components/glowcast/brand/ with resvg (which
supports the feGaussianBlur glow), checks that each is trimmed, and compresses
them losslessly (interlaced) with oxipng. See docs/brand/README.md.

    uv run scripts/render_brand.py --font "Outfit[wght].ttf"  # wordmark + PNGs
    uv run scripts/render_brand.py                            # PNGs only
"""

import argparse
import io
import math
import re
from pathlib import Path

import oxipng
import resvg_py
import uharfbuzz as hb
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "docs" / "brand"
OUTPUT = ROOT / "custom_components" / "glowcast" / "brand"

ICON = 512  # icon size in SVG units
GAP = ICON / 4  # icon to the ink of the "G"
FONT_SIZE = ICON * 0.7  # 56 px text next to an 80 px icon in the approved design
WEIGHT = 600  # SemiBold
TRACKING = -0.01  # letter spacing in em
WORDMARK = "Glowcast"
TEXT_COLOURS = {"logo.svg": "#101218", "logo-dark.svg": "#F4F1EA"}

# (source, output, height in px of the PNG)
RENDERS = [
    ("icon.svg", "icon.png", 256),
    ("icon.svg", "icon@2x.png", 512),
    ("logo.svg", "logo.png", 256),
    ("logo.svg", "logo@2x.png", 512),
    ("logo-dark.svg", "dark_logo.png", 256),
    ("logo-dark.svg", "dark_logo@2x.png", 512),
]


def wordmark(font_path: Path) -> tuple[str, float]:
    """Return the wordmark as one SVG path placed after the icon, and the logo width."""
    data = font_path.read_bytes()
    face = hb.Face(data)
    hb_font = hb.Font(face)
    hb_font.set_variations({"wght": WEIGHT})
    buf = hb.Buffer()
    buf.add_str(WORDMARK)
    buf.guess_segment_properties()
    hb.shape(hb_font, buf, {"kern": True, "liga": True})

    font = instantiateVariableFont(TTFont(io.BytesIO(data)), {"wght": WEIGHT})
    upem = font["head"].unitsPerEm
    glyphs = font.getGlyphSet()
    order = font.getGlyphOrder()
    scale = FONT_SIZE / upem
    # Centre the cap height on the icon's vertical centre.
    baseline = ICON / 2 + font["OS/2"].sCapHeight * scale / 2

    def draw(pen_for_glyph) -> None:
        x = 0.0
        last = len(buf.glyph_infos) - 1
        for i, (info, pos) in enumerate(
            zip(buf.glyph_infos, buf.glyph_positions, strict=True)
        ):
            name = order[info.codepoint]
            glyphs[name].draw(pen_for_glyph(x + pos.x_offset))
            x += pos.x_advance
            if i < last:
                x += TRACKING * upem

    bounds = BoundsPen(glyphs)
    draw(lambda x: TransformPen(bounds, (1, 0, 0, 1, x, 0)))
    x_min, _, x_max, _ = bounds.bounds
    shift = ICON + GAP - x_min * scale

    path = SVGPathPen(glyphs, ntos=lambda v: f"{round(v, 2):g}")
    draw(
        lambda x: TransformPen(path, (scale, 0, 0, -scale, shift + x * scale, baseline))
    )
    return path.getCommands(), ICON + GAP + (x_max - x_min) * scale


def build_logos(font_path: Path) -> None:
    icon = (SOURCES / "icon.svg").read_text()
    inner = re.search(r"<svg[^>]*>\n(.*)</svg>", icon, re.S).group(1)
    commands, width = wordmark(font_path)
    width = math.ceil(width)
    for name, colour in TEXT_COLOURS.items():
        (SOURCES / name).write_text(
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {ICON}">\n'
            f"{inner}"
            f'  <path fill="{colour}" d="{commands}"/>\n'
            "</svg>\n"
        )
        print(f"{name}: {width} x {ICON}")


def render_pngs() -> None:
    OUTPUT.mkdir(exist_ok=True)
    for source, output, height in RENDERS:
        png = bytes(
            resvg_py.svg_to_bytes(
                svg_path=str(SOURCES / source), height=height, skip_system_fonts=True
            )
        )
        png = oxipng.optimize_from_memory(
            png,
            level=6,
            interlace=oxipng.Interlacing.Adam7,
            strip=oxipng.StripChunks.safe(),
            force=True,  # keep the interlaced result, though it is larger
        )
        image = Image.open(io.BytesIO(png))
        if image.mode != "RGBA" or image.height != height:
            raise SystemExit(f"{output}: unexpected {image.mode} {image.size}")
        if image.getchannel("A").getbbox() != (0, 0, *image.size):
            raise SystemExit(f"{output}: not trimmed")
        (OUTPUT / output).write_bytes(png)
        print(f"{output}: {image.width} x {image.height}, {len(png) / 1000:.1f} kB")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--font", type=Path, help="Outfit[wght].ttf from google/fonts")
    args = parser.parse_args()
    if args.font:
        build_logos(args.font)
    render_pngs()


if __name__ == "__main__":
    main()
