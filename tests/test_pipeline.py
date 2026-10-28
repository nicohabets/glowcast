"""Tests for the pipeline with a fake source, a fake output and a fake clock.

No Home Assistant test framework (architecture D13).
"""

import asyncio
import heapq
import itertools
from collections.abc import Callable
from typing import Any

import pytest

from custom_components.glowcast.core.models import Channel, ChannelColor, Frame
from custom_components.glowcast.core.pipeline import (
    Pipeline,
    Settings,
    StopReason,
    UpdateRate,
)
from custom_components.glowcast.core.ports import (
    OutputAuthFailed,
    OutputBusy,
    OutputLost,
    OutputNotFound,
    OutputTooManyChannels,
    OutputUnavailable,
    SourceUnavailable,
)

RED = (255, 0, 0)
BLUE = (0, 0, 255)
BLACK = (0, 0, 0)
CHANNEL = Channel(id="1", name="one", x=0.0, z=0.0)
TICK = 1 / 25


def solid(rgb: tuple[int, int, int]) -> Frame:
    return Frame(width=8, height=4, pixels=bytes(rgb) * 32)


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


async def settle() -> None:
    for _ in range(20):
        await asyncio.sleep(0)


class FakeSource:
    """A TV. `frames` scripts the captures: a Frame, an exception, or "hang"."""

    def __init__(self, clock: FakeClock, log: list[str]) -> None:
        self.clock = clock
        self.log = log
        self.start_failures = 0
        self.on = True
        self.frames: list[Any] = []
        self.default: Any = solid(RED)
        self.capture_time = 0.25
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
    """The lights. `start_errors` scripts start(); `check_error` fails checks."""

    max_rate_hz = 25.0

    def __init__(self, clock: FakeClock, log: list[str]) -> None:
        self.clock = clock
        self.log = log
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
        self.log.append("output.stop")

    async def close(self) -> None:
        self.log.append("output.close")

    def rgb_at(self, index: int) -> tuple[float, float, float]:
        return self.sent[index][1][0].rgb


CLEANUP = ["output.stop", "output.close", "source.close"]


class Harness:
    def __init__(
        self,
        brightness: int = 100,
        intensity: int = 100,
        rate: UpdateRate = UpdateRate.HIGH,
    ) -> None:
        self.clock = FakeClock()
        self.log: list[str] = []
        self.source = FakeSource(self.clock, self.log)
        self.output = FakeOutput(self.clock, self.log)
        self.reasons: list[StopReason] = []
        self.compute_error: Exception | None = None
        self.pipeline = Pipeline(
            self.source,
            self.output,
            Settings(brightness, intensity, rate),
            self._runner,
            self.clock,
            self.reasons.append,
        )
        self.task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self.task = asyncio.ensure_future(self.pipeline.run())
        await settle()

    async def finish(self, limit: float = 30) -> StopReason:
        """Advance time until the pipeline has stopped."""
        assert self.task is not None
        waited = 0.0
        while not self.task.done() and waited < limit:
            await self.clock.advance(0.5)
            waited += 0.5
        assert self.task.done(), "the pipeline did not stop"
        await self.task
        assert len(self.reasons) == 1
        return self.reasons[0]

    async def stop(self) -> StopReason:
        self.pipeline.stop()
        return await self.finish()

    async def _runner(self, func: Callable[..., Any], *args: Any) -> Any:
        if self.compute_error is not None:
            raise self.compute_error
        return func(*args)


def approx_rgb(rgb: tuple[float, float, float]) -> Any:
    return pytest.approx(rgb, abs=0.005)


# Start order


async def test_tv_never_comes_up_lights_untouched() -> None:
    h = Harness()
    h.source.start_failures = 1000
    await h.start()
    assert await h.finish() == StopReason.TV_OFF
    assert "output.start" not in h.log
    assert h.output.sent == []
    assert h.log[-3:] == CLEANUP
    assert 10 <= h.clock.now() <= 11


async def test_tv_reports_off_during_the_whole_grace() -> None:
    h = Harness()
    h.source.on = False
    await h.start()
    assert await h.finish() == StopReason.TV_OFF
    assert "output.start" not in h.log


async def test_tv_still_waking_up_is_retried() -> None:
    h = Harness()
    h.source.start_failures = 3
    await h.start()
    await h.clock.advance(6)
    assert h.log.count("source.start") == 4
    assert "output.start" in h.log
    assert h.output.sent
    assert await h.stop() == StopReason.USER


async def test_failed_captures_during_the_grace_do_not_count() -> None:
    h = Harness()
    h.source.frames = [SourceUnavailable("waking")] * 6
    await h.start()
    await h.clock.advance(9)
    assert "output.start" in h.log
    assert not h.task.done()
    assert await h.stop() == StopReason.USER


async def test_a_capture_hanging_past_the_grace_gives_tv_off() -> None:
    h = Harness()
    h.source.frames = ["hang", "hang", "hang"]
    h.source.start_failures = 0
    await h.start()
    assert await h.finish() == StopReason.TV_OFF
    assert "output.start" not in h.log


# Fading and rhythm


async def test_first_frame_is_shown_straight_away() -> None:
    h = Harness()
    await h.start()
    await h.clock.advance(0.3)
    assert h.output.rgb_at(0) == approx_rgb((1.0, 0.05, 0.05))
    assert await h.stop() == StopReason.USER


async def test_fades_linearly_over_the_measured_capture_interval() -> None:
    h = Harness(rate=UpdateRate.LOW)  # one capture per second
    h.source.frames = [solid(RED), solid(RED)]
    h.source.default = solid(BLUE)
    await h.start()
    # Captures take 0.25 s and start every second: they complete at 0.25
    # (startup), 0.5 and 1.5 s, so blue arrives at 1.5 s after an interval of 1 s.
    await h.clock.advance(2.0)
    blue_at = 1.5
    interval = 1.0  # measured between the last two captures
    sends = [(t, c[0].rgb) for t, c in h.output.sent if blue_at <= t]
    for t, rgb in sends:
        p = min(1.0, (t - blue_at) / interval)
        assert rgb[0] == pytest.approx(max(1.0 - p, 0.05), abs=0.01)
        assert rgb[2] == pytest.approx(max(p, 0.05), abs=0.01)
    await h.clock.advance(1.0)
    # The next capture arrives one tick before a fade ends, so the colour
    # trails the target by a fraction and converges.
    assert h.output.sent[-1][1][0].rgb == pytest.approx((0.05, 0.05, 1.0), abs=0.01)
    steps = [t for t, _ in h.output.sent if blue_at <= t <= blue_at + interval]
    assert all(b - a == pytest.approx(TICK) for a, b in itertools.pairwise(steps))
    assert await h.stop() == StopReason.USER


async def test_sends_keep_their_rhythm_while_a_capture_hangs() -> None:
    h = Harness()
    await h.start()
    await h.clock.advance(1)
    h.source.frames = ["hang"]
    before = len(h.output.sent)
    await h.clock.advance(5)
    assert len(h.output.sent) - before == pytest.approx(125, abs=1)
    assert await h.stop() == StopReason.USER


async def test_sends_keep_their_rhythm_while_a_check_hangs() -> None:
    h = Harness()
    await h.start()
    h.output.check_hangs = True
    await h.clock.advance(1)
    before = len(h.output.sent)
    await h.clock.advance(5)
    assert len(h.output.sent) - before == pytest.approx(125, abs=1)
    assert await h.stop() == StopReason.USER


async def test_output_is_checked_continuously() -> None:
    h = Harness()
    await h.start()
    await h.clock.advance(2)
    assert h.output.checks >= 40  # a new check whenever none is running
    assert await h.stop() == StopReason.USER


@pytest.mark.parametrize(
    ("rate", "per_second"),
    [
        (UpdateRate.LOW, 1),
        (UpdateRate.MEDIUM, 2),
        (UpdateRate.HIGH, 3),
        (UpdateRate.MAXIMUM, 4),  # as fast as the TV allows: 0.25 s per capture
    ],
)
async def test_update_rate(rate: UpdateRate, per_second: int) -> None:
    h = Harness(rate=rate)
    await h.start()
    await h.clock.advance(1)
    before = h.source.captures
    await h.clock.advance(10)
    assert h.source.captures - before == pytest.approx(10 * per_second, abs=1)
    assert await h.stop() == StopReason.USER


# Colour


@pytest.mark.parametrize(
    ("brightness", "frame", "expected"),
    [
        (40, solid(RED), (0.40, 0.05, 0.05)),
        (40, solid(BLACK), (0.05, 0.05, 0.05)),
        (3, solid(BLACK), (0.03, 0.03, 0.03)),
        (3, solid(RED), (0.03, 0.03, 0.03)),
        (100, solid(RED), (1.0, 0.05, 0.05)),
    ],
)
async def test_brightness_and_minimal_glow(
    brightness: int, frame: Frame, expected: tuple[float, float, float]
) -> None:
    h = Harness(brightness=brightness)
    h.source.default = frame
    await h.start()
    await h.clock.advance(0.5)
    assert h.output.rgb_at(-1) == approx_rgb(expected)
    assert await h.stop() == StopReason.USER


async def test_intensity_is_passed_as_a_factor() -> None:
    h = Harness(intensity=0)  # 0 % = no saturation
    h.source.default = solid((200, 100, 100))
    await h.start()
    await h.clock.advance(0.5)
    r, g, b = h.output.rgb_at(-1)
    assert r == pytest.approx(g) == pytest.approx(b)
    assert await h.stop() == StopReason.USER


# The TV turns off


async def test_tv_reports_off() -> None:
    h = Harness()
    await h.start()
    await h.clock.advance(2)
    h.source.on = False
    assert await h.finish() == StopReason.TV_OFF
    assert h.log[-3:] == CLEANUP


async def test_five_failed_captures_in_a_row() -> None:
    h = Harness()
    await h.start()
    await h.clock.advance(1)
    h.source.default = SourceUnavailable("gone")
    assert await h.finish() == StopReason.TV_OFF
    assert h.log[-3:] == CLEANUP


async def test_four_failed_captures_then_a_good_one() -> None:
    h = Harness()
    await h.start()
    await h.clock.advance(1)
    h.source.frames = [SourceUnavailable("hiccup")] * 4
    await h.clock.advance(5)
    assert not h.task.done()
    assert await h.stop() == StopReason.USER


# The stream is lost


async def test_stream_lost_retries_once_and_continues() -> None:
    h = Harness()
    await h.start()
    await h.clock.advance(1)
    h.output.check_error = OutputLost("dropped")
    await h.clock.advance(0.1)
    h.output.check_error = None
    assert h.log[-2:] == ["output.stop", "output.close"]
    await h.clock.advance(4.5)
    assert h.log.count("output.start") == 1  # waits 5 s
    await h.clock.advance(1)
    assert h.log.count("output.start") == 2
    sends = len(h.output.sent)
    await h.clock.advance(1)
    assert len(h.output.sent) > sends
    assert await h.stop() == StopReason.USER


@pytest.mark.parametrize(
    ("retry_error", "reason"),
    [
        (OutputBusy("taken"), StopReason.OUTPUT_LOST),
        (OutputUnavailable("rebooting"), StopReason.OUTPUT_LOST),
        (OutputAuthFailed("revoked"), StopReason.AUTH_FAILED),
    ],
)
async def test_stream_lost_and_the_retry_fails(
    retry_error: Exception, reason: StopReason
) -> None:
    h = Harness()
    h.output.start_errors = [None, retry_error]
    await h.start()
    await h.clock.advance(1)
    h.output.check_error = OutputLost("dropped")
    assert await h.finish() == reason
    assert h.log[-3:] == CLEANUP


async def lose_stream(h: Harness) -> None:
    """Make the next check raise OutputLost once."""
    h.output.check_error = OutputLost("dropped")
    await h.clock.advance(0.1)
    h.output.check_error = None


async def test_switched_off_during_the_retry_wait() -> None:
    h = Harness()
    await h.start()
    await h.clock.advance(1)
    await lose_stream(h)
    await h.clock.advance(2)
    assert await h.stop() == StopReason.USER
    assert h.log.count("output.start") == 1  # the lights are not claimed again
    assert h.log[-3:] == CLEANUP


async def test_switched_off_while_the_retry_starts() -> None:
    h = Harness()
    h.output.start_errors = [None, "hang"]
    await h.start()
    await h.clock.advance(1)
    await lose_stream(h)
    await h.clock.advance(6)
    assert h.log.count("output.start") == 2
    assert await h.stop() == StopReason.USER
    assert h.log[-3:] == CLEANUP


async def test_tv_turns_off_during_the_retry_wait() -> None:
    h = Harness()
    await h.start()
    await h.clock.advance(1)
    await lose_stream(h)
    await h.clock.advance(2)
    h.source.on = False
    assert await h.finish() == StopReason.TV_OFF
    assert h.log.count("output.start") == 1
    assert h.log[-3:] == CLEANUP


async def test_stream_lost_again_soon_after_a_retry() -> None:
    h = Harness()
    await h.start()
    await h.clock.advance(1)
    h.output.check_error = OutputLost("keeps dropping")
    assert await h.finish(limit=120) == StopReason.OUTPUT_LOST
    assert h.log.count("output.start") == 2  # one retry, then stop
    assert h.log[-3:] == CLEANUP


async def test_stream_lost_again_after_a_minute_gets_a_new_retry() -> None:
    h = Harness()
    await h.start()
    await h.clock.advance(1)
    await lose_stream(h)
    await h.clock.advance(70)  # retried after 5 s, then a minute of good streaming
    await lose_stream(h)
    await h.clock.advance(6)
    assert h.log.count("output.start") == 3
    assert not h.task.done()
    assert await h.stop() == StopReason.USER


async def test_colours_continue_after_a_retry() -> None:
    h = Harness(rate=UpdateRate.LOW)
    await h.start()
    await h.clock.advance(1)
    await lose_stream(h)
    await h.clock.advance(5.05)  # just after the retry, before the next capture
    after = [c for t, c in h.output.sent if t > 6]
    assert after
    assert all(colors == [ChannelColor("1", (1.0, 0.05, 0.05))] for colors in after)
    assert await h.stop() == StopReason.USER


async def test_compute_error_stops_with_source_unavailable() -> None:
    h = Harness()
    await h.start()
    await h.clock.advance(1)
    h.compute_error = ValueError("odd frame")
    assert await h.finish() == StopReason.SOURCE_UNAVAILABLE
    assert h.log[-3:] == CLEANUP


async def test_key_revoked_while_streaming() -> None:
    h = Harness()
    await h.start()
    await h.clock.advance(1)
    h.output.check_error = OutputAuthFailed("revoked")
    assert await h.finish() == StopReason.AUTH_FAILED
    assert h.log[-3:] == CLEANUP


# Errors at start


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (OutputBusy("busy"), StopReason.OUTPUT_BUSY),
        (OutputNotFound("gone"), StopReason.OUTPUT_NOT_FOUND),
        (OutputAuthFailed("revoked"), StopReason.AUTH_FAILED),
        (OutputTooManyChannels("21"), StopReason.OUTPUT_TOO_MANY_CHANNELS),
        (OutputUnavailable("offline"), StopReason.OUTPUT_UNAVAILABLE),
    ],
)
async def test_output_cannot_start(error: Exception, reason: StopReason) -> None:
    h = Harness()
    h.output.start_errors = [error]
    await h.start()
    assert await h.finish() == reason
    assert h.output.sent == []
    assert h.log == ["source.start", "output.start", *CLEANUP]


async def test_unexpected_source_error() -> None:
    h = Harness()
    h.source.frames = [RuntimeError("bug")]
    await h.start()
    assert await h.finish() == StopReason.SOURCE_UNAVAILABLE
    assert h.log[-3:] == CLEANUP


# Stopping and cleanup


async def test_user_stop() -> None:
    h = Harness()
    await h.start()
    await h.clock.advance(2)
    assert await h.stop() == StopReason.USER
    assert h.log == ["source.start", "output.start", *CLEANUP]


async def test_user_stop_during_the_grace() -> None:
    h = Harness()
    h.source.start_failures = 1000
    await h.start()
    await h.clock.advance(3)
    assert await h.stop() == StopReason.USER
    assert "output.start" not in h.log
    assert h.log[-3:] == CLEANUP


@pytest.mark.parametrize("when", [0.1, 3.0], ids=["during start", "while running"])
async def test_cancelling_the_task_still_cleans_up(when: float) -> None:
    h = Harness()
    await h.start()
    await h.clock.advance(when)
    h.task.cancel()
    await settle()
    h.task.cancel()  # cancelled again while cleaning up
    with pytest.raises(asyncio.CancelledError):
        await h.task
    assert h.log[-3:] == CLEANUP
    assert h.reasons == [StopReason.USER]


async def test_cleanup_continues_after_a_failing_step() -> None:
    h = Harness()

    async def broken_stop() -> None:
        h.log.append("output.stop")
        raise RuntimeError("bridge gone")

    h.output.stop = broken_stop  # type: ignore[method-assign]
    await h.start()
    await h.clock.advance(1)
    assert await h.stop() == StopReason.USER
    assert h.log[-3:] == CLEANUP
