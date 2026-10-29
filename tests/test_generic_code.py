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
TYPE_MODULES = re.compile(r"hue_entertainment|hue_area_select|webos")
TYPE_WORDS = re.compile(r"hue|webos|webostv|bridge|entertainment|\barea", re.I)


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
