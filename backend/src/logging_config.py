"""Centralized logging configuration aligned with uvicorn's log style.

Provides a UvicornStyleFormatter that outputs colored, human-readable log
records matching uvicorn's default output format, and a configure_logging()
function that sets up the root logger from the environment.
"""

import logging
import os
import sys

from src.logging_context import correlation_id_var, frame_seq_var

_VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

_configured = False

# ANSI color codes matching uvicorn's color scheme
COLORS = {
    "DEBUG": "\033[36m",      # cyan
    "INFO": "\033[32m",       # green
    "WARNING": "\033[33m",    # yellow
    "ERROR": "\033[31m",      # red
    "CRITICAL": "\033[1;31m", # bold red
}
RESET = "\033[0m"


def _supports_color() -> bool:
    """Detect whether the output stream supports ANSI colors."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    if sys.platform == "win32":
        # Windows 10+ supports ANSI via virtual terminal processing
        return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


class UvicornStyleFormatter(logging.Formatter):
    """Formats log records in uvicorn's style: LEVEL:     message.

    Matches uvicorn's default access/error log appearance with optional
    ANSI color support and contextual fields (correlation_id, frame_seq).
    """

    def __init__(self, use_colors: bool = True) -> None:
        super().__init__()
        self.use_colors = use_colors and _supports_color()

    def format(self, record: logging.LogRecord) -> str:
        """Format the log record in uvicorn style."""
        levelname = record.levelname
        # Pad the level to 9 chars (matches uvicorn's padding for "CRITICAL")
        padded_level = levelname.ljust(9)

        if self.use_colors:
            color = COLORS.get(levelname, "")
            colored_level = f"{color}{padded_level}{RESET}"
        else:
            colored_level = padded_level

        # Build the message
        message = record.getMessage()

        # Append contextual info if present (correlation_id, frame_seq, duration_ms)
        extras = []
        cid = correlation_id_var.get()
        if cid is not None:
            extras.append(f"cid={cid[:8]}")

        seq = frame_seq_var.get()
        if seq is not None:
            extras.append(f"seq={seq}")

        duration_ms = record.__dict__.get("duration_ms")
        if duration_ms is not None:
            extras.append(f"{duration_ms}ms")

        fps = record.__dict__.get("fps")
        if fps is not None:
            extras.append(f"fps={fps:.1f}")

        if extras:
            context = " ".join(extras)
            message = f"{message} [{context}]"

        # Format exception info if present
        if record.exc_info and record.exc_info[0] is not None:
            exc_text = self.formatException(record.exc_info)
            message = f"{message}\n{exc_text}"

        return f"{colored_level} {message}"


def configure_logging() -> None:
    """Configure application loggers with uvicorn-style formatting.

    Reads LOG_LEVEL from the environment, validates it, and attaches a
    StreamHandler with UvicornStyleFormatter to the 'src' logger (application
    namespace). Uvicorn's own access/error loggers are left untouched.

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

    # Configure the application namespace logger ('src') with uvicorn-style output.
    # This leaves uvicorn.access / uvicorn.error loggers with their default format.
    app_logger = logging.getLogger("src")

    # Remove pre-existing handlers (idempotent behavior)
    for handler in app_logger.handlers[:]:
        app_logger.removeHandler(handler)

    app_logger.setLevel(level)
    app_logger.propagate = False  # Don't propagate to root / uvicorn handlers

    # Attach StreamHandler with UvicornStyleFormatter to stderr (matching uvicorn)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(UvicornStyleFormatter(use_colors=True))
    app_logger.addHandler(handler)

    # Emit warning for invalid LOG_LEVEL after handler is ready
    if env_level and env_level.upper() not in _VALID_LEVELS:
        app_logger.warning(
            "Invalid LOG_LEVEL '%s' ignored, falling back to INFO", env_level
        )

    _configured = True
