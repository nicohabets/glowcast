"""Home Assistant can load the manifest, and the demo installs its requirements."""

import ast
import json
from pathlib import Path

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from custom_components.glowcast.const import DOMAIN

ROOT = Path(__file__).resolve().parents[1]


async def test_integration_loads(
    hass: HomeAssistant, enable_custom_integrations: None
) -> None:
    """The manifest is valid for this Home Assistant version."""
    integration = await async_get_integration(hass, DOMAIN)

    assert integration.domain == DOMAIN
    assert integration.dependencies == ["webostv"]
    # Hue is optional since loose lights (#9): set up first when it is there.
    assert integration.after_dependencies == ["hue"]
    assert integration.requirements == ["hue-entertainment==0.1.2"]


def _demo_requirements() -> list[str]:
    """REQUIREMENTS from scripts/demo.py, read without running the demo."""
    tree = ast.parse((ROOT / "scripts" / "demo.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign) and node.value:
            targets = [node.target]
        else:
            continue
        if any(
            isinstance(target, ast.Name) and target.id == "REQUIREMENTS"
            for target in targets
        ):
            return ast.literal_eval(node.value)
    pytest.fail("scripts/demo.py has no REQUIREMENTS list")


def test_demo_installs_manifest_requirements() -> None:
    """The demo installs every manifest requirement, as it runs with --skip-pip."""
    manifest = json.loads(
        (ROOT / "custom_components" / "glowcast" / "manifest.json").read_text()
    )
    # Names as pip compares them: hue_entertainment is hue-entertainment.
    demo = {
        canonicalize_name(requirement.name): requirement
        for requirement in map(Requirement, _demo_requirements())
    }

    problems = []
    for requirement in map(Requirement, manifest["requirements"]):
        found = demo.get(canonicalize_name(requirement.name))
        if found is None:
            problems.append(f"{requirement} is missing from the demo")
        elif found != requirement:
            problems.append(f"{requirement} differs: the demo has {found}")

    assert not problems, (
        "\n".join(problems)
        + "\nmanifest.json and REQUIREMENTS in scripts/demo.py must change"
        " together: the demo runs Home Assistant with --skip-pip (#118)."
    )
