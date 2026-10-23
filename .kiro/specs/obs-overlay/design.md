# Design Document: OBS Overlay

## Overview

The feature has two halves that can ship separately, backend first:

1. **Backend replay.** `src/pipeline/history.py` keeps the current Run's telemetry records as compact JSON strings. The Broadcaster appends each record when it sends it. `ConnectionManager.connect()` snapshots that history, registers the client, sends the snapshot in `replay` chunks and only then lets live messages through, flushing whatever arrived meanwhile in order. A new Run clears the history and broadcasts an empty `replay` so every client resets.
2. **Frontend overlays.** `app/main.ts` routes `?overlay=<id>` to `app/overlay.ts`, which mounts one widget from a registry with chrome turned off, a read-only WebSocket and memory-only settings stores seeded from the URL snapshot. Each dashboard panel gets an OBS button that opens `ui/obs-popover.ts` with the URL.

The replay also fixes a dashboard refresh mid-flight: the dashboard is just another client.

Decisions taken here (defaults, not re-opened design questions) are marked **Default:**.

## Architecture

### Where this sits in the pipeline

```
capture → FrameProcessor (vision / ocr, ~98% of compute) → ReorderCoordinator.drain()
   (stage assignment in video order, sequence_number assigned) → Broadcaster.enqueue()
   → Broadcaster._worker:  history.append(record)  ┐ one synchronous step
                           manager.broadcast_json() ┘ (routing decided before first await)
   → ConnectionManager: live clients get it now; replaying clients queue it
```

Nothing before the Broadcaster changes. Frame order, the reorder buffer, stage assignment and the concurrency limits stay as they are; the history is fed from the already ordered output, so it inherits video order and the gap-free broadcast `sequence_number` from `ReorderCoordinator.drain()` (`backend/src/pipeline/ordering.py`). The OCR hot path gains nothing; the per-record cost is one extra `json.dumps` in the Broadcaster task (see Performance).

### Layering

Backend (`tests/test_layering.py`): `history.py` is in `pipeline` and imports only stdlib. `api/replay.py` builds the chunk messages and imports `src.pipeline.history` (api is above pipeline). `ConnectionManager` gets the replay as a callback, so `connections.py` itself still imports nothing from the pipeline.

Frontend (`src/architecture.test.ts`, `core → domain → api → state → ui → features → app`):

| Module | Layer | Imports |
| --- | --- | --- |
| `core/types.ts` (`ReplayPayload`) | core | none |
| `domain/overlay/widgets.ts`, `domain/overlay/snapshot.ts` | domain | core |
| `api/websocket.ts` (`readOnly` option, `replay` validation) | api | core |
| `state/state-manager.ts` (`handleReplay`), `state/flight-colors.ts`, `state/engine-serials.ts` (memory-only mode) | state | core, domain, api |
| `ui/obs-popover.ts` | ui | domain, ui/popover |
| `features/*` (`chrome: false` options, OBS buttons, `features/met-display.ts`) | features | everything below |
| `app/overlay.ts`, `app/overlay-registry.ts`, `app/main.ts` | app | everything |

The widget ids live in `domain/overlay/widgets.ts` so features can name them without importing the app. The graph ids repeat the `SeriesKey`s from `features/graphs/types.ts`; a registry test in `app/` fails if the two lists drift.

### Handoff without gaps or duplicates

asyncio runs one task at a time, so code with no `await` in it cannot be interleaved. Two steps rely on that:

- **Send side** (`Broadcaster._worker`): `history.append(payload)` and the call into `manager.broadcast_json(message)` run back to back. `broadcast_json` serialises and calls `broadcast`, which copies the connection list and sorts clients into live and replaying before its first `await` (today it already copies the list before `asyncio.gather`). So for every record there is one moment at which it both enters the history and is routed.
- **Connect side** (`ConnectionManager.connect`): after `await websocket.accept()`, `snapshot = replay()` and registering the client as *replaying* run back to back.

A record appended before the client registered is in its snapshot and was not routed to it; a record appended after is routed to it (queued while replaying) and is not in the snapshot. So each record reaches each client exactly once, in order. The replaying client's queue is flushed in a loop until it is empty, and the client is switched to *live* in the same synchronous step as the emptiness check, so nothing can slip in between.

The only messages that reach clients outside the Broadcaster are `status`/`error` broadcasts from `api/websocket.py` and `PipelineOrchestrator._broadcast_status`; they pass through the same `broadcast` and are queued the same way, which keeps their order relative to telemetry. `validation_result` is sent to one client directly with `websocket.send_json` and is unaffected (a client cannot send a command before `connect()` has returned, because the receive loop starts after it).

### New Run

`PipelineOrchestrator.start()` already resets per-session state before `self._frame_extractor.start(config)`. It will also call `self._history.clear()` (new `run_id`) and `await self._broadcast(empty replay)` there, before the extractor and the Broadcaster start, so the empty replay precedes every telemetry message of the Run on every connection. `stop()` does not clear the history.

**Default:** the dashboard now clears its live data and downloadable records when a new Run starts. Today `StateManager.reset()` is never called outside tests, so a second Run appends to the first; with the history per Run, keeping old data client-side would make a refreshed dashboard and an unrefreshed one disagree.

### Memory bound

A record dump (`TelemetryRecord.model_dump()`, 39 engine entries, four field values) is roughly 1.2–1.6 KB as compact JSON (task 1.1 measures it). At the default `skip_frames=30` on a 30 fps stream the pipeline emits about 1 record/s, so a 65-minute flight is about 4 000 records, about 6 MB. **Default:** `MAX_HISTORY_RECORDS = 20 000` (about 30 MB worst case, 5.5 h at 1 record/s, or 11 min at `skip_frames=1`). When it is full the oldest record is dropped (the current state, which overlays show, stays right) and the snapshot reports `truncated: true`. Records are stored as `str`, not dicts: about 3× smaller and joined straight into the replay text.

### Frontend replay ingestion

`StateManager.handleTelemetry()` already appends in place (`appendToTimeSeriesInPlace`, `telemetryRecords.push`, `EngineEventDetector.push`) and notifies once per record. `handleReplay()` reuses that per-record body (extracted to a private `ingest(record)`), skips the FPS tick, and notifies once per chunk. Subscribers that do real work already coalesce: `TimeSeriesGraphs.scheduleUpdate()` draws at most once per animation frame, and `EngineVisualizer` and `TelemetryDisplay` only look at `latestTelemetry`. **Default:** chunks of 500 records, so a large replay is spread over several event-loop turns (each chunk is parsed and ingested in one go) instead of one multi-megabyte `JSON.parse` blocking the page.

`MetClock.estimate()` depends only on the records' MET and `timestamp`, in order, and `EngineEventDetector` only on the record order, so a replay gives the same `metSeconds` and engine events as live delivery (Property 9).

### Overlay routing

```
app/main.ts
  ├─ parseOverlayLocation(location.search) === null → dashboard (unchanged)
  └─ otherwise → mountOverlay(app, { widget, snapshot })   (app/overlay.ts)
        document.documentElement.classList.add('overlay')
        StateManager + TelemetryWebSocket({ readOnly: true })
        OVERLAY_REGISTRY[widget].mount(container, { stateManager, snapshot })
```

The dashboard branch keeps its current code path, so `app-smoke.test.ts` changes only by the new OBS buttons.

## Components and Interfaces

### Backend

#### 1. `backend/src/pipeline/history.py` (new)

```python
MAX_HISTORY_RECORDS = 20_000

@dataclass(frozen=True)
class HistorySnapshot:
    run_id: str
    records: tuple[str, ...]   # compact JSON of each TelemetryRecord dump, oldest first
    truncated: bool

class TelemetryHistory:
    def __init__(self, max_records: int = MAX_HISTORY_RECORDS) -> None: ...
    @property
    def run_id(self) -> str: ...
    def clear(self) -> str: ...                       # empties, new uuid4 run_id, returns it
    def append(self, payload: dict[str, Any]) -> None # json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    def snapshot(self) -> HistorySnapshot: ...        # O(n) tuple copy, no await
    def __len__(self) -> int: ...
```

Backed by `collections.deque(maxlen=max_records)`. The run id starts as a fresh uuid4 at construction, so a client connecting before the first Run gets an empty replay with a valid id.

#### 2. `backend/src/pipeline/broadcast.py`

`Broadcaster.__init__(broadcast, history: TelemetryHistory | None = None)`. In `_worker`, immediately before `await self._broadcast(message)`: `if self._history is not None and message.get("type") == "telemetry": self._history.append(message["payload"])`. Nothing may be awaited between the two. A message dropped from the queue (previews) or still queued when `stop()` cancels the worker is never appended, which is right: no client got it.

#### 3. `backend/src/pipeline/orchestrator.py`

- `self._history = TelemetryHistory()` in `__init__`, passed to `Broadcaster(broadcast, self._history)`.
- `history` property (read-only) for the API.
- In `start()`, after the per-session reset and before `self._frame_extractor.start(config)`: `run_id = self._history.clear()` then `await self._broadcast(empty_replay_message(run_id))`. The message shape is built by `src.pipeline.history.empty_replay_message()` so the pipeline does not import the api layer; `api/replay.py` reuses it for chunk 0 of an empty snapshot.
- `stop()` is unchanged (history kept).

#### 4. `backend/src/api/replay.py` (new)

```python
REPLAY_CHUNK_SIZE = 500

def replay_messages(snapshot: HistorySnapshot, chunk_size: int = REPLAY_CHUNK_SIZE) -> Iterator[str]:
    """Yield the replay as pre-serialised JSON texts, one per chunk, lazily."""
```

Each text is assembled from the stored record strings without re-parsing them:
`'{"type":"replay","payload":{"run_id":' + json.dumps(run_id) + ',"chunk":' + str(i) + ',"final":' + ('true' if last else 'false') + ',"truncated":' + ... + ',"records":[' + ",".join(chunk) + ']}}'`. Lazy, so building a big replay never holds the event loop for more than one chunk.

#### 5. `backend/src/api/connections.py`

```python
MAX_PENDING_MESSAGES = 2_000

ReplaySource = Callable[[], Iterable[str]]   # called synchronously; must snapshot before returning

class ConnectionManager:
    async def connect(self, websocket: WebSocket, replay: ReplaySource | None = None) -> None
    async def broadcast(self, message: str, droppable: bool = False) -> None
    async def broadcast_json(self, data: dict[str, Any]) -> None   # droppable = data.get("type") == "frame"
```

- `connect`: `await websocket.accept()`; then, with no `await`: `messages = replay()` (the callback takes the history snapshot and returns the lazy chunk iterator), append the websocket to `_active_connections` and set `self._pending[websocket] = []`. Then `for text in messages: await websocket.send_text(text)`; then flush: `while queue := self._pending[websocket]: await websocket.send_text(queue.pop(0))` (a `deque` in the code), and once the queue is seen empty, `del self._pending[websocket]` in the same step. Without `replay`, behaviour is today's (immediately live).
- `broadcast`: for each connection in the copied list, if it is in `_pending`, then skip if `droppable`, else append; if the queue is then longer than `MAX_PENDING_MESSAGES`, schedule a close (`websocket.close(code=1013)`) and disconnect it. Otherwise send as today with `asyncio.gather`.
- `disconnect` also drops `_pending[websocket]`.

#### 6. `backend/src/api/websocket.py`

Move `await manager.connect(websocket, replay=_replay_source)` inside the existing `try`, so a failure while sending the replay goes through the same `finally` (one "session disconnected" log, `manager.disconnect`). `_replay_source = lambda: replay_messages(orchestrator.history.snapshot())`; the snapshot is taken when the lambda is called, synchronously inside `connect`.

### Frontend

#### 7. `frontend/src/core/types.ts`

```ts
export interface ReplayPayload {
  run_id: string;
  chunk: number;
  final: boolean;
  truncated: boolean;
  records: TelemetryRecord[];
}
// WebSocketMessage.type gains 'replay'; payload union gains ReplayPayload
```

#### 8. `frontend/src/api/websocket.ts`

- `validateMessageStructure` and `parseWebSocketMessage` repeat the type check; both go through one `MESSAGE_TYPES` set that includes `'replay'`. A `replay` payload must have a `records` array and an integer `chunk` or the message is rejected with the existing warning.
- `new TelemetryWebSocket({ readOnly?: boolean })`: when `readOnly`, `sendCommand` drops the command and logs a WARNING. Overlays construct it read-only.

#### 9. `frontend/src/state/state-manager.ts`

`handleMessage` gains `case 'replay': this.handleReplay(payload)`. `handleReplay(p)`: if `p.chunk === 0`, reset the clocks/detector and replace `timeSeries`, `telemetryRecords`, `engineEvents` with fresh containers (new identities so subscribers that compare identity redraw) while keeping `pipelineStatus`; then `ingest()` each record; then one state object update (`latestTelemetry` = last record or unchanged, versions bumped when anything was appended) and one `notifySubscribers()`. `handleTelemetry` becomes `fpsMeter.tick(); ingest(record); update; notify`.

#### 10. `frontend/src/domain/overlay/widgets.ts` (new)

```ts
export type GraphSeriesId = 'speedSuperHeavy' | 'speedStarship' | 'altitudeSuperHeavy'
  | 'altitudeStarship' | 'accelerationSuperHeavy' | 'accelerationStarship';
export type OverlayWidgetId = 'telemetry' | 'met' | 'engines-superheavy' | 'engines-starship'
  | 'globe' | `graph-${GraphSeriesId}`;
export interface OverlayWidgetInfo { id: OverlayWidgetId; label: string; size: { width: number; height: number } }
export const OVERLAY_WIDGETS: readonly OverlayWidgetInfo[];
export function isOverlayWidgetId(value: string): value is OverlayWidgetId;
```

**Default** suggested OBS sizes (ui-checker confirms): telemetry 420×260, met 320×90, engines-superheavy 380×400, engines-starship 260×260, globe 640×400, each graph 800×400.

#### 11. `frontend/src/domain/overlay/snapshot.ts` (new)

```ts
export const SNAPSHOT_VERSION = 1;
export interface OverlaySnapshot {
  v: 1;
  /** graphs: compared flights in load order, colours resolved: [filename, '#rrggbb'] */
  x?: [string, string][];
  /** globe: trajectory colour overrides by name, and hidden trajectory names */
  t?: Record<string, string>;
  h?: string[];
  /** engines: mission, ship, booster names */
  m?: { n: string; ss: string; sh: string };
  /** engines: this vehicle's serials, keyed by engine number ("6" for ss_e6 / sh_e6) */
  e?: Record<string, string>;
}
export function encodeSnapshot(s: OverlaySnapshot): string;            // UTF-8 → base64url, no padding
export function decodeSnapshot(text: string | null): OverlaySnapshot;  // never throws; invalid → { v: 1 } + WARNING
export function buildOverlayUrl(base: string, widget: OverlayWidgetId, s: OverlaySnapshot): string;
export function parseOverlayLocation(search: string): { widget: string; snapshot: OverlaySnapshot } | null;
```

Short keys and engine numbers instead of `sh_e12` keep the worst case (33 serials of 8 characters plus 80/40/40-character names, all multi-byte) near 1 300 characters. **Default:** no compression (`CompressionStream` is async and adds little at this size). Decoding validates every field (hex colours against `/^#[0-9a-f]{6}$/i`, serials through `normaliseSerial` (moved with `MAX_SERIAL_LENGTH` from `state/engine-serials.ts` to `domain/engines/serials.ts`, re-exported from state so existing imports keep working), strings length-capped as in `mission-fields.ts`: 80 for the mission, 40 for ship and booster) and drops what does not validate.

#### 12. Memory-only settings stores (`frontend/src/state/`)

- `FlightColors(namespace, options?: { persist?: boolean; initial?: Record<string, string> })`: with `persist: false` it neither reads nor writes localStorage and starts from `initial`.
- `EngineSerials(vehicle, options?: { persist?: boolean; initial?: Record<string, string> })`: same.
- `CurrentMission` is not constructed in overlays at all (it writes the mission file through `api/missions.ts`). The engine overlay gets the names from the snapshot as plain strings.

#### 13. `frontend/src/ui/obs-popover.ts` (new)

```ts
export function createObsButton(panelName: string): HTMLButtonElement;   // class 'obs-btn', text 'OBS', aria-haspopup
export interface ObsChoice {
  title: string;
  widgets: readonly OverlayWidgetInfo[];
  selected: OverlayWidgetId;
  urlFor(widget: OverlayWidgetId): string;   // reads the panel's settings at call time
}
export function toggleObsPopover(anchor: HTMLElement, choice: ObsChoice): void;
```

A module-level singleton like `ColorPopover` in `ui/color-picker.ts`: `bindDismiss` + `anchorPanel(align: 'right')`. Content: title, a `<select>` when there is more than one widget, a read-only `<input>` with the URL, Copy (clipboard API, fallback `input.select()`), the suggested size and a note "Copy again after changing settings".

#### 14. Feature changes (`frontend/src/features/`)

Each panel gains an options bag whose default is today's behaviour, plus a method returning its part of the snapshot:

| Feature | Overlay option | Dashboard OBS button | Snapshot part |
| --- | --- | --- | --- |
| `telemetry-display.ts` | none needed (no chrome) | next to the MET, widgets `telemetry`, `met` | none |
| `met-display.ts` (new) | n/a, MET text only, reuses the display's formatting | n/a | none |
| `engines/visualizer.ts` | `{ vehicles?: Vehicle[]; chrome?: boolean; serials?; mission?: CurrentMission \| null }`; `chrome: false` drops titles, gear and legend, `mission: null` skips `CurrentMission` | one per vehicle section, beside the gear | `m`, `e` for that vehicle |
| `graphs/graphs.ts`, `graphs/toolbar.ts`, `graphs/comparisons.ts` | `{ chrome?: boolean; series?: SeriesKey; comparisons?: [filename, color][] }`; `chrome: false` skips `renderGraphsDom`'s header/toolbar (canvas only), turns the zoom plugin and crosshair off, and runs `Comparisons` headless (no checkbox list, fixed selection, colours from the snapshot) | in the graphs header, widgets = the six series, preselect `selectedKey` | `x` from `Comparisons.loaded` + `flightColor()` |
| `ship-globe/globe.ts`, `ship-globe/overlay.ts` | `{ chrome?: boolean; hidden?: string[]; colors?: FlightColors }`; `chrome: false` leaves out zoom, centre, history and download controls, keeps the readout | in the globe header | `t` (overrides only), `h` |

**Default:** engine overlays leave out the legend and the vehicle titles (both read as chrome on a stream); the telemetry overlay keeps the T-0 and STAGE SEP badges (they are data).

#### 15. `frontend/src/app/overlay-registry.ts`, `app/overlay.ts`, `app/main.ts`, `styles/overlay.css`

- Registry: `Record<OverlayWidgetId, (container, deps: { stateManager; snapshot }) => void>`. Graph entries are generated from `OVERLAY_WIDGETS`.
- `mountOverlay`: adds `overlay` class to `<html>`, renders `<main class="overlay-root"><div id="overlay-widget"></div></main>`, wires a read-only socket to a `StateManager`, mounts the widget. The globe overlay also starts a `ShipTracker` and fetches `/api/trajectories`, as `main.ts` does; graph overlays fetch the compared flights (GET only).
- `main.ts`: `const route = parseOverlayLocation(location.search); if (route) mountOverlay(app, route); else { …today's code… }`.
- `styles/overlay.css` (imported from `styles/index.css`): `html.overlay, html.overlay body { background: transparent; }`, panel padding/borders kept, `overflow: hidden`.

## Data Models

### WebSocket message (new, server → client)

```json
{"type":"replay","payload":{"run_id":"5f0c…","chunk":0,"final":false,"truncated":false,"records":[{…TelemetryRecord…}, …]}}
```

- Sent per client after connect (1..n chunks), and broadcast once as an empty chunk 0 when a Run starts.
- `records` items are exactly the `telemetry` payloads (`TelemetryRecord.model_dump()`), unchanged. No change to `TelemetryRecord` itself.
- `frontend/src/core/types.ts` ↔ backend: `ReplayPayload` mirrors `src/api/replay.py` / `src/pipeline/history.empty_replay_message`. Update both together, and DEVELOPMENT.md's API list.

### Overlay URL

`http://localhost:5173/?overlay=graph-speedStarship&s=eyJ2IjoxLCJ4IjpbWyJpZnQtMTIuanNvbiIsIiNlOTFlNjMiXV19` (`s` decodes to `{"v":1,"x":[["ift-12.json","#e91e63"]]}`). The base is the dashboard's own `location.origin + location.pathname`, so it works on the Vite dev server (which proxies `/ws` and `/api`) and wherever the built frontend is served.

## Error Handling

| Situation | Behaviour |
| --- | --- |
| Replay send fails (client gone) | Exception leaves `connect`, endpoint's `finally` logs once and calls `manager.disconnect` (which also drops the pending queue). |
| Replaying client too slow (> `MAX_PENDING_MESSAGES`) | Close with 1013, WARNING; reconnect gets a fresh replay. |
| History full | Drop oldest, `truncated: true`, one WARNING per Run. |
| `broadcast` to a live client fails | Unchanged: client removed. |
| Malformed `replay` on the client | Rejected by validation, WARNING, state untouched. |
| Replay chunk > 0 arrives with a `run_id` different from chunk 0's | Cannot happen on one socket (chunks are sent back to back before any live message); the client still trusts chunk 0 as the reset point and logs a WARNING on mismatch. |
| Bad or missing `s` | Defaults, WARNING, widget still renders. |
| Unknown `overlay` id | "Unknown overlay" text with the valid ids; no socket opened. |
| Clipboard unavailable or denied | Select the URL text, show "Press Ctrl+C". |
| Compared flight fetch fails in an overlay | Same as dashboard: logged, flight left out. |

## Correctness Properties

### Property 1: History keeps the newest records in order
For any sequence of appended payloads and any `max_records ≥ 1`, `snapshot().records` equals the last `min(n, max_records)` serialised payloads in append order, and `truncated` is true iff `n > max_records`.
**Validates: Requirements 1.2, 1.4, 1.5**

### Property 2: Replayed record text equals the live text
For any `TelemetryRecord` built by Hypothesis, the record's text inside the replay equals the `payload` part of `json.dumps(message, separators=(",", ":"), ensure_ascii=False)` as `broadcast_json` sends it, and parses back with `TelemetryRecord.model_validate`.
**Validates: Requirement 1.3**

### Property 3: Chunking is lossless
For any snapshot and `chunk_size ≥ 1`, `replay_messages` yields `max(1, ceil(n / chunk_size))` valid JSON texts, chunk numbers `0..k-1`, `final` only on the last, every chunk at most `chunk_size` records, and the records concatenated equal the snapshot.
**Validates: Requirements 2.2, 2.3**

### Property 4: No gaps or duplicates at the handoff
Model-based (Hypothesis `RuleBasedStateMachine` or an interleaving of steps): rules are "broadcast next record", "broadcast status", "connect a client" and "let the event loop run k steps", with fake websockets whose `send_text` yields to the loop. For every client, the `sequence_number`s it receives (replay records then live records) are strictly increasing and gap-free from its first one to the last broadcast, and its first one is the oldest record in the history at connect time (or the first record broadcast after it, if the history was empty). Status messages keep their position relative to telemetry.
**Validates: Requirements 2.1, 2.4, 2.7**

### Property 5: Previews never queue for a replaying client, and the queue is bounded
For any interleaving as above, a replaying client's queue never holds a `frame` message and never exceeds `MAX_PENDING_MESSAGES`; a client that would exceed it is closed.
**Validates: Requirements 2.5, 2.6**

### Property 6: A new Run resets every client
After `start()`, every connected client has received an empty `replay` chunk 0 with the new run id before any telemetry of the Run, and a client connecting later gets only the new Run's records.
**Validates: Requirements 1.6, 2.9, 8.4**

### Property 7: Stop keeps the history
For any records broadcast and then `stop()`, a client connecting afterwards receives all of them.
**Validates: Requirement 1.7**

### Property 8: Replay equals live delivery
For any array of generated `TelemetryRecord`s (MET strings, separation flips, engine statuses) and any split into chunks, a fresh `StateManager` fed the replay chunks ends with the same `timeSeries` (deep-equal, including `metSeconds`), `telemetryRecords`, `engineEvents`, `separationMetSeconds` and `latestTelemetry` as one fed `reset()` and then each record as a `telemetry` message.
**Validates: Requirements 3.3, 3.4**

### Property 9: Chunk 0 resets, so reconnects never duplicate
For any records A received live and then a replay of records B, the state equals that of a replay of B alone.
**Validates: Requirements 3.2, 3.6**

### Property 10: One notification per replay message, no FPS ticks
For any replay chunk, subscribers are called exactly once and `processingFps` is unchanged by it.
**Validates: Requirements 3.3, 3.5**

### Property 11: Snapshot round trip
For any valid `OverlaySnapshot` (fast-check arbitrary: unicode names within the length caps, hex colours, serials matching the serial rules, up to 6 compared flights), `decodeSnapshot(encodeSnapshot(s))` deep-equals `s`, and the encoded text contains only `[A-Za-z0-9_-]`.
**Validates: Requirement 4.4**

### Property 12: Decoding never throws
For any string (including non-base64url, invalid UTF-8, JSON of the wrong shape, oversized values), `decodeSnapshot` returns a valid snapshot whose fields all pass validation.
**Validates: Requirements 4.3, 4.5**

### Property 13: URL length bound
For any snapshot within the caps, `buildOverlayUrl('http://localhost:5173/', widget, s)` is at most 2 000 characters.
**Validates: Requirement 4.6**

### Property 14: Overlays are read-only and isolated
For every `OverlayWidgetId`, mounting the overlay with a seeded localStorage, a spy socket and a spy `fetch`, then delivering replay and live messages: no `send` on the socket, no localStorage `getItem` of the settings keys and no `setItem` at all, no non-GET request, and the rendered settings (colours, serials, hidden trajectories) are the snapshot's, not the seeded ones.
**Validates: Requirements 7.1–7.4**

### Property 15: Registry is complete
Every `SERIES_OPTIONS` key has a `graph-<key>` entry in `OVERLAY_WIDGETS` and in the registry, and every `OVERLAY_WIDGETS` id has a registry entry.
**Validates: Requirement 4 glossary, 6.3**

## Testing Strategy

### Backend (pytest, Hypothesis, `asyncio_mode=auto`)

- `tests/test_telemetry_history.py` (new): Properties 1, 2; `clear()` changes the run id; record size measurement printed for task 1.1.
- `tests/test_replay.py` (new): Property 3, empty snapshot, `truncated` passthrough.
- `tests/test_connection_replay.py` (new): Properties 4, 5 with fake websockets (`send_text` = `await asyncio.sleep(0)` then record), the flush loop, disconnect during replay.
- `tests/test_pipeline_history.py` (new) (orchestrator set up as in `tests/test_pipeline_frame_path.py`): Broadcaster appends at send time only (dropped previews and cancelled queue entries are not appended), Properties 6, 7 with a stubbed frame extractor as existing orchestrator tests do.
- `tests/test_server.py`: `TestClient` WebSocket gets a `replay` first; with records pre-seeded in `orchestrator.history`, they arrive in the replay; a failing replay send is logged once (extend `test_disconnect_logged_once`).
- `tests/test_layering.py` passes unchanged (new modules sit in existing packages).

### Frontend (Vitest jsdom, fast-check)

- `src/api/websocket.test.ts`: `replay` accepted, malformed `replay` rejected, `readOnly` drops commands.
- `src/state/state-manager.test.ts`: Properties 8, 9, 10.
- `src/domain/overlay/snapshot.test.ts` (new): Properties 11, 12, 13; `parseOverlayLocation` cases.
- `src/state/flight-colors.test.ts`, `src/state/engine-serials.test.ts`: `persist: false` never touches localStorage.
- `src/ui/obs-popover.test.ts` (new, `vi.resetModules()` for the singleton): open/close/dismiss, select changes URL, Copy with and without clipboard.
- Feature tests (`telemetry-display.test.ts`, `features/met-display.test.ts`, `engines/visualizer.test.ts`, `graphs/graphs.test.ts`, `ship-globe/globe.trace.test.ts`): `chrome: false` renders no buttons/selects/inputs; snapshot parts read back the panel's settings.
- `src/app/overlay.test.ts` (new): Properties 14, 15; unknown id message; reconnect (second fake socket open + replay) rebuilds state.
- `src/app-smoke.test.ts`: snapshot updated once for the OBS buttons; the diff must contain only them.
- `src/architecture.test.ts` passes unchanged.

### Performance (perf-benchmarker)

OCR is about 98% of compute and runs in thread pools; the new work is in the event loop. Measure before and after on the CPU-only dev box with `test_videos/ift-12` (`scripts/serve_test_videos.py`):

1. Processing FPS and frames discarded over a full run, with no extra clients (cost of `history.append`).
2. The same, with a client connecting every 10 s once 4 000 and 20 000 records are in history (cost of building and sending a replay during a run). Budget: FPS within noise of the baseline, no stale-gap warnings caused by the replay.
3. Frontend: time from socket open to the last chunk ingested and the graph drawn, for 4 000 and 20 000 records, in Chromium (OBS uses CEF/Chromium). Budget: under 1 s for 4 000.

If (2) shows a stall, the follow-up is to serialise each record once (history string reused for the live message) rather than to shrink chunks.

### Manual / ui-checker

Dashboard: each OBS button, popover placement, Copy. Overlay pages in a browser at the suggested sizes on a checkerboard background (transparency), no chrome. In OBS: add each URL as a Browser Source, switch scenes with "Refresh browser when scene becomes active" on, restart the backend, start a new Run.

## Questions for the docs-researcher

1. Uvicorn's WebSocket implementations: is `permessage-deflate` negotiated by default (`--ws-per-message-deflate`), and does OBS's CEF accept it? This decides whether a multi-megabyte replay matters on localhost at all.
2. Starlette `WebSocket.send_text` from two tasks on the same socket: safe or interleaving? The design avoids it (a replaying client is only written to by `connect`), but the reviewer should know whether it is a hard rule.
3. OBS Browser Source: is the page background transparent by default, and what exactly do "Shutdown source when not visible" and "Refresh browser when scene becomes active" do to the page (reload vs. hide)? Does the default CEF build allow `ws://localhost` from an `http://localhost` page without extra flags?
4. Chart.js 4 with `chartjs-plugin-zoom`: cleanest way to disable zoom and pan per chart (plugin options `enabled: false` vs. not registering), given the plugin is registered globally in `features/graphs/index.ts`.
