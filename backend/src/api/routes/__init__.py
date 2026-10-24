"""REST routers, one ``APIRouter`` per resource.

- ``flights``: /api/previous-flights[/{name}]
- ``trajectories``: /api/trajectories[/{name}]
- ``missions``: /api/missions[/{mission_id}] (GET and PUT)
- ``pipeline``: /api/status and /api/validate-url
- ``timeline``: /api/timeline/status, /api/timeline/active, /api/timeline/{event_id}/status
"""
