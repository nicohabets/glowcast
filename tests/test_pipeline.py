"""Tests for the pipeline with a fake source, a fake output and a fake clock.

No Home Assistant test framework (architecture D13).
"""

import asyncio
import dataclasses
import itertools
import logging
from collections.abc import Callable
from typing import Any

import pytest

from custom_components.glowcast.core.letterbox import NO_BARS, STABLE, Bars, Letterbox
from custom_components.glowcast.core.light_state import (
    BLACK_HOLD,
    CUT_FADE,
    FADE_IN,
    GLOW,
    GLOW_COLOR,
)
from custom_components.glowcast.core.liveliness import Liveliness, Profile
from custom_components.glowcast.core.models import Channel, Frame
from custom_components.glowcast.core.pipeline import (
    NO_PICTURE_WAIT,
    STARTUP_GRACE,
    Phase,
    Pipeline,
    PipelineStatus,
    Settings,
    StopReason,
    UpdateRate,
    _compute,
)
from custom_components.glowcast.core.ports import (
    OutputAuthFailed,
    OutputBusy,
    OutputGone,
    OutputLost,
    OutputNotFound,
    OutputTooManyChannels,
    OutputUnavailable,
    SourceAuthFailed,
    SourceNotSupported,
    SourceUnavailable,
)

from .fakes import (
    BLUE,
    CHANNEL,
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
# Two colours 51 of 255 (0.2) apart per component: below the cut (#258).
ROSE = (204, 51, 51)
LILAC = (153, 51, 102)


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
        self.profiles: list[Profile] = []  # the level's knobs of each computation
        # Per colour computation: how often the lights were claimed by then,
        # the letterbox memory it got and its bars before the frame.
        self.computes: list[tuple[int, Letterbox, Bars]] = []
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
        if func is _compute:
            _, _, intensity, letterbox, _, profile = args
            self.intensities.append(intensity)
            self.profiles.append(profile)
            self.computes.append(
                (self.log.count("output.start"), letterbox, letterbox.bars)
            )
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


WARM_WHITE = (0.8, 0.62, 0.40)  # what the light showed before Glowcast
FIRST_FRAME_AT = 0.25  # the first capture takes 0.25 s
RED_RGB = (1.0, 0.0, 0.0)  # red as sent at brightness 100 %
BLUE_RGB = (0.0, 0.0, 1.0)


def own_state(h: Harness, rgb: tuple[float, float, float] | None = WARM_WHITE) -> None:
    """The fake output reports what its light showed before the claim."""
    h.output.channels = [dataclasses.replace(CHANNEL, start_rgb=rgb)]


def blend(
    start: tuple[float, float, float], end: tuple[float, float, float], p: float
) -> tuple[float, float, float]:
    r, g, b = (s + (e - s) * p for s, e in zip(start, end, strict=True))
    return r, g, b


def sent_between(
    h: Harness, begin: float, end: float
) -> list[tuple[float, tuple[float, float, float]]]:
    return [(t, colors[0].rgb) for t, colors in h.output.sent if begin <= t <= end]


async def test_the_first_colours_fade_in_from_the_lights_own_state() -> None:
    h = Harness()
    own_state(h)
    await h.start()
    await h.clock.advance(FIRST_FRAME_AT + FADE_IN + 1)
    fading = sent_between(h, FIRST_FRAME_AT, FIRST_FRAME_AT + FADE_IN)
    assert len(fading) >= FADE_IN * 25 - 1  # every send tick of the fade-in
    assert fading[0][1] == approx_rgb(WARM_WHITE)
    for t, rgb in fading:
        assert rgb == approx_rgb(
            blend(WARM_WHITE, RED_RGB, (t - fading[0][0]) / FADE_IN)
        )
    assert h.output.rgb_at(-1) == approx_rgb(RED_RGB)
    assert await h.stop() == StopReason.USER


async def test_a_light_that_reports_no_state_fades_in_from_dark() -> None:
    h = Harness()
    own_state(h, None)
    await h.start()
    await h.clock.advance(FIRST_FRAME_AT + FADE_IN / 2)
    assert h.output.rgb_at(0) == approx_rgb((0.0, 0.0, 0.0))
    assert h.output.rgb_at(-1)[0] == pytest.approx(0.5, abs=0.03)
    assert await h.stop() == StopReason.USER


async def test_a_new_frame_during_the_fade_in_changes_its_target() -> None:
    """Blue arrives during the fade-in: the lights go to blue, from where the
    fade-in is, and it still ends FADE_IN after the first colours."""
    h = Harness()  # captures every 1/3 s
    own_state(h)
    h.source.frames = [solid(RED)]
    h.source.default = solid(BLUE)
    await h.start()
    await h.clock.advance(FIRST_FRAME_AT + FADE_IN + 0.5)
    [(began, _), *_] = sent_between(h, FIRST_FRAME_AT, 99)
    # Half way, the picture's own fade is at blue already (by 1/3 s later).
    [(_, half)] = sent_between(
        h, began + FADE_IN / 2 - 0.02, began + FADE_IN / 2 + 0.02
    )
    assert half == pytest.approx(blend(WARM_WHITE, BLUE_RGB, 0.5), abs=0.03)
    [(_, end)] = sent_between(h, began + FADE_IN + 0.02, began + FADE_IN + 0.06)
    assert end == approx_rgb(BLUE_RGB)
    assert await h.stop() == StopReason.USER


@pytest.mark.parametrize("brightness", [100, 50, 5])
async def test_a_dark_first_picture_fades_in_to_the_glow(brightness: int) -> None:
    h = Harness(brightness=brightness)
    own_state(h)
    h.source.default = solid(DARK)
    await h.start()
    await h.clock.advance(FIRST_FRAME_AT + FADE_IN / 2)
    level = min(GLOW, brightness / 100)
    glow = tuple(level * c for c in GLOW_COLOR)
    assert h.output.rgb_at(-1) == pytest.approx(blend(WARM_WHITE, glow, 0.5), abs=0.03)
    await h.clock.advance(FADE_IN)
    assert h.output.rgb_at(-1) == approx_rgb(glow)
    assert await h.stop() == StopReason.USER


async def test_brightness_applies_during_the_fade_in() -> None:
    h = Harness(brightness=100)
    own_state(h, (0.0, 0.0, 0.0))
    await h.start()
    await h.clock.advance(FIRST_FRAME_AT + FADE_IN / 2)
    h.pipeline.update_settings(Settings(40, 100, UpdateRate.HIGH))
    sent = len(h.output.sent)
    await h.clock.advance(TICK)
    assert h.output.rgb_at(sent)[0] == pytest.approx(0.2, abs=0.03)
    assert await h.stop() == StopReason.USER


async def test_no_fade_in_after_a_settings_change() -> None:
    h = Harness(brightness=100)
    own_state(h)
    await h.start()
    await h.clock.advance(FIRST_FRAME_AT + FADE_IN + 0.5)
    for settings in (
        Settings(40, 100, UpdateRate.HIGH),
        Settings(40, 150, UpdateRate.HIGH),
        Settings(40, 150, UpdateRate.LOW),
    ):
        h.pipeline.update_settings(settings)
        await h.clock.advance(1.5)
        assert h.output.rgb_at(-1) == approx_rgb((0.40, 0.0, 0.0))
    assert h.log == ["source.start", "output.start"]
    assert await h.stop() == StopReason.USER


async def test_no_fade_in_after_a_retry() -> None:
    """The lights come back with the picture's colours, not from their own
    state, even when the output reports it again."""
    h = Harness()
    own_state(h)
    await h.start()
    await h.clock.advance(FIRST_FRAME_AT + FADE_IN + 0.5)
    await lose_stream(h)
    await h.clock.advance(5.1)  # retried
    assert h.log.count("output.start") == 2
    # The output need not read what the lights show (#153).
    assert h.output.retries == [False, True]
    retried = len(h.output.sent)
    await h.clock.advance(1)
    assert all(
        colors[0].rgb == approx_rgb(RED_RGB) for _, colors in h.output.sent[retried:]
    )
    assert await h.stop() == StopReason.USER


@pytest.mark.usefixtures("no_fade_in")
async def test_fades_linearly_over_the_measured_capture_interval() -> None:
    """A change below the cut threshold (#258) fades over the interval."""
    h = Harness(rate=UpdateRate.LOW)  # one capture per second
    h.source.frames = [solid(ROSE), solid(ROSE)]
    h.source.default = solid(LILAC)
    await h.start()
    # Captures take 0.25 s and start every second: they complete at 0.25
    # (startup), 0.5 and 1.5 s, so blue arrives at 1.5 s after an interval of 1 s.
    await h.clock.advance(2.0)
    blue_at = 1.5
    interval = 1.0  # measured between the last two captures
    sends = [(t, c[0].rgb) for t, c in h.output.sent if blue_at <= t]
    for t, rgb in sends:
        p = min(1.0, (t - blue_at) / interval)
        assert rgb[0] == pytest.approx((204 - 51 * p) / 255, abs=0.01)
        assert rgb[1] == pytest.approx(51 / 255, abs=0.01)  # no glow (#43)
        assert rgb[2] == pytest.approx((51 + 51 * p) / 255, abs=0.01)
    await h.clock.advance(1.0)
    # The next capture arrives one tick before a fade ends, so the colour
    # trails the target by a fraction and converges.
    assert h.output.sent[-1][1][0].rgb == pytest.approx(
        (153 / 255, 51 / 255, 102 / 255), abs=0.01
    )
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


@pytest.mark.parametrize(
    ("rate", "per_second"),
    [
        (UpdateRate.LOW, 1),
        (UpdateRate.MEDIUM, 2),
        (UpdateRate.HIGH, 3),
        (UpdateRate.MAXIMUM, 4),  # capped by the source, not by the capture time
    ],
)
async def test_the_source_caps_the_update_rate(
    rate: UpdateRate, per_second: int
) -> None:
    """A source that allows 4 per second (the screen, #110) caps only Maximum."""
    h = Harness(rate=rate)
    h.source.max_rate_hz = 4.0
    h.source.capture_time = 0.01
    await h.start()
    await h.clock.advance(1)
    before = h.source.captures
    await h.clock.advance(10)
    assert h.source.captures - before == pytest.approx(10 * per_second, abs=1)
    assert await h.stop() == StopReason.USER


async def test_maximum_without_a_cap_goes_as_fast_as_the_source() -> None:
    h = Harness(rate=UpdateRate.MAXIMUM)
    h.source.capture_time = 0.1
    await h.start()
    await h.clock.advance(1)
    before = h.source.captures
    await h.clock.advance(10)
    assert h.source.captures - before == pytest.approx(100, abs=2)
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
@pytest.mark.usefixtures("no_fade_in")
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


@pytest.mark.usefixtures("no_fade_in")
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


async def test_new_liveliness_applies_at_the_next_capture() -> None:
    """A new level is used from the next capture on, colours and fade, and
    the lights stay claimed (#16)."""
    h = Harness()
    await h.start()
    await h.clock.advance(1)
    assert set(h.profiles) == {Liveliness.NORMAL.profile}
    captures = h.source.captures
    h.pipeline.update_settings(Settings(100, 100, UpdateRate.HIGH, Liveliness.CALM))
    await h.clock.advance(1)
    assert h.source.captures > captures
    assert h.profiles[-(h.source.captures - captures) :] == [
        Liveliness.CALM.profile
    ] * (h.source.captures - captures)
    assert h.log == ["source.start", "output.start"]  # no new handshake
    assert await h.stop() == StopReason.USER


@pytest.mark.usefixtures("no_fade_in")
async def test_calm_fades_a_cut_over_two_capture_intervals() -> None:
    """At Calm a cut is no fast fade: halfway after about one capture
    interval, where Normal is there within the cut fade (#16)."""
    halfway = {}
    for level in (Liveliness.NORMAL, Liveliness.CALM):
        h = Harness(rate=UpdateRate.LOW)
        h.pipeline.update_settings(Settings(100, 100, UpdateRate.LOW, level))
        h.source.default = solid((255, 0, 0))
        await h.start()
        await h.clock.advance(3.1)  # settled on red, between two captures
        h.source.default = solid((0, 0, 255))
        start = len(h.output.sent)
        await h.clock.advance(3)
        blues = [h.output.rgb_at(i)[2] for i in range(start, len(h.output.sent))]
        halfway[level] = next(n for n, b in enumerate(blues) if b >= 0.5) * TICK
        assert await h.stop() == StopReason.USER
    assert halfway[Liveliness.NORMAL] <= 1.0 + 0.1  # the next capture, then fast
    assert halfway[Liveliness.CALM] >= halfway[Liveliness.NORMAL] + 0.8


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


@pytest.mark.usefixtures("no_fade_in")
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


@pytest.mark.usefixtures("no_fade_in")
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


@pytest.mark.usefixtures("no_fade_in")
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


@pytest.mark.usefixtures("no_fade_in")
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


@pytest.mark.parametrize(
    ("rate", "max_rate_hz", "seconds"),
    [
        # A screen at 4 per second: 5 failures take about 1 s, the 3 s decide.
        (UpdateRate.MAXIMUM, 4.0, 3.0),
        # The TV at Maximum, failing fast: the 3 s decide too (#110).
        (UpdateRate.MAXIMUM, 20.0, 3.0),
        # The TV at High: 5 failures take about 1.3 s, the 3 s decide.
        (UpdateRate.HIGH, 20.0, 3.0),
        # The TV at Medium: 5 failures take about 2 s, the 3 s decide.
        (UpdateRate.MEDIUM, 20.0, 3.0),
        # 1 per second: the 5 failures take longer than 3 s and decide.
        (UpdateRate.LOW, 4.0, 4.0),
    ],
)
async def test_failed_captures_stop_after_five_and_3_s(
    rate: UpdateRate, max_rate_hz: float, seconds: float
) -> None:
    h = Harness(rate=rate)
    h.source.max_rate_hz = max_rate_hz
    h.source.capture_time = 0.01
    await h.start()
    await h.clock.advance(1)
    h.source.default = SourceUnavailable("no picture")
    failing_since = h.clock.now()
    assert await h.finish() == StopReason.TV_OFF
    stopped_after = h.status_times[-1] - failing_since
    assert seconds <= stopped_after < seconds + 1.0


async def test_a_capture_that_hangs_counts_from_its_start() -> None:
    """The first failure hangs for 5 s: the 3 s are over when it ends, so
    the stop comes with the fifth failure, not 3 s after the hang ended."""
    h = Harness(rate=UpdateRate.MAXIMUM)
    h.source.max_rate_hz = 4.0
    h.source.capture_time = 0.01
    await h.start()
    await h.clock.advance(1)
    h.source.frames = ["hang"]
    h.source.default = SourceUnavailable("no picture")
    failing_since = h.clock.now()
    assert await h.finish() == StopReason.TV_OFF
    # The hang (5 s), then four more failures at 4 per second.
    assert 5.5 <= h.status_times[-1] - failing_since < 7.0


async def test_a_good_frame_starts_the_wait_again() -> None:
    """Five failures within 3 s, a good frame, then failures again: the stop
    comes 3 s after the second run of failures began."""
    h = Harness(rate=UpdateRate.MAXIMUM)
    h.source.max_rate_hz = 4.0
    h.source.capture_time = 0.01
    await h.start()
    await h.clock.advance(1)
    h.source.frames = [SourceUnavailable("no picture")] * 6 + [solid(RED)]
    h.source.default = SourceUnavailable("no picture")
    began = h.clock.now()
    # 6 failures and the frame at 4 per second; the second run begins with
    # the eighth capture, about 1.75 s later.
    assert await h.finish() == StopReason.TV_OFF
    assert 1.75 + 3.0 <= h.status_times[-1] - began < 1.75 + 3.0 + 0.6


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


async def test_output_gone_stops_without_a_retry() -> None:
    """A combined output retried its parts itself (#11): no second round."""
    h = Harness()
    await h.start()
    await h.clock.advance(1)
    h.output.check_error = OutputGone("every part is lost")
    assert await h.finish() == StopReason.OUTPUT_LOST
    assert h.log.count("output.start") == 1
    assert Phase.RETRYING not in h.phases
    assert h.log[-3:] == CLEANUP


async def test_dropped_parts_are_reported_when_they_change() -> None:
    h = Harness()
    await h.start()
    await h.clock.advance(1)
    assert all(status.dropped_parts == {} for status in h.statuses)
    reports = len(h.statuses)
    h.output.dropped_parts = {"area": OutputLost("dropped")}
    await h.clock.advance(TICK)
    assert len(h.statuses) == reports + 1
    assert h.statuses[-1].phase is Phase.STREAMING
    assert h.statuses[-1].dropped_parts == {"area": StopReason.OUTPUT_LOST}
    await h.clock.advance(0.2)  # unchanged: no report per tick
    assert len(h.statuses) == reports + 1
    h.output.dropped_parts = {"area": OutputAuthFailed("revoked")}
    await h.clock.advance(TICK)
    assert h.statuses[-1].dropped_parts == {"area": StopReason.AUTH_FAILED}
    h.output.dropped_parts = {}
    await h.clock.advance(TICK)
    assert h.statuses[-1].dropped_parts == {}
    assert await h.stop() == StopReason.USER


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (OutputBusy("taken"), StopReason.OUTPUT_BUSY),
        (OutputNotFound("gone"), StopReason.OUTPUT_NOT_FOUND),
        (OutputTooManyChannels("21"), StopReason.OUTPUT_TOO_MANY_CHANNELS),
        (OutputUnavailable("off"), StopReason.OUTPUT_UNAVAILABLE),
        (OutputAuthFailed("revoked"), StopReason.AUTH_FAILED),
        (OutputLost("dropped"), StopReason.OUTPUT_LOST),
        (OutputGone("gone"), StopReason.OUTPUT_LOST),
        (ValueError("unexpected"), StopReason.OUTPUT_UNAVAILABLE),
    ],
)
async def test_parts_dropped_at_start_are_in_the_first_reports(
    error: Exception, reason: StopReason
) -> None:
    h = Harness()
    h.output.dropped_parts = {"lights": error}
    await h.start()
    await h.clock.advance(0.5)
    streaming = next(s for s in h.statuses if s.phase is Phase.STREAMING)
    assert streaming.dropped_parts == {"lights": reason}
    assert await h.stop() == StopReason.USER
    assert h.statuses[-1].dropped_parts == {"lights": reason}


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


async def test_source_rejects_the_key_at_start() -> None:
    """No retrying for the startup grace: pair again (#110)."""
    h = Harness()
    h.source.start_error = SourceAuthFailed("rejected")
    await h.start()
    await h.clock.advance(0.5)
    assert h.task is not None and h.task.done()
    assert await h.finish() == StopReason.SOURCE_AUTH_FAILED
    assert "output.start" not in h.log
    assert h.log[-1] == "source.close"


async def test_source_rejects_the_key_while_streaming() -> None:
    h = Harness()
    await h.start()
    await h.clock.advance(1)
    h.source.frames = [SourceAuthFailed("rejected")]
    assert await h.finish() == StopReason.SOURCE_AUTH_FAILED
    assert h.log[-3:] == CLEANUP


# The source refuses to give its picture (#214): stop at the first refusal.

REFUSALS = [
    (SourceNotSupported("no such call"), StopReason.SOURCE_NOT_SUPPORTED),
    (
        SourceNotSupported("not permitted", not_permitted=True),
        StopReason.SOURCE_NOT_PERMITTED,
    ),
]


@pytest.mark.parametrize(("error", "reason"), REFUSALS)
async def test_source_refusing_at_start_stops_when_the_grace_ends(
    error: SourceNotSupported, reason: StopReason
) -> None:
    """A source that may still be starting is asked again for the startup
    grace; it stops with the refusal, and the lights are never touched."""
    h = Harness()
    h.source.default = error
    await h.start()
    await h.clock.advance(STARTUP_GRACE - 1)
    assert h.task is not None and not h.task.done()
    await h.clock.advance(2)
    assert h.task.done()
    assert await h.finish() == reason
    assert h.source.captures > 1
    assert "output.start" not in h.log
    assert h.log[-3:] == CLEANUP


async def test_the_grace_ends_on_a_refusal_during_an_attempt() -> None:
    """The grace may end while the next attempt is still asking: the last
    answer, the refusal, still counts (#253)."""
    h = Harness()
    h.source.default = SourceNotSupported("not permitted", not_permitted=True)
    # Attempts of 0.6 s and 1 s apart: the one from 9.6 s is cut off at 10 s.
    h.source.capture_time = 0.6
    await h.start()
    await h.clock.advance(STARTUP_GRACE + 1)
    assert await h.finish() == StopReason.SOURCE_NOT_PERMITTED
    assert "output.start" not in h.log


async def test_a_refusal_at_start_that_passes_starts_normally() -> None:
    """A TV still starting its services may refuse once (#214)."""
    h = Harness()
    h.source.frames = [SourceNotSupported("no such call yet")]
    await h.start()
    await h.clock.advance(3)
    assert h.task is not None and not h.task.done()
    assert "output.start" in h.log
    assert await h.stop() == StopReason.USER


async def test_the_grace_ends_on_tv_off_after_an_earlier_refusal() -> None:
    """Only the last answer counts: a refusal followed by no picture is TV_OFF."""
    h = Harness()
    h.source.frames = [SourceNotSupported("no such call yet")]
    h.source.default = SourceUnavailable("off")
    await h.start()
    await h.clock.advance(STARTUP_GRACE + 1)
    assert await h.finish() == StopReason.TV_OFF


@pytest.mark.parametrize(("error", "reason"), REFUSALS)
async def test_source_refuses_while_streaming(
    error: SourceNotSupported, reason: StopReason
) -> None:
    """The first refusal stops, without waiting for five failed captures."""
    h = Harness()
    await h.start()
    await h.clock.advance(1)
    captures = h.source.captures
    h.source.frames = [error]
    await h.clock.advance(0.5)
    assert h.task is not None and h.task.done()
    assert await h.finish() == reason
    assert h.source.captures == captures + 1
    assert h.log[-3:] == CLEANUP


async def test_source_refuses_while_the_lights_are_released() -> None:
    h = Harness()
    await lose_picture(h)
    h.source.frames = [SourceNotSupported("no such call")]
    await h.clock.advance(0.5)
    assert await h.finish() == StopReason.SOURCE_NOT_SUPPORTED
    assert h.log[-3:] == CLEANUP


# No picture (#68): all-zero frames for 7 s release the lights (#139)


async def lose_picture(h: Harness) -> None:
    """Sync red, then let the frames turn all zero until the lights are released."""
    await h.start()
    await h.clock.advance(2)
    h.source.default = solid(NO_PICTURE)
    await h.clock.advance(NO_PICTURE_WAIT + 1)
    assert h.log[-2:] == ["output.stop", "output.close"]


def first_frame_at(h: Harness, frame: Frame, after: float = 0.0) -> float:
    """When the pipeline got the first such frame at or after `after`."""
    return next(t for t, f in h.frames if f == frame and t >= after)


def first_status_at(h: Harness, match: Callable[[PipelineStatus], bool]) -> float:
    """When the pipeline first reported a status that matches."""
    return next(t for t, s in zip(h.status_times, h.statuses, strict=True) if match(s))


async def test_no_picture_for_7_s_releases_the_lights() -> None:
    h = Harness()
    await h.start()
    await h.clock.advance(2)
    h.source.default = solid(NO_PICTURE)
    await h.clock.advance(7.0)  # all zero since about 2.25-2.6 s: still waiting
    assert h.log == ["source.start", "output.start"]
    await h.clock.advance(1.0)
    assert h.log == ["source.start", "output.start", "output.stop", "output.close"]
    released_at = h.output.sent[-1][0]
    await h.clock.advance(5)
    assert h.output.sent[-1][0] == released_at  # nothing is sent while released
    assert h.statuses[-1].phase is Phase.NO_PICTURE
    assert h.statuses[-1].channels == 0
    # The rate of the stream before is cleared, as after a retry (#105).
    assert h.statuses[-1].captures_per_second is None
    assert not h.task.done()  # the switch stays on
    assert await h.stop() == StopReason.USER


async def test_black_picture_at_the_first_all_zero_frame_no_picture_7_s_later() -> None:
    h = Harness()
    await lose_picture(h)
    first = first_frame_at(h, solid(NO_PICTURE))
    assert first_status_at(h, lambda s: s.black_picture) == first
    released = first_status_at(h, lambda s: s.phase is Phase.NO_PICTURE)
    assert NO_PICTURE_WAIT <= released - first <= NO_PICTURE_WAIT + 1 / 3 + 0.01
    assert await h.stop() == StopReason.USER


async def test_a_5_s_all_zero_moment_keeps_the_lights() -> None:
    h = Harness()
    await h.start()
    await h.clock.advance(2)
    h.source.default = solid(NO_PICTURE)  # a hard cut in a film (#126)
    await h.clock.advance(5)
    h.source.default = solid(BLUE)
    await h.clock.advance(2)
    first = first_frame_at(h, solid(NO_PICTURE))
    back = first_frame_at(h, solid(BLUE))
    assert back - first >= 5 - 0.01
    assert h.output.rgb_at(-1) == approx_rgb((0.0, 0.0, 1.0))
    assert h.log == ["source.start", "output.start"]
    assert Phase.NO_PICTURE not in h.phases
    assert await h.stop() == StopReason.USER


@pytest.mark.usefixtures("no_fade_in")
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


async def test_a_picture_after_6_9_s_starts_the_wait_again() -> None:
    h = Harness(rate=UpdateRate.MAXIMUM)
    h.source.capture_time = 0.1  # a frame every 0.1 s
    await h.start()
    await h.clock.advance(1)
    zero = solid(NO_PICTURE)
    h.source.frames = [zero] * 69 + [solid(DARK)]  # a dark frame, not all zero
    h.source.default = zero
    await h.clock.advance(13.5)
    first = first_frame_at(h, zero)
    dark = first_frame_at(h, solid(DARK))
    assert dark - first == pytest.approx(6.9, abs=0.01)
    assert h.log == ["source.start", "output.start"]
    # A new wait from the next all-zero frame.
    again = first_frame_at(h, zero, after=dark)
    await h.clock.advance(1)
    assert h.log[-2:] == ["output.stop", "output.close"]
    released = first_status_at(h, lambda s: s.phase is Phase.NO_PICTURE)
    assert NO_PICTURE_WAIT <= released - again <= NO_PICTURE_WAIT + 0.11
    assert await h.stop() == StopReason.USER


@pytest.mark.usefixtures("no_fade_in")
async def test_the_glow_shows_before_the_release() -> None:
    h = Harness()
    await h.start()
    await h.clock.advance(2)
    h.source.default = solid(NO_PICTURE)
    await h.clock.advance(3.5)  # the hold on black keeps red ...
    first = first_frame_at(h, solid(NO_PICTURE))
    assert all(
        c[0].rgb == approx_rgb(RED_SENT)
        for t, c in h.output.sent
        if t < first + BLACK_HOLD
    )
    await h.clock.advance(3.0)  # ... then the glow, still claimed
    assert h.output.rgb_at(-1) == approx_rgb(GLOW_ONLY)
    assert h.log == ["source.start", "output.start"]
    await h.clock.advance(1.5)
    assert h.log[-2:] == ["output.stop", "output.close"]
    glow = [t for t, c in h.output.sent if c[0].rgb == approx_rgb(GLOW_ONLY)]
    # The glow for about the last 3 s: the wait minus the hold and the fade.
    assert glow[-1] - glow[0] >= NO_PICTURE_WAIT - BLACK_HOLD - 1
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
    await h.clock.advance(3.5)  # about 6.2 s after the first all-zero frame
    assert h.log == ["source.start", "output.start"]
    await h.clock.advance(1)  # 7 s after the first all-zero frame (about 4.6 s)
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


async def test_after_a_claim_back_the_colours_and_the_glow_are_as_before() -> None:
    """Nothing of the fade-in stays after it: the same picture gives the same
    colours, and black the same glow, after a claim back (bench check on
    #188)."""
    h = Harness(brightness=80)
    own_state(h)

    async def red_then_glow() -> tuple[Any, Any]:
        h.source.default = solid(RED)
        await h.clock.advance(FADE_IN + 1)
        red = h.output.rgb_at(-1)
        h.source.default = solid(DARK)
        await h.clock.advance(BLACK_HOLD + 1.5)
        return red, h.output.rgb_at(-1)

    await h.start()
    first = await red_then_glow()
    h.source.default = solid(NO_PICTURE)
    await h.clock.advance(NO_PICTURE_WAIT + 1)
    assert h.log[-2:] == ["output.stop", "output.close"]
    for _ in range(2):  # a claim back, and one more
        again = await red_then_glow()
        glow = tuple(GLOW * c for c in GLOW_COLOR)
        assert first == (approx_rgb((0.8, 0.0, 0.0)), approx_rgb(glow))
        assert again == (approx_rgb(first[0]), approx_rgb(first[1]))
        h.source.default = solid(NO_PICTURE)
        await h.clock.advance(NO_PICTURE_WAIT + 1)
    assert h.log.count("output.start") == 3
    assert await h.stop() == StopReason.USER


async def test_the_picture_returns_and_the_lights_are_claimed_again() -> None:
    h = Harness()
    own_state(h)
    await lose_picture(h)
    h.source.default = solid(BLUE)
    await h.clock.advance(0.7)
    assert h.log[-1] == "output.start"
    # Not measured yet on the new stream (#105).
    assert h.statuses[-1] == PipelineStatus(Phase.STREAMING, channels=1)
    claimed_at = h.status_times[-1]
    await h.clock.advance(TICK)
    # The first colours fade in from the light's own state, as at a start; no
    # fade from the red before the release.
    sent_at = h.output.sent[-1][0]
    assert sent_at > claimed_at
    assert h.output.rgb_at(-1) == approx_rgb(
        blend(WARM_WHITE, BLUE_RGB, (sent_at - claimed_at) / FADE_IN)
    )
    await h.clock.advance(FADE_IN)
    assert h.output.rgb_at(-1) == approx_rgb(BLUE_RGB)
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


@pytest.mark.usefixtures("no_fade_in")
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
    # From before: the glow, shown before the release.
    assert h.output.sent[-1][1][0].rgb == approx_rgb(GLOW_ONLY)
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
    assert (stopped.phase, stopped.channels) == (Phase.STOPPED, 0)  # released
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


async def test_a_stop_releases_the_channels_and_keeps_the_last_measurement() -> None:
    h = Harness()
    await h.start()
    await h.clock.advance(3)
    measured = h.statuses[-1]
    assert (measured.channels, measured.captures_per_second) == (1, 3.0)
    assert await h.stop() == StopReason.USER
    stopped = h.statuses[-1]
    assert (stopped.channels, stopped.captures_per_second) == (0, 3.0)


async def test_a_failed_retry_shows_nothing_of_the_earlier_stream() -> None:
    h = Harness()
    h.output.start_errors = [None, OutputBusy("taken")]
    await h.start()
    await h.clock.advance(3)
    await lose_stream(h)
    retrying = h.statuses[-1]
    assert (retrying.phase, retrying.channels) == (Phase.RETRYING, 1)
    assert await h.finish() == StopReason.OUTPUT_LOST
    assert h.statuses[-1] == PipelineStatus(
        Phase.STOPPED, stop_reason=StopReason.OUTPUT_LOST
    )


async def test_the_captures_per_second_are_measured_again_after_a_retry() -> None:
    h = Harness()
    await h.start()
    await h.clock.advance(3)
    await lose_stream(h)
    retried = len(h.statuses)
    await h.clock.advance(5.2)  # retried after 5 s
    first = h.statuses[retried]
    assert first == PipelineStatus(Phase.STREAMING, channels=1)  # not measured yet
    await h.clock.advance(2)
    assert h.statuses[-1].phase is Phase.STREAMING
    assert h.statuses[-1].captures_per_second == pytest.approx(3, abs=0.3)
    assert await h.stop() == StopReason.USER


def black_changes(h: Harness) -> list[tuple[Phase, bool]]:
    """The phase and the black-picture flag of each report that changed either."""
    return [
        key
        for key, _ in itertools.groupby((s.phase, s.black_picture) for s in h.statuses)
    ]


async def test_the_black_picture_flag_follows_the_picture() -> None:
    h = Harness()
    await h.start()
    await h.clock.advance(2)
    h.source.default = solid(DARK)
    await h.clock.advance(2)
    assert h.statuses[-1].black_picture
    assert h.statuses[-1].phase is Phase.STREAMING
    h.source.default = solid(BLUE)
    await h.clock.advance(2)
    assert not h.statuses[-1].black_picture
    assert await h.stop() == StopReason.USER
    assert black_changes(h) == [
        (Phase.STARTING, False),
        (Phase.STREAMING, False),
        (Phase.STREAMING, True),
        (Phase.STREAMING, False),
        (Phase.STOPPED, False),
    ]


async def test_the_black_picture_flag_is_reported_once_per_change() -> None:
    h = Harness(rate=UpdateRate.MAXIMUM)
    await h.start()
    await h.clock.advance(1)
    h.source.default = solid(DARK)
    reported = len(h.statuses)
    await h.clock.advance(5)  # about 20 black frames
    black = h.statuses[reported:]
    assert all(s.black_picture for s in black)
    # One report for the change; the others are the rate, once a second.
    assert len(black) <= 1 + 5
    assert await h.stop() == StopReason.USER


async def test_starting_on_a_black_picture_reports_it() -> None:
    h = Harness()
    h.source.default = solid(DARK)
    await h.start()
    await h.clock.advance(0.5)
    assert black_changes(h) == [
        (Phase.STARTING, False),
        (Phase.STREAMING, False),  # the lights are claimed
        (Phase.STREAMING, True),  # then the first frame is set
    ]
    assert await h.stop() == StopReason.USER


async def test_black_during_the_wait_then_no_picture_without_the_flag() -> None:
    h = Harness()
    await lose_picture(h)
    # All-zero frames count as black until the lights are released (#68).
    assert black_changes(h)[-3:] == [
        (Phase.STREAMING, False),
        (Phase.STREAMING, True),
        (Phase.NO_PICTURE, False),
    ]
    await h.clock.advance(5)  # still all zero: no reports while released
    assert h.statuses[-1] == PipelineStatus(Phase.NO_PICTURE)
    h.source.default = solid(BLUE)
    await h.clock.advance(0.7)
    assert h.statuses[-1] == PipelineStatus(Phase.STREAMING, channels=1)
    assert await h.stop() == StopReason.USER


async def test_the_flag_is_cleared_when_the_lights_are_released() -> None:
    h = Harness()
    await h.start()
    await h.clock.advance(1)
    h.source.default = solid(DARK)
    await h.clock.advance(1)
    assert h.statuses[-1].black_picture
    await lose_stream(h)
    await h.clock.advance(5.2)  # retried after 5 s, on a black picture
    retried = next(s for s in reversed(h.statuses) if s.phase is Phase.RETRYING)
    after = h.statuses[h.statuses.index(retried) + 1 :]
    assert after[0] == PipelineStatus(Phase.STREAMING, channels=1)
    assert after[1].black_picture  # set again by the next frame
    assert await h.stop() == StopReason.USER
    assert not h.statuses[-1].black_picture


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


def _letterbox_paint(col: int, row: int) -> tuple[int, int, int]:
    """Bars of 8 rows, a dark strip of picture next to them, then red."""
    if row < 8 or row >= 36 - 8:
        return (0, 0, 0)
    return (40, 40, 40) if row < 11 else (255, 0, 0)


LETTERBOXED = Frame(
    64,
    36,
    bytes(
        c for row in range(36) for col in range(64) for c in _letterbox_paint(col, row)
    ),
)
LETTERBOXED_BARS = Bars(8, 8, 0, 0)


async def test_letterbox_bars_move_the_regions_after_they_were_stable() -> None:
    """The top light samples the picture inside the bars once they have been
    there for STABLE (#34); before that, the frame's own top."""
    top = Channel(id="top", name="top", x=0.0, z=1.0)
    h = Harness()
    h.output.channels = [top]
    h.source.default = LETTERBOXED
    await h.start()
    await h.clock.advance(1)
    before = h.output.rgb_at(-1)
    await h.clock.advance(STABLE + 1.5)
    after = h.output.rgb_at(-1)
    assert before[0] < 0.2  # the dark strip next to the bar: a dim grey
    assert after[0] > 0.5 > after[2] * 2  # the picture's red
    assert await h.stop() == StopReason.USER


def first_compute_of_claim(h: Harness, claim: int) -> tuple[Letterbox, Bars]:
    """The letterbox memory, and its bars, at the first colour computation
    after the lights were claimed for the `claim`-th time."""
    return next((box, bars) for claims, box, bars in h.computes if claims == claim)


async def test_the_letterbox_is_kept_across_a_retry() -> None:
    """After the OutputLost retry the bars are used at once, not learned again."""
    h = Harness()
    h.source.default = LETTERBOXED
    await h.start()
    await h.clock.advance(STABLE + 1.5)
    first = h.computes[0][1]
    assert first.bars == LETTERBOXED_BARS
    h.output.check_error = OutputLost("dropped")
    await h.clock.advance(0.1)
    h.output.check_error = None
    await h.clock.advance(6)
    assert h.log.count("output.start") == 2
    assert first_compute_of_claim(h, 2) == (first, LETTERBOXED_BARS)
    assert await h.stop() == StopReason.USER


async def test_the_letterbox_is_kept_across_a_release_for_no_picture() -> None:
    h = Harness()
    h.source.default = LETTERBOXED
    await h.start()
    await h.clock.advance(STABLE + 1.5)
    first = h.computes[0][1]
    h.source.default = Frame(64, 36, bytes(64 * 36 * 3))  # all zero, same size
    await h.clock.advance(NO_PICTURE_WAIT + 1)
    assert h.log[-2:] == ["output.stop", "output.close"]
    h.source.default = LETTERBOXED
    await h.clock.advance(1)
    assert h.log.count("output.start") == 2
    assert first_compute_of_claim(h, 2) == (first, LETTERBOXED_BARS)
    assert await h.stop() == StopReason.USER


async def test_a_new_pipeline_starts_without_bars() -> None:
    """A restart (switch off and on, a new area) learns the bars afresh."""
    h = Harness()
    h.source.default = LETTERBOXED
    await h.start()
    await h.clock.advance(STABLE + 1.5)
    assert await h.stop() == StopReason.USER
    learned = h.computes[0][1]
    assert learned.bars == LETTERBOXED_BARS
    again = Harness()
    again.source.default = LETTERBOXED
    await again.start()
    await again.clock.advance(1)
    box, bars = first_compute_of_claim(again, 1)
    assert box is not learned and bars == NO_BARS
    assert await again.stop() == StopReason.USER


async def test_a_cut_shows_at_once() -> None:
    """A scene cut fades over CUT_FADE, not the capture interval (#258)."""
    h = Harness(rate=UpdateRate.LOW)  # one capture per second
    # Blue arrives at 2.5 s, after the fade-in of the claim (FADE_IN).
    h.source.frames = [solid(RED)] * 3
    h.source.default = solid(BLUE)
    await h.start()
    await h.clock.advance(3.0)
    sends = [(t, c[0].rgb) for t, c in h.output.sent]
    first = next(t for t, rgb in sends if rgb[2] > 0)
    assert first > FADE_IN
    # From the first tick with some blue, blue within CUT_FADE.
    done = [rgb for t, rgb in sends if t >= first + CUT_FADE]
    assert done[0] == pytest.approx((0.0, 0.0, 1.0), abs=0.01)
    assert await h.stop() == StopReason.USER
