"""What the helper reports: one JSON line per event with --json, else plain text.

Never pass a token here. The pairing code only goes into an event when the
caller passed it in with --pairing-code (see cli.py).
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, TextIO


def timestamp(seconds: float) -> str:
    """UTC time as ISO 8601 with milliseconds, for example 2026-10-29T12:00:05.000Z."""
    return (
        datetime.fromtimestamp(seconds, UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


class Events:
    def __init__(
        self,
        json_mode: bool,
        out: TextIO | None = None,
        err: TextIO | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.json_mode = json_mode
        self._out = out or sys.stdout
        self._err = err or sys.stderr
        self._clock = clock

    def emit(self, event: str, text: str, /, **fields: Any) -> None:
        """Report an event: `fields` as JSON with --json, `text` otherwise."""
        if self.json_mode:
            line = {"event": event, "time": timestamp(self._clock()), **fields}
            # flush: an agent reading the pipe must see each line at once.
            print(json.dumps(line), file=self._out, flush=True)
        else:
            print(text, file=self._out, flush=True)

    def say(self, message: str) -> None:
        """Text for the person at the keyboard only; never part of the JSON output."""
        print(message, file=self._err if self.json_mode else self._out, flush=True)
