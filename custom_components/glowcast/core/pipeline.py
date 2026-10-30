"""Pipeline: keeps the lights in sync with the TV until something stops it.

Architecture 3.5 and 4.4. Plain asyncio, no Home Assistant imports: the
blocking-call runner and the clock are injected.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from .color_engine import compute, is_all_zero, is_black
from .letterbox import Letterbox
from .light_state import RGB, LightState
from .light_test import LightTest, pattern_colors
from .models import Channel, ChannelColor, Frame
from .ports import (
    FrameSource,
    LightOutput,
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

_LOGGER = logging.getLogger(__name__)

STARTUP_GRACE = 10.0  # seconds for the TV to start and deliver a frame
START_RETRY_INTERVAL = 1.0
# Failed captures stop with TV_OFF once there are this many in a row, after
# the startup grace, and this long has passed since the first of them began:
# as long as a screen's own wait before it counts as off (#110), so a fast
# source (4 per second) is not stopped after about 1 s (4 intervals of 0.25 s
# plus the capture time). It applies to the TV too (product owner's decision,
# #110).
MAX_CAPTURE_FAILURES = 5
CAPTURE_FAILURE_WAIT = 3.0
LOST_RETRY_DELAY = 5.0
# Lost again this soon after a successful retry: stop instead of retrying.
LOST_AGAIN_WINDOW = 60.0
MIN_CAPTURE_INTERVAL = 0.05  # floor for Maximum, so a failing source never spins
CAPTURE_RATE_WINDOW = 1.0  # seconds; the captures per second are reported this often
# Release the lights after this long of all-zero frames (no picture, as from a
# protected app); shorter all-zero moments in a film or series over HDMI, after
# a hard cut, lasted up to 5 s at 1 capture per second, so up to about 6 s
# (measured on #126, see "Verified facts"; #139).
NO_PICTURE_WAIT = 7.0

Runner = Callable[..., Awaitable[Any]]


class StopReason(StrEnum):
    """Why the pipeline stopped."""

    USER = "user"
    TV_OFF = "tv_off"
    OUTPUT_LOST = "output_lost"
    OUTPUT_BUSY = "output_busy"
    OUTPUT_NOT_FOUND = "output_not_found"
    OUTPUT_TOO_MANY_CHANNELS = "output_too_many_channels"
    OUTPUT_UNAVAILABLE = "output_unavailable"
    AUTH_FAILED = "auth_failed"
    SOURCE_UNAVAILABLE = "source_unavailable"
    SOURCE_AUTH_FAILED = "source_auth_failed"
    # The source refuses to give its picture (#214): it does not offer the
    # capture, or it does not permit it.
    SOURCE_NOT_SUPPORTED = "source_not_supported"
    SOURCE_NOT_PERMITTED = "source_not_permitted"


class Phase(StrEnum):
    """What the pipeline is doing; every change is reported."""

    STARTING = "starting"  # waiting for the TV, then claiming the lights
    STREAMING = "streaming"  # the lights follow the picture
    RETRYING = "retrying"  # the stream was lost; claiming the lights again
    NO_PICTURE = "no_picture"  # the TV shows no picture; the lights are released
    STOPPED = "stopped"  # finished; the lights are released


@dataclass(frozen=True)
class PipelineStatus:
    """What the pipeline reports through its status callback."""

    phase: Phase
    # The number of channels while the lights are claimed; 0 before and once
    # they are released.
    channels: int = 0
    stop_reason: StopReason | None = None  # only when stopped
    # Frames delivered per second, measured while streaming; kept after a
    # stop. None until the first measurement, and again once the lights are
    # released for a retry or for no picture.
    captures_per_second: float | None = None
    # While streaming: the whole frame is black by the colour engine's rule (a
    # dark scene; the lights hold the last colours or show the glow). False
    # otherwise; reported when it changes, never per frame.
    black_picture: bool = False
    # The parts of a combined output that do not run now, by name, with the
    # reason (architecture 4.4): reported when it changes. Empty for an
    # output of one part.
    dropped_parts: Mapping[str, "StopReason"] = field(default_factory=dict)


class UpdateRate(StrEnum):
    """How often the TV picture is captured."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    MAXIMUM = "maximum"

    @property
    def interval(self) -> float:
        """Seconds between captures; 0 means as fast as the source allows."""
        return {"low": 1.0, "medium": 0.5, "high": 1 / 3, "maximum": 0.0}[self.value]


@dataclass(frozen=True)
class Settings:
    """The user's settings."""

    brightness: int  # 1-100 %
    intensity: int  # 0-200 %
    update_rate: UpdateRate


class Clock(Protocol):
    """Time, injected so tests can control it."""

    def now(self) -> float:
        """Return the current time in seconds."""

    async def sleep(self, seconds: float) -> None:
        """Wait for the given number of seconds."""


class MonotonicClock:
    """The real clock."""

    def now(self) -> float:
        """Return time.monotonic()."""
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        """Wait with asyncio.sleep()."""
        await asyncio.sleep(seconds)


class _Stop(Exception):
    """Ends the run with a reason."""

    def __init__(self, reason: StopReason) -> None:
        super().__init__(reason)
        self.reason = reason


class _NoPicture(Exception):
    """The frames have been all zero for NO_PICTURE_WAIT: release the lights."""


_START_ERRORS: dict[type[Exception], StopReason] = {
    OutputBusy: StopReason.OUTPUT_BUSY,
    OutputNotFound: StopReason.OUTPUT_NOT_FOUND,
    OutputTooManyChannels: StopReason.OUTPUT_TOO_MANY_CHANNELS,
    OutputUnavailable: StopReason.OUTPUT_UNAVAILABLE,
    OutputAuthFailed: StopReason.AUTH_FAILED,
}


class Pipeline:
    """Captures the TV picture and fades the lights towards it."""

    def __init__(
        self,
        source: FrameSource,
        output: LightOutput,
        settings: Settings,
        runner: Runner,
        clock: Clock,
        on_status: Callable[[PipelineStatus], None],
    ) -> None:
        """Initialize; nothing happens until run().

        `on_status` is called on the event loop on every change of phase, and
        while streaming about once per second with new captures per second and
        when the picture turns black or back (the same phase); the last call
        is always the stop, with its reason.
        """
        self._source = source
        self._output = output
        self._settings = settings
        self._runner = runner
        self._clock = clock
        self._on_status = on_status
        self._stop_requested = asyncio.Event()
        self._channels: list[Channel] = []
        self._lights = LightState()
        # Where the picture sits inside letterbox bars, over frames (#34).
        self._letterbox = Letterbox()
        self._retried_at: float | None = None
        # The last refusal in the startup grace, if its last attempt was one.
        self._refusal: SourceNotSupported | None = None
        self._captures_per_second: float | None = None
        self._failures = 0  # captures in a row that failed
        self._failing_since = 0.0  # when the first of them began
        self._no_picture_since: float | None = None
        self._black_picture = False
        self._dropped_parts: dict[str, StopReason] = {}
        self._phase = Phase.STARTING
        # The light test while streaming (#182): what it shows, and once it is
        # over, its last colours until the next frame fades in from them.
        self._test: LightTest | None = None
        self._test_sent: list[ChannelColor] = []
        self._fade_back: dict[str, RGB] | None = None

    @property
    def testing(self) -> bool:
        """Whether the light test shows (or waits for the picture to return)."""
        return self._test is not None or self._fade_back is not None

    async def async_test(self) -> bool:
        """Show the light test instead of the picture's colours (#182).

        Only while streaming: the lights are claimed, so there is no new
        handshake. The picture's colours fade back in from the test's last
        colours at the next frame. False when it cannot test now (not
        streaming, or a test runs already).
        """
        if self._phase is not Phase.STREAMING or self.testing:
            return False
        channels = self._channels
        try:
            steps = await self._runner(pattern_colors, channels)
        except Exception:
            _LOGGER.exception("Unexpected error computing the test colours")
            return False
        if (
            self._phase is not Phase.STREAMING
            or self.testing
            or self._channels is not channels
        ):
            return False  # changed meanwhile
        self._test = LightTest(steps, self._clock.now())
        _LOGGER.info("Testing the lights")
        return True

    async def run(self) -> None:
        """Run until stopped; then clean up and report the stop once.

        Cancelling the task that runs this (unload, HA stop) counts as USER;
        the cleanup still completes, even if the task is cancelled again.
        """
        cancelled = False
        self._report(Phase.STARTING)
        try:
            reason = await self._async_run()
        except asyncio.CancelledError:
            cancelled = True
            reason = StopReason.USER
        except Exception:
            _LOGGER.exception("Glowcast stopped after an unexpected error")
            reason = StopReason.OUTPUT_UNAVAILABLE
        cleanup = asyncio.ensure_future(self._async_cleanup())
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                cancelled = True
        self._channels = []
        self._black_picture = False
        self._end_test()
        _LOGGER.info("Glowcast stopped: %s", reason)
        self._report(Phase.STOPPED, reason)
        if cancelled:
            raise asyncio.CancelledError

    def stop(self) -> None:
        """Ask the pipeline to stop (the user switched it off)."""
        self._stop_requested.set()

    def update_settings(self, settings: Settings) -> None:
        """Use new settings without a restart.

        Brightness applies at the next send, intensity and the update rate at
        the next capture; the lights stay claimed.
        """
        self._settings = settings

    async def _async_run(self) -> StopReason:
        try:
            frame = await self._async_wait_for_tv()
            if frame is None:
                _LOGGER.info(
                    "The picture source did not come up within the startup grace"
                )
                return StopReason.TV_OFF
            if await self._async_run_job(is_all_zero, frame):
                # Nothing is claimed yet, so there is nothing to wait for.
                frame = await self._async_without_lights()
            else:
                self._channels = await self._async_start_output()
                self._lights.fade_in(_starts(self._channels))
                self._report(Phase.STREAMING)
            await self._async_set_target(frame)
            while True:
                try:
                    await self._async_stream()
                except _NoPicture:
                    _LOGGER.info(
                        "The picture source shows no picture; releasing the lights"
                    )
                    await self._async_release_output()
                    frame = await self._async_without_lights()
                    await self._async_set_target(frame)
                except OutputLost as err:
                    if (
                        self._retried_at is not None
                        and self._clock.now() - self._retried_at < LOST_AGAIN_WINDOW
                    ):
                        _LOGGER.warning("%s again soon after a retry; stopping", err)
                        raise _Stop(StopReason.OUTPUT_LOST) from err
                    _LOGGER.warning("%s; retrying in %.0f s", err, LOST_RETRY_DELAY)
                    self._report(Phase.RETRYING)
                    await self._async_retry_output()
        except _Stop as stop:
            return stop.reason

    async def _async_wait_for_tv(self) -> Frame | None:
        """Start the source and get one good frame within the startup grace."""
        deadline = self._clock.now() + STARTUP_GRACE
        started = False
        # A refusal is retried like any failure while the source may still be
        # starting its services; it stops only when the grace ends on it (#214).
        self._refusal = None
        while (remaining := deadline - self._clock.now()) > 0:
            if self._stop_requested.is_set():
                raise _Stop(StopReason.USER)
            attempt = asyncio.ensure_future(self._async_try_tv(started))
            timer = asyncio.ensure_future(self._clock.sleep(remaining))
            stop = asyncio.ensure_future(self._stop_requested.wait())
            try:
                await asyncio.wait(
                    {attempt, timer, stop}, return_when=asyncio.FIRST_COMPLETED
                )
            finally:
                for task in (attempt, timer, stop):
                    if not task.done():
                        task.cancel()
                # Wait for a cancelled attempt too, so nothing is left running.
                await asyncio.gather(attempt, timer, stop, return_exceptions=True)
            if self._stop_requested.is_set():
                raise _Stop(StopReason.USER)
            if attempt.cancelled():
                break  # the startup grace ran out
            started, frame = attempt.result()
            if frame is not None:
                return frame
            await self._clock.sleep(min(START_RETRY_INTERVAL, max(remaining, 0)))
        if self._refusal is not None:
            raise _not_supported(self._refusal)
        return None

    async def _async_try_tv(self, started: bool) -> tuple[bool, Frame | None]:
        """One attempt: start if needed, then capture. Returns (started, frame).

        `_refusal` is the answer of the last attempt that ended, so an attempt
        the end of the grace cuts off leaves it as it was (#253).
        """
        frame = None
        try:
            if not started:
                await self._source.start()
                started = True
            if self._source.is_on():
                frame = await self._source.capture()
        except SourceUnavailable:
            pass
        except SourceNotSupported as err:
            # Unlike while syncing, only recorded: the startup grace asks again.
            self._refusal = err
            return started, None
        except Exception as err:
            raise _source_stop(err) from err
        self._refusal = None
        return started, frame

    async def _async_start_output(self) -> list[Channel]:
        try:
            return await self._output.start()
        except tuple(_START_ERRORS) as err:
            _LOGGER.warning("Could not start the lights: %s", err)
            raise _Stop(_START_ERRORS[type(err)]) from err
        except Exception as err:
            _LOGGER.exception("Unexpected error starting the lights")
            raise _Stop(StopReason.OUTPUT_UNAVAILABLE) from err

    async def _async_retry_output(self) -> None:
        """One retry after OutputLost; stop if the bridge is not free.

        A stop by the user or the TV turning off during the wait means the
        lights are not claimed again.
        """
        await self._async_release_output()
        await self._async_unless_stopped(self._clock.sleep(LOST_RETRY_DELAY))
        if not self._source.is_on():
            raise _Stop(StopReason.TV_OFF)
        try:
            channels = await self._async_unless_stopped(self._output.start(retry=True))
        except OutputAuthFailed as err:
            raise _Stop(StopReason.AUTH_FAILED) from err
        except _Stop:
            raise
        except Exception as err:
            _LOGGER.warning("The retry failed (%s); stopping", err)
            raise _Stop(StopReason.OUTPUT_LOST) from err
        self._retried_at = self._clock.now()
        self._channels = channels
        self._lights.resume({channel.id for channel in channels})
        self._report(Phase.STREAMING)

    async def _async_without_lights(self) -> Frame:
        """Report NO_PICTURE and capture until there is a picture again, then
        claim the lights as at switch-on. Returns the frame with the picture.

        Only a frame that is not black by the whole-frame rule claims them, so
        subtitles, player controls or a switch-off animation over no picture
        do not; each claim costs the bridge's handshake.
        """
        self._report(Phase.NO_PICTURE)
        self._failures = 0
        while True:
            begin = self._clock.now()
            if not self._source.is_on():
                _LOGGER.info("The picture source reports that it is off")
                raise _Stop(StopReason.TV_OFF)
            frame = await self._async_unless_stopped(self._async_capture())
            if frame is not None and not await self._async_run_job(is_black, frame):
                break
            await self._async_unless_stopped(self._async_pace(begin))
        _LOGGER.info("The picture is back; claiming the lights again")
        self._channels = await self._async_unless_stopped(self._async_start_output())
        # Start afresh: the first colours fade in, as at a start.
        self._lights = LightState()
        self._lights.fade_in(_starts(self._channels))
        self._no_picture_since = None
        self._report(Phase.STREAMING)
        return frame

    async def _async_unless_stopped(self, awaitable: Awaitable[Any]) -> Any:
        """Await something, but give up with USER as soon as stop() is called."""
        work = asyncio.ensure_future(awaitable)
        stop = asyncio.ensure_future(self._stop_requested.wait())
        try:
            await asyncio.wait({work, stop}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (work, stop):
                if not task.done():
                    task.cancel()
            # Wait for cancelled work too, so it is not still running during cleanup.
            await asyncio.gather(work, stop, return_exceptions=True)
        if self._stop_requested.is_set():
            raise _Stop(StopReason.USER)
        return work.result()

    async def _async_release_output(self) -> None:
        with suppress(Exception):
            await self._output.stop()
        with suppress(Exception):
            await self._output.close()
        # Released for a retry or for no picture: measured again on the next stream.
        self._end_test()
        self._channels = []
        self._captures_per_second = None
        self._black_picture = False

    async def _async_stream(self) -> None:
        """Run the capture and send loops until one of them ends the run."""
        capture = asyncio.ensure_future(self._async_capture_loop())
        send = asyncio.ensure_future(self._async_send_loop())
        stop = asyncio.ensure_future(self._stop_requested.wait())
        tasks = {capture, send, stop}
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            if stop in done:
                raise _Stop(StopReason.USER)
            for task in done:
                task.result()  # raises _Stop, OutputLost or _NoPicture
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _async_capture_loop(self) -> None:
        self._failures = 0
        # Captures per second, from the end of the first capture to the end of
        # each later one, over at least CAPTURE_RATE_WINDOW.
        captures = 0
        window_start: float | None = None
        while True:
            begin = self._clock.now()
            if (frame := await self._async_capture()) is not None:
                captures += 1
                await self._async_set_target(frame)
            now = self._clock.now()
            if window_start is None:
                captures, window_start = 0, now
            elif (elapsed := now - window_start) >= CAPTURE_RATE_WINDOW:
                self._captures_per_second = round(captures / elapsed, 2)
                self._report(Phase.STREAMING)
                captures, window_start = 0, now
            await self._async_pace(begin)

    async def _async_capture(self) -> Frame | None:
        """One capture, or None if it failed; too many failures in a row stop."""
        begin = self._clock.now()
        try:
            frame = await self._source.capture()
        except SourceUnavailable:
            if self._failures == 0:
                # Its start: a capture that hangs for seconds counts in full.
                self._failing_since = begin
            self._failures += 1
            if (
                self._failures >= MAX_CAPTURE_FAILURES
                and self._clock.now() - self._failing_since >= CAPTURE_FAILURE_WAIT
            ):
                _LOGGER.info("%d captures in a row failed", self._failures)
                raise _Stop(StopReason.TV_OFF) from None
            return None
        except Exception as err:
            raise _source_stop(err) from err
        self._failures = 0
        return frame

    async def _async_pace(self, begin: float) -> None:
        """Wait until the next capture is due after one that began at `begin`."""
        # Read on every capture: the update rate can change while streaming.
        # The source's own limit caps Maximum (and any faster level).
        interval = max(
            self._settings.update_rate.interval,
            1 / self._source.max_rate_hz,
            MIN_CAPTURE_INTERVAL,
        )
        await self._clock.sleep(max(0.0, begin + interval - self._clock.now()))

    async def _async_send_loop(self) -> None:
        tick = 1 / self._output.max_rate_hz
        check: asyncio.Task[None] | None = None
        next_tick = self._clock.now()
        try:
            while True:
                if not self._source.is_on():
                    _LOGGER.info("The picture source reports that it is off")
                    raise _Stop(StopReason.TV_OFF)
                if check is not None and check.done():
                    _raise_check_result(check)
                    check = None
                if check is None:
                    # The output throttles its own status requests.
                    check = asyncio.ensure_future(self._output.check())
                self._output.send(self._colors())
                if self._parts_dropped() != self._dropped_parts:
                    self._report(Phase.STREAMING)
                next_tick += tick
                now = self._clock.now()
                if next_tick < now:  # behind: skip ticks instead of bursting
                    next_tick = now
                await self._clock.sleep(next_tick - now)
        finally:
            if check is not None:
                check.cancel()
                with suppress(BaseException):
                    await check

    def _colors(self) -> list[ChannelColor]:
        """The colours to send now: the picture's, or the light test's."""
        now = self._clock.now()
        brightness = self._settings.brightness / 100
        # Always asked, so the fades between frames go on under the test.
        colors = self._lights.colors(now, brightness)
        if self._test is not None:
            if (test := self._test.colors(now, brightness)) is not None:
                self._test_sent = test
                return test
            # Over: the next frame fades in from the test's last colours.
            self._test = None
            self._fade_back = {color.channel_id: color.rgb for color in self._test_sent}
        if self._fade_back is not None:
            return self._test_sent
        return colors

    def _end_test(self) -> None:
        """Forget the light test (the lights are released)."""
        self._test = None
        self._fade_back = None
        self._test_sent = []

    async def _async_set_target(self, frame: Frame) -> None:
        """Give the lights the frame's colours; raise _NoPicture after all-zero
        frames for NO_PICTURE_WAIT. Until then an all-zero frame is a black
        frame, with the hold on black and the glow, and the status says so."""
        now = self._clock.now()
        colors, frame_black, no_picture = await self._async_run_job(
            _compute,
            frame,
            self._channels,
            self._settings.intensity / 100,
            self._letterbox,
            now,
        )
        if not no_picture:
            self._no_picture_since = None
        elif self._no_picture_since is None:
            self._no_picture_since = now
        elif now - self._no_picture_since >= NO_PICTURE_WAIT:
            raise _NoPicture
        if self._fade_back is not None and self._test is None:
            # The light test is over: fade from its last colours to the picture.
            self._lights.fade_in(self._fade_back)
            self._fade_back = None
        self._lights.set_target(colors, frame_black, now)
        if frame_black != self._black_picture:
            self._black_picture = frame_black
            self._report(Phase.STREAMING)

    async def _async_run_job(self, func: Callable[..., Any], *args: Any) -> Any:
        """Run the colour engine's work in the executor."""
        try:
            return await self._runner(func, *args)
        except Exception as err:
            _LOGGER.exception("Unexpected error computing the colours")
            raise _Stop(StopReason.SOURCE_UNAVAILABLE) from err

    async def _async_cleanup(self) -> None:
        """Release everything, in order, whatever happened."""
        for step in (self._output.stop, self._output.close, self._source.close):
            try:
                await step()
            except Exception:
                _LOGGER.exception("Error while stopping Glowcast")

    def _parts_dropped(self) -> dict[str, StopReason]:
        """The output's parts that do not run now, with the reason."""
        return {
            name: stop_reason_of(error)
            for name, error in self._output.dropped_parts.items()
        }

    def _report(self, phase: Phase, reason: StopReason | None = None) -> None:
        self._phase = phase
        self._dropped_parts = self._parts_dropped()
        status = PipelineStatus(
            phase,
            len(self._channels),
            reason,
            self._captures_per_second,
            self._black_picture,
            dict(self._dropped_parts),
        )
        try:
            self._on_status(status)
        except Exception:
            _LOGGER.exception("Error in the status callback")


async def run_light_test(
    output: LightOutput, brightness: float, runner: Runner, clock: Clock
) -> StopReason | None:
    """Claim the lights for the light test only, show it, and release them (#182).

    For when Glowcast does not sync: no picture source is used. The release
    (`stop()`, `close()`) runs on every exit path and completes even when the
    task is cancelled (unload, Home Assistant stopping, a switch-on). Returns
    None when the test was shown, or why the lights could not be claimed
    (another app streams, or any other start error); then nothing was sent.
    """
    cancelled = False
    try:
        result = await _async_light_test(output, brightness, runner, clock)
    except asyncio.CancelledError:
        cancelled = True
        result = None
    except Exception:
        _LOGGER.exception("Unexpected error during the light test")
        result = StopReason.OUTPUT_UNAVAILABLE
    cleanup = asyncio.ensure_future(_async_release(output))
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            cancelled = True
    if cancelled:
        raise asyncio.CancelledError
    return result


async def _async_light_test(
    output: LightOutput, brightness: float, runner: Runner, clock: Clock
) -> StopReason | None:
    """Claim the lights and show the test; the caller releases them."""
    try:
        channels = await output.start()
    except tuple(_START_ERRORS) as err:
        return _START_ERRORS[type(err)]
    test = LightTest(await runner(pattern_colors, channels), clock.now())
    tick = 1 / output.max_rate_hz
    while (colors := test.colors(clock.now(), brightness)) is not None:
        output.send(colors)
        await clock.sleep(tick)
    return None


async def _async_release(output: LightOutput) -> None:
    """Release the lights after the light test, as a stop does."""
    for step in (output.stop, output.close):
        try:
            await step()
        except Exception:
            _LOGGER.exception("Error while releasing the lights after the test")


def stop_reason_of(error: Exception) -> StopReason:
    """The stop reason for an output's error, as for a single output."""
    if isinstance(error, (OutputLost, OutputGone)):
        return StopReason.OUTPUT_LOST
    return next(
        (
            reason
            for error_type, reason in _START_ERRORS.items()
            if isinstance(error, error_type)
        ),
        StopReason.OUTPUT_UNAVAILABLE,
    )


def _source_stop(error: Exception) -> _Stop:
    """How a source error other than `SourceUnavailable` stops, at the start
    and while syncing alike; call it from the `except` block.

    A rejected key, a refusal and an unexpected error. At the start a refusal
    does not get here until the startup grace ends on it (#214).
    """
    if isinstance(error, SourceAuthFailed):
        _LOGGER.warning("%s", error)
        return _Stop(StopReason.SOURCE_AUTH_FAILED)
    if isinstance(error, SourceNotSupported):
        return _not_supported(error)
    _LOGGER.exception("Unexpected error from the picture source")
    return _Stop(StopReason.SOURCE_UNAVAILABLE)


def _not_supported(error: SourceNotSupported) -> _Stop:
    """Stop at the first refusal: the source will refuse every capture."""
    _LOGGER.warning("%s", error)
    if error.not_permitted:
        return _Stop(StopReason.SOURCE_NOT_PERMITTED)
    return _Stop(StopReason.SOURCE_NOT_SUPPORTED)


def _starts(channels: list[Channel]) -> dict[str, tuple[float, float, float] | None]:
    """What each claimed light showed before, for the fade-in."""
    return {channel.id: channel.start_rgb for channel in channels}


def _compute(
    frame: Frame,
    channels: list[Channel],
    intensity: float,
    letterbox: Letterbox,
    now: float,
) -> tuple[list[ChannelColor], bool, bool]:
    """The colour engine's work for one frame, in one executor job.

    The capture loop awaits it before the next capture, so the letterbox
    memory is only updated by one frame at a time.
    """
    black = is_black(frame)
    bars = letterbox.update(frame, now, black)
    picture = bars.picture(frame.width, frame.height)
    return compute(frame, channels, intensity, picture), black, is_all_zero(frame)


def _raise_check_result(check: asyncio.Task[None]) -> None:
    """Turn a finished output check into a stop or a retry."""
    try:
        check.result()
    except OutputAuthFailed as err:
        _LOGGER.warning("%s", err)
        raise _Stop(StopReason.AUTH_FAILED) from err
    except OutputGone as err:
        # The output's own retry failed: no second retry here.
        _LOGGER.warning("%s", err)
        raise _Stop(StopReason.OUTPUT_LOST) from err
    except OutputLost:
        raise
    except Exception as err:
        _LOGGER.exception("Unexpected error checking the lights")
        raise _Stop(StopReason.OUTPUT_UNAVAILABLE) from err
