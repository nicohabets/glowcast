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

import dataclasses
import json
import os
import shutil
import sys
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
# Loose lights (#9): proxy lights under stub `hue` and `matter` entries
# (stubs/proxy.py), and the service that stores Glowcast's lights.
STUBS = Path(__file__).resolve().parent / "stubs"
PROXY = "bench_proxy"  # the stub entries' data key (stubs/proxy.py)
LOOSE_CONFIGURATION = """\
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


def loose_glowcast_entry(
    *, helper: dict[str, Any], settings: dict[str, Any]
) -> dict[str, Any]:
    """Glowcast's entry with the loose-lights output (no `bridge` in data).

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


def proxy_block(url: str, token: str, lights: list[dict[str, str]]) -> dict[str, Any]:
    """A stub entry's proxy lights: [{"unique_id", "name", "source"}]."""
    return {"url": url, "token": token, "lights": lights}


def _add_loose_stubs(inst: demo.Instance) -> None:
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
) -> tuple[demo.Instance, str]:
    """A fresh config with the entries, Home Assistant started; returns a token.

    `proxies` ({"hue": block, "matter": block}, see proxy_block): proxy lights
    for loose lights under the stub `hue` entry and a stub `matter` entry.
    """
    inst = instance()
    stop()
    with demo._lock():
        demo._install(browser=False)  # the bench needs no Chromium
        port = demo._free_port(HA_PORT, set())
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
            + (LOOSE_CONFIGURATION if proxies is not None else "")
        )
        hue_data: dict[str, Any] = {
            "host": bridge_host,
            "api_key": "unused-by-glowcast",
            "api_version": 2,
        }
        entries = [demo._entry("hue", "Hue Bridge", BRIDGE_UNIQUE_ID, hue_data)]
        if proxies is not None:
            _add_loose_stubs(inst)
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
        demo._spawn(
            inst,
            "homeassistant",
            ["-m", "homeassistant", "--config", str(inst.config), "--skip-pip"],
        )
    try:
        demo._wait_for_ha(inst)
        token = demo._onboard(inst)
        demo._wait_until_running(inst, token)
    except BaseException:
        stop()
        raise
    return inst, token


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
