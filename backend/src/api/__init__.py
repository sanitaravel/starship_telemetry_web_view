"""HTTP and WebSocket layer for the Starship Telemetry system.

- ``app``: ``create_app()``, the FastAPI app with GZip, the static mount and every router.
- ``runtime``: the process singletons (connection manager, ROI templates, pipeline orchestrator),
  created on first import, after ``configure_logging()``.
- ``websocket``: the ``/ws/telemetry`` endpoint, its control-command handlers and status broadcast.
- ``connections``: ``ConnectionManager``, the connected clients and broadcasting to them.
- ``schemas``: the pydantic request/response models.
- ``routes``: the REST routers (previous flights, trajectories, missions, pipeline status).

Nothing is imported here, so importing a leaf module (e.g. ``schemas``) does not
create the singletons; ``src.server`` is the uvicorn entry point.
"""
