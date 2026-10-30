"""The bench's combined output (#11): the judgement of a part that fails.
Pure functions on recorded numbers, so `tests/test_bench_combined.py` runs
them in CI.

Glowcast's status sensor shows the parts of a combined output that do not run
in its attribute `dropped_outputs` ({part type: reason}); the loose lights'
part is `ha_lights`.

A loose light that is in the entertainment area too (#198) gets no light
command from Glowcast, and Glowcast raises the repair issue
`loose_lights_in_area_<entry id>`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

PART = "ha_lights"
ATTRIBUTE = "dropped_outputs"
IN_AREA_ISSUE = "loose_lights_in_area"  # outputs/ha_lights.py IN_AREA


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


def calls_to(
    calls: Iterable[tuple[float, str, dict[str, Any]]],
    entity_ids: Iterable[str],
    after: float,
    before: float,
) -> int:
    """Any light.turn_on or turn_off call (time, service, data) to these
    lights between two times, colour or restore."""
    wanted = set(entity_ids)
    count = 0
    for t, _service, data in calls:
        ids = data.get("entity_id")
        if after <= t <= before and any(
            e in wanted for e in ([ids] if isinstance(ids, str) else ids or [])
        ):
            count += 1
    return count


def has_issue(issues: Iterable[Mapping[str, Any]], entry_id: str) -> bool:
    """Whether Glowcast's repair issue for loose lights in the area is up."""
    return any(
        issue.get("domain") == "glowcast"
        and issue.get("issue_id") == f"{IN_AREA_ISSUE}_{entry_id}"
        for issue in issues
    )
