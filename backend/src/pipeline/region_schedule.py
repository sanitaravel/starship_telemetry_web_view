"""Which ROIs each frame reads.

Most ROIs are read on every frame. The optional ones are not:

- Before T-0 the countdown ROIs (``countdown_time``, ``time_sign``) are read
  every ``countdown_read_interval_s`` seconds of video.
- After T-0 the HUD milestone arc's label spots are read every
  ``hud_read_interval_s``: ``timeline_below`` always at that rate, and
  ``stage_sep_text`` too once stage separation is known (before it, stage
  assignment needs it on every frame).

The decision runs on the event loop as each frame starts processing, so it
needs no lock. The rate follows the frame's position in the video, not the
frame count, so it holds at any skip interval.
"""

from typing import Mapping

from src.core.models import ROIRect

TIMELINE_BELOW = "timeline_below"
STAGE_SEP_TEXT = "stage_sep_text"
COUNTDOWN_ROIS = ("countdown_time", "time_sign")
# ROIs read only on some frames; every other ROI of the template is read on all
OPTIONAL_ROIS = frozenset({TIMELINE_BELOW, *COUNTDOWN_ROIS})


class RegionSchedule:
    """Picks each frame's ROIs, reading the optional ones at reduced rates."""

    def __init__(self, hud_interval_s: float, countdown_interval_s: float) -> None:
        self._hud_interval_s = hud_interval_s
        self._countdown_interval_s = countdown_interval_s
        self.reset()

    def reset(self) -> None:
        """A new run: the next frame of each kind reads its ROIs."""
        self._last_hud: int | None = None
        self._last_countdown: int | None = None

    @staticmethod
    def _due(last: int | None, video_s: float | None, interval_s: float) -> tuple[bool, int | None]:
        """Whether a read is due at this video time, and the new last slot."""
        if video_s is None or interval_s <= 0:
            return True, last
        slot = int(video_s // interval_s)
        if last is None or slot > last:
            return True, slot
        return False, last

    def regions(
        self,
        text_regions: Mapping[str, ROIRect],
        video_s: float | None,
        *,
        t_zero: bool,
        post_separation: bool,
    ) -> tuple[dict[str, ROIRect], bool]:
        """The ROIs this frame reads, and whether it reads the countdown ROIs.

        Args:
            text_regions: Every text ROI of the template.
            video_s: The frame's position in the video in seconds (None when
                unknown: every frame is then due).
            t_zero: Whether T-0 was already detected.
            post_separation: Whether stage separation is known.
        """
        regions = {k: v for k, v in text_regions.items() if k not in OPTIONAL_ROIS}
        countdown = False
        if not t_zero:
            if any(k in text_regions for k in COUNTDOWN_ROIS):
                countdown, self._last_countdown = self._due(
                    self._last_countdown, video_s, self._countdown_interval_s
                )
                if countdown:
                    regions.update({k: text_regions[k] for k in COUNTDOWN_ROIS if k in text_regions})
            return regions, countdown

        hud, self._last_hud = self._due(self._last_hud, video_s, self._hud_interval_s)
        if hud and TIMELINE_BELOW in text_regions:
            regions[TIMELINE_BELOW] = text_regions[TIMELINE_BELOW]
        if post_separation and not hud:
            regions.pop(STAGE_SEP_TEXT, None)
        return regions, False
