"""The bench's Home Assistant lights suite (#9): Glowcast's `ha_lights` output on proxy
lights in the local Home Assistant, each mirroring a light of the product
owner's Home Assistant (stubs/proxy.py), with the helper's test patterns.

The calibration runs first, as in the other suites (it finds the camera
lights through the bench's entertainment area); in this suite nothing streams
that area, and the run checks it stays inactive: the bridge ignores commands
to the lights of a streaming area. Judged with numbers only:
- the colours per position: a camera light with the camera, any other Home
  Assistant light by its state in the owner's Home Assistant;
- the rate, from the local HA's `call_service` events: per light at most once
  per its interval, per network at most 4 commands in any second, and
  `transition` its actual interval (the time since its command before);
- the restore after no picture, after a switch off and after the helper
  stops (tv_off): the owner's state as before the switch-on, and the camera
  lights as before the run.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

import ha_lights
import report as rp
import suite
from report import FAIL, PASS, SKIP, Step

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from custom_components.glowcast.core.color_engine import (  # noqa: E402
    channel_region,
)
from custom_components.glowcast.core.models import Channel  # noqa: E402

MODE = "ha-lights"
FRAME = (64, 36)  # the helper's frame (screen_helper/README.md)
SPLIT = "left-red-right-blue"


def expected_split(light: ha_lights.HaLight) -> str | None:
    """What the light shows for left-red-right-blue, from its band in the frame."""
    col0, col1, _, _ = channel_region(
        Channel(str(light.index), "", light.x, light.z), *FRAME
    )
    return ha_lights.split_colour(col0, col1, FRAME[0])


def entity_of(bench: Any, light: ha_lights.HaLight) -> str:
    """The light's proxy in the local Home Assistant."""
    return bench.proxies[light.index]["entity_id"]


async def run(bench: Any) -> None:
    """The suite, after the calibration, with the helper and the local HA up."""
    assert bench.ha is not None and bench.bridge is not None and bench.owner
    area = bench.cfg["bench_area_id"]
    if await bench.bridge.wait_status(area, "inactive", 15) is None:
        raise suite.BenchError(
            "the bench area streams; the Hue lights among the Home Assistant "
            "lights would not respond"
        )
    bench.owner.follow([light.entity_id for light in bench.ha_lights], 1.0)
    interval = float(
        bench.cfg["helper"].get("pattern_interval", suite.TIMING.pattern_interval)
    )
    # `loose_cycles`: old name until v0.4.
    cycles = int(bench.cfg.get("ha_lights_cycles", bench.cfg.get("loose_cycles", 1)))
    await bench.wait_for_red(interval)
    reference = await read_states(bench)
    on_at = time.time()
    if not await switch_on(
        bench, "switch on: Glowcast syncs the Home Assistant lights"
    ):
        return
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
    for (name, start), nxt in zip(steps, [*steps[1:], (None, end)], strict=True):
        stop = nxt[1]
        colours = suite.PATTERN_COLOURS
        if name in colours:
            colour_step(
                bench,
                name,
                {li.index: colours[name] for li in bench.ha_lights},
                start,
                stop,
            )
        elif name == SPLIT:
            colour_step(
                bench,
                name,
                {li.index: expected_split(li) for li in bench.ha_lights},
                start,
                stop,
            )
        elif name == "black":
            release_step(bench, reference, start, stop)
    rate_step(bench, on_at, bench.wsl(end))
    area_step(bench, on_at, bench.wsl(end))
    await bench.wait_for_colour(interval)
    await stop_step(
        bench, reference, "switch off: each Home Assistant light back", "switch"
    )
    await bench.wait_for_red(interval)
    # The same reference: a light that did not come back must not become it.
    if await switch_on(bench, "switch on again"):
        await bench.wait_for_colour(interval)
        await stop_step(
            bench,
            reference,
            "helper stopped: tv_off, each Home Assistant light back",
            "helper",
        )


async def read_states(bench: Any) -> dict[str, dict[str, Any]]:
    """Each Home Assistant light's state in the owner's HA: what a restore must
    bring back."""
    return {
        light.entity_id: await bench.owner.state(light.entity_id)
        for light in bench.ha_lights
    }


async def switch_on(bench: Any, name: str) -> bool:
    start = time.time()
    await bench.ha.call("switch", "turn_on", {"entity_id": bench.ha.entities["switch"]})
    status = await bench.ha.wait_state(
        "status", {"streaming", "black_picture", "no_picture"}, bench.th.stream_within_s
    )
    ok = status is not None
    bench.report.add(
        Step(
            name,
            MODE,
            PASS if ok else FAIL,
            seconds=None if status is None else status[1] - start,
            numbers={"limit s": bench.th.stream_within_s},
            note="" if ok else "Glowcast did not start syncing in time",
        )
    )
    return ok


def colour_step(
    bench: Any,
    pattern: str,
    expected: dict[int, str | None],
    start: float,
    stop: float,
    mode: str = MODE,
) -> None:
    """Each Home Assistant light shows its colour once settled (Windows
    times): from `ha_lights_settle_s` after Glowcast's first command to it in
    the step."""
    b = stop - 0.3
    samples = bench.samples()
    sent = ha_lights.commands(
        bench.ha.calls if bench.ha else [],
        [entity_of(bench, li) for li in bench.ha_lights],
    )
    numbers: dict[str, Any] = {}
    ok = True
    judged = 0
    for light in bench.ha_lights:
        label = bench.ha_lights_label[light.index]
        want = expected[light.index]
        a = ha_lights.judged_from(
            start,
            [bench.win(t) for t, _ in sent[entity_of(bench, light)]],
            stop,
            bench.th.ha_lights_settle_s,
        )
        states = ha_lights.states_between(
            bench.owner.samples, light.entity_id, bench.wsl(a), bench.wsl(b)
        )
        state_colour = ha_lights.settled_colour(states)
        if want is None:
            numbers[f"{label}"] = "skipped: its band takes from both halves"
            continue
        judged += 1
        numbers[f"{label} expected"] = want
        numbers[f"{label} judged from s"] = round(a - start, 2)
        if light.camera is not None:
            mean = rp.settled(samples, light.camera, a, b)
            cal = bench.cal[light.camera]
            seen = None if mean is None else rp.classify(mean, cal, bench.th)
            numbers[f"{label} seen"] = seen
            numbers[f"{label} level"] = (
                None if mean is None else round(cal.level(mean), 2)
            )
            numbers[f"{label} state"] = state_colour
            ok &= seen == want
        else:
            numbers[f"{label} state"] = state_colour
            numbers[f"{label} states"] = len(states)
            if state_colour != want:
                numbers[f"{label} colours"] = ha_lights.colour_counts(states)
            ok &= state_colour == want
    shown = "the colour of its band" if pattern == SPLIT else pattern
    bench.report.add(
        Step(
            f"{pattern}: each Home Assistant light shows {shown}",
            mode,
            (PASS if ok else FAIL) if judged else SKIP,
            numbers=numbers,
            note="" if ok else "another colour than expected",
        )
    )


def release_step(
    bench: Any,
    reference: dict[str, dict[str, Any]],
    start: float,
    stop: float,
    mode: str = MODE,
) -> None:
    """All-zero black: released after the no-picture wait, each light back."""
    timing = suite.TIMING
    released = bench.ha.first("status", "no_picture", bench.wsl(start))
    numbers: dict[str, Any] = {"no-picture wait s": timing.no_picture_wait}
    ok = released is not None and bench.win(released) < stop
    if released is not None:
        numbers["released after s"] = round(bench.win(released) - start, 2)
    back = lights_back(bench, reference, start + timing.back_from, stop)
    numbers.update(back[1])
    ok &= back[0]
    bench.report.add(
        Step(
            "no picture (all-zero black past the no-picture wait): "
            "Home Assistant lights released and back",
            mode,
            PASS if ok else FAIL,
            numbers=numbers,
            note="" if ok else "not released, or a light not back",
        )
    )


def lights_back(
    bench: Any, reference: dict[str, dict[str, Any]], start: float, end: float
) -> tuple[bool, dict[str, Any]]:
    """Whether every Home Assistant light is back from `start` (Windows times): its
    owner state as `reference`, and a camera light as before the run."""
    samples = bench.samples()
    numbers: dict[str, Any] = {}
    ok = True
    for light in bench.ha_lights:
        label = bench.ha_lights_label[light.index]
        t = ha_lights.first_restored(
            bench.owner.samples,
            light.entity_id,
            reference[light.entity_id],
            bench.wsl(start),
            bench.wsl(end),
        )
        numbers[f"{label} state back after s"] = (
            None if t is None else round(max(0.0, bench.win(t) - start), 2)
        )
        ok &= t is not None
        if light.camera is not None and light.camera in bench.pre_camera:
            seen = rp.first_match(
                samples,
                light.camera,
                bench.pre_camera[light.camera],
                bench.cal[light.camera],
                bench.th,
                start,
                end,
            )
            numbers[f"{label} seen back after s"] = (
                None if seen is None else round(seen - start, 2)
            )
            ok &= seen is not None
    return ok, numbers


def networks(bench: Any) -> dict[str, str | None]:
    """{local entity: its network}, as Glowcast's output groups them; the
    Home Assistant lights' proxies only (not one of a light in the area too, #198)."""
    sch = ha_lights.schedule()
    return {
        entry["entity_id"]: sch.network_of(entry["platform"], entry["config_entry_id"])
        for entry in (bench.proxies[light.index] for light in bench.ha_lights)
    }


def rate_step(bench: Any, start: float, end: float, mode: str = MODE) -> None:
    """The rate limits on Glowcast's commands between two WSL times."""
    entity_label = {
        entity_of(bench, light): bench.ha_lights_label[light.index]
        for light in bench.ha_lights
    }
    calls = [(t, data) for t, data in bench.ha.calls if start <= t <= end]
    sent = ha_lights.commands(calls, entity_label)
    network = networks(bench)
    kind_of = {entity_of(bench, light): light.kind for light in bench.ha_lights}
    network_label = {
        n: ha_lights.NETWORK_LABEL[kind_of[e]]
        for e, n in network.items()
        if n is not None
    }
    result = ha_lights.judge_rate(
        sent, network, entity_label, network_label, slack=bench.th.rate_slack_s
    )
    bench.report.add(
        Step(
            "the rate: per light its interval, per network at most 4 commands "
            "a second, transition = the actual interval",
            mode,
            PASS if result.ok else FAIL,
            seconds=end - start,
            numbers=result.numbers,
            note=result.note,
        )
    )


def area_step(bench: Any, start: float, end: float) -> None:
    """Nothing streamed the bench area while the Home Assistant lights were driven."""
    statuses = bench.bridge.statuses(start, end)
    ok = bool(statuses) and all(s["status"] == "inactive" for s in statuses)
    bench.report.add(
        Step(
            "the bench area did not stream during the Home Assistant light steps",
            MODE,
            PASS if ok else FAIL,
            numbers={"bridge polls": len(statuses)},
            note="" if ok else "the area streamed: the bridge ignores light commands",
        )
    )


async def stop_step(
    bench: Any, reference: dict[str, dict[str, Any]], name: str, how: str
) -> None:
    """Stop Glowcast (switch off, or the helper gone); each light back."""
    if how == "switch":
        start = time.time()
        await bench.ha.call(
            "switch", "turn_off", {"entity_id": bench.ha.entities["switch"]}
        )
    else:
        stopped = await bench.stop_helper()
        start = stopped or time.time()
    expected = "idle" if how == "switch" else "tv_off"
    status = await bench.ha.wait_state("status", {expected}, 20)
    within = bench.th.restore_within_s + 2.0  # the owner's state, polled each second
    camera = await bench.wait_restored(bench.win(start), within)
    ok, numbers = lights_back(
        bench, reference, bench.win(start), bench.win(start) + within
    )
    for light, seconds in camera.items():
        numbers[f"{bench.label[light]} camera back after s"] = seconds
    ok &= all(v is not None for v in camera.values())
    problems = []
    if status is None:
        problems.append(f"status never {expected}")
        ok = False
    if how == "helper":
        attrs = (await bench.ha.state("status"))["attributes"]
        numbers["stop reason"] = attrs.get("stop_reason")
        if attrs.get("stop_reason") != "tv_off":
            problems.append("wrong stop reason")
            ok = False
    if not ok and not problems:
        problems.append("a light did not come back")
    bench.report.add(
        Step(
            name,
            MODE,
            PASS if ok else FAIL,
            seconds=None if status is None else status[1] - start,
            numbers=numbers,
            note="; ".join(problems),
        )
    )
