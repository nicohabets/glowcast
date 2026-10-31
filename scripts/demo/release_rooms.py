"""The room per release, for the website's testing pages (#293).

Run through `scripts/demo.py rooms`, in the demo's environment (as room.py).
For every release tag (all `vX.Y.Z` tags, plus `--next REF --next-label vX.Y.Z`
for a release that is not tagged yet), it exports that release's
`custom_components/glowcast/` with `git archive` into a temporary folder (the
tags and the working tree are not touched) and runs the current harness
(room_harness.py) against it, as `room --compare` does for main: with the
entertainment area, and with Home Assistant lights and combined for the
releases that have them. A release whose code cannot run a kind of lights is
recorded as not available, with the reason.

Each scenario of each release is compared with the previous release's: what is
sent to the lights, per channel and per send tick. Within TOLERANCE it is the
same and the release shows the earlier release's GIF; otherwise it gets a GIF
of its own: one room, labelled with the release. Only the scenarios that the
testing pages show get GIFs (testing_pages.toml, which names them by their
lights and their name without the number, so a renumbered scenario keeps its
texts). A GIF that exists already is kept (--force makes them all again), so a
new release adds only its own.

The GIFs go to website/pages/testing/rooms/<release>/, with manifest.json
beside them (what was made, what changed, what is not available). Then the
testing pages are written from the manifest and testing_pages.toml (the
scenarios' texts and what changed per release): the part of each page between
`<!-- rooms -->` and `<!-- /rooms -->`.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
PAGES = REPO / "website" / "pages" / "testing"
ROOMS = PAGES / "rooms"
TEXTS = Path(__file__).with_name("testing_pages.toml")
PACKAGE = "custom_components/glowcast"
TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
# As in room.py and room_harness.py.
LIGHTS = ("area", "ha", "combined")
EXIT_NO_OUTPUT = 3
# The largest difference (0-255, per colour component) that still counts as
# the same: rounding, nothing to see.
TOLERANCE = 2
# Smaller than room.py's defaults, to keep the committed GIFs small; the pages
# show them 400 px wide.
WIDTH = 600
STEP = 3  # every third send tick: 8.3 frames per second
MARKER = re.compile(r"(<!-- rooms -->\n).*?(<!-- /rooms -->)", re.S)
CHANGED = "●"
LEGEND = (
    f"{CHANGED}: what the lights get changed since the previous release. "
    "same: the same as in the previous release (within rounding). "
    "new: the first release with these lights. -: not available."
)
OUTPUT_NAMES = {
    "area": "An entertainment area",
    "ha": "Home Assistant lights",
    "combined": "An entertainment area and Home Assistant lights together",
}


# --- Releases and names -----------------------------------------------------


def version(tag: str) -> tuple[int, int, int]:
    match = TAG.match(tag)
    if match is None:
        raise ValueError(f"{tag} is not a release tag (vX.Y.Z)")
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def releases(
    tags: list[str], next_ref: str | None = None, next_label: str | None = None
) -> list[tuple[str, str]]:
    """(label, git ref) per release, oldest first: the release tags, and the
    next release at `next_ref` while its tag does not exist."""
    found = sorted({tag for tag in tags if TAG.match(tag)}, key=version)
    result = [(tag, tag) for tag in found]
    if next_ref and next_label and next_label not in found:
        version(next_label)
        result.append((next_label, next_ref))
        result.sort(key=lambda release: version(release[0]))
    return result


def short(name: str) -> str:
    """A scenario's name without its number: "1 Sunset" -> "Sunset"."""
    return re.sub(r"^\d+\s+", "", name)


def slug(lights: str, name: str) -> str:
    """A scenario's anchor and file name: ("area", "4:3 picture") ->
    "area-43-picture"."""
    words = re.sub(r"[^a-z0-9]+", "-", short(name).lower().replace(":", ""))
    return f"{lights}-{words.strip('-')}"


def gif_path(release: str, lights: str, name: str) -> str:
    """Where a scenario's GIF goes, relative to the rooms folder."""
    return f"{release}/{slug(lights, name)}.gif"


def split(key: str) -> tuple[str, str]:
    """A testing_pages.toml key, "<lights>:<name>", as (lights, name)."""
    lights, _, name = key.partition(":")
    if lights not in LIGHTS or not name:
        raise ValueError(f"{key!r} is not <lights>:<scenario name>")
    return lights, name


def wanted(texts: dict[str, Any]) -> dict[str, list[str]]:
    """The scenarios the pages show (names without number), per kind of
    lights."""
    result: dict[str, list[str]] = {}
    for group in texts["groups"]:
        for key in group["scenarios"]:
            lights, name = split(key)
            result.setdefault(lights, []).append(name)
    return result


def numbers(recording: dict[str, Any]) -> dict[str, int]:
    """{name without number: the scenario's number (1 is the first)}."""
    return {short(s["name"]): n for n, s in enumerate(recording["scenarios"], 1)}


# --- What changed -----------------------------------------------------------


def difference(old: list[dict[str, Any]], new: list[dict[str, Any]]) -> int | None:
    """The largest difference between two runs of a scenario, in 0-255 per
    colour component over every channel and send tick (a light that is off
    counts as black). None when they cannot be compared tick by tick, or a
    light is unavailable or a part dropped in one and not in the other."""
    if len(old) != len(new):
        return None
    largest = 0.0
    for a, b in zip(old, new, strict=True):
        for key in ("unavailable", "retrying", "dropped"):
            if a.get(key, []) != b.get(key, []):
                return None
        lights_a, lights_b = a.get("lights", {}), b.get("lights", {})
        for channel in set(lights_a) | set(lights_b):
            ca = lights_a.get(channel) or [0.0, 0.0, 0.0]
            cb = lights_b.get(channel) or [0.0, 0.0, 0.0]
            for x, y in zip(ca, cb, strict=True):
                largest = max(largest, abs(x - y))
    return round(largest * 255)


def scenario_samples(recording: dict[str, Any], number: int) -> list[dict[str, Any]]:
    """The samples of scenario `number` (1 is the first)."""
    return [s for s in recording["samples"] if s["scenario"] == number - 1]


def entry(
    release: str,
    lights: str,
    name: str,
    recording: dict[str, Any],
    previous: tuple[dict[str, Any], dict[str, Any]] | None,
) -> dict[str, Any]:
    """Scenario `name` (without number) of a release, for the manifest: "new"
    (the first release that runs it), "changed" or "same" against `previous`
    (the previous release's recording and entry), the largest difference, and
    its GIF: an unchanged scenario shows the GIF of the release since which
    it is the same."""
    own = {"gif": gif_path(release, lights, name), "since": release}
    if previous is None or name not in numbers(previous[0]):
        return {"status": "new", "difference": None, **own}
    old_recording, old_entry = previous
    diff = difference(
        scenario_samples(old_recording, numbers(old_recording)[name]),
        scenario_samples(recording, numbers(recording)[name]),
    )
    if diff is None or diff > TOLERANCE:
        return {"status": "changed", "difference": diff, **own}
    return {
        "status": "same",
        "difference": diff,
        "gif": old_entry["gif"],
        "since": old_entry["since"],
    }


# --- Running a release ------------------------------------------------------


def export(ref: str, folder: Path) -> Path:
    """`ref`'s Glowcast package in `folder`, without touching the working tree;
    returns its core/."""
    archive = subprocess.run(
        ["git", "-C", str(REPO), "archive", "--format=tar", ref, PACKAGE],
        check=True,
        capture_output=True,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(folder, filter="data")
    return folder / PACKAGE / "core"


def run_release(
    room: Any, ref: str, lights: str, tmp: Path
) -> tuple[dict[str, Any] | None, dict[str, str] | None]:
    """(recording, None), or (None, why it is not available)."""
    name = ref.replace("/", "_")
    core = export(ref, tmp / name)
    out = tmp / f"{name}-{lights}.json"
    result = room._record(core, out, lights, [])
    if result.returncode == EXIT_NO_OUTPUT:
        return None, {"why": "no_output", "detail": room._last_line(result.stderr)}
    if result.returncode != 0:
        return None, {"why": "error", "detail": room._last_line(result.stderr)}
    return json.loads(out.read_text()), None


def room_label(room: Any, release: str, lights: str) -> str:
    mode = room.MODE_LABELS.get(lights)
    return f"{release} - {mode}" if mode else release


def write_gif(
    room: Any, recording: dict[str, Any], label: str, number: int, gif: Path
) -> None:
    """One room, scenario `number` (1 is the first), labelled `label`."""
    painter = room.Painter(recording)
    samples = recording["samples"]
    title = recording["scenarios"][number - 1]["name"]
    indexes = [i for i, s in enumerate(samples) if s["scenario"] == number - 1]

    def frame(index: int) -> Any:
        return room.compose(
            [(label, painter.room(index))], title, [room.notes(samples[index])]
        )

    gif.parent.mkdir(parents=True, exist_ok=True)
    room._write_gif(frame, indexes, gif)


def make(
    room: Any,
    chosen: list[tuple[str, str]],
    scenarios: dict[str, list[str]],
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Record every release, write the GIFs that are new or changed (and not
    there yet, unless `force`), and return the manifest."""
    manifest: dict[str, Any] = {
        "tolerance": TOLERANCE,
        "releases": [label for label, _ in chosen],
        "lights": {},
    }
    with tempfile.TemporaryDirectory() as folder:
        tmp = Path(folder)
        for lights, names in scenarios.items():
            kind: dict[str, Any] = {"names": {}, "releases": {}}
            manifest["lights"][lights] = kind
            previous: dict[str, Any] | None = None
            previous_entries: dict[str, dict[str, Any]] = {}
            for label, ref in chosen:
                print(f"{label} ({ref}), {lights}", flush=True)
                recording, reason = run_release(room, ref, lights, tmp)
                if recording is None:
                    kind["releases"][label] = {"available": False, **(reason or {})}
                    print(f"  not available: {reason}")
                    continue
                found = numbers(recording)
                entries = {}
                for name in names:
                    if name not in found:
                        continue
                    # The full name, with its number in the newest release.
                    full = recording["scenarios"][found[name] - 1]["name"]
                    kind["names"][name] = full
                    old = None
                    if previous is not None and name in previous_entries:
                        old = (previous, previous_entries[name])
                    entries[name] = new = entry(label, lights, name, recording, old)
                    gif = ROOMS / new["gif"]
                    if new["since"] == label and (force or not gif.exists()):
                        title = room_label(room, label, lights)
                        write_gif(room, recording, title, found[name], gif)
                kind["releases"][label] = {"available": True, "scenarios": entries}
                previous, previous_entries = recording, entries
    return manifest


# --- The pages --------------------------------------------------------------


def lookup(
    manifest: dict[str, Any], lights: str, name: str, release: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """(the release's info for these lights, the scenario's entry)."""
    info = manifest["lights"].get(lights, {"releases": {}})["releases"].get(release)
    if info is None or not info["available"]:
        return info, None
    return info, info["scenarios"].get(name)


def title(manifest: dict[str, Any], texts: dict[str, Any], key: str) -> str:
    """A scenario's heading: its number and name in the newest release, with
    the kind of lights from testing_pages.toml in front (if any)."""
    lights, name = split(key)
    full = manifest["lights"].get(lights, {}).get("names", {}).get(name, name)
    prefix = texts["scenarios"][key].get("prefix")
    return f"{prefix}: {full}" if prefix else full


def cell(scenario: dict[str, Any] | None, href: str) -> str:
    """A table cell: a link to the scenario's tabs."""
    if scenario is None:
        return "-"
    if scenario["status"] == "changed":
        return f'[{CHANGED}]({href} "changed"){{ .glowcast-changed }}'
    return f"[{scenario['status']}]({href})"


def table(
    manifest: dict[str, Any], texts: dict[str, Any], rows: list[tuple[str, str]]
) -> list[str]:
    """Scenarios (rows) per release (columns, newest first). A row: (the
    scenario's key, the page with its tabs or "" for this page)."""
    newest = list(reversed(manifest["releases"]))
    lines = [
        "| Scenario | " + " | ".join(newest) + " |",
        "|---|" + "---|" * len(newest),
    ]
    for key, page in rows:
        lights, name = split(key)
        href = f"{page}#{slug(lights, name)}"
        cells = [cell(lookup(manifest, lights, name, r)[1], href) for r in newest]
        lines.append(f"| {title(manifest, texts, key)} | " + " | ".join(cells) + " |")
    return lines


def first_release(manifest: dict[str, Any], lights: str) -> str | None:
    for release in manifest["releases"]:
        info, _ = lookup(manifest, lights, "", release)
        if info and info["available"]:
            return release
    return None


def unavailable(manifest: dict[str, Any], lights: str, info: dict[str, Any]) -> str:
    """Why a release has no room for these lights, in plain words."""
    if info["why"] == "no_output":
        since = first_release(manifest, lights)
        later = f"in {since}" if since else "later"
        return f"Not available: {OUTPUT_NAMES[lights]} came {later}."
    return "Not available: this release's code cannot run the room's scenarios."


def scenario_section(
    manifest: dict[str, Any], texts: dict[str, Any], key: str
) -> list[str]:
    """A scenario's text and its tabs, newest release first."""
    lights, name = split(key)
    scenario = texts["scenarios"][key]
    heading = title(manifest, texts, key)
    lines = [
        f"### {heading} {{ #{slug(lights, name)} }}",
        "",
        f"**The picture:** {scenario['picture']}",
        "",
        f"**What the lights should do:** {scenario['lights']}",
        "",
    ]
    notes = texts.get("notes", {})
    for release in reversed(manifest["releases"]):
        info, found = lookup(manifest, lights, name, release)
        mark = f" {CHANGED}" if found and found["status"] == "changed" else ""
        lines += [f'=== "{release}{mark}"', ""]
        note = notes.get(release, {}).get(key)
        if info is None:
            text = "Not made for this release."
        elif not info["available"]:
            text = unavailable(manifest, lights, info)
        elif found is None:
            text = "Not available: this release has no such scenario."
        elif found["status"] == "same":
            text = f"Same as {found['since']}." + (f" {note}" if note else "")
        elif found["status"] == "new":
            text = note or "The first release with these lights."
        else:
            text = f"{CHANGED} {note or 'Changed since the previous release.'}"
        lines += [f"    {text}", ""]
        if found is not None:
            url = f"rooms/{found['gif']}"
            alt = f"{short(heading)}, in the room with {release}"
            # Markdown, not HTML, so that mkdocs checks and rewrites the links;
            # 400 px wide, a click opens it at full size.
            lines += [f'    [![{alt}]({url}){{ width="400" loading=lazy }}]({url})', ""]
    return lines


def group_block(
    manifest: dict[str, Any], texts: dict[str, Any], group: dict[str, Any]
) -> str:
    lines = [*table(manifest, texts, [(k, "") for k in group["scenarios"]]), ""]
    lines += [LEGEND, ""]
    for key in group["scenarios"]:
        lines += scenario_section(manifest, texts, key)
    return "\n".join(lines)


def overview_block(manifest: dict[str, Any], texts: dict[str, Any]) -> str:
    rows = [(k, g["page"]) for g in texts["groups"] for k in g["scenarios"]]
    return "\n".join([*table(manifest, texts, rows), "", LEGEND, ""])


def fill(text: str, block: str) -> str:
    """`text` with `block` between its markers."""
    if not MARKER.search(text):
        raise ValueError("no <!-- rooms --> ... <!-- /rooms --> part")
    return MARKER.sub(lambda m: m.group(1) + block + "\n" + m.group(2), text)


def write_pages(manifest: dict[str, Any], texts: dict[str, Any]) -> None:
    pages = [(g["page"], group_block(manifest, texts, g)) for g in texts["groups"]]
    pages.append(("index.md", overview_block(manifest, texts)))
    for name, block in pages:
        page = PAGES / name
        page.write_text(fill(page.read_text(), block))


# --- Main -------------------------------------------------------------------


def _tags() -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(REPO), "tag", "--list", "v*"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.split()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--next", dest="next_ref", help="a ref for the next release")
    parser.add_argument("--next-label", help="its label, for example v0.5.0")
    parser.add_argument("--force", action="store_true", help="make every GIF again")
    parser.add_argument(
        "--pages-only", action="store_true", help="write the pages from the manifest"
    )
    parser.add_argument("--width", type=int, default=WIDTH, help="px per room")
    parser.add_argument("--step", type=int, default=STEP, help="every n-th send tick")
    args = parser.parse_args()
    if bool(args.next_ref) != bool(args.next_label):
        parser.error("--next and --next-label go together")
    texts = tomllib.loads(TEXTS.read_text())
    manifest_file = ROOMS / "manifest.json"
    if not args.pages_only:
        sys.path.insert(0, str(Path(__file__).parent))
        import room

        room.ROOM_WIDTH = args.width
        room.STEPS = (args.step,)
        chosen = releases(_tags(), args.next_ref, args.next_label)
        manifest = make(room, chosen, wanted(texts), force=args.force)
        ROOMS.mkdir(parents=True, exist_ok=True)
        text = json.dumps(manifest, indent=2, ensure_ascii=False)
        manifest_file.write_text(text + "\n")
    manifest = json.loads(manifest_file.read_text())
    write_pages(manifest, texts)
    size = sum(gif.stat().st_size for gif in ROOMS.rglob("*.gif"))
    print(f"Pages written; the GIFs in {ROOMS} take {size / 1_000_000:.1f} MB")


if __name__ == "__main__":
    main()
