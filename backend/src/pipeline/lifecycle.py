"""Helpers for starting and stopping a pipeline run.

Resolving the ROI template to start with and cancelling the in-flight frame
tasks on stop; neither touches the orchestrator's state.
"""

import asyncio
import logging

from src.core.models import ROIConfiguration
from src.templates.template_registry import TemplateNotFoundError, TemplateRegistry


logger = logging.getLogger(__name__)


def resolve_template(
    registry: TemplateRegistry, active_template_name: str
) -> ROIConfiguration | None:
    """The active ROI template, else the default, else None (after logging why)."""
    template_result = registry.get(active_template_name)
    if isinstance(template_result, TemplateNotFoundError):
        # Fallback to default
        template_result = registry.get_default()
        if isinstance(template_result, TemplateNotFoundError):
            logger.error(
                f"No ROI template available: {template_result.template_name}. "
                f"Available: {template_result.available_templates}"
            )
            return None
    return template_result


async def cancel_in_flight(in_flight_tasks: set[asyncio.Task]) -> None:
    """Cancel the in-flight frame tasks and wait up to 5 seconds for them to end.

    Tasks still pending after that are abandoned and the count is logged as an
    error. The set itself is left for the caller to clear.
    """
    tasks_to_cancel = set(in_flight_tasks)
    if tasks_to_cancel:
        logger.info(
            "Cancelling %d in-flight frame tasks for graceful shutdown.",
            len(tasks_to_cancel),
        )
        for task in tasks_to_cancel:
            task.cancel()

        # Wait up to 5 seconds for tasks to terminate
        done, pending = await asyncio.wait(
            tasks_to_cancel, timeout=5.0
        )

        # If tasks still pending after 5 seconds, abandon and log error
        if pending:
            logger.error(
                "Graceful shutdown: %d in-flight tasks did not terminate "
                "within 5 seconds. Abandoning remaining tasks.",
                len(pending),
            )
