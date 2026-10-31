"""MkDocs hook: serve the repository's images on the site without copying them.

The illustrations, screenshots and brand files live in docs/images/ and
docs/brand/ (the README and the release notes use them too). This hook adds
them to the site's files as images/... and brand/..., so the pages link to
them as if they were in website/pages/, in `mkdocs serve` and `mkdocs build`
alike.
"""

from __future__ import annotations

from pathlib import Path

from mkdocs.config.defaults import MkDocsConfig
from mkdocs.structure.files import File, Files

DOCS = Path(__file__).resolve().parent.parent / "docs"

# What the site shows, relative to docs/. The large review GIFs and the
# screenshots of older releases stay out of the site.
SHARED = (
    "images/illustrations/*.svg",
    "images/illustrations/*.webp",
    "images/setup-flow.gif",
    "images/settings.png",
    "images/device-page.png",
    "images/ha-lights-setup.gif",
    "images/ha-lights-place.png",
    "images/combined-settings-ha-lights.gif",
    "images/settings-menu.png",
    "images/device-page-ha-lights.png",
    "images/liveliness-*",
    "brand/*.svg",
)


def on_files(files: Files, config: MkDocsConfig) -> Files:
    """Add the shared images from docs/ to the site's files."""
    for pattern in SHARED:
        for path in sorted(DOCS.glob(pattern)):
            files.append(
                File(
                    path.relative_to(DOCS).as_posix(),
                    src_dir=str(DOCS),
                    dest_dir=config.site_dir,
                    use_directory_urls=config.use_directory_urls,
                )
            )
    return files
