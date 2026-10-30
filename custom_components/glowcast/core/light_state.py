"""Light state: what the lights show between frames (architecture 3.5).

Pure and synchronous, no asyncio and no Home Assistant imports: the time is an
argument. Fades towards each new target over the measured capture interval,
holds the last colours when the picture suddenly turns black, and applies
brightness and the minimal glow. When the lights have just been claimed, the
first colours fade in from what each light showed before (#153).
"""

from collections.abc import Mapping

from .color_engine import BLACK
from .models import ChannelColor

GLOW = 0.10  # level of the minimal glow
# Colour of the minimal glow: a warm white of about 2700K (D11).
GLOW_COLOR = (1.0, 0.78, 0.50)
# When the whole picture suddenly turns black, keep the last colours this long
# before fading to the glow: a TV being switched off shows an (almost) black
# switch-off animation for up to 3.0 s before it reports that it is off
# (measured, see "Verified facts").
BLACK_HOLD = 3.5
# When the lights are claimed (at a start, and when the picture is back after
# no picture), the first colours fade in from each light's own state over
# this long, so the room changes calmly (#153). The bridge keeps a light's own
# state from the stream start until the first colour (measured, "Verified
# facts").
FADE_IN = 2.0
# A channel whose colour changes by more than this (the largest change of
# one component, 0-1, in engine colours) between two frames has a cut: it
# fades over CUT_FADE instead of the capture interval, so a scene change shows
# at once while small changes stay soft (#35).
CUT = 0.25
CUT_FADE = 0.08

RGB = tuple[float, float, float]


class LightState:
    """The colours per channel over time, from the colour engine's targets."""

    def __init__(self) -> None:
        """Initialize with nothing to show."""
        # Fading happens in engine colour space, before brightness and glow.
        self._current: dict[str, RGB] = {}
        self._fade_from: dict[str, RGB] = {}
        self._target: dict[str, RGB] = {}
        self._fade_start = 0.0
        self._fade_duration = 0.0
        # The channels with a cut in the current fade: they fade over CUT_FADE.
        self._cuts: set[str] = set()
        self._last_capture: float | None = None
        self._black_since: float | None = None
        # The fade-in after a claim, in sent colours (after brightness and
        # glow): from each light's own state, from the first target on.
        self._fade_in_from: dict[str, RGB] | None = None
        self._fade_in_start: float | None = None

    def fade_in(self, starts: Mapping[str, RGB | None]) -> None:
        """The lights were just claimed: fade the first colours in.

        `starts` has what each channel showed before, as it is sent (0-1); a
        channel that is missing or None fades in from dark. The fade-in starts
        at the first target and takes FADE_IN; later targets change where it
        goes, they do not restart it.
        """
        self._fade_in_from = {
            channel_id: rgb for channel_id, rgb in starts.items() if rgb is not None
        }
        self._fade_in_start = None

    def set_target(
        self, colors: list[ChannelColor], frame_black: bool, now: float
    ) -> None:
        """A new frame's colours: fade to them over the last capture interval."""
        if self._last_capture is not None:
            self._fade_duration = now - self._last_capture
        self._last_capture = now
        if self._fade_in_from is not None and self._fade_in_start is None:
            self._fade_in_start = now
        target = {color.channel_id: color.rgb for color in colors}
        if self._hold_black(frame_black, now):
            return
        if not self._current:
            # The first target is shown straight away.
            self._current = dict(target)
            self._fade_duration = 0.0
        self._fade_from = dict(self._current)
        self._target = target
        self._fade_start = now
        self._cuts = {
            channel_id
            for channel_id, rgb in target.items()
            if channel_id in self._fade_from
            and rgb != BLACK  # after the hold on black, fade softly to the glow
            and max(
                abs(a - b)
                for a, b in zip(rgb, self._fade_from[channel_id], strict=True)
            )
            > CUT
        }

    def resume(self, channel_ids: set[str]) -> None:
        """After a retry: keep showing the last colours on the channels that
        still exist, and do not fade over the gap."""
        self._target = {i: rgb for i, rgb in self._target.items() if i in channel_ids}
        self._current = {i: rgb for i, rgb in self._current.items() if i in channel_ids}
        self._fade_from = dict(self._current)
        # The gap during the retry is not a capture interval to fade over.
        self._last_capture = None
        # The lights come back where they were, not from their own state.
        self._fade_in_from = None

    def colors(self, now: float, brightness: float) -> list[ChannelColor]:
        """The faded colours at `now`, with brightness (0-1) and glow applied."""
        if self._fade_duration > 0:
            progress = min(1.0, (now - self._fade_start) / self._fade_duration)
        else:
            progress = 1.0
        cut_progress = min(1.0, (now - self._fade_start) / CUT_FADE)
        level = min(GLOW, brightness)
        glow = tuple(level * c for c in GLOW_COLOR)
        fade_in = self._fade_in_progress(now)
        colors = []
        for channel_id, (tr, tg, tb) in self._target.items():
            sr, sg, sb = self._fade_from.get(channel_id, (tr, tg, tb))
            p = cut_progress if channel_id in self._cuts else progress
            rgb = (
                sr + (tr - sr) * p,
                sg + (tg - sg) * p,
                sb + (tb - sb) * p,
            )
            self._current[channel_id] = rgb
            lit = [c * brightness for c in rgb]
            # The glow is for dark channels: all of it on black, less the
            # closer the brightest component gets to the glow level, none at or
            # above it. So bright colours stay pure, and no component goes
            # above the glow level or the brightness setting (#43).
            share = max(0.0, 1 - max(lit) / level) if level > 0 else 0.0
            sent = [c + share * w for c, w in zip(lit, glow, strict=True)]
            if fade_in < 1.0 and self._fade_in_from is not None:
                # Between the light's own state and the colours with brightness
                # and glow applied, so a change of brightness shows at once.
                own = self._fade_in_from.get(channel_id, BLACK)
                sent = [o + (c - o) * fade_in for o, c in zip(own, sent, strict=True)]
            r, g, b = sent
            colors.append(ChannelColor(channel_id, (r, g, b)))
        return colors

    def _fade_in_progress(self, now: float) -> float:
        """How far the fade-in is (0-1); 1 when there is none (any more)."""
        if self._fade_in_from is None or self._fade_in_start is None:
            return 1.0
        elapsed = now - self._fade_in_start
        if elapsed >= FADE_IN:
            self._fade_in_from = None  # done
            return 1.0
        return max(0.0, elapsed / FADE_IN)

    def _hold_black(self, frame_black: bool, now: float) -> bool:
        """Whether to keep the last colours instead of the new frame's.

        Only a picture that suddenly turns black is held (for BLACK_HOLD), so
        the lights go straight from the picture to their previous state when
        the TV is switched off. Starting on black shows the glow at once.
        """
        showing_picture = any(rgb != BLACK for rgb in self._target.values())
        if not frame_black:
            self._black_since = None
            return False
        if not showing_picture:
            return False
        if self._black_since is None:
            self._black_since = now
        return now - self._black_since < BLACK_HOLD
