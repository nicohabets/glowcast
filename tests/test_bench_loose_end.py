"""The test bench's loose lights at the end of a run and the proxies' rules.

No hardware: run.py's end check and cleanup on fakes, and the proxy's
allow-list (scripts/bench/stubs/proxy.py) with a fake HTTP session.
"""

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from .test_bench_loose import BENCH, _bench, _load, _on, proxy, rp

run = _load("bench_run_for_loose", BENCH / "run.py")
END = "end: loose lights set back explicitly, state as before the run"
LABEL = "light 3 (state) state as before the run"


class FailingOwner:
    """The owner's HA at the end of a run: light calls work, reads fail."""

    async def call(self, domain: str, service: str, data: dict[str, Any]) -> None:
        pass

    async def state(self, entity_id: str) -> dict[str, Any]:
        raise TimeoutError


async def test_a_state_that_cannot_be_read_is_not_verified() -> None:
    bench = _bench()
    bench.owner = FailingOwner()
    ok, numbers = await run._loose_restored(bench, within=0.0)
    assert not ok
    assert numbers == {LABEL: "not verified"}


async def test_the_end_step_is_there_without_the_camera() -> None:
    bench = _bench()
    bench.owner = FailingOwner()
    bench.th = rp.Thresholds(restore_within_s=0.0)
    bench.pre_states["light.desk_lamp"]["entity_id"] = "light.desk_lamp"
    bench.calibrated = False  # the camera check is skipped; the state check not
    await run._restore(bench)
    steps = {step.name: step for step in bench.report.steps}
    assert steps[END].result == rp.FAIL
    assert steps[END].numbers[LABEL] == "not verified"
    camera = steps["end: lights set back explicitly and seen back (camera)"]
    assert camera.result == rp.SKIP


async def test_the_end_check_waits_for_the_state() -> None:
    bench = _bench()
    late = iter([_on(brightness=10, color_mode="xy", xy_color=[0.6, 0.3])])

    class LateOwner:
        async def state(self, entity_id: str) -> dict[str, Any]:
            return next(late, bench.pre_states["light.desk_lamp"])

    bench.owner = LateOwner()
    ok, numbers = await run._loose_restored(bench, within=1.0, every=0.01)
    assert ok
    assert numbers[LABEL] == "yes"


class Client:
    def __init__(self, fails: bool) -> None:
        self.fails = fails
        self.closed = False
        self.entities = {"switch": "switch.bench"}

    async def call(self, *args: Any) -> None:
        pass

    async def close(self) -> None:
        self.closed = True
        if self.fails:
            raise RuntimeError("close")


async def _nothing() -> None:
    return None


async def test_a_failing_close_never_leaves_another_connection_open() -> None:
    ha, owner, bridge = Client(True), Client(True), Client(False)
    bench = SimpleNamespace(
        report=rp.Report(suite="loose", modes=["pattern"], started="-"),
        ha_start=None,
        ha=ha,
        screen=None,
        camera=None,
        pre_states={},
        tv_off_at=None,
        owner=owner,
        bridge=bridge,
        stop_helper=_nothing,
    )
    await run.cleanup(bench, {"bench_area_id": "a", "home_assistant": {}}, None)
    assert ha.closed and owner.closed and bridge.closed


class FakeResponse:
    async def __aenter__(self) -> "FakeResponse":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    async def json(self) -> dict[str, Any]:
        return {"state": "on"}


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(("GET", url))
        return FakeResponse()

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(("POST", url))
        return FakeResponse()


async def test_the_proxy_reads_and_calls_only_its_own_lights(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession()
    monkeypatch.setattr(proxy, "async_get_clientsession", lambda hass: session)
    owner = proxy.Owner(None, "http://owner.invalid/", "token", ["light.desk_lamp"])
    assert (await owner.state("light.desk_lamp"))["state"] == "on"
    await owner.call("turn_on", {"entity_id": "light.desk_lamp", "brightness": 1})
    await owner.call("turn_off", {"entity_id": "light.desk_lamp"})
    for refused in (
        owner.state("light.other"),
        owner.call("turn_on", {"entity_id": "light.other"}),
        owner.call("toggle", {"entity_id": "light.desk_lamp"}),
        owner.call("turn_on", {"entity_id": ["light.desk_lamp", "light.other"]}),
    ):
        with pytest.raises(proxy.HomeAssistantError):
            await refused
    assert session.calls == [
        ("GET", "http://owner.invalid/api/states/light.desk_lamp"),
        ("POST", "http://owner.invalid/api/services/light/turn_on"),
        ("POST", "http://owner.invalid/api/services/light/turn_off"),
    ]


async def test_the_proxies_keep_polling_after_an_unexpected_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refreshed: list[int] = []
    third = asyncio.Event()

    async def refresh(self: Any) -> None:
        refreshed.append(1)
        if len(refreshed) == 3:
            third.set()
        if len(refreshed) == 1:
            raise RuntimeError("unexpected")

    monkeypatch.setattr(proxy.ProxyLight, "async_refresh", refresh)
    monkeypatch.setattr(proxy, "POLL_SECONDS", 0)
    tasks: list[Any] = []
    light = {"unique_id": "bench-proxy-1", "name": "Bench proxy 1", "source": "light.x"}
    entry = SimpleNamespace(
        runtime_data=(None, {"light.x": {"state": "off", "attributes": {}}}),
        data={proxy.PROXY: {"lights": [light]}},
        async_create_background_task=lambda hass, coro, name: tasks.append(coro),
    )
    await proxy.async_setup_lights(None, entry, lambda lights: None)
    (poll,) = tasks
    task = asyncio.ensure_future(poll)
    await asyncio.wait_for(third.wait(), 2)
    task.cancel()
    assert len(refreshed) >= 3  # the error did not end the poll


def test_the_network_window_is_glowcasts_bound() -> None:
    # Five commands spread over 1.0 s are within Glowcast's bound (4 in any
    # 0.99 s); five within 0.98 s are not.
    def judge(times: list[float]) -> Any:
        sent = {f"light.p{i}": [(t, 1.25)] for i, t in enumerate(times)}
        label = {e: f"light {i}" for i, e in enumerate(sent, 1)}
        return run_loose().judge_rate(
            sent,
            dict.fromkeys(sent, "hue:a"),
            label,
            {"hue:a": "Hue network"},
            slack=0.15,
        )

    assert judge([0.0, 0.25, 0.5, 0.75, 1.0]).ok
    assert not judge([0.0, 0.245, 0.49, 0.735, 0.98]).ok


def run_loose() -> Any:
    from .test_bench_loose import loose

    return loose
