"""Centralized logging configuration with JSON-structured output.

Provides a JSONFormatter that outputs single-line JSON log records and a
configure_logging() function that sets up the root logger from environment.
"""

import json
import logging
import os
import sys
import traceback
from datetime import datetime, timezone

from src.logging_context import correlation_id_var, frame_seq_var

_VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

_configured = False


class JSONFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects to stdout."""

    def format(self, record: logging.LogRecord) -> str:
        """Produce a JSON string with required and contextual fields.

        Falls back to the default Formatter output if JSON serialization
        fails, to avoid silently swallowing log entries.
        """
        try:
            return self._format_json(record)
        except Exception:
            # Fallback to default formatter to avoid silent swallowing
            return super().format(record)

    def _format_json(self, record: logging.LogRecord) -> str:
        """Build the JSON log entry from the record."""
        # Required fields present in every entry
        entry: dict = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger_name": record.name,
            "module": record.name,
            "message": record.getMessage(),
        }

        # Contextual fields from contextvars (omit if None)
        cid = correlation_id_var.get()
        if cid is not None:
            entry["correlation_id"] = cid

        seq = frame_seq_var.get()
        if seq is not None:
            entry["frame_seq"] = seq

        # Exception info (only when present)
        if record.exc_info and record.exc_info[0] is not None:
            entry["exc_type"] = record.exc_info[0].__name__
            entry["exc_traceback"] = "".join(
                traceback.format_exception(*record.exc_info)
            )

        # Extra fields from record.__dict__ (duration_ms, fps, etc.)
        for key in ("duration_ms", "fps"):
            value = record.__dict__.get(key)
            if value is not None:
                entry[key] = value

        # Serialize, handling non-serializable values
        return json.dumps(entry, default=_safe_serialize)


def _safe_serialize(obj: object) -> str:
    """Convert non-serializable objects to their string representation."""
    return str(obj)


def configure_logging() -> None:
    """Configure the root logger with JSON formatting and level from environment.

    Reads LOG_LEVEL from the environment, validates it, and attaches a
    StreamHandler with JSONFormatter to the root logger. Idempotent on
    repeated calls — clears existing handlers before reconfiguring.
    """
    global _configured

    root_logger = logging.getLogger()

    # Remove pre-existing handlers (idempotent behavior)
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Determine log level from environment
    env_level = os.environ.get("LOG_LEVEL", "").strip()
    level = logging.INFO  # default

    if env_level:
        if env_level.upper() in _VALID_LEVELS:
            level = getattr(logging, env_level.upper())
        else:
            # Invalid value — will emit warning after handler is attached
            level = logging.INFO

    root_logger.setLevel(level)

    # Attach StreamHandler with JSONFormatter to stdout
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    root_logger.addHandler(handler)

    # Emit warning for invalid LOG_LEVEL after handler is ready
    if env_level and env_level.upper() not in _VALID_LEVELS:
        logging.warning(
            "Invalid LOG_LEVEL '%s' ignored, falling back to INFO", env_level
        )

    _configured = True
