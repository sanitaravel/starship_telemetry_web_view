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
    """Configure application loggers with JSON formatting and level from environment.

    Reads LOG_LEVEL from the environment, validates it, and attaches a
    StreamHandler with JSONFormatter to the 'src' logger (application namespace).
    Uvicorn's access/error loggers are left untouched so they retain their
    default plain-text format.

    Idempotent on repeated calls — clears existing handlers before reconfiguring.
    """
    global _configured

    # Determine log level from environment
    env_level = os.environ.get("LOG_LEVEL", "").strip()
    level = logging.INFO  # default

    if env_level:
        if env_level.upper() in _VALID_LEVELS:
            level = getattr(logging, env_level.upper())
        else:
            # Invalid value — will emit warning after handler is attached
            level = logging.INFO

    # Configure the application namespace logger ('src') with JSON output.
    # This leaves uvicorn.access / uvicorn.error loggers with their default
    # plain-text format (e.g. "INFO:     127.0.0.1:... - "GET ..." 200").
    app_logger = logging.getLogger("src")

    # Remove pre-existing handlers (idempotent behavior)
    for handler in app_logger.handlers[:]:
        app_logger.removeHandler(handler)

    app_logger.setLevel(level)
    app_logger.propagate = False  # Don't propagate to root / uvicorn handlers

    # Attach StreamHandler with JSONFormatter to stdout
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    app_logger.addHandler(handler)

    # Emit warning for invalid LOG_LEVEL after handler is ready
    if env_level and env_level.upper() not in _VALID_LEVELS:
        app_logger.warning(
            "Invalid LOG_LEVEL '%s' ignored, falling back to INFO", env_level
        )

    _configured = True
