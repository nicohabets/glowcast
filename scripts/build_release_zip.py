"""Build glowcast.zip, the release asset HACS installs (#189).

HACS (`zip_release` in hacs.json) downloads the asset named in `filename` from
the release and unpacks it into custom_components/glowcast/, so the zip holds
the contents of that folder at its root: manifest.json, __init__.py, brand/ and
the rest, without a custom_components/glowcast/ prefix. Python caches are left
out.

Usage: python3 scripts/build_release_zip.py [output [source]]
(default: glowcast.zip from this checkout's custom_components/glowcast/; the
release workflow passes the source of the release's tag)
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SOURCE = REPO / "custom_components" / "glowcast"


def files_to_zip(source: Path) -> list[Path]:
    """The integration's files, relative to its folder, in a stable order."""
    return sorted(
        path.relative_to(source)
        for path in source.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix not in (".pyc", ".pyo")
    )


def build(source: Path, output: Path) -> list[str]:
    """Write the zip and return the names in it."""
    names = [path.as_posix() for path in files_to_zip(source)]
    if "manifest.json" not in names:
        raise SystemExit(f"no manifest.json in {source}")
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
        for name in names:
            zip_file.write(source / name, name)
    return names


def main(argv: list[str]) -> int:
    output = Path(argv[0]) if argv else Path("glowcast.zip")
    source = Path(argv[1]) if len(argv) > 1 else SOURCE
    names = build(source, output)
    print(f"{output}: {len(names)} files")
    for name in names:
        print(f"  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
