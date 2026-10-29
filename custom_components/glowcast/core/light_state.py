"""Light state: what the lights show between frames (architecture 3.5).

Pure and synchronous, no asyncio and no Home Assistant imports: the time is an
argument. Fades towards each new target over the measured capture interval,
holds the last colours when the picture suddenly turns black, and applies
brightness and the minimal glow.
"""

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
        self._last_capture: float | None = None
        self._black_since: float | None = None

    def set_target(
        self, colors: list[ChannelColor], frame_black: bool, now: float
    ) -> None:
        """A new frame's colours: fade to them over the last capture interval."""
        if self._last_capture is not None:
            self._fade_duration = now - self._last_capture
        self._last_capture = now
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

    def resume(self, channel_ids: set[str]) -> None:
        """After a retry: keep showing the last colours on the channels that
        still exist, and do not fade over the gap."""
        self._target = {i: rgb for i, rgb in self._target.items() if i in channel_ids}
        self._current = {i: rgb for i, rgb in self._current.items() if i in channel_ids}
        self._fade_from = dict(self._current)
        # The gap during the retry is not a capture interval to fade over.
        self._last_capture = None

    def colors(self, now: float, brightness: float) -> list[ChannelColor]:
        """The faded colours at `now`, with brightness (0-1) and glow applied."""
        if self._fade_duration > 0:
            progress = min(1.0, (now - self._fade_start) / self._fade_duration)
        else:
            progress = 1.0
        level = min(GLOW, brightness)
        glow = tuple(level * c for c in GLOW_COLOR)
        colors = []
        for channel_id, (tr, tg, tb) in self._target.items():
            sr, sg, sb = self._fade_from.get(channel_id, (tr, tg, tb))
            rgb = (
                sr + (tr - sr) * progress,
                sg + (tg - sg) * progress,
                sb + (tb - sb) * progress,
            )
            self._current[channel_id] = rgb
            r, g, b = (
                max(c * brightness, floor) for c, floor in zip(rgb, glow, strict=True)
            )
            colors.append(ChannelColor(channel_id, (r, g, b)))
        return colors

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
