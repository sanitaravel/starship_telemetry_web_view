"""The live timeline statuses the backend serves: the active mission and its snapshot.

The dashboard says which mission the run is (``PUT /api/timeline/active``,
sent by its ``CurrentMission`` when the mission is chosen or the page loads);
the backend reads that mission's timeline from its file and re-reads it when
the file is saved. ``TimelineService`` holds the status machine and stamps
every snapshot with a ``revision`` (increasing, also across restarts) and
the ``run_id`` of the run the statuses belong to.

Every method runs on the event loop, never in a worker thread, so the
machine is only touched from one thread; the API reads the mission file in
the threadpool and hands the events over.
"""

import time
from typing import Any, Iterable, Mapping

from src.storage import missions
from src.storage.missions import TimelineEvent
from src.timeline.config import TIMELINE_CONFIG, TimelineConfig
from src.timeline.status import ChangeSet, TimelineStatusMachine, plausible_label, utc_now

MESSAGE_TYPE = "timeline_status"


def read_mission_timeline(mission_id: str) -> list[TimelineEvent]:
    """A mission's timeline from its file; empty when it has none or no file (does file I/O)."""
    record = missions.load_mission(mission_id, missions.MISSIONS_DIR)
    if record is None:
        return []
    return [TimelineEvent.model_validate(e) for e in record.get("timeline") or []]


class TimelineService:
    """The active mission's timeline statuses for the current run."""

    def __init__(
        self,
        config: TimelineConfig = TIMELINE_CONFIG,
        now=utc_now,
        first_revision: int | None = None,
    ) -> None:
        """Args:
            first_revision: Revision before the first change. Defaults to
                the wall clock in ms, so revisions keep growing across
                backend restarts and a client can always drop a stale snapshot.
        """
        self._config = config
        self._machine = TimelineStatusMachine(config, now)
        # The countdown clock shown before T-0: (met, hold), None when none
        self._countdown: tuple[float, bool] | None = None
        self._mission_id: str | None = None
        self._run_id: str | None = None
        self._revision = time.time_ns() // 1_000_000 if first_revision is None else first_revision

    @property
    def mission_id(self) -> str | None:
        """The mission whose timeline is followed, or None."""
        return self._mission_id

    @property
    def machine(self) -> TimelineStatusMachine:
        return self._machine

    def _changed(self, change: ChangeSet) -> bool:
        if change:
            self._revision += 1
        return bool(change)

    def set_mission(self, mission_id: str | None, events: Iterable[TimelineEvent]) -> bool:
        """Follow this mission's timeline (None: none). Returns whether the snapshot changed."""
        switched = mission_id != self._mission_id
        self._mission_id = mission_id
        change = self._machine.set_timeline(events)
        if switched and not change:
            self._revision += 1  # the snapshot names the mission
            return True
        return self._changed(change)

    def reset_run(self, run_id: str) -> bool:
        """A new run started: every status goes back to auto and not yet."""
        self._run_id = run_id
        self._countdown = None
        self._machine.reset()
        self._revision += 1  # the run id changed
        return True

    def on_record(self, record: Mapping[str, Any]) -> bool:
        return self._changed(self._machine.on_record(record))

    def on_met(self, met: float, source: str = "telemetry") -> bool:
        return self._changed(self._machine.on_met(met, source))

    def on_hud_label(self, text: str, met: float | None = None) -> bool:
        """A label read off the HUD's milestone arc; noise and labels of no ``hud`` event are dropped."""
        if not plausible_label(text, self._config) or not self._machine.matches_hud_event(text):
            return False
        return self._changed(self._machine.on_hud_label(text, met))

    def on_countdown(self, met: float, hold: bool = False) -> bool:
        """The countdown clock before T-0 (checked, see ``countdown.py``). Returns whether to broadcast.

        The snapshot then shows this countdown, also when a recycle took it
        back (statuses still follow the furthest MET), so the dashboard's
        clock ticks with it; once the telemetry's clock runs it is ignored.
        """
        if self._machine.clock_source == "telemetry":
            return False
        change = self._machine.on_met(met, "countdown")
        shown = (float(met), bool(hold))
        moved = shown != self._countdown
        self._countdown = shown
        if change or moved:
            self._revision += 1
            return True
        return False

    def override(self, event_id: str, status: str | None) -> bool:
        """Raises KeyError for an unknown event id, ValueError for an unknown status."""
        return self._changed(self._machine.override(event_id, status))

    def snapshot(self) -> dict[str, Any]:
        """The full status payload: small enough to send whole on every change."""
        events = [s.to_dict() for s in self._machine.snapshot()]
        counting_down = self._machine.clock_source == "countdown" and self._countdown is not None
        return {
            "mission_id": self._mission_id,
            "run_id": self._run_id,
            "revision": self._revision,
            # Before T-0 the countdown clock as it reads now, after it the furthest MET
            "met": self._countdown[0] if counting_down else self._machine.met,
            "clock_source": self._machine.clock_source,
            "hold": bool(counting_down and self._countdown[1]),
            "pending": sum(1 for e in events if e["status"] == "pending"),
            "events": events,
        }

    def message(self) -> dict[str, Any]:
        """The ``timeline_status`` WebSocket message with the current snapshot."""
        return {"type": MESSAGE_TYPE, "payload": self.snapshot()}
