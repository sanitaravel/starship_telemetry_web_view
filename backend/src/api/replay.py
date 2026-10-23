"""The replay a newly connected client gets: the history in `replay` chunks."""

import json
from typing import Iterator

from src.pipeline.history import HistorySnapshot, empty_replay_message

# Records per replay message, so the client parses a big replay in steps
REPLAY_CHUNK_SIZE = 500


def replay_messages(
    snapshot: HistorySnapshot, chunk_size: int = REPLAY_CHUNK_SIZE
) -> Iterator[str]:
    """Yield the replay as pre-serialised JSON texts, one per chunk, lazily.

    Each text is assembled from the stored record strings without re-parsing
    them, and only when it is about to be sent, so building a big replay never
    holds the event loop for more than one chunk.

    Args:
        snapshot: The history to replay.
        chunk_size: Most records per chunk (at least 1).
    """
    if chunk_size < 1:
        raise ValueError("chunk_size must be at least 1")
    records = snapshot.records
    if not records:
        message = empty_replay_message(snapshot.run_id)
        message["payload"]["truncated"] = snapshot.truncated
        yield json.dumps(message, separators=(",", ":"), ensure_ascii=False)
        return

    head = (
        '{"type":"replay","payload":{"run_id":'
        + json.dumps(snapshot.run_id, ensure_ascii=False)
        + ',"chunk":'
    )
    truncated = "true" if snapshot.truncated else "false"
    count = (len(records) + chunk_size - 1) // chunk_size
    for index in range(count):
        chunk = records[index * chunk_size:(index + 1) * chunk_size]
        final = "true" if index == count - 1 else "false"
        yield (
            head + str(index)
            + ',"final":' + final
            + ',"truncated":' + truncated
            + ',"records":[' + ",".join(chunk) + "]}}"
        )
