"""Check the links of the README and the built website (#167).

Usage, from the repository root, after `mkdocs build -f website/mkdocs.yml`:

    python3 scripts/check_links.py [--offline] [--site website/site] README.md

- Links to the website (https://nicohabets.github.io/glowcast/...) are checked
  against the built site, including the #anchor, so a README link to a page
  that this branch adds works before the site is published.
- Links to this repository's files on main (github.com/.../blob|tree/main/...,
  raw.githubusercontent.com/.../main/...) are checked against the working
  copy, which becomes main when the pull request merges.
- Every other http(s) link is fetched (GET, with retries), unless --offline.
  A 429 (rate limited) counts as a warning, not a failure.
- Relative links inside the site are mkdocs' job (`mkdocs build --strict`).

Only the standard library; exits 1 when a link is broken.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SITE_URL = "https://nicohabets.github.io/glowcast/"
REPO_PREFIXES = (
    "https://github.com/nicohabets/glowcast/blob/main/",
    "https://github.com/nicohabets/glowcast/tree/main/",
    "https://raw.githubusercontent.com/nicohabets/glowcast/main/",
)
# Links that are not pages to fetch.
SKIP_PREFIXES = ("mailto:",)
MARKDOWN_LINK = re.compile(r"\]\((https?://[^)\s]+)\)")
HTML_LINK = re.compile(r"""(?:href|src|srcset)="(https?://[^"\s]+)\"""")


class _Collector(HTMLParser):
    """Collects the ids and the external links of one HTML page."""

    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.links: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if value is None:
                continue
            if name in ("id", "name"):
                self.ids.add(value)
            # The canonical link of each page points at the live site.
            elif (
                name in ("href", "src")
                and value.startswith(("http://", "https://"))
                and not (tag == "link" and ("rel", "canonical") in attrs)
            ):
                self.links.add(value)


def _parse(path: Path) -> _Collector:
    collector = _Collector()
    collector.feed(path.read_text(encoding="utf-8"))
    return collector


def _check_site(url: str, site: Path) -> str | None:
    parts = urllib.parse.urlsplit(url)
    rel = urllib.parse.unquote(parts.path[len(urllib.parse.urlsplit(SITE_URL).path) :])
    target = site / rel
    if rel == "" or rel.endswith("/"):
        target = target / "index.html"
    if not target.is_file():
        return f"not in the built site ({target.relative_to(site)})"
    if (
        parts.fragment
        and target.suffix == ".html"
        and parts.fragment not in _parse(target).ids
    ):
        return f"no anchor #{parts.fragment} on that page"
    return None


def _check_repo(url: str, prefix: str) -> str | None:
    rel = urllib.parse.unquote(urllib.parse.urlsplit(url[len(prefix) :]).path)
    if not (REPO / rel).exists():
        return f"no {rel} in the repository"
    return None


def _check_web(url: str) -> tuple[str | None, bool]:
    """Return (error, rate_limited)."""
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "glowcast-link-check (+https://github.com/nicohabets/glowcast)"
        },
    )
    error = "unknown error"
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                response.read(1024)
            return None, False
        except urllib.error.HTTPError as err:
            if err.code == 429:
                return None, True
            error = f"HTTP {err.code}"
            if err.code < 500:
                break
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            error = str(getattr(err, "reason", err))
        time.sleep(2 * (attempt + 1))
    return error, False


def check(url: str, site: Path, offline: bool) -> tuple[str | None, bool]:
    """Check one link; return (error, warning)."""
    # The blueprint import button carries the blueprint's own URL.
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    if "blueprint_url" in query:
        error, warning = check(query["blueprint_url"][0], site, offline)
        if error:
            return f"blueprint_url: {error}", warning
    if url.startswith(SITE_URL):
        return _check_site(url, site), False
    for prefix in REPO_PREFIXES:
        if url.startswith(prefix):
            return _check_repo(url, prefix), False
    if offline:
        return None, False
    return _check_web(url)


def collect(files: list[Path], site: Path) -> dict[str, set[str]]:
    """Map each external link to the files it appears in."""
    found: dict[str, set[str]] = {}
    for path in files:
        text = path.read_text(encoding="utf-8")
        for url in MARKDOWN_LINK.findall(text) + HTML_LINK.findall(text):
            found.setdefault(url, set()).add(str(path))
    for page in sorted(site.rglob("*.html")):
        for url in _parse(page).links:
            found.setdefault(url, set()).add(str(page.relative_to(site)))
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="*", type=Path, default=[REPO / "README.md"])
    parser.add_argument("--site", type=Path, default=REPO / "website" / "site")
    parser.add_argument(
        "--offline", action="store_true", help="skip links to other sites"
    )
    args = parser.parse_args()
    if not (args.site / "index.html").is_file():
        print(f"No built site in {args.site}; run mkdocs build first.", file=sys.stderr)
        return 2

    links = collect(args.files, args.site)
    broken = 0
    for url in sorted(links):
        if url.startswith(SKIP_PREFIXES):
            continue
        error, warning = check(url, args.site, args.offline)
        where = ", ".join(sorted(links[url]))
        if error:
            broken += 1
            print(f"BROKEN {url} ({error}) in {where}")
        elif warning:
            print(f"WARNING {url} (rate limited, not checked) in {where}")
    print(f"{len(links)} links checked, {broken} broken.")
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
