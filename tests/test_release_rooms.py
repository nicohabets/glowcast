"""The room per release (scripts/demo/release_rooms.py, #293): the releases,
the names, what changed and the pages. The recording and drawing need the
demo's environment and git; these are the pure parts."""

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO = Path(__file__).resolve().parent.parent


def _load() -> ModuleType:
    path = REPO / "scripts" / "demo" / "release_rooms.py"
    spec = importlib.util.spec_from_file_location("release_rooms", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rooms = _load()


def _recording(*scenarios: tuple[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """A recording with these scenarios: (name, the lights per sample)."""
    samples = [
        {"scenario": index, "lights": lights}
        for index, (_, per_sample) in enumerate(scenarios)
        for lights in per_sample
    ]
    return {
        "scenarios": [{"name": name} for name, _ in scenarios],
        "samples": samples,
    }


RED = {"left": [1.0, 0.0, 0.0]}


def test_releases_are_the_tags_in_order_and_the_next_one() -> None:
    tags = ["v0.2.0", "v0.10.0", "v0.1.1", "v0.1.0", "test", "v0.3.0-rc1"]
    assert rooms.releases(tags) == [
        ("v0.1.0", "v0.1.0"),
        ("v0.1.1", "v0.1.1"),
        ("v0.2.0", "v0.2.0"),
        ("v0.10.0", "v0.10.0"),
    ]
    # The next release while it has no tag; once tagged, the tag.
    assert rooms.releases(["v0.3.1"], "main", "v0.4.0")[-1] == ("v0.4.0", "main")
    assert rooms.releases(["v0.4.0"], "main", "v0.4.0") == [("v0.4.0", "v0.4.0")]
    with pytest.raises(ValueError):
        rooms.releases(["v0.3.1"], "main", "next")


def test_names_without_the_number() -> None:
    assert rooms.short("1 Sunset") == "Sunset"
    assert rooms.short("8 4:3 picture") == "4:3 picture"
    assert rooms.slug("area", "4:3 picture") == "area-43-picture"
    assert rooms.slug("combined", "9 Home Assistant lights unavailable") == (
        "combined-home-assistant-lights-unavailable"
    )
    assert rooms.gif_path("v0.4.0", "ha", "Sunset") == "v0.4.0/ha-sunset.gif"
    assert rooms.split("area:4:3 picture") == ("area", "4:3 picture")
    with pytest.raises(ValueError):
        rooms.split("lamps:Sunset")
    texts = {"groups": [{"scenarios": ["area:Sunset", "ha:Sunset", "area:Concert"]}]}
    assert rooms.wanted(texts) == {"area": ["Sunset", "Concert"], "ha": ["Sunset"]}
    # A renumbered scenario is found by its name.
    recording = _recording(("1 Sunset", []), ("2 Concert", []))
    assert rooms.numbers(recording) == {"Sunset": 1, "Concert": 2}


def test_difference_per_colour_component() -> None:
    red, off = {"lights": RED}, {"lights": {}}
    assert rooms.difference([red, red], [red, red]) == 0
    # A light that is off counts as black.
    assert rooms.difference([red], [off]) == 255
    half = [{"lights": {"left": [0.5, 0, 0]}}]
    assert rooms.difference(half, [{"lights": {"left": [0.51, 0, 0]}}]) == 3
    # Not comparable tick by tick.
    assert rooms.difference([red], [red, red]) is None
    assert rooms.difference([red], [red | {"unavailable": ["wall_light"]}]) is None


def test_new_changed_and_same() -> None:
    first = _recording(("1 Sunset", [RED]))
    entry = rooms.entry("v0.1.0", "area", "Sunset", first, None)
    assert entry == {
        "status": "new",
        "difference": None,
        "gif": "v0.1.0/area-sunset.gif",
        "since": "v0.1.0",
    }
    # Within the tolerance: the same, with the earlier release's GIF.
    close = _recording(("1 Sunset", [{"left": [0.995, 0, 0]}]))
    same = rooms.entry("v0.1.1", "area", "Sunset", close, (first, entry))
    assert same["status"] == "same"
    assert same["gif"] == "v0.1.0/area-sunset.gif"
    assert same["since"] == "v0.1.0"
    # And the release after it says since when.
    again = rooms.entry("v0.2.0", "area", "Sunset", close, (close, same))
    assert (again["status"], again["since"]) == ("same", "v0.1.0")
    # A change beyond it gets its own GIF.
    blue = _recording(("1 Sunset", [{"left": [0, 0, 1.0]}]))
    changed = rooms.entry("v0.3.0", "area", "Sunset", blue, (close, again))
    assert changed == {
        "status": "changed",
        "difference": 255,
        "gif": "v0.3.0/area-sunset.gif",
        "since": "v0.3.0",
    }
    # A scenario the previous release did not have is new.
    more = _recording(("1 Sunset", [RED]), ("2 Concert", []))
    assert rooms.entry("v0.4.0", "area", "Concert", more, (blue, changed))[
        "status"
    ] == ("new")


MANIFEST = {
    "releases": ["v0.2.0", "v0.3.0", "v0.4.0"],
    "lights": {
        "ha": {
            "names": {"Sunset": "1 Sunset"},
            "releases": {
                "v0.2.0": {"available": False, "why": "no_output", "detail": "x"},
                "v0.3.0": {
                    "available": True,
                    "scenarios": {
                        "Sunset": {
                            "status": "new",
                            "difference": None,
                            "gif": "v0.3.0/ha-sunset.gif",
                            "since": "v0.3.0",
                        }
                    },
                },
                "v0.4.0": {
                    "available": True,
                    "scenarios": {
                        "Sunset": {
                            "status": "changed",
                            "difference": 6,
                            "gif": "v0.4.0/ha-sunset.gif",
                            "since": "v0.4.0",
                        }
                    },
                },
            },
        }
    },
}
TEXTS = {
    "groups": [{"page": "lights.md", "scenarios": ["ha:Sunset"]}],
    "scenarios": {
        "ha:Sunset": {
            "prefix": "Home Assistant lights",
            "picture": "A sunset.",
            "lights": "Follow it.",
        }
    },
    "notes": {"v0.4.0": {"ha:Sunset": "Livelier colours."}},
}


def test_the_table_marks_what_changed() -> None:
    lines = rooms.table(MANIFEST, TEXTS, [("ha:Sunset", "lights.md")])
    assert lines[0] == "| Scenario | v0.4.0 | v0.3.0 | v0.2.0 |"
    title, changed, new, missing = (c.strip() for c in lines[2].strip("|").split(" | "))
    assert title == "[Home Assistant lights: 1 Sunset](lights.md#ha-sunset)"
    gif = "rooms/v0.4.0/ha-sunset.gif"
    assert changed == (
        f'[![Sunset in v0.4.0]({gif}){{ width="140" loading=lazy }}]({gif})'
        "<br>[● changed](lights.md#ha-sunset){ .glowcast-changed }"
    )
    assert new.startswith("[![Sunset in v0.3.0](rooms/v0.3.0/ha-sunset.gif)")
    assert new.endswith("<br>[new](lights.md#ha-sunset)")
    assert missing == "-"


def test_a_cell_that_is_the_same_links_to_the_gif_it_shows() -> None:
    same = {"status": "same", "gif": "v0.3.0/ha-sunset.gif", "since": "v0.3.0"}
    assert rooms.cell(same, "lights.md#ha-sunset") == (
        '[same](rooms/v0.3.0/ha-sunset.gif "same as v0.3.0")'
    )


def test_the_tabs_newest_first() -> None:
    text = "\n".join(rooms.scenario_section(MANIFEST, TEXTS, "ha:Sunset"))
    assert text.index('=== "v0.4.0 ●"') < text.index('=== "v0.3.0"')
    assert "    ● Livelier colours." in text
    gif = "rooms/v0.4.0/ha-sunset.gif"
    assert f'({gif}){{ width="400" loading=lazy }}]({gif})' in text
    # Plain words, not the harness's message.
    assert "Not available: Home Assistant lights came in v0.3.0." in text


def test_the_pages_are_written_between_the_markers() -> None:
    page = "# Title\n\nText.\n\n<!-- rooms -->\nold\n<!-- /rooms -->\n\nMore.\n"
    filled = rooms.fill(page, "new")
    assert filled == page.replace("old", "new")
    assert rooms.fill(filled, "new") == filled
    with pytest.raises(ValueError):
        rooms.fill("# No markers\n", "new")


def test_the_texts_name_every_scenario_on_the_pages() -> None:
    import tomllib

    texts = tomllib.loads(rooms.TEXTS.read_text())
    keys = [key for group in texts["groups"] for key in group["scenarios"]]
    assert keys and all(key in texts["scenarios"] for key in keys)
    assert all((rooms.PAGES / group["page"]).is_file() for group in texts["groups"])
    for notes in texts["notes"].values():
        assert set(notes) <= set(keys)


def test_a_release_note_for_every_scenario() -> None:
    """A release that changed nothing the room shows says what it did."""
    texts = TEXTS | {"releases": {"v0.3.0": "Other changes, see the overview."}}
    text = "\n".join(rooms.scenario_section(MANIFEST, texts, "ha:Sunset"))
    assert "    Other changes, see the overview." in text
    # A scenario's own note comes first.
    assert "    ● Livelier colours." in text
