"""Tests for the pipeline with a fake source, a fake output and a fake clock.

No Home Assistant test framework (architecture D13).
"""

import asyncio
import itertools
import logging
from collections.abc import Callable
from typing import Any

import pytest

from custom_components.glowcast.core.models import Frame
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
    OutputLost,
    OutputNotFound,
    OutputTooManyChannels,
    OutputUnavailable,
    SourceUnavailable,
)

from .fakes import (
    BLUE,
    CLEANUP,
    DARK,
    NO_PICTURE,
    RED,
    FakeClock,
    FakeOutput,
    FakeSource,
    settle,
    solid,
)

TICK = 1 / 25


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
        self.statuses: list[PipelineStatus] = []
        self.status_times: list[float] = []
        self.status_error: Exception | None = None
        self.compute_error: Exception | None = None
        self.intensities: list[float] = []  # the factor of each colour computation
        self.frames: list[tuple[float, Frame]] = []  # each frame the pipeline got
        self.pipeline = Pipeline(
            self.source,
            self.output,
            Settings(brightness, intensity, rate),
            self._runner,
            self.clock,
            self._on_status,
        )
        self.task: asyncio.Task[None] | None = None

    @property
    def reasons(self) -> list[StopReason]:
        return [s.stop_reason for s in self.statuses if s.phase is Phase.STOPPED]

    @property
    def phases(self) -> list[Phase]:
        """The changes of phase, without the updates of the captures per second."""
        return [phase for phase, _ in itertools.groupby(s.phase for s in self.statuses)]

    def _on_status(self, status: PipelineStatus) -> None:
        self.statuses.append(status)
        self.status_times.append(self.clock.now())
        if self.status_error is not None:
            raise self.status_error

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
        self.intensities.append(args[-1])
        self.frames.append((self.clock.now(), args[0]))
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
    assert h.output.rgb_at(0) == approx_rgb((1.0, 0.0, 0.0))
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
        assert rgb[0] == pytest.approx(1.0 - p, abs=0.01)
        assert rgb[1] == 0.0  # a bright fade gets no glow (#43)
        assert rgb[2] == pytest.approx(p, abs=0.01)
    await h.clock.advance(1.0)
    # The next capture arrives one tick before a fade ends, so the colour
    # trails the target by a fraction and converges.
    assert h.output.sent[-1][1][0].rgb == pytest.approx((0.0, 0.0, 1.0), abs=0.01)
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
        (80, solid(DARK), (0.10, 0.078, 0.050)),
        (40, solid(RED), (0.40, 0.0, 0.0)),
        (20, solid(BLUE), (0.0, 0.0, 0.20)),
        (40, solid(DARK), (0.10, 0.078, 0.050)),
        (8, solid(DARK), (0.08, 0.0624, 0.0400)),
        (3, solid(DARK), (0.03, 0.0234, 0.0150)),
        (3, solid(RED), (0.03, 0.0, 0.0)),
        (100, solid(RED), (1.0, 0.0, 0.0)),
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


# New settings while streaming (#13): no restart, so no new claim of the lights


async def test_new_brightness_applies_at_the_next_send() -> None:
    h = Harness(brightness=100)
    await h.start()
    await h.clock.advance(0.5)
    assert h.output.rgb_at(-1) == approx_rgb((1.0, 0.0, 0.0))
    h.pipeline.update_settings(Settings(40, 100, UpdateRate.HIGH))
    sent = len(h.output.sent)
    await h.clock.advance(TICK)
    assert h.output.rgb_at(sent) == approx_rgb((0.40, 0.0, 0.0))
    assert h.log == ["source.start", "output.start"]  # never stopped or claimed again
    assert await h.stop() == StopReason.USER


async def test_new_intensity_applies_at_the_next_capture() -> None:
    h = Harness(intensity=100)
    h.source.default = solid((200, 100, 100))
    await h.start()
    await h.clock.advance(1)
    captures = h.source.captures
    h.pipeline.update_settings(Settings(100, 0, UpdateRate.HIGH))
    await h.clock.advance(1)
    assert h.source.captures > captures
    # Every computation after the change uses the new intensity.
    assert h.intensities[-(h.source.captures - captures) :] == [0.0] * (
        h.source.captures - captures
    )
    await h.clock.advance(1)  # the fade towards the new colours has finished
    r, g, b = h.output.rgb_at(-1)
    assert r == pytest.approx(g) == pytest.approx(b)
    assert h.log == ["source.start", "output.start"]
    assert await h.stop() == StopReason.USER


async def test_new_update_rate_applies_from_the_next_capture() -> None:
    h = Harness(rate=UpdateRate.LOW)
    await h.start()
    await h.clock.advance(1)
    h.pipeline.update_settings(Settings(100, 100, UpdateRate.MAXIMUM))
    await h.clock.advance(1)  # the wait of the capture before the change
    before = h.source.captures
    await h.clock.advance(10)
    assert h.source.captures - before == pytest.approx(40, abs=1)
    assert h.log == ["source.start", "output.start"]
    assert await h.stop() == StopReason.USER


async def test_new_settings_before_streaming_are_used_once_it_streams() -> None:
    h = Harness(brightness=100)
    h.pipeline.update_settings(Settings(40, 100, UpdateRate.HIGH))
    await h.start()
    await h.clock.advance(0.5)
    assert h.output.rgb_at(-1) == approx_rgb((0.40, 0.0, 0.0))
    assert await h.stop() == StopReason.USER


# Holding the last colours when the picture suddenly turns black (#39)

RED_SENT = (1.0, 0.0, 0.0)  # red at brightness 100 %: no glow mixed in (#43)
GLOW_ONLY = (0.10, 0.078, 0.050)  # black at brightness 100 %


async def test_tv_switched_off_during_the_hold_never_shows_the_glow() -> None:
    h = Harness()
    await h.start()
    await h.clock.advance(2)
    h.source.default = solid(DARK)  # the screen goes black ...
    await h.clock.advance(0.6)
    off_at = h.clock.now()
    h.source.on = False  # ... then the TV reports that it is off
    assert await h.finish() == StopReason.TV_OFF
    after_black = [c[0].rgb for t, c in h.output.sent if t >= 2]
    assert after_black
    assert all(rgb == approx_rgb(RED_SENT) for rgb in after_black)
    assert h.output.sent[-1][0] <= off_at + TICK  # TV_OFF is not delayed
    assert h.log[-3:] == CLEANUP


async def test_a_black_scene_longer_than_the_hold_fades_to_the_glow() -> None:
    h = Harness()
    await h.start()
    await h.clock.advance(2)
    h.source.default = solid(DARK)
    await h.clock.advance(3.6)  # black arrives at about 2.25 s, held for 3.5 s
    assert h.output.rgb_at(-1) == approx_rgb(RED_SENT)
    await h.clock.advance(1.5)
    assert h.output.rgb_at(-1) == approx_rgb(GLOW_ONLY)
    assert await h.stop() == StopReason.USER


def switch_off_animation() -> Frame:
    """Black with one bright pixel: under 5 % of the frame, but inside the region
    of the test channel, like the small element of a TV's switch-off animation."""
    pixels = bytearray(bytes(DARK) * 32)
    pixels[(2 * 8 + 3) * 3 : (2 * 8 + 3) * 3 + 3] = bytes((220, 220, 220))
    return Frame(width=8, height=4, pixels=bytes(pixels))


async def test_a_switch_off_animation_counts_as_black() -> None:
    h = Harness()
    await h.start()
    await h.clock.advance(2)
    h.source.default = switch_off_animation()
    await h.clock.advance(2.5)
    h.source.on = False
    assert await h.finish() == StopReason.TV_OFF
    after = [c[0].rgb for t, c in h.output.sent if t >= 2]
    assert all(rgb == approx_rgb(RED_SENT) for rgb in after)


async def test_black_shorter_than_the_hold_goes_straight_to_the_new_picture() -> None:
    h = Harness()
    await h.start()
    await h.clock.advance(2)
    h.source.frames = [solid(DARK), solid(DARK)]  # about 0.67 s of black
    h.source.default = solid(BLUE)
    await h.clock.advance(3)
    sent = [c[0].rgb for t, c in h.output.sent if t >= 2]
    assert not any(rgb == approx_rgb(GLOW_ONLY) for rgb in sent)
    assert sent[-1] == approx_rgb((0.0, 0.0, 1.0))
    assert await h.stop() == StopReason.USER


async def test_starting_on_a_black_picture_shows_the_glow_at_once() -> None:
    h = Harness()
    h.source.default = solid(DARK)
    await h.start()
    await h.clock.advance(0.3)
    assert h.output.rgb_at(0) == approx_rgb(GLOW_ONLY)
    assert await h.stop() == StopReason.USER


async def _stop_moment_after_failures(last_frame: Frame) -> float:
    h = Harness()
    await h.start()
    await h.clock.advance(2)
    h.source.frames = [last_frame]
    h.source.default = SourceUnavailable("gone")
    assert await h.finish() == StopReason.TV_OFF
    return h.output.sent[-1][0]


async def test_five_failed_captures_stop_at_the_same_moment_during_a_hold() -> None:
    held = await _stop_moment_after_failures(solid(DARK))
    not_held = await _stop_moment_after_failures(solid(RED))
    assert held == pytest.approx(not_held)


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
    assert all(colors[0].rgb == approx_rgb((1.0, 0.0, 0.0)) for colors in after)
    assert await h.stop() == StopReason.USER


async def test_first_fade_after_a_retry_is_not_stretched_over_the_gap() -> None:
    h = Harness(rate=UpdateRate.LOW)
    await h.start()
    await h.clock.advance(1.6)  # two captures, measured interval 1 s
    await lose_stream(h)
    h.source.default = solid(BLUE)  # the first capture after the retry
    await h.clock.advance(5.1)  # retried
    await h.clock.advance(1.3)  # blue fades in over about 1 s, not over the 5 s gap
    assert h.output.rgb_at(-1) == pytest.approx((0.0, 0.0, 1.0), abs=0.01)
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


# No picture (#68): all-zero frames for 3 s release the lights


async def lose_picture(h: Harness) -> None:
    """Sync red, then let the frames turn all zero until the lights are released."""
    await h.start()
    await h.clock.advance(2)
    h.source.default = solid(NO_PICTURE)
    await h.clock.advance(4)
    assert h.log[-2:] == ["output.stop", "output.close"]


async def test_no_picture_for_3_s_releases_the_lights() -> None:
    h = Harness()
    await h.start()
    await h.clock.advance(2)
    h.source.default = solid(NO_PICTURE)
    await h.clock.advance(3.0)  # all zero since about 2.25-2.6 s: still waiting
    assert h.log == ["source.start", "output.start"]
    after = [c[0].rgb for t, c in h.output.sent if t >= 2]
    # The hold on black keeps the last colours (3.5 s, longer than the wait).
    assert all(rgb == approx_rgb(RED_SENT) for rgb in after)
    await h.clock.advance(1.0)
    assert h.log == ["source.start", "output.start", "output.stop", "output.close"]
    released_at = h.output.sent[-1][0]
    await h.clock.advance(5)
    assert h.output.sent[-1][0] == released_at  # nothing is sent while released
    assert h.statuses[-1].phase is Phase.NO_PICTURE
    assert h.statuses[-1].channels == 0
    assert not h.task.done()  # the switch stays on
    assert await h.stop() == StopReason.USER


async def test_the_release_comes_3_s_after_the_first_all_zero_frame() -> None:
    h = Harness()
    await lose_picture(h)
    first = next(t for t, frame in h.frames if frame == solid(NO_PICTURE))
    released = next(
        t
        for t, s in zip(h.status_times, h.statuses, strict=True)
        if s.phase is Phase.NO_PICTURE
    )
    assert 3.0 <= released - first <= 3.0 + 1 / 3 + 0.01
    assert await h.stop() == StopReason.USER


async def test_a_short_all_zero_moment_keeps_the_lights() -> None:
    h = Harness(rate=UpdateRate.LOW)  # one capture per second
    await h.start()
    await h.clock.advance(1.6)  # two captures, measured interval 1 s
    h.source.frames = [solid(NO_PICTURE)] * 2  # a cut: all zero for about 2 s
    h.source.default = solid(BLUE)
    await h.clock.advance(2.0)  # the all-zero frames at 2.25 and 3.25 s
    assert all(c[0].rgb == approx_rgb(RED_SENT) for t, c in h.output.sent if t > 1.6)
    await h.clock.advance(2.2)  # blue at 4.25 s fades in over 1 s, not over 3 s
    assert h.output.rgb_at(-1) == pytest.approx((0.0, 0.0, 1.0), abs=0.01)
    assert h.log == ["source.start", "output.start"]
    assert Phase.NO_PICTURE not in h.phases
    assert await h.stop() == StopReason.USER


async def test_the_wait_starts_again_after_a_frame_with_a_picture() -> None:
    h = Harness(rate=UpdateRate.LOW)
    await h.start()
    await h.clock.advance(1)
    zero = solid(NO_PICTURE)
    h.source.frames = [zero, zero, solid(DARK), zero, zero, zero]
    await h.clock.advance(7)
    assert h.log == ["source.start", "output.start"]
    assert await h.stop() == StopReason.USER


async def test_the_hold_on_black_runs_during_the_wait() -> None:
    h = Harness()
    await h.start()
    await h.clock.advance(2)
    h.source.default = solid(DARK)  # a fade: dark first (from about 2.6 s) ...
    await h.clock.advance(2)
    h.source.default = solid(NO_PICTURE)  # ... then all zero (from about 4.6 s)
    await h.clock.advance(1.2)  # the hold ends at about 6.1 s
    assert h.output.rgb_at(-1) == approx_rgb(RED_SENT)
    await h.clock.advance(2)  # the fade to the glow has finished at about 6.6 s
    assert h.output.rgb_at(-1) == approx_rgb(GLOW_ONLY)  # glow, still claimed
    assert h.log == ["source.start", "output.start"]
    await h.clock.advance(1)  # 3 s after the first all-zero frame (about 4.6 s)
    assert h.log[-2:] == ["output.stop", "output.close"]
    assert await h.stop() == StopReason.USER


async def test_a_dark_scene_is_not_no_picture() -> None:
    h = Harness()
    await h.start()
    await h.clock.advance(2)
    h.source.default = solid(DARK)
    await h.clock.advance(6)
    assert h.output.rgb_at(-1) == approx_rgb(GLOW_ONLY)  # the hold, then the glow
    assert h.log == ["source.start", "output.start"]
    assert await h.stop() == StopReason.USER


async def test_the_picture_returns_and_the_lights_are_claimed_again() -> None:
    h = Harness()
    await lose_picture(h)
    h.source.default = solid(BLUE)
    await h.clock.advance(0.7)
    assert h.log[-1] == "output.start"
    assert h.statuses[-1] == PipelineStatus(
        Phase.STREAMING,
        channels=1,
        captures_per_second=h.statuses[-1].captures_per_second,
    )
    claimed_at = h.status_times[-1]
    await h.clock.advance(TICK)
    # The first colour is shown at once, as at a start; no fade from red.
    assert h.output.sent[-1][0] > claimed_at
    assert h.output.rgb_at(-1) == approx_rgb((0.0, 0.0, 1.0))
    assert await h.stop() == StopReason.USER
    assert h.phases == [
        Phase.STARTING,
        Phase.STREAMING,
        Phase.NO_PICTURE,
        Phase.STREAMING,
        Phase.STOPPED,
    ]


async def test_a_fresh_colour_state_after_the_claim() -> None:
    h = Harness()
    await lose_picture(h)
    h.source.default = solid(BLUE)
    await h.clock.advance(1)
    h.source.default = solid(DARK)  # straight after: a sudden black is held again
    await h.clock.advance(2)
    assert h.output.rgb_at(-1) == approx_rgb((0.0, 0.0, 1.0))
    assert await h.stop() == StopReason.USER


@pytest.mark.parametrize(
    "frame",
    [solid(DARK), switch_off_animation()],
    ids=["dark scene or subtitles", "switch-off animation"],
)
async def test_almost_black_frames_do_not_claim_the_lights(frame: Frame) -> None:
    h = Harness()
    await lose_picture(h)
    h.source.default = frame
    await h.clock.advance(10)
    assert h.log.count("output.start") == 1
    assert h.statuses[-1].phase is Phase.NO_PICTURE
    assert await h.stop() == StopReason.USER


async def test_switched_on_during_no_picture_leaves_the_lights_alone() -> None:
    h = Harness()
    h.source.default = solid(NO_PICTURE)
    await h.start()
    await h.clock.advance(15)  # longer than the startup grace: no TV_OFF
    assert not h.task.done()
    assert "output.start" not in h.log
    assert h.output.sent == []
    assert h.phases == [Phase.STARTING, Phase.NO_PICTURE]
    h.source.default = solid(RED)
    await h.clock.advance(0.7)
    assert h.log == ["source.start", "output.start"]
    assert h.output.rgb_at(-1) == approx_rgb(RED_SENT)
    assert await h.stop() == StopReason.USER


async def test_tv_turns_off_while_there_is_no_picture() -> None:
    h = Harness()
    await lose_picture(h)
    h.source.on = False
    assert await h.finish() == StopReason.TV_OFF
    assert h.log.count("output.start") == 1


async def test_five_failed_captures_while_there_is_no_picture() -> None:
    h = Harness()
    await lose_picture(h)
    h.source.default = SourceUnavailable("gone")
    assert await h.finish() == StopReason.TV_OFF
    assert h.log.count("output.start") == 1


async def test_switched_off_while_there_is_no_picture() -> None:
    h = Harness()
    await lose_picture(h)
    assert await h.stop() == StopReason.USER
    assert h.log.count("output.start") == 1
    assert h.log[-3:] == CLEANUP


async def test_switched_off_while_claiming_the_lights_again() -> None:
    h = Harness()
    await lose_picture(h)
    h.output.start_errors = ["hang"]
    h.source.default = solid(RED)
    await h.clock.advance(0.7)
    assert h.log[-1] == "output.start"
    assert await h.stop() == StopReason.USER
    assert h.output.sent[-1][1][0].rgb == approx_rgb(RED_SENT)  # from before
    assert h.log[-3:] == CLEANUP


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
async def test_claiming_the_lights_again_fails(
    error: Exception, reason: StopReason
) -> None:
    h = Harness()
    await lose_picture(h)
    sent = len(h.output.sent)
    h.output.start_errors = [error]
    h.source.default = solid(RED)
    assert await h.finish() == reason
    assert len(h.output.sent) == sent
    assert h.log[-4:] == ["output.start", *CLEANUP]


async def test_release_and_claim_log_one_line_each(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="custom_components.glowcast")
    h = Harness()
    await lose_picture(h)
    await h.clock.advance(5)
    h.source.default = solid(RED)
    await h.clock.advance(5)
    assert caplog.text.count("no picture; releasing the lights") == 1
    assert caplog.text.count("claiming the lights again") == 1
    assert await h.stop() == StopReason.USER


# The status


async def test_status_reports_each_change_of_phase_in_order() -> None:
    h = Harness()
    await h.start()
    assert h.statuses == [PipelineStatus(Phase.STARTING)]
    await h.clock.advance(1)
    # Phases and channels only: the captures per second depend on timing.
    assert (h.statuses[1].phase, h.statuses[1].channels) == (Phase.STREAMING, 1)
    await lose_stream(h)
    assert (h.statuses[-1].phase, h.statuses[-1].channels) == (Phase.RETRYING, 1)
    await h.clock.advance(6)  # retried after 5 s
    assert await h.stop() == StopReason.USER
    assert h.phases == [
        Phase.STARTING,
        Phase.STREAMING,
        Phase.RETRYING,
        Phase.STREAMING,
        Phase.STOPPED,
    ]
    stopped = h.statuses[-1]
    assert (stopped.phase, stopped.channels) == (Phase.STOPPED, 1)
    assert stopped.stop_reason is StopReason.USER


@pytest.mark.parametrize(
    ("rate", "per_second"),
    [(UpdateRate.LOW, 1), (UpdateRate.HIGH, 3), (UpdateRate.MAXIMUM, 4)],
)
async def test_streaming_reports_the_captures_per_second_once_a_second(
    rate: UpdateRate, per_second: int
) -> None:
    h = Harness(rate=rate)
    await h.start()
    await h.clock.advance(10)
    updates = [
        (t, s)
        for t, s in zip(h.status_times, h.statuses, strict=True)
        if s.phase is Phase.STREAMING
    ]
    first, *measured = updates
    assert first[1].captures_per_second is None  # not measured yet
    assert 8 <= len(measured) <= 10  # about once per second, not per frame
    times = [first[0]] + [t for t, _ in measured]
    assert all(b - a >= 1.0 for a, b in itertools.pairwise(times))
    for _, status in measured:
        assert status.captures_per_second == pytest.approx(per_second, abs=0.3)
    assert await h.stop() == StopReason.USER
    last = measured[-1][1].captures_per_second
    assert h.statuses[-1].captures_per_second == last  # kept after the stop


async def test_failed_captures_lower_the_captures_per_second() -> None:
    h = Harness(rate=UpdateRate.LOW)  # one capture per second
    await h.start()
    await h.clock.advance(3)
    before = len(h.statuses)
    h.source.frames = [SourceUnavailable("hiccup")] * 2
    await h.clock.advance(4)
    rates = [s.captures_per_second for s in h.statuses[before:]]
    assert rates.count(0.0) == 2  # only the captures that delivered a frame count
    assert rates[-1] == pytest.approx(1.0)
    assert await h.stop() == StopReason.USER


async def test_a_stop_before_the_lights_are_claimed_has_no_channels() -> None:
    h = Harness()
    h.source.on = False
    await h.start()
    assert await h.finish() == StopReason.TV_OFF
    assert h.statuses == [
        PipelineStatus(Phase.STARTING),
        PipelineStatus(Phase.STOPPED, stop_reason=StopReason.TV_OFF),
    ]


async def test_a_failing_status_callback_is_logged_and_does_not_stop(
    caplog: pytest.LogCaptureFixture,
) -> None:
    h = Harness()
    h.status_error = RuntimeError("broken listener")
    await h.start()
    await h.clock.advance(2)
    assert not h.task.done()
    sends = len(h.output.sent)
    await h.clock.advance(1)
    assert len(h.output.sent) > sends
    assert await h.stop() == StopReason.USER
    assert h.phases == [Phase.STARTING, Phase.STREAMING, Phase.STOPPED]
    assert h.log[-3:] == CLEANUP
    assert caplog.text.count("Error in the status callback") == len(h.statuses)


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
