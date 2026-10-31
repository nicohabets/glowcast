"""Draws what Glowcast sends to the lights in the illustrated living room.

Run through `scripts/demo.py room [--compare]`, in the demo's environment
(resvg-py, Pillow, imageio-ffmpeg). room_harness.py runs the scenarios through
`core/`; this draws its recording: the TV shows the picture Glowcast got (the
64x36 frame, scaled up without smoothing), and each light shows the colour and
brightness sent to its channel (room_harness.CHANNELS). A light that is
released or never claimed is drawn off.

The room is the one of the illustrations in docs/images/illustrations (#135,
style "S1b Dusk, deeper"): the same camera, colours and transparent
background.

With --compare, the same harness also runs against the `core/` of the merge
base with `main`, in a temporary worktree, and both rooms are drawn side by
side, labelled "main" and "this PR".

--lights picks the output the room's lights belong to (room_harness.py): an
entertainment area (the default), Home Assistant lights, or both combined. --place
moves a Home Assistant light, and --versus draws a second room beside the first with
other places, labelled with them (the place comparison). --liveliness runs the
scenarios at a level (#16), and --liveliness all draws the three levels side by
side, labelled Calm, Normal and Lively. A Home Assistant light that
is unavailable, and a part of a combined output that is dropped, are named at
the bottom of the room.

Files in --out: room.gif (all scenarios), and per scenario room-<n>.gif and
room-<n>.png, a still at the moment that shows it best.
"""

import argparse
import base64
import io
import json
import shutil
import signal
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("room_harness.py")
CORE = Path("custom_components") / "glowcast" / "core"

ROOM_WIDTH = 800  # px per room
BAND = 64  # px above the rooms for the scenario's name
MAX_GIF_BYTES = 10_000_000  # GitHub's limit for an attachment
SEND_HZ = 25  # the harness samples every send tick
# Render every second send tick (12.5 frames per second); fewer only when the
# GIF would be over MAX_GIF_BYTES.
STEPS = (2, 3, 4)

# --- The room (ported from the illustrations' scene.py and series.py) -------

C, S = 0.8660254, 0.5
W, D, H = 600, 500, 300  # floor x, floor y, wall height
VIEWBOX = (-470, -330, 1020, 900)
# Plane transforms: draw 2D shapes in plane coordinates.
WALL_A = f"matrix({C} {S} 0 -1 0 0)"  # y=0 wall, coords (x, z)
WALL_B = f"matrix({-C} {S} 0 -1 0 0)"  # x=0 wall, coords (y, z)
FLOOR = f"matrix({C} {S} {-C} {S} 0 0)"  # z=0 floor, coords (x, y)
# Style "S1b Dusk, deeper".
WALL_A_FILL, WALL_B_FILL, FLOOR_FILL, RUG = "#3E3858", "#352F4C", "#4A3B3A", "#55495F"
WOOD = ("#8A6A55", "#735746", "#5F483A")
SOFA = ("#6C7598", "#5A6386", "#4D5576")
METAL, TV_FRAME, CAP = "#24212F", "#15141C", "#6E6890"
PLANT = ("#5E8A68", "#4B7155")
SLAB = ("#2E2A3E", "#262234")
GLOW_STRENGTH = 1.3
# A light that is off (scene 5 of #135); the console's strip when off (Room v2).
STRIP_OFF, LAMP_OFF, LAMP_TOP_OFF = "#4A4560", "#7A7390", "#8E88A3"
CONSOLE_STRIP_OFF = "#2A2638"
# The TV screen in wall-A plane coordinates.
SCREEN_X0, SCREEN_X1, SCREEN_Z0, SCREEN_Z1 = 236, 444, 116, 230

# The labels: a pill as in the illustrations' series.py.
LABEL_FILL, TITLE_FILL, LABEL_TEXT = CAP, "#2E2A3E", "#FFFFFF"
# Behind the rooms in the GIF (the stills stay transparent): a GIF has no
# partial transparency, and with a transparent background each frame is stored
# whole, five times the size. GitHub's light background colour.
GIF_BACKGROUND = "#F6F8FA"
# The lines at the bottom of a room: an unavailable light, a dropped part.
NOTE_SIZE, NOTE_FILL = 20, "#8A3A4A"
LIGHT_NAMES = {
    "floor_lamp": "floor lamp",
    "light_bar": "light bar",
    "wall_light": "wall light",
    "console_lamp": "orb lamp",
    "console_strip": "light strip",
    "table_lamp": "table lamp",
}
# Where --names puts each Home Assistant light's name and place: a point in the room
# (x, y, z), as the tags of Room v2 (#196).
NAME_TAGS = {
    "floor_lamp": (130, 60, 262),
    "light_bar": (600, 150, 240),
    "wall_light": (396, 6, 300),
    "console_lamp": (356, 29, 92),
    "console_strip": (300, 110, -6),
    "table_lamp": (116, 430, 150),
}
TAG_SIZE, TAG_FILL = 16, "#2A2638"
PART_NAMES = {
    "ha_lights": "Home Assistant lights",
    "hue_entertainment": "Entertainment area",
}
# As in room_harness.py, which runs in an interpreter of its own.
LIGHTS = ("area", "ha", "combined")
EXIT_NO_OUTPUT = 3
# How a side is labelled when it is the only one, per --lights.
MODE_LABELS = {
    "ha": "Home Assistant lights",
    "combined": "Area + Home Assistant lights",
}
# As in room_harness.py: the liveliness levels (#16).
LEVELS = ("calm", "normal", "lively")
NORMAL = "normal"
ALL_LEVELS = "all"

Light = tuple[str, float] | None  # (colour at full brightness, strength 0-1)


def _p(x: float, y: float, z: float = 0) -> tuple[float, float]:
    return (x - y) * C, (x + y) * S - z


def _poly(points: list[tuple[float, float, float]], fill: str) -> str:
    d = " ".join(f"{a:.1f},{b:.1f}" for a, b in (_p(*p) for p in points))
    return f'<polygon points="{d}" fill="{fill}"/>'


def _box(
    x0: float,
    x1: float,
    y0: float,
    y1: float,
    z0: float,
    z1: float,
    fills: tuple[str, str, str],
) -> str:
    """The visible faces: +y (left-front), +x (right-front), top."""
    top, left, right = fills
    return "".join(
        [
            _poly([(x0, y1, z0), (x1, y1, z0), (x1, y1, z1), (x0, y1, z1)], left),
            _poly([(x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)], right),
            _poly([(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)], top),
        ]
    )


def _mix(a: str, b: str, t: float) -> str:
    """Hex colour a, moved towards b by t (0-1)."""
    ca = [int(a[i : i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i : i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(
        f"{round(x + (y - x) * t):02X}" for x, y in zip(ca, cb, strict=True)
    )


def light(rgb: list[float] | None) -> Light:
    """A channel's colour as drawn: its hue at full brightness, and a strength.

    The strength is the brightest component with a display gamma (level **
    (1 / 2.2)), so Glowcast's dim glow (level 0.1) shows as a soft glow, about
    as strong as in scene 2 of #135. Black, or no colour at all, is off.
    """
    if not rgb or (level := max(rgb)) <= 0:
        return None
    colour = "#" + "".join(f"{round(255 * c / level):02X}" for c in rgb)
    return colour, round(level ** (1 / 2.2), 3)


def room_svg(lights: dict[str, Light], screen: str | None) -> str:
    """The room with these lights (by room_harness.CHANNELS id) and the TV
    showing `screen` (a PNG data URI of the frame), or off."""
    defs: list[str] = []
    back: list[str] = []
    mid: list[str] = []
    front: list[str] = []

    def glow(
        plane: str, name: str, cx: float, cy: float, r: float, id_: str, op: float
    ) -> None:
        if (lit := lights.get(id_)) is None:
            return
        colour, strength = lit
        opacity = min(1, op * GLOW_STRENGTH * strength)
        defs.append(
            f'<radialGradient id="g{name}"><stop offset="0" stop-color="{colour}"/>'
            f'<stop offset="0.45" stop-color="{colour}" stop-opacity="0.5"/>'
            f'<stop offset="1" stop-color="{colour}" stop-opacity="0"/>'
            "</radialGradient>"
        )
        clip = {WALL_A: "cA", WALL_B: "cB", FLOOR: "cF"}[plane]
        back.append(
            f'<g transform="{plane}" clip-path="url(#{clip})" '
            f'style="mix-blend-mode:screen"><circle cx="{cx}" cy="{cy}" r="{r}" '
            f'fill="url(#g{name})" opacity="{opacity:.3f}"/></g>'
        )

    def fill(id_: str, off: str) -> str:
        """A lamp's own colour: towards the light's colour by its strength."""
        if (lit := lights.get(id_)) is None:
            return off
        colour, strength = lit
        return _mix(off, colour, strength)

    defs.append(
        f'<clipPath id="cA"><rect x="0" y="0" width="{W}" height="{H}"/></clipPath>'
        f'<clipPath id="cB"><rect x="0" y="0" width="{D}" height="{H}"/></clipPath>'
        f'<clipPath id="cF"><rect x="0" y="0" width="{W}" height="{D}"/></clipPath>'
    )
    # The slab under the room, its wall caps and a soft ground shadow.
    t, z = 14, 18
    gx, gy = _p(W / 2, D / 2, -z)
    defs.append(
        '<radialGradient id="sh"><stop offset="0" stop-color="#000" '
        'stop-opacity="0.22"/><stop offset="1" stop-color="#000" '
        'stop-opacity="0"/></radialGradient>'
    )
    back.append(
        f'<ellipse cx="{gx:.1f}" cy="{gy + 40:.1f}" rx="520" ry="150" fill="url(#sh)"/>'
    )
    back.append(_poly([(-t, D, -z), (W, D, -z), (W, D, 0), (-t, D, 0)], SLAB[0]))
    back.append(_poly([(W, -t, -z), (W, D, -z), (W, D, 0), (W, -t, 0)], SLAB[1]))
    back.append(
        _poly(
            [(-t, -t, H), (W, -t, H), (W, 0, H), (0, 0, H), (0, D, H), (-t, D, H)], CAP
        )
    )
    back.append(_poly([(W, -t, -z), (W, 0, -z), (W, 0, H), (W, -t, H)], SLAB[1]))
    back.append(_poly([(-t, D, -z), (0, D, -z), (0, D, H), (-t, D, H)], SLAB[0]))
    # Walls, floor, a print on wall B and the rug.
    back.append(_poly([(0, 0, 0), (W, 0, 0), (W, 0, H), (0, 0, H)], WALL_A_FILL))
    back.append(_poly([(0, 0, 0), (0, D, 0), (0, D, H), (0, 0, H)], WALL_B_FILL))
    back.append(_poly([(0, 0, 0), (W, 0, 0), (W, D, 0), (0, D, 0)], FLOOR_FILL))
    back.append(
        f'<g transform="{WALL_B}"><rect x="170" y="150" width="120" height="80" '
        f'rx="4" fill="{WALL_B_FILL}" stroke="{WOOD[1]}" stroke-width="4"/>'
        f'<path d="M180 160 Q230 210 280 170 L280 220 L180 220 Z" fill="{WOOD[0]}" '
        'opacity="0.5"/></g>'
    )
    back.append(
        f'<g transform="{FLOOR}"><rect x="170" y="130" width="330" height="200" '
        f'rx="18" fill="{RUG}"/></g>'
    )

    # The light on the walls and the floor, each from its light's channel.
    glow(WALL_A, "sl", 270, 175, 210, "left", 0.95)  # strip, left side
    glow(WALL_A, "sr", 410, 175, 210, "right", 0.95)  # strip, right side
    glow(WALL_A, "stl", 280, 250, 130, "top_left", 0.5)  # strip, top
    glow(WALL_A, "st", 340, 250, 130, "top", 0.5)
    glow(WALL_A, "str", 400, 250, 130, "top_right", 0.5)
    glow(WALL_A, "fl", 110, 215, 170, "floor_lamp", 0.75)  # floor lamp, wall A
    glow(WALL_B, "flb", 70, 215, 170, "floor_lamp", 0.6)  # floor lamp, wall B
    glow(WALL_A, "lb", 515, 150, 190, "light_bar", 1.0)  # light bar, wall A
    glow(WALL_A, "lb2", 500, 110, 90, "light_bar", 0.9)  # and close to it
    glow(FLOOR, "ffl", 110, 70, 190, "floor_lamp", 0.55)  # floor pool, lamp
    glow(FLOOR, "flb2", 515, 70, 180, "light_bar", 0.65)  # floor pool, bar
    glow(FLOOR, "ftv", 340, 120, 220, "top", 0.35)  # TV spill on the floor
    # Room v2's Home Assistant lights (#196), as in its series2.py.
    glow(WALL_A, "wlu", 340, 300, 160, "wall_light", 1.0)  # up from the wall light
    glow(WALL_A, "cl", 340, 82, 120, "console_lamp", 0.9)  # the orb lamp
    glow(FLOOR, "cs", 340, 95, 210, "console_strip", 1.0)  # under the console
    glow(FLOOR, "cs2", 250, 80, 120, "console_strip", 0.6)
    glow(FLOOR, "cs3", 430, 80, 120, "console_strip", 0.6)
    glow(WALL_B, "tlw", 430, 120, 160, "table_lamp", 0.75)  # table lamp, wall B
    glow(FLOOR, "tlf", 90, 430, 160, "table_lamp", 0.65)  # and its floor pool

    def faces(id_: str, top: float, left: float) -> tuple[tuple[str, str, str], float]:
        """A light's box faces (top, left, right), whitened towards the top as
        in Room v2, and its strength (0 when off)."""
        if (lit := lights.get(id_)) is None:
            return (STRIP_OFF,) * 3, 0.0
        colour = fill(id_, STRIP_OFF)
        strength = lit[1]
        whiter = [_mix(colour, "#FFFFFF", t * strength) for t in (top, left)]
        return (whiter[0], whiter[1], colour), strength

    def core(points: list[tuple[float, float, float]], opacity: float) -> str:
        """A bright line on a light's face, so it reads as a light."""
        return f'<g opacity="{opacity:.3f}">{_poly(points, "#FFFFFF")}</g>'

    # The strip behind the TV: segments peeking out above it and at its sides.
    top = ["top_left"] * 3 + ["top"] * 2 + ["top_right"] * 3
    segments = [
        f'<rect x="{228 + i * 28}" y="236" width="22" height="5" rx="2.5" '
        f'fill="{fill(id_, STRIP_OFF)}"/>'
        for i, id_ in enumerate(top)
    ]
    segments.append(
        f'<rect x="224" y="118" width="5" height="110" rx="2.5" '
        f'fill="{fill("left", STRIP_OFF)}"/>'
    )
    segments.append(
        f'<rect x="451" y="118" width="5" height="110" rx="2.5" '
        f'fill="{fill("right", STRIP_OFF)}"/>'
    )
    mid.append(f'<g transform="{WALL_A}">{"".join(segments)}</g>')

    # The wall light above the TV.
    wall, strength = faces("wall_light", 0.5, 0.25)
    mid.append(_box(290, 390, 0, 12, 270, 282, wall))
    if strength:
        mid.append(
            core(
                [(296, 12, 274), (384, 12, 274), (384, 12, 278), (296, 12, 278)],
                0.6 * strength,
            )
        )

    # The TV on wall A; the screen sits on its front face (y=8).
    mid.append(_box(228, 452, 0, 8, 108, 238, (TV_FRAME,) * 3))
    mid.append(
        f'<g transform="translate({-8 * C:.2f} {8 * S:.2f}) {WALL_A}">'
        f"{_screen(screen)}</g>"
    )

    # The media console.
    mid.append(_box(200, 480, 0, 56, 0, 52, WOOD))
    mid.append(
        f'<g transform="translate({-56 * C:.2f} {56 * S:.2f}) {WALL_A}">'
        f'<rect x="216" y="14" width="120" height="26" rx="3" fill="{WOOD[2]}" '
        f'opacity="0.6"/><rect x="348" y="14" width="120" height="26" rx="3" '
        f'fill="{WOOD[2]}" opacity="0.6"/></g>'
    )

    # The light strip along the console's front and its right end.
    strip = fill("console_strip", CONSOLE_STRIP_OFF)
    mid.append(_poly([(204, 56, 1), (476, 56, 1), (476, 56, 10), (204, 56, 10)], strip))
    mid.append(_poly([(480, 4, 1), (480, 56, 1), (480, 56, 10), (480, 4, 10)], strip))
    if (lit := lights.get("console_strip")) is not None:
        mid.append(
            core([(204, 56, 4), (476, 56, 4), (476, 56, 7), (204, 56, 7)], 0.6 * lit[1])
        )
    # The orb lamp on the console.
    mid.append(_box(330, 352, 18, 40, 52, 58, (METAL,) * 3))
    ox, oy = _p(341, 29, 78)
    orb = lights.get("console_lamp")
    mid.append(
        f'<circle cx="{ox:.1f}" cy="{oy:.1f}" r="21" '
        f'fill="{fill("console_lamp", LAMP_OFF)}"/>'
        f'<circle cx="{ox - 6:.1f}" cy="{oy - 7:.1f}" r="9" fill="#FFFFFF" '
        f'opacity="{0.15 + 0.4 * (orb[1] if orb else 0):.3f}"/>'
    )

    # The light bar on the console's right end: wider, with a bright core
    # (Room v2).
    bar, strength = faces("light_bar", 0.55, 0.35)
    mid.append(_box(490, 514, 14, 38, 0, 176, bar))
    if strength:
        mid.append(
            core(
                [(498, 38, 12), (506, 38, 12), (506, 38, 170), (498, 38, 170)],
                0.55 * strength,
            )
        )
        mid.append(
            core(
                [(514, 22, 12), (514, 30, 12), (514, 30, 170), (514, 22, 170)],
                0.35 * strength,
            )
        )
    mid.append(_box(486, 518, 10, 42, 0, 8, (METAL,) * 3))

    # The floor lamp, left of the TV.
    mid.append(_box(96, 128, 44, 76, 0, 6, (METAL,) * 3))
    mid.append(_box(109, 115, 57, 63, 6, 200, (METAL,) * 3))
    shade = fill("floor_lamp", LAMP_OFF)
    shade_top = LAMP_TOP_OFF
    if (lit := lights.get("floor_lamp")) is not None:
        shade_top = _mix(LAMP_TOP_OFF, "#FFF3E0", lit[1])
    mid.extend(_shade(112, 60, 198, 34, 252, 24, shade, shade_top))

    # A plant in the far left corner.
    mid.append(_box(30, 70, 300, 340, 0, 40, (WOOD[1], WOOD[2], WOOD[0])))
    for cx, cy, cz, r, colour in [
        (50, 320, 75, 34, PLANT[0]),
        (40, 305, 100, 24, PLANT[1]),
        (60, 330, 110, 22, PLANT[0]),
    ]:
        x, y = _p(cx, cy, cz)
        front.append(
            f'<ellipse cx="{x:.1f}" cy="{y:.1f}" rx="{r}" ry="{r * 0.85:.1f}" '
            f'fill="{colour}"/>'
        )

    # The coffee table, and the sofa facing the TV (its back to the viewer).
    front.append(_box(280, 400, 200, 290, 0, 34, WOOD))
    # The side table by the sofa, with the table lamp.
    front.append(_box(70, 130, 400, 460, 0, 56, WOOD))
    front.append(_box(97, 103, 427, 433, 56, 92, (METAL,) * 3))
    table_top = LAMP_TOP_OFF
    if (lit := lights.get("table_lamp")) is not None:
        table_top = _mix(LAMP_TOP_OFF, "#FFF3E0", lit[1])
    front.extend(
        _shade(100, 430, 90, 22, 124, 15, fill("table_lamp", LAMP_OFF), table_top)
    )
    front.append(_box(180, 480, 380, 440, 0, 46, SOFA))  # seat
    front.append(_box(180, 210, 380, 470, 0, 78, SOFA))  # left arm
    front.append(_box(450, 480, 380, 470, 0, 78, SOFA))  # right arm
    front.append(_box(180, 480, 440, 470, 0, 104, SOFA))  # back

    x, y, w, h = VIEWBOX
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x} {y} {w} {h}">'
        f"<defs>{''.join(defs)}</defs>{''.join(back)}{''.join(mid)}"
        f"{''.join(front)}</svg>"
    )


def _screen(screen: str | None) -> str:
    """The TV picture in wall-A plane coordinates, or a screen that is off."""
    x0, x1, z0, z1 = SCREEN_X0, SCREEN_X1, SCREEN_Z0, SCREEN_Z1
    if screen is None:
        return (
            f'<rect x="{x0}" y="{z0}" width="{x1 - x0}" height="{z1 - z0}" '
            f'fill="#1C1B26"/><path d="M{x0 + 120} {z1} L{x0 + 170} {z1} '
            f'L{x0 + 90} {z0} L{x0 + 40} {z0} Z" fill="#FFFFFF" opacity="0.05"/>'
        )
    # The plane's second axis points up, so the image is flipped to stand
    # upright; optimizeSpeed scales it up without smoothing.
    return (
        f'<g transform="matrix(1 0 0 -1 0 {z0 + z1})"><image x="{x0}" y="{z0}" '
        f'width="{x1 - x0}" height="{z1 - z0}" preserveAspectRatio="none" '
        f'image-rendering="optimizeSpeed" href="{screen}"/></g>'
    )


def _shade(
    cx: float,
    cy: float,
    zb: float,
    rb: float,
    zt: float,
    rt: float,
    fill: str,
    top: str,
) -> list[str]:
    """A lamp shade: a cone from radius rb at height zb to rt at zt."""
    k = 1.2247
    bx, by = _p(cx, cy, zb)
    tx, ty = _p(cx, cy, zt)
    brx, bry, trx, tr = rb * k, rb * k * 0.577, rt * k, rt * k * 0.577
    d = (
        f"M{bx - brx:.1f} {by:.1f} A{brx:.1f} {bry:.1f} 0 0 0 {bx + brx:.1f} "
        f"{by:.1f} L{tx + trx:.1f} {ty:.1f} A{trx:.1f} {tr:.1f} 0 0 0 "
        f"{tx - trx:.1f} {ty:.1f} Z"
    )
    return [
        f'<path d="{d}" fill="{fill}"/>',
        f'<ellipse cx="{tx:.1f}" cy="{ty:.1f}" rx="{trx:.1f}" ry="{tr:.1f}" '
        f'fill="{top}" opacity="0.9"/>',
        f'<path d="{d}" fill="#FFFFFF" opacity="0.12"/>',
    ]


# --- Frames -----------------------------------------------------------------


class Painter:
    """Draws a recording's samples; the same lights and picture once."""

    def __init__(self, recording: dict[str, Any]) -> None:
        self._recording = recording
        self._screens: dict[int, str] = {}
        # As PNG: a decoded room takes about 2 MB, and a fade has hundreds.
        self._rooms: dict[tuple[Any, ...], bytes] = {}

    def room(self, index: int) -> Image.Image:
        sample = self._recording["samples"][index]
        lights = {id_: light(rgb) for id_, rgb in sample["lights"].items()}
        key = (sample["frame"], tuple(sorted(lights.items())))
        if key not in self._rooms:
            import resvg_py  # only the drawing needs it, not the tests

            svg = room_svg(lights, self._screen(sample["frame"]))
            png = resvg_py.svg_to_bytes(
                svg_string=svg, width=ROOM_WIDTH, skip_system_fonts=True
            )
            self._rooms[key] = bytes(png)
        return Image.open(io.BytesIO(self._rooms[key])).convert("RGBA")

    def _screen(self, frame: int | None) -> str | None:
        if frame is None:
            return None
        if frame not in self._screens:
            pixels = bytes.fromhex(self._recording["frames"][frame])
            image = Image.frombytes("RGB", (64, 36), pixels)
            png = io.BytesIO()
            image.save(png, "PNG")
            data = base64.b64encode(png.getvalue()).decode()
            self._screens[frame] = f"data:image/png;base64,{data}"
        return self._screens[frame]


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    # Pillow's own font: the same on every machine, no font file needed.
    return ImageFont.load_default(size=size)


def _pill(
    draw: ImageDraw.ImageDraw,
    text: str,
    size: int,
    fill: str,
    x: float,
    y: float,
    *,
    centre: bool = False,
) -> None:
    font = _font(size)
    pad_x, height = size * 0.8, size * 1.7
    width = draw.textlength(text, font=font) + 2 * pad_x
    if centre:
        x -= width / 2
    draw.rounded_rectangle((x, y, x + width, y + height), radius=height / 2, fill=fill)
    # Anchored on the font's middle, so every text sits at the same height.
    draw.text(
        (x + pad_x, y + height / 2), text, font=font, fill=LABEL_TEXT, anchor="lm"
    )


def compose(
    rooms: list[tuple[str | None, Image.Image]],
    title: str,
    captions: list[list[str]] | None = None,
    tags: list[list[tuple[str, float, float]]] | None = None,
) -> Image.Image:
    """The rooms side by side, each with its label, and the title above.

    `captions`, per room: lines shown at its bottom (what is unavailable or
    dropped). `tags`, per room: texts at a point of the room (name_tags).
    """
    height = rooms[0][1].height
    canvas = Image.new("RGBA", (ROOM_WIDTH * len(rooms), BAND + height))
    draw = ImageDraw.Draw(canvas)
    for i, (label, image) in enumerate(rooms):
        canvas.alpha_composite(image, (i * ROOM_WIDTH, BAND))
        for text, x, y in tags[i] if tags and i < len(tags) else []:
            # Inside the room, also for a light at its right edge.
            width = draw.textlength(text, font=_font(TAG_SIZE)) + 1.6 * TAG_SIZE
            x = min(x + 8, ROOM_WIDTH - width - 4)
            _pill(
                draw,
                text,
                TAG_SIZE,
                TAG_FILL,
                i * ROOM_WIDTH + x,
                BAND + y - TAG_SIZE * 0.85,
            )
        if label:
            _pill(draw, label, 24, LABEL_FILL, i * ROOM_WIDTH + 24, BAND + 24)
        lines = captions[i] if captions and i < len(captions) else []
        for n, line in enumerate(reversed(lines)):
            y = BAND + height - 24 - (n + 1) * NOTE_SIZE * 2.1
            _pill(draw, line, NOTE_SIZE, NOTE_FILL, i * ROOM_WIDTH + 24, y)
    _pill(draw, title, 26, TITLE_FILL, canvas.width / 2, 8, centre=True)
    return canvas


def notes(sample: dict[str, Any]) -> list[str]:
    """What a sample's room says at its bottom: the Home Assistant lights that
    are unavailable, and the parts of a combined output that are dropped."""
    lines = []
    if unavailable := sample.get("unavailable"):
        names = [LIGHT_NAMES.get(id_, id_) for id_ in unavailable]
        if len(names) > 2:  # a list that long does not fit
            lines.append(f"{len(names)} Home Assistant lights unavailable")
        else:
            lines.append(f"{', '.join(names)} unavailable".capitalize())
    for part in sample.get("retrying", []):
        lines.append(f"{PART_NAMES.get(part, part)} lost, retrying")
    for part in sample.get("dropped", []):
        lines.append(f"{PART_NAMES.get(part, part)} dropped")
    return lines


def write(
    sides: list[tuple[str | None, dict[str, Any]]], out: Path, *, tags: bool = False
) -> list[Path]:
    """Write the GIF of all scenarios, and a GIF and a still per scenario.

    `tags`: each Home Assistant light's name and place next to it (--names).
    """
    painters = [(label, Painter(recording)) for label, recording in sides]
    recording = sides[0][1]
    names = [scenario["name"] for scenario in recording["scenarios"]]
    samples = recording["samples"]
    side_tags = [name_tags(side) if tags else [] for _, side in sides]

    def frame(index: int) -> Image.Image:
        title = names[samples[index]["scenario"]]
        return compose(
            [(label, p.room(index)) for label, p in painters],
            title,
            [notes(side["samples"][index]) for _, side in sides],
            side_tags,
        )

    written = [_write_gif(frame, list(range(len(samples))), out / "room.gif")]
    for number, scenario in enumerate(recording["scenarios"]):
        indexes = [i for i, s in enumerate(samples) if s["scenario"] == number]
        written.append(_write_gif(frame, indexes, out / f"room-{number + 1}.gif"))
        still = out / f"room-{number + 1}.png"
        frame(indexes[round(scenario["still"] * (len(indexes) - 1))]).save(
            still, optimize=True
        )
        written.append(still)
    return written


def _write_gif(
    frame: Callable[[int], Image.Image], indexes: list[int], gif: Path
) -> Path:
    """A GIF of these samples, at the highest frame rate under MAX_GIF_BYTES."""
    for step in STEPS:
        with tempfile.TemporaryDirectory() as tmp:
            for n, index in enumerate(indexes[::step]):
                image = frame(index)
                flat = Image.new("RGBA", image.size, GIF_BACKGROUND)
                flat.alpha_composite(image)
                flat.convert("RGB").save(Path(tmp) / f"{n:04d}.png", compress_level=1)
            _gif(Path(tmp), SEND_HZ / step, gif)
        size = gif.stat().st_size
        print(f"{gif}: {size / 1_000_000:.1f} MB at {SEND_HZ / step:g} frames/s")
        if size <= MAX_GIF_BYTES:
            return gif
    print(f"{gif} is over 10 MB even at the lowest frame rate: too big for GitHub.")
    return gif


def _gif(frames: Path, fps: float, gif: Path) -> None:
    # One palette for the whole GIF and a fine ordered dither: the glows'
    # gradients stay smooth, and what does not change is not stored again.
    palette = (
        "split[a][b];[a]palettegen=stats_mode=full[p];"
        "[b][p]paletteuse=dither=bayer:bayer_scale=2:diff_mode=rectangle"
    )
    import imageio_ffmpeg  # in the demo's environment, not the tests'

    subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-loglevel",
            "error",
            "-y",
            "-framerate",
            f"{fps:g}",
            "-i",
            str(frames / "%04d.png"),
            "-filter_complex",
            palette,
            "-r",
            f"{fps:g}",
            str(gif),
        ],
        check=True,
    )


# --- Recordings -------------------------------------------------------------


def _record(
    core: Path,
    out: Path,
    lights: str = "area",
    places: list[str] | None = None,
    *,
    liveliness: str = NORMAL,
) -> subprocess.CompletedProcess[str]:
    """Run the harness against `core` in its own interpreter; the scenarios'
    timings always come from this working copy's core. `liveliness` is
    passed only when it is not Normal, so the harness call is as before."""
    return subprocess.run(
        [
            sys.executable,
            "-I",
            str(HARNESS),
            "--core",
            str(core),
            "--timing-from",
            str(REPO / CORE),
            "--lights",
            lights,
            *(f"--place={value}" for value in places or []),
            *([f"--liveliness={liveliness}"] if liveliness != NORMAL else []),
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
    )


def _record_here(
    out: Path, lights: str, places: list[str], liveliness: str = NORMAL
) -> dict[str, Any]:
    """The recording on this working copy; exits when it fails."""
    result = _record(REPO / CORE, out, lights, places, liveliness=liveliness)
    if result.returncode != 0:
        sys.exit(f"The scenarios failed on this working copy:\n{result.stderr}")
    return json.loads(out.read_text())


def level_label(recording: dict[str, Any]) -> str:
    """The recording's liveliness level, for its label: for example "Calm"."""
    return str(recording.get("liveliness", NORMAL)).capitalize()


def mode_label(recording: dict[str, Any]) -> str | None:
    """The label of a room on its own: none for an entertainment area (as
    #138), else the kind of output and the places --place moved (--names
    shows them all); the level first when it is not Normal."""
    if recording.get("liveliness", NORMAL) != NORMAL:
        mode = MODE_LABELS.get(recording.get("lights", "area"))
        return f"{level_label(recording)}: {mode}" if mode else level_label(recording)
    if (mode := MODE_LABELS.get(recording.get("lights", "area"))) is None:
        return None
    moved = ", ".join(
        f"{LIGHT_NAMES.get(id_, id_)} {place['name']}"
        for id_, place in recording["places"].items()
        if place.get("moved")
    )
    return f"{mode}: {moved}" if moved else mode


def name_tags(recording: dict[str, Any]) -> list[tuple[str, float, float]]:
    """Each Home Assistant light's name and place at the light (--names): the text and
    where it starts, in px of a room."""
    tags = []
    for id_, place in recording.get("places", {}).items():
        if id_ not in NAME_TAGS:
            continue
        x, y = _p(*NAME_TAGS[id_])
        scale = ROOM_WIDTH / VIEWBOX[2]
        name = LIGHT_NAMES.get(id_, id_).capitalize()
        tags.append(
            (
                f"{name} - {place['name']}",
                (x - VIEWBOX[0]) * scale,
                (y - VIEWBOX[1]) * scale,
            )
        )
    return tags


def place_label(this: dict[str, Any], other: dict[str, Any]) -> str:
    """The places of `this` recording that differ from `other`'s, for its
    label: for example "Floor lamp left"."""
    differ = [
        f"{LIGHT_NAMES.get(id_, id_)} {place['name']}"
        for id_, place in this["places"].items()
        if place != other["places"].get(id_)
    ]
    return (", ".join(differ) or "the same places").capitalize()


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(REPO), *args], check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def record_main(
    tmp: Path, lights: str = "area", places: list[str] | None = None
) -> dict[str, Any] | None:
    """The recording on the merge base with main, or None if its core cannot
    run the scenarios, or it has no such lights yet. The temporary worktree is
    always removed."""
    try:
        _git("fetch", "--quiet", "origin", "+refs/heads/main:refs/remotes/origin/main")
        base = _git("merge-base", "HEAD", "origin/main")
    except subprocess.CalledProcessError as err:
        _this_side_only(f"cannot find main's merge base ({_last_line(err.stderr)})")
        return None
    worktree = Path(tempfile.mkdtemp(prefix="glowcast-room-main-"))
    try:
        try:
            _git("worktree", "add", "--quiet", "--detach", str(worktree), base)
        except subprocess.CalledProcessError as err:
            _this_side_only(f"cannot check out {base[:7]} ({_last_line(err.stderr)})")
            return None
        print(f"main: the merge base {base[:7]}", flush=True)
        result = _record(worktree / CORE, tmp / "main.json", lights, places)
    finally:
        subprocess.run(
            ["git", "-C", str(REPO), "worktree", "remove", "--force", str(worktree)],
            capture_output=True,
        )
        subprocess.run(["git", "-C", str(REPO), "worktree", "prune"], check=False)
        shutil.rmtree(worktree, ignore_errors=True)
    if result.returncode == EXIT_NO_OUTPUT:
        # As for a new kind of light: main has no Home Assistant lights yet.
        _this_side_only(f"main ({base[:7]}) {_last_line(result.stderr)}")
        return None
    if result.returncode != 0:
        reason = _last_line(result.stderr)
        _this_side_only(f"main ({base[:7]}) cannot run the scenarios ({reason})")
        return None
    return json.loads((tmp / "main.json").read_text())


def _last_line(text: str) -> str:
    return (text.strip().splitlines() or ["no output"])[-1]


def _this_side_only(reason: str) -> None:
    print(f"{reason[0].upper()}{reason[1:]}; drawing this PR's side only.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--compare", action="store_true")
    parser.add_argument("--lights", choices=LIGHTS, default="area")
    parser.add_argument("--place", action="append", default=[])
    parser.add_argument("--versus", action="append", default=[])
    parser.add_argument("--names", action="store_true")
    parser.add_argument("--liveliness", choices=(*LEVELS, ALL_LEVELS), default=NORMAL)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.lights == "area" and (args.place or args.versus):
        parser.error(
            "--place and --versus are for Home Assistant lights: --lights ha|combined"
        )
    if args.versus and args.compare:
        parser.error("--versus and --compare draw two rooms each: pick one")
    if args.liveliness != NORMAL and (args.versus or args.compare):
        parser.error(
            "--liveliness is for this working copy on its own: "
            "not with --compare or --versus"
        )
    # A stop from outside also runs the cleanup (the temporary worktree).
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(1))
    args.out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as folder:
        tmp = Path(folder)
        if args.liveliness == ALL_LEVELS:
            # The three levels side by side (#16), Calm on the left.
            levels = [
                _record_here(tmp / f"{level}.json", args.lights, args.place, level)
                for level in LEVELS
            ]
            written = write(
                [(level_label(r), r) for r in levels], args.out, tags=args.names
            )
            print(f"Wrote {len(written)} files to {args.out}")
            return
        this = _record_here(tmp / "pr.json", args.lights, args.place, args.liveliness)
        sides: list[tuple[str | None, dict[str, Any]]] = [(mode_label(this), this)]
        if args.versus:
            # The later of two places for a light wins (the harness reads
            # them in order).
            other = _record_here(
                tmp / "versus.json", args.lights, args.place + args.versus
            )
            sides = [
                (place_label(this, other), this),
                (place_label(other, this), other),
            ]
        elif args.compare:
            sides = [("this PR", this)]
            if (main_ := record_main(tmp, args.lights, args.place)) is not None:
                sides.insert(0, ("main", main_))
    written = write(sides, args.out, tags=args.names)
    print(f"Wrote {len(written)} files to {args.out}")


if __name__ == "__main__":
    main()
