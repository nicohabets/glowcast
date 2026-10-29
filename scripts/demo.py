"""A local Home Assistant with Glowcast from the working copy, for review GIFs.

Nothing in it reaches a real TV or bridge: stub hue and webostv integrations
report their entries as loaded, a fake bridge on 127.0.0.1 answers the setup
flow, and Home Assistant listens on 127.0.0.1 only, without default_config or
any discovery integration. See scripts/README.md.

Usage:
    python scripts/demo.py start
    python scripts/demo.py record [FLOW ...] [--lang en|nl] [--theme light|dark]
                                  [--still] [--out DIR]
    python scripts/demo.py stop
    python scripts/demo.py clean

Runs on Linux and macOS (on Windows: in WSL). `start` needs Python 3.14.2 or
newer, or uv (https://docs.astral.sh/uv/) to fetch it. Everything it makes
lives in ~/.cache/glowcast-demo, outside the repository.
"""

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
DEMO = REPO / "scripts" / "demo"
HOME = Path.home() / ".cache" / "glowcast-demo"
VENV = HOME / "venv"
CONFIG = HOME / "config"
RUN = HOME / "run"
RECORDINGS = HOME / "recordings"
FLOWS = ["setup", "settings", "device"]  # the flows in demo/record.py

HA_PORT = 8199
BRIDGE_PORT = 8443
HA_URL = f"http://127.0.0.1:{HA_PORT}"

# Home Assistant and the frontend track the "latest" row in
# .github/workflows/ci.yml; bump them together with CI.
REQUIREMENTS = [
    "homeassistant==2026.10.4",
    "home-assistant-frontend==20260930.5",
    "aiowebostv==0.10.0",
    "hue-entertainment==0.1.2",
    "playwright==1.64.1",
    "imageio-ffmpeg==0.6.0",  # ffmpeg for the GIFs, without a system install
]
PYTHON = (3, 14, 2)

# Generic demo values, made at start-up; never a real login.
USERNAME = "demo"
PASSWORD = "demo-password"
TV_UNIQUE_ID = "demo-tv"
NOW = "2026-01-01T00:00:00+00:00"
BRIDGE_UNIQUE_ID = "demo-bridge"

# Integrations that look for devices on the network; the demo must load none.
DISCOVERY = {
    "bluetooth",
    "default_config",
    "dhcp",
    "homekit_controller",
    "ssdp",
    "usb",
    "zeroconf",
}

CONFIGURATION = """\
# Glowcast demo: no default_config, so no discovery integrations. The HTTP
# settings are in .storage/http (written by demo.py), not here.
homeassistant:
  name: Demo
  unit_system: metric
  time_zone: UTC
  country: NL
  language: en
frontend:
config:
logger:
  default: warning
  logs:
    custom_components.glowcast: info
"""


def main() -> None:
    """Run one command."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("start", help="start Home Assistant and the fake bridge")
    record = commands.add_parser("record", help="record GIFs of the flows")
    record.add_argument("flows", nargs="*", choices=FLOWS, metavar="FLOW")
    record.add_argument("--lang", choices=["en", "nl"], default="en")
    record.add_argument("--theme", choices=["light", "dark"], default="light")
    record.add_argument(
        "--still", action="store_true", help="a PNG of each step instead of a GIF"
    )
    record.add_argument("--out", type=Path, default=RECORDINGS)
    commands.add_parser("stop", help="stop Home Assistant and the fake bridge")
    commands.add_parser("clean", help="stop and remove everything the demo made")
    args = parser.parse_args()

    if args.command == "start":
        start()
    elif args.command == "record":
        record_flows(args)
    elif args.command == "stop":
        stop()
    else:
        stop()
        shutil.rmtree(HOME, ignore_errors=True)
        print(f"Removed {HOME}")


def start() -> None:
    """Install if needed, write a fresh config and start both processes."""
    if _running():
        sys.exit("The demo is already running; run `stop` first.")
    _install()
    _write_config()
    RUN.mkdir(parents=True, exist_ok=True)
    _spawn(
        "bridge",
        [str(DEMO / "fake_bridge.py"), str(RUN / "bridge"), str(BRIDGE_PORT)],
    )
    _spawn("homeassistant", ["-m", "homeassistant", "--config", str(CONFIG)])
    print(f"Waiting for Home Assistant on {HA_URL}...", flush=True)
    _wait_for_ha()
    token = _onboard()
    _wait_until_running(token)
    _check_isolated(token)
    print(
        f"Ready: {HA_URL} (user {USERNAME!r}, password {PASSWORD!r}).\n"
        "Glowcast is not set up yet; `record` runs the setup flow."
    )


def record_flows(args: argparse.Namespace) -> None:
    """Run the recording script in the demo's environment."""
    if not _running():
        sys.exit("The demo is not running; run `start` first.")
    started_from = (CONFIG / "custom_components" / "glowcast").resolve()
    if started_from != (REPO / "custom_components" / "glowcast").resolve():
        sys.exit(
            f"The running demo uses Glowcast from {started_from}; "
            "run `stop`, then `start` from this working copy."
        )
    args.out.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            str(_python()),
            str(DEMO / "record.py"),
            "--url",
            HA_URL,
            "--bridge",
            f"https://127.0.0.1:{BRIDGE_PORT}",
            "--user",
            USERNAME,
            "--password",
            PASSWORD,
            "--lang",
            args.lang,
            "--theme",
            args.theme,
            "--out",
            str(args.out),
            *(["--still"] if args.still else []),
            *args.flows,
        ],
    )
    sys.exit(result.returncode)


def stop() -> None:
    """Stop both processes, Home Assistant first."""
    for name in ("homeassistant", "bridge"):
        pid_file = RUN / f"{name}.pid"
        if not pid_file.exists():
            continue
        pid = int(pid_file.read_text())
        if _alive(pid):
            os.kill(pid, signal.SIGTERM)
            for _ in range(60):
                if not _alive(pid):
                    break
                time.sleep(0.5)
            else:
                os.kill(pid, signal.SIGKILL)
            print(f"Stopped {name}")
        pid_file.unlink()


def _install() -> None:
    """Create the virtual environment with the pinned requirements, once."""
    marker = VENV / "glowcast-demo.json"
    if marker.exists() and json.loads(marker.read_text()) == REQUIREMENTS:
        return
    shutil.rmtree(VENV, ignore_errors=True)
    print(f"Installing Home Assistant into {VENV}...", flush=True)
    if sys.version_info >= PYTHON:
        subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
        install = [str(_python()), "-m", "pip", "install"]
    elif uv := shutil.which("uv"):
        python = ".".join(str(part) for part in PYTHON[:2])
        subprocess.run([uv, "venv", "--python", python, str(VENV)], check=True)
        install = [uv, "pip", "install", "--python", str(_python())]
    else:
        sys.exit(
            "The demo needs Python {}.{}.{} or newer, or uv to fetch it.".format(
                *PYTHON
            )
        )
    subprocess.run([*install, *REQUIREMENTS], check=True)
    subprocess.run(
        [str(_python()), "-m", "playwright", "install", "chromium"], check=True
    )
    marker.write_text(json.dumps(REQUIREMENTS))


def _write_config() -> None:
    """A fresh config directory: the TV and bridge entries, no Glowcast entry."""
    shutil.rmtree(CONFIG, ignore_errors=True)
    (CONFIG / ".storage").mkdir(parents=True)
    (CONFIG / "configuration.yaml").write_text(CONFIGURATION)
    entries = [
        _entry(
            "hue",
            "Hue Bridge",
            BRIDGE_UNIQUE_ID,
            {
                "host": f"127.0.0.1:{BRIDGE_PORT}",
                "api_key": "demo-api-key",
                "api_version": 2,
            },
        ),
        # No TV listens on 127.0.0.1, so switching Glowcast on stops at once.
        _entry(
            "webostv",
            "Living room TV",
            TV_UNIQUE_ID,
            {"host": "127.0.0.1", "client_secret": "demo-client-secret"},
        ),
    ]
    _write_storage("core.config_entries", {"entries": entries}, minor_version=5)
    # Since 2026.10 HTTP settings from YAML start as a trial that reverts to the
    # default (port 8123 on every interface) after a while; stored settings
    # are used as they are.
    _write_storage(
        "http",
        {
            "stable": {
                "server_host": ["127.0.0.1"],
                "server_port": HA_PORT,
                "cors_allowed_origins": [],
                "login_attempts_threshold": -1,
                "ip_ban_enabled": True,
                "ssl_profile": "modern",
                "use_x_frame_options": True,
                "created_at": NOW,
                "error": None,
                "error_message": None,
            },
            "pending": None,
            "yaml_migration_done": True,
        },
        version=2,
        minor_version=2,
    )
    # The other onboarding steps are marked done, so onboarding does not add its
    # default integrations (some of them reach the internet).
    _write_storage(
        "onboarding", {"done": ["core_config", "analytics", "integration"]}, version=4
    )
    custom = CONFIG / "custom_components"
    custom.mkdir()
    for stub in ("hue", "webostv"):
        shutil.copytree(DEMO / "stubs" / stub, custom / stub)
    (custom / "glowcast").symlink_to(REPO / "custom_components" / "glowcast")


def _write_storage(
    key: str, data: dict[str, Any], *, version: int = 1, minor_version: int = 1
) -> None:
    (CONFIG / ".storage" / key).write_text(
        json.dumps(
            {
                "version": version,
                "minor_version": minor_version,
                "key": key,
                "data": data,
            },
            indent=2,
        )
    )


def _entry(
    domain: str, title: str, unique_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    return {
        "created_at": NOW,
        "data": data,
        "disabled_by": None,
        "discovery_keys": {},
        "domain": domain,
        "entry_id": f"demo{domain}entry",
        "minor_version": 1,
        "modified_at": NOW,
        "options": {},
        "pref_disable_new_entities": False,
        "pref_disable_polling": False,
        "source": "user",
        "subentries": [],
        "title": title,
        "unique_id": unique_id,
        "version": 1,
    }


def _wait_for_ha() -> None:
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        if not _alive(int((RUN / "homeassistant.pid").read_text())):
            sys.exit(f"Home Assistant stopped; see {RUN / 'homeassistant.log'}")
        try:
            _request("GET", "/api/onboarding")
            return
        except (urllib.error.URLError, ConnectionError):
            time.sleep(2)
    sys.exit("Home Assistant did not start in time")


def _wait_until_running(token: str) -> None:
    """The API answers before start-up is done; wait for the rest."""
    deadline = time.monotonic() + 300
    while _request("GET", "/api/config", token=token)["state"] != "RUNNING":
        if time.monotonic() > deadline:
            sys.exit("Home Assistant did not finish starting in time")
        time.sleep(1)


def _onboard() -> str:
    """Create the test user, the last onboarding step; return an access token."""
    client_id = f"{HA_URL}/"
    user = _request(
        "POST",
        "/api/onboarding/users",
        {
            "client_id": client_id,
            "name": "Demo",
            "username": USERNAME,
            "password": PASSWORD,
            "language": "en",
        },
    )
    token = _request(
        "POST",
        "/auth/token",
        form={
            "grant_type": "authorization_code",
            "code": user["auth_code"],
            "client_id": client_id,
        },
    )["access_token"]
    return token


def _check_isolated(token: str) -> None:
    """Stop the demo if it could reach or be reached from the network."""
    http = json.loads((CONFIG / ".storage" / "http").read_text())["data"]
    if http["pending"] is not None or http["stable"]["server_host"] != ["127.0.0.1"]:
        stop()
        sys.exit("Stopped: Home Assistant does not listen on 127.0.0.1 only.")
    log = (CONFIG / "home-assistant.log").read_text()
    for stub in ("hue", "webostv"):
        if f"custom integration {stub} " not in log:
            stop()
            sys.exit(f"Stopped: the {stub} stub was not loaded.")
    loaded = {
        component.split(".")[0]
        for component in _request("GET", "/api/config", token=token)["components"]
    }
    if found := loaded & DISCOVERY:
        stop()
        sys.exit(f"Stopped: discovery integrations were loaded: {sorted(found)}")
    print(
        "Isolated: listening on 127.0.0.1 only, the hue and webostv stubs loaded,"
        f" none of {sorted(DISCOVERY)} loaded."
    )


def _request(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    form: dict[str, str] | None = None,
    token: str | None = None,
) -> Any:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        HA_URL + path, data=data, headers=headers, method=method
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read() or b"null")


def _spawn(name: str, args: list[str]) -> None:
    """Start a process in the background, with its output in run/<name>.log."""
    with open(RUN / f"{name}.log", "wb") as log:
        process = subprocess.Popen(
            [str(_python()), *args],
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            # Not the repository: its custom_components would hide the stubs.
            cwd=CONFIG,
            start_new_session=True,
        )
    (RUN / f"{name}.pid").write_text(str(process.pid))


def _running() -> bool:
    return any(
        (pid_file := RUN / f"{name}.pid").exists() and _alive(int(pid_file.read_text()))
        for name in ("homeassistant", "bridge")
    )


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _python() -> Path:
    return VENV / "bin" / "python"


if __name__ == "__main__":
    main()
