"""Screen capture with a fake grabber: the shrink, the byte order, the screen choice."""

from __future__ import annotations

import asyncio
import threading

import pytest
from fakes import FakeClock, FakeGrabber, left_red_right_blue
from glowcast_screen.capture import (
    CaptureError,
    NoScreenAccess,
    ScreenCapture,
    downscale,
)
from glowcast_screen.patterns import FRAME_SIZE, FRAMES, HEIGHT, WIDTH


@pytest.mark.parametrize(
    ("width", "height"), [(64, 36), (640, 360), (1920, 1080), (3440, 1440)]
)
def test_downscale_gives_64x36_rgb_row_major(width: int, height: int) -> None:
    frame = downscale(width, height, left_red_right_blue(width, height))
    assert len(frame) == FRAME_SIZE
    # BGRA in, RGB out: the left half red, the right half blue, row by row. The
    # two middle columns may blend where the edge falls inside a reduced pixel.
    for y in range(HEIGHT):
        for x in [*range(31), *range(33, WIDTH)]:
            i = (y * WIDTH + x) * 3
            assert frame[i : i + 3] == bytes((255, 0, 0) if x < 32 else (0, 0, 255))


def test_downscale_keeps_top_and_bottom_in_place() -> None:
    width, height = 640, 360
    bgra = bytearray(left_red_right_blue(width, height))
    # Paint the top tenth green to check the row order.
    for y in range(36):
        for x in range(width):
            i = (y * width + x) * 4
            bgra[i : i + 4] = bytes((0, 255, 0, 255))
    frame = downscale(width, height, bytes(bgra))
    assert frame[:3] == bytes((0, 255, 0))
    assert frame[-3:] == bytes((0, 0, 255))
    assert frame[(4 * WIDTH) * 3 : (4 * WIDTH) * 3 + 3] == bytes((255, 0, 0))


async def test_default_is_the_main_screen() -> None:
    grabber = FakeGrabber()
    capture = ScreenCapture(grabber, None)
    await capture.start()
    assert capture.screen is not None and capture.screen.index == 1
    assert await capture.frame() == FRAMES["red"]
    await capture.close()


async def test_main_screen_need_not_be_the_first() -> None:
    grabber = FakeGrabber()
    a, b = grabber.screens
    grabber.screens = [
        type(a)(index=1, width=a.width, height=a.height, left=0, top=0, primary=False),
        type(b)(index=2, width=b.width, height=b.height, left=512, top=0, primary=True),
    ]
    capture = ScreenCapture(grabber, None)
    await capture.start()
    assert capture.screen is not None and capture.screen.index == 2
    await capture.close()


async def test_screen_option_picks_the_second_monitor() -> None:
    grabber = FakeGrabber()
    capture = ScreenCapture(grabber, 2)
    await capture.start()
    assert await capture.frame() == FRAMES["left-red-right-blue"]
    assert set(grabber.grabs) == {2}
    await capture.close()


async def test_a_screen_that_does_not_exist_is_no_screen_access() -> None:
    capture = ScreenCapture(FakeGrabber(), 3)
    with pytest.raises(
        NoScreenAccess, match="no screen 3; screens: 1 \\(512x216\\), 2"
    ):
        await capture.start()
    await capture.close()


async def test_no_access_and_a_failing_first_grab_stop_the_start() -> None:
    grabber = FakeGrabber()
    grabber.no_access = "no permission"
    with pytest.raises(NoScreenAccess, match="no permission"):
        await ScreenCapture(grabber, None).start()

    grabber = FakeGrabber()
    grabber.fail = CaptureError("display gone")
    with pytest.raises(NoScreenAccess, match="display gone"):
        await ScreenCapture(grabber, None).start()


async def test_a_failing_grab_is_no_picture_and_recovers() -> None:
    clock = FakeClock()
    grabber = FakeGrabber()
    capture = ScreenCapture(grabber, None, clock=clock)
    await capture.start()

    grabber.fail = CaptureError("locked")
    clock.now += 1
    assert await capture.frame() is None
    assert capture.no_picture_reason == "capture_failed"

    grabber.fail = None
    clock.now += 1
    assert await capture.frame() == FRAMES["red"]
    assert capture.no_picture_reason is None
    await capture.close()


async def test_a_hanging_grab_is_no_picture(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("glowcast_screen.capture.GRAB_TIMEOUT", 0.05)
    clock = FakeClock()
    grabber = FakeGrabber()
    capture = ScreenCapture(grabber, None, clock=clock)
    await capture.start()
    grabber.block = threading.Event()
    clock.now += 1
    assert await capture.frame() is None
    assert capture.no_picture_reason == "capture_timeout"
    grabber.block.set()
    await capture.close()


async def test_grabs_only_on_demand_and_shares_one_grab() -> None:
    clock = FakeClock()
    grabber = FakeGrabber()
    capture = ScreenCapture(grabber, None, clock=clock)
    await capture.start()
    assert len(grabber.grabs) == 1  # the check at start

    # Nothing is grabbed while nobody asks.
    await asyncio.sleep(0.05)
    assert len(grabber.grabs) == 1

    clock.now += 1
    frames = await asyncio.gather(*(capture.frame() for _ in range(5)))
    assert frames == [FRAMES["red"]] * 5
    assert len(grabber.grabs) == 2

    # A frame younger than MAX_AGE is served again.
    assert await capture.frame() == FRAMES["red"]
    assert len(grabber.grabs) == 2
    clock.now += 1
    await capture.frame()
    assert len(grabber.grabs) == 3
    await capture.close()


async def test_every_grab_runs_on_one_thread_off_the_loop() -> None:
    clock = FakeClock()
    grabber = FakeGrabber()
    capture = ScreenCapture(grabber, None, clock=clock)
    await capture.start()
    for _ in range(3):
        clock.now += 1
        await capture.frame()
    await capture.close()
    assert len(grabber.threads) == 1
    assert threading.get_ident() not in grabber.threads
    assert grabber.closed


async def test_capture_times_are_reported_and_reset() -> None:
    clock = FakeClock()
    capture = ScreenCapture(FakeGrabber(), None, clock=clock)
    await capture.start()
    capture.take_capture_ms()
    assert capture.take_capture_ms() is None
    clock.now += 1
    await capture.frame()
    times = capture.take_capture_ms()
    assert times is not None and 0 <= times["median"] <= times["max"]
    await capture.close()
