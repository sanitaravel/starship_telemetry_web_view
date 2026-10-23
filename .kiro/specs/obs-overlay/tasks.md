# Implementation Plan: OBS Overlay

## Overview

Two phases. Phase A (backend replay) is useful on its own: once it lands, a dashboard refresh mid-flight keeps its history. Phase B (frontend) builds on A's `replay` message and then adds the overlays. Each task is small enough to review alone and comes with its tests; run `pytest` from `backend/` with the venv's `python.exe`, and `npm test` plus `npm run build` from `frontend/`.

Agents: coder writes code and the tests named in a task; tester runs the suites and the optional property tasks (`*`); perf-benchmarker and ui-checker take the tasks marked for them; docs-researcher answers the four questions at the end of `design.md` before tasks 3 and 11.

## Tasks

### Phase A: Backend replay

- [ ] 1. Telemetry history store
  - [ ] 1.1 Create `backend/src/pipeline/history.py` with `MAX_HISTORY_RECORDS`, `HistorySnapshot`, `TelemetryHistory` (`deque(maxlen=…)` of compact JSON strings, `run_id`, `clear()`, `append()`, `snapshot()`, `__len__`, one WARNING per Run when it starts dropping) and `empty_replay_message(run_id) -> dict`
    - Tests: `backend/tests/test_telemetry_history.py`: clear gives a new uuid4 run id; append/snapshot order; truncation flag and the single warning (`src_caplog`); print the compact size of a full `TelemetryRecord` dump for the design's memory estimate
    - _Requirements: 1.2, 1.3, 1.4, 1.5, 1.8_
  - [ ]* 1.2 Hypothesis tests in `test_telemetry_history.py`
    - **Property 1: History keeps the newest records in order**
    - **Property 2: Replayed record text equals the live text**
    - **Validates: Requirements 1.2, 1.3, 1.4, 1.5**

- [ ] 2. Replay chunk messages
  - [ ] 2.1 Create `backend/src/api/replay.py` with `REPLAY_CHUNK_SIZE = 500` and the lazy `replay_messages(snapshot, chunk_size)` generator, assembling each text from the stored record strings (no re-parse); chunk 0 of an empty snapshot is `json.dumps(empty_replay_message(run_id))` with the same separators
    - Tests: `backend/tests/test_replay.py`: empty snapshot gives one final chunk 0; 1 001 records at size 500 gives 3 chunks; every text passes `json.loads`; `truncated` passes through
    - _Requirements: 2.2, 2.3_
  - [ ]* 2.2 Hypothesis test in `test_replay.py`
    - **Property 3: Chunking is lossless**
    - **Validates: Requirements 2.2, 2.3**

- [ ] 3. Replay-aware `ConnectionManager`
  - [ ] 3.1 In `backend/src/api/connections.py` add `MAX_PENDING_MESSAGES`, the per-connection pending queue, `connect(websocket, replay=None)` (snapshot and register with no `await` in between, send chunks, flush loop, switch to live in the same step as the empty check), `broadcast(message, droppable=False)` routing replaying clients to their queue (frames dropped, overflow closes with 1013 and disconnects), `broadcast_json` passing `droppable` for `frame`, and `disconnect` dropping the queue
    - Tests: extend `TestConnectionManager` in `backend/tests/test_server.py` (existing tests still pass with `replay=None`); new `backend/tests/test_connection_replay.py`: a broadcast during replay is delivered after the last chunk; a frame during replay is not delivered; overflow closes the socket; disconnect during replay leaves no pending entry
    - _Requirements: 2.1, 2.2, 2.4, 2.5, 2.6_
  - [ ]* 3.2 Model-based Hypothesis test in `test_connection_replay.py` (fake websockets whose `send_text` yields with `await asyncio.sleep(0)`, a `TelemetryHistory` fed by a tiny stand-in for the Broadcaster send step)
    - **Property 4: No gaps or duplicates at the handoff**
    - **Property 5: Previews never queue for a replaying client, and the queue is bounded**
    - **Validates: Requirements 2.1, 2.4, 2.5, 2.6, 2.7**

- [ ] 4. Feed the history from the pipeline
  - [ ] 4.1 `backend/src/pipeline/broadcast.py`: optional `history` argument; in `_worker`, append `telemetry` payloads immediately before `await self._broadcast(message)` (comment the no-`await` invariant)
    - Tests: `backend/tests/test_pipeline_history.py` (new): telemetry appended once per send; frames, status and dropped previews not appended; messages still queued at `stop()` not appended
    - _Requirements: 1.1, 1.8_
  - [ ] 4.2 `backend/src/pipeline/orchestrator.py`: own a `TelemetryHistory`, pass it to `Broadcaster`, expose a read-only `history` property; in `start()` clear it and `await self._broadcast(empty_replay_message(run_id))` after the per-session reset and before `self._frame_extractor.start(config)`; leave `stop()` alone
    - Tests: in `test_pipeline_history.py`, with the frame extractor stubbed as in `tests/test_pipeline_frame_path.py` / `tests/test_pipeline_stage_order.py`: the broadcast callback sees the empty replay before any telemetry of the Run; history survives `stop()`; a second `start()` empties it and changes the run id
    - _Requirements: 1.6, 1.7, 2.9_
  - [ ]* 4.3 Hypothesis tests in `test_pipeline_history.py`
    - **Property 6: A new Run resets every client**
    - **Property 7: Stop keeps the history**
    - **Validates: Requirements 1.6, 1.7, 2.9, 8.4**

- [ ] 5. Wire the endpoint
  - [ ] 5.1 `backend/src/api/websocket.py`: move `manager.connect(...)` inside the `try`, passing `replay=lambda: replay_messages(orchestrator.history.snapshot())`
    - Tests: `backend/tests/test_server.py` `TestWebSocketEndpoint`: the first message after connect is a `replay` chunk 0; with records appended to `orchestrator.history` beforehand they arrive in it, in order; a replay send failure is logged once (extend `test_disconnect_logged_once`); `tests/test_layering.py` still passes
    - _Requirements: 2.1, 2.2, 2.8_

- [ ] 6. Backend checkpoint
  - [ ] 6.1 tester: full `pytest`; perf-benchmarker: measurements 1 and 2 from the design's Performance section against `development` as the baseline (ift-12 test stream, CPU-only box, OCR worker count as in the 2026-10-19 sweep)
  - [ ] 6.2 Docs: `DEVELOPMENT.md` (api section: replay on connect, history per Run, the `replay` message; pipeline section: `history.py`)
    - _Requirements: 1, 2_

### Phase B: Frontend

- [ ] 7. Replay message on the client
  - [ ] 7.1 `frontend/src/core/types.ts`: `ReplayPayload`, `'replay'` in `WebSocketMessage`. `frontend/src/api/websocket.ts`: one shared `MESSAGE_TYPES` check for `validateMessageStructure` and `parseWebSocketMessage`, `replay` payload shape check, `TelemetryWebSocket({ readOnly })` dropping commands with a WARNING
    - Tests: `src/api/websocket.test.ts` (valid and malformed `replay`, read-only `sendCommand` never calls `ws.send`), `src/core/types.test.ts` if it lists message types
    - _Requirements: 3.1, 7.1_

- [ ] 8. Batch ingestion in `StateManager`
  - [ ] 8.1 `frontend/src/state/state-manager.ts`: extract `ingest(record)` from `handleTelemetry`; add `handleReplay` (chunk 0 resets clocks, detector and containers but keeps `pipelineStatus`; one state update and one notify per chunk; no FPS tick)
    - Tests: `src/state/state-manager.test.ts`: chunk 0 clears earlier live data; two chunks append; one notify per chunk; `processingFps` unchanged
    - _Requirements: 3.2, 3.3, 3.5, 3.6_
  - [ ]* 8.2 fast-check tests in `state-manager.test.ts` (arbitrary records with MET strings, separation flips and engine statuses; arbitrary chunk splits)
    - **Property 8: Replay equals live delivery**
    - **Property 9: Chunk 0 resets, so reconnects never duplicate**
    - **Property 10: One notification per replay message, no FPS ticks**
    - **Validates: Requirements 3.2–3.6**
  - [ ] 8.3 ui-checker: dashboard refresh mid-run keeps graphs, engine markers and downloads; starting a new Run clears them (the stated default)
    - _Requirements: 3.4, 8.4_

- [ ] 9. Widget ids and URL snapshot (pure logic)
  - [ ] 9.1 Move `normaliseSerial` and `MAX_SERIAL_LENGTH` to `frontend/src/domain/engines/serials.ts`, re-export them from `src/state/engine-serials.ts`
    - Tests: existing `src/state/engine-serials.test.ts` and `features/engines/*` tests pass unchanged
    - _Requirements: 4.5_
  - [ ] 9.2 Create `frontend/src/domain/overlay/widgets.ts` (`GraphSeriesId`, `OverlayWidgetId`, `OVERLAY_WIDGETS` with labels and suggested sizes, `isOverlayWidgetId`) and `frontend/src/domain/overlay/snapshot.ts` (`OverlaySnapshot`, `encodeSnapshot`, `decodeSnapshot`, `buildOverlayUrl`, `parseOverlayLocation`; UTF-8 base64url without padding; field validation and caps)
    - Tests: `src/domain/overlay/snapshot.test.ts` (new): examples for each widget's snapshot, a non-ASCII mission name, missing `s`, unknown `v`, garbage `s`, unknown widget id returned as-is for the router to reject
    - _Requirements: 4.1, 4.2, 4.3, 4.5_
  - [ ]* 9.3 fast-check tests in `snapshot.test.ts`
    - **Property 11: Snapshot round trip**
    - **Property 12: Decoding never throws**
    - **Property 13: URL length bound**
    - **Validates: Requirements 4.3–4.6**

- [ ] 10. Memory-only settings stores
  - [ ] 10.1 `frontend/src/state/flight-colors.ts` and `frontend/src/state/engine-serials.ts`: `{ persist?: boolean; initial?: Record<string, string> }` option; with `persist: false` no `localStorage` access at all, `initial` validated like loaded data
    - Tests: `src/state/flight-colors.test.ts`, `src/state/engine-serials.test.ts`: spy `localStorage` sees no calls; `set`/`subscribe` still work in memory
    - _Requirements: 7.2, 7.4_

- [ ] 11. OBS popover
  - [ ] 11.1 Create `frontend/src/ui/obs-popover.ts` (`createObsButton`, `ObsChoice`, `toggleObsPopover`; singleton on `bindDismiss` and `anchorPanel`; widget select, read-only URL input, Copy with clipboard fallback, suggested size, re-copy note) and its CSS in `frontend/src/styles/obs-popover.css`, imported from `styles/index.css`
    - Tests: `src/ui/obs-popover.test.ts` (new, private module copy via `vi.resetModules()`): toggle open/close, Escape and outside press dismiss, changing the select calls `urlFor` with the new id, Copy calls `navigator.clipboard.writeText`, rejected clipboard selects the input
    - _Requirements: 5.2, 5.4, 5.5, 5.6_

- [ ] 12. Chrome-free modes for each panel (no OBS buttons yet)
  - [ ] 12.1 `frontend/src/features/met-display.ts` (new): MET text only, subscribing like `TelemetryDisplay`
    - Tests: `src/features/met-display.test.ts` (new): shows `--:--:--`, then the record's MET
    - _Requirements: 6.6_
  - [ ] 12.2 `frontend/src/features/engines/visualizer.ts`: options `vehicles`, `chrome`, `serials`, `mission: CurrentMission | null`; `chrome: false` drops titles, gear and legend; `mission: null` never constructs `CurrentMission`; add `overlaySnapshot(vehicle)` returning `m` and `e`
    - Tests: `src/features/engines/visualizer.test.ts`: one-vehicle render, no buttons with `chrome: false`, serials from injected memory-only store appear on the circles, `overlaySnapshot` matches the stores
    - _Requirements: 6.2, 6.4, 7.3_
  - [ ] 12.3 `frontend/src/features/graphs/graphs.ts`, `toolbar.ts`, `comparisons.ts`, `chart-config.ts`: options `chrome`, `series`, `comparisons`; canvas only, zoom/pan and crosshair off (per docs-researcher answer 4), headless `Comparisons` with a fixed selection and snapshot colours; add `overlaySnapshot()` returning `x`
    - Tests: `src/features/graphs/graphs.test.ts`: no select/buttons/checkboxes with `chrome: false`; compared flights fetched by GET and drawn in the snapshot colours; `overlaySnapshot` equals the loaded comparisons with `flightColor()`
    - _Requirements: 6.2, 6.3_
  - [ ] 12.4 `frontend/src/features/ship-globe/globe.ts`, `overlay.ts`: options `chrome`, `hidden`, `colors`; `chrome: false` omits zoom, centre, history and download controls, keeps the readout, always follows the ship; add `overlaySnapshot()` returning `t` (overrides only) and `h`
    - Tests: `src/features/ship-globe/globe.trace.test.ts`: existing hashes unchanged for the default mode; a new trace for `chrome: false` with one hidden trajectory and one colour override
    - _Requirements: 6.2, 6.5_

- [ ] 13. OBS buttons on the dashboard
  - [ ] 13.1 Add the OBS button to `features/telemetry-display.ts` (widgets `telemetry`, `met`), each vehicle section of `features/engines/visualizer.ts`, the header of `features/graphs/toolbar.ts` (six series, preselect the shown one) and the header in `features/ship-globe/overlay.ts` (`GLOBE_HTML`); each builds `urlFor` from `buildOverlayUrl(location.origin + location.pathname, id, <panel>.overlaySnapshot())`; button CSS in `styles/obs-popover.css`
    - Tests: per-feature tests click the button and assert the popover URL decodes to the panel's current settings; `src/app-smoke.test.ts` snapshot updated, with a diff containing only the OBS buttons
    - _Requirements: 4.2, 5.1, 5.3, 5.4_

- [ ] 14. Overlay route
  - [ ] 14.1 Create `frontend/src/app/overlay-registry.ts` (mount function per `OverlayWidgetId`, graph entries generated from `OVERLAY_WIDGETS`, memory-only stores seeded from the snapshot, `mission: null` for engines) and `frontend/src/app/overlay.ts` (`mountOverlay`: `overlay` class on `<html>`, read-only `TelemetryWebSocket`, `StateManager`, ship tracker and trajectories only for the globe, "Unknown overlay" message without opening a socket); branch on `parseOverlayLocation` in `frontend/src/app/main.ts`; `frontend/src/styles/overlay.css` (transparent `html`/`body`, no scrollbars) imported from `styles/index.css`
    - Tests: `src/app/overlay.test.ts` (new, private module copies via `vi.resetModules()` as in `app-smoke.test.ts`): each id mounts and contains no `button`/`select`/`input`; unknown id message; replay then live telemetry updates the widget; a second socket `open` followed by a replay rebuilds state without duplicates
    - _Requirements: 4.7, 6.1, 6.2, 8.1, 8.2, 8.3_
  - [ ]* 14.2 Property tests in `src/app/overlay.test.ts`
    - **Property 14: Overlays are read-only and isolated** (seeded `localStorage`, spy socket and `fetch`, every widget id)
    - **Property 15: Registry is complete** (against `SERIES_OPTIONS` from `features/graphs/panels.ts`)
    - **Validates: Requirements 6.3, 7.1–7.4**

- [ ] 15. Frontend checkpoint
  - [ ] 15.1 tester: `npm test` (including `src/architecture.test.ts`) and `npm run build`
  - [ ] 15.2 perf-benchmarker: measurement 3 from the design (replay of 4 000 and 20 000 records to a drawn graph, Chromium)
  - [ ] 15.3 ui-checker: every overlay id at its suggested size over a checkerboard (transparency, no chrome, panel look kept), the OBS popover on each panel, then the OBS checklist from the design (scene switch with refresh, backend restart, new Run, late open); confirm or adjust the suggested sizes in `domain/overlay/widgets.ts`
  - [ ] 15.4 Docs: `DEVELOPMENT.md` frontend section (overlay route, `domain/overlay/`, `ui/obs-popover.ts`, `app/overlay*.ts`, memory-only stores, the read-only rule)
  - _Requirements: 5, 6, 7, 8_
