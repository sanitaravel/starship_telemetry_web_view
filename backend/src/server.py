"""Uvicorn entry point: ``uvicorn src.server:app``.

The HTTP/WebSocket layer lives in the ``src.api`` package; importing it
configures logging and creates the pipeline singletons (see ``src.api.runtime``).
"""

from src.api.app import create_app

# Create the default app instance for direct use with uvicorn
app = create_app()
