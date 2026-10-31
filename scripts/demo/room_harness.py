"""Runs fixed scenario pictures through Glowcast's own pipeline, on a fake clock.

The pipeline, the colour engine and the light state are `core/`'s own: the
fade, the hold on black, the glow, brightness and the release after no picture
are exactly what a real output would get. Only the TV (a source that makes the
scenario pictures in memory) and the lights (an output that records what it
gets) are fakes, and time only moves when the harness steps the clock.

With `--lights area` (the default) `core/` is loaded from a folder by path,
without the Home Assistant package around it, so the same harness runs against
another commit's `core/` for `demo.py room --compare`; then it needs only the
standard library (the drawing is in room.py).

With `--lights ha` or `--lights combined` (#196) the lights are Glowcast's
own outputs too: the real Home Assistant lights output (`outputs/ha_lights.py`, with its
places, its schedule and its `transition`) and, combined, the real
`CompositeOutput` with the entertainment area beside it. Only Home
Assistant around the Home Assistant lights is a fake (FakeHass: the lights' states, an
entity registry, and `light.turn_on` and `turn_off`, recorded); no Home
Assistant is started. These modes import the Glowcast package of the tree that
holds `--core`, so they need Home Assistant installed (the demo's environment,
or the test requirements).

Usage (room.py runs it; the result is JSON):
    python room_harness.py --core DIR [--timing-from DIR]
        [--lights area|ha|combined] [--place LIGHT=PLACE ...] --out FILE
"""

import argparse
import asyncio
import colorsys
import dataclasses
import heapq
import importlib
import importlib.util
import itertools
import json
import math
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType, ModuleType, SimpleNamespace
from typing import Any

WIDTH, HEIGHT = 64, 36  # as the webOS source delivers them
SEND_HZ = 25  # as Hue's entertainment stream (max_rate_hz)
# About as long as a capture from the TV takes (about 4 per second).
CAPTURE_SECONDS = 0.24
# How long (fake time) the pipeline may take to clean up after stop().
STOP_SECONDS = 60
# Glowcast's default settings (const.py, which belongs to the Home Assistant
# package and is not imported here).
BRIGHTNESS = 80
INTENSITY = 100
UPDATE_RATE = "high"

# The lights in the illustrated room, per output type: the one place that says
# which light of the room stands for which channel. room.py draws each light
# by its id; a new light in the room gets a row here and a drawing there.
#
# The entertainment area (--lights area, and combined), with each channel's
# position (x: -1 left of the screen, +1 right; z: -1 floor, +1 ceiling).
# The strip sits where a real area puts a gradient TV strip: close to the
# centre, between -0.4 and 0.4 (architecture 2, "TV strip positions").
CHANNELS = [
    ("left", -0.4, 0.0),  # the strip's left side
    ("top_left", -0.2, 0.4),  # the strip's top, left third
    ("top", 0.0, 0.4),  # the strip's top, middle
    ("top_right", 0.2, 0.4),  # the strip's top, right third
    ("right", 0.4, 0.0),  # the strip's right side
    ("floor_lamp", -1.0, -0.5),  # the floor lamp, left of the TV
    ("light_bar", 1.0, -0.5),  # the light bar on the console, right
    ("table_lamp", -1.0, -0.3),  # the table lamp by the sofa, left, at the back
]
# Each area channel's depth (y: +1 the TV side, -1 the far side) and the light
# it is a part of: the strip's five segments are one gradient light (#23); the
# table lamp stands at the back of the room, so it follows more softly (#15).
# Given only to a core/ whose Channel has these fields, so --compare still
# runs an older main.
DEPTH = {
    "left": (0.8, "strip"),
    "top_left": (0.8, "strip"),
    "top": (0.8, "strip"),
    "top_right": (0.8, "strip"),
    "right": (0.8, "strip"),
    "floor_lamp": (0.6, "floor_lamp"),
    "light_bar": (0.9, "light_bar"),
    "table_lamp": (-0.8, "table_lamp"),
}
# The Home Assistant lights (#9) of "Room v2" (#196), one at every place, each with its
# name and place: a preset of outputs/ha_lights.py or "x,z" as the sliders
# fine-tune it (--place). --lights ha: all of them. Combined: those that
# are not in the area. --lights area: the ones not in the area stay off.
HA_LIGHTS = {
    "floor_lamp": ("Floor lamp", "left"),
    "light_bar": ("Light bar", "right"),
    "wall_light": ("Wall light", "top"),  # above the TV
    "console_lamp": ("Orb lamp", "bottom"),  # on the console
    "console_strip": ("Light strip", "bottom"),  # along the console
    "table_lamp": ("Table lamp", "ambient"),  # by the sofa
}


def ha_light_ids(lights: str) -> list[str]:
    """The room's Home Assistant lights with this output (see HA_LIGHTS)."""
    area = {id_ for id_, _, _ in CHANNELS} if lights == "combined" else set()
    return [id_ for id_ in HA_LIGHTS if id_ not in area]


LIGHTS = ("area", "ha", "combined")
# The parts of a combined output, named by their type as outputs/__init__.py
# names them.
AREA_PART, HA_LIGHTS_PART = "hue_entertainment", "ha_lights"
# The exit code when the tree has no Home Assistant lights or no combined output (main
# before #9 or #11): room.py then draws this side only.
EXIT_NO_OUTPUT = 3

RGB = tuple[int, int, int]
Picture = Callable[[float], bytes]  # seconds into the scenario -> pixels


class NoOutput(Exception):
    """The tree has no output of this kind (main before #9 or #11)."""


@dataclass(frozen=True)
class Scenario:
    """A named run of pictures; `still` is the moment (0-1) that shows it best.

    `unavailable`: (Home Assistant light, seconds into the scenario) when that light
    becomes unavailable in Home Assistant, for good.
    """

    name: str
    seconds: float
    picture: Picture
    still: float = 1.0
    unavailable: tuple[tuple[str, float], ...] = ()


def _image(pixel: Callable[[float, float], RGB]) -> bytes:
    """Pixels from a function of x and y (0-1, y = 0 is the top row)."""
    pixels = bytearray()
    for row in range(HEIGHT):
        for col in range(WIDTH):
            pixels += bytes(pixel((col + 0.5) / WIDTH, (row + 0.5) / HEIGHT))
    return bytes(pixels)


def _mix(a: RGB, b: RGB, t: float) -> RGB:
    r, g, b_ = (round(x + (y - x) * t) for x, y in zip(a, b, strict=True))
    return r, g, b_


def _across(stops: list[RGB], x: float) -> RGB:
    """A horizontal gradient through evenly spaced colours."""
    position = x * (len(stops) - 1)
    i = min(int(position), len(stops) - 2)
    return _mix(stops[i], stops[i + 1], position - i)


def _sunset(x: float, y: float) -> RGB:
    """A sunset with a place for every light (#196): an orange sky on the
    left, pink above the middle and blue on the right; a golden sun on the
    horizon in the middle; a teal sea below, warm where it mirrors the left
    and blue on the right. Each place reads its own part (color_engine's
    regions), so left, right, top, bottom and the middle differ in hue."""
    horizon = 0.58
    sun = math.hypot((x - 0.5) * 16 / 9, y - horizon + 0.04)
    if sun < 0.13:
        return 255, 230, 110
    if y < horizon:
        sky = _across([(255, 140, 60), (232, 96, 172), (70, 90, 230)], x)
        if sun < 0.32:  # the golden glow around the sun
            sky = _mix(sky, (255, 212, 50), min(1.0, (0.32 - sun) / 0.12))
        return sky
    sea = _across([(190, 105, 75), (20, 150, 165), (20, 150, 165), (25, 60, 190)], x)
    if abs(x - 0.5) < 0.13 and y < horizon + 0.12:  # the sun's reflection
        sea = _mix(sea, (255, 214, 70), 0.85)
    return sea


def _night(x: float, y: float) -> RGB:
    """A dark picture: black by the colour engine (a small moon), not all zero."""
    if math.hypot((x - 0.25) * 16 / 9, y - 0.25) < 0.06:
        return 233, 222, 196
    return _mix((8, 10, 18), (16, 15, 28), y)


def _red(x: float, y: float) -> RGB:
    """A saturated red sky over darker red hills."""
    if y > 0.75 + 0.06 * math.sin(x * 7):
        return 142, 10, 20
    return _mix((200, 15, 28), (255, 51, 38), y)


def _letterbox(x: float, y: float) -> RGB:
    """A 2.39:1 film in a 16:9 picture at dusk: black bars above and below,
    and a dim band of sky right under the top bar, as in the film of #5."""
    bar = (1 - (16 / 9) / 2.39) / 2
    if y < bar or y > 1 - bar:
        return 0, 0, 0
    if y < bar + 0.1:
        return 44, 46, 58  # dusk, just above the colour engine's black level
    if y < 0.55:
        return _across([(64, 170, 210), (120, 200, 230)], x)  # sky
    return _across([(70, 140, 60), (150, 175, 70)], x)  # field


def _four_three(x: float, y: float) -> RGB:
    """A 4:3 picture in the 16:9 frame: black bars left and right (an eighth of
    the width each), and in the picture a warm red wall on the left, a sky
    above and a teal wall on the right, so the lights at the sides and the
    strip's ends show whether they read the picture's edges or the bars."""
    bar = (1 - (4 / 3) / (16 / 9)) / 2
    if x < bar or x > 1 - bar:
        return 0, 0, 0
    u = (x - bar) / (1 - 2 * bar)  # across the picture inside the bars
    if y < 0.3:
        return _across([(240, 150, 90), (120, 180, 235), (90, 150, 230)], u)  # sky
    return _across([(220, 60, 50), (205, 185, 150), (30, 170, 160)], u)  # walls


def _concert(x: float, y: float) -> RGB:
    """A concert: a purple and an orange spotlight on a dark stage (#233)."""
    if math.hypot(x - 0.3, (y - 0.35) * 0.6) < 0.12:
        return 160, 60, 230
    if math.hypot(x - 0.72, (y - 0.35) * 0.6) < 0.1:
        return 250, 140, 30
    if y > 0.8:
        return 46, 40, 44  # the audience, dimly lit
    return 36, 34, 42


def _ocean(x: float, y: float) -> RGB:
    return _across([(31, 181, 196), (61, 123, 255), (43, 79, 214)], x)


def _meadow(x: float, y: float) -> RGB:
    return _across([(123, 209, 90), (242, 210, 92), (47, 158, 110)], x)


def _still(pixel: Callable[[float, float], RGB]) -> Picture:
    pixels = _image(pixel)
    return lambda seconds: pixels


def _crossfade(
    a: Callable[[float, float], RGB],
    b: Callable[[float, float], RGB],
    hold: float,
    fade: float,
) -> Picture:
    """`a`, then a fade over `fade` seconds to `b`, after `hold` seconds."""
    start, end = _image(a), _image(b)

    def picture(seconds: float) -> bytes:
        t = min(max((seconds - hold) / fade, 0.0), 1.0)
        return bytes(round(p + (q - p) * t) for p, q in zip(start, end, strict=True))

    return picture


def _drifting(seconds: float) -> bytes:
    """Colours that keep moving across the picture, one turn in 12 seconds."""

    def pixel(x: float, y: float) -> RGB:
        hue = (x * 0.5 + seconds / 12) % 1.0
        r, g, b = colorsys.hsv_to_rgb(hue, 0.75, 0.9 if y < 0.6 else 0.6)
        return round(r * 255), round(g * 255), round(b * 255)

    return _image(pixel)


def scenarios(
    black_hold: float,
    no_picture_wait: float,
    lights: str = "area",
    lost_retry_delay: float = 5.0,
) -> list[Scenario]:
    """The fixed scenarios, in order; the timings follow `core/`.

    The combined output gets a ninth: its Home Assistant lights become unavailable,
    the first (the wall light) 1 s in and the others 3 s in, until that part
    is dropped (the Home Assistant lights are checked every 5 s, then released and
    started again once after `lost_retry_delay`) while the entertainment area
    keeps following the picture.
    """
    run = [
        Scenario("1 Sunset", 2.5, _still(_sunset)),
        # Holds the last colours for BLACK_HOLD, then fades to the glow.
        Scenario("2 Sudden dark picture", black_hold + 2.0, _still(_night)),
        Scenario("3 Saturated red", 2.0, _still(_red)),
        # Long enough for the bars to be taken (letterbox.STABLE, 2 s).
        Scenario("4 Letterbox", 4.0, _still(_letterbox)),
        Scenario(
            "5 Fade between scenes", 3.0, _crossfade(_ocean, _meadow, 0.5, 2.0), 0.6
        ),
        # Colourful spots on a dark stage: a mixed picture (#233).
        Scenario("6 Concert", 2.5, _still(_concert)),
        # Bars at the sides, taken after letterbox.STABLE as in 4 (#294).
        Scenario("7 4:3 picture", 4.0, _still(_four_three)),
        # All-zero frames: released after NO_PICTURE_WAIT.
        Scenario("8 No picture", no_picture_wait + 2.0, _still(lambda x, y: (0, 0, 0))),
    ]
    if lights == "combined":
        run.append(
            Scenario(
                "9 Home Assistant lights unavailable",
                # One Home Assistant light 1 s in, the others 3 s in, the check up to
                # 5 s later, the retry, and a moment to see that part dropped.
                3.0 + 5.0 + lost_retry_delay + 2.0,
                _drifting,
                0.97,
                tuple(
                    (id_, 3.0 if n else 1.0)
                    for n, id_ in enumerate(ha_light_ids("combined"))
                ),
            )
        )
    return run


_LOADED = itertools.count()  # each load its own package name


def load_core(folder: Path, name: str) -> ModuleType:
    """Import a `core/` folder as package `name`, without its parent package."""
    spec = importlib.util.spec_from_file_location(
        name, folder / "__init__.py", submodule_search_locations=[str(folder)]
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"No core package in {folder}")
    package = importlib.util.module_from_spec(spec)
    sys.modules[name] = package
    spec.loader.exec_module(package)
    for module in ("models", "light_state", "pipeline"):
        # v0.1.x kept the light state in pipeline.py (the timings come from
        # this working copy's core/, see --timing-from).
        if module == "light_state" and not (folder / "light_state.py").is_file():
            continue
        setattr(package, module, importlib.import_module(f"{name}.{module}"))
    return package


def load_glowcast(core_folder: Path, lights: str) -> SimpleNamespace:
    """The Glowcast package of the tree that holds `core_folder`: its `core/`
    and the outputs that `lights` needs, from the same tree.

    Raises NoOutput when that tree does not have them yet.
    """
    package = core_folder.resolve().parent
    needed = ["ha_lights.py"]
    if lights == "combined":
        needed.append("composite.py")
    if missing := [
        name for name in needed if not (package / "outputs" / name).is_file()
    ]:
        what = "Home Assistant lights" if lights == "ha" else "combined output"
        files = ", ".join(f"outputs/{name}" for name in missing)
        raise NoOutput(f"has no {what} ({files})")
    root = package.parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    name = "custom_components.glowcast"
    glowcast = importlib.import_module(name)
    if Path(glowcast.__file__).resolve().parent != package:
        raise ImportError(f"Glowcast is imported from {glowcast.__file__} already")
    core = importlib.import_module(f"{name}.core")
    for module in ("models", "light_state", "pipeline"):
        setattr(core, module, importlib.import_module(f"{name}.core.{module}"))
    return SimpleNamespace(
        core=core,
        ha_lights=importlib.import_module(f"{name}.outputs.ha_lights"),
        composite=(
            importlib.import_module(f"{name}.outputs.composite")
            if lights == "combined"
            else None
        ),
    )


class FakeClock:
    """Time only moves when the harness advances it (as tests/fakes.py)."""

    def __init__(self) -> None:
        self.time = 0.0
        self._timers: list[tuple[float, int, asyncio.Future[None]]] = []
        self._order = itertools.count()

    def now(self) -> float:
        return self.time

    async def sleep(self, seconds: float) -> None:
        future = asyncio.get_running_loop().create_future()
        heapq.heappush(
            self._timers, (self.time + max(seconds, 0), next(self._order), future)
        )
        await future

    async def advance_to(self, end: float) -> None:
        while True:
            await _settle()
            while self._timers and self._timers[0][2].done():
                heapq.heappop(self._timers)
            if not self._timers or self._timers[0][0] > end:
                break
            wake, _, future = heapq.heappop(self._timers)
            self.time = wake
            future.set_result(None)
        self.time = end
        await _settle()


async def _settle() -> None:
    for _ in range(20):
        await asyncio.sleep(0)


class ScenarioSource:
    """A TV that shows the scenarios one after another."""

    max_rate_hz = math.inf  # no cap of its own, like the TV

    def __init__(
        self, clock: FakeClock, run: list[Scenario], models: ModuleType
    ) -> None:
        self._clock = clock
        self._run = run
        self._models = models
        self.last: bytes | None = None  # the picture Glowcast got last

    async def start(self) -> None:
        pass

    def is_on(self) -> bool:
        return True

    async def capture(self) -> Any:
        # The TV takes the picture when asked; it arrives a capture later.
        pixels = self.picture(self._clock.now())
        await self._clock.sleep(CAPTURE_SECONDS)
        self.last = pixels
        return self._models.Frame(WIDTH, HEIGHT, pixels)

    def picture(self, now: float) -> bytes:
        for scenario in self._run:
            if now < scenario.seconds:
                return scenario.picture(now)
            now -= scenario.seconds
        return self._run[-1].picture(self._run[-1].seconds)

    async def close(self) -> None:
        pass


class RecordingOutput:
    """Lights at the CHANNELS positions that keep the last colours they got."""

    max_rate_hz = float(SEND_HZ)
    dropped_parts: Mapping[str, Exception] = MappingProxyType({})  # one part

    def __init__(self, models: ModuleType) -> None:
        self._models = models
        # Empty until the first send after a claim, and again once released.
        self.colors: dict[str, list[float]] = {}

    async def start(self, *, retry: bool = False) -> list[Any]:
        fields = {f.name for f in dataclasses.fields(self._models.Channel)}
        channels = []
        for id, x, z in CHANNELS:
            y, light = DEPTH[id]
            extra = {"y": y, "light": light} if {"y", "light"} <= fields else {}
            channels.append(self._models.Channel(id, id, x, z, **extra))
        return channels

    def send(self, colors: list[Any]) -> None:
        self.colors = {color.channel_id: list(color.rgb) for color in colors}

    async def check(self) -> None:
        pass

    async def stop(self) -> None:
        self.colors = {}

    async def close(self) -> None:
        pass


# --- Home Assistant around the Home Assistant lights (a fake) -------------------------


@dataclass
class FakeLight:
    """A Home Assistant light as Home Assistant shows it; it fades as a real light does:
    from what it shows now to the new colour, over the command's `transition`.
    """

    room_id: str  # the light in the room (HA_LIGHTS)
    name: str
    available: bool = True
    on: bool = False
    _from: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    _to: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    _start: float = 0.0
    _fade: float = 0.0
    rgb_color: list[int] | None = None
    brightness: int | None = None

    @property
    def registry_id(self) -> str:
        # Not an entity id, as the entity registry's ids, so the output
        # resolves it through the registry.
        return f"room-{self.room_id.replace('_', '-')}"

    @property
    def entity_id(self) -> str:
        return f"light.{self.room_id}"

    def level(self, now: float) -> list[float] | None:
        """The colour it shows now (0-1, brightness included); None when off."""
        if not self.available or not self.on:
            return None
        t = 1.0 if self._fade <= 0 else min(max((now - self._start) / self._fade, 0), 1)
        return [
            round(a + (b - a) * t, 4) for a, b in zip(self._from, self._to, strict=True)
        ]

    def turn_on(self, now: float, data: Mapping[str, Any]) -> None:
        start = self.level(now) or [0.0, 0.0, 0.0]
        if "rgb_color" in data:
            self.rgb_color = list(data["rgb_color"])
        self.brightness = data.get("brightness", self.brightness or 255)
        rgb = self.rgb_color or [255, 255, 255]
        self._from = start
        self._to = [c / 255 * self.brightness / 255 for c in rgb]
        self._start, self._fade = now, float(data.get("transition", 0))
        self.on = True

    def turn_off(self) -> None:
        self.on = False


class FakeRegistry:
    """The entity registry, for the Home Assistant lights only."""

    def __init__(self, lights: list[FakeLight]) -> None:
        entries = [
            SimpleNamespace(
                id=light.registry_id,
                entity_id=light.entity_id,
                domain="light",
                # Not Hue or Matter: no shared network, so each light gets a
                # command at most once per second.
                platform="glowcast_room",
                config_entry_id=None,
                device_id=None,
                area_id=None,
                disabled=False,
                name=None,
                original_name=light.name,
                capabilities=None,
            )
            for light in lights
        ]
        self._by_entity_id = {entry.entity_id: entry for entry in entries}
        by_id = {entry.id: entry for entry in entries}
        self.entities = SimpleNamespace(
            get_entry=by_id.get, values=lambda: list(entries)
        )

    def async_get(self, entity_id: str) -> Any:
        return self._by_entity_id.get(entity_id)


class FakeHass:
    """What the Home Assistant lights output uses of Home Assistant: the lights' states,
    the entity registry, `light.turn_on` and `turn_off` (recorded in `calls`)
    and its tasks. Nothing else of Home Assistant runs."""

    def __init__(self, clock: FakeClock, lights: list[FakeLight]) -> None:
        from homeassistant.core import State
        from homeassistant.helpers import entity_registry as er

        self._clock = clock
        self._state = State
        self.lights = {light.entity_id: light for light in lights}
        self.calls: list[dict[str, Any]] = []
        self.data: dict[Any, Any] = {er.DATA_REGISTRY: FakeRegistry(lights)}
        self.states = SimpleNamespace(get=self._get)
        self.services = SimpleNamespace(async_call=self._async_call)

    def async_create_task(
        self, coro: Any, name: str | None = None, eager_start: bool = True
    ) -> asyncio.Task[Any]:
        return asyncio.ensure_future(coro)

    def levels(self, now: float) -> dict[str, list[float]]:
        """{room light: the colour it shows} of the lights that are on."""
        return {
            light.room_id: level
            for light in self.lights.values()
            if (level := light.level(now)) is not None
        }

    def _get(self, entity_id: str) -> Any:
        light = self.lights.get(entity_id)
        if light is None:
            return None
        attributes: dict[str, Any] = {
            "supported_color_modes": ["rgb"],
            "friendly_name": light.name,
        }
        if not light.available:
            return self._state(entity_id, "unavailable", attributes)
        if not light.on:
            return self._state(entity_id, "off", attributes)
        attributes |= {
            "color_mode": "rgb",
            "rgb_color": light.rgb_color,
            "brightness": light.brightness,
        }
        return self._state(entity_id, "on", attributes)

    async def _async_call(
        self,
        domain: str,
        service: str,
        data: Mapping[str, Any],
        blocking: bool = False,
        **kwargs: Any,
    ) -> None:
        light = self.lights[data["entity_id"]]
        now = self._clock.now()
        self.calls.append(
            {
                "time": round(now, 6),
                "light": light.room_id,
                "service": service,
                **{k: v for k, v in data.items() if k != "entity_id"},
            }
        )
        if not light.available:
            return  # gone: nothing reaches it
        if service == "turn_on":
            light.turn_on(now, data)
        elif service == "turn_off":
            light.turn_off()


class BadPlace(ValueError):
    """A --place for a light that is not a Home Assistant light, or a place that
    does not exist."""


def place(
    value: str, presets: Mapping[str, tuple[float, float]]
) -> tuple[float, float]:
    """A place as the Home Assistant lights' settings store it: a preset, or "x,z"."""
    if value in presets:
        return presets[value]
    try:
        x, z = (float(part) for part in value.split(","))
    except ValueError:
        raise BadPlace(
            f"Unknown place {value!r}: one of {', '.join(presets)}, or x,z"
        ) from None
    if not (-1 <= x <= 1 and -1 <= z <= 1):
        raise BadPlace(f"Place {value!r} is outside -1 to 1")
    return x, z


def places_of(places: Mapping[str, str] | None, lights: str = "ha") -> dict[str, str]:
    """The place of each Home Assistant light with this output: HA_LIGHTS's, with
    `places` instead."""
    ids = ha_light_ids(lights)
    chosen = {room_id: HA_LIGHTS[room_id][1] for room_id in ids}
    for room_id, value in (places or {}).items():
        if room_id not in ids:
            raise BadPlace(
                f"No Home Assistant light {room_id!r} with --lights {lights}: "
                f"one of {', '.join(ids)}"
            )
        chosen[room_id] = value
    return chosen


class WatchedPart:
    """A part of the combined output, passed through unchanged, that notes
    whether its last start failed.

    The port does not tell a part that waits for its retry from one that is
    dropped for good: `dropped_parts` names a lost part from the moment it is
    lost. A part that started before and then fails to start is the
    composite's retry that failed, so from then on it is dropped.

    The composite also drops a part without a retry: when its key is
    rejected, when it fails with an error that is not OutputLost, or when it
    is lost again within LOST_AGAIN_WINDOW of a retry. Such a part shows as
    retrying here, because no start fails; the scenarios never do that.
    """

    def __init__(self, output: Any) -> None:
        self._output = output
        self.max_rate_hz = output.max_rate_hz
        self.dropped_parts = output.dropped_parts
        self.start_failed = False

    async def start(self, *, retry: bool = False) -> Any:
        try:
            channels = await self._output.start(retry=retry)
        except Exception:
            self.start_failed = True
            raise
        self.start_failed = False
        return channels

    def send(self, colors: list[Any]) -> None:
        self._output.send(colors)

    async def check(self) -> None:
        await self._output.check()

    async def stop(self) -> None:
        await self._output.stop()

    async def close(self) -> None:
        await self._output.close()


def _ha_lights(
    glowcast: SimpleNamespace, clock: FakeClock, places: Mapping[str, str]
) -> tuple[FakeHass, Any, dict[str, tuple[float, float]]]:
    """The real Home Assistant lights output on FakeHass, set up as the settings store
    it (architecture 5): single lights, each with its place."""
    ha_lights = glowcast.ha_lights
    lights = [FakeLight(room_id, HA_LIGHTS[room_id][0]) for room_id in places]
    positions = {
        light.room_id: place(places[light.room_id], ha_lights.PRESETS)
        for light in lights
    }
    block = {
        "type": ha_lights.OUTPUT_HA_LIGHTS,
        ha_lights.CONF_FOLLOW: ha_lights.FOLLOW_LIGHTS,
        ha_lights.CONF_LIGHTS: [
            {
                ha_lights.CONF_ENTITY: light.registry_id,
                ha_lights.CONF_X: positions[light.room_id][0],
                ha_lights.CONF_Z: positions[light.room_id][1],
            }
            for light in lights
        ],
    }
    hass = FakeHass(clock, lights)
    output = ha_lights.HaLightsOutput(hass, block, now=clock.now)
    return hass, output, positions


async def _runner(func: Callable[..., Any], *args: Any) -> Any:
    return func(*args)


async def _record(
    core: ModuleType,
    run: list[Scenario],
    lights: str = "area",
    places: Mapping[str, str] | None = None,
    glowcast: SimpleNamespace | None = None,
) -> dict[str, Any]:
    pipeline = core.pipeline
    clock = FakeClock()
    source = ScenarioSource(clock, run, core.models)
    area: RecordingOutput | None = None
    hass: FakeHass | None = None
    positions: dict[str, tuple[float, float]] = {}
    chosen: dict[str, str] = {}
    watched: dict[str, WatchedPart] = {}
    output: Any
    if lights == "area":
        output = area = RecordingOutput(core.models)
    else:
        assert glowcast is not None
        chosen = places_of(places, lights)
        hass, output, positions = _ha_lights(glowcast, clock, chosen)
        if lights == "combined":
            area = RecordingOutput(core.models)
            watched[HA_LIGHTS_PART] = WatchedPart(output)
            output = glowcast.composite.CompositeOutput(
                [(AREA_PART, area), (HA_LIGHTS_PART, watched[HA_LIGHTS_PART])], clock
            )

    def retrying(part: str) -> bool:
        """A lost part whose retry has not failed (yet)."""
        return part in watched and not watched[part].start_failed

    settings = pipeline.Settings(
        BRIGHTNESS, INTENSITY, pipeline.UpdateRate(UPDATE_RATE)
    )
    lights_ = pipeline.Pipeline(
        source, output, settings, _runner, clock, lambda status: None
    )
    task = asyncio.ensure_future(lights_.run())
    frames: dict[bytes, int] = {}
    samples = []
    start = 0
    begin = 0.0  # when the scenario starts
    for index, scenario in enumerate(run):
        ticks = round(scenario.seconds * SEND_HZ)
        events = sorted((begin + at, room_id) for room_id, at in scenario.unavailable)
        for tick in range(start, start + ticks):
            # Halfway between two sends, so a send and a sample never tie.
            now = (tick + 0.5) / SEND_HZ
            while events and events[0][0] <= now:
                _, room_id = events.pop(0)
                if hass is not None:
                    hass.lights[f"light.{room_id}"].available = False
            await clock.advance_to(now)
            if task.done():
                task.result()
                raise RuntimeError("The pipeline stopped before the scenarios ended")
            frame = None
            if source.last is not None:
                frame = frames.setdefault(source.last, len(frames))
            # A light that was never claimed or is released is off.
            lit = dict(area.colors) if area is not None else {}
            if hass is not None:
                lit |= hass.levels(clock.now())
            samples.append(
                {
                    "scenario": index,
                    "time": round(clock.now(), 6),
                    "frame": frame,
                    "lights": lit,
                    "unavailable": sorted(
                        light.room_id
                        for light in (hass.lights.values() if hass else [])
                        if not light.available
                    ),
                    # Parts of a combined output that do not run: lost and
                    # waiting for their retry, or dropped until the next start.
                    "retrying": sorted(p for p in output.dropped_parts if retrying(p)),
                    "dropped": sorted(
                        p for p in output.dropped_parts if not retrying(p)
                    ),
                }
            )
        start += ticks
        begin += scenario.seconds
    lights_.stop()
    # Advance until the pipeline has cleaned up, but never wait forever on a
    # core/ that does not stop (--compare against another commit).
    for _ in range(STOP_SECONDS):
        if task.done():
            break
        await clock.advance_to(clock.now() + 1)
    else:
        task.cancel()
        raise RuntimeError(f"The pipeline did not stop within {STOP_SECONDS} s")
    task.result()
    return {
        "lights": lights,
        # Each Home Assistant light's place: as given (a preset or "x,z"), where, and
        # whether --place moved it from HA_LIGHTS's.
        "places": {
            room_id: {
                "name": name,
                "x": positions[room_id][0],
                "z": positions[room_id][1],
                "moved": name != HA_LIGHTS[room_id][1],
            }
            for room_id, name in chosen.items()
        },
        "scenarios": [
            {"name": scenario.name, "still": scenario.still} for scenario in run
        ],
        "frames": [pixels.hex() for pixels in frames],
        "samples": samples,
        # What reached Home Assistant for the Home Assistant lights, in order.
        "commands": hass.calls if hass is not None else [],
    }


def record(
    core_folder: Path,
    run: list[Scenario] | None = None,
    timing_folder: Path | None = None,
    lights: str = "area",
    places: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """record_async() in an event loop of its own."""
    return asyncio.run(record_async(core_folder, run, timing_folder, lights, places))


async def record_async(
    core_folder: Path,
    run: list[Scenario] | None = None,
    timing_folder: Path | None = None,
    lights: str = "area",
    places: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Run the scenarios through the `core/` in `core_folder`, sampling the
    lights and the TV picture on every send tick. The timings come from
    `timing_folder`'s `core/` (by default the same), so both sides of a
    comparison have the same frames.

    `lights`: the room's lights as an entertainment area ("area"), as Home
    Assistant lights at `places` ("ha"), or both ("combined": the area as with
    "area", the other lights of HA_LIGHTS as Home Assistant lights). Raises
    NoOutput when the tree of `core_folder` has no such output.
    """
    if lights not in LIGHTS:
        raise ValueError(f"lights is one of {', '.join(LIGHTS)}")
    glowcast = None
    if lights == "area":
        core = load_core(core_folder, f"glowcast_core_{next(_LOADED)}")
    else:
        glowcast = load_glowcast(core_folder, lights)
        core = glowcast.core
    if run is None:
        timing = core
        if timing_folder is not None:
            timing = load_core(timing_folder, f"glowcast_core_{next(_LOADED)}")
        run = scenarios(
            timing.light_state.BLACK_HOLD,
            timing.pipeline.NO_PICTURE_WAIT,
            lights,
            timing.pipeline.LOST_RETRY_DELAY,
        )
    return await _record(core, run, lights, places, glowcast)


def parse_places(values: list[str]) -> dict[str, str]:
    """{light: place} from LIGHT=PLACE arguments (for example floor_lamp=left)."""
    places = {}
    for value in values:
        room_id, separator, chosen = value.partition("=")
        if not separator:
            raise BadPlace(f"{value!r} is not LIGHT=PLACE")
        places[room_id] = chosen
    return places


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--core", type=Path, required=True)
    parser.add_argument("--timing-from", type=Path)
    parser.add_argument("--lights", choices=LIGHTS, default="area")
    parser.add_argument("--place", action="append", default=[])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        places = parse_places(args.place)
        places_of(places, args.lights)  # the lights, before anything runs
    except BadPlace as err:
        sys.exit(str(err))
    try:
        recording = record(
            args.core, timing_folder=args.timing_from, lights=args.lights, places=places
        )
    except NoOutput as err:
        print(err, file=sys.stderr)
        sys.exit(EXIT_NO_OUTPUT)
    except BadPlace as err:
        # The presets are the Home Assistant lights' own, known once they are
        # imported; a place is checked before the pipeline runs.
        sys.exit(str(err))
    args.out.write_text(json.dumps(recording))


if __name__ == "__main__":
    main()
