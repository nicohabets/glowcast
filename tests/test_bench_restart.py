"""The test bench's restart suite (#183): Glowcast continues after Home
Assistant restarts.

No hardware and no Home Assistant started: the restart of the local HA with
the demo's helpers replaced (same config, same command, a token through the
login flow), the graceful stop, the switch's stored `resume`, and the
suite's judgements and steps on a fake bench with scripted answers.
"""

import contextlib
import json
import signal
import time
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from .test_bench_loose import BENCH, CAMERA, REPO, _load, local_ha, rp, suite

restart_suite = _load("bench_restart_suite", BENCH / "restart_suite.py")
run = _load("bench_run_for_restart", BENCH / "run.py")
demo = local_ha.demo

SWITCH = "switch.glowcast_bench"


# ---- the restart of the local HA ---------------------------------------------
def _instance(tmp_path: Path) -> Any:
    home = tmp_path / "instance"
    (home / "config" / ".storage").mkdir(parents=True)
    (home / "run").mkdir()
    (home / "state.json").write_text(json.dumps({"ha_port": 8299, "bridge_port": 0}))
    return demo.Instance("bench", home)


def _restore_state(config: Path, items: list[dict[str, Any]]) -> None:
    (config / ".storage" / "core.restore_state").write_text(
        json.dumps({"version": 1, "key": "core.restore_state", "data": items})
    )


def test_the_stored_resume_of_the_switch(tmp_path: Path) -> None:
    inst = _instance(tmp_path)
    assert local_ha.stored_resume(inst.config, SWITCH) is None  # no file
    _restore_state(
        inst.config,
        [
            {"state": {"entity_id": "sensor.other"}, "extra_data": {"resume": True}},
            {"state": {"entity_id": SWITCH}, "extra_data": {"resume": True}},
        ],
    )
    assert local_ha.stored_resume(inst.config, SWITCH) is True
    _restore_state(
        inst.config, [{"state": {"entity_id": SWITCH}, "extra_data": {"resume": False}}]
    )
    assert local_ha.stored_resume(inst.config, SWITCH) is False
    _restore_state(inst.config, [{"state": {"entity_id": SWITCH}, "extra_data": None}])
    assert local_ha.stored_resume(inst.config, SWITCH) is None
    (inst.config / ".storage" / "core.restore_state").write_text("{broken")
    assert local_ha.stored_resume(inst.config, SWITCH) is None


def _processes(
    monkeypatch: pytest.MonkeyPatch, inst: Any, gone_after: int | None
) -> list[int]:
    """HA's pid until `gone_after` reads (None: never gone); the signals sent."""
    (inst.run / "homeassistant.pid").write_text("4242")
    reads = {"n": 0}
    signals: list[int] = []

    def pid(instance: Any, name: str) -> int | None:
        assert name == "homeassistant"
        reads["n"] += 1
        return None if gone_after is not None and reads["n"] > gone_after else 4242

    def kill(pid: int, sig: int) -> None:
        assert pid == 4242
        signals.append(sig)

    monkeypatch.setattr(demo, "_pid", pid)
    monkeypatch.setattr(local_ha.os, "kill", kill)
    return signals


def test_a_graceful_stop_waits_for_ha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inst = _instance(tmp_path)
    signals = _processes(monkeypatch, inst, gone_after=3)
    seconds, graceful = local_ha.stop_gracefully(inst, within=60, poll=0)
    assert graceful and seconds >= 0
    assert signals == [signal.SIGTERM]  # never killed
    assert not (inst.run / "homeassistant.pid").exists()


def test_a_stop_that_hangs_is_killed_and_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inst = _instance(tmp_path)
    signals = _processes(monkeypatch, inst, gone_after=None)
    _, graceful = local_ha.stop_gracefully(inst, within=0, poll=0)
    assert not graceful
    assert signals == [signal.SIGTERM, signal.SIGKILL]


def test_a_stop_of_no_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    inst = _instance(tmp_path)
    signals = _processes(monkeypatch, inst, gone_after=0)
    assert local_ha.stop_gracefully(inst) == (0.0, False)
    assert signals == []


def test_the_restart_keeps_the_config_and_logs_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same config, same command; no fresh config, no onboarding; the switch's
    resume is read between the stop and the start."""
    inst = _instance(tmp_path)
    _restore_state(
        inst.config, [{"state": {"entity_id": SWITCH}, "extra_data": {"resume": True}}]
    )
    order: list[str] = []

    def never(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("not on a restart")

    def stop(instance: Any) -> tuple[float, bool]:
        order.append("stop")
        return 3.5, True

    def spawn(instance: Any, name: str, args: list[str]) -> None:
        # The restore state is read before HA writes it again.
        assert local_ha.stored_resume(instance.config, SWITCH) is True
        order.append("spawn")
        assert name == "homeassistant"
        assert args == local_ha.ha_args(instance)
        assert args[args.index("--config") + 1] == str(inst.config)

    monkeypatch.setattr(local_ha, "instance", lambda: inst)
    monkeypatch.setattr(local_ha, "stop_gracefully", stop)
    monkeypatch.setattr(demo, "_lock", contextlib.nullcontext)
    monkeypatch.setattr(demo, "_spawn", spawn)
    monkeypatch.setattr(demo, "_write_config", never)
    monkeypatch.setattr(demo, "_onboard", never)
    monkeypatch.setattr(demo, "_free_port", never)
    monkeypatch.setattr(local_ha, "_wait_for_http", lambda i: order.append("http"))
    monkeypatch.setattr(local_ha, "login", lambda i: "new-token")
    monkeypatch.setattr(
        demo, "_wait_until_running", lambda i, token: order.append(f"running {token}")
    )
    monkeypatch.setattr(local_ha, "_check_own", lambda i, token: order.append("own"))
    before = time.time()
    got, token, done = local_ha.restart(SWITCH)
    assert got is inst and token == "new-token"
    assert order == ["stop", "spawn", "http", "running new-token", "own"]
    assert (done.stop_s, done.graceful, done.resume) == (3.5, True, True)
    assert done.running_at >= before
    assert (inst.home.stat().st_mode & 0o777) == 0o700


def test_a_restart_that_fails_stops_ha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inst = _instance(tmp_path)
    stopped: list[bool] = []

    def refused(instance: Any) -> str:
        raise SystemExit("The local Home Assistant refused the bench's login")

    monkeypatch.setattr(local_ha, "instance", lambda: inst)
    monkeypatch.setattr(local_ha, "stop_gracefully", lambda i: (1.0, True))
    monkeypatch.setattr(demo, "_lock", contextlib.nullcontext)
    monkeypatch.setattr(demo, "_spawn", lambda *args: None)
    monkeypatch.setattr(local_ha, "_wait_for_http", lambda i: None)
    monkeypatch.setattr(local_ha, "login", refused)
    monkeypatch.setattr(local_ha, "stop", lambda: stopped.append(True))
    with pytest.raises(SystemExit):
        local_ha.restart(SWITCH)
    assert stopped == [True]


def test_no_restart_of_an_instance_never_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        local_ha, "instance", lambda: demo.Instance("bench", tmp_path / "none")
    )
    with pytest.raises(SystemExit, match="never started"):
        local_ha.restart(SWITCH)


def test_the_login_flow_gives_a_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inst = _instance(tmp_path)
    requests: list[tuple[str, str, Any, Any]] = []
    answers = [
        {"flow_id": "flow1", "type": "form"},
        {"type": "create_entry", "result": "code1"},
        {"access_token": "token1"},
    ]

    def request(
        instance: Any,
        method: str,
        path: str,
        body: Any = None,
        *,
        form: Any = None,
        token: Any = None,
    ) -> Any:
        requests.append((method, path, body, form))
        return answers.pop(0)

    monkeypatch.setattr(demo, "_request", request)
    assert local_ha.login(inst) == "token1"
    client = "http://127.0.0.1:8299/"
    assert requests == [
        (
            "POST",
            "/auth/login_flow",
            {
                "client_id": client,
                "handler": ["homeassistant", None],
                "redirect_uri": client,
            },
            None,
        ),
        (
            "POST",
            "/auth/login_flow/flow1",
            {"client_id": client, "username": demo.USERNAME, "password": demo.PASSWORD},
            None,
        ),
        (
            "POST",
            "/auth/token",
            None,
            {"grant_type": "authorization_code", "code": "code1", "client_id": client},
        ),
    ]
    answers[:] = [{"flow_id": "flow2"}, {"type": "form", "errors": {"base": "x"}}]
    with pytest.raises(SystemExit, match="refused"):
        local_ha.login(inst)


def test_the_wait_asks_a_page_an_onboarded_ha_has(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once onboarded, HA has no /api/onboarding; /auth/providers it has."""
    inst = _instance(tmp_path)
    paths: list[str] = []

    def request(instance: Any, method: str, path: str, *args: Any, **kw: Any) -> Any:
        paths.append(path)
        if len(paths) == 1:
            raise urllib.error.URLError("not yet")
        return {"providers": []}

    monkeypatch.setattr(demo, "_request", request)
    monkeypatch.setattr(demo, "_pid", lambda i, name: 4242)
    monkeypatch.setattr(local_ha.time, "sleep", lambda s: None)
    local_ha._wait_for_http(inst)
    assert paths == ["/auth/providers", "/auth/providers"]
    monkeypatch.setattr(demo, "_pid", lambda i, name: None)
    with pytest.raises(SystemExit, match="stopped"):
        local_ha._wait_for_http(inst)


# ---- the judgements ----------------------------------------------------------
ACTIVE = {"status": "active", "streamer": "app"}
INACTIVE = {"status": "inactive", "streamer": None}


def test_streaming_again_after_a_release() -> None:
    assert restart_suite.streamed_again([ACTIVE, INACTIVE], [ACTIVE, ACTIVE]) == (
        True,
        True,
        2,
    )
    # Never released: the old stream went on, Glowcast did not stop with HA.
    assert restart_suite.streamed_again([ACTIVE], [ACTIVE]) == (False, False, 1)
    assert not restart_suite.streamed_again([INACTIVE], [ACTIVE, INACTIVE])[0]
    assert not restart_suite.streamed_again(
        [INACTIVE], [ACTIVE, {**ACTIVE, "streamer": "other"}]
    )[0]
    assert not restart_suite.streamed_again([INACTIVE], [])[0]


def test_staying_off() -> None:
    assert restart_suite.stayed_off(["off", "off"], [INACTIVE, INACTIVE])
    assert not restart_suite.stayed_off(["off", "on"], [INACTIVE])
    assert not restart_suite.stayed_off(["off"], [INACTIVE, ACTIVE])
    assert not restart_suite.stayed_off([], [INACTIVE])
    assert not restart_suite.stayed_off(["off"], [])


def test_the_log_line_is_glowcasts() -> None:
    """The bench looks for the line runtime.py logs."""
    runtime = (REPO / "custom_components" / "glowcast" / "runtime.py").read_text()
    assert restart_suite.RESUMED in runtime
    line = "2026-10-30 INFO ... Glowcast Bench " + restart_suite.RESUMED
    assert restart_suite.logged_resume(line)
    assert not restart_suite.logged_resume("Glowcast Bench did not continue")


def test_the_suite_is_in_the_runner() -> None:
    assert "restart" in run.PATTERN_ONLY and run.PLAN["restart"]
    assert rp.Thresholds().resume_within_s == 45.0
    assert rp.Thresholds().stays_off_s == 30.0


# ---- the steps on a fake bench -----------------------------------------------
class ScriptedHA:
    """The local HA after the restart: the switch's state."""

    def __init__(self, switch: str, on_at: float | None = None) -> None:
        self.switch = switch
        self.on_at = on_at

    async def wait_state(
        self, key: str, states: set[str], within: float
    ) -> tuple[str, float] | None:
        assert key == "switch"
        return None if self.on_at is None else ("on", self.on_at)

    async def state(self, key: str) -> dict[str, Any]:
        return {"state": self.switch, "attributes": {}}


class ScriptedBridge:
    def __init__(self, samples: list[tuple[float, dict[str, Any]]]) -> None:
        self.samples = samples

    async def wait_status(self, area: str, status: str, within: float) -> float:
        return time.time()

    def statuses(self, start: float, end: float) -> list[dict[str, Any]]:
        return [s for t, s in self.samples if start <= t <= end]

    def first(self, status: str, after: float) -> float | None:
        return next(
            (t for t, s in self.samples if t >= after and s["status"] == status), None
        )


def _bench() -> Any:
    cfg = {
        "lights": CAMERA,
        "bench_area_id": "area-0000",
        "home_assistant": {"url": "http://192.0.2.10:8123", "tv_switch": "switch.tv"},
        "helper": {"port": 8489, "pairing_code": "123456"},
    }
    report = rp.Report(suite="restart", modes=["pattern"], started="-")
    return suite.Bench(cfg, "restart", report, None, {"host": "192.0.2.20"})


async def _no_wait(seconds: float) -> None:
    pass


def _log(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, text: str) -> None:
    path = tmp_path / "home-assistant.log"
    path.write_text(text)
    monkeypatch.setattr(restart_suite.local_ha, "log_path", lambda: path)


async def test_the_steps_of_a_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(restart_suite, "sleep", _no_wait)
    _log(monkeypatch, tmp_path, "INFO Glowcast Bench " + restart_suite.RESUMED)
    now = time.time()
    running = now - 20
    bench = _bench()
    bench.ha = ScriptedHA("on", on_at=running + 1.2)
    bench.bridge = ScriptedBridge(
        [
            (now - 40, ACTIVE),
            (now - 30, INACTIVE),  # Glowcast stopped with HA
            (running + 3.0, ACTIVE),
            *[(running + 3.0 + i, ACTIVE) for i in range(1, 10)],
        ]
    )
    done = restart_suite.local_ha.Restarted(4.0, True, True, running)
    restart_suite.stop_step(bench, done, True)
    await restart_suite.resumed(bench, done, now - 35)
    stop, on, streams, sides, log = bench.report.steps
    assert stop.result == rp.PASS and stop.numbers["stored resume"] == "yes"
    assert on.result == rp.PASS and on.seconds == 1.2
    assert streams.result == rp.PASS
    assert streams.numbers["HA running to streaming s"] == 3.0
    assert streams.numbers["released during the restart"] == "yes"
    assert sides.result == rp.SKIP  # no sides from a calibration here
    assert sides.name.startswith("after the restart")
    assert log.result == rp.PASS and log.numbers["line in the log"] == "yes"
    text = bench.report.to_markdown()
    assert not [v for v in bench.forbidden() if v and v in text]
    assert SWITCH not in text and "area-0000" not in text


async def test_no_resume_fails_the_steps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(restart_suite, "sleep", _no_wait)
    _log(monkeypatch, tmp_path, "INFO Glowcast Bench did not continue")
    now = time.time()
    bench = _bench()
    bench.ha = ScriptedHA("off")
    bench.bridge = ScriptedBridge([(now - 30, INACTIVE), (now - 1, INACTIVE)])
    done = restart_suite.local_ha.Restarted(90.0, False, None, now - 20)
    restart_suite.stop_step(bench, done, True)
    await restart_suite.resumed(bench, done, now - 35)
    stop, on, streams, log = bench.report.steps
    assert stop.result == rp.FAIL and stop.note == "killed after the stop limit"
    assert stop.numbers["stored resume"] == "not stored"
    assert on.result == rp.FAIL and on.seconds is None
    assert streams.result == rp.FAIL
    assert log.result == rp.FAIL and log.numbers["line in the log"] == "no"


async def test_off_at_the_stop_stays_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(restart_suite, "sleep", _no_wait)
    _log(monkeypatch, tmp_path, "INFO nothing about Glowcast")
    now = time.time()
    bench = _bench()
    bench.th = rp.Thresholds(stays_off_s=3.0)
    bench.ha = ScriptedHA("off")
    bench.bridge = ScriptedBridge([(now - 0.005, INACTIVE)] * 3)
    done = restart_suite.local_ha.Restarted(2.0, True, False, now - 0.01)
    restart_suite.stop_step(bench, done, False)
    await restart_suite.stays_off(bench, done)
    stop, off = bench.report.steps
    assert stop.result == rp.PASS and stop.numbers["stored resume"] == "no"
    assert off.result == rp.PASS, off.numbers
    assert off.numbers["switch reads"] == 3
    assert off.numbers["switch on seen"] == "no"


async def test_started_by_itself_when_off_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(restart_suite, "sleep", _no_wait)
    _log(monkeypatch, tmp_path, "INFO Glowcast Bench " + restart_suite.RESUMED)
    now = time.time()
    bench = _bench()
    bench.th = rp.Thresholds(stays_off_s=2.0)
    bench.ha = ScriptedHA("on")
    bench.bridge = ScriptedBridge([(now - 0.005, ACTIVE)])
    done = restart_suite.local_ha.Restarted(2.0, True, True, now - 0.01)
    restart_suite.stop_step(bench, done, False)
    await restart_suite.stays_off(bench, done)
    stop, off = bench.report.steps
    assert stop.result == rp.FAIL
    assert stop.note == "the switch's resume was not stored as expected"
    assert off.result == rp.FAIL
    assert off.numbers["switch on seen"] == "yes"
    assert off.numbers["area active seen"] == "yes"
    assert off.numbers["line in the log"] == "yes"


class FakeLocalHA:
    """bench.ha before the restart, and the client made after it."""

    def __init__(self, *args: Any) -> None:
        self.closed = False
        self.followed = False
        self.entities = {"switch": SWITCH}
        self.events: list[Any] = []
        self.calls: list[Any] = []

    async def close(self) -> None:
        self.closed = True

    async def find_entities(self, entry_id: str) -> dict[str, str]:
        return self.entities

    async def follow(self) -> None:
        self.followed = True


async def test_the_bench_follows_the_restarted_ha(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bench = _bench()
    old = FakeLocalHA()
    old.events.append((1.0, "switch", "on", {}))
    bench.ha = old
    done = restart_suite.local_ha.Restarted(2.0, True, True, time.time())
    asked: list[str] = []

    def restart(switch: str) -> Any:
        asked.append(switch)
        return SimpleNamespace(url="http://127.0.0.1:8299"), "new-token", done

    monkeypatch.setattr(restart_suite.local_ha, "restart", restart)
    monkeypatch.setattr(restart_suite.clients, "LocalHA", FakeLocalHA)
    assert await restart_suite.restart(bench) is done
    assert asked == [SWITCH] and old.closed
    assert bench.ha is not old and bench.ha.followed
    assert bench.ha.events is old.events  # the run's record goes on
    assert "new-token" in bench.tokens  # scrubbed from the report
    assert bench.ha_start is not None and bench.ha_start.done()

    def fails(switch: str) -> Any:
        raise SystemExit("Home Assistant did not start in time")

    monkeypatch.setattr(restart_suite.local_ha, "restart", fails)
    with pytest.raises(restart_suite.BenchError, match="did not start again"):
        await restart_suite.restart(bench)
