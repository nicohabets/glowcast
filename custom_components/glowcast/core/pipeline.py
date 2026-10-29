"""Pipeline: keeps the lights in sync with the TV until something stops it.

Architecture 3.5 and 4.4. Plain asyncio, no Home Assistant imports: the
blocking-call runner and the clock are injected.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from .color_engine import compute, is_black
from .light_state import LightState
from .models import Channel, ChannelColor, Frame
from .ports import (
    FrameSource,
    LightOutput,
    OutputAuthFailed,
    OutputBusy,
    OutputLost,
    OutputNotFound,
    OutputTooManyChannels,
    OutputUnavailable,
    SourceUnavailable,
)

_LOGGER = logging.getLogger(__name__)

STARTUP_GRACE = 10.0  # seconds for the TV to start and deliver a frame
START_RETRY_INTERVAL = 1.0
MAX_CAPTURE_FAILURES = 5  # in a row, after the startup grace
LOST_RETRY_DELAY = 5.0
# Lost again this soon after a successful retry: stop instead of retrying.
LOST_AGAIN_WINDOW = 60.0
MIN_CAPTURE_INTERVAL = 0.05  # floor for Maximum, so a failing source never spins
CAPTURE_RATE_WINDOW = 1.0  # seconds; the captures per second are reported this often

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


class Phase(StrEnum):
    """What the pipeline is doing; every change is reported."""

    STARTING = "starting"  # waiting for the TV, then claiming the lights
    STREAMING = "streaming"  # the lights follow the picture
    RETRYING = "retrying"  # the stream was lost; claiming the lights again
    STOPPED = "stopped"  # finished; the lights are released


@dataclass(frozen=True)
class PipelineStatus:
    """What the pipeline reports through its status callback."""

    phase: Phase
    channels: int = 0  # the number of channels, once the lights are claimed
    stop_reason: StopReason | None = None  # only when stopped
    # Frames delivered per second, measured while streaming; kept after a
    # stop. None until the first measurement.
    captures_per_second: float | None = None


class UpdateRate(StrEnum):
    """How often the TV picture is captured."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    MAXIMUM = "maximum"

    @property
    def interval(self) -> float:
        """Seconds between captures; 0 means as fast as the TV allows."""
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
        while streaming about once per second with new captures per second
        (the same phase); the last call is always the stop, with its reason.
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
        self._retried_at: float | None = None
        self._captures_per_second: float | None = None

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
        _LOGGER.info("Glowcast stopped: %s", reason)
        self._report(Phase.STOPPED, reason)
        if cancelled:
            raise asyncio.CancelledError

    def stop(self) -> None:
        """Ask the pipeline to stop (the user switched it off)."""
        self._stop_requested.set()

    async def _async_run(self) -> StopReason:
        try:
            frame = await self._async_wait_for_tv()
            if frame is None:
                _LOGGER.info("The TV did not come up within the startup grace")
                return StopReason.TV_OFF
            self._channels = await self._async_start_output()
            self._report(Phase.STREAMING)
            await self._async_set_target(frame)
            while True:
                try:
                    await self._async_stream()
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
                return None  # the startup grace ran out
            started, frame = attempt.result()
            if frame is not None:
                return frame
            await self._clock.sleep(min(START_RETRY_INTERVAL, max(remaining, 0)))
        return None

    async def _async_try_tv(self, started: bool) -> tuple[bool, Frame | None]:
        """One attempt: start if needed, then capture. Returns (started, frame)."""
        try:
            if not started:
                await self._source.start()
                started = True
            if not self._source.is_on():
                return started, None
            return started, await self._source.capture()
        except SourceUnavailable:
            return started, None
        except Exception as err:
            _LOGGER.exception("Unexpected error from the TV source")
            raise _Stop(StopReason.SOURCE_UNAVAILABLE) from err

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
            channels = await self._async_unless_stopped(self._output.start())
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
                task.result()  # raises _Stop or OutputLost
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _async_capture_loop(self) -> None:
        interval = max(self._settings.update_rate.interval, MIN_CAPTURE_INTERVAL)
        failures = 0
        # Captures per second, from the end of the first capture to the end of
        # each later one, over at least CAPTURE_RATE_WINDOW.
        captures = 0
        window_start: float | None = None
        while True:
            begin = self._clock.now()
            try:
                frame = await self._source.capture()
            except SourceUnavailable:
                failures += 1
                if failures >= MAX_CAPTURE_FAILURES:
                    _LOGGER.info("%d captures in a row failed", failures)
                    raise _Stop(StopReason.TV_OFF) from None
            except Exception as err:
                _LOGGER.exception("Unexpected error from the TV source")
                raise _Stop(StopReason.SOURCE_UNAVAILABLE) from err
            else:
                failures = 0
                captures += 1
                await self._async_set_target(frame)
            now = self._clock.now()
            if window_start is None:
                captures, window_start = 0, now
            elif (elapsed := now - window_start) >= CAPTURE_RATE_WINDOW:
                self._captures_per_second = round(captures / elapsed, 2)
                self._report(Phase.STREAMING)
                captures, window_start = 0, now
            await self._clock.sleep(max(0.0, begin + interval - self._clock.now()))

    async def _async_send_loop(self) -> None:
        tick = 1 / self._output.max_rate_hz
        check: asyncio.Task[None] | None = None
        next_tick = self._clock.now()
        try:
            while True:
                if not self._source.is_on():
                    _LOGGER.info("The TV reports that it is off")
                    raise _Stop(StopReason.TV_OFF)
                if check is not None and check.done():
                    _raise_check_result(check)
                    check = None
                if check is None:
                    # The output throttles its own status requests.
                    check = asyncio.ensure_future(self._output.check())
                self._output.send(
                    self._lights.colors(
                        self._clock.now(), self._settings.brightness / 100
                    )
                )
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

    async def _async_set_target(self, frame: Frame) -> None:
        try:
            colors, frame_black = await self._runner(
                _compute, frame, self._channels, self._settings.intensity / 100
            )
        except Exception as err:
            _LOGGER.exception("Unexpected error computing the colours")
            raise _Stop(StopReason.SOURCE_UNAVAILABLE) from err
        self._lights.set_target(colors, frame_black, self._clock.now())

    async def _async_cleanup(self) -> None:
        """Release everything, in order, whatever happened."""
        for step in (self._output.stop, self._output.close, self._source.close):
            try:
                await step()
            except Exception:
                _LOGGER.exception("Error while stopping Glowcast")

    def _report(self, phase: Phase, reason: StopReason | None = None) -> None:
        status = PipelineStatus(
            phase, len(self._channels), reason, self._captures_per_second
        )
        try:
            self._on_status(status)
        except Exception:
            _LOGGER.exception("Error in the status callback")


def _compute(
    frame: Frame, channels: list[Channel], intensity: float
) -> tuple[list[ChannelColor], bool]:
    """The colour engine's work for one frame, in one executor job."""
    return compute(frame, channels, intensity), is_black(frame)


def _raise_check_result(check: asyncio.Task[None]) -> None:
    """Turn a finished output check into a stop or a retry."""
    try:
        check.result()
    except OutputAuthFailed as err:
        _LOGGER.warning("%s", err)
        raise _Stop(StopReason.AUTH_FAILED) from err
    except OutputLost:
        raise
    except Exception as err:
        _LOGGER.exception("Unexpected error checking the lights")
        raise _Stop(StopReason.OUTPUT_UNAVAILABLE) from err
