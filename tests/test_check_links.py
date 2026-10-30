"""The website's link checker (scripts/check_links.py, #167): links to the site,
to this repository's files and inside the blueprint import button, all checked
without the network."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

REPO = Path(__file__).resolve().parent.parent


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "check_links", REPO / "scripts" / "check_links.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cl = _load()
SITE = cl.SITE_URL
REPO_FILE = "https://github.com/nicohabets/glowcast/blob/main/"


def _site(tmp_path: Path) -> Path:
    page = tmp_path / "docs" / "status"
    page.mkdir(parents=True)
    (tmp_path / "index.html").write_text("<h1 id='top'>Home</h1>")
    (page / "index.html").write_text("<h2 id='the-status'>The status</h2>")
    return tmp_path


def test_a_link_to_a_page_and_anchor_of_the_built_site(tmp_path: Path) -> None:
    site = _site(tmp_path)
    assert cl.check(SITE, site, True) == (None, False)
    assert cl.check(f"{SITE}docs/status/#the-status", site, True) == (None, False)
    error, _ = cl.check(f"{SITE}docs/status/#gone", site, True)
    assert error is not None and "#gone" in error
    error, _ = cl.check(f"{SITE}docs/missing/", site, True)
    assert error is not None


def test_a_link_to_a_file_of_this_repository(tmp_path: Path) -> None:
    site = _site(tmp_path)
    assert cl.check(f"{REPO_FILE}LICENSE", site, True) == (None, False)
    error, _ = cl.check(f"{REPO_FILE}no/such/file.md", site, True)
    assert error is not None


def test_the_blueprint_inside_the_import_button_is_checked(tmp_path: Path) -> None:
    site = _site(tmp_path)
    button = (
        "https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url="
        "https%3A%2F%2Fraw.githubusercontent.com%2Fnicohabets%2Fglowcast%2Fmain%2F"
    )
    path = "blueprints%2Fautomation%2Fglowcast%2Fstart_with_tv.yaml"
    assert cl.check(button + path, site, True) == (None, False)
    error, _ = cl.check(button + "blueprints%2Fgone.yaml", site, True)
    assert error is not None and error.startswith("blueprint_url")


def test_markdown_links_with_parentheses_in_the_url() -> None:
    text = "[a](https://example.org/wiki/Foo_(bar)) and ([b](https://example.org/y))."
    assert cl.MARKDOWN_LINK.findall(text) == [
        "https://example.org/wiki/Foo_(bar)",
        "https://example.org/y",
    ]
