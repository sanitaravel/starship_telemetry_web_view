"""Process-wide singletons shared by the WebSocket endpoint and the REST routes.

Created once, when this module is first imported (via ``src.server`` /
``src.api.app``), right after logging is configured.
"""

import logging

from src.api.connections import ConnectionManager
from src.core.logging_config import configure_logging
from src.pipeline import PipelineOrchestrator
from src.templates.template_registry import TemplateRegistry, load_default_template
from src.vision.gpu_detector import GPUCapabilities, detect_gpu

# Initialize structured logging before any HTTP/WebSocket processing
configure_logging()

logger = logging.getLogger(__name__)

manager = ConnectionManager()
gpu_capabilities: GPUCapabilities = detect_gpu()
template_registry = TemplateRegistry()

# Load the default ROI template
_load_error = load_default_template(template_registry)
if _load_error is not None:
    logger.warning(f"Failed to load default ROI template: {_load_error.message}")

# Create the pipeline orchestrator, wiring broadcast through the connection manager
orchestrator = PipelineOrchestrator(
    gpu_capabilities=gpu_capabilities,
    template_registry=template_registry,
    broadcast=manager.broadcast_json,
)
