"""Fakes shared by the pipeline tests and the Home Assistant tests.

No Home Assistant imports (architecture D13); the fixtures that put these
fakes into Home Assistant are in conftest.py. The source and the output take
any clock: the pipeline tests pass a FakeClock, the Home Assistant tests the
real one.
"""

import asyncio
import heapq
import itertools
from collections.abc import Callable
from typing import Any

from custom_components.glowcast.core.models import Channel, ChannelColor, Frame
from custom_components.glowcast.core.pipeline import (
    Clock,
    MonotonicClock,
    Phase,
    PipelineStatus,
    Settings,
    StopReason,
    stop_reason_of,
)
from custom_components.glowcast.core.ports import SourceUnavailable

# The screen helper of the screen source's tests (#109, #110): its id, the
# token it hands out on pairing, its generic name and its pairing code.
HELPER_ID = "4f0c9a1b2c3d4e5f6a7b8c9d0e1f2a3b"
HELPER_TOKEN = "Zq3vX8pL1mN7cR4tY9wK2bF6hJ0dS5aG"
HELPER_NAME = "Test PC"
PAIRING_CODE = "123456"

RED = (255, 0, 0)
BLUE = (0, 0, 255)
# A dark scene: black by the colour engine, but not all zero (#68).
DARK = (5, 5, 5)
# Every pixel 0: no picture, as from a protected app (#68).
NO_PICTURE = (0, 0, 0)
CHANNEL = Channel(id="1", name="one", x=0.0, z=0.0)
CLEANUP = ["output.stop", "output.close", "source.close"]


def solid(rgb: tuple[int, int, int]) -> Frame:
    return Frame(width=8, height=4, pixels=bytes(rgb) * 32)


async def settle() -> None:
    for _ in range(20):
        await asyncio.sleep(0)


async def wait_for(condition: Callable[[], Any], limit: float = 2.0) -> None:
    """Wait until `condition()` holds; the real pipeline runs on the real clock."""
    for _ in range(round(limit / 0.01)):
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"not reached within {limit} s")


class FakeClock:
    """Time only moves when the test advances it."""

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

    async def advance(self, seconds: float) -> None:
        end = self.time + seconds
        while True:
            await settle()
            while self._timers and self._timers[0][2].done():
                heapq.heappop(self._timers)
            if not self._timers or self._timers[0][0] > end:
                break
            wake, _, future = heapq.heappop(self._timers)
            self.time = wake
            future.set_result(None)
        self.time = end
        await settle()


class FakeSource:
    """A TV. `frames` scripts the captures: a Frame, an exception, or "hang".

    `start_error` makes every start() raise it, after `start_failures`.
    """

    # As fast as the pipeline allows, like the TV.
    max_rate_hz = 20.0

    def __init__(
        self, clock: Clock, log: list[str], capture_time: float = 0.25
    ) -> None:
        self.clock = clock
        self.log = log
        self.start_failures = 0
        self.start_error: Exception | None = None
        self.on = True
        self.frames: list[Any] = []
        self.default: Any = solid(RED)
        self.capture_time = capture_time
        self.captures = 0

    async def start(self) -> None:
        self.log.append("source.start")
        if self.start_failures:
            self.start_failures -= 1
            raise SourceUnavailable("not reachable yet")
        if self.start_error is not None:
            raise self.start_error

    def is_on(self) -> bool:
        return self.on

    async def capture(self) -> Frame:
        self.captures += 1
        item = self.frames.pop(0) if self.frames else self.default
        if item == "hang":
            await self.clock.sleep(5)
            raise SourceUnavailable("timed out")
        await self.clock.sleep(self.capture_time)
        if isinstance(item, BaseException):
            raise item
        return item

    async def close(self) -> None:
        self.log.append("source.close")


class FakeOutput:
    """The lights. `start_errors` scripts start(); `check_error` fails checks.

    With `slow_stop`, stop() yields once before it is done, like a real
    release that takes a moment.
    """

    max_rate_hz = 25.0

    def __init__(
        self,
        clock: Clock,
        log: list[str],
        slow_stop: bool = False,
        channels: list[Channel] | None = None,
    ) -> None:
        self.clock = clock
        self.log = log
        self.slow_stop = slow_stop
        self.channels = [CHANNEL] if channels is None else channels
        self.start_errors: list[Any] = []  # None, an exception, or "hang"
        self.check_error: Exception | None = None
        self.check_hangs = False
        # Parts of a combined output that do not run (by name).
        self.dropped_parts: dict[str, Exception] = {}
        self.sent: list[tuple[float, list[ChannelColor]]] = []
        self.checks = 0

    async def start(self) -> list[Channel]:
        self.log.append("output.start")
        if self.start_errors and (error := self.start_errors.pop(0)):
            if error == "hang":
                await asyncio.Event().wait()
            raise error
        return list(self.channels)

    def send(self, colors: list[ChannelColor]) -> None:
        self.sent.append((self.clock.now(), colors))

    async def check(self) -> None:
        self.checks += 1
        if self.check_hangs:
            await asyncio.Event().wait()
        if self.check_error is not None:
            raise self.check_error

    async def stop(self) -> None:
        if self.slow_stop:
            await asyncio.sleep(0)
        self.log.append("output.stop")

    async def close(self) -> None:
        self.log.append("output.close")

    def rgb_at(self, index: int) -> tuple[float, float, float]:
        return self.sent[index][1][0].rgb


class FakeAdapters:
    """The TV and the lights the source and output types build, on the real clock.

    `source_missing` and `output_missing` say whether the integration each
    one uses is set up; `channels` are the lights the outputs claim.
    """

    def __init__(self) -> None:
        self.log: list[str] = []
        self.source_missing = False
        self.output_missing = False
        self.channels = [CHANNEL]
        self.sources: list[FakeSource] = []
        self.outputs: list[FakeOutput] = []

    def source(self) -> FakeSource:
        source = FakeSource(MonotonicClock(), self.log, capture_time=0)
        self.sources.append(source)
        return source

    def output(self) -> FakeOutput:
        output = FakeOutput(
            MonotonicClock(), self.log, slow_stop=True, channels=self.channels
        )
        self.outputs.append(output)
        return output


class FakePipeline:
    """Uses the source and output like the real pipeline, without timing.

    `start_error` makes the next pipeline stop before the lights are claimed;
    finish() makes a running one stop by itself.
    """

    instances: list["FakePipeline"] = []
    start_error: StopReason | None = None

    def __init__(
        self,
        source: FakeSource,
        output: Any,
        settings: Any,
        runner: Any,
        clock: Any,
        on_status: Any,
    ) -> None:
        self.source = source
        self.output = output
        self.settings = settings
        self.runner = runner
        self.on_status = on_status
        self.reason = StopReason.USER
        self.updates: list[Settings] = []
        self._stop = asyncio.Event()
        FakePipeline.instances.append(self)

    async def run(self) -> None:
        self.on_status(PipelineStatus(Phase.STARTING))
        try:
            await self.source.start()
            if (error := FakePipeline.start_error) is not None:
                FakePipeline.start_error = None
                self.reason = error
            else:
                await self.output.start()
                self.on_status(
                    PipelineStatus(
                        Phase.STREAMING,
                        channels=1,
                        dropped_parts={
                            name: stop_reason_of(error)
                            for name, error in self.output.dropped_parts.items()
                        },
                    )
                )
                await self._stop.wait()
        except asyncio.CancelledError:
            # Like the real pipeline: release, report USER, re-raise.
            await asyncio.shield(self._release())
            self.on_status(self.stopped(StopReason.USER))
            raise
        await self._release()
        self.on_status(self.stopped(self.reason))

    @staticmethod
    def stopped(reason: StopReason) -> PipelineStatus:
        return PipelineStatus(Phase.STOPPED, stop_reason=reason)

    async def _release(self) -> None:
        await self.output.stop()
        await self.output.close()
        await self.source.close()

    def stop(self) -> None:
        self._stop.set()

    def update_settings(self, settings: Settings) -> None:
        self.updates.append(settings)

    def finish(self, reason: StopReason) -> None:
        self.reason = reason
        self._stop.set()
