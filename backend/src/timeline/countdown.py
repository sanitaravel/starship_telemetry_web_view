"""The countdown clock before T-0, from the readings of the two clocks on screen.

Before liftoff the webcast shows the countdown in the top-left corner
("T- 00:12:31", ROI ``countdown_time``) until about T-29 s; the HUD clock at
the bottom ("T- 00:00:27", ROI ``time`` with its sign in ``time_sign``) takes
over at about T-27 s, so for ~2 s no clock is on screen. ``CountdownTracker``
turns these readings, taken about once a second, into a checked countdown
MET (negative seconds) for the timeline:

- The first reading is only believed once ``countdown_confirm_reads``
  readings agree with each other and with the video clock.
- A reading close to what the video clock predicts is accepted; one far
  from it (a misread, a recycle to an earlier time, a jump) only once
  ``countdown_confirm_reads`` consecutive readings agree with it.
- A visible clock that keeps showing the same value for longer than
  ``countdown_hold_s`` is a hold: the MET stops there until it changes.
- No clock on screen is not a hold: the MET runs on from the last reading
  for at most ``countdown_gap_max_s``.

Pure logic, no I/O; video time is the frame's position in seconds.
"""

from dataclasses import dataclass

from src.timeline.config import TIMELINE_CONFIG, TimelineConfig
from src.timeline.samples import parse_met

# Where a tick's MET came from
TOP_CLOCK = "countdown_time"
HUD_CLOCK = "time"
EXTRAPOLATED = "extrapolated"


@dataclass(frozen=True)
class CountdownTick:
    """The countdown at one frame: its MET (seconds, negative), where it was read, and whether it holds."""

    met: float
    source_roi: str
    hold: bool


def clock_reading(
    hud_time: str | None, hud_sign: str | None, top_clock: str | None
) -> tuple[int, str] | None:
    """The countdown on screen in seconds (negative) and the ROI it was read from, or None.

    Args:
        hud_time: The HUD clock's parsed ``HH:MM:SS`` (unsigned), if read.
        hud_sign: ``"+"`` or ``"-"`` read left of the HUD clock, if read.
        top_clock: The top-left clock's parsed signed ``-HH:MM:SS``, if read.

    The HUD clock only counts when its sign reads ``-``: it shows ``T+``
    after liftoff, and without the sign the two cannot be told apart.
    """
    if hud_time and hud_sign == "-":
        seconds = parse_met(f"-{hud_time}")
        if seconds is not None and seconds <= 0:
            return seconds, HUD_CLOCK
    if top_clock:
        seconds = parse_met(top_clock)
        if seconds is not None and seconds <= 0:
            return seconds, TOP_CLOCK
    return None


class CountdownTracker:
    """Checked countdown MET from successive clock readings, in video order."""

    def __init__(self, config: TimelineConfig = TIMELINE_CONFIG) -> None:
        self._config = config
        self.reset()

    def reset(self) -> None:
        # (value shown, video time it first showed) of the accepted clock
        self._anchor: tuple[int, float] | None = None
        # (value, video time, roi) of the last accepted reading
        self._last: tuple[int, float, str] | None = None
        # Readings that disagree with the accepted clock, newest last
        self._candidates: list[tuple[int, float]] = []
        self._hold = False

    def push(self, video_s: float, reading: int | None, source_roi: str = HUD_CLOCK) -> CountdownTick | None:
        """One frame's reading (None: no clock on screen). Returns the countdown, or None while unknown."""
        if reading is None:
            return self._extrapolate(video_s)
        if self._anchor is None or self._last is None:
            return self._confirm(video_s, reading, source_roi)

        value, anchor_s = self._anchor
        if reading == value:
            # The same second still showing: running, or holding once it
            # outlasts the second plus the hold allowance
            self._candidates = []
            self._hold = video_s - anchor_s > self._config.countdown_hold_s
            self._last = (reading, video_s, source_roi)
            met = float(reading) if self._hold else reading + min(0.999, max(0.0, video_s - anchor_s))
            return CountdownTick(min(met, 0.0), source_roi, self._hold)

        if abs(reading - self._predict(video_s)) <= self._config.countdown_tolerance_s:
            return self._accept(video_s, reading, source_roi)
        # Far from the clock: a misread, or a recycle/jump to be confirmed
        tick = self._confirm(video_s, reading, source_roi)
        return tick if tick is not None else self._extrapolate(video_s)

    def _predict(self, video_s: float) -> float:
        """The value the clock should show now: on from the anchor, or from the last reading while it held."""
        assert self._anchor is not None and self._last is not None
        if self._hold:
            value, at, _ = self._last
            return value + (video_s - at)
        value, at = self._anchor
        return value + (video_s - at)

    def _confirm(self, video_s: float, reading: int, source_roi: str) -> CountdownTick | None:
        """Keep a disagreeing reading; accept it once enough consecutive ones agree."""
        if self._candidates:
            value, at = self._candidates[-1]
            if abs(reading - (value + (video_s - at))) <= self._config.countdown_tolerance_s:
                self._candidates.append((reading, video_s))
            else:
                self._candidates = [(reading, video_s)]
        else:
            self._candidates = [(reading, video_s)]
        if len(self._candidates) >= self._config.countdown_confirm_reads:
            return self._accept(video_s, reading, source_roi)
        return None

    def _accept(self, video_s: float, reading: int, source_roi: str) -> CountdownTick:
        self._anchor = (reading, video_s)
        self._last = (reading, video_s, source_roi)
        self._candidates = []
        self._hold = False
        return CountdownTick(float(min(reading, 0)), source_roi, False)

    def _extrapolate(self, video_s: float) -> CountdownTick | None:
        """No usable clock this frame: run on from the last reading, for a while."""
        if self._last is None:
            return None
        value, at, _ = self._last
        if video_s - at > self._config.countdown_gap_max_s or video_s < at:
            return None
        if self._hold:
            # A held clock that left the screen: the hold is not known to go
            # on, so the countdown runs from where it was last seen
            base = value + (video_s - at)
        else:
            assert self._anchor is not None
            base = self._anchor[0] + (video_s - self._anchor[1])
        return CountdownTick(min(base, 0.0), EXTRAPOLATED, False)
