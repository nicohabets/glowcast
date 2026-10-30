"""Tests for the combined output (#11) with fake parts and a fake clock.

No Home Assistant: the composite is generic and knows only the port.
"""

import asyncio
import logging

import pytest

from custom_components.glowcast.core.models import Channel, ChannelColor
from custom_components.glowcast.core.pipeline import (
    Phase,
    Pipeline,
    PipelineStatus,
    Settings,
    StopReason,
    UpdateRate,
)
from custom_components.glowcast.core.ports import (
    OutputAuthFailed,
    OutputBusy,
    OutputGone,
    OutputLost,
    OutputNotFound,
    OutputUnavailable,
)
from custom_components.glowcast.outputs.composite import CompositeOutput

from .fakes import FakeClock, FakeOutput, FakeSource, settle

AREA_CHANNELS = [Channel("0", "left", -1, 0), Channel("1", "right", 1, 0)]
LIGHT_CHANNELS = [Channel("0", "lamp", 0, 0)]


class Parts:
    """An entertainment area at 25 per second and Home Assistant lights at 4."""

    def __init__(self) -> None:
        self.clock = FakeClock()
        self.area_log: list[str] = []
        self.lights_log: list[str] = []
        self.area = FakeOutput(self.clock, self.area_log, channels=AREA_CHANNELS)
        self.lights = FakeOutput(self.clock, self.lights_log, channels=LIGHT_CHANNELS)
        self.lights.max_rate_hz = 4.0
        self.output = CompositeOutput(
            [("area", self.area), ("lights", self.lights)], self.clock
        )

    async def tick(self, seconds: float, step: float = 1 / 25) -> None:
        """Send and check like the pipeline's send loop for `seconds`."""
        for _ in range(round(seconds / step)):
            self.output.send(colors(self.channels))
            await self.output.check()
            await self.clock.advance(step)

    channels: list[Channel] = []

    async def start(self) -> list[Channel]:
        self.channels = await self.output.start()
        return self.channels


def colors(channels: list[Channel]) -> list[ChannelColor]:
    return [ChannelColor(channel.id, (1.0, 0.0, 0.0)) for channel in channels]


async def test_channel_ids_are_unique_across_parts() -> None:
    p = Parts()
    channels = await p.start()
    assert [c.id for c in channels] == ["0/0", "0/1", "1/0"]
    assert [c.name for c in channels] == ["left", "right", "lamp"]
    assert (channels[1].x, channels[1].z) == (1, 0)


async def test_the_parts_starting_colours_are_passed_on() -> None:
    """What each part's lights showed before, for the fade-in (#153)."""
    p = Parts()
    p.area.channels = [
        Channel("0", "left", -1, 0, start_rgb=(0.8, 0.6, 0.4)),
        Channel("1", "right", 1, 0),  # this part could not read it
    ]
    p.lights.channels = [Channel("0", "lamp", 0, 0, start_rgb=(0.0, 0.0, 0.0))]
    channels = await p.start()
    assert [(c.id, c.start_rgb) for c in channels] == [
        ("0/0", (0.8, 0.6, 0.4)),
        ("0/1", None),
        ("1/0", (0.0, 0.0, 0.0)),
    ]


async def test_a_retry_of_the_whole_output_is_a_retry_of_each_part() -> None:
    p = Parts()
    await p.output.start(retry=True)
    assert p.area.retries == p.lights.retries == [True]


async def test_rate_is_the_fastest_part() -> None:
    assert Parts().output.max_rate_hz == 25.0


async def test_each_part_gets_its_own_colours() -> None:
    p = Parts()
    await p.start()
    p.output.send(colors(p.channels))
    assert [c.channel_id for c in p.area.sent[0][1]] == ["0", "1"]
    assert [c.channel_id for c in p.lights.sent[0][1]] == ["0"]


async def test_each_part_at_its_own_rate() -> None:
    p = Parts()
    await p.start()
    await p.tick(2.0)
    assert len(p.area.sent) == 50  # every tick: the area is not held back
    assert len(p.lights.sent) == 8  # 4 per second
    times = [t for t, _ in p.lights.sent]
    gaps = [b - a for a, b in zip(times, times[1:], strict=False)]
    # On the tick nearest to when it is due (ticks every 0.04 s).
    assert all(gap == pytest.approx(0.25, abs=0.04) for gap in gaps)


async def test_a_part_that_cannot_start_is_dropped() -> None:
    p = Parts()
    p.area.start_errors = [OutputBusy("another app streams")]
    channels = await p.start()
    assert [c.id for c in channels] == ["1/0"]
    assert list(p.output.dropped_parts) == ["area"]
    assert isinstance(p.output.dropped_parts["area"], OutputBusy)
    await p.tick(1)
    assert p.area.sent == []
    assert p.lights.sent


async def test_parts_dropped_from_the_outset_are_reported() -> None:
    clock = FakeClock()
    lights = FakeOutput(clock, [], channels=LIGHT_CHANNELS)
    output = CompositeOutput(
        [("lights", lights)], clock, {"area": OutputUnavailable("not set up")}
    )
    assert [c.id for c in await output.start()] == ["0/0"]
    assert list(output.dropped_parts) == ["area"]


async def test_no_part_can_start_raises_the_first_error() -> None:
    p = Parts()
    p.area.start_errors = [OutputAuthFailed("revoked")]
    p.lights.start_errors = [OutputNotFound("no lights")]
    with pytest.raises(OutputAuthFailed):
        await p.start()
    assert set(p.output.dropped_parts) == {"area", "lights"}


async def test_lost_part_is_retried_after_5_s_while_the_rest_runs() -> None:
    p = Parts()
    await p.start()
    await p.tick(1)
    p.area.check_error = OutputLost("stream dropped")
    await p.tick(0.2)
    p.area.check_error = None
    assert p.area_log[-2:] == ["output.stop", "output.close"]
    assert "area" in p.output.dropped_parts
    area_sends, light_sends = len(p.area.sent), len(p.lights.sent)
    await p.tick(4.5)
    assert len(p.area.sent) == area_sends  # released, waiting
    assert len(p.lights.sent) > light_sends + 15  # without interruption
    assert p.area_log.count("output.start") == 1
    await p.tick(1)
    assert p.area_log.count("output.start") == 2
    # Its retry: it need not read what its lights show (#153).
    assert p.area.retries == [False, True]
    assert p.lights.retries == [False]
    assert p.output.dropped_parts == {}
    sends = len(p.area.sent)
    await p.tick(0.4)
    assert len(p.area.sent) == sends + 10


async def test_retry_fails_and_the_part_stays_dropped() -> None:
    p = Parts()
    p.area.start_errors = [None, OutputBusy("taken")]
    await p.start()
    await p.tick(1)
    p.area.check_error = OutputLost("stream dropped")
    await p.tick(6)
    assert p.area_log.count("output.start") == 2
    assert isinstance(p.output.dropped_parts["area"], OutputBusy)
    await p.tick(20)
    assert p.area_log.count("output.start") == 2  # no second retry
    # Stopping and starting again (the pipeline's no-picture release and
    # claim) keeps it dropped; only a new start (a new output) tries again.
    await p.output.stop()
    await p.output.close()
    await p.start()
    assert p.area_log.count("output.start") == 2
    assert [c.id for c in p.channels] == ["1/0"]


async def test_lost_again_soon_after_a_successful_retry() -> None:
    p = Parts()
    await p.start()
    await p.tick(1)
    p.area.check_error = OutputLost("stream dropped")
    await p.tick(0.1)
    p.area.check_error = None
    await p.tick(6)
    assert p.output.dropped_parts == {}
    await p.tick(30)  # within 60 s of the retry
    p.area.check_error = OutputLost("dropped again")
    await p.tick(10)
    assert p.area_log.count("output.start") == 2
    assert "area" in p.output.dropped_parts
    assert p.lights.sent[-1][0] == pytest.approx(p.clock.now(), abs=0.3)


async def test_lost_again_after_a_minute_gets_a_new_retry() -> None:
    p = Parts()
    await p.start()
    await p.tick(1)
    for _ in range(2):
        p.area.check_error = OutputLost("stream dropped")
        await p.tick(0.1)
        p.area.check_error = None
        await p.tick(70)
    assert p.area_log.count("output.start") == 3
    assert p.output.dropped_parts == {}


async def test_rejected_key_drops_the_part_without_a_retry() -> None:
    p = Parts()
    await p.start()
    await p.tick(1)
    p.area.check_error = OutputAuthFailed("revoked")
    await p.tick(10)
    assert p.area_log.count("output.start") == 1
    assert p.area_log[-2:] == ["output.stop", "output.close"]
    assert isinstance(p.output.dropped_parts["area"], OutputAuthFailed)
    assert p.lights.sent[-1][0] == pytest.approx(p.clock.now(), abs=0.3)


async def test_no_part_left_raises_output_gone_once_the_last_retry_fails() -> None:
    p = Parts()
    p.area.start_errors = [OutputBusy("taken")]
    p.lights.start_errors = [None, OutputNotFound("gone")]
    await p.start()
    p.lights.check_error = OutputLost("none available")
    await p.output.check()
    await settle()
    await p.output.check()  # the retry is pending: nothing raised yet
    await p.clock.advance(5)
    await settle()
    with pytest.raises(OutputGone):
        await p.output.check()


async def test_last_part_with_a_rejected_key_raises_auth_failed() -> None:
    p = Parts()
    p.lights.start_errors = [OutputNotFound("gone")]
    await p.start()
    p.area.check_error = OutputAuthFailed("revoked")
    await p.output.check()
    await settle()
    with pytest.raises(OutputAuthFailed):
        await p.output.check()


async def test_stop_and_close_reach_every_part_and_are_safe_twice() -> None:
    p = Parts()
    p.area.start_errors = [OutputBusy("taken")]
    await p.start()
    await p.output.stop()
    await p.output.stop()
    await p.output.close()
    assert p.area_log == ["output.start", "output.stop", "output.stop", "output.close"]
    assert p.lights_log.count("output.stop") == 2
    assert p.lights_log[-1] == "output.close"


async def test_stop_releases_every_part_when_one_fails() -> None:
    p = Parts()
    await p.start()

    async def broken() -> None:
        raise RuntimeError("bridge gone")

    p.area.stop = broken  # type: ignore[method-assign]
    p.area.close = broken  # type: ignore[method-assign]
    await p.output.stop()
    await p.output.close()
    assert p.lights_log[-2:] == ["output.stop", "output.close"]


async def test_stop_during_the_retry_wait_cancels_the_retry() -> None:
    p = Parts()
    await p.start()
    p.area.check_error = OutputLost("dropped")
    await p.tick(1)
    await p.output.stop()
    await p.output.close()
    await p.clock.advance(10)
    assert p.area_log.count("output.start") == 1


async def test_stop_while_a_part_starts_after_its_wait() -> None:
    p = Parts()
    p.area.start_errors = [None, "hang"]
    await p.start()
    p.area.check_error = OutputLost("dropped")
    await p.tick(0.1)
    p.area.check_error = None
    await p.tick(6)
    assert p.area_log.count("output.start") == 2
    await p.output.stop()
    assert p.area_log[-1] == "output.stop"


async def test_one_warning_each_for_lost_and_retry(
    caplog: pytest.LogCaptureFixture,
) -> None:
    p = Parts()
    await p.start()
    await p.tick(1)
    caplog.set_level(logging.WARNING)
    p.area.check_error = OutputLost("stream dropped")
    await p.tick(0.1)
    p.area.check_error = None
    await p.tick(6)
    messages = [r.getMessage() for r in caplog.records]
    assert len(messages) == 2
    assert "area was lost (stream dropped); retrying in 5 s" in messages[0]
    assert "area follows the picture again" in messages[1]


# Through the real pipeline


async def test_pipeline_with_a_combined_output() -> None:
    p = Parts()
    log: list[str] = []
    source = FakeSource(p.clock, log)
    statuses: list[PipelineStatus] = []

    async def runner(func, *args):  # type: ignore[no-untyped-def]
        return func(*args)

    pipeline = Pipeline(
        source,
        p.output,
        Settings(100, 100, UpdateRate.HIGH),
        runner,
        p.clock,
        statuses.append,
    )
    p.lights.start_errors = [None, OutputNotFound("gone")]
    task = asyncio.ensure_future(pipeline.run())
    await p.clock.advance(2)
    assert statuses[-1].phase is Phase.STREAMING
    assert statuses[-1].channels == 3
    assert p.area.sent and p.lights.sent
    p.lights.check_error = OutputLost("none available")
    await p.clock.advance(1)
    assert statuses[-1].dropped_parts == {"lights": StopReason.OUTPUT_LOST}
    await p.clock.advance(6)  # the retry fails: dropped, the area runs on
    assert statuses[-1].dropped_parts == {"lights": StopReason.OUTPUT_NOT_FOUND}
    assert not task.done()
    p.area.check_error = OutputLost("stream dropped")
    p.area.start_errors = [OutputBusy("taken")]
    for _ in range(20):
        await p.clock.advance(1)
        if task.done():
            break
    await task
    assert statuses[-1].phase is Phase.STOPPED
    assert statuses[-1].stop_reason is StopReason.OUTPUT_LOST
    assert Phase.RETRYING not in [s.phase for s in statuses]
    assert p.area_log.count("output.start") == 2  # the composite's retry only
    assert p.area_log[-2:] == ["output.stop", "output.close"]
    assert p.lights_log[-2:] == ["output.stop", "output.close"]
    assert log[-1] == "source.close"


async def test_stop_during_a_slow_release_still_closes_the_part() -> None:
    """Cancelling the retry must not cancel the lost part's release."""
    p = Parts()
    await p.start()
    released = asyncio.Event()

    async def slow_stop() -> None:
        await released.wait()
        p.area_log.append("output.stop")

    p.area.stop = slow_stop  # type: ignore[method-assign]
    p.area.check_error = OutputLost("dropped")
    await p.output.check()
    await settle()
    await p.output.check()  # the retry waits for the release
    stopping = asyncio.ensure_future(p.output.stop())
    await settle()
    released.set()
    await stopping
    # The release ran to its close, then stop() stopped the part once more.
    assert p.area_log[1:3] == ["output.stop", "output.close"]


async def test_a_part_waiting_for_its_retry_is_not_dropped_after_a_stop() -> None:
    """Released for no picture: the part is claimed again at the next start."""
    p = Parts()
    await p.start()
    p.area.check_error = OutputLost("dropped")
    await p.tick(0.2)
    p.area.check_error = None
    assert "area" in p.output.dropped_parts
    await p.output.stop()
    await p.output.close()
    assert p.output.dropped_parts == {}
    channels = await p.start()
    assert [c.id for c in channels] == ["0/0", "0/1", "1/0"]


async def test_no_part_can_start_logs_one_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    p = Parts()
    p.area.start_errors = [OutputBusy("another app streams")]
    p.lights.start_errors = [OutputNotFound("no lights")]
    caplog.set_level(logging.INFO)
    with pytest.raises(OutputBusy):
        await p.start()
    # The pipeline logs the stop with the first error; the other is info.
    assert [r.levelno for r in caplog.records] == [logging.INFO]
    assert "lights could not start either (no lights)" in caplog.text


async def test_last_part_failing_unexpectedly_raises_that_error() -> None:
    """As for a single output: OUTPUT_UNAVAILABLE, not OUTPUT_LOST."""
    p = Parts()
    p.lights.start_errors = [OutputNotFound("gone")]
    await p.start()
    p.area.check_error = RuntimeError("bug")
    await p.output.check()
    await settle()
    with pytest.raises(RuntimeError):
        await p.output.check()


async def test_a_failed_retry_of_the_last_part_is_output_gone() -> None:
    p = Parts()
    p.lights.start_errors = [OutputNotFound("gone")]
    p.area.start_errors = [None, RuntimeError("bug in the retry")]
    await p.start()
    p.area.check_error = OutputLost("dropped")
    await p.output.check()
    await settle()
    await p.output.check()  # the loss is handled: the retry waits
    await p.clock.advance(5)
    await settle()
    with pytest.raises(OutputGone):
        await p.output.check()
