"""A local Home Assistant with Glowcast from the working copy, for review GIFs.

Nothing in it reaches a real TV or bridge: stub hue and webostv integrations
report their entries as loaded, a fake bridge on 127.0.0.1 answers the setup
flow, and Home Assistant listens on 127.0.0.1 only, without default_config or
any discovery integration. See scripts/README.md.

Each working copy runs its own instance, with its own ports, config directory
and recordings; the virtual environment is shared.

Usage:
    python scripts/demo.py start
    python scripts/demo.py record [FLOW ...] [--lang en|nl] [--theme light|dark]
                                  [--still] [--out DIR]
    python scripts/demo.py stop
    python scripts/demo.py clean
    python scripts/demo.py clean --all [--yes]
    python scripts/demo.py status

start, record, stop and clean take --instance NAME to act on another instance
than this working copy's.

Runs on Linux and macOS (on Windows: in WSL). `start` needs Python 3.14.2 or
newer, or uv (https://docs.astral.sh/uv/) to fetch it. Everything it makes
lives in ~/.cache/glowcast-demo, outside the repository.
"""

import argparse
import contextlib
import dataclasses
import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
DEMO = REPO / "scripts" / "demo"
HOME = Path.home() / ".cache" / "glowcast-demo"
VENV = HOME / "venv"  # shared by all instances
INSTANCES = HOME / "instances"

# The first instance gets these if they are free; the others get free ones.
HA_PORT = 8199
BRIDGE_PORT = 8443
# A demo from before instances kept its state in HOME itself.
LEGACY = "legacy"
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

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


@dataclasses.dataclass(frozen=True)
class Instance:
    """One demo: its folder, and the working copy and ports it got at `start`."""

    name: str
    home: Path

    @property
    def legacy(self) -> bool:
        return self.name == LEGACY

    @property
    def config(self) -> Path:
        return self.home / "config"

    @property
    def run(self) -> Path:
        return self.home / "run"

    @property
    def recordings(self) -> Path:
        return self.home / "recordings"

    @property
    def state(self) -> dict[str, Any]:
        """The working copy and ports; empty before the first `start`."""
        if self.legacy:
            # Its code always used the default ports.
            state: dict[str, Any] = {"ha_port": HA_PORT, "bridge_port": BRIDGE_PORT}
            glowcast = self.config / "custom_components" / "glowcast"
            with contextlib.suppress(OSError):
                state["repo"] = str(Path(os.readlink(glowcast)).parent.parent)
            return state
        with contextlib.suppress(OSError):
            return json.loads((self.home / "state.json").read_text())
        return {}

    @property
    def ha_port(self) -> int:
        return self.state["ha_port"]

    @property
    def bridge_port(self) -> int:
        return self.state["bridge_port"]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.ha_port}"


def main() -> None:
    """Run one command."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    start_ = commands.add_parser(
        "start", help="start Home Assistant and the fake bridge"
    )
    record = commands.add_parser("record", help="record GIFs of the flows")
    record.add_argument(
        "flows", nargs="*", metavar="FLOW", help="flows in demo/record.py (all)"
    )
    record.add_argument("--lang", choices=["en", "nl"], default="en")
    record.add_argument("--theme", choices=["light", "dark"], default="light")
    record.add_argument(
        "--still", action="store_true", help="a PNG of each step instead of a GIF"
    )
    record.add_argument(
        "--out", type=Path, help="where to save them (the instance's recordings)"
    )
    stop_ = commands.add_parser("stop", help="stop Home Assistant and the fake bridge")
    clean = commands.add_parser(
        "clean", help="stop the instance and remove everything it made"
    )
    clean.add_argument(
        "--all",
        action="store_true",
        help="stop all instances and remove everything, the environment too",
    )
    clean.add_argument("--yes", action="store_true", help="do not ask to confirm")
    for command in (start_, record, stop_, clean):
        command.add_argument(
            "--instance",
            metavar="NAME",
            help=f"another instance than this working copy's ({_default_name()})",
        )
    commands.add_parser("status", help="list all instances")
    args = parser.parse_args()

    if args.command == "status":
        status()
        return
    if args.command == "clean" and args.all:
        clean_all(confirmed=args.yes)
        return
    instance = _instance(args.instance)
    print(f"Instance {instance.name} ({instance.home})", flush=True)
    if args.command == "start":
        start(instance)
    elif args.command == "record":
        record_flows(instance, args)
    elif args.command == "stop":
        stop(instance)
    else:
        stop(instance)
        # The legacy demo's folder also holds the shared environment.
        folders = (
            [instance.config, instance.run, instance.recordings]
            if instance.legacy
            else [instance.home]
        )
        for folder in folders:
            shutil.rmtree(folder, ignore_errors=True)
            print(f"Removed {folder}")


def start(instance: Instance) -> None:
    """Install if needed, write a fresh config and start both processes."""
    if instance.legacy:
        sys.exit(
            "The legacy demo cannot be started any more; `start` without "
            "--instance starts this working copy's instance."
        )
    if _running(instance):
        sys.exit(f"Instance {instance.name} is already running; run `stop` first.")
    with _lock("install"):
        _install()
    try:
        # Until both processes run, another `start` could pick the same ports.
        with _lock("ports"):
            ha_port, bridge_port = _pick_ports(instance)
            instance.home.mkdir(parents=True, exist_ok=True)
            (instance.home / "state.json").write_text(
                json.dumps(
                    {"repo": str(REPO), "ha_port": ha_port, "bridge_port": bridge_port}
                )
            )
            _write_config(instance)
            instance.run.mkdir(parents=True, exist_ok=True)
            _spawn(
                instance,
                "bridge",
                [
                    str(DEMO / "fake_bridge.py"),
                    str(instance.run / "bridge"),
                    str(bridge_port),
                ],
            )
            _spawn(
                instance,
                "homeassistant",
                # No installs at start-up: the environment is shared.
                ["-m", "homeassistant", "--config", str(instance.config), "--skip-pip"],
            )
        print(f"Waiting for Home Assistant on {instance.url}...", flush=True)
        _wait_for_ha(instance)
        token = _onboard(instance)
        _wait_until_running(instance, token)
        _check_isolated(instance, token)
    except BaseException:
        # Also on sys.exit() and Ctrl-C: leave nothing running.
        stop(instance)
        raise
    print(
        f"Ready: {instance.url} (user {USERNAME!r}, password {PASSWORD!r}).\n"
        "Glowcast is not set up yet; `record` runs the setup flow."
    )


def record_flows(instance: Instance, args: argparse.Namespace) -> None:
    """Run the recording script in the demo's environment."""
    if not _running(instance):
        sys.exit(f"Instance {instance.name} is not running; run `start` first.")
    started_from = (instance.config / "custom_components" / "glowcast").resolve()
    if started_from != (REPO / "custom_components" / "glowcast").resolve():
        sys.exit(
            f"Instance {instance.name} uses Glowcast from {started_from}; "
            "run `record` from that working copy."
        )
    out = args.out or instance.recordings
    out.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            str(_python()),
            str(DEMO / "record.py"),
            "--url",
            instance.url,
            "--bridge",
            f"https://127.0.0.1:{instance.bridge_port}",
            "--user",
            USERNAME,
            "--password",
            PASSWORD,
            "--lang",
            args.lang,
            "--theme",
            args.theme,
            "--out",
            str(out),
            *(["--still"] if args.still else []),
            *args.flows,
        ],
    )
    sys.exit(result.returncode)


def stop(instance: Instance) -> None:
    """Stop both processes, Home Assistant first."""
    for name in ("homeassistant", "bridge"):
        if (pid := _pid(instance, name)) is not None:
            os.kill(pid, signal.SIGTERM)
            for _ in range(60):
                if _pid(instance, name) is None:
                    break
                time.sleep(0.5)
            else:
                os.kill(pid, signal.SIGKILL)
            print(f"Stopped {name} of instance {instance.name}")
        (instance.run / f"{name}.pid").unlink(missing_ok=True)


def status() -> None:
    """A table of all instances; * marks this working copy's."""
    instances = _instances()
    if not instances:
        print(f"No instances in {HOME}")
        return
    rows = [["", "INSTANCE", "WORKING COPY", "HA", "BRIDGE", "RUNNING", "URL"]]
    for instance in instances:
        state = instance.state
        repo = state.get("repo", "-")
        if repo != "-" and not Path(repo).exists():
            repo += " (gone)"
        running = _running(instance)
        rows.append(
            [
                "*" if instance.name == _default_name() else "",
                instance.name,
                repo,
                str(state.get("ha_port", "-")),
                str(state.get("bridge_port", "-")),
                "yes" if running else "no",
                instance.url if running else "-",
            ]
        )
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    for row in rows:
        print(
            "  ".join(
                cell.ljust(width) for cell, width in zip(row, widths, strict=True)
            ).rstrip()
        )


def clean_all(*, confirmed: bool) -> None:
    """Stop every instance and remove HOME, the shared environment too."""
    instances = _instances()
    running = [instance.name for instance in instances if _running(instance)]
    print(
        f"This stops {len(running)} running instance(s) {running} and removes "
        f"{HOME}: {len(instances)} instance(s), the shared environment and all "
        "recordings."
    )
    if not confirmed and input("Continue? [y/N] ").strip().lower() not in ("y", "yes"):
        sys.exit("Nothing removed.")
    for instance in instances:
        stop(instance)
    shutil.rmtree(HOME, ignore_errors=True)
    print(f"Removed {HOME}")


def _default_name() -> str:
    """This working copy's instance: its folder's name and a hash of its path."""
    folder = re.sub(r"[^A-Za-z0-9._-]", "-", REPO.name).lstrip("._-") or "glowcast"
    return f"{folder}-{hashlib.sha256(str(REPO).encode()).hexdigest()[:6]}"


def _instance(name: str | None) -> Instance:
    if name is None:
        name = _default_name()
    if name == LEGACY:
        return Instance(LEGACY, HOME)
    if not NAME.fullmatch(name):
        sys.exit(f"An instance name is letters, digits, '.', '_' and '-': {name!r}")
    return Instance(name, INSTANCES / name)


def _instances() -> list[Instance]:
    """Every instance with state, the legacy demo first."""
    found = []
    if (HOME / "config").exists() or (HOME / "run").exists():
        found.append(Instance(LEGACY, HOME))
    if INSTANCES.exists():
        found += [
            Instance(folder.name, folder)
            for folder in sorted(INSTANCES.iterdir())
            if folder.is_dir()
        ]
    return found


@contextlib.contextmanager
def _lock(name: str) -> Iterator[None]:
    """Hold HOME/<name>.lock, shared by all instances, waiting if need be."""
    HOME.mkdir(parents=True, exist_ok=True)
    with open(HOME / f"{name}.lock", "w") as file:
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(f"Waiting for another instance ({name})...", flush=True)
            fcntl.flock(file, fcntl.LOCK_EX)
        yield


def _pick_ports(instance: Instance) -> tuple[int, int]:
    """Home Assistant's and the bridge's port: the defaults if free, else others.

    Not a port of another running instance (Home Assistant binds its port only
    after a while), nor the defaults while a legacy demo exists: its code
    always uses them.
    """
    taken: set[int] = set()
    for other in _instances():
        if other.legacy:
            taken |= {HA_PORT, BRIDGE_PORT}
        elif other != instance and _running(other):
            taken |= {other.ha_port, other.bridge_port}
    ha_port = _free_port(HA_PORT, taken)
    return ha_port, _free_port(BRIDGE_PORT, taken | {ha_port})


def _free_port(preferred: int, taken: set[int]) -> int:
    port = preferred
    while True:
        if port not in taken:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                try:
                    probe.bind(("127.0.0.1", port))
                    return port
                except OSError:
                    pass
        # A port the system has free now.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]


def _install() -> None:
    """Install the pinned requirements; again only when a pin changed."""
    marker = VENV / "glowcast-demo.json"
    if marker.exists() and json.loads(marker.read_text()) == REQUIREMENTS:
        return
    if running := [i.name for i in _instances() if _running(i)]:
        sys.exit(
            f"The shared environment in {VENV} needs other versions, but "
            f"instances {running} use it; stop them first."
        )
    print(f"Installing Home Assistant into {VENV}...", flush=True)
    # A changed pin is installed into the existing environment.
    uv = shutil.which("uv")
    if not _python().exists():
        if sys.version_info >= PYTHON:
            subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
        elif uv:
            python = ".".join(str(part) for part in PYTHON[:2])
            subprocess.run([uv, "venv", "--python", python, str(VENV)], check=True)
        else:
            sys.exit(
                "The demo needs Python {}.{}.{} or newer, or uv to fetch it.".format(
                    *PYTHON
                )
            )
    if uv:
        install = [uv, "pip", "install", "--python", str(_python())]
    else:
        install = [str(_python()), "-m", "pip", "install"]
    subprocess.run([*install, *REQUIREMENTS], check=True)
    subprocess.run(
        [str(_python()), "-m", "playwright", "install", "chromium"], check=True
    )
    marker.write_text(json.dumps(REQUIREMENTS))


def _write_config(instance: Instance) -> None:
    """A fresh config directory: the TV and bridge entries, no Glowcast entry."""
    config = instance.config
    shutil.rmtree(config, ignore_errors=True)
    (config / ".storage").mkdir(parents=True)
    (config / "configuration.yaml").write_text(CONFIGURATION)
    entries = [
        _entry(
            "hue",
            "Hue Bridge",
            BRIDGE_UNIQUE_ID,
            {
                "host": f"127.0.0.1:{instance.bridge_port}",
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
    _write_storage(config, "core.config_entries", {"entries": entries}, minor_version=5)
    # Since 2026.10 HTTP settings from YAML start as a trial that reverts to the
    # default (port 8123 on every interface) after a while; stored settings
    # are used as they are.
    _write_storage(
        config,
        "http",
        {
            "stable": {
                "server_host": ["127.0.0.1"],
                "server_port": instance.ha_port,
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
        config,
        "onboarding",
        {"done": ["core_config", "analytics", "integration"]},
        version=4,
    )
    custom = config / "custom_components"
    custom.mkdir()
    for stub in ("hue", "webostv"):
        shutil.copytree(DEMO / "stubs" / stub, custom / stub)
    (custom / "glowcast").symlink_to(REPO / "custom_components" / "glowcast")


def _write_storage(
    config: Path,
    key: str,
    data: dict[str, Any],
    *,
    version: int = 1,
    minor_version: int = 1,
) -> None:
    (config / ".storage" / key).write_text(
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


def _wait_for_ha(instance: Instance) -> None:
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        if _pid(instance, "homeassistant") is None:
            sys.exit(
                f"Home Assistant stopped; see {instance.run / 'homeassistant.log'}"
            )
        try:
            _request(instance, "GET", "/api/onboarding")
            return
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            time.sleep(2)
    sys.exit("Home Assistant did not start in time")


def _wait_until_running(instance: Instance, token: str) -> None:
    """The API answers before start-up is done; wait for the rest."""
    deadline = time.monotonic() + 300
    while _request(instance, "GET", "/api/config", token=token)["state"] != "RUNNING":
        if time.monotonic() > deadline:
            sys.exit("Home Assistant did not finish starting in time")
        time.sleep(1)


def _onboard(instance: Instance) -> str:
    """Create the test user, the last onboarding step; return an access token."""
    client_id = f"{instance.url}/"
    user = _request(
        instance,
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
    return _request(
        instance,
        "POST",
        "/auth/token",
        form={
            "grant_type": "authorization_code",
            "code": user["auth_code"],
            "client_id": client_id,
        },
    )["access_token"]


def _check_isolated(instance: Instance, token: str) -> None:
    """Exit (start() then stops the demo) if it could reach or be reached."""
    # A pending HTTP config would be tried at the next start, then reverted.
    http = json.loads((instance.config / ".storage" / "http").read_text())["data"]
    if http["pending"] is not None:
        sys.exit("Stopped: Home Assistant has pending HTTP settings.")
    if address := _reachable_from_network(instance):
        sys.exit(f"Stopped: the demo answers on {address}, not only on 127.0.0.1.")
    log = (instance.config / "home-assistant.log").read_text()
    for stub in ("hue", "webostv"):
        if f"custom integration {stub} " not in log:
            sys.exit(f"Stopped: the {stub} stub was not loaded.")
    loaded = {
        component.split(".")[0]
        for component in _request(instance, "GET", "/api/config", token=token)[
            "components"
        ]
    }
    if found := loaded & DISCOVERY:
        sys.exit(f"Stopped: discovery integrations were loaded: {sorted(found)}")
    print(
        "Isolated: listening on 127.0.0.1 only, the hue and webostv stubs loaded,"
        f" none of {sorted(DISCOVERY)} loaded."
    )


def _reachable_from_network(instance: Instance) -> str | None:
    """Where the demo answers on this computer's network address, if anywhere."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            # Picks the outgoing interface; a UDP connect sends nothing.
            probe.connect(("192.0.2.1", 9))
        except OSError:
            return None  # no network
        address = probe.getsockname()[0]
    for port in (instance.ha_port, instance.bridge_port):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
            client.settimeout(1)
            if client.connect_ex((address, port)) == 0:
                return f"{address}:{port}"
    return None


def _request(
    instance: Instance,
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
        instance.url + path, data=data, headers=headers, method=method
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read() or b"null")


def _spawn(instance: Instance, name: str, args: list[str]) -> None:
    """Start a process in the background, with its output in run/<name>.log."""
    with open(instance.run / f"{name}.log", "wb") as log:
        process = subprocess.Popen(
            [str(_python()), *args],
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            # Not the repository: its custom_components would hide the stubs.
            cwd=instance.config,
            start_new_session=True,
        )
    (instance.run / f"{name}.pid").write_text(str(process.pid))


def _running(instance: Instance) -> bool:
    return any(_pid(instance, name) is not None for name in ("homeassistant", "bridge"))


def _pid(instance: Instance, name: str) -> int | None:
    """The pid of the instance's process `name` if it runs, else None."""
    pid_file = instance.run / f"{name}.pid"
    if not pid_file.exists():
        return None
    pid = int(pid_file.read_text())
    # A child of this process that has exited stays a zombie until reaped.
    with contextlib.suppress(ChildProcessError):
        if os.waitpid(pid, os.WNOHANG)[0] == pid:
            return None
    # After a reboot the pid can belong to another program, and every instance
    # runs the shared environment's Python: only the instance's own folder in
    # the command line tells its processes apart.
    command = subprocess.run(
        ["ps", "-ww", "-o", "command=", "-p", str(pid)],
        capture_output=True,
        text=True,
    ).stdout
    own = instance.config if name == "homeassistant" else instance.run / "bridge"
    return pid if str(VENV) in command and f"{own} " in f"{command.strip()} " else None


def _python() -> Path:
    return VENV / "bin" / "python"


if __name__ == "__main__":
    main()
