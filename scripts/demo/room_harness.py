"""Runs fixed scenario pictures through Glowcast's own pipeline, on a fake clock.

The pipeline, the colour engine and the light state are `core/`'s own: the
fade, the hold on black, the glow, brightness and the release after no picture
are exactly what a real output would get. Only the TV (a source that makes the
scenario pictures in memory) and the lights (an output that records what it
gets) are fakes, and time only moves when the harness steps the clock.

`core/` is loaded from a folder by path, without the Home Assistant package
around it, so the same harness runs against another commit's `core/` for
`demo.py room --compare`. Standard library only: the drawing is in room.py.

Usage (room.py runs it; the result is JSON):
    python room_harness.py --core DIR [--timing-from DIR] --out FILE
"""

import argparse
import asyncio
import heapq
import importlib.util
import itertools
import json
import math
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

WIDTH, HEIGHT = 64, 36  # as the webOS source delivers them
SEND_HZ = 25  # as Hue's entertainment stream (max_rate_hz)
# About as long as a capture from the TV takes (about 4 per second).
CAPTURE_SECONDS = 0.24
# Glowcast's default settings (const.py, which belongs to the Home Assistant
# package and is not imported here).
BRIGHTNESS = 80
INTENSITY = 100
UPDATE_RATE = "high"

# The lights in the illustrated room and the channel each one stands for, with
# its position (x: -1 left of the screen, +1 right; z: -1 floor, +1 ceiling).
# room.py draws each light by its id; a new light in the room (loose lights,
# #76) gets a row here and a drawing there.
CHANNELS = [
    ("left", -1.0, 0.0),  # the strip's left side
    ("top_left", -0.5, 1.0),  # the strip's top, left third
    ("top", 0.0, 1.0),  # the strip's top, middle
    ("top_right", 0.5, 1.0),  # the strip's top, right third
    ("right", 1.0, 0.0),  # the strip's right side
    ("floor_lamp", -1.0, -0.5),  # the floor lamp, left of the TV
    ("light_bar", 1.0, -0.5),  # the light bar on the console, right
]

RGB = tuple[int, int, int]
Picture = Callable[[float], bytes]  # seconds into the scenario -> pixels


@dataclass(frozen=True)
class Scenario:
    """A named run of pictures; `still` is the moment (0-1) that shows it best."""

    name: str
    seconds: float
    picture: Picture
    still: float = 1.0


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
    """Warm on the left, pink in the middle, blue on the right; a sun left."""
    if math.hypot((x - 0.22) * 16 / 9, y - 0.5) < 0.1:
        return 255, 226, 176
    sky = _across([(255, 154, 74), (224, 122, 168), (61, 123, 255)], x)
    if y < 0.58:
        return sky
    return _mix(sky, (16, 16, 32), 0.4)  # the sea, darker


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
    """A 2.39:1 film in a 16:9 picture: black bars above and below."""
    bar = (1 - (16 / 9) / 2.39) / 2
    if y < bar or y > 1 - bar:
        return 0, 0, 0
    if y < 0.55:
        return _across([(64, 170, 210), (120, 200, 230)], x)  # sky
    return _across([(70, 140, 60), (150, 175, 70)], x)  # field


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


def scenarios(black_hold: float, no_picture_wait: float) -> list[Scenario]:
    """The fixed scenarios, in order; the timings follow `core/`."""
    return [
        Scenario("1 Sunset", 2.5, _still(_sunset)),
        # Holds the last colours for BLACK_HOLD, then fades to the glow.
        Scenario("2 Sudden dark picture", black_hold + 2.0, _still(_night)),
        Scenario("3 Saturated red", 2.0, _still(_red)),
        Scenario("4 Letterbox", 2.0, _still(_letterbox)),
        Scenario(
            "5 Fade between scenes", 3.0, _crossfade(_ocean, _meadow, 0.5, 2.0), 0.6
        ),
        # All-zero frames: released after NO_PICTURE_WAIT.
        Scenario("6 No picture", no_picture_wait + 2.0, _still(lambda x, y: (0, 0, 0))),
    ]


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
        setattr(package, module, importlib.import_module(f"{name}.{module}"))
    return package


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

    def __init__(self, models: ModuleType) -> None:
        self._models = models
        self.claimed = False
        self.colors: dict[str, list[float]] = {}

    async def start(self) -> list[Any]:
        self.claimed = True
        return [self._models.Channel(id, id, x, z) for id, x, z in CHANNELS]

    def send(self, colors: list[Any]) -> None:
        self.colors = {color.channel_id: list(color.rgb) for color in colors}

    async def check(self) -> None:
        pass

    async def stop(self) -> None:
        self.claimed = False
        self.colors = {}

    async def close(self) -> None:
        pass


async def _runner(func: Callable[..., Any], *args: Any) -> Any:
    return func(*args)


async def _record(core: ModuleType, run: list[Scenario]) -> dict[str, Any]:
    pipeline = core.pipeline
    clock = FakeClock()
    source = ScenarioSource(clock, run, core.models)
    output = RecordingOutput(core.models)
    settings = pipeline.Settings(
        BRIGHTNESS, INTENSITY, pipeline.UpdateRate(UPDATE_RATE)
    )
    lights = pipeline.Pipeline(
        source, output, settings, _runner, clock, lambda status: None
    )
    task = asyncio.ensure_future(lights.run())
    frames: dict[bytes, int] = {}
    samples = []
    start = 0
    for index, scenario in enumerate(run):
        ticks = round(scenario.seconds * SEND_HZ)
        for tick in range(start, start + ticks):
            # Halfway between two sends, so a send and a sample never tie.
            await clock.advance_to((tick + 0.5) / SEND_HZ)
            if task.done():
                task.result()
                raise RuntimeError("The pipeline stopped before the scenarios ended")
            frame = None
            if source.last is not None:
                frame = frames.setdefault(source.last, len(frames))
            samples.append(
                {
                    "scenario": index,
                    "time": round(clock.now(), 6),
                    "frame": frame,
                    # A light that was never claimed or is released is off.
                    "lights": dict(output.colors) if output.claimed else {},
                }
            )
        start += ticks
    lights.stop()
    await clock.advance_to(clock.now() + 1)
    await task
    return {
        "scenarios": [
            {"name": scenario.name, "still": scenario.still} for scenario in run
        ],
        "frames": [pixels.hex() for pixels in frames],
        "samples": samples,
    }


def record(
    core_folder: Path,
    run: list[Scenario] | None = None,
    timing_folder: Path | None = None,
) -> dict[str, Any]:
    """record_async() in an event loop of its own."""
    return asyncio.run(record_async(core_folder, run, timing_folder))


async def record_async(
    core_folder: Path,
    run: list[Scenario] | None = None,
    timing_folder: Path | None = None,
) -> dict[str, Any]:
    """Run the scenarios through the `core/` in `core_folder`, sampling the
    lights and the TV picture on every send tick. The timings come from
    `timing_folder`'s `core/` (by default the same), so both sides of a
    comparison have the same frames."""
    core = load_core(core_folder, f"glowcast_core_{next(_LOADED)}")
    if run is None:
        timing = core
        if timing_folder is not None:
            timing = load_core(timing_folder, f"glowcast_core_{next(_LOADED)}")
        run = scenarios(timing.light_state.BLACK_HOLD, timing.pipeline.NO_PICTURE_WAIT)
    return await _record(core, run)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--core", type=Path, required=True)
    parser.add_argument("--timing-from", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    recording = record(args.core, timing_folder=args.timing_from)
    args.out.write_text(json.dumps(recording))


if __name__ == "__main__":
    main()
