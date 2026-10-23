# Requirements Document

## Introduction

Streamers want to put individual pieces of the dashboard into an OBS scene. This feature adds an "OBS" button to each dashboard info piece. The button gives a URL for an OBS Browser Source that shows only that widget: transparent background, the same panel look without chrome, and no way to control the pipeline.

OBS runs each Browser Source in its own CEF instance. That instance has its own localStorage, connects whenever the scene is first shown (often mid-flight), and may reload the page on every scene switch. Two things follow from that:

1. **History comes from the backend.** The server keeps the current run's telemetry records and replays them to every newly connected WebSocket client before live messages. This also fixes a dashboard refresh mid-flight losing its history.
2. **Settings come from the URL.** A snapshot of the relevant dashboard settings (flight colours, engine serials, the graph series, compared previous flights, current mission) is encoded into the overlay URL. To change an overlay's settings, the user copies the URL again. Overlays never read the dashboard's localStorage-backed settings and never write anything.

The design was agreed with the user on 2026-10-23. This spec turns it into a plan grounded in the code as of `34d31cc` (after the backend and frontend refactors).

## Glossary

- **Dashboard**: the normal frontend page (`frontend/src/app/main.ts` without an `overlay` query parameter).
- **Overlay**: the frontend page opened with `?overlay=<widget id>&s=<snapshot>`, rendering a single widget for OBS.
- **Overlay_Widget**: one of the widgets an Overlay can show: `telemetry`, `met`, `engines-superheavy`, `engines-starship`, `globe`, and one `graph-<SeriesKey>` per entry of `SERIES_OPTIONS` in `frontend/src/features/graphs/panels.ts` (`speedSuperHeavy`, `speedStarship`, `altitudeSuperHeavy`, `altitudeStarship`, `accelerationSuperHeavy`, `accelerationStarship`).
- **Overlay_Snapshot**: the versioned settings object encoded into the overlay URL as base64url JSON.
- **Overlay_Router**: the code in `frontend/src/app/` that decides between Dashboard and Overlay and mounts the Overlay_Widget.
- **OBS_Popover**: the popover opened by an OBS button that shows and copies the overlay URL (`frontend/src/ui/obs-popover.ts`).
- **Telemetry_History**: the backend store of the current run's broadcast telemetry records (`backend/src/pipeline/history.py`).
- **Replay**: the `replay` WebSocket messages the server sends a client right after it connects, carrying the Telemetry_History in chunks.
- **Run**: the span from one `PipelineOrchestrator.start()` to the next. A Run's history survives `stop()`.
- **Connection_Manager**: `ConnectionManager` in `backend/src/api/connections.py`.
- **Broadcaster**: `Broadcaster` in `backend/src/pipeline/broadcast.py`, the single task that sends queued pipeline messages in order.
- **Pipeline_Orchestrator**: `PipelineOrchestrator` in `backend/src/pipeline/orchestrator.py`.
- **State_Manager**: `StateManager` in `frontend/src/state/state-manager.ts`.

## Requirements

### Requirement 1: Backend telemetry history

**User Story:** As a viewer who opens the dashboard or an overlay mid-flight, I want the server to remember the current run's telemetry, so that graphs, engine events and downloads are complete.

#### Acceptance Criteria

1. WHEN the Broadcaster sends a `telemetry` message, THE Telemetry_History SHALL append that record, in the same synchronous step as choosing which clients receive it live.
2. THE Telemetry_History SHALL keep records in broadcast order, which is ascending `sequence_number` order.
3. THE Telemetry_History SHALL store each record serialised exactly as the live broadcast serialises it (compact separators, `ensure_ascii=False`), so a replayed record equals its live copy.
4. THE Telemetry_History SHALL hold at most `MAX_HISTORY_RECORDS` records (default 20 000).
5. IF appending would exceed `MAX_HISTORY_RECORDS`, THEN THE Telemetry_History SHALL drop the oldest record, mark itself truncated, and log one WARNING per Run.
6. WHEN the Pipeline_Orchestrator starts a Run, THE Telemetry_History SHALL be cleared and given a new run id before the frame extractor starts.
7. WHEN the Pipeline_Orchestrator stops, THE Telemetry_History SHALL keep its records, so a client connecting after the flight still gets them.
8. THE Telemetry_History SHALL NOT store `frame`, `status`, `error` or `validation_result` messages.

### Requirement 2: Replay on connect

**User Story:** As an OBS user whose browser source reloads on every scene switch, I want each new connection to receive the run so far and then the live stream, with nothing missing and nothing twice.

#### Acceptance Criteria

1. WHEN a WebSocket client connects, THE Connection_Manager SHALL take a Telemetry_History snapshot and register the client in the same synchronous step (no `await` in between).
2. WHEN a client is registered, THE Connection_Manager SHALL send it the snapshot as one or more `replay` messages of at most `REPLAY_CHUNK_SIZE` records each (default 500), with `chunk` numbered from 0 and `final: true` on the last one only.
3. WHEN the snapshot is empty, THE Connection_Manager SHALL send exactly one `replay` message with `chunk: 0`, `final: true` and no records.
4. WHILE a client's replay is being sent, THE Connection_Manager SHALL queue every message broadcast to that client and send the queue in order once the replay is done, before the client receives any newer message.
5. WHILE a client's replay is being sent, THE Connection_Manager SHALL drop `frame` messages for that client instead of queueing them.
6. IF a replaying client's queue exceeds `MAX_PENDING_MESSAGES` (default 2 000), THEN THE Connection_Manager SHALL close that connection and log a WARNING; the client's reconnect gets a fresh replay.
7. THE records a client receives (replay followed by live) SHALL have strictly increasing, gap-free `sequence_number`s from the first record kept in the Telemetry_History to the latest broadcast.
8. IF sending the replay fails, THEN THE WebSocket endpoint SHALL disconnect the client and log the failure once, the same way a failed session is logged today.
9. WHEN a Run starts, THE Pipeline_Orchestrator SHALL broadcast an empty `replay` message (`chunk: 0`, `final: true`, the new run id) to all connected clients before any telemetry of the new Run.

### Requirement 3: Frontend replay ingestion

**User Story:** As a viewer, I want a replay burst to rebuild the dashboard quickly without freezing it.

#### Acceptance Criteria

1. THE frontend WebSocket client SHALL accept `replay` messages whose payload has `run_id` (string), `chunk` (integer ≥ 0), `final` (boolean), `truncated` (boolean) and `records` (array).
2. WHEN a `replay` message with `chunk: 0` arrives, THE State_Manager SHALL clear the time series, telemetry records, engine events, separation MET, latest telemetry, MET clock and engine event detector before ingesting its records.
3. WHEN a `replay` message arrives, THE State_Manager SHALL ingest its records in order with the same rules as live telemetry and notify subscribers once for the whole message.
4. THE State_Manager SHALL produce the same `timeSeries`, `telemetryRecords`, `engineEvents`, `separationMetSeconds` and `latestTelemetry` from a replay of records R (any chunking) as from receiving R one by one as live `telemetry` messages after a reset.
5. THE State_Manager SHALL NOT count replayed records in the processing FPS meter.
6. WHEN the WebSocket reconnects, THE State_Manager SHALL end up with each record once (the new replay's chunk 0 resets what the old connection had received).

### Requirement 4: Overlay URL and settings snapshot

**User Story:** As a streamer, I want the overlay URL to carry my current settings, so that the OBS source looks like my dashboard without sharing its storage.

#### Acceptance Criteria

1. THE overlay URL SHALL be `<dashboard origin and path>?overlay=<widget id>&s=<base64url JSON of the Overlay_Snapshot>`.
2. THE Overlay_Snapshot SHALL include only the settings its widget uses: graph widgets the compared flights with their resolved colours, the globe its trajectory colour overrides and hidden trajectories, each engine widget its vehicle's serials and the mission, ship and booster names; `telemetry` and `met` carry none.
3. THE Overlay_Snapshot SHALL carry a format version `v`; decoding a snapshot with an unknown version SHALL fall back to defaults and log a WARNING.
4. FOR ALL valid Overlay_Snapshots, decoding the encoding SHALL give back an equal snapshot (round trip), including non-ASCII mission names.
5. IF the `s` parameter is missing, not base64url, not JSON or not a valid snapshot, THEN THE Overlay_Router SHALL render the widget with default settings and log a WARNING, and SHALL NOT throw.
6. THE overlay URL for the largest snapshot (33 Super Heavy serials of 8 characters with the mission, ship and booster names at their maximum length, or 6 compared flights with colours) SHALL be at most 2 000 characters.
7. WHEN the `overlay` parameter names no known Overlay_Widget, THE Overlay_Router SHALL show a short "Unknown overlay" message listing the valid ids.

### Requirement 5: OBS button and popover

**User Story:** As a streamer, I want an OBS button on each info piece, so that I can copy a ready-made browser source URL.

#### Acceptance Criteria

1. THE Dashboard SHALL show an OBS button on the telemetry display, on each vehicle's engine diagram, on the ship globe and on the time-series graphs.
2. WHEN the OBS button is clicked, THE OBS_Popover SHALL open next to it (using `anchorPanel` and `bindDismiss` from `frontend/src/ui/popover.ts`) and show the widget choices of that panel, the URL for the selected one, a Copy button and a suggested OBS source size.
3. THE OBS_Popover SHALL offer `telemetry` and `met` on the telemetry display, the six graph series on the graphs panel (preselecting the series currently shown), and a single widget on the other panels.
4. THE OBS_Popover SHALL build the URL from the settings at the moment it opens and again whenever the selection changes.
5. WHEN Copy is clicked, THE OBS_Popover SHALL write the URL to the clipboard and confirm it; IF the clipboard API is unavailable or rejects, THEN it SHALL select the URL text so the user can copy it by hand.
6. THE OBS_Popover SHALL close on Escape and on a press outside it, like the colour and engine settings popovers.

### Requirement 6: Overlay rendering

**User Story:** As a streamer, I want each overlay to look like its dashboard panel on a transparent background, without controls.

#### Acceptance Criteria

1. WHEN an Overlay is loaded, THE Overlay_Router SHALL render only the selected Overlay_Widget, with a transparent page background.
2. THE Overlay SHALL NOT render the dashboard header, pipeline controls, downloads, frame preview, buttons, selects, checkboxes, the graphs toolbar or zoom hint, the globe's zoom, centre, history and download controls, or the engine diagrams' settings gear.
3. THE `graph-<SeriesKey>` Overlay_Widget SHALL draw that series for the live flight and the snapshot's compared flights in their snapshot colours, with the engine event markers the dashboard draws, and with zoom and pan disabled.
4. THE `engines-superheavy` and `engines-starship` Overlay_Widgets SHALL draw one vehicle's diagram, its circles carrying the snapshot's serials as the dashboard does.
5. THE `globe` Overlay_Widget SHALL follow the latest ship, draw the recorded trajectories not hidden in the snapshot in the snapshot's colours, and keep the active ship's data card.
6. THE `met` Overlay_Widget SHALL show only the mission elapsed time.

### Requirement 7: Overlays are read-only and isolated

**User Story:** As a streamer, I want overlays to be unable to change anything, so that an OBS scene can never stop my pipeline or overwrite my settings.

#### Acceptance Criteria

1. THE Overlay SHALL NOT send any WebSocket command (`start`, `stop`, `validate_url`, `set_interval` or any other).
2. THE Overlay SHALL NOT read the `flight-colors.*`, `engine-serials.*` or `engine-mission.current` localStorage keys and SHALL NOT write any localStorage key.
3. THE Overlay SHALL NOT send `PUT /api/missions/{id}` or any other non-GET HTTP request.
4. WHERE a setting is not in the Overlay_Snapshot, THE Overlay SHALL use the dashboard's built-in default, never a stored value.

### Requirement 8: Reload and late connection

**User Story:** As a streamer, I want an overlay to recover by itself when OBS reloads it or opens it late.

#### Acceptance Criteria

1. WHEN an Overlay loads while a Run is in progress, THE Overlay SHALL show the Run's history from the Replay and then the live updates.
2. WHEN the WebSocket connection drops, THE Overlay SHALL reconnect with the existing backoff of `TelemetryWebSocket` and rebuild its state from the new Replay.
3. WHEN the backend restarts, THE Overlay SHALL reconnect and show whatever the new process replays (an empty history until a new Run).
4. WHEN a new Run starts, THE Overlay and the Dashboard SHALL clear their live data on the empty `replay` message (Requirement 2.9).
