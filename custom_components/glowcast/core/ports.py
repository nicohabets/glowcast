"""Ports: the interfaces the pipeline uses to talk to the outside world."""

from collections.abc import Mapping
from typing import Protocol

from .models import Channel, ChannelColor, Frame


class SourceUnavailable(Exception):
    """The source cannot deliver a frame (TV off, unreachable or not set up)."""


class SourceAuthFailed(Exception):
    """The source rejected Glowcast's credentials; pair again."""


class OutputBusy(Exception):
    """Another app is streaming on the bridge; nothing was started."""


class OutputNotFound(Exception):
    """The chosen area (or group of lights) no longer exists."""


class OutputTooManyChannels(Exception):
    """The chosen area has more channels than the output can drive."""


class OutputUnavailable(Exception):
    """The output cannot be reached or used."""


class OutputAuthFailed(Exception):
    """The output rejected Glowcast's credentials."""


class OutputLost(Exception):
    """A running stream is gone or was taken over."""


class OutputGone(Exception):
    """No lights are left; stop with OUTPUT_LOST, without the pipeline's retry.

    A combined output raises it when its last part is dropped: its retry
    failed, or it was lost again within 60 s of a successful retry. It retries
    each part itself (architecture 4.4), so the pipeline must not retry the
    whole output on top of that. When the last part rejected the key or failed
    unexpectedly, that error is raised instead (AUTH_FAILED, or
    OUTPUT_UNAVAILABLE), as for a single output.
    """


class FrameSource(Protocol):
    """Delivers frames from a TV."""

    # How often capture() may be called. The pipeline never captures faster,
    # whatever the update rate.
    max_rate_hz: float

    async def start(self) -> None:
        """Connect; may take a few seconds.

        Raises SourceUnavailable, or SourceAuthFailed when the source rejects
        Glowcast's credentials.
        """

    def is_on(self) -> bool:
        """Return whether the TV is on and showing a picture.

        False only after the source's own reconnect attempt has failed, or when
        the TV itself reports that it is off.
        """

    async def capture(self) -> Frame:
        """Return the current picture.

        Raises SourceUnavailable within 5 s, or SourceAuthFailed.
        """

    async def close(self) -> None:
        """Disconnect and release resources; safe to call twice."""


class LightOutput(Protocol):
    """Drives lights with one colour per channel."""

    max_rate_hz: float  # how often send() may be called

    # The parts of a combined output that do not run now, by their name, with
    # why: they could not start, or were lost (waiting for their retry, or
    # dropped until the next start). Empty for an output of one part.
    dropped_parts: Mapping[str, Exception]

    async def start(self, *, retry: bool = False) -> list[Channel]:
        """Claim the lights and return the channels to drive.

        The output has its own channel limit (Hue: 20). May be called again
        after stop() and close(), for the retry after OutputLost (`retry`
        True). Each channel may say what its light shows before the claim
        (`Channel.start_rgb`), for the fade-in; on a retry there is no
        fade-in, so the output need not read it.

        Raises OutputBusy, OutputAuthFailed, OutputNotFound, OutputTooManyChannels
        or OutputUnavailable.
        """

    def send(self, colors: list[ChannelColor]) -> None:
        """Send colours; non-blocking."""

    async def check(self) -> None:
        """Raise OutputLost (gone or taken over), OutputGone or OutputAuthFailed.

        Called again on every send tick when no check is running, so the
        output throttles its own status requests.
        """

    async def stop(self) -> None:
        """Release the lights; safe to call twice."""

    async def close(self) -> None:
        """Release network resources; after stop()."""
