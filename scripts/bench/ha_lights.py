"""The bench's Home Assistant lights (#9): their config, and the judgement of the rate,
the colours from a light's state and the restore. Pure functions on recorded
numbers, so `tests/test_bench_ha_lights.py` runs them in CI.

A Home Assistant light is a light of the product owner's Home Assistant that the
bench's local Home Assistant mirrors as a proxy light (stubs/proxy.py) of a
Hue network (`kind: hue`) or a Thread network (`kind: matter`). One that the
camera sees (`light:` names one of the bench's `lights:`) is judged with the
camera; any other by its state in the owner's Home Assistant.

A camera light of the Hue network may be marked `in_area: true` (#198): one
that is in the bench's entertainment area too. Only the combined suite uses
the mark; its proxy then carries the light's Hue light resource id as unique
id, as Home Assistant's hue integration does, so Glowcast finds it in the area.
"""

from __future__ import annotations

import colorsys
import dataclasses
import importlib.util
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any

REPO = Path(__file__).resolve().parent.parent.parent
KINDS = ("hue", "matter")
NETWORK_LABEL = {"hue": "Hue network", "matter": "Thread network"}
# As outputs/ha_lights.py PRESETS (a test checks they are the same): x is -1
# left .. +1 right, z is -1 bottom .. +1 top.
PRESETS: dict[str, tuple[float, float]] = {
    "left": (-1.0, 0.0),
    "right": (1.0, 0.0),
    "top": (0.0, 1.0),
    "bottom": (0.0, -1.0),
    "ambient": (0.0, 0.0),
}
MAX_LIGHTS = 8


def schedule() -> ModuleType:
    """Glowcast's own rate limits (outputs/ha_lights_schedule.py), loaded by
    path: it is pure, and the package around it imports Home Assistant."""
    path = REPO / "custom_components" / "glowcast" / "outputs" / "ha_lights_schedule.py"
    spec = importlib.util.spec_from_file_location("glowcast_ha_lights_schedule", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@dataclasses.dataclass(frozen=True)
class HaLight:
    """One Home Assistant light: its entity in the owner's HA, network, position, and
    the camera light that shows it (None: judged by its state)."""

    index: int  # 1, 2, ...
    entity_id: str
    kind: str
    x: float
    z: float
    camera: str | None
    in_area: bool = False

    @property
    def unique_id(self) -> str:
        return f"bench-proxy-{self.index}"

    @property
    def name(self) -> str:
        """The proxy's name in the local HA: generic, never the owner's."""
        return f"Bench proxy {self.index}"


def parse(cfg: Mapping[str, Any]) -> list[HaLight]:
    """The `ha_lights:` list; ValueError with the reason when it is wrong.

    Each item: `kind` (hue or matter); `light` (optional, a name in `lights:`
    that the camera sees); `entity_id` (in the owner's HA; default: that
    light's); the position as `position` (a preset) or `x` and `z` (-1..1);
    `in_area: true` (optional, at most one: a camera light of kind hue).
    """
    items = cfg.get("ha_lights", cfg.get("loose_lights"))  # old name until v0.4
    camera = cfg.get("lights") or {}
    if not isinstance(items, list) or not items:
        raise ValueError("`ha_lights` must be a list of lights")
    if len(items) > MAX_LIGHTS:
        raise ValueError(f"`ha_lights` takes at most {MAX_LIGHTS} lights")
    out: list[HaLight] = []
    for i, item in enumerate(items, 1):
        where = f"`ha_lights` item {i}"
        if not isinstance(item, dict):
            raise ValueError(f"{where} must be a mapping")
        name = item.get("light")
        if name is not None and name not in camera:
            raise ValueError(f"{where}: `light` must be one of `lights`")
        entity = item.get("entity_id") or (camera[name]["entity_id"] if name else "")
        if not str(entity).startswith("light."):
            raise ValueError(f"{where}: `entity_id` must be a light")
        if item.get("kind") not in KINDS:
            raise ValueError(f"{where}: `kind` must be hue or matter")
        if "position" in item:
            if item["position"] not in PRESETS:
                raise ValueError(
                    f"{where}: `position` must be one of {', '.join(PRESETS)}"
                )
            x, z = PRESETS[item["position"]]
        else:
            try:
                x, z = float(item["x"]), float(item["z"])
            except (KeyError, TypeError, ValueError):
                raise ValueError(f"{where} needs a `position` or `x` and `z`") from None
            if not (-1 <= x <= 1 and -1 <= z <= 1):
                raise ValueError(f"{where}: `x` and `z` must be within -1..1")
        in_area = item.get("in_area", False)
        if not isinstance(in_area, bool):
            raise ValueError(f"{where}: `in_area` must be true or false")
        if in_area and (item["kind"] != "hue" or name is None):
            raise ValueError(f"{where}: `in_area` needs kind hue and a camera `light`")
        out.append(HaLight(i, str(entity), item["kind"], x, z, name, in_area))
    if len({light.entity_id for light in out}) != len(out):
        raise ValueError("`ha_lights` names a light twice")
    if len({light.camera for light in out if light.camera}) != sum(
        1 for light in out if light.camera
    ):
        raise ValueError("`ha_lights` names a camera light twice")
    if sum(light.in_area for light in out) > 1:
        raise ValueError("`ha_lights` marks more than one light `in_area`")
    return out


def beside_area(lights: Sequence[HaLight]) -> list[HaLight]:
    """The Home Assistant lights the combined suite drives beside the bench area: the
    ones the camera does not see (the camera lights are the area's)."""
    return [light for light in lights if light.camera is None]


def in_area(lights: Sequence[HaLight]) -> HaLight | None:
    """The Home Assistant light the combined suite also puts in the Home Assistant
    lights while the bench area streams to it (#198), or None."""
    return next((light for light in lights if light.in_area), None)


def area_unique_id(light: HaLight, rid_of: Mapping[str, str | None]) -> str | None:
    """The in-area light's proxy unique id: its Hue light resource id, the
    bridge light that came on with its camera light in the calibration
    (`rid_of`); None when that could not be told (also when it came on with
    another camera light too)."""
    rid = rid_of.get(light.camera) if light.camera else None
    return rid if rid is not None and list(rid_of.values()).count(rid) == 1 else None


def labels(lights: Sequence[HaLight], camera_labels: Mapping[str, str]) -> dict:
    """{index: label}: a camera light's own label ("light 1"), the others
    numbered on after the camera lights ("light 3 (state)")."""
    n = len(camera_labels)
    out = {}
    for light in lights:
        if light.camera is not None:
            out[light.index] = camera_labels[light.camera]
        else:
            n += 1
            out[light.index] = f"light {n} (state)"
    return out


# ---- the colour of a light from its state ---------------------------------
def state_colour(state: Mapping[str, Any] | None) -> str:
    """off, white, red, green, blue or other, from a light's state.

    From `hs_color` (Home Assistant fills it in for every colour mode), else
    from `rgb_color`. A Hue light in its gamut reads blue at about 235 and
    green at about 120.
    """
    if state is None or state.get("state") != "on":
        return "off"
    a = state.get("attributes") or {}
    hs = a.get("hs_color")
    if hs is None and a.get("rgb_color"):
        r, g, b = (v / 255 for v in a["rgb_color"][:3])
        h, s, _ = colorsys.rgb_to_hsv(r, g, b)
        hs = (h * 360, s * 100)
    if hs is None:
        return "other"
    hue, saturation = float(hs[0]) % 360, float(hs[1])
    if saturation < 25:
        return "white"
    if hue >= 330 or hue < 30:
        return "red"
    if 75 <= hue < 165:
        return "green"
    if 195 <= hue < 285:
        return "blue"
    return "other"


def split_colour(col0: int, col1: int, width: int) -> str | None:
    """What a light whose band is columns col0..col1 of a frame `width` wide
    shows for left-red-right-blue (the left half red); None when its band
    takes from both halves."""
    middle = width // 2
    if col1 <= middle:
        return "red"
    if col0 >= middle:
        return "blue"
    return None


# ---- the restore -------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class RestoreTolerance:
    brightness: int = 3
    kelvin: int = 50
    xy: float = 0.01
    hs: float = 5.0


TOLERANCE = RestoreTolerance()


def restored(
    before: Mapping[str, Any],
    after: Mapping[str, Any] | None,
    tol: RestoreTolerance = TOLERANCE,
) -> tuple[bool, list[str]]:
    """Whether the light is back as `before`: on or off, and when on its
    brightness and its colour (colour temperature, xy or hs) within `tol`."""
    if after is None:
        return False, ["no state"]
    problems: list[str] = []
    if before.get("state") != after.get("state"):
        return False, ["on/off"]
    if before.get("state") != "on":
        return True, []
    a, b = before.get("attributes") or {}, after.get("attributes") or {}
    if a.get("brightness") is not None and (
        b.get("brightness") is None
        or abs(a["brightness"] - b["brightness"]) > tol.brightness
    ):
        problems.append("brightness")
    if a.get("color_mode") == "color_temp":
        if b.get("color_mode") != "color_temp":
            problems.append("colour mode")
        elif a.get("color_temp_kelvin") is not None and (
            b.get("color_temp_kelvin") is None
            or abs(a["color_temp_kelvin"] - b["color_temp_kelvin"]) > tol.kelvin
        ):
            problems.append("colour temperature")
    elif a.get("xy_color") and b.get("xy_color"):
        if any(
            abs(p - q) > tol.xy
            for p, q in zip(a["xy_color"], b["xy_color"], strict=False)
        ):
            problems.append("xy")
    elif a.get("hs_color") and b.get("hs_color"):
        dh = abs(a["hs_color"][0] - b["hs_color"][0]) % 360
        if min(dh, 360 - dh) > tol.hs or abs(a["hs_color"][1] - b["hs_color"][1]) > (
            tol.hs
        ):
            problems.append("hs")
    return not problems, problems


StateSample = tuple[float, str, dict[str, Any]]  # time, owner entity, state


def states_between(
    samples: Iterable[StateSample], entity: str, start: float, end: float
) -> list[dict[str, Any]]:
    return [s for t, e, s in samples if e == entity and start <= t <= end]


def first_restored(
    samples: Sequence[StateSample],
    entity: str,
    before: Mapping[str, Any],
    start: float,
    end: float,
    tol: RestoreTolerance = TOLERANCE,
) -> float | None:
    """The first time from `start` from which every state up to `end` is
    `before` again (a light that comes back and stays back)."""
    window = [(t, s) for t, e, s in samples if e == entity and start <= t <= end]
    since: float | None = None
    for t, state in window:
        if restored(before, state, tol)[0]:
            if since is None:
                since = t
        else:
            since = None
    return since


def settled_colour(states: Sequence[Mapping[str, Any]]) -> str | None:
    """The colour every state in a window shows, or None (none, or mixed)."""
    colours = {state_colour(s) for s in states}
    return colours.pop() if len(colours) == 1 else None


# ---- the rate ----------------------------------------------------------------
def epoch(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def commands(
    calls: Iterable[tuple[float, dict[str, Any]]], entity_ids: Iterable[str]
) -> dict[str, list[tuple[float, float | None]]]:
    """Glowcast's colour commands per local entity: (time, transition).

    From the local HA's `call_service` events of `light.turn_on`: a colour
    command carries `rgb_color` and `transition`; a restore (a light service call with
    the saved state) carries no transition and is left out.
    """
    wanted = set(entity_ids)
    out: dict[str, list[tuple[float, float | None]]] = {e: [] for e in wanted}
    for t, data in calls:
        if "rgb_color" not in data or "transition" not in data:
            continue
        ids = data.get("entity_id")
        for entity in [ids] if isinstance(ids, str) else list(ids or []):
            if entity in wanted:
                out[entity].append((t, data.get("transition")))
    return out


def max_in_window(times: Sequence[float], length: float) -> int:
    """The most times in any window of `length` seconds (closed at both ends)."""
    ordered = sorted(times)
    best = 0
    first = 0
    for last, t in enumerate(ordered):
        while t - ordered[first] > length:
            first += 1
        best = max(best, last - first + 1)
    return best


def min_gap(times: Sequence[float]) -> float | None:
    ordered = sorted(times)
    gaps = [b - a for a, b in zip(ordered, ordered[1:], strict=False)]
    return min(gaps) if gaps else None


@dataclasses.dataclass(frozen=True)
class RateResult:
    ok: bool
    numbers: dict[str, Any]
    note: str


def expected_transitions(
    times: Sequence[float], interval: float, gap: float, slack: float = 0.0
) -> list[tuple[float, ...]]:
    """The fade each command may have, as Glowcast's schedule sets it: the
    time since the light's command before, or its interval for the first
    command and after a wait of `gap` intervals or more (a colour that did
    not change). Within `slack` of that limit, either."""
    ordered = sorted(times)
    out: list[tuple[float, ...]] = []
    for i, t in enumerate(ordered):
        since = t - ordered[i - 1] if i else None
        if since is None or since >= gap * interval + slack:
            out.append((interval,))
        elif since < gap * interval - slack:
            out.append((since,))
        else:
            out.append((since, interval))
    return out


def judge_rate(
    sent: Mapping[str, Sequence[tuple[float, float | None]]],
    network: Mapping[str, str | None],
    label: Mapping[str, str],
    network_label: Mapping[str, str],
    *,
    slack: float,
) -> RateResult:
    """The rate limits of Glowcast's Home Assistant lights, on the commands it sent.

    Per light (`sent`: local entity -> commands): at least its interval minus
    `slack` between two commands, and `transition` its actual interval (the
    time since its command before, within `slack`; its interval for the
    first command and after a wait, see expected_transitions).
    Per network: at most NETWORK_COMMANDS_PER_SECOND commands in any window
    of 1 s minus `slack`. The intervals come from Glowcast's own schedule
    (each light once per second; more than 4 lights on a network share it).
    """
    sch = schedule()
    networks = {e: ((n,) if n else ()) for e, n in network.items()}
    plan = sch.Schedule(networks)
    numbers: dict[str, Any] = {}
    problems: list[str] = []
    for entity in sent:
        times = [t for t, _ in sent[entity]]
        interval = plan.interval(entity)
        gap = min_gap(times)
        numbers[f"{label[entity]} commands"] = len(times)
        numbers[f"{label[entity]} shortest gap s"] = (
            None if gap is None else round(gap, 2)
        )
        numbers[f"{label[entity]} interval s"] = round(interval, 2)
        if not times:
            problems.append(f"{label[entity]}: no command")
        if gap is not None and gap < interval - slack:
            problems.append(f"{label[entity]}: commands too close")
        bad = [
            tr
            for tr, want in zip(
                [tr for _, tr in sorted(sent[entity])],
                expected_transitions(times, interval, sch.TRANSITION_GAP, slack),
                strict=True,
            )
            if tr is None or all(abs(float(tr) - w) > slack for w in want)
        ]
        numbers[f"{label[entity]} transition not the actual interval"] = len(bad)
        if bad:
            problems.append(f"{label[entity]}: transition")
    by_network: dict[str, list[float]] = {}
    for entity, n in network.items():
        if n is not None:
            by_network.setdefault(n, []).extend(t for t, _ in sent.get(entity, []))
    for n, times in by_network.items():
        lights = sum(1 for v in network.values() if v == n)
        # Glowcast's bound: 4 commands in any second less its tolerance
        # (TOLERANCE, 10 ms); a millisecond less for the events' times.
        most = max_in_window(times, 1.0 - sch.TOLERANCE - 0.001)
        name = network_label[n]
        numbers[f"{name} lights"] = lights
        numbers[f"{name} most commands in 1 s"] = most
        if most > sch.NETWORK_COMMANDS_PER_SECOND:
            problems.append(f"{name}: more than its commands per second")
    return RateResult(not problems, numbers, "; ".join(problems))
