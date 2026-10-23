"""The current Run's broadcast telemetry, kept for replay to new clients.

The Broadcaster appends each telemetry record as it sends it, so the history
is in broadcast (ascending sequence_number) order. Records are stored as the
compact JSON text the live broadcast uses, so a replayed record equals its
live copy and the replay can be assembled without re-serialising anything.
"""

import json
import logging
import uuid
from collections import deque
from dataclasses import dataclass
from typing import Any


logger = logging.getLogger(__name__)

# About 30 MB of records at worst; 5.5 h at the default one record per second
MAX_HISTORY_RECORDS = 20_000


@dataclass(frozen=True)
class HistorySnapshot:
    """The history at one moment, safe to send while new records arrive."""

    run_id: str
    records: tuple[str, ...]  # compact JSON of each TelemetryRecord dump, oldest first
    truncated: bool


def empty_replay_message(run_id: str) -> dict[str, Any]:
    """The replay message for an empty history: one final chunk 0.

    Broadcast when a Run starts so every client resets, and sent as the whole
    replay to a client connecting while the history is empty.
    """
    return {
        "type": "replay",
        "payload": {
            "run_id": run_id,
            "chunk": 0,
            "final": True,
            "truncated": False,
            "records": [],
        },
    }


class TelemetryHistory:
    """Bounded, ordered store of the current Run's telemetry records."""

    def __init__(self, max_records: int = MAX_HISTORY_RECORDS) -> None:
        """Initialize an empty history with a fresh run id.

        Args:
            max_records: Most records kept; the oldest is dropped beyond it.
        """
        if max_records < 1:
            raise ValueError("max_records must be at least 1")
        self._records: deque[str] = deque(maxlen=max_records)
        self._run_id = str(uuid.uuid4())
        self._truncated = False

    @property
    def run_id(self) -> str:
        """The id of the Run the records belong to."""
        return self._run_id

    @property
    def truncated(self) -> bool:
        """Whether records of this Run have been dropped to stay in bounds."""
        return self._truncated

    def clear(self) -> str:
        """Empty the history for a new Run and return its new run id."""
        self._records.clear()
        self._run_id = str(uuid.uuid4())
        self._truncated = False
        return self._run_id

    def append(self, payload: dict[str, Any]) -> None:
        """Store one telemetry payload, serialised as broadcast_json does."""
        if len(self._records) == self._records.maxlen and not self._truncated:
            self._truncated = True
            logger.warning(
                "Telemetry history full (%d records), dropping the oldest records "
                "of this run.",
                self._records.maxlen,
            )
        self._records.append(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))

    def snapshot(self) -> HistorySnapshot:
        """Copy the history (synchronously, so no record can slip in or out)."""
        return HistorySnapshot(self._run_id, tuple(self._records), self._truncated)

    def __len__(self) -> int:
        return len(self._records)
