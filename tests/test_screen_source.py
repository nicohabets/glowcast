"""Tests for the screen source, against a fake screen helper.

The fake helper is a real small aiohttp server on 127.0.0.1 that answers as
the helper's protocol version 1 does (architecture 8.3): fixed frames, "no
picture" (204), a rejected token (401) and slow answers.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from homeassistant.core import HomeAssistant

from custom_components.glowcast.core.models import Frame
from custom_components.glowcast.core.ports import SourceAuthFailed, SourceUnavailable
from custom_components.glowcast.sources import SOURCES, screen
from custom_components.glowcast.sources.screen import ScreenFrameSource

TOKEN = "Zq3vX8pL1mN7cR4tY9wK2bF6hJ0dS5aG"
HELPER_ID = "4f0c9a1b2c3d4e5f6a7b8c9d0e1f2a3b"
RED = bytes((255, 0, 0)) * (64 * 36)
BLUE = bytes((0, 0, 255)) * (64 * 36)


class FakeHelper:
    """Answers /v1/info and /v1/frame; `frames` scripts the frame answers.

    Each item is bytes (a 200 with that body), an HTTP status (204, 401, 500)
    or "slow" (an answer after `slow` seconds). After the script, `default`.
    """

    def __init__(self) -> None:
        self.frames: list[Any] = []
        self.default: Any = RED
        self.info_status: int | str = 200  # or "slow"
        # The /v1/info answer; None answers with a page that is not JSON.
        self.info: Any = {"id": HELPER_ID, "protocol": 1}
        self.slow = 10.0
        self.frame_requests = 0
        self.authorization: list[str | None] = []
        # The /v1/pair answer: (status, body); body None is a page, not JSON.
        self.pair_answer: tuple[int, Any] = (200, {"token": TOKEN, "id": HELPER_ID})
        self.pair_bodies: list[Any] = []
        app = web.Application()
        app.router.add_post("/v1/pair", self._pair)
        app.router.add_get("/v1/info", self._info)
        app.router.add_get("/v1/frame", self._frame)
        self.server = TestServer(app, host="127.0.0.1")

    @property
    def port(self) -> int:
        assert self.server.port is not None
        return self.server.port

    async def _pair(self, request: web.Request) -> web.Response:
        self.pair_bodies.append(await request.json())
        status, body = self.pair_answer
        if body is None:
            return web.Response(status=status, text="<html>not a helper</html>")
        return web.json_response(body, status=status)

    async def _info(self, request: web.Request) -> web.Response:
        self.authorization.append(request.headers.get("Authorization"))
        if self.info_status == "slow":
            await asyncio.sleep(self.slow)
        elif self.info_status != 200:
            return web.Response(status=self.info_status)
        if self.info is None:
            return web.Response(text="<html>not a helper</html>")
        return web.json_response(self.info)

    async def _frame(self, request: web.Request) -> web.StreamResponse:
        self.frame_requests += 1
        self.authorization.append(request.headers.get("Authorization"))
        item = self.frames.pop(0) if self.frames else self.default
        if item == "slow":
            await asyncio.sleep(self.slow)
            item = RED
        if isinstance(item, int):
            return web.Response(status=item)
        return web.Response(body=item, content_type="application/octet-stream")


@pytest.fixture
async def helper(socket_enabled: None) -> AsyncIterator[FakeHelper]:
    """The fake helper, on a free port; Home Assistant's tests block sockets
    unless `socket_enabled` is asked for (127.0.0.1 only)."""
    fake = FakeHelper()
    await fake.server.start_server()
    yield fake
    await fake.server.close()


@pytest.fixture(autouse=True)
def short_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    """The same rules, in tenths of the time."""
    monkeypatch.setattr(screen, "REQUEST_TIMEOUT", 0.2)
    monkeypatch.setattr(screen, "CAPTURE_TIMEOUT", 0.5)
    monkeypatch.setattr(screen, "RETRY_DELAY", 0.01)


def source_for(hass: HomeAssistant, helper: FakeHelper) -> ScreenFrameSource:
    """The source as the registry builds it from an entry's data."""
    data = {
        "source": {
            "type": "screen",
            "helper_id": HELPER_ID,
            "host": "127.0.0.1",
            "port": helper.port,
            "token": TOKEN,
        }
    }
    source = SOURCES["screen"].create(hass, data, {})
    assert isinstance(source, ScreenFrameSource)
    return source


async def test_frames_come_from_the_helper(
    hass: HomeAssistant, helper: FakeHelper
) -> None:
    source = source_for(hass, helper)
    assert not source.is_on()
    await source.start()
    assert source.is_on()
    assert await source.capture() == Frame(width=64, height=36, pixels=RED)
    helper.default = BLUE
    assert (await source.capture()).pixels == BLUE
    assert helper.authorization == [f"Bearer {TOKEN}"] * 3
    await source.close()
    await source.close()  # safe twice
    assert not source.is_on()


async def test_the_registry(hass: HomeAssistant) -> None:
    """Its own device "Glowcast <computer name>", linked to nothing (#110)."""
    screen_type = SOURCES["screen"]
    data = {"source": {"type": "screen", "helper_id": HELPER_ID}}
    assert screen_type.is_missing(hass, data, {}) is False
    assert screen_type.device_identifier(data) is None
    assert screen_type.device_id(hass, data) is None
    assert screen_type.missing is None
    # Its setup and reauth steps (#109).
    assert screen_type.setup_step == "screen"
    assert screen_type.reauth_step == "screen_reauth"
    assert ScreenFrameSource.max_rate_hz == 4.0


async def test_one_no_picture_is_asked_again(
    hass: HomeAssistant, helper: FakeHelper
) -> None:
    source = source_for(hass, helper)
    await source.start()
    helper.frames = [204]
    assert (await source.capture()).pixels == RED
    assert source.is_on()
    assert helper.frame_requests == 2


@pytest.mark.parametrize("answer", [204, 500, b"\x00" * 100])
async def test_no_picture_twice_turns_the_source_off(
    hass: HomeAssistant, helper: FakeHelper, answer: Any
) -> None:
    """Locked, asleep or broken: the pipeline then stops with TV_OFF."""
    source = source_for(hass, helper)
    await source.start()
    await source.capture()
    helper.frames = [answer, answer]
    with pytest.raises(SourceUnavailable):
        await source.capture()
    assert not source.is_on()
    assert helper.frame_requests == 3
    with pytest.raises(SourceUnavailable):
        await source.capture()  # off: not asked again
    assert helper.frame_requests == 3


async def test_no_picture_before_the_first_frame_keeps_it_on(
    hass: HomeAssistant, helper: FakeHelper
) -> None:
    """The startup grace asks again while the computer wakes or unlocks; if
    no frame comes within it, the pipeline stops with TV_OFF."""
    source = source_for(hass, helper)
    await source.start()
    helper.frames = [204, 204]
    with pytest.raises(SourceUnavailable):
        await source.capture()
    assert source.is_on()
    assert (await source.capture()).pixels == RED


async def test_a_new_start_after_no_picture(
    hass: HomeAssistant, helper: FakeHelper
) -> None:
    source = source_for(hass, helper)
    await source.start()
    await source.capture()
    helper.frames = [204, 204]
    with pytest.raises(SourceUnavailable):
        await source.capture()
    assert not source.is_on()
    await source.start()
    assert source.is_on()
    assert (await source.capture()).pixels == RED


async def test_unreachable_at_start(hass: HomeAssistant, helper: FakeHelper) -> None:
    source = source_for(hass, helper)
    await helper.server.close()
    with pytest.raises(SourceUnavailable):
        await source.start()
    assert not source.is_on()


async def test_unreachable_while_syncing(
    hass: HomeAssistant, helper: FakeHelper
) -> None:
    source = source_for(hass, helper)
    await source.start()
    await source.capture()
    await helper.server.close()
    with pytest.raises(SourceUnavailable):
        await source.capture()
    assert not source.is_on()


async def test_an_unexpected_answer_at_start(
    hass: HomeAssistant, helper: FakeHelper
) -> None:
    helper.info_status = 500
    source = source_for(hass, helper)
    with pytest.raises(SourceUnavailable):
        await source.start()


@pytest.mark.parametrize(
    "info",
    [
        {"id": "0123456789abcdef0123456789abcdef", "protocol": 1},
        {"id": HELPER_ID, "protocol": 2},
        {"id": HELPER_ID},
        ["not", "an", "object"],
        None,
    ],
    ids=["another helper", "another protocol", "no protocol", "a list", "not json"],
)
async def test_frames_only_from_glowcasts_own_helper(
    hass: HomeAssistant, helper: FakeHelper, info: Any
) -> None:
    """Another helper or service at the stored address is never asked for
    frames (#110)."""
    helper.info = info
    source = source_for(hass, helper)
    with pytest.raises(SourceUnavailable):
        await source.start()
    assert not source.is_on()
    with pytest.raises(SourceUnavailable):
        await source.capture()
    assert helper.frame_requests == 0


async def test_an_oversized_frame_is_not_a_picture(
    hass: HomeAssistant, helper: FakeHelper
) -> None:
    source = source_for(hass, helper)
    await source.start()
    helper.frames = [RED + b"\x00", RED + RED]
    with pytest.raises(SourceUnavailable):
        await source.capture()
    assert helper.frame_requests == 2


async def test_a_rejected_token_at_start(
    hass: HomeAssistant, helper: FakeHelper
) -> None:
    helper.info_status = 401
    source = source_for(hass, helper)
    with pytest.raises(SourceAuthFailed):
        await source.start()
    assert not source.is_on()


async def test_a_rejected_token_while_syncing(
    hass: HomeAssistant, helper: FakeHelper
) -> None:
    """Pair again: no second request, no "no picture"."""
    source = source_for(hass, helper)
    await source.start()
    helper.frames = [401]
    with pytest.raises(SourceAuthFailed):
        await source.capture()
    assert helper.frame_requests == 1


async def test_a_slow_answer_is_asked_again(
    hass: HomeAssistant, helper: FakeHelper
) -> None:
    source = source_for(hass, helper)
    await source.start()
    helper.frames = ["slow"]
    assert (await source.capture()).pixels == RED
    assert source.is_on()


async def test_a_capture_that_hangs_ends_within_the_timeout(
    hass: HomeAssistant, helper: FakeHelper
) -> None:
    source = source_for(hass, helper)
    await source.start()
    await source.capture()
    helper.default = "slow"
    loop = asyncio.get_running_loop()
    began = loop.time()
    with pytest.raises(SourceUnavailable):
        await source.capture()
    assert loop.time() - began < screen.CAPTURE_TIMEOUT
    assert not source.is_on()


async def test_a_start_that_hangs(hass: HomeAssistant, helper: FakeHelper) -> None:
    helper.info_status = "slow"
    source = source_for(hass, helper)
    with pytest.raises(SourceUnavailable):
        await source.start()


async def test_the_token_and_frames_are_never_logged(
    hass: HomeAssistant, helper: FakeHelper, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    source = source_for(hass, helper)
    await source.start()
    helper.frames = [b"\x01" * 50, 204, 401]
    with pytest.raises(SourceUnavailable):
        await source.capture()
    await source.start()
    with pytest.raises(SourceAuthFailed) as err:
        await source.capture()
    text = caplog.text + str(err.value)
    assert TOKEN not in text
    assert "\\x01" not in text


# Pairing and the helper's info, for the setup and reauth steps (#109).


async def test_pairing_returns_the_token_and_the_id(
    hass: HomeAssistant, helper: FakeHelper, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    assert await screen.async_pair(hass, "127.0.0.1", helper.port, "123456") == (
        TOKEN,
        HELPER_ID,
    )
    assert helper.pair_bodies == [{"code": "123456"}]
    assert TOKEN not in caplog.text
    assert "123456" not in caplog.text


@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        ((403, {"error": "wrong_code"}), "wrong_code"),
        ((403, {"error": "pairing_unavailable"}), "pairing_unavailable"),
        ((400, {"error": "bad_request"}), "wrong_code"),
    ],
)
async def test_a_refused_code(
    hass: HomeAssistant, helper: FakeHelper, answer: tuple[int, Any], reason: str
) -> None:
    helper.pair_answer = answer
    with pytest.raises(screen.PairingRefused) as err:
        await screen.async_pair(hass, "127.0.0.1", helper.port, "654321")
    assert err.value.reason == reason


@pytest.mark.parametrize(
    "answer",
    [(200, None), (200, {"token": TOKEN}), (500, {"error": "oops"}), (403, None)],
)
async def test_an_unexpected_pairing_answer(
    hass: HomeAssistant, helper: FakeHelper, answer: tuple[int, Any]
) -> None:
    helper.pair_answer = answer
    with pytest.raises(SourceUnavailable):
        await screen.async_pair(hass, "127.0.0.1", helper.port, "123456")


async def test_pairing_with_an_unreachable_helper(
    hass: HomeAssistant, helper: FakeHelper
) -> None:
    port = helper.port
    await helper.server.close()
    with pytest.raises(SourceUnavailable):
        await screen.async_pair(hass, "127.0.0.1", port, "123456")
    assert not await screen.async_reachable(hass, "127.0.0.1", port)


async def test_the_info_with_the_token(hass: HomeAssistant, helper: FakeHelper) -> None:
    assert await screen.async_reachable(hass, "127.0.0.1", helper.port)
    info = await screen.async_info(hass, "127.0.0.1", helper.port, TOKEN)
    assert info == {"id": HELPER_ID, "protocol": 1}
    assert helper.authorization[-1] == f"Bearer {TOKEN}"

    helper.info_status = 401
    with pytest.raises(SourceAuthFailed):
        await screen.async_info(hass, "127.0.0.1", helper.port, TOKEN)
    helper.info_status = 200
    helper.info = None
    with pytest.raises(SourceUnavailable):
        await screen.async_info(hass, "127.0.0.1", helper.port, TOKEN)


async def test_an_address_that_is_not_a_host_is_not_reachable(
    hass: HomeAssistant,
) -> None:
    assert not await screen.async_reachable(hass, "not a host/", 8488)
