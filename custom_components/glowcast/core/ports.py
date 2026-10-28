"""Ports: the interfaces the pipeline uses to talk to the outside world."""

from typing import Protocol

from .models import Frame


class SourceUnavailable(Exception):
    """The source cannot deliver a frame (TV off, unreachable or not set up)."""


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
