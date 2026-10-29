"""Home Assistant can find and load the integration's manifest."""

import ast
import json
from pathlib import Path

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration
from packaging.utils import canonicalize_name

from custom_components.glowcast.const import DOMAIN

ROOT = Path(__file__).resolve().parents[1]


async def test_integration_loads(
    hass: HomeAssistant, enable_custom_integrations: None
) -> None:
    """The manifest is valid for this Home Assistant version."""
    integration = await async_get_integration(hass, DOMAIN)

    assert integration.domain == DOMAIN
    assert integration.dependencies == ["webostv", "hue"]
    assert integration.requirements == ["hue-entertainment==0.1.2"]


def _demo_requirements() -> list[str]:
    """REQUIREMENTS from scripts/demo.py, read without running the demo."""
    tree = ast.parse((ROOT / "scripts" / "demo.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "REQUIREMENTS"
            for target in node.targets
        ):
            return ast.literal_eval(node.value)
    pytest.fail("scripts/demo.py has no REQUIREMENTS list")


def test_demo_installs_manifest_requirements() -> None:
    """The demo installs every manifest requirement, as it runs with --skip-pip."""
    manifest = json.loads(
        (ROOT / "custom_components" / "glowcast" / "manifest.json").read_text()
    )
    demo = _demo_requirements()
    demo_by_name = {
        canonicalize_name(requirement.split("==")[0]): requirement
        for requirement in demo
    }

    problems = []
    for requirement in manifest["requirements"]:
        if requirement in demo:
            continue
        found = demo_by_name.get(canonicalize_name(requirement.split("==")[0]))
        if found:
            problems.append(f"{requirement} differs: the demo has {found}")
        else:
            problems.append(f"{requirement} is missing from the demo")

    assert not problems, (
        "\n".join(problems)
        + "\nmanifest.json and REQUIREMENTS in scripts/demo.py must change"
        " together: the demo runs Home Assistant with --skip-pip (#118)."
    )
