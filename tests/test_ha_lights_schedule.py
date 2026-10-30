"""The loose lights' command schedule: rate limits per light and per network.

Plain unit tests with the time as an argument (#9, limits from #125).
"""

from custom_components.glowcast.outputs.ha_lights_schedule import (
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


def most_in_one_second(times: list[float]) -> int:
    """The most commands in any window of 1 s (half open)."""
    return max(
        (sum(1 for t in times if start <= t < start + 1.0) for start in times),
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
    assert most_in_one_second([t for t, _ in sent]) <= 4


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
