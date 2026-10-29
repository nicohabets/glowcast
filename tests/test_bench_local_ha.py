"""The test bench's local Home Assistant (scripts/bench/local_ha.py).

A smoke test: it builds on scripts/demo.py's helpers, so a change there that
the bench relies on breaks CI, and the Glowcast entry it writes must be one
the screen source and the Hue output can be built from.
"""

import importlib.util
import inspect
import sys
from pathlib import Path
from types import ModuleType

from custom_components.glowcast.outputs import hue_entertainment
from custom_components.glowcast.settings import read_settings
from custom_components.glowcast.sources import screen

REPO = Path(__file__).resolve().parent.parent

# What local_ha.py uses of demo.py, with the parameters it passes.
DEMO_API = {
    "_default_name": [],
    "_lock": [],
    "_install": [],
    "_python": [],
    "_free_port": ["preferred", "taken"],
    "_write_config": ["instance"],
    "_write_storage": ["config", "key", "data"],
    "_entry": ["domain", "title", "unique_id", "data"],
    "_spawn": ["instance", "name", "args"],
    "_wait_for_ha": ["instance"],
    "_onboard": ["instance"],
    "_wait_until_running": ["instance", "token"],
    "stop": ["instance"],
}


def _load() -> ModuleType:
    path = REPO / "scripts" / "bench" / "local_ha.py"
    spec = importlib.util.spec_from_file_location("bench_local_ha", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_demo_helpers_it_uses_exist() -> None:
    local_ha = _load()
    demo = local_ha.demo
    for name, params in DEMO_API.items():
        function = getattr(demo, name)
        assert list(inspect.signature(function).parameters)[: len(params)] == params
    assert "CONFIGURATION" in vars(demo)
    instance = local_ha.instance()
    assert instance.home.parent == local_ha.INSTANCES
    assert ".cache/glowcast-bench" in str(instance.home)


def test_the_entry_builds_the_screen_source_and_the_hue_output() -> None:
    local_ha = _load()
    entry = local_ha.glowcast_entry(
        helper={"helper_id": "a" * 32, "host": "127.0.0.1", "port": 8489, "token": "t"},
        app_key="app-key",
        client_key="00" * 16,
        area_id="area-id",
        settings={"brightness": 80, "intensity": 100, "update_rate": "high"},
    )
    data, options = entry["data"], entry["options"]
    assert data["source"]["type"] == screen.SOURCE_SCREEN
    assert isinstance(screen.create(None, data, options), screen.ScreenFrameSource)
    assert options["output"]["type"] == hue_entertainment.OUTPUT_HUE_ENTERTAINMENT
    output = hue_entertainment.create(None, data, options)
    assert isinstance(output, hue_entertainment.HueEntertainmentOutput)
    assert data["bridge"]["unique_id"] == local_ha.BRIDGE_UNIQUE_ID
    assert read_settings(options).brightness == 80
