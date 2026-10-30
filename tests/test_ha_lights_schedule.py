"""The Home Assistant lights' command schedule: rate limits per light and per network.

Plain unit tests with the time as an argument (#9, limits from #125).
"""

import random

import pytest

from custom_components.glowcast.outputs.ha_lights_schedule import (
    TOLERANCE,
    Schedule,
    network_of,
)


def run(schedule: Schedule, seconds: float, tick: float = 0.25) -> list[tuple]:
    """Ask the schedule every tick; return (time, light) for every command."""
    sent = []
    steps = round(seconds / tick)
    for step in range(steps):
        now = step * tick
        sent.extend((now, light) for light in schedule.due(now, lambda light: True))
    return sent


def most_in_one_second(times: list[float], window: float = 1.0) -> int:
    """The most commands in any window of 1 s (half open)."""
    return max(
        (sum(1 for t in times if start <= t < start + window) for start in times),
        default=0,
    )


def test_network_of_hue_and_matter_lights() -> None:
    assert network_of("hue", "entry-1") == "hue:entry-1"
    assert network_of("matter", "entry-2") == "matter:entry-2"


def test_other_lights_have_no_shared_network() -> None:
    assert network_of("zha", "entry-3") is None
    assert network_of("wled", "entry-4") is None
    assert network_of("glowcast_bench_proxy", "entry-5") is None
    assert network_of("hue", None) is None


def test_each_light_once_per_second_on_its_own() -> None:
    schedule = Schedule({"a": (), "b": ()})
    sent = run(schedule, 5.0)
    for light in ("a", "b"):
        times = [t for t, name in sent if name == light]
        assert times == [0.0, 1.0, 2.0, 3.0, 4.0]
    assert schedule.interval("a") == 1.0


def test_four_lights_on_one_bridge_each_once_per_second() -> None:
    schedule = Schedule({name: ("hue:1",) for name in "abcd"})
    sent = run(schedule, 6.0)
    times = [t for t, _ in sent]
    assert most_in_one_second(times) <= 4
    # One command per tick at most, spread over the second.
    assert len(times) == len(set(times))
    for light in "abcd":
        assert schedule.interval(light) == 1.0
        light_times = [t for t, name in sent if name == light]
        assert len(light_times) >= 5


def test_more_than_four_lights_share_the_four_commands() -> None:
    schedule = Schedule({name: ("hue:1",) for name in "abcdefgh"})
    sent = run(schedule, 10.0)
    assert most_in_one_second([t for t, _ in sent]) <= 4
    assert schedule.interval("a") == 2.0
    for light in "abcdefgh":
        light_times = [t for t, name in sent if name == light]
        gaps = [b - a for a, b in zip(light_times, light_times[1:], strict=False)]
        assert gaps and min(gaps) >= 2.0


def test_networks_do_not_share_capacity() -> None:
    lights = {f"hue{i}": ("hue:1",) for i in range(4)}
    lights |= {f"matter{i}": ("matter:2",) for i in range(4)}
    schedule = Schedule(lights)
    sent = run(schedule, 4.0)
    hue = [t for t, name in sent if name.startswith("hue")]
    matter = [t for t, name in sent if name.startswith("matter")]
    assert most_in_one_second(hue) <= 4
    assert most_in_one_second(matter) <= 4
    assert schedule.interval("hue0") == schedule.interval("matter0") == 1.0
    assert len(hue) == len(matter) >= 12


def test_never_more_than_four_per_second_even_with_fast_ticks() -> None:
    schedule = Schedule({name: ("hue:1",) for name in "abcdef"})
    sent = run(schedule, 6.0, tick=0.05)
    assert most_in_one_second([t for t, _ in sent], 1.0 - TOLERANCE) <= 4


def test_a_light_on_two_networks_uses_a_slot_on_both() -> None:
    # A light group of Home Assistant with members on two bridges.
    lights = {"group": ("hue:1", "hue:2")}
    lights |= {f"one{i}": ("hue:1",) for i in range(4)}
    schedule = Schedule(lights)
    sent = run(schedule, 6.0)
    on_one = [t for t, name in sent if name == "group" or name.startswith("one")]
    assert most_in_one_second(on_one) <= 4
    assert schedule.interval("group") == 1.25


def test_a_light_that_is_not_ready_keeps_its_turn() -> None:
    schedule = Schedule({"a": ("hue:1",), "b": ("hue:1",)})
    assert schedule.due(0.0, lambda light: light == "b") == ["b"]
    # "a" was never sent, so it goes first once it is ready.
    assert schedule.due(0.25, lambda light: True) == ["a"]
    assert schedule.due(0.5, lambda light: True) == []
    assert schedule.due(1.0, lambda light: True) == ["b"]


def run_jittered(
    schedule: Schedule, seconds: float, seed: int = 1
) -> list[tuple[float, str, float]]:
    """As the pipeline ticks: every 0.25 s, each tick 0.5-4 ms late.

    Returns (time, light, transition) for every command.
    """
    rng = random.Random(seed)
    sent = []
    for step in range(round(seconds / 0.25)):
        now = step * 0.25 + rng.uniform(0.0005, 0.004)
        sent.extend(
            (now, light, schedule.transition(light))
            for light in schedule.due(now, lambda light: True)
        )
    return sent


def test_late_ticks_do_not_lose_commands() -> None:
    """Four lights on a bridge keep 4 commands per second with late ticks."""
    schedule = Schedule({name: ("hue:1",) for name in "abcd"})
    sent = run_jittered(schedule, 20.0)
    times = [t for t, _, _ in sent]
    # 4 per second, each light every second.
    assert len(times) >= 78
    for light in "abcd":
        light_times = [t for t, name, _ in sent if name == light]
        gaps = [b - a for a, b in zip(light_times, light_times[1:], strict=False)]
        assert max(gaps) < 1.01
    # Never more than 4 within a second, give or take the tolerance.
    assert most_in_one_second(times, 1.0 - TOLERANCE) <= 4


def test_the_transition_follows_the_actual_interval() -> None:
    schedule = Schedule({name: ("hue:1",) for name in "abcdef"})
    sent = run_jittered(schedule, 12.0)
    by_light: dict[str, list[tuple[float, float]]] = {}
    for t, light, transition in sent:
        by_light.setdefault(light, []).append((t, transition))
    for times in by_light.values():
        assert times[0][1] == 1.5  # the first: its share of the network
        for (before, _), (after, transition) in zip(times, times[1:], strict=False):
            assert transition == pytest.approx(after - before, abs=0.01)


def test_a_light_group_of_home_assistant_counts_as_its_members() -> None:
    # A group with 3 members on the bridge sends 3 commands at once.
    schedule = Schedule({"group": ("hue:1",) * 3, "single": ("hue:1",)})
    assert schedule.interval("group") == schedule.interval("single") == 1.0
    sent = run(schedule, 6.0)
    commands = [t for t, name in sent for _ in range(3 if name == "group" else 1)]
    assert most_in_one_second(commands) <= 4

    crowded = Schedule({"group": ("hue:1",) * 6, "single": ("hue:1",)})
    assert crowded.interval("single") == 7 / 4
    sent = run(crowded, 12.0)
    group_times = [t for t, name in sent if name == "group"]
    assert group_times
    # A group bigger than the limit goes alone, then the network rests.
    for start in group_times:
        assert not [
            t for t, name in sent if name == "single" and start - 1.0 < t < start + 1.0
        ]
