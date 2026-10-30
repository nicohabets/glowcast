"""The generic code knows no source or output type (architecture 8, #66).

Only `sources/` knows webOS and only `outputs/` knows Hue; that includes the
setup and options flows. The registries in `sources/__init__.py` and
`outputs/__init__.py` are the only place where the types are named.
"""

import ast
import re
from pathlib import Path

import pytest

PACKAGE = Path(__file__).parent.parent / "custom_components/glowcast"
# Every module outside sources/, outputs/ and core/ (core/ has its own rule).
GENERIC = sorted(path.name for path in PACKAGE.glob("*.py"))
TYPE_MODULES = re.compile(r"hue_entertainment|hue_area_select|webos|ha_lights")
TYPE_WORDS = re.compile(
    r"hue|webos|webostv|bridge|entertainment|\barea|ha_lights|loose", re.I
)
# How the Home Assistant lights set Home Assistant's lights: light entity ids and
# actions, and the colour fields of light.turn_on (#9).
LIGHT_SERVICE = re.compile(r"\blight\.[a-z_]|\bturn_on\b|rgb_color|hs_color|xy_color")
CORE = sorted((PACKAGE / "core").glob("*.py"))


def imported_modules(path: Path) -> list[str]:
    """Every module a file imports, with relative imports as written."""
    modules: list[str] = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            modules += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            module = "." * node.level + (node.module or "")
            modules += [f"{module}.{alias.name}" for alias in node.names]
    return modules


def test_the_generic_modules_are_the_expected_ones() -> None:
    """A new generic module is checked too; this list only guards the glob."""
    assert {"config_flow.py", "flow.py", "runtime.py", "__init__.py"} <= set(GENERIC)


@pytest.mark.parametrize("name", GENERIC)
def test_no_imports_from_a_type(name: str) -> None:
    imports = imported_modules(PACKAGE / name)
    assert [module for module in imports if TYPE_MODULES.search(module)] == []


@pytest.mark.parametrize("name", GENERIC)
def test_no_hue_or_webos_wording(name: str) -> None:
    """No keys, step ids, checks or texts of a type, also not in comments."""
    assert TYPE_WORDS.findall((PACKAGE / name).read_text()) == []


@pytest.mark.parametrize("name", GENERIC)
def test_no_light_service_details(name: str) -> None:
    """Only the Home Assistant lights output knows how Home Assistant's lights
    are set."""
    assert LIGHT_SERVICE.findall((PACKAGE / name).read_text()) == []


@pytest.mark.parametrize(
    "path", [*CORE, PACKAGE / "outputs/composite.py"], ids=lambda path: path.name
)
def test_core_does_not_import_home_assistant(path: Path) -> None:
    """core/ is tested with plain fakes, without Home Assistant (CLAUDE.md);
    so is the combined output (#11), as its docstring says."""
    imports = imported_modules(path)
    roots = {module.split(".")[0] for module in imports}
    assert "homeassistant" not in roots


def test_the_core_modules_are_found() -> None:
    """This list only guards the glob."""
    assert {"pipeline.py", "ports.py", "models.py"} <= {path.name for path in CORE}


# The combined output (#11) and the blocks it is stored in are generic too.
COMBINED = ["outputs/blocks.py", "outputs/composite.py", "outputs/composite_flow.py"]


@pytest.mark.parametrize("name", COMBINED)
def test_the_combined_output_knows_no_type(name: str) -> None:
    path = PACKAGE / name
    imports = imported_modules(path)
    assert [module for module in imports if TYPE_MODULES.search(module)] == []
    assert TYPE_WORDS.findall(path.read_text()) == []
    assert LIGHT_SERVICE.findall(path.read_text()) == []
