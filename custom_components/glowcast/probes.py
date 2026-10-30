"""Running one source or output type's hardware-check probe (#115).

Generic: it knows only the probe and `is_missing` of a type (architecture 5).
Shared by the hardware check and the combined output, which probes each part.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__package__)

# A type's probe: (hass, data, options, measure=...) -> what it found. With
# `measure` false (Glowcast syncs) a source must not capture.
type Probe = Callable[..., Awaitable[dict[str, Any]]]

# One type's probe may take this long; the whole check stays within 30 s.
PROBE_TIMEOUT = 25.0


class ProbedType(Protocol):
    """What a source or output type offers the check."""

    @property
    def probe(self) -> Probe | None:
        """The type's probe, or None when it has none."""

    @property
    def is_missing(
        self,
    ) -> Callable[[HomeAssistant, Mapping[str, Any], Mapping[str, Any]], bool]:
        """Whether the integration entry the type uses is missing."""


async def async_run_probe(
    kind: ProbedType | None,
    hass: HomeAssistant,
    data: Mapping[str, Any],
    options: Mapping[str, Any],
    *,
    measure: bool,
) -> dict[str, Any]:
    """Run the type's probe: {"probe": what it found}, or why it did not.

    A probe that fails, or takes longer than PROBE_TIMEOUT, is recorded by
    the kind of error only: an exception's text may hold an address.
    """
    if kind is None:
        return {"probe": None, "error": "unknown type"}
    if kind.probe is None:
        return {"probe": None}
    if kind.is_missing(hass, data, options):
        return {"probe": None, "error": "the integration it uses is not set up"}
    try:
        async with asyncio.timeout(PROBE_TIMEOUT):
            return {"probe": await kind.probe(hass, data, options, measure=measure)}
    except TimeoutError:
        return {"probe": None, "error": "timeout"}
    except Exception as err:  # noqa: BLE001 - recorded, the check still finishes
        # Its type only: the text or trace may hold an image URL (CLAUDE.md).
        _LOGGER.debug("The hardware check's probe failed: %s", type(err).__name__)
        return {"probe": None, "error": type(err).__name__}
