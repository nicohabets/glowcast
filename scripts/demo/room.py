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
# A light that is off (scene 5 of #135).
STRIP_OFF, LAMP_OFF, LAMP_TOP_OFF = "#4A4560", "#7A7390", "#8E88A3"
# The TV screen in wall-A plane coordinates.
SCREEN_X0, SCREEN_X1, SCREEN_Z0, SCREEN_Z1 = 236, 444, 116, 230

# The labels: a pill as in the illustrations' series.py.
LABEL_FILL, TITLE_FILL, LABEL_TEXT = CAP, "#2E2A3E", "#FFFFFF"
# Behind the rooms in the GIF (the stills stay transparent): a GIF has no
# partial transparency, and with a transparent background each frame is stored
# whole, five times the size. GitHub's light background colour.
GIF_BACKGROUND = "#F6F8FA"

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
    glow(WALL_A, "lb", 520, 140, 160, "light_bar", 0.8)  # light bar, wall A
    glow(FLOOR, "ffl", 110, 70, 190, "floor_lamp", 0.55)  # floor pool, lamp
    glow(FLOOR, "flb2", 520, 80, 170, "light_bar", 0.5)  # floor pool, bar
    glow(FLOOR, "ftv", 340, 120, 220, "top", 0.35)  # TV spill on the floor

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

    # The light bar on the console's right end.
    bar = fill("light_bar", STRIP_OFF)
    mid.append(_box(498, 512, 18, 32, 0, 150, (bar,) * 3))
    mid.append(_box(496, 514, 16, 34, 0, 6, (METAL,) * 3))

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


def compose(rooms: list[tuple[str | None, Image.Image]], title: str) -> Image.Image:
    """The rooms side by side, each with its label, and the title above."""
    height = rooms[0][1].height
    canvas = Image.new("RGBA", (ROOM_WIDTH * len(rooms), BAND + height))
    draw = ImageDraw.Draw(canvas)
    for i, (label, image) in enumerate(rooms):
        canvas.alpha_composite(image, (i * ROOM_WIDTH, BAND))
        if label:
            _pill(draw, label, 24, LABEL_FILL, i * ROOM_WIDTH + 24, BAND + 24)
    _pill(draw, title, 26, TITLE_FILL, canvas.width / 2, 8, centre=True)
    return canvas


def write(sides: list[tuple[str | None, dict[str, Any]]], out: Path) -> list[Path]:
    """Write the GIF of all scenarios, and a GIF and a still per scenario."""
    painters = [(label, Painter(recording)) for label, recording in sides]
    recording = sides[0][1]
    names = [scenario["name"] for scenario in recording["scenarios"]]
    samples = recording["samples"]

    def frame(index: int) -> Image.Image:
        title = names[samples[index]["scenario"]]
        return compose([(label, p.room(index)) for label, p in painters], title)

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


def _record(core: Path, out: Path) -> subprocess.CompletedProcess[str]:
    """Run the harness against `core` in its own interpreter; the scenarios'
    timings always come from this working copy's core."""
    return subprocess.run(
        [
            sys.executable,
            "-I",
            str(HARNESS),
            "--core",
            str(core),
            "--timing-from",
            str(REPO / CORE),
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
    )


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(REPO), *args], check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def record_main(tmp: Path) -> dict[str, Any] | None:
    """The recording on the merge base with main, or None if its core cannot
    run the scenarios. The temporary worktree is always removed."""
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
        result = _record(worktree / CORE, tmp / "main.json")
    finally:
        subprocess.run(
            ["git", "-C", str(REPO), "worktree", "remove", "--force", str(worktree)],
            capture_output=True,
        )
        subprocess.run(["git", "-C", str(REPO), "worktree", "prune"], check=False)
        shutil.rmtree(worktree, ignore_errors=True)
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
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    # A stop from outside also runs the cleanup (the temporary worktree).
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(1))
    args.out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as folder:
        tmp = Path(folder)
        result = _record(REPO / CORE, tmp / "pr.json")
        if result.returncode != 0:
            sys.exit(f"The scenarios failed on this working copy:\n{result.stderr}")
        this = json.loads((tmp / "pr.json").read_text())
        sides: list[tuple[str | None, dict[str, Any]]] = [(None, this)]
        if args.compare:
            sides = [("this PR", this)]
            if (main_ := record_main(tmp)) is not None:
                sides.insert(0, ("main", main_))
    written = write(sides, args.out)
    print(f"Wrote {len(written)} files to {args.out}")


if __name__ == "__main__":
    main()
