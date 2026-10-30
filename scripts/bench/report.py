"""The bench's judgement and its report: pure functions on recorded numbers.

No hardware, no Home Assistant and no OpenCV here, so the unit tests
(`tests/test_bench_report.py`) run it in CI on recorded numbers. Everything is
on one clock per comparison: camera samples and the picture changes they are
compared with are both on the Windows clock (the camera worker stamps each
frame with time.time() when read() returns; the helper's `pattern.since` and
the screen window's `shown` are Windows times too).

The colour rule is the one of spike #133, calibrated per run: per channel
n = (mean - dark) / (white - dark), level = sum(mean - dark) / sum(white - dark);
off below `off_level`; white when max(n) / min(n) < `white_ratio`; else the
dominant channel. Glowcast's own glow on black is a dim warm white, read as a
low level above the dark noise, with red the strongest normalised channel.

The report holds numbers and fixed texts only (no names, ids or addresses),
so it can be attached to a public pull request; `scrub` is a last check.
The format (`Report.to_json`) is meant to be reused by the TV check (#71).
"""

from __future__ import annotations

import dataclasses
import json
import statistics
from collections.abc import Iterable, Sequence
from typing import Any

RGB = tuple[float, float, float]
# One camera frame: its Windows time and per region (r, g, b) means.
Sample = tuple[float, dict[str, RGB]]

# Exit codes of run.py.
EXIT_PASSED = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_OTHER_AREA = 3  # skipped: the bridge streams another area
EXIT_LOCKED = 4  # skipped: another bench run holds the lock
EXIT_TV_AREA_OTHER_APP = 5  # skipped: another app streams the TV's area
EXIT_ERROR = 6  # the run could not complete (the bench itself, not Glowcast)
EXIT_CALIBRATION = 7  # the calibration failed: a light not found, too much ambient

PASS, FAIL, SKIP = "pass", "fail", "skip"


@dataclasses.dataclass(frozen=True)
class Thresholds:
    """Tolerances; the defaults come from #133 and the recorded bench runs."""

    off_level: float = 0.15
    white_ratio: float = 1.3
    # Glowcast's white (sRGB white at the brightness setting) against the
    # cool-white reference.
    glowcast_white_ratio: float = 1.5
    # The glow (10 % warm white) on a lit wall reads about 0.012 at exposure
    # -8, on a spot's rim about 0.35 (the clipped core): lit, not off.
    glow_min_level: float = 0.005
    glow_max_level: float = 0.5
    # Restore and hold: the largest difference of a normalised channel.
    same_picture: float = 0.12
    # A change counts for a delay only when the pictures differ this much.
    min_change: float = 0.2
    max_delay_ms: float = 1500.0
    # Purity: the largest other channel as a share of the colour's own
    # (against the cool reference); 20 % may not be less pure than 80 %.
    max_impurity: float = 0.35
    impurity_margin: float = 0.1
    stream_within_s: float = 15.0
    restore_within_s: float = 6.0
    # Calibration: a light is found when its window brightens this much per
    # pixel (0-765); a region clips at this 95th percentile; the lights in
    # warm white must lift a region at least this much (sum of RGB) above
    # dark; ambient light (lights off) may be at most this share of white.
    min_found: float = 30.0
    # ... and stand this many times above the picture's median change.
    min_contrast: float = 5.0
    # Two found regions may share at most this much of the smaller one.
    max_overlap: float = 0.3
    # A light region that moved more than this is reported as a warning.
    warn_moved_px: float = 40.0
    # A region that clips at every exposure (a spot's core): at most this
    # share of its pixels may clip, so its rim is still measured.
    max_clipped_share: float = 0.7
    clip_p95: float = 240.0
    min_white_lift: float = 60.0
    max_ambient: float = 0.35
    # Loose lights (#9): a colour counts once this long after a change (the
    # engine's smoothing, a command per second with a 1 s fade and the light's
    # report about once per second); commands may come this much early
    # (the send loop's tick and the event's time).
    loose_settle_s: float = 4.0
    # A combined output (#11): a lost part shows in `dropped_outputs` within
    # this (its check every 5 s), and clears this long after it is back (the
    # retry after 5 s, its release first).
    drop_within_s: float = 10.0
    clear_within_s: float = 12.0
    rate_slack_s: float = 0.15


@dataclasses.dataclass(frozen=True)
class Timing:
    """The suite's timing, derived from Glowcast's own constants, so a change
    of the no-picture wait or the hold on black does not break the bench.

    no_picture_wait: all-zero frames for this long release the lights
    (core.pipeline.NO_PICTURE_WAIT); black_hold: the colours hold this long on
    a black picture before the glow (core.light_state.BLACK_HOLD).
    """

    no_picture_wait: float
    black_hold: float

    @property
    def pattern_interval(self) -> float:
        """Seconds per helper pattern: the release (the wait and a capture),
        the lights' return (about 1.7 s, #142) and a settled look after it."""
        return max(8.0, float(int(self.no_picture_wait + 5.0 + 0.999)))

    @property
    def release_hold_end(self) -> float:
        """Until here (after the change) all-zero black still holds the
        colours: the hold on black, or the release if that comes first."""
        return min(self.no_picture_wait, self.black_hold) - 0.3

    @property
    def glow_before_release(self) -> tuple[float, float] | None:
        """When all-zero black shows the glow before the release (a wait
        longer than the hold and the fade), the window to look at it."""
        start, end = self.glow_settled_from, self.no_picture_wait - 0.3
        return (start, end) if end - start >= 1.0 else None

    @property
    def released_by(self) -> float:
        """From here the area must be inactive."""
        return self.no_picture_wait + 2.5

    @property
    def back_from(self) -> float:
        """From here the camera looks for the lights' own picture."""
        return self.no_picture_wait - 0.5

    @property
    def short_black(self) -> float:
        """A near-black picture this short holds the colours."""
        return min(2.0, self.black_hold - 1.0)

    @property
    def long_black(self) -> float:
        """A near-black picture this long shows the glow for a while."""
        return self.black_hold + 4.5

    @property
    def glow_hold_end(self) -> float:
        return self.black_hold - 0.5

    @property
    def glow_window(self) -> tuple[float, float]:
        """When the light must have crossed to the glow (after the change)."""
        return self.black_hold - 0.5, self.black_hold + 2.5

    @property
    def glow_settled_from(self) -> float:
        return self.black_hold + 2.0


@dataclasses.dataclass(frozen=True)
class Calibration:
    """A region's references: dark (lights off), white (warm white, full) and
    cool (a cool white at full brightness, for Glowcast's white)."""

    dark: RGB
    white: RGB
    cool: RGB | None = None

    def cool_normalised(self, mean: Sequence[float]) -> RGB:
        """Normalised against the cool white: sRGB white reads about even."""
        cool = self.cool or self.white
        return tuple(  # type: ignore[return-value]
            (m - d) / max(c - d, 1.0)
            for m, d, c in zip(mean[:3], self.dark, cool, strict=True)
        )

    def normalised(self, mean: Sequence[float]) -> RGB:
        return tuple(  # type: ignore[return-value]
            (m - d) / max(w - d, 1.0)
            for m, d, w in zip(mean[:3], self.dark, self.white, strict=True)
        )

    def level(self, mean: Sequence[float]) -> float:
        lit = sum(m - d for m, d in zip(mean[:3], self.dark, strict=True))
        return lit / max(
            sum(w - d for w, d in zip(self.white, self.dark, strict=True)), 1.0
        )


def classify(mean: Sequence[float], cal: Calibration, th: Thresholds) -> str:
    """off, white, red, green or blue, by the #133 rule.

    White is also a cool white (even against the cool reference), such as
    Glowcast's sRGB white, which the warm reference alone would call blue.
    """
    if cal.level(mean) < th.off_level:
        return "off"
    if cal.cool is not None:
        c = cal.cool_normalised(mean)
        if max(c) / max(min(c), 1e-3) < th.glowcast_white_ratio:
            return "white"
    n = cal.normalised(mean)
    low = max(min(n), 1e-3)
    if max(n) / low < th.white_ratio:
        return "white"
    return ("red", "green", "blue")[n.index(max(n))]


def matches_colour(
    expected: str, mean: Sequence[float], cal: Calibration, th: Thresholds
) -> bool:
    """Whether the light shows the colour a pattern asks of Glowcast."""
    return classify(mean, cal, th) == expected


def is_glow(
    mean: Sequence[float],
    cal: Calibration,
    th: Thresholds,
    before: Sequence[float] | None = None,
) -> bool:
    """A dim warm light: above the dark noise, under the glow's ceiling, dimmer
    than the colour `before` it, and warm (against the cool white, red over blue)."""
    level = cal.level(mean)
    if not th.glow_min_level <= level <= th.glow_max_level:
        return False
    if before is not None and level >= cal.level(before):
        return False
    n = cal.cool_normalised(mean)
    return n[0] > n[2]


def impurity(mean: Sequence[float], cal: Calibration, colour: str) -> float:
    """The largest other channel as a share of `colour`'s channel, against the
    cool reference (0 for a pure colour; a warm white mixed in raises it)."""
    n = [max(v, 0.0) for v in cal.cool_normalised(mean)]
    own = ("red", "green", "blue").index(colour)
    others = [v for i, v in enumerate(n) if i != own]
    return max(others) / max(n[own], 1e-3)


def difference(a: Sequence[float], b: Sequence[float], cal: Calibration) -> float:
    """The largest difference of a channel between two means, as a share of
    that channel's full range (the brighter of the warm and cool references).

    Not the warm-normalised value: its blue range is small, so a white
    monitor's light on a wall would count as a change of colour.
    """
    cool = cal.cool or cal.white
    return max(
        abs(x - y) / max(w - d, c - d, 1.0)
        for x, y, d, w, c in zip(a[:3], b[:3], cal.dark, cal.white, cool, strict=True)
    )


def window(
    samples: Iterable[Sample], region: str, start: float, end: float
) -> list[RGB]:
    return [r[region] for t, r in samples if start <= t <= end and region in r]


def mean_of(values: Sequence[Sequence[float]]) -> RGB | None:
    if not values:
        return None
    return tuple(statistics.fmean(v[i] for v in values) for i in range(3))  # type: ignore[return-value]


def settled(
    samples: Sequence[Sample], region: str, start: float, end: float
) -> RGB | None:
    """The mean of a region between two times (a settled picture)."""
    return mean_of(window(samples, region, start, end))


def crossing(
    samples: Sequence[Sample],
    region: str,
    before: Sequence[float],
    after: Sequence[float],
    start: float,
    end: float,
) -> float | None:
    """The first time after `start` that the region is past halfway from `before`
    to `after`, and stays past it for the next two frames.

    Halfway along the line between the two colours (a projection), so a change
    between two equally bright colours counts too.
    """
    span = [a - b for a, b in zip(after[:3], before[:3], strict=True)]
    norm = sum(s * s for s in span)
    if norm == 0:
        return None
    frames = [(t, r[region]) for t, r in samples if start <= t <= end and region in r]

    def fraction(rgb: Sequence[float]) -> float:
        return (
            sum((c - b) * s for c, b, s in zip(rgb[:3], before[:3], span, strict=True))
            / norm
        )

    for i, (t, rgb) in enumerate(frames):
        if fraction(rgb) >= 0.5 and all(
            fraction(x) >= 0.5 for _, x in frames[i + 1 : i + 3]
        ):
            return t
    return None


def first_match(
    samples: Sequence[Sample],
    region: str,
    reference: Sequence[float],
    cal: Calibration,
    th: Thresholds,
    start: float,
    end: float,
    hold: float = 0.5,
) -> float | None:
    """The first time after `start` from which the region matches `reference`
    for `hold` seconds (a restore)."""
    frames = [(t, r[region]) for t, r in samples if start <= t <= end and region in r]
    since: float | None = None
    for t, rgb in frames:
        if difference(rgb, reference, cal) <= th.same_picture:
            if since is None:
                since = t
            if t - since >= hold:
                return since
        else:
            since = None
    return None


def stays(
    samples: Sequence[Sample],
    region: str,
    reference: Sequence[float],
    cal: Calibration,
    th: Thresholds,
    start: float,
    end: float,
) -> tuple[bool, float]:
    """Whether every frame between the times matches `reference`; the worst
    difference."""
    values = window(samples, region, start, end)
    if not values:
        return False, float("nan")
    worst = max(difference(v, reference, cal) for v in values)
    return worst <= th.same_picture, worst


def bridge_restored(
    before: dict[str, Any], after: dict[str, Any]
) -> tuple[bool, list[str]]:
    """The issue's rule on the bridge's light state: on, brightness ±1, xy ±0.005,
    or the same colour temperature. Not proof (#142): the camera decides."""
    problems = []
    if before.get("on") != after.get("on"):
        problems.append("on")
    if before.get("on"):
        bri_a, bri_b = before.get("brightness"), after.get("brightness")
        if bri_a is not None and (bri_b is None or abs(bri_a - bri_b) > 1):
            problems.append("brightness")
        if before.get("mirek") is not None:
            if before.get("mirek") != after.get("mirek"):
                problems.append("colour temperature")
        elif before.get("xy") is not None:
            xa, xb = before["xy"], after.get("xy")
            if xb is None or any(
                abs(p - q) > 0.005 for p, q in zip(xa, xb, strict=True)
            ):
                problems.append("xy")
    return not problems, problems


def delay_stats(delays_ms: Sequence[float]) -> dict[str, Any]:
    """Median and spread of the delays, in ms."""
    if not delays_ms:
        return {"count": 0}
    ordered = sorted(delays_ms)

    def pct(p: float) -> float:
        k = (len(ordered) - 1) * p
        lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
        return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)

    return {
        "count": len(ordered),
        "median": round(statistics.median(ordered)),
        "min": round(ordered[0]),
        "p10": round(pct(0.1)),
        "p90": round(pct(0.9)),
        "max": round(ordered[-1]),
    }


@dataclasses.dataclass
class Step:
    """One checked step: pass, fail or skip, with numbers and a fixed text."""

    name: str
    mode: str
    result: str
    seconds: float | None = None
    numbers: dict[str, Any] = dataclasses.field(default_factory=dict)
    note: str = ""


@dataclasses.dataclass
class Report:
    suite: str
    modes: list[str]
    started: str
    steps: list[Step] = dataclasses.field(default_factory=list)
    delays: dict[str, dict[str, Any]] = dataclasses.field(default_factory=dict)
    calibration: dict[str, Any] = dataclasses.field(default_factory=dict)
    facts: dict[str, Any] = dataclasses.field(default_factory=dict)
    outcome: str = "running"
    exit_code: int = EXIT_PASSED

    def add(self, step: Step) -> Step:
        self.steps.append(step)
        return step

    def finish(self, outcome: str | None = None, exit_code: int | None = None) -> None:
        if outcome is not None:
            self.outcome = outcome
            self.exit_code = EXIT_PASSED if exit_code is None else exit_code
            return
        failed = any(s.result == FAIL for s in self.steps)
        self.outcome = "failed" if failed else "passed"
        self.exit_code = EXIT_FAILED if failed else EXIT_PASSED

    def to_json(self) -> str:
        return json.dumps(dataclasses.asdict(self), indent=2, default=_number)

    def to_markdown(self) -> str:
        counts = {r: sum(s.result == r for s in self.steps) for r in (PASS, FAIL, SKIP)}
        lines = [
            f"## Test bench report: {self.outcome}",
            "",
            f"Suite `{self.suite}`, modes {', '.join(f'`{m}`' for m in self.modes)}, "
            f"started {self.started}. {counts[PASS]} passed, {counts[FAIL]} failed, "
            f"{counts[SKIP]} skipped (exit code {self.exit_code}).",
            "",
        ]
        if self.facts:
            lines += ["| Run | Value |", "|---|---|"]
            lines += [f"| {k} | {_cell(v)} |" for k, v in self.facts.items()]
            lines.append("")
        if self.calibration:
            lines += ["Calibration:", "", "| Calibration | Value |", "|---|---|"]
            lines += [f"| {k} | {_cell(v)} |" for k, v in self.calibration.items()]
            lines.append("")
        if self.delays:
            lines += [
                "Delay from picture to light (ms, camera frames, one clock):",
                "",
                "| Mode / light | n | median | min | p10 | p90 | max |",
                "|---|---|---|---|---|---|---|",
            ]
            for key, d in self.delays.items():
                lines.append(
                    f"| {key} | {d.get('count', 0)} | "
                    + " | ".join(
                        str(d.get(k, "-"))
                        for k in ("median", "min", "p10", "p90", "max")
                    )
                    + " |"
                )
            lines.append("")
        lines += [
            "| Mode | Step | Result | Time (s) | Numbers | Note |",
            "|---|---|---|---|---|---|",
        ]
        for s in self.steps:
            numbers = ", ".join(f"{k} {_cell(v)}" for k, v in s.numbers.items())
            seconds = "" if s.seconds is None else f"{s.seconds:.2f}"
            lines.append(
                f"| {s.mode} | {s.name} | {s.result} | {seconds} "
                f"| {numbers} | {s.note} |"
            )
        return "\n".join(lines) + "\n"


def _number(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 3)
    return str(value)


def _cell(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.3g}" if abs(value) < 10 else f"{value:.0f}"
    if isinstance(value, (list, tuple)):
        return "(" + ", ".join(_cell(v) for v in value) + ")"
    return str(value)


def choose_exposure(
    sweep: dict[
        float, dict[str, tuple[Sequence[float], Sequence[float], float, float]]
    ],
    th: Thresholds,
) -> tuple[float | None, list[str]]:
    """The fixed exposure for the run, from a sweep with the lights in warm
    white at full brightness: per exposure and region (white mean, dark mean,
    p95, share of clipped pixels).

    Every region must be lifted well above dark. A region that clips at some
    exposures must have its p95 under the clip limit. A region that clips at
    every exposure (a spot's core) is judged on its rim: at most
    `max_clipped_share` of its pixels may clip. The brightest exposure that
    fits wins; when only always-clipping regions are there, the darkest one
    (the most rim). Returns (None, always) when no exposure fits.
    """
    regions = {r for values in sweep.values() for r in values}
    always = sorted(
        r
        for r in regions
        if all(v[r][2] >= th.clip_p95 for v in sweep.values() if r in v)
    )

    def fits(values: dict[str, tuple[Any, Any, float, float]]) -> bool:
        for region, (white, dark, p95, clipped) in values.items():
            lift = sum(w - d for w, d in zip(white[:3], dark[:3], strict=True))
            if lift < th.min_white_lift:
                return False
            if region in always:
                if clipped > th.max_clipped_share:
                    return False
            elif p95 >= th.clip_p95:
                return False
        return True

    good = [exposure for exposure, values in sweep.items() if fits(values)]
    if not good:
        return None, always
    return (max(good) if regions - set(always) else min(good)), always


def ambient_ratio(dark: Sequence[float], white: Sequence[float]) -> float:
    """The light with the lights off (daylight, a lamp) as a share of the
    lights' warm white at full brightness, in one region."""
    return sum(dark[:3]) / max(sum(white[:3]), 1.0)


def moved(configured: Sequence[int], found: Sequence[int]) -> float:
    """How far a region's centre moved, in camera pixels."""
    cx = configured[0] + configured[2] / 2 - (found[0] + found[2] / 2)
    cy = configured[1] + configured[3] / 2 - (found[1] + found[3] / 2)
    return round((cx * cx + cy * cy) ** 0.5, 1)


def channel_x(
    area: dict[str, Any], services: list[dict[str, Any]], light_rid: str
) -> float | None:
    """The x position of this light in the area (bridge data: a channel's
    members are entertainment services, each renders a light). A gradient
    strip has several channels; their mean x is where the light is."""
    renders = {
        s["id"]: (s.get("renderer_reference") or {}).get("rid") for s in services
    }
    xs = [
        channel.get("position", {}).get("x", 0.0)
        for channel in area.get("channels", [])
        if any(
            renders.get(member.get("service", {}).get("rid")) == light_rid
            for member in channel.get("members", [])
        )
    ]
    return sum(xs) / len(xs) if xs else None


def channel_spread(area: dict[str, Any]) -> float:
    """How far apart the area's channels sit in x."""
    xs = [c.get("position", {}).get("x", 0.0) for c in area.get("channels", [])]
    return max(xs) - min(xs) if xs else 0.0


def same_picture(a: Sequence[float], b: Sequence[float], tolerance: float) -> bool:
    """Two means of one region at one exposure without a calibration (the
    picture before the run and after the calibration's restore): each channel
    within `tolerance` of the brighter picture's brightest channel."""
    scale = max(max(a[:3]), max(b[:3]), 20.0)
    return all(
        abs(x - y) / scale <= tolerance for x, y in zip(a[:3], b[:3], strict=True)
    )


def overlap(a: Sequence[int], b: Sequence[int]) -> float:
    """The shared area of two boxes (x, y, w, h) as a share of the smaller one."""
    width = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    height = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    if width <= 0 or height <= 0:
        return 0.0
    return width * height / min(a[2] * a[3], b[2] * b[3])


def sides(xs: dict[str, float | None], min_spread: float) -> dict[str, str] | None:
    """Left and right per light from their channels' x, or None when the
    positions do not differ by `min_spread` (or one is unknown)."""
    if not xs or any(x is None for x in xs.values()):
        return None
    values = [x for x in xs.values() if x is not None]
    if max(values) - min(values) < min_spread:
        return None
    middle = (max(values) + min(values)) / 2
    return {
        name: "left" if x < middle else "right"
        for name, x in xs.items()
        if x is not None
    }


def describe_error(err: BaseException) -> str:
    """An error for the terminal: its type and HTTP status only.

    Never its repr: an HTTP error's repr carries the request's headers, and
    with them a token or a Hue key.
    """
    status = getattr(err, "status", None)
    name = type(err).__name__
    return f"{name} (status {status})" if isinstance(status, int) else name


def scrub(text: str, forbidden: Iterable[str]) -> str:
    """Replace any configured name, id, address or key that slipped into the text."""
    for value in sorted(
        {f for f in forbidden if f and len(f) >= 4}, key=len, reverse=True
    ):
        text = text.replace(value, "[removed]")
    return text
