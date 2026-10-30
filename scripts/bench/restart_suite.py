"""The bench's restart suite (#183): Glowcast continues after Home Assistant
restarts.

Only the bench's own local Home Assistant (local_ha.py, in WSL on 127.0.0.1)
is restarted, never the owner's. After the calibration, in pattern mode, with
the bench's entertainment area and a steady picture (left-red-right-blue,
never black: no picture would release the lights):
1. switched on: the area streams, each light its side; then HA stopped
   gracefully (SIGTERM: its stop stages store the switch's `resume`) and
   started again on the same config: the switch on again within the limit,
   the area streams again with one stream, each light its side again, and
   the local HA's log says Glowcast continues;
2. switched off (the lights back, camera), restarted the same way: the
   switch stays off and the area inactive;
3. the end as in every suite (run.cleanup): Glowcast off, the area inactive,
   the lights set back explicitly and checked with the camera.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
import time
from typing import Any

import clients
import combined
import local_ha
from report import FAIL, PASS, Step
from suite import BenchError

MODE = "restart"
STEADY = "left-red-right-blue"
# Glowcast's line when it starts by itself after the restart (runtime.py).
RESUMED = "continues after Home Assistant's restart"
# The waits (a test runs the steps without them).
sleep = asyncio.sleep


async def run(bench: Any) -> None:
    """The suite, after the calibration, with the helper and the local HA up."""
    assert bench.ha is not None and bench.bridge is not None
    bench.helper_pattern = STEADY
    await bench.start_helper("pattern")
    if not await bench.switch_on(MODE, name="switch on, steady picture"):
        return
    on = time.time()
    await sleep(8)
    bench.left_right_step(
        MODE,
        bench.win(on),
        bench.win(time.time()),
        title="before the restart: each light the colour of its side",
    )
    # 1: restarted while syncing.
    stopping = time.time()
    done = await restart(bench)
    stop_step(bench, done, True)
    await resumed(bench, done, stopping)
    # 2: restarted while off.
    await bench.check_stop(
        MODE, "switch off: area released, lights back (camera)", "switch"
    )
    done = await restart(bench)
    stop_step(bench, done, False)
    await stays_off(bench, done)


async def restart(bench: Any) -> local_ha.Restarted:
    """Restart the local HA (local_ha.restart); the bench's client follows
    with the new token, and keeps the run's record."""
    old = bench.ha
    await old.close()  # its websocket ends with HA anyway
    # A future, not a task: a SystemExit in the thread then reaches the
    # except below, instead of leaving the event loop from the task.
    bench.ha_start = asyncio.get_running_loop().run_in_executor(
        None, local_ha.restart, old.entities["switch"]
    )
    try:
        inst, token, done = await asyncio.shield(bench.ha_start)
    except SystemExit as err:
        print(f"The local Home Assistant did not start again: {err}", file=sys.stderr)
        raise BenchError("the local Home Assistant did not start again") from None
    bench.tokens.append(token)
    bench.ha = clients.LocalHA(bench.session, inst.url, token)
    bench.ha.events, bench.ha.calls = old.events, old.calls
    if "switch" not in await bench.ha.find_entities(local_ha.ENTRY_ID):
        raise BenchError("Glowcast's switch is gone after the restart")
    await bench.ha.follow()
    return done


def yes(value: bool | None) -> str:
    return "not stored" if value is None else "yes" if value else "no"


def stop_step(bench: Any, done: local_ha.Restarted, syncing: bool) -> None:
    """HA stopped by itself (its stop stages ran) with the switch's `resume`
    stored as it should: yes while syncing, no while off."""
    ok = done.graceful and done.resume is syncing
    if ok:
        note = ""
    elif not done.graceful:
        note = "killed after the stop limit"
    else:
        note = "the switch's resume was not stored as expected"
    bench.report.add(
        Step(
            f"restart {'while syncing' if syncing else 'while off'}: HA stopped "
            f"gracefully, resume stored as {yes(syncing)}",
            MODE,
            PASS if ok else FAIL,
            seconds=done.stop_s,
            numbers={
                "stopped by itself": yes(done.graceful),
                "stored resume": yes(done.resume),
            },
            note=note,
        )
    )


def streamed_again(
    during: list[dict[str, Any]], after: list[dict[str, Any]]
) -> tuple[bool, bool, int]:
    """Released during the restart (Glowcast stopped with HA), then streaming
    with one stream: (ok, released, bridge polls after)."""
    released = any(s.get("status") == "inactive" for s in during)
    streams, polls = combined.one_stream(after)
    return released and streams, released, polls


def stayed_off(switch: list[str], statuses: list[dict[str, Any]]) -> bool:
    """The switch off on every read, the area inactive on every poll."""
    return (
        bool(switch)
        and bool(statuses)
        and all(state == "off" for state in switch)
        and all(s.get("status") == "inactive" for s in statuses)
    )


def logged_resume(text: str) -> bool:
    """Whether the local HA's log has Glowcast's line of a resume."""
    return RESUMED in text


async def read_log() -> str:
    """The local HA's log since its start (HA rolls it over at every start)."""
    with contextlib.suppress(OSError):
        return await asyncio.to_thread(local_ha.log_path().read_text, errors="replace")
    return ""


async def resumed(bench: Any, done: local_ha.Restarted, stopping: float) -> None:
    """Step 1 after the restart: the switch, the stream, the colours, the log."""
    th = bench.th
    within = done.running_at + th.resume_within_s + 5 - time.time()
    on = await bench.ha.wait_state("switch", {"on"}, max(1.0, within))
    on_after = None if on is None else round(max(0.0, on[1] - done.running_at), 2)
    ok = on_after is not None and on_after <= th.resume_within_s
    bench.report.add(
        Step(
            "after the restart: the switch on again by itself",
            MODE,
            PASS if ok else FAIL,
            seconds=on_after,
            numbers={"limit s": th.resume_within_s},
            note="" if ok else "the switch did not come on in time",
        )
    )
    if on is not None:
        await bench.bridge.wait_status(
            bench.cfg["bench_area_id"], "active", th.stream_within_s
        )
    # Glowcast may stream again before the bench sees HA running (it looks
    # for the picture source as soon as HA has started): the first stream
    # after the release during the restart counts.
    released_at = bench.bridge.first("inactive", stopping)
    first = bench.bridge.first(
        "active", done.running_at if released_at is None else released_at
    )
    if first is not None:
        # Long enough for the colours to settle (left_right_step).
        await sleep(max(0.0, first + 9 - time.time()))
    end = time.time()
    good, released, polls = streamed_again(
        bench.bridge.statuses(stopping, first or done.running_at),
        [] if first is None else bench.bridge.statuses(first + 1.0, end),
    )
    streaming = None if first is None else round(max(0.0, first - done.running_at), 2)
    bench.report.add(
        Step(
            "after the restart: the area streams again (one stream)",
            MODE,
            PASS if good else FAIL,
            seconds=streaming,
            numbers={
                "HA running to streaming s": streaming,
                "released during the restart": yes(released),
                "bridge polls": polls,
            },
            note="" if good else "not released, not streaming, or streamed again",
        )
    )
    if first is not None:
        bench.left_right_step(
            MODE,
            bench.win(first),
            bench.win(end),
            title="after the restart: each light the colour of its side again",
        )
    found = logged_resume(await read_log())
    bench.report.add(
        Step(
            "after the restart: the local HA's log says Glowcast continues",
            MODE,
            PASS if found else FAIL,
            numbers={"line in the log": yes(found)},
        )
    )


async def stays_off(bench: Any, done: local_ha.Restarted) -> None:
    """Step 2 after the restart: off at the stop, so off it stays."""
    th = bench.th
    states: list[str] = []
    for _ in range(max(1, round(done.running_at + th.stays_off_s - time.time()))):
        with contextlib.suppress(Exception):
            states.append((await bench.ha.state("switch"))["state"])
        await sleep(1)
    statuses = bench.bridge.statuses(done.running_at, time.time())
    found = logged_resume(await read_log())
    ok = stayed_off(states, statuses) and not found
    bench.report.add(
        Step(
            "after the restart, off before: Glowcast stays off, the area inactive",
            MODE,
            PASS if ok else FAIL,
            seconds=th.stays_off_s,
            numbers={
                "switch reads": len(states),
                "switch on seen": yes(any(s != "off" for s in states)),
                "bridge polls": len(statuses),
                "area active seen": yes(
                    any(s.get("status") != "inactive" for s in statuses)
                ),
                "line in the log": yes(found),
            },
            note="" if ok else "Glowcast started by itself, or the area streamed",
        )
    )
