"""The bench's combined suite (#11): the bench's entertainment area and the
Home Assistant lights beside it (from `ha_lights:`, the ones the camera does not
see) in one Glowcast entry, output type `combined`.

After the calibration, in pattern mode:
1. with the helper's `cycle`: the area lights (camera) and the Home Assistant lights
   (their state in the owner's HA) follow the colours; the area streams with
   one stream while the Home Assistant lights get their commands at their own rate;
   switch off: the area released, the bench lights back (camera), the Home
   Assistant lights back (owner state);
2. with a steady picture (left-red-right-blue, never black): the Home
   Assistant lights' proxies made unavailable in the local HA only
   (`glowcast_bench.set_available`; the owner's lights are not touched): the
   part shows in `dropped_outputs` while the area streams on; available again
   before the retry: it follows again and the attribute clears; later
   (Glowcast's "lost again" window past), unavailable through the retry: it
   stays dropped with the reason, also once available again;
   then, with a Home Assistant light marked `in_area: true` (#198), still on the
   steady picture: that camera light's proxy (under its Hue light resource
   id) stored beside the Home Assistant lights and switched on: Glowcast sends it no
   light command, raises the repair issue, the area streams on with its
   lights on their sides and the Home Assistant lights beside it follow; switch off:
   everything back;
3. switched on again: the helper stopped (tv_off), the area released and
   every light back.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
import time
from pathlib import Path
from typing import Any

import combined
import ha_lights_suite
import local_ha
import suite
from report import FAIL, PASS, Step

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from custom_components.glowcast.core.pipeline import (  # noqa: E402
    LOST_AGAIN_WINDOW,
    LOST_RETRY_DELAY,
)

MODE = "combined"
STEADY = "left-red-right-blue"
IN_AREA = "a Home Assistant light in the area too"
IN_AREA_WAIT = 8.0  # seconds synced before the checks
# The waits (a test runs the steps without them).
sleep = asyncio.sleep


async def run(bench: Any) -> None:
    """The suite, after the calibration, with the helper and the local HA up."""
    assert bench.ha is not None and bench.bridge is not None and bench.owner
    bench.owner.follow([light.entity_id for light in bench.ha_lights], 1.0)
    reference = await colours(bench)
    if reference is None:
        return
    # 2: a part failing, on a steady picture (black would release everything).
    bench.helper_pattern = STEADY
    await bench.start_helper("pattern")
    if not await bench.switch_on(MODE, name="switch on, steady picture"):
        return
    await sleep(6)
    try:
        await part_fails(bench)
    finally:
        await set_available(bench, True)
    await switch_off(bench)
    await in_area(bench)
    # 3: both parts again from the start; the helper stopped. The Home
    # Assistant lights go back to their state at this start: a light that was
    # unavailable when its part was released kept Glowcast's last colour.
    reference = await ha_lights_suite.read_states(bench)
    if await bench.switch_on(MODE, name="switch on again: both parts"):
        await sleep(6)
        await stop_both(bench, reference, "helper stopped (tv_off)", "helper")


async def colours(bench: Any) -> dict[str, dict[str, Any]] | None:
    """Step 1 with the helper's cycle; the Home Assistant lights' reference states."""
    interval = float(
        bench.cfg["helper"].get("pattern_interval", suite.TIMING.pattern_interval)
    )
    cycles = int(bench.cfg.get("combined_cycles", 1))
    await bench.wait_for_red(interval)
    reference = await ha_lights_suite.read_states(bench)
    on_at = time.time()
    if not await bench.switch_on(
        MODE, name="switch on: the area streams, the Home Assistant lights follow"
    ):
        return None
    showing, since = bench.pattern_changes()[-1]
    cycle = list(suite.CYCLE)
    first_red = since + ((len(cycle) - cycle.index(showing)) % len(cycle)) * interval
    while first_red < bench.win_now() + 1:
        first_red += len(cycle) * interval
    end = first_red + cycles * len(cycle) * interval
    await bench.sleep_until_win(end + 1.5)
    steps = [
        (n, s) for n, s in bench.pattern_changes() if first_red - 0.1 <= s < end - 0.1
    ]
    bench.changes += [[MODE, n, t] for n, t in steps]
    streaming: list[tuple[float, float]] = []
    for (name, start), nxt in zip(steps, [*steps[1:], (None, end)], strict=True):
        stop = nxt[1]
        if name in suite.PATTERN_COLOURS:
            colour = suite.PATTERN_COLOURS[name]
            bench.colour_step(MODE, name, colour, start, stop)
            ha_lights_suite.colour_step(
                bench,
                name,
                dict.fromkeys(light_indexes(bench), colour),
                start,
                stop,
                MODE,
            )
            streaming.append((start, stop))
        elif name == STEADY:
            bench.left_right_step(MODE, start, stop)
            expected = {
                li.index: ha_lights_suite.expected_split(li) for li in bench.ha_lights
            }
            ha_lights_suite.colour_step(bench, name, expected, start, stop, MODE)
        elif name == "black":
            # No picture releases both parts; the Home Assistant lights come back.
            ha_lights_suite.release_step(bench, reference, start, stop, MODE)
    stream_step(bench, streaming)
    ha_lights_suite.rate_step(bench, on_at, bench.wsl(end), MODE)
    await bench.wait_for_colour(interval)
    await stop_both(bench, reference, "switch off", "switch")
    return reference


def light_indexes(bench: Any) -> list[int]:
    return [light.index for light in bench.ha_lights]


def stream_step(bench: Any, windows: list[tuple[float, float]]) -> None:
    """The area streamed all through the colour patterns, with one stream
    per run of patterns (windows on the Windows clock; a black between
    releases the lights, so each run counts on its own)."""
    runs: list[tuple[float, float]] = []
    for start, stop in windows:
        if runs and abs(runs[-1][1] - start) < 0.5:
            runs[-1] = (runs[-1][0], stop)
        else:
            runs.append((start, stop))
    ok = bool(runs)
    polls = 0
    for start, stop in runs:
        # From when the stream must be up (a claim after black takes a moment).
        good, n = combined.one_stream(
            bench.bridge.statuses(bench.wsl(start + 3.0), bench.wsl(stop - 0.3))
        )
        ok &= good
        polls += n
    bench.report.add(
        Step(
            "the area streams through the colours (one stream) beside the Home "
            "Assistant lights",
            MODE,
            PASS if ok else FAIL,
            numbers={"runs of patterns": len(runs), "bridge polls": polls},
            note="" if ok else "the area was released or streamed again",
        )
    )


async def set_available(bench: Any, available: bool) -> None:
    """The Home Assistant lights' proxies (un)available in the local HA only."""
    for light in bench.ha_lights:
        await bench.ha.call(
            "glowcast_bench",
            "set_available",
            {
                "entity_id": ha_lights_suite.entity_of(bench, light),
                "available": available,
            },
        )


async def wait_dropped(
    bench: Any, want: bool, within: float
) -> tuple[float | None, dict[str, str]]:
    """Until the Home Assistant lights' part is dropped (`want`) or not: the WSL time
    and the attribute, or None and the last attribute."""
    deadline = time.monotonic() + within
    seen: dict[str, str] = {}
    while time.monotonic() < deadline:
        with contextlib.suppress(Exception):
            seen = combined.dropped((await bench.ha.state("status"))["attributes"])
            if (combined.PART in seen) == want:
                return time.time(), seen
        await sleep(0.25)
    return None, seen


async def part_fails(bench: Any) -> None:
    """Step 2: the Home Assistant lights' part lost, back before the retry, then lost
    through the retry; the area streams on all along."""
    th = bench.th
    entities = [ha_lights_suite.entity_of(bench, light) for light in bench.ha_lights]
    # a: lost.
    start = time.time()
    await set_available(bench, False)
    dropped_at, seen = await wait_dropped(bench, True, th.drop_within_s + 5)
    ok = dropped_at is not None and dropped_at - start <= th.drop_within_s
    bench.report.add(
        Step(
            "a part failing (the Home Assistant lights unavailable in the local HA): "
            "shown as dropped",
            MODE,
            PASS if ok else FAIL,
            seconds=None if dropped_at is None else dropped_at - start,
            numbers={
                "limit s": th.drop_within_s,
                "reason": seen.get(combined.PART),
            },
            note="" if ok else "not shown as dropped in time",
        )
    )
    if dropped_at is None:
        return
    # b: back before the retry (LOST_RETRY_DELAY after the loss).
    back = time.time()
    await set_available(bench, True)
    cleared, seen = await wait_dropped(bench, False, th.clear_within_s + 5)
    await sleep(4)
    sent = combined.commands_after(bench.ha.calls, entities, back)
    stream, polls = combined.one_stream(bench.bridge.statuses(start, time.time()))
    ok = (
        cleared is not None
        and cleared - dropped_at <= th.clear_within_s
        and sent > 0
        and stream
    )
    bench.report.add(
        Step(
            "available again before the retry: follows again, the attribute "
            "clears, the area streams on",
            MODE,
            PASS if ok else FAIL,
            seconds=None if cleared is None else cleared - dropped_at,
            numbers={
                "retry delay s": LOST_RETRY_DELAY,
                "made available after s": round(back - dropped_at, 2),
                "commands after the retry": sent,
                "area one stream": "yes" if stream else "no",
                "bridge polls": polls,
            },
            note="" if ok else "not back, the attribute stayed, or the area stopped",
        )
    )
    if cleared is None:
        return
    # c: lost again after Glowcast's "lost again" window, through the retry.
    await sleep(max(0.0, cleared + LOST_AGAIN_WINDOW + 2 - time.time()))
    start = time.time()
    await set_available(bench, False)
    dropped_at, first = await wait_dropped(bench, True, th.drop_within_s + 5)
    await sleep(LOST_RETRY_DELAY + 5)
    after_retry = await current(bench)
    await set_available(bench, True)
    available_at = time.time()
    await sleep(8)
    later = await current(bench)
    sent = combined.commands_after(bench.ha.calls, entities, available_at)
    stream, polls = combined.one_stream(bench.bridge.statuses(start, time.time()))
    ok = (
        dropped_at is not None
        and combined.PART in after_retry
        and combined.PART in later
        and sent == 0
        and stream
    )
    bench.report.add(
        Step(
            "unavailable through the retry: stays dropped with the reason, the "
            "area streams on",
            MODE,
            PASS if ok else FAIL,
            seconds=None if dropped_at is None else dropped_at - start,
            numbers={
                "reason when lost": first.get(combined.PART),
                "reason after the retry": after_retry.get(combined.PART),
                "still dropped once available again": (
                    "yes" if combined.PART in later else "no"
                ),
                "commands once available again": sent,
                "area one stream": "yes" if stream else "no",
                "bridge polls": polls,
            },
            note="" if ok else "not dropped, came back, or the area stopped",
        )
    )


async def in_area(bench: Any) -> None:
    """Step 2, a Home Assistant light in the bench area too (#198), on the steady
    picture: left out of the Home Assistant lights, the repair issue raised, the area
    and the Home Assistant lights beside it on as before."""
    light = bench.in_area
    if light is None:
        return
    if light.index not in bench.proxies:
        bench.report.add(
            Step(
                f"{IN_AREA}: left out of the Home Assistant lights",
                MODE,
                FAIL,
                note="not matched to a bridge light in the calibration",
            )
        )
        return
    if (await bench.ha.state("switch"))["state"] == "on":
        await switch_off(bench)
    reference = await ha_lights_suite.read_states(bench)
    proxy = ha_lights_suite.entity_of(bench, light)
    before = await issue_raised(bench, tries=1)
    await bench.set_ha_lights_output([*bench.ha_lights, light])
    try:
        on_at = time.time()
        if not await bench.switch_on(MODE, name=f"switch on: {IN_AREA}"):
            return
        start = time.time()
        await sleep(IN_AREA_WAIT)
        await in_area_checks(bench, before, on_at, start, time.time())
        await stop_both(bench, reference, f"{IN_AREA}, switch off", "switch")
        sent = combined.calls_to(bench.ha.light_calls, [proxy], on_at, time.time())
        bench.report.add(
            Step(
                f"{IN_AREA}: Glowcast sent it no light command (on to off)",
                MODE,
                PASS if sent == 0 else FAIL,
                numbers={"light calls": sent},
                note="" if sent == 0 else "it was driven as a Home Assistant light",
            )
        )
    finally:
        if (await bench.ha.state("switch"))["state"] == "on":
            await switch_off(bench)
        await bench.set_ha_lights_output(bench.ha_lights)


async def issue_raised(bench: Any, tries: int = 10) -> bool:
    """Whether Glowcast's repair issue for Home Assistant lights in the area is up in
    the local HA; `tries` reads half a second apart (it is raised after the
    claim)."""
    for attempt in range(tries):
        if attempt:
            await sleep(0.5)
        with contextlib.suppress(Exception):
            if combined.has_issue(await bench.ha.issues(), local_ha.ENTRY_ID):
                return True
    return False


async def in_area_checks(
    bench: Any, before: bool, on_at: float, start: float, end: float
) -> None:
    """The in-area step's checks while syncing: the repair issue (not up
    before the switch on), the area (one stream; its lights by side, camera)
    and the Home Assistant lights beside it (their commands since the switch on)."""
    raised = await issue_raised(bench)
    ok = raised and not before
    bench.report.add(
        Step(
            f"{IN_AREA}: the repair issue is raised",
            MODE,
            PASS if ok else FAIL,
            numbers={
                "repair issue before": "yes" if before else "no",
                "repair issue": "yes" if raised else "no",
            },
            note="" if ok else "no repair issue, or it was up before",
        )
    )
    stream, polls = combined.one_stream(bench.bridge.statuses(start, end))
    bench.report.add(
        Step(
            f"{IN_AREA}: the area streams (one stream)",
            MODE,
            PASS if stream else FAIL,
            numbers={"bridge polls": polls},
            note="" if stream else "the area was released or streamed again",
        )
    )
    bench.left_right_step(
        MODE,
        bench.win(start),
        bench.win(end),
        f"{IN_AREA}: each area light the colour of its side (camera)",
    )
    numbers: dict[str, Any] = {}
    ok = True
    for other in bench.ha_lights:
        sent = combined.commands_after(
            bench.ha.calls, [ha_lights_suite.entity_of(bench, other)], on_at, end
        )
        numbers[f"{bench.ha_lights_label[other.index]} commands"] = sent
        ok &= sent > 0
    dropped = combined.PART in await current(bench)
    numbers["Home Assistant lights dropped"] = "yes" if dropped else "no"
    ok &= not dropped
    bench.report.add(
        Step(
            f"{IN_AREA}: the Home Assistant lights beside the area follow",
            MODE,
            PASS if ok else FAIL,
            numbers=numbers,
            note=""
            if ok
            else "a Home Assistant light got no command, or the part dropped",
        )
    )


async def current(bench: Any) -> dict[str, str]:
    with contextlib.suppress(Exception):
        return combined.dropped((await bench.ha.state("status"))["attributes"])
    return {}


async def switch_off(bench: Any) -> None:
    """Off after the failing part (its lights were unavailable when it was
    released, so they are not checked here; step 3 checks them)."""
    await bench.ha.call(
        "switch", "turn_off", {"entity_id": bench.ha.entities["switch"]}
    )
    await bench.ha.wait_state("status", {"idle"}, 20)
    await bench.bridge.wait_status(bench.cfg["bench_area_id"], "inactive", 15)


async def stop_both(
    bench: Any, reference: dict[str, dict[str, Any]], name: str, how: str
) -> None:
    """Stop (switch off, or the helper gone): the area and the bench lights
    as in the basic suite, then the Home Assistant lights' states."""
    start = time.time()
    await bench.check_stop(
        MODE, f"{name}: area released, bench lights back (camera)", how
    )
    within = bench.th.restore_within_s + 2.0
    await sleep(max(0.0, start + within + 1.0 - time.time()))
    ok, numbers = ha_lights_suite.lights_back(
        bench, reference, bench.win(start), bench.win(start + within + 1.0)
    )
    bench.report.add(
        Step(
            f"{name}: the Home Assistant lights back (owner state)",
            MODE,
            PASS if ok else FAIL,
            numbers=numbers,
            note="" if ok else "a light did not come back",
        )
    )
