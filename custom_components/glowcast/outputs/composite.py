"""A combined output: several outputs follow the same picture (#11).

Architecture 4.4 and 8.1. Generic: it knows no output type, only the
LightOutput port of its parts. No Home Assistant
imports either, so it is tested with fake parts and a fake clock.

- Each part gets the colours of its own channels at its own rate; the
  combined rate is that of the fastest part, so no part is held back.
- A part that cannot start is dropped and the others start; with none
  started, start() raises the first part's error.
- A part lost while running is released and started again after 5 s while
  the others keep running; when that retry fails, or the part is lost again
  within 60 s of a successful retry, it stays dropped until the next start (a
  new CompositeOutput). A part whose key is rejected is dropped without a
  retry. With no part left, check() raises OutputGone (or OutputAuthFailed
  when the last part rejected the key), so the pipeline stops without a retry
  of its own.
- stop() and close() reach every part, also the dropped ones.
- Each channel keeps what its part reports, its starting colour included,
  so the first colours fade in for every part (#153). A part that comes back
  after its retry gets the current colours at once, without a fade-in, as
  after the pipeline's own retry.
"""

import asyncio
import logging
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field, replace

from ..core.models import Channel, ChannelColor
from ..core.pipeline import LOST_AGAIN_WINDOW, LOST_RETRY_DELAY, Clock, MonotonicClock
from ..core.ports import LightOutput, OutputAuthFailed, OutputGone, OutputLost

_LOGGER = logging.getLogger(__name__)

# Channel ids are "<part index>/<the part's own id>", unique across parts.
SEPARATOR = "/"


@dataclass(eq=False)
class _Part:
    """One output of the combination and what the composite knows about it."""

    index: int
    name: str
    output: LightOutput
    running: bool = False
    # Why it does not run (None while it runs), and whether it stays dropped
    # until the next start.
    error: Exception | None = None
    dropped: bool = False
    retried_at: float | None = None
    next_send: float = 0.0
    check: asyncio.Task[None] | None = None
    retry: asyncio.Task[None] | None = None
    releases: set[asyncio.Task[None]] = field(default_factory=set)

    def channel_id(self, channel_id: str) -> str:
        return f"{self.index}{SEPARATOR}{channel_id}"


class CompositeOutput:
    """Fans the colours out to several outputs, each at its own rate."""

    def __init__(
        self,
        parts: Sequence[tuple[str, LightOutput]],
        clock: Clock | None = None,
        dropped: Mapping[str, Exception] | None = None,
    ) -> None:
        """Initialize with (name, output) per part; the names are unique.

        `dropped` are parts that could not be made (for example because the
        integration they use is not set up), with why: they are reported as
        dropped from the start.
        """
        self._parts = [
            _Part(index, name, output) for index, (name, output) in enumerate(parts)
        ]
        self._clock = clock or MonotonicClock()
        self._dropped = dict(dropped or {})
        self.max_rate_hz = max(
            (part.output.max_rate_hz for part in self._parts), default=1.0
        )
        # Parts at the combined rate get every send; slower ones are sent to
        # on the tick nearest to when they are due.
        self._tolerance = 0.5 / self.max_rate_hz
        self._last_error: Exception | None = None
        self._release: asyncio.Future[None] | None = None

    @property
    def dropped_parts(self) -> Mapping[str, Exception]:
        """The parts that do not run now, with why (the port, 3.2)."""
        running = {
            part.name: part.error for part in self._parts if part.error is not None
        }
        return {**self._dropped, **running}

    async def start(self) -> list[Channel]:
        """Start every part that is not dropped, together.

        Raises the first part's error when none of them starts.
        """
        parts = [part for part in self._parts if not part.dropped]
        for part in parts:
            part.running = False
        results = await asyncio.gather(
            *(part.output.start() for part in parts), return_exceptions=True
        )
        channels: list[Channel] = []
        failed: list[tuple[_Part, Exception]] = []
        for part, result in zip(parts, results, strict=True):
            if isinstance(result, BaseException):
                if not isinstance(result, Exception):
                    raise result
                failed.append((part, result))
                part.error, part.dropped = result, True
                continue
            channels.extend(self._started(part, result))
        if not channels and failed:
            # The pipeline logs the stop with the first error; the others once.
            for part, error in failed[1:]:
                _LOGGER.info(
                    "Glowcast: %s could not start either (%s)", part.name, error
                )
            raise failed[0][1]
        for part, error in failed:
            _LOGGER.warning(
                "Glowcast: %s could not start (%s); it stays off until the next "
                "start, the other lights start without it",
                part.name,
                error,
                exc_info=not _is_port_error(error),
            )
        return channels

    def send(self, colors: list[ChannelColor]) -> None:
        """Hand each running part its own colours, when its turn has come."""
        now = self._clock.now()
        per_part: dict[int, list[ChannelColor]] = {}
        for color in colors:
            index, separator, channel_id = color.channel_id.partition(SEPARATOR)
            if separator and index.isdigit():
                per_part.setdefault(int(index), []).append(
                    ChannelColor(channel_id, color.rgb)
                )
        for part in self._parts:
            if not part.running or now + self._tolerance < part.next_send:
                continue
            interval = 1 / part.output.max_rate_hz
            part.next_send += interval
            if part.next_send <= now:  # behind: skip instead of bursting
                part.next_send = now + interval
            try:
                part.output.send(per_part.get(part.index, []))
            except Exception as err:
                _LOGGER.exception("Unexpected error sending to %s", part.name)
                self._lost(part, err)

    async def check(self) -> None:
        """Check every running part in the background; handle what they raised.

        Returns at once. Raises OutputGone (or OutputAuthFailed) only when no
        part is left and no retry is pending.
        """
        for part in self._parts:
            if not part.running:
                continue
            if part.check is not None and part.check.done():
                task, part.check = part.check, None
                try:
                    task.result()
                except Exception as err:
                    self._lost(part, err)
                    continue
            if part.check is None:
                part.check = asyncio.ensure_future(part.output.check())
        if not any(part.running or part.retry is not None for part in self._parts):
            # As for a single output: a rejected key gives AUTH_FAILED, an
            # unexpected error OUTPUT_UNAVAILABLE, anything else OUTPUT_LOST.
            error = self._last_error
            if isinstance(error, OutputAuthFailed) or (
                error is not None and not _is_port_error(error)
            ):
                raise error
            raise OutputGone("None of the lights is left")

    async def stop(self) -> None:
        """Release every part; shielded and safe to call twice."""
        if self._release is None or self._release.done():
            self._release = asyncio.ensure_future(self._async_stop())
        await asyncio.shield(self._release)

    async def close(self) -> None:
        """Close every part, also when one of them fails."""
        results = await asyncio.gather(
            *(part.output.close() for part in self._parts), return_exceptions=True
        )
        self._log_errors("closing", results)

    def _started(self, part: _Part, channels: list[Channel]) -> list[Channel]:
        part.running = True
        part.error = None
        part.next_send = self._clock.now()
        # With the rest of each channel as the part reports it: its position
        # and what its light showed before (for the fade-in, #153).
        return [
            replace(channel, id=part.channel_id(channel.id)) for channel in channels
        ]

    def _lost(self, part: _Part, error: Exception) -> None:
        """A running part failed: release it, then retry it or drop it."""
        part.running = False
        part.error = self._last_error = error
        self._async_release_part(part)
        now = self._clock.now()
        if isinstance(error, OutputAuthFailed):
            part.dropped = True
            _LOGGER.warning(
                "Glowcast: %s rejected Glowcast's key (%s); the other lights "
                "keep following the picture",
                part.name,
                error,
            )
        elif isinstance(error, OutputLost) and (
            part.retried_at is None or now - part.retried_at >= LOST_AGAIN_WINDOW
        ):
            _LOGGER.warning(
                "Glowcast: %s was lost (%s); retrying in %.0f s",
                part.name,
                error,
                LOST_RETRY_DELAY,
            )
            part.retry = asyncio.ensure_future(self._async_retry(part))
        else:
            part.dropped = True
            _LOGGER.warning(
                "Glowcast: %s was lost (%s)%s; it stays off until the next "
                "start, the other lights keep following the picture",
                part.name,
                error,
                " again soon after its retry" if part.retried_at is not None else "",
            )

    async def _async_retry(self, part: _Part) -> None:
        """Start a lost part again after the wait, once."""
        try:
            if part.releases:
                # asyncio.wait: a cancelled retry does not cancel the release.
                await asyncio.wait(set(part.releases))
            await self._clock.sleep(LOST_RETRY_DELAY)
            channels = await part.output.start()
        except Exception as err:
            part.error = err
            # A failed retry stops as lost, as for a single output, unless
            # the key was rejected.
            self._last_error = (
                err
                if isinstance(err, OutputAuthFailed)
                else OutputLost(f"The retry of {part.name} failed")
            )
            part.dropped = True
            _LOGGER.warning(
                "Glowcast: the retry of %s failed (%s); it stays off until the "
                "next start",
                part.name,
                err,
            )
            self._async_release_part(part)
            return
        finally:
            part.retry = None
        # Only channels that were there at the start get colours; a channel
        # that is new now waits for the next start.
        self._started(part, channels)
        part.retried_at = self._clock.now()
        _LOGGER.warning(
            "Glowcast: %s follows the picture again after its retry",
            part.name,
        )

    def _async_release_part(self, part: _Part) -> None:
        """Release one part in the background; stop() waits for it."""

        async def release() -> None:
            for step in (part.output.stop, part.output.close):
                try:
                    await step()
                except Exception:
                    _LOGGER.exception("Error while releasing %s", part.name)

        task = asyncio.ensure_future(release())
        part.releases.add(task)
        task.add_done_callback(part.releases.discard)

    async def _async_stop(self) -> None:
        tasks = [
            task
            for part in self._parts
            for task in (part.retry, part.check)
            if task is not None
        ]
        for task in tasks:
            task.cancel()
        with suppress(asyncio.CancelledError):
            await asyncio.gather(*tasks, return_exceptions=True)
        for part in self._parts:
            part.running = False
            part.retry = part.check = None
            if not part.dropped:
                part.error = None  # it is claimed again at the next start()
        releases = [task for part in self._parts for task in part.releases]
        await asyncio.gather(*releases, return_exceptions=True)
        results = await asyncio.gather(
            *(part.output.stop() for part in self._parts), return_exceptions=True
        )
        self._log_errors("stopping", results)

    def _log_errors(self, doing: str, results: list[object]) -> None:
        for part, result in zip(self._parts, results, strict=True):
            if isinstance(result, Exception):
                _LOGGER.error("Error while %s %s", doing, part.name, exc_info=result)


def _is_port_error(error: Exception) -> bool:
    """Whether the error is one of the port's own (not a bug)."""
    return type(error).__module__ == OutputLost.__module__
