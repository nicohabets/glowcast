"""The light test (#182): known colours by position, while syncing and while off.

Fakes and a fake clock only (architecture D13).
"""

import asyncio

import pytest

from custom_components.glowcast.core.light_state import FADE_IN
from custom_components.glowcast.core.light_test import (
    STEP,
    LightTest,
    pattern_colors,
)
from custom_components.glowcast.core.models import Channel
from custom_components.glowcast.core.pipeline import (
    NO_PICTURE_WAIT,
    Phase,
    StopReason,
    run_light_test,
)
from custom_components.glowcast.core.ports import OutputBusy, OutputNotFound

from .fakes import NO_PICTURE, RED, FakeClock, FakeOutput, settle, solid
from .test_pipeline import Harness

RED_RGB = (1.0, 0.0, 0.0)
BLUE_RGB = (0.0, 0.0, 1.0)
GREEN_RGB = (0.0, 1.0, 0.0)
WHITE_RGB = (1.0, 1.0, 1.0)
# A light at the top left and one at the bottom right: each step tells them apart.
TOP_LEFT = Channel(id="tl", name="top left", x=-1.0, z=1.0)
BOTTOM_RIGHT = Channel(id="br", name="bottom right", x=1.0, z=-1.0)


def approx(rgb: tuple[float, float, float]) -> object:
    return pytest.approx(rgb, abs=0.01)


def by_id(colors: list) -> dict[str, tuple[float, float, float]]:
    return {color.channel_id: color.rgb for color in colors}


# The colours by position


@pytest.mark.parametrize(
    ("x", "z", "first", "second"),
    [
        (-1.0, 0.0, RED_RGB, WHITE_RGB),  # left
        (1.0, 0.0, BLUE_RGB, WHITE_RGB),  # right
        (0.0, 1.0, None, GREEN_RGB),  # top
        (0.0, -1.0, None, WHITE_RGB),  # bottom
        (0.0, 0.0, None, WHITE_RGB),  # ambient: white, as the bottom
        # A gradient strip's segments near the TV (#23's positions).
        (-0.4, 0.4, RED_RGB, None),
        (0.4, -0.4, BLUE_RGB, WHITE_RGB),
        (-0.4, 1.0, RED_RGB, GREEN_RGB),
    ],
)
def test_each_position_gets_its_colour(
    x: float,
    z: float,
    first: tuple[float, float, float] | None,
    second: tuple[float, float, float] | None,
) -> None:
    channel = Channel(id="1", name="one", x=x, z=z)
    [[left_right], [top_bottom]] = pattern_colors([channel])
    if first is not None:
        assert left_right.rgb == approx(first)
    if second is not None:
        assert top_bottom.rgb == approx(second)


def test_the_middle_of_the_first_pattern_mixes_both_sides() -> None:
    """A light in the middle (ambient) takes from both halves: purple."""
    [[middle], _] = pattern_colors([Channel(id="1", name="one", x=0.0, z=0.0)])
    red, green, blue = middle.rgb
    assert red > 0.3 and blue > 0.3 and green == 0


def test_the_steps_take_3_s_each_at_the_brightness() -> None:
    test = LightTest(pattern_colors([TOP_LEFT, BOTTOM_RIGHT]), start=10.0)
    assert by_id(test.colors(10.0, 0.5)) == {
        "tl": approx((0.5, 0, 0)),
        "br": approx((0, 0, 0.5)),
    }
    assert by_id(test.colors(10.0 + STEP - 0.01, 1.0))["tl"] == approx(RED_RGB)
    assert by_id(test.colors(10.0 + STEP, 1.0)) == {
        "tl": approx(GREEN_RGB),
        "br": approx(WHITE_RGB),
    }
    assert test.colors(10.0 + 2 * STEP, 1.0) is None


# While syncing


def syncing_harness() -> Harness:
    h = Harness()
    h.output.channels = [TOP_LEFT, BOTTOM_RIGHT]
    h.source.default = solid(RED)
    return h


def last_sent(h: Harness) -> dict[str, tuple[float, float, float]]:
    return by_id(h.output.sent[-1][1])


async def test_while_syncing_the_test_replaces_the_picture_and_fades_back() -> None:
    h = syncing_harness()
    await h.start()
    await h.clock.advance(3)  # the fade-in after the claim is over
    assert last_sent(h) == {"tl": approx(RED_RGB), "br": approx(RED_RGB)}

    assert await h.pipeline.async_test()
    assert h.pipeline.testing
    await h.clock.advance(0.1)
    assert last_sent(h) == {"tl": approx(RED_RGB), "br": approx(BLUE_RGB)}
    await h.clock.advance(STEP)
    assert last_sent(h) == {"tl": approx(GREEN_RGB), "br": approx(WHITE_RGB)}

    # Over: the next frame (within 1/3 s) fades in from the test's last colours.
    await h.clock.advance(STEP + 0.4)
    assert not h.pipeline.testing
    await h.clock.advance(FADE_IN / 2)
    halfway = last_sent(h)
    assert 0.1 < halfway["tl"][1] < 0.9  # between green and red
    assert 0.1 < halfway["br"][2] < 0.9  # between white and red
    await h.clock.advance(FADE_IN)
    assert last_sent(h) == {"tl": approx(RED_RGB), "br": approx(RED_RGB)}

    # No new handshake, and the status said streaming throughout.
    assert h.log.count("output.start") == 1
    assert h.phases == [Phase.STARTING, Phase.STREAMING]
    assert await h.stop() == StopReason.USER


async def test_the_test_shows_until_the_next_frame() -> None:
    """Between the end of the test and the next frame, nothing jumps."""
    h = syncing_harness()
    await h.start()
    await h.clock.advance(3)
    assert await h.pipeline.async_test()
    h.source.frames = ["hang", "hang"]  # no frame for about 10 s
    await h.clock.advance(2 * STEP + 1)
    assert h.pipeline.testing  # over, waiting for a frame to fade back from
    assert last_sent(h) == {"tl": approx(GREEN_RGB), "br": approx(WHITE_RGB)}
    await h.clock.advance(5)  # a frame came
    assert not h.pipeline.testing
    assert await h.stop() == StopReason.USER


async def test_a_second_press_during_the_test_does_nothing() -> None:
    h = syncing_harness()
    await h.start()
    await h.clock.advance(1)
    assert await h.pipeline.async_test()
    await h.clock.advance(1)
    assert not await h.pipeline.async_test()
    await h.clock.advance(STEP)  # still the same test: its second step
    assert last_sent(h) == {"tl": approx(GREEN_RGB), "br": approx(WHITE_RGB)}
    assert await h.stop() == StopReason.USER


async def test_no_test_while_the_lights_are_released_for_no_picture() -> None:
    h = syncing_harness()
    h.source.default = solid(NO_PICTURE)
    await h.start()
    await h.clock.advance(NO_PICTURE_WAIT + 1)
    assert h.statuses[-1].phase is Phase.NO_PICTURE
    assert not await h.pipeline.async_test()
    assert await h.stop() == StopReason.USER


async def test_a_release_ends_the_test() -> None:
    h = syncing_harness()
    await h.start()
    await h.clock.advance(1)
    assert await h.pipeline.async_test()
    h.source.default = solid(NO_PICTURE)
    await h.clock.advance(NO_PICTURE_WAIT + 1)
    assert h.statuses[-1].phase is Phase.NO_PICTURE
    assert not h.pipeline.testing
    assert await h.stop() == StopReason.USER


async def test_switching_off_during_the_test_stops_as_usual() -> None:
    h = syncing_harness()
    await h.start()
    await h.clock.advance(1)
    assert await h.pipeline.async_test()
    await h.clock.advance(1)
    assert await h.stop() == StopReason.USER
    assert h.log[-3:] == ["output.stop", "output.close", "source.close"]
    assert not h.pipeline.testing


# While off: the lights are claimed for the test only


class OffTest:
    def __init__(self) -> None:
        self.clock = FakeClock()
        self.log: list[str] = []
        self.output = FakeOutput(
            self.clock, self.log, channels=[TOP_LEFT, BOTTOM_RIGHT]
        )
        self.task: asyncio.Task[StopReason | None] | None = None

    async def start(self, brightness: float = 1.0) -> None:
        self.task = asyncio.ensure_future(
            run_light_test(self.output, brightness, self._runner, self.clock)
        )
        await settle()

    async def _runner(self, func, *args):  # type: ignore[no-untyped-def]
        return func(*args)


async def test_off_claims_shows_both_steps_and_releases() -> None:
    t = OffTest()
    await t.start(brightness=0.8)
    assert t.log == ["output.start"]
    await t.clock.advance(0.1)
    assert by_id(t.output.sent[-1][1]) == {
        "tl": approx((0.8, 0, 0)),
        "br": approx((0, 0, 0.8)),
    }
    await t.clock.advance(STEP)
    assert by_id(t.output.sent[-1][1]) == {
        "tl": approx((0, 0.8, 0)),
        "br": approx((0.8, 0.8, 0.8)),
    }
    await t.clock.advance(STEP)
    assert t.task.done()
    assert await t.task is None
    assert t.log == ["output.start", "output.stop", "output.close"]
    # At the output's own rate, 25 per second here.
    assert 2 * STEP * 25 - 2 <= len(t.output.sent) <= 2 * STEP * 25 + 2


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (OutputBusy("another app streams"), StopReason.OUTPUT_BUSY),
        (OutputNotFound("gone"), StopReason.OUTPUT_NOT_FOUND),
        (RuntimeError("unexpected"), StopReason.OUTPUT_UNAVAILABLE),
    ],
)
async def test_off_a_start_error_sends_nothing(
    error: Exception, reason: StopReason
) -> None:
    t = OffTest()
    t.output.start_errors = [error]
    await t.start()
    await t.clock.advance(1)
    assert await t.task is reason
    assert t.output.sent == []
    # Released as after any failed start; a busy output stops nothing of others.
    assert t.log == ["output.start", "output.stop", "output.close"]


async def test_off_cancelled_mid_test_still_releases() -> None:
    """Unload, Home Assistant stopping or a switch-on during the test."""
    t = OffTest()
    t.output.slow_stop = True
    await t.start()
    await t.clock.advance(1)
    t.task.cancel()
    await settle()
    with pytest.raises(asyncio.CancelledError):
        await t.task
    assert t.log == ["output.start", "output.stop", "output.close"]


async def test_off_cancelled_again_during_the_release_still_completes() -> None:
    t = OffTest()
    t.output.slow_stop = True
    await t.start()
    await t.clock.advance(1)
    t.task.cancel()
    await asyncio.sleep(0)
    t.task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t.task
    assert t.log == ["output.start", "output.stop", "output.close"]
