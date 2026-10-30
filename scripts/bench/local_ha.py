"""The bench's local Home Assistant: one per working copy, in WSL.

Built on the local demo's machinery (`scripts/demo.py`: its shared virtual
environment, config directory, stub `hue` and `webostv` integrations,
onboarding and process handling), but with Glowcast's real screen source and
real Hue output: the stub `hue` entry carries the real bridge's host, and
Glowcast's entry carries the bench's own Hue key and the helper's token. So,
unlike the demo, it does reach the bridge; it still listens on 127.0.0.1 only
and loads no discovery integration.

The instances live in ~/.cache/glowcast-bench/instances/, outside the demo's
folder, so the demo's `clean --all` does not remove them.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import shutil
import signal
import sys
import time
import urllib.error
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import demo  # noqa: E402  (scripts/demo.py)

HOME = Path.home() / ".cache" / "glowcast-bench"
INSTANCES = HOME / "instances"
HA_PORT = 8299  # the demo starts at 8199
ENTRY_ID = "benchglowcastentry"
BRIDGE_UNIQUE_ID = "bench-bridge"
TITLE = "Bench"
EXTRA_CONFIGURATION = """\
diagnostics:
"""
# Home Assistant lights (#9): proxy lights under stub `hue` and `matter` entries
# (stubs/proxy.py), and the service that stores Glowcast's lights.
STUBS = Path(__file__).resolve().parent / "stubs"
PROXY = "bench_proxy"  # the stub entries' data key (stubs/proxy.py)
HA_LIGHTS_CONFIGURATION = """\
glowcast_bench:
"""
MATTER_UNIQUE_ID = "bench-thread"


def instance() -> demo.Instance:
    """This working copy's bench instance."""
    return demo.Instance(demo._default_name(), INSTANCES / demo._default_name())


def install() -> None:
    """The demo's shared environment (Home Assistant), installed if needed."""
    with demo._lock():
        demo._install(browser=False)  # the bench needs no Chromium


def python() -> Path:
    return demo._python()


def glowcast_entry(
    *,
    helper: dict[str, Any],
    app_key: str,
    client_key: str,
    area_id: str,
    settings: dict[str, Any],
) -> dict[str, Any]:
    """Glowcast's entry as the setup flow stores it (architecture 5)."""
    entry = demo._entry(
        "glowcast",
        TITLE,
        helper["helper_id"],
        {
            "source": {"type": "screen", **helper},
            "bridge": {
                "unique_id": BRIDGE_UNIQUE_ID,
                "app_key": app_key,
                "client_key": client_key,
            },
        },
    )
    entry["entry_id"] = ENTRY_ID
    entry["options"] = {
        "output": {"type": "hue_entertainment", "area_id": area_id},
        **settings,
    }
    return entry


def ha_lights_glowcast_entry(
    *, helper: dict[str, Any], settings: dict[str, Any]
) -> dict[str, Any]:
    """Glowcast's entry with the Home Assistant lights output (no `bridge` in data).

    No lights yet: they are stored by entity registry id, known only once
    Home Assistant has added the proxy lights (`glowcast_bench.set_output`).
    """
    entry = demo._entry(
        "glowcast",
        TITLE,
        helper["helper_id"],
        {"source": {"type": "screen", **helper}},
    )
    entry["entry_id"] = ENTRY_ID
    entry["options"] = {
        "output": {"type": "ha_lights", "follow": "lights", "lights": []},
        **settings,
    }
    return entry


def combined_output(area_id: str, lights: dict[str, Any]) -> dict[str, Any]:
    """The combined output block (#11): the area, then the Home Assistant lights."""
    return {
        "type": "combined",
        "parts": [{"type": "hue_entertainment", "area_id": area_id}, lights],
    }


def combined_glowcast_entry(
    *,
    helper: dict[str, Any],
    app_key: str,
    client_key: str,
    area_id: str,
    settings: dict[str, Any],
) -> dict[str, Any]:
    """Glowcast's entry with the bench area and Home Assistant lights in one (#11).

    The Home Assistant lights' part has no lights yet: they are stored by entity
    registry id once Home Assistant has added the proxies.
    """
    entry = glowcast_entry(
        helper=helper,
        app_key=app_key,
        client_key=client_key,
        area_id=area_id,
        settings=settings,
    )
    entry["options"]["output"] = combined_output(
        area_id, {"type": "ha_lights", "follow": "lights", "lights": []}
    )
    return entry


def stub_entry_id(domain: str) -> str:
    """The id of a stub entry the bench writes (demo._entry's)."""
    return demo._entry(domain, "", "", {})["entry_id"]


def proxy_block(url: str, token: str, lights: list[dict[str, str]]) -> dict[str, Any]:
    """A stub entry's proxy lights: [{"unique_id", "name", "source"}]."""
    return {"url": url, "token": token, "lights": lights}


def _add_ha_lights_stubs(inst: demo.Instance) -> None:
    """The bench's `hue` overlay (the demo's stub plus proxy lights), its
    `matter` stub and `glowcast_bench`, in this instance only."""
    custom = inst.config / "custom_components"
    for name in ("hue", "matter", "glowcast_bench"):
        shutil.copytree(STUBS / name, custom / name, dirs_exist_ok=True)
    for name in ("hue", "matter"):
        shutil.copy(STUBS / "proxy.py", custom / name / "proxy.py")


def start(
    bridge_host: str,
    glowcast: dict[str, Any],
    proxies: dict[str, dict[str, Any]] | None = None,
    *,
    hue_api_key: str | None = None,
) -> tuple[demo.Instance, str]:
    """A fresh config with the entries, Home Assistant started; returns a token.

    `proxies` ({"hue": block, "matter": block}, see proxy_block): proxy lights
    for Home Assistant lights under the stub `hue` entry and a stub `matter` entry.
    `hue_api_key`: the stub `hue` entry's key (the bench's own Hue key, for
    the combined suite: Glowcast asks the bridge through it which lights the
    area streams to, #198); only in the instance's private storage.
    """
    inst = instance()
    stop()
    with demo._lock():
        demo._install(browser=False)  # the bench needs no Chromium
        # Not a port of another running bench instance (Home Assistant binds
        # its port only after a while), nor one something listens on.
        port = demo._free_port(HA_PORT, _taken_ports(inst))
        inst.home.mkdir(parents=True, exist_ok=True)
        state = inst.home / "state.json"
        state.write_text(
            json.dumps(
                {
                    "repo": str(demo.REPO),
                    "ha_port": port,
                    "bridge_port": 0,
                    "fake_sync": False,
                }
            )
        )
        inst = dataclasses.replace(inst)
        # The demo's config (HTTP on 127.0.0.1, onboarding, the stubs), then
        # the bench's own entries over its entries.
        demo._write_config(inst)
        (inst.config / "configuration.yaml").write_text(
            demo.CONFIGURATION
            + EXTRA_CONFIGURATION
            + (HA_LIGHTS_CONFIGURATION if proxies is not None else "")
        )
        hue_data: dict[str, Any] = {
            "host": bridge_host,
            "api_key": hue_api_key or "unused-by-glowcast",
            "api_version": 2,
        }
        entries = [demo._entry("hue", "Hue Bridge", BRIDGE_UNIQUE_ID, hue_data)]
        if proxies is not None:
            _add_ha_lights_stubs(inst)
            if "hue" in proxies:
                hue_data[PROXY] = proxies["hue"]
            if "matter" in proxies:
                entries.append(
                    demo._entry(
                        "matter", "Matter", MATTER_UNIQUE_ID, {PROXY: proxies["matter"]}
                    )
                )
        demo._write_storage(
            inst.config,
            "core.config_entries",
            {"entries": [*entries, glowcast]},
            minor_version=5,
        )
        _private(inst.home)
        inst.run.mkdir(parents=True, exist_ok=True)
        demo._spawn(inst, "homeassistant", ha_args(inst))
    try:
        demo._wait_for_ha(inst)
        token = demo._onboard(inst)
        demo._wait_until_running(inst, token)
        _check_own(inst, token)
    except BaseException:
        stop()
        raise
    return inst, token


def ha_args(inst: demo.Instance) -> list[str]:
    """Home Assistant's command line (after the shared environment's Python)."""
    return ["-m", "homeassistant", "--config", str(inst.config), "--skip-pip"]


@dataclasses.dataclass(frozen=True)
class Restarted:
    """What a restart did: how the stop went and when HA ran again."""

    stop_s: float  # from SIGTERM until the process had gone
    graceful: bool  # gone by itself (not killed): its stop stages ran
    resume: bool | None  # the switch's stored `resume`; None: not stored
    running_at: float  # time.time() when HA was running again


def restart(switch: str) -> tuple[demo.Instance, str, Restarted]:
    """Stop Home Assistant gracefully and start it again on the same config
    (#183): its entries, storage and restore state stay. Returns the
    instance, a new token and what happened.

    `switch`: Glowcast's switch, whose stored `resume` is read between the
    stop and the start (HA writes the restore state again once it runs).
    """
    inst = instance()
    if not inst.state:
        sys.exit("The bench instance has no state: it was never started")
    stop_s, graceful = stop_gracefully(inst)
    resume = stored_resume(inst.config, switch)
    with demo._lock():
        _private(inst.home)
        demo._spawn(inst, "homeassistant", ha_args(inst))
    try:
        _wait_for_http(inst)
        token = login(inst)
        demo._wait_until_running(inst, token)
        running_at = time.time()
        _check_own(inst, token)
    except BaseException:
        stop()
        raise
    return inst, token, Restarted(stop_s, graceful, resume, running_at)


# SIGTERM runs Home Assistant's stop stages (the restore state is written at
# EVENT_HOMEASSISTANT_STOP); demo.stop kills after 30 s, this waits longer.
STOP_WITHIN = 90.0


def stop_gracefully(
    inst: demo.Instance, within: float = STOP_WITHIN, poll: float = 0.5
) -> tuple[float, bool]:
    """SIGTERM, then wait until Home Assistant has gone; SIGKILL after
    `within`. Returns the seconds it took and whether it went by itself."""
    pid = demo._pid(inst, "homeassistant")
    if pid is None:
        return 0.0, False
    start = time.monotonic()
    os.kill(pid, signal.SIGTERM)
    graceful = True
    while demo._pid(inst, "homeassistant") is not None:
        if time.monotonic() - start > within:
            os.kill(pid, signal.SIGKILL)
            graceful = False
            break
        time.sleep(poll)
    (inst.run / "homeassistant.pid").unlink(missing_ok=True)
    return round(time.monotonic() - start, 2), graceful


def stored_resume(config: Path, switch: str) -> bool | None:
    """The switch's stored `resume` (the extra data in core.restore_state)."""
    with contextlib.suppress(OSError, ValueError):
        stored = json.loads((config / ".storage" / "core.restore_state").read_text())
        for item in stored.get("data") or []:
            if (item.get("state") or {}).get("entity_id") == switch:
                value = (item.get("extra_data") or {}).get("resume")
                return value if isinstance(value, bool) else None
    return None


def _wait_for_http(inst: demo.Instance, within: float = 900) -> None:
    """Until HA answers. Not demo._wait_for_ha: once onboarded, HA has no
    /api/onboarding (404), so that wait would never end."""
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if demo._pid(inst, "homeassistant") is None:
            sys.exit(f"Home Assistant stopped; see {inst.run / 'homeassistant.log'}")
        try:
            demo._request(inst, "GET", "/auth/providers")
            return
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            time.sleep(2)
    sys.exit("Home Assistant did not start in time")


def login(inst: demo.Instance) -> str:
    """An access token for the demo's user on an onboarded config (the user
    exists, so demo._onboard cannot make it): the login flow, then a code."""
    client_id = f"{inst.url}/"
    flow = demo._request(
        inst,
        "POST",
        "/auth/login_flow",
        {
            "client_id": client_id,
            "handler": ["homeassistant", None],
            "redirect_uri": client_id,
        },
    )
    done = demo._request(
        inst,
        "POST",
        f"/auth/login_flow/{flow['flow_id']}",
        {
            "client_id": client_id,
            "username": demo.USERNAME,
            "password": demo.PASSWORD,
        },
    )
    if done.get("type") != "create_entry":
        sys.exit("The local Home Assistant refused the bench's login")
    return demo._request(
        inst,
        "POST",
        "/auth/token",
        form={
            "grant_type": "authorization_code",
            "code": done["result"],
            "client_id": client_id,
        },
    )["access_token"]


def _taken_ports(inst: demo.Instance) -> set[int | None]:
    """The Home Assistant ports of the other running bench instances."""
    taken: set[int | None] = set()
    if INSTANCES.exists():
        for home in INSTANCES.iterdir():
            other = demo.Instance(home.name, home)
            if home != inst.home and demo._running(other):
                taken.add(other.state.get("ha_port"))
    return taken


def _check_own(inst: demo.Instance, token: str) -> None:
    """Exit (start() then stops it) unless the Home Assistant on the port is
    this instance: another one there would get the bench's calls."""
    config_dir = demo._request(inst, "GET", "/api/config", token=token).get(
        "config_dir"
    )
    if Path(str(config_dir)).resolve() != inst.config.resolve():
        sys.exit("Another Home Assistant answers on the bench instance's port")


def _private(home: Path) -> None:
    """Only this user may read the instance: its storage holds the bench's keys."""
    os.chmod(home, 0o700)
    for folder, _dirs, files in os.walk(home / "config" / ".storage"):
        os.chmod(folder, 0o700)
        for name in files:
            os.chmod(Path(folder) / name, 0o600)


def stop() -> None:
    inst = instance()
    if inst.state:
        demo.stop(inst)


def log_path() -> Path:
    return instance().config / "home-assistant.log"
