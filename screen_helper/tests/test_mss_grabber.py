"""MssGrabber, the real grabber, against a stand-in `mss` module (no real screen)."""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest
from glowcast_screen.capture import (
    CaptureError,
    MssGrabber,
    NoScreenAccess,
    Screen,
    downscale,
)
from glowcast_screen.patterns import FRAMES


class ScreenShotError(Exception):
    pass


class FakeShot:
    def __init__(self, width: int, height: int, raw: bytearray) -> None:
        self.width = width
        self.height = height
        self.raw = raw


class FakeMss:
    """What mss.mss() returns: monitors (0 = all of them) and grab()."""

    instances: list[FakeMss] = []
    monitors_to_report: list[dict[str, Any]] = []
    fail_on_create = False
    fail_on_grab = False

    def __init__(self) -> None:
        if FakeMss.fail_on_create:
            raise ScreenShotError("$DISPLAY not set.")
        self.monitors = [{}, *(dict(m) for m in FakeMss.monitors_to_report)]
        self.grabbed: list[dict[str, int]] = []
        self.closed = False
        FakeMss.instances.append(self)

    def grab(self, box: dict[str, int]) -> FakeShot:
        if FakeMss.fail_on_grab:
            raise ScreenShotError("gdi32.BitBlt() failed.")
        self.grabbed.append(box)
        # Red left half, blue right half, as BGRA.
        width, height = box["width"], box["height"]
        row = bytes((0, 0, 255, 255)) * (width // 2) + bytes((255, 0, 0, 255)) * (
            width // 2
        )
        return FakeShot(width, height, bytearray(row * height))

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def fake_mss(monkeypatch: pytest.MonkeyPatch) -> None:
    module = types.ModuleType("mss")
    module.mss = FakeMss  # type: ignore[attr-defined]
    exception = types.ModuleType("mss.exception")
    exception.ScreenShotError = ScreenShotError  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mss", module)
    monkeypatch.setitem(sys.modules, "mss.exception", exception)
    # The macOS permission check is not what these tests are about.
    monkeypatch.setattr(
        "glowcast_screen.capture._macos_screen_access", lambda ask: True
    )
    FakeMss.instances = []
    FakeMss.fail_on_create = False
    FakeMss.fail_on_grab = False
    FakeMss.monitors_to_report = [
        {"left": 0, "top": 0, "width": 512, "height": 216, "is_primary": False},
        {"left": 512, "top": 0, "width": 256, "height": 144, "is_primary": True},
    ]


def test_open_lists_screens_with_the_marked_main_screen() -> None:
    assert MssGrabber(ask_permission=False).open() == [
        Screen(index=1, width=512, height=216, left=0, top=0, primary=False),
        Screen(index=2, width=256, height=144, left=512, top=0, primary=True),
    ]


def test_without_a_marked_main_screen_the_first_is_main() -> None:
    # macOS reports no is_primary; its first display is the main one.
    for monitor in FakeMss.monitors_to_report:
        del monitor["is_primary"]
    screens = MssGrabber(ask_permission=False).open()
    assert [s.primary for s in screens] == [True, False]


def test_grab_uses_the_screen_box_and_returns_bgra() -> None:
    grabber = MssGrabber(ask_permission=False)
    screens = grabber.open()
    width, height, raw = grabber.grab(screens[1])
    assert FakeMss.instances[-1].grabbed == [
        {"left": 512, "top": 0, "width": 256, "height": 144}
    ]
    assert (width, height) == (256, 144)
    assert downscale(width, height, raw) == FRAMES["left-red-right-blue"]


def test_open_again_sees_changed_monitors_and_closes_the_old_one() -> None:
    grabber = MssGrabber(ask_permission=False)
    grabber.open()
    first = FakeMss.instances[-1]
    FakeMss.monitors_to_report = FakeMss.monitors_to_report[:1]
    assert len(grabber.open()) == 1
    assert first.closed
    grabber.close()
    assert FakeMss.instances[-1].closed


def test_no_display_is_no_screen_access() -> None:
    FakeMss.fail_on_create = True
    with pytest.raises(NoScreenAccess, match="DISPLAY"):
        MssGrabber(ask_permission=False).open()


def test_a_failing_grab_is_a_capture_error() -> None:
    grabber = MssGrabber(ask_permission=False)
    screens = grabber.open()
    FakeMss.fail_on_grab = True
    with pytest.raises(CaptureError, match="BitBlt"):
        grabber.grab(screens[0])


def test_missing_macos_permission_is_no_screen_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("glowcast_screen.capture.sys.platform", "darwin")
    monkeypatch.setattr(
        "glowcast_screen.capture._macos_screen_access", lambda ask: False
    )
    with pytest.raises(NoScreenAccess, match="Screen Recording"):
        MssGrabber(ask_permission=False).open()
    assert FakeMss.instances == []
