"""The links Glowcast shows in Home Assistant lead somewhere (#253).

Every URL constant in Glowcast's modules (the repair issues' troubleshooting
anchor and hardware report template, the flows' help links) and the
manifest's documentation link must map to a page of the website, with its
heading for an #anchor, or to an issue template in this repository. Checked
against the sources, so it needs no built site and no network.
"""

import importlib
import json
import pkgutil
import re
import unicodedata
import urllib.parse
from pathlib import Path

import pytest

import custom_components.glowcast as glowcast

REPO = Path(__file__).resolve().parent.parent
SITE_URL = "https://nicohabets.github.io/glowcast/"
NEW_ISSUE_URL = "https://github.com/nicohabets/glowcast/issues/new"
PAGES = REPO / "website" / "pages"
TEMPLATES = REPO / ".github" / "ISSUE_TEMPLATE"
HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)
EXPLICIT_ID = re.compile(r"\{\s*#([\w-]+)\s*\}\s*$")


def _links() -> list[str]:
    """Every module-level URL to the website or a new issue."""
    links: set[str] = set()
    prefix = f"{glowcast.__name__}."
    for module_info in pkgutil.walk_packages(glowcast.__path__, prefix):
        module = importlib.import_module(module_info.name)
        for value in vars(module).values():
            if isinstance(value, str) and value.startswith((SITE_URL, NEW_ISSUE_URL)):
                links.add(value)
    manifest = json.loads(
        (Path(glowcast.__file__).parent / "manifest.json").read_text()
    )
    links.add(manifest["documentation"])
    return sorted(links)


def _slug(heading: str) -> str:
    """The id Python-Markdown's toc extension gives a heading."""
    if match := EXPLICIT_ID.search(heading):
        return match.group(1)
    text = unicodedata.normalize("NFKD", heading).encode("ascii", "ignore")
    text = re.sub(r"[^\w\s-]", "", text.decode("ascii")).strip().lower()
    return re.sub(r"[-\s]+", "-", text)


def test_the_links_are_found() -> None:
    """The scan sees the links the repair issues and flows use."""
    from custom_components.glowcast.outputs import ha_lights_flow
    from custom_components.glowcast.sources import webos

    links = _links()
    assert webos.HARDWARE_REPORT_URL in links
    assert webos.TROUBLESHOOTING_URL in links
    assert ha_lights_flow.ENTERTAINMENT_AREA_HELP in links


@pytest.mark.parametrize("url", _links())
def test_link_leads_to_a_page_or_template(url: str) -> None:
    parts = urllib.parse.urlsplit(url)
    if url.startswith(NEW_ISSUE_URL):
        [template] = urllib.parse.parse_qs(parts.query)["template"]
        assert (TEMPLATES / template).is_file(), url
        return
    path = parts.path.removeprefix(urllib.parse.urlsplit(SITE_URL).path)
    page = PAGES / (f"{path}index.md" if path.endswith("/") or not path else path)
    if not page.is_file():
        page = PAGES / f"{path.rstrip('/')}.md"
    assert page.is_file(), f"{url}: no page in website/pages"
    if parts.fragment:
        headings = HEADING.findall(page.read_text(encoding="utf-8"))
        anchors = {_slug(heading) for heading in headings}
        assert parts.fragment in anchors, f"{url}: no such heading"
