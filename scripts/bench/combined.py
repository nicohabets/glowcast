"""The bench's combined output (#11): the judgement of a part that fails.
Pure functions on recorded numbers, so `tests/test_bench_combined.py` runs
them in CI.

Glowcast's status sensor shows the parts of a combined output that do not run
in its attribute `dropped_outputs` ({part type: reason}); the loose lights'
part is `ha_lights`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

PART = "ha_lights"
ATTRIBUTE = "dropped_outputs"


def dropped(attributes: Mapping[str, Any] | None) -> dict[str, str]:
    """The dropped parts in the status sensor's attributes: {part: reason}."""
    value = (attributes or {}).get(ATTRIBUTE) or {}
    return {str(k): str(v) for k, v in value.items()} if isinstance(value, dict) else {}


def commands_after(
    calls: Iterable[tuple[float, dict[str, Any]]],
    entity_ids: Iterable[str],
    after: float,
    before: float | None = None,
) -> int:
    """Glowcast's colour commands to these lights between two times (a part
    that follows the picture again gets them; a dropped one none)."""
    wanted = set(entity_ids)
    count = 0
    for t, data in calls:
        if t < after or (before is not None and t > before):
            continue
        if "rgb_color" not in data or "transition" not in data:
            continue
        ids = data.get("entity_id")
        if any(e in wanted for e in ([ids] if isinstance(ids, str) else ids or [])):
            count += 1
    return count


def one_stream(statuses: list[dict[str, Any]]) -> tuple[bool, int]:
    """Whether the area streamed all along with one streamer (no new
    handshake, no release), and the number of bridge polls."""
    streamers = {s.get("streamer") for s in statuses}
    ok = (
        bool(statuses)
        and all(s.get("status") == "active" for s in statuses)
        and len(streamers) == 1
    )
    return ok, len(statuses)
