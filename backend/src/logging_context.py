"""Context variables for structured logging propagation.

Provides task-local storage for correlation ID and frame sequence number.
asyncio tasks inherit context from their parent, so pipeline frames spawned
from a WebSocket handler automatically carry the session's correlation ID.
"""

import contextvars

correlation_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    'correlation_id', default=None
)

frame_seq_var: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    'frame_seq', default=None
)
