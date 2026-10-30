"""The release zip for HACS (scripts/build_release_zip.py, #189): HACS unpacks
it into custom_components/glowcast/, so the integration's files sit at the
root of the zip."""

import importlib.util
import json
import sys
import zipfile
from pathlib import Path
from types import ModuleType

REPO = Path(__file__).resolve().parent.parent


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "build_release_zip", REPO / "scripts" / "build_release_zip.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


build_release_zip = _load()


def test_zip_has_the_integration_at_its_root(tmp_path: Path) -> None:
    output = tmp_path / "glowcast.zip"
    build_release_zip.build(build_release_zip.SOURCE, output)

    with zipfile.ZipFile(output) as zip_file:
        names = zip_file.namelist()
        manifest = json.loads(zip_file.read("manifest.json"))

    assert manifest["domain"] == "glowcast"
    assert "__init__.py" in names
    assert "brand/icon.png" in names
    assert not any(name.startswith("custom_components/") for name in names)
    source_files = {
        path.relative_to(build_release_zip.SOURCE).as_posix()
        for path in build_release_zip.SOURCE.rglob("*.py")
        if "__pycache__" not in path.parts
    }
    assert source_files <= set(names)


def test_zip_leaves_out_python_caches(tmp_path: Path) -> None:
    source = tmp_path / "glowcast"
    (source / "__pycache__").mkdir(parents=True)
    (source / "manifest.json").write_text("{}")
    (source / "__init__.py").write_text("")
    (source / "__pycache__" / "x.cpython-314.pyc").write_bytes(b"")

    names = build_release_zip.build(source, tmp_path / "glowcast.zip")

    assert names == ["__init__.py", "manifest.json"]
