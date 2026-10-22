"""Retrying a lost video source with exponential backoff."""

import asyncio
import logging
from typing import Awaitable, Callable


logger = logging.getLogger(__name__)


async def retry_with_backoff(
    attempt: Callable[[], Awaitable[bool]],
    stop_event: asyncio.Event,
    initial_s: float,
    max_s: float,
    multiplier: float,
) -> bool:
    """Call ``attempt`` after growing delays until it succeeds or stop is set.

    Waits ``initial_s`` before the first attempt, then multiplies the delay
    by ``multiplier`` after each failure, capped at ``max_s``. A stop request
    ends the wait at once.

    Returns:
        True once ``attempt`` returns True, False if stop was requested first.
    """
    backoff_s = initial_s

    while not stop_event.is_set():
        logger.info(f"Reconnecting in {backoff_s:.1f}s...")

        # Wait for backoff duration (or stop event)
        try:
            await asyncio.wait_for(
                stop_event.wait(), timeout=backoff_s
            )
            # Stop was requested during backoff
            return False
        except asyncio.TimeoutError:
            # Backoff elapsed, attempt reconnection
            pass

        if await attempt():
            return True

        # Increase backoff with exponential growth, capped at max
        backoff_s = min(backoff_s * multiplier, max_s)

    return False
