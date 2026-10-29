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


def instance() -> demo.Instance:
    """This working copy's bench instance."""
    return demo.Instance(demo._default_name(), INSTANCES / demo._default_name())


def install() -> None:
    """The demo's shared environment (Home Assistant), installed if needed."""
    with demo._lock():
        demo._install()


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


def start(bridge_host: str, glowcast: dict[str, Any]) -> tuple[demo.Instance, str]:
    """A fresh config with the entries, Home Assistant started; returns a token."""
    inst = instance()
    stop()
    with demo._lock():
        demo._install()
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
            demo.CONFIGURATION + EXTRA_CONFIGURATION
        )
        hue = demo._entry(
            "hue",
            "Hue Bridge",
            BRIDGE_UNIQUE_ID,
            {"host": bridge_host, "api_key": "unused-by-glowcast", "api_version": 2},
        )
        demo._write_storage(
            inst.config,
            "core.config_entries",
            {"entries": [hue, glowcast]},
            minor_version=5,
        )
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


def stop() -> None:
    inst = instance()
    if inst.state:
        demo.stop(inst)


def log_path() -> Path:
    return instance().config / "home-assistant.log"
