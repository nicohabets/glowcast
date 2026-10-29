"""Ports: the interfaces the pipeline uses to talk to the outside world."""

from typing import Protocol

from .models import Channel, ChannelColor, Frame


class SourceUnavailable(Exception):
    """The source cannot deliver a frame (TV off, unreachable or not set up)."""


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


class FrameSource(Protocol):
    """Delivers frames from a TV."""

    async def start(self) -> None:
        """Connect; may take a few seconds. Raises SourceUnavailable."""

    def is_on(self) -> bool:
        """Return whether the TV is on and showing a picture.

        False only after the source's own reconnect attempt has failed, or when
        the TV itself reports that it is off.
        """

    async def capture(self) -> Frame:
        """Return the current picture. Raises SourceUnavailable within 5 s."""

    async def close(self) -> None:
        """Disconnect and release resources; safe to call twice."""


class LightOutput(Protocol):
    """Drives lights with one colour per channel."""

    max_rate_hz: float  # how often send() may be called

    async def start(self) -> list[Channel]:
        """Claim the lights and return the channels to drive.

        The output has its own channel limit (Hue: 20). May be called again
        after stop() and close(), for the retry after OutputLost.

        Raises OutputBusy, OutputAuthFailed, OutputNotFound, OutputTooManyChannels
        or OutputUnavailable.
        """

    def send(self, colors: list[ChannelColor]) -> None:
        """Send colours; non-blocking."""

    async def check(self) -> None:
        """Raise OutputLost (gone or taken over) or OutputAuthFailed.

        Called again on every send tick when no check is running, so the
        output throttles its own status requests.
        """

    async def stop(self) -> None:
        """Release the lights; safe to call twice."""

    async def close(self) -> None:
        """Release network resources; after stop()."""
