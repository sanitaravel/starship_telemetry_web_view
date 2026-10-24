"""The mission timeline's live statuses: which planned events happened, and how well.

- ``config``: ``TimelineConfig``, every threshold (grace window, planned engine counts, speeds).
- ``samples``: reads one telemetry record into what the rules use (MET, engine counts, ship speed).
- ``rules``: ``RuleEngine``, the telemetry rules of ``TIMELINE_RULES`` (pure, no I/O).
- ``status``: ``TimelineStatusMachine``, each event's status from time, HUD labels,
  rules and human overrides (pure, no I/O).
- ``service``: ``TimelineService``, the active mission and the snapshot the API sends.

Nothing is imported here, so importing a leaf module stays cheap.
"""
