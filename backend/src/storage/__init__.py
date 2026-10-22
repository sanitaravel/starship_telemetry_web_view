"""File-backed data served by the API, all under the repository root.

- ``missions``: one JSON file per mission in ``missions/`` (names and engine serials).
- ``previous_flights``: recorded flights in ``results_previous/``.
- ``trajectories``: recorded ship paths in ``trajectories/positions_ship<N>.csv``.

Nothing is imported here, so importing a leaf module stays cheap.
"""
