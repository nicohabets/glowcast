"""Fakes shared by the pipeline tests and the Home Assistant tests.

No Home Assistant imports (architecture D13). The source and the output take
any clock: the pipeline tests pass a FakeClock, the Home Assistant tests the
real one.
"""

import asyncio
import heapq
import itertools
from typing import Any

from custom_components.glowcast.core.models import Channel, ChannelColor, Frame
from custom_components.glowcast.core.pipeline import Clock
from custom_components.glowcast.core.ports import SourceUnavailable

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
    """A TV. `frames` scripts the captures: a Frame, an exception, or "hang"."""

    def __init__(
        self, clock: Clock, log: list[str], capture_time: float = 0.25
    ) -> None:
        self.clock = clock
        self.log = log
        self.start_failures = 0
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

    def __init__(self, clock: Clock, log: list[str], slow_stop: bool = False) -> None:
        self.clock = clock
        self.log = log
        self.slow_stop = slow_stop
        self.start_errors: list[Any] = []  # None, an exception, or "hang"
        self.check_error: Exception | None = None
        self.check_hangs = False
        self.sent: list[tuple[float, list[ChannelColor]]] = []
        self.checks = 0

    async def start(self) -> list[Channel]:
        self.log.append("output.start")
        if self.start_errors and (error := self.start_errors.pop(0)):
            if error == "hang":
                await asyncio.Event().wait()
            raise error
        return [CHANNEL]

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
