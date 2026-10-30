"""The protocol through a real aiohttp test client: pairing, 401, frame, info."""

from __future__ import annotations

import asyncio
import io
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from aiohttp.test_utils import TestClient
from fakes import FakeClock, FakeGrabber, FakeSleep
from glowcast_screen.capture import CaptureError, ScreenCapture
from glowcast_screen.events import Events
from glowcast_screen.patterns import FRAMES, PatternSource
from glowcast_screen.server import HelperServer, PatternPicture, Picture, ScreenPicture
from glowcast_screen.store import Store

CODE = "123456"

ClientFactory = Callable[..., Awaitable[TestClient]]


class Helper:
    def __init__(
        self, server: HelperServer, client: TestClient, out: io.StringIO
    ) -> None:
        self.server = server
        self.client = client
        self.out = out

    def events(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.out.getvalue().splitlines()]

    def names(self) -> list[str]:
        return [e["event"] for e in self.events()]

    async def pair(self) -> str:
        resp = await self.client.post("/v1/pair", json={"code": CODE})
        assert resp.status == 200
        return (await resp.json())["token"]


MakeHelper = Callable[..., Awaitable[Helper]]


@pytest.fixture
def make_helper(
    aiohttp_client: ClientFactory, config_dir: Path
) -> Callable[..., Awaitable[Helper]]:
    async def make(
        picture: Picture | None = None, code: str | None = CODE, **kwargs: Any
    ) -> Helper:
        store = Store(config_dir)
        store.load()
        out = io.StringIO()
        server = HelperServer(
            store=store,
            picture=picture or PatternPicture(PatternSource("red", FakeClock())),
            events=Events(json_mode=True, out=out),
            name="Test PC",
            pairing_code=code,
            **{"pair_delay": 0, **kwargs},
        )
        return Helper(server, await aiohttp_client(server.app), out)

    return make


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_pairing_with_the_right_code_returns_a_token(
    make_helper: MakeHelper,
) -> None:
    helper = await make_helper()
    resp = await helper.client.post("/v1/pair", json={"code": CODE})
    assert resp.status == 200
    body = await resp.json()
    assert body["id"] == helper.server.store.helper_id
    assert len(body["token"]) >= 32
    resp = await helper.client.get("/v1/info", headers=auth(body["token"]))
    assert resp.status == 200
    # The event says who paired, never the token.
    assert helper.names() == ["paired", "client_connected"]
    assert body["token"] not in helper.out.getvalue()


async def test_a_wrong_code_is_refused_slowly(make_helper: MakeHelper) -> None:
    helper = await make_helper(pair_delay=0.2)
    loop = asyncio.get_running_loop()
    started = loop.time()
    resp = await helper.client.post("/v1/pair", json={"code": "654321"})
    assert resp.status == 403
    assert (await resp.json()) == {"error": "wrong_code"}
    assert loop.time() - started >= 0.2
    assert helper.server.store.paired == 0
    assert helper.names() == ["pairing_failed"]
    assert "654321" not in helper.out.getvalue()


@pytest.mark.parametrize("body", [b"not json", b"{}", b'{"code": 123456}'])
async def test_a_bad_pairing_request(make_helper: MakeHelper, body: bytes) -> None:
    helper = await make_helper()
    resp = await helper.client.post("/v1/pair", data=body)
    assert resp.status == 400


async def test_without_a_pairing_code_nobody_can_pair(make_helper: MakeHelper) -> None:
    helper = await make_helper(code=None)
    resp = await helper.client.post("/v1/pair", json={"code": CODE})
    assert resp.status == 403
    assert (await resp.json()) == {"error": "pairing_unavailable"}


@pytest.mark.parametrize("path", ["/v1/frame", "/v1/info", "/v1/other"])
@pytest.mark.parametrize(
    "headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": "Basic abc"}]
)
async def test_everything_but_pairing_needs_a_valid_token(
    make_helper: MakeHelper, path: str, headers: dict[str, str]
) -> None:
    helper = await make_helper()
    await helper.pair()
    resp = await helper.client.get(path, headers=headers)
    assert resp.status == 401
    assert resp.headers["WWW-Authenticate"] == "Bearer"


async def test_an_unpaired_token_gets_401(make_helper: MakeHelper) -> None:
    helper = await make_helper()
    token = await helper.pair()
    helper.server.store.unpair()
    resp = await helper.client.get("/v1/frame", headers=auth(token))
    assert resp.status == 401


async def test_frame_is_6912_bytes_of_rgb(make_helper: MakeHelper) -> None:
    source = PatternSource("left-red-right-blue", FakeClock())
    helper = await make_helper(PatternPicture(source))
    token = await helper.pair()
    resp = await helper.client.get("/v1/frame", headers=auth(token))
    assert resp.status == 200
    assert resp.content_type == "application/octet-stream"
    body = await resp.read()
    assert len(body) == 6912
    assert body[:3] == bytes((255, 0, 0))  # top left: red
    assert body[31 * 3 : 32 * 3] == bytes((255, 0, 0))  # x=31: still red
    assert body[32 * 3 : 33 * 3] == bytes((0, 0, 255))  # x=32: blue
    assert body[-3:] == bytes((0, 0, 255))  # bottom right


async def test_info_in_pattern_mode_follows_the_cycle(make_helper: MakeHelper) -> None:
    clock = FakeClock()
    source = PatternSource("cycle", clock, interval=5)
    helper = await make_helper(PatternPicture(source))
    token = await helper.pair()

    info = await (await helper.client.get("/v1/info", headers=auth(token))).json()
    assert info == {
        "id": helper.server.store.helper_id,
        "name": "Test PC",
        "protocol": 1,
        "version": "0.1.0",
        "width": 64,
        "height": 36,
        "screens": [],
        "screen": None,
        "pattern": {
            "name": "cycle",
            "showing": "red",
            "since": "2027-01-15T08:00:00.000Z",
            "interval": 5.0,
        },
        "no_picture": None,
    }

    clock.now += 12.5
    info = await (await helper.client.get("/v1/info", headers=auth(token))).json()
    assert info["pattern"]["showing"] == "blue"
    assert info["pattern"]["since"] == "2027-01-15T08:00:10.000Z"
    frame = await (await helper.client.get("/v1/frame", headers=auth(token))).read()
    assert frame == FRAMES["blue"]


async def test_info_in_screen_mode_lists_the_screens(make_helper: MakeHelper) -> None:
    capture = ScreenCapture(FakeGrabber(), 2)
    await capture.start()
    helper = await make_helper(ScreenPicture(capture))
    token = await helper.pair()
    info = await (await helper.client.get("/v1/info", headers=auth(token))).json()
    assert info["screens"] == [
        {"index": 1, "width": 512, "height": 216, "left": 0, "top": 0, "primary": True},
        {
            "index": 2,
            "width": 256,
            "height": 144,
            "left": 512,
            "top": 0,
            "primary": False,
        },
    ]
    assert info["screen"] == 2
    assert info["pattern"] is None
    await capture.close()


async def test_no_picture_is_204_reported_once_and_recovers(
    make_helper: MakeHelper,
) -> None:
    clock = FakeClock()
    grabber = FakeGrabber()
    capture = ScreenCapture(grabber, None, clock=clock)
    await capture.start()
    helper = await make_helper(ScreenPicture(capture))
    token = await helper.pair()

    grabber.fail = CaptureError("screen locked")
    for _ in range(3):
        clock.now += 1
        resp = await helper.client.get("/v1/frame", headers=auth(token))
        assert resp.status == 204
        assert await resp.read() == b""
    info = await (await helper.client.get("/v1/info", headers=auth(token))).json()
    assert info["no_picture"] == "capture_failed"

    grabber.fail = None
    clock.now += 1
    resp = await helper.client.get("/v1/frame", headers=auth(token))
    assert resp.status == 200
    assert await resp.read() == FRAMES["red"]

    events = helper.events()
    assert [e["event"] for e in events] == [
        "paired",
        "client_connected",
        "no_picture",
        "picture",
    ]
    assert events[2]["reason"] == "capture_failed"
    await capture.close()


async def test_frames_per_second_with_capture_times(make_helper: MakeHelper) -> None:
    clock = FakeClock()
    capture = ScreenCapture(FakeGrabber(), None, clock=clock)
    await capture.start()
    capture.take_capture_ms()
    helper = await make_helper(ScreenPicture(capture), stats_interval=0.2)
    token = await helper.pair()
    helper.server.start_reporting()
    for _ in range(3):
        clock.now += 1
        await helper.client.get("/v1/frame", headers=auth(token))
    await asyncio.sleep(0.3)
    await helper.server.stop_reporting()

    frames = [e for e in helper.events() if e["event"] == "frames"]
    # 3 frames in one or two windows of 0.2 s; quiet windows give no event.
    assert 1 <= len(frames) <= 2
    assert round(sum(f["fps"] for f in frames) * 0.2) == 3
    assert all(set(f["capture_ms"]) == {"median", "max"} for f in frames)
    await capture.close()


async def test_cycle_reports_each_pattern_change(make_helper: MakeHelper) -> None:
    clock = FakeClock()
    source = PatternSource("cycle", clock, interval=0.1)
    sleep = FakeSleep(clock, wakes=3)
    helper = await make_helper(PatternPicture(source), pattern_sleep=sleep)
    helper.server.start_reporting()
    await sleep.done.wait()
    await helper.server.stop_reporting()
    patterns = [e for e in helper.events() if e["event"] == "pattern"]
    assert [p["showing"] for p in patterns] == ["red", "green", "blue", "white"]
    assert all(p["since"].endswith("Z") for p in patterns)


async def test_cycle_never_reports_a_pattern_twice(make_helper: MakeHelper) -> None:
    # The reporter wakes 5 ms before each change and must not report the old
    # pattern again.
    clock = FakeClock()
    source = PatternSource("cycle", clock, interval=0.1)
    sleep = FakeSleep(clock, wakes=6, early=0.005)
    helper = await make_helper(PatternPicture(source), pattern_sleep=sleep)
    helper.server.start_reporting()
    await sleep.done.wait()
    await helper.server.stop_reporting()
    shown = [e["showing"] for e in helper.events() if e["event"] == "pattern"]
    assert sleep.early_wakes == 3
    assert shown == ["red", "green", "blue", "white"]
