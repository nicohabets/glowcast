"""The end-to-end check: the screen helper's test patterns in, colours out.

Run by `demo.py e2e` (see scripts/README.md) against its own demo instance:
Home Assistant with Glowcast from the working copy, the stub hue integration,
and the fake bridge with its DTLS side, all on 127.0.0.1. This script starts
the screen helper with `--pattern cycle` on 127.0.0.1, sets Glowcast up
through the real setup flow (a computer screen by hand, then the Hue bridge),
switches it on and checks per pattern the colours the fake bridge decrypted
from the entertainment stream. Then the failure paths: the helper stops (the
switch turns off with tv_off and the lights are released), and the helper
forgets its pairings (reauth starts; pairing again makes it sync again).

It prints PASS or FAIL per step, writes a JSON summary (--summary) and exits
with 1 if a step failed. The access token comes in GLOWCAST_E2E_TOKEN.
"""

import argparse
import asyncio
import contextlib
import json
import os
import shutil
import signal
import ssl
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import aiohttp
from fake_bridge import AREAS, TV_AREA_ID, channel_position
from room_harness import load_core

REPO = Path(__file__).resolve().parent.parent.parent
HELPER_NAME = "Test PC"  # generic, never the computer's name
PAIRING_CODE = "123456"
# How long all-zero frames last before the pipeline releases the lights,
# from the working copy's core/ (loaded without Home Assistant).
NO_PICTURE_WAIT: float = load_core(
    REPO / "custom_components" / "glowcast" / "core", "glowcast_e2e_core"
).pipeline.NO_PICTURE_WAIT
# Seconds per pattern: long enough for the black pattern's hold and release
# (NO_PICTURE_WAIT) and the next pattern's claim, with a settled stretch in each.
INTERVAL = NO_PICTURE_WAIT + 3.0
# The release comes NO_PICTURE_WAIT after the first all-zero frame, which
# Glowcast gets up to a capture interval after black starts, plus the stop.
RELEASE_EARLY = 0.5
RELEASE_LATE = 2.0
# Colours are compared from this long after a pattern starts (the capture
# interval, the fade and the stream's latency), up to MARGIN before it ends.
SETTLE = 2.0
MARGIN = 0.5
TOLERANCE = 12  # 0-255
MIN_FRAMES = 20  # in a settled stretch; the stream runs at about 25 per second
CYCLE = [
    "red",
    "green",
    "blue",
    "white",
    "black",
    "left-red-right-blue",
    "letterbox",
]
TV_AREA = next(area for area in AREAS if area["id"] == TV_AREA_ID)
CHANNELS = [str(channel) for channel in range(TV_AREA["channels"])]


class Failed(Exception):
    """A step failed; the message says why."""


class Run:
    """The steps and their results."""

    def __init__(self) -> None:
        """No steps yet."""
        self.steps: list[dict[str, Any]] = []
        self.stopped = False  # a step the rest depends on failed

    def step(self, name: str, check: Callable[[], dict[str, Any] | None]) -> bool:
        """Run one check; record and print its result."""
        if self.stopped:
            self.steps.append({"name": name, "passed": None, "detail": "skipped"})
            print(f"SKIP {name}", flush=True)
            return False
        begin = time.monotonic()
        try:
            detail = check() or {}
            passed = True
        except Failed as failure:
            detail, passed = {"error": str(failure)}, False
        except Exception as error:  # noqa: BLE001 - reported as a failed step
            detail, passed = {"error": f"{type(error).__name__}: {error}"}, False
        self.steps.append(
            {
                "name": name,
                "passed": passed,
                "seconds": round(time.monotonic() - begin, 1),
                "detail": detail,
            }
        )
        print(
            f"{'PASS' if passed else 'FAIL'} {name}: {json.dumps(detail)}", flush=True
        )
        return passed

    def required(self, name: str, check: Callable[[], dict[str, Any] | None]) -> None:
        """A step the later ones need: when it fails, they are skipped."""
        if not self.step(name, check):
            self.stopped = True


class HomeAssistant:
    """Home Assistant's REST and websocket API, with the demo user's token."""

    def __init__(self, url: str, token: str) -> None:
        """For the instance at this URL."""
        self.url = url
        self._token = token

    def request(self, method: str, path: str, body: Any = None) -> Any:
        """One REST request; the answer's JSON."""
        return json.loads(self._send(method, path, body) or b"null")

    def template(self, template: str) -> str:
        """Render a template; the API answers with plain text."""
        return self._send("POST", "/api/template", {"template": template}).decode()

    def _send(self, method: str, path: str, body: Any) -> bytes:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(
            self.url + path,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            raise Failed(
                f"{method} {path}: HTTP {error.code} {error.read()[:300]!r}"
            ) from None

    def state(self, entity_id: str) -> dict[str, Any]:
        """An entity's state and attributes."""
        return self.request("GET", f"/api/states/{entity_id}")

    def call(self, domain: str, service: str, data: dict[str, Any]) -> None:
        """Call an action."""
        self.request("POST", f"/api/services/{domain}/{service}", data)

    def ws(self, message: dict[str, Any]) -> Any:
        """One websocket command (for what REST does not offer)."""

        async def call() -> Any:
            async with (
                aiohttp.ClientSession() as session,
                session.ws_connect(self.url + "/api/websocket") as ws,
            ):
                await ws.receive_json()  # auth_required
                await ws.send_json({"type": "auth", "access_token": self._token})
                if (await ws.receive_json())["type"] != "auth_ok":
                    raise Failed("the websocket did not accept the token")
                await ws.send_json({"id": 1, **message})
                answer = await ws.receive_json()
                if not answer.get("success"):
                    raise Failed(f"{message['type']}: {answer.get('error')}")
                return answer["result"]

        return asyncio.run(call())


class Helper:
    """The screen helper as a process, with its JSON events."""

    def __init__(self, work: Path) -> None:
        """Its config directory (id and pairings) lives in the work folder."""
        self.config = work / "helper-config"
        self.log = work / "helper.log"
        self.port = 0  # any free port the first time, then the same one
        self.events: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None

    def command(self, *args: str) -> list[str]:
        """`uv run` of the helper's console script, as documented."""
        uv = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")
        return [
            uv,
            "run",
            "--project",
            str(REPO / "screen_helper"),
            "--locked",
            "glowcast-screen",
            "--config-dir",
            str(self.config),
            "--non-interactive",
            "--json",
            *args,
        ]

    def start(self) -> dict[str, Any]:
        """Serve the patterns on 127.0.0.1; return the `started` event."""
        self._process = subprocess.Popen(
            self.command(
                "--pattern",
                "cycle",
                "--pattern-interval",
                str(INTERVAL),
                "--name",
                HELPER_NAME,
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
                "--pairing-code",
                PAIRING_CODE,
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            start_new_session=True,  # stopped as a group, uv and the helper
        )
        threading.Thread(target=self._read, args=(self._process,), daemon=True).start()
        started = self.wait_for("started", timeout=180)
        if started is None:
            self.stop()
            raise Failed("the screen helper did not start (see helper.log)")
        self.port = started["port"]
        return started

    def _read(self, process: subprocess.Popen[str]) -> None:
        assert process.stdout is not None
        with open(self.log, "a", encoding="utf-8") as log:
            for line in process.stdout:
                log.write(line)
                log.flush()
                with contextlib.suppress(ValueError):
                    event = json.loads(line)
                    event["received"] = time.time()
                    with self._lock:
                        self.events.append(event)

    def wait_for(
        self, kind: str, *, after: float = 0.0, timeout: float
    ) -> dict[str, Any] | None:
        """The first event of this kind received after `after`."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                for event in self.events:
                    if event["event"] == kind and event["received"] >= after:
                        return event
            if self._process is not None and self._process.poll() is not None:
                return None
            time.sleep(0.1)
        return None

    def patterns(self) -> list[tuple[str, float]]:
        """(pattern, start as a Unix time) of each `pattern` event so far."""
        with self._lock:
            return [
                (event["showing"], _timestamp(event["since"]))
                for event in self.events
                if event["event"] == "pattern"
            ]

    def stop(self) -> float:
        """Stop it as Ctrl+C would; return when it was asked to."""
        asked = time.time()
        process, self._process = self._process, None
        if process is not None and process.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(10)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait(5)
        return asked

    def unpair(self) -> None:
        """Forget all pairings (`--unpair`); the id stays."""
        result = subprocess.run(
            self.command("--unpair"),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if result.returncode != 0 or '"unpaired"' not in result.stdout:
            raise Failed(f"--unpair failed with exit code {result.returncode}")


class Bridge:
    """What the fake bridge recorded (events.jsonl)."""

    def __init__(self, events: Path, url: str) -> None:
        """Read from this file; press the link button at this URL."""
        self._events = events
        self._url = url

    def events(self, kind: str | None = None) -> list[dict[str, Any]]:
        """All recorded events (of one kind)."""
        lines = self._events.read_text(encoding="utf-8").splitlines()
        events = [json.loads(line) for line in lines if line.strip()]
        return [e for e in events if kind is None or e["event"] == kind]

    def frames(self, begin: float, end: float) -> list[dict[str, Any]]:
        """The colour messages received in [begin, end)."""
        return [e for e in self.events("stream") if begin <= e["time"] < end]

    def actions(self, action: str, begin: float, end: float) -> list[float]:
        """When the TV area got this action in [begin, end)."""
        return [
            e["time"]
            for e in self.events("action")
            if e["action"] == action
            and e["area"] == TV_AREA_ID
            and begin <= e["time"] < end
        ]

    def press_link_button(self) -> None:
        """As record.py does; the next pairing request succeeds."""
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE  # the fake's self-signed key
        request = urllib.request.Request(
            self._url + "/demo/press-link-button", data=b"", method="POST"
        )
        with urllib.request.urlopen(request, timeout=10, context=context):
            pass


class Watcher:
    """Polls the switch and the status sensor; keeps every change."""

    def __init__(self, ha: HomeAssistant) -> None:
        """Not watching yet."""
        self._ha = ha
        self.switch = ""
        self.status = ""
        self.timeline: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def watch(self, switch: str, status: str) -> None:
        """Start polling these entities, four times a second."""
        self.switch, self.status = switch, status
        threading.Thread(target=self._poll, daemon=True).start()

    def _poll(self) -> None:
        last = None
        while True:
            with contextlib.suppress(Exception):
                state = (
                    self._ha.state(self.switch)["state"],
                    self._ha.state(self.status)["state"],
                    self._ha.state(self.status)["attributes"].get("stop_reason"),
                )
                if state != last:
                    last = state
                    with self._lock:
                        self.timeline.append(
                            {
                                "time": time.time(),
                                "switch": state[0],
                                "status": state[1],
                                "stop_reason": state[2],
                            }
                        )
            time.sleep(0.25)

    def seen(self, status: str, begin: float, end: float) -> bool:
        """Whether the status sensor showed this value in [begin, end)."""
        with self._lock:
            return any(
                t["status"] == status and begin <= t["time"] < end
                for t in self.timeline
            )

    def wait(
        self, predicate: Callable[[dict[str, Any]], bool], timeout: float
    ) -> dict[str, Any] | None:
        """The current state once it matches, else None after the timeout."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                current = self.timeline[-1] if self.timeline else None
            if current is not None and predicate(current):
                return current
            time.sleep(0.1)
        return None


def main() -> None:
    """Run every step; write the summary; exit 1 on a failure."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", required=True)
    parser.add_argument("--bridge", required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    shutil.rmtree(args.work, ignore_errors=True)
    args.work.mkdir(parents=True)

    ha = HomeAssistant(args.url, os.environ["GLOWCAST_E2E_TOKEN"])
    bridge = Bridge(args.events, args.bridge)
    helper = Helper(args.work)
    watcher = Watcher(ha)
    run = Run()
    begin = time.time()
    try:
        steps(run, ha, bridge, helper, watcher)
    finally:
        helper.stop()
    passed = all(step["passed"] for step in run.steps)
    summary = {
        "passed": passed,
        "seconds": round(time.time() - begin, 1),
        "steps": run.steps,
        "status_timeline": [
            {**t, "time": round(t["time"] - begin, 2)} for t in watcher.timeline
        ],
    }
    args.summary.write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    failed = [step["name"] for step in run.steps if step["passed"] is not True]
    print(
        "PASSED: all steps" if passed else f"FAILED: {', '.join(failed)}",
        file=sys.stderr,
    )
    sys.exit(0 if passed else 1)


def steps(
    run: Run, ha: HomeAssistant, bridge: Bridge, helper: Helper, watcher: Watcher
) -> None:
    """Every step, in order."""
    found: dict[str, Any] = {}

    def start_helper() -> dict[str, Any]:
        started = helper.start()
        if started.get("mdns"):
            raise Failed("the helper announced itself on the network")
        return {"port": started["port"], "pattern": started["pattern"]}

    run.required("helper starts with --pattern cycle on 127.0.0.1", start_helper)
    run.required(
        "setup flow: computer screen by hand, Hue bridge, area",
        lambda: setup_flow(ha, bridge, helper, found),
    )

    def switch_on() -> dict[str, Any]:
        watcher.watch(found["switch"], found["status"])
        ha.call("switch", "turn_on", {"entity_id": found["switch"]})
        state = watcher.wait(lambda s: s["status"] == "streaming", timeout=30)
        if state is None:
            raise Failed(f"not streaming after 30 s: {watcher.timeline[-1:]}")
        found["streaming_since"] = state["time"]
        found["brightness"] = diagnostics(ha, found)["settings"]["brightness"]
        return {"brightness": found["brightness"]}

    run.required("switch on: streaming", switch_on)

    def wait_for_cycle() -> dict[str, Any]:
        # The first red whose settled stretch starts while streaming (it may
        # have begun just before): then white is streamed before black, so
        # the hold on black shows white, without waiting for a whole cycle.
        earliest = found["streaming_since"] - (SETTLE - MARGIN)
        deadline = time.time() + len(CYCLE) * INTERVAL + 10
        while time.time() < deadline:
            reds = [
                since
                for name, since in helper.patterns()
                if name == "red" and since >= earliest
            ]
            if reds:
                found["cycle"] = reds[0]
                break
            time.sleep(0.2)
        else:
            raise Failed("the helper showed no red after streaming started")
        end = found["cycle"] + len(CYCLE) * INTERVAL
        while time.time() < end + 0.5:
            if watcher.timeline and watcher.timeline[-1]["switch"] != "on":
                raise Failed(f"the switch went off: {watcher.timeline[-1]}")
            time.sleep(0.2)
        return {"patterns": [name for name, _ in helper.patterns()][-len(CYCLE) - 1 :]}

    run.required("one full pattern cycle while streaming", wait_for_cycle)

    level = round(found.get("brightness", 0) / 100 * 255)
    red, green, blue = (level, 0, 0), (0, level, 0), (0, 0, level)
    white = (level, level, level)
    for index, name in enumerate(CYCLE):
        start = found.get("cycle", 0.0) + index * INTERVAL
        if name == "black":
            run.step(
                "pattern black: the colours hold, then the lights are released",
                lambda start=start: check_black(bridge, watcher, start, white),
            )
            continue
        expected = {
            "red": dict.fromkeys(CHANNELS, red),
            "green": dict.fromkeys(CHANNELS, green),
            "blue": dict.fromkeys(CHANNELS, blue),
            "white": dict.fromkeys(CHANNELS, white),
            # The channel at x = 0 sees both halves; the top one (letterbox)
            # sees the bar and the picture: only the picture counts.
            "left-red-right-blue": {
                channel: red if channel_position(int(channel))["x"] < 0 else blue
                for channel in CHANNELS
                if channel_position(int(channel))["x"] != 0
            },
            "letterbox": dict.fromkeys(CHANNELS, green),
        }[name]
        run.step(
            f"pattern {name}",
            lambda start=start, name=name, expected=expected: check_pattern(
                bridge, start, name, expected
            ),
        )
    run.step(
        "stream: decrypted, with the right key, only to the claimed area",
        lambda: check_stream(
            bridge,
            found.get("cycle", 0.0),
            found.get("cycle", 0.0) + len(CYCLE) * INTERVAL,
        ),
    )
    run.step(
        "helper stops: switch off with tv_off",
        lambda: helper_stops(bridge, helper, watcher),
    )
    run.step(
        "helper forgets its pairings: reauth starts",
        lambda: reauth_starts(ha, helper, watcher, found),
    )
    run.step(
        "reauth with a new pairing code: syncing again, then off",
        lambda: reauth_done(ha, bridge, watcher, found),
    )


def setup_flow(
    ha: HomeAssistant, bridge: Bridge, helper: Helper, found: dict[str, Any]
) -> dict[str, Any]:
    """The setup flow through the REST API, as the frontend drives it."""
    flow = ha.request(
        "POST", "/api/config/config_entries/flow", {"handler": "glowcast"}
    )
    path = f"/api/config/config_entries/flow/{flow['flow_id']}"
    expect(flow, "menu", "user")
    flow = ha.request("POST", path, {"next_step_id": "screen"})
    expect(flow, "form", "screen")
    flow = ha.request("POST", path, {"host": "127.0.0.1", "port": helper.port})
    expect(flow, "form", "screen_code")
    flow = ha.request("POST", path, {"code": PAIRING_CODE})
    expect(flow, "form", "bridge")
    flow = ha.request("POST", path, {"bridge": "demo-bridge"})
    expect(flow, "form", "link")
    bridge.press_link_button()
    flow = ha.request("POST", path, {})
    deadline = time.monotonic() + 40
    while flow["type"] == "progress" and time.monotonic() < deadline:
        time.sleep(0.5)
        flow = ha.request("GET", path)
    expect(flow, "form", "area")
    flow = ha.request("POST", path, {"area": TV_AREA_ID})
    expect(flow, "create_entry", None)
    entry_id = flow["result"]["entry_id"]
    entities = json.loads(
        ha.template("{{ integration_entities('glowcast') | list | tojson }}")
    )
    switch = next((e for e in entities if e.startswith("switch.")), None)
    status = next(
        (
            e
            for e in entities
            if e.startswith("sensor.")
            and "tv_off" in (ha.state(e)["attributes"].get("options") or [])
        ),
        None,
    )
    if switch is None or status is None:
        raise Failed(f"no switch or status sensor among {entities}")
    found.update(entry_id=entry_id, switch=switch, status=status)
    return {"title": flow["title"], "switch": switch, "status": status}


def expect(flow: dict[str, Any], kind: str, step: str | None) -> None:
    """Fail unless the flow is at this kind of step."""
    if flow.get("type") != kind or (step is not None and flow.get("step_id") != step):
        raise Failed(
            f"expected {kind} {step}, got {flow.get('type')} {flow.get('step_id')} "
            f"(errors {flow.get('errors')}, reason {flow.get('reason')})"
        )
    if flow.get("errors"):
        raise Failed(f"{step}: errors {flow['errors']}")


def diagnostics(ha: HomeAssistant, found: dict[str, Any]) -> dict[str, Any]:
    """Glowcast's diagnostics of the entry (settings and status)."""
    return ha.request("GET", f"/api/diagnostics/config_entry/{found['entry_id']}")[
        "data"
    ]


def check_pattern(
    bridge: Bridge, start: float, name: str, expected: dict[str, tuple[int, ...]]
) -> dict[str, Any]:
    """Every message in the settled stretch has the expected colours."""
    frames = bridge.frames(start + SETTLE, start + INTERVAL - MARGIN)
    detail = colours(frames, expected)
    if len(frames) < MIN_FRAMES:
        raise Failed(f"{len(frames)} messages, fewer than {MIN_FRAMES}: {detail}")
    if detail["mismatches"]:
        raise Failed(f"colours differ from {expected}: {detail}")
    return detail


def colours(
    frames: list[dict[str, Any]], expected: dict[str, tuple[int, ...]]
) -> dict[str, Any]:
    """The messages, the median colour per channel and the mismatches."""
    mismatches = []
    for frame in frames:
        for channel, want in expected.items():
            got = frame["channels"].get(channel)
            if got is None or any(
                abs(a - b) > TOLERANCE for a, b in zip(got, want, strict=True)
            ):
                mismatches.append({"channel": channel, "got": got, "want": want})
    medians = {
        channel: [
            round(statistics.median(f["channels"][channel][i] for f in frames))
            for i in range(3)
        ]
        for channel in CHANNELS
        if frames and all(channel in f["channels"] for f in frames)
    }
    return {
        "messages": len(frames),
        "median": medians,
        "mismatches": len(mismatches),
        **({"first_mismatch": mismatches[0]} if mismatches else {}),
    }


def check_black(
    bridge: Bridge, watcher: Watcher, start: float, white: tuple[int, ...]
) -> dict[str, Any]:
    """All-zero frames are no picture (#68): the last colours (white) hold,
    and after NO_PICTURE_WAIT the lights are released; the stream stops.

    Before the release, the colours hold (and fade to the glow if
    NO_PICTURE_WAIT is longer than the light state's hold on black); only
    the first stretch, well within that hold, is compared.
    """
    frames = bridge.frames(start + 0.8, start + 2.3)
    hold = colours(frames, dict.fromkeys(CHANNELS, white))
    if len(frames) < MIN_FRAMES // 2 or hold["mismatches"]:
        raise Failed(f"the colours did not hold white: {hold}")
    stops = bridge.actions("stop", start, start + INTERVAL)
    if not stops:
        raise Failed("no `action: stop` for the area during the black pattern")
    delay = stops[0] - start
    early, late = NO_PICTURE_WAIT - RELEASE_EARLY, NO_PICTURE_WAIT + RELEASE_LATE
    if not early <= delay <= late:
        raise Failed(
            f"released {delay:.2f} s after black, not after about "
            f"{NO_PICTURE_WAIT:g} s (NO_PICTURE_WAIT)"
        )
    if after := bridge.frames(stops[0], start + INTERVAL):
        raise Failed(f"{len(after)} messages after the release")
    if not watcher.seen("no_picture", start, start + INTERVAL + 1):
        raise Failed("the status sensor never showed no_picture")
    return {"hold": hold, "released_after_s": round(delay, 2), "status": "no_picture"}


def check_stream(bridge: Bridge, cycle: float, until: float) -> dict[str, Any]:
    """Every record decrypted, every handshake with the right key, messages
    only for the claimed area, and a new claim after black in the cycle.

    Not whether the values are 8-bit: hue-entertainment writes an 8-bit value
    twice and shifts a 16-bit one down, so the wire looks the same (the unit
    tests cover Glowcast's 8-bit rule).
    """
    if errors := [e for e in bridge.events("dtls_error") if e["time"] < until]:
        raise Failed(f"{len(errors)} DTLS errors, the first: {errors[0]['reason']}")
    handshakes = [e for e in bridge.events("handshake") if e["time"] < until]
    if not handshakes or not all(
        e["identity_ok"] and e["finished_ok"] for e in handshakes
    ):
        raise Failed(f"a handshake failed: {handshakes}")
    frames = [e for e in bridge.events("stream") if e["time"] < until]
    bad = [
        e
        for e in frames
        if e["area"] != TV_AREA_ID
        or not e["active"]
        or not e["whole"]
        or e["color_space"] != 0
        or sorted(e["channels"]) != CHANNELS
    ]
    if bad:
        raise Failed(f"{len(bad)} of {len(frames)} messages wrong, the first: {bad[0]}")
    black = cycle + CYCLE.index("black") * INTERVAL
    claims = bridge.actions("start", black, black + 2 * INTERVAL)
    reclaim = [e for e in handshakes if black <= e["time"] < black + 2 * INTERVAL]
    if len(claims) != 1 or len(reclaim) != 1:
        raise Failed(
            f"{len(claims)} claims and {len(reclaim)} handshakes after the black "
            "pattern; expected one of each"
        )
    return {
        "messages": len(frames),
        "handshakes": len(handshakes),
        "reclaim_after_black_s": round(claims[0] - black, 2),
    }


def helper_stops(bridge: Bridge, helper: Helper, watcher: Watcher) -> dict[str, Any]:
    """A computer that goes away: the switch turns off with tv_off."""
    # Stop while a picture streams: never during black (or white, just
    # before it), nor during the claim at the start of a pattern, and with at
    # least 2 s of the pattern left.
    deadline = time.monotonic() + 2 * len(CYCLE) * INTERVAL
    while True:
        name, since = helper.patterns()[-1]
        shown = time.time() - since
        current = watcher.timeline[-1]
        if (
            name not in ("white", "black")
            and 1.0 <= shown <= INTERVAL - 2.0
            and current["status"] == "streaming"
        ):
            break
        if time.monotonic() > deadline:
            raise Failed(f"no moment to stop the helper while streaming: {current}")
        time.sleep(0.1)
    stopped = helper.stop()
    state = watcher.wait(
        lambda s: s["switch"] == "off" and s["status"] == "tv_off", timeout=20
    )
    if state is None:
        raise Failed(
            f"the switch did not turn off with tv_off: {watcher.timeline[-3:]}"
        )
    if state["stop_reason"] != "tv_off":
        raise Failed(f"stop reason {state['stop_reason']}, not tv_off")
    releases = bridge.actions("stop", stopped, time.time())
    if not releases:
        raise Failed("the lights were not released (`action: stop`)")
    if late := bridge.frames(releases[0], time.time()):
        raise Failed(f"{len(late)} messages after the release")
    return {
        "off_after_s": round(state["time"] - stopped, 2),
        "released_after_s": round(releases[0] - stopped, 2),
    }


def reauth_starts(
    ha: HomeAssistant, helper: Helper, watcher: Watcher, found: dict[str, Any]
) -> dict[str, Any]:
    """The helper forgets Glowcast's token: switching on starts reauth."""
    helper.unpair()
    helper.start()  # same id, same port, no pairings
    asked = time.time()
    ha.call("switch", "turn_on", {"entity_id": found["switch"]})
    state = watcher.wait(
        lambda s: s["time"] > asked and s["switch"] == "off" and s["status"] == "error",
        timeout=20,
    )
    if state is None:
        raise Failed(f"not stopped with an error: {watcher.timeline[-3:]}")
    if state["stop_reason"] != "source_auth_failed":
        raise Failed(f"stop reason {state['stop_reason']}, not source_auth_failed")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        flows = [
            flow
            for flow in ha.ws({"type": "config_entries/flow/progress"})
            if flow["handler"] == "glowcast"
            and flow["context"].get("source") == "reauth"
            and flow["context"].get("entry_id") == found["entry_id"]
        ]
        if flows:
            found["reauth_flow"] = flows[0]["flow_id"]
            return {
                "stop_reason": state["stop_reason"],
                "reauth_step": flows[0]["step_id"],
            }
        time.sleep(0.5)
    raise Failed("no reauth flow in progress for the entry")


def reauth_done(
    ha: HomeAssistant, bridge: Bridge, watcher: Watcher, found: dict[str, Any]
) -> dict[str, Any]:
    """Pair again in the reauth flow, switch on (streams), switch off (released)."""
    flow = ha.request(
        "POST",
        f"/api/config/config_entries/flow/{found['reauth_flow']}",
        {"code": PAIRING_CODE},
    )
    if flow.get("type") != "abort" or flow.get("reason") != "screen_reauth_successful":
        raise Failed(
            f"the reauth flow ended with {flow.get('type')} {flow.get('reason')}"
        )
    asked = time.time()
    ha.call("switch", "turn_on", {"entity_id": found["switch"]})
    if (
        watcher.wait(lambda s: s["time"] > asked and s["status"] == "streaming", 30)
        is None
    ):
        raise Failed(f"not streaming after the reauth: {watcher.timeline[-3:]}")
    time.sleep(2)
    if not bridge.frames(asked, time.time()):
        raise Failed("no colours at the bridge after the reauth")
    off = time.time()
    ha.call("switch", "turn_off", {"entity_id": found["switch"]})
    if watcher.wait(lambda s: s["time"] > off and s["status"] == "idle", 20) is None:
        raise Failed(f"not idle after switching off: {watcher.timeline[-3:]}")
    time.sleep(0.5)
    if not bridge.actions("stop", off, time.time()):
        raise Failed("switching off did not release the lights")
    return {"streams_again": True, "released_on_switch_off": True}


def _timestamp(iso: str) -> float:
    """A helper time (UTC, ISO 8601 with Z) as a Unix time."""
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


if __name__ == "__main__":
    main()
