# Development Guide

How to build, run and test the project, and how the code is organised.

## Overview

Real-time telemetry extraction from SpaceX Starship livestreams. A Python/FastAPI backend grabs video frames, runs OpenCV engine detection and EasyOCR text extraction, and broadcasts structured records over WebSocket to a TypeScript/Chart.js dashboard (Vite, no framework).

## Commands

Setup and launch scripts live in `scripts/windows/` (`.ps1`/`.bat`) and `scripts/unix/` (`.sh`). `setup` creates the venv and installs dependencies, and it auto-selects a CUDA or CPU torch build (override with `TORCH_CUDA`). `start` runs setup and then launches both services.

```bash
# Backend (from backend/, venv activated). Use ".[dev]" for pytest/hypothesis/httpx.
pip install -e ".[dev]"
uvicorn src.server:app --reload          # http://127.0.0.1:8000
pytest                                    # all tests (asyncio_mode=auto)
pytest tests/test_reorder_buffer.py       # one file
pytest tests/test_server.py -k name       # one test

# Frontend (from frontend/)
npm run dev                               # http://localhost:5173, proxies /ws and /api to :8000
npm run build                             # tsc && vite build (type-check gate)
npm test                                  # vitest run
npx vitest run src/state.test.ts          # one file
```

No linter is configured for either side. Backend imports use the `src.` package prefix (e.g. `from src.models import ...`), so run everything from `backend/`.

**Versioning**: `VERSION` (repo root) holds the SemVer release, `MAJOR.MINOR.PATCH`. The full version adds the commit count (`git rev-list --count HEAD`) as a fourth, build number, e.g. `0.1.0.64`. It is computed by `backend/src/version.py` (FastAPI) and by `frontend/vite.config.ts` at build time (`__APP_VERSION__`, shown in the dashboard header). Bump with `python scripts/bump_version.py patch|minor|major|X.Y.Z`, which also updates the copies in `frontend/package.json` and `backend/pyproject.toml`; `tests/test_version.py` fails if they drift.

`results_previous/` is tracked with Git LFS. Without `git lfs install`/`git lfs pull` it contains pointer stubs, not real data.

**Test videos**: downloaded broadcast streams live in `test_videos/<flight>/` (`output.m3u8` plus `seg_*.ts`), e.g. `ift-12/` and `ift-13/`. The folder is local-only: it is excluded through `.git/info/exclude`, not `.gitignore`, so each checkout has to fetch its own copy. The IFT-12 and IFT-13 streams are trimmed to start about 30 s before T-0. Serve one with `python scripts/serve_test_videos.py ift-12` and point the pipeline at `http://localhost:8001/output.m3u8`; without a flight argument every flight is served at `http://localhost:8001/<flight>/output.m3u8`. `--port`, `--host` and `--dir` override the defaults, and the server sends HLS MIME types and `Access-Control-Allow-Origin: *`.

## Architecture

**Backend pipeline** (`backend/src/`): `capture/` (frame extraction) → `engine_analyzer` (Hough circles, colour classification against SVG-derived ROI templates) and `ocr/` (EasyOCR on defined regions) → `stage_assignment` (attributes values to Starship or Super Heavy by separation state) → `record_assembler` → `telemetry_record`, then a WebSocket broadcast from `api/`.

- `pipeline/` is the orchestration package; `from src.pipeline import PipelineOrchestrator, PipelineState` is its public API. `orchestrator.py` (`PipelineOrchestrator`) handles Start/Stop, dispatches each captured frame (inline before T-0, as a bounded concurrent task after it) and owns and wires the collaborators: `frame_processor.py` (`FrameProcessor`: OCR engine, ROI config, thread pools, T-0 flag, and the per-frame stages and record assembly), `ordering.py` (`ReorderCoordinator`: reorder buffer, stale-gap and overflow handling, stage assignment in video order, broadcast sequence and FPS) and `broadcast.py` (`Broadcaster`: the ordered send queue and its worker task). `stages.py` holds stateless stage helpers (timed executor runs, preview encoding, stage-failure fallbacks), `state.py` holds `PipelineState` and `status_payload.py` builds the status message.
- Frames are processed concurrently. `pipeline/concurrency_controller` bounds the in-flight work, and `pipeline/reorder_buffer` restores frame order before results are broadcast. Frame order matters because stage assignment and the telemetry timeline are stateful. `pipeline/parallel_config` holds the tuning and the FPS meter.
- `capture/` is the frame capture package; `from src.capture import FrameExtractor, FrameExtractorConfig, SourceUnavailable` is its public API. `extractor.py` (`FrameExtractor`) handles start/stop, validation and the capture loop (open the source, read the next kept frame, dispatch it, recover a lost source), with its status and frame/status callbacks in `notifier.py` (`CaptureNotifier`). `config.py` holds `FrameExtractorConfig`, the tuning constants and `SourceUnavailable` (the result of a failed `validate`), `source.py` all OpenCV access (opening, validation, frame rate and resolution, grab/read, the seek check) and `reconnect.py` the retry with exponential backoff. A seekable file is decoded with `parallel_decoder.py` (several decoders over separate stretches, same frames as serial decoding) when `planning.py` finds the skip interval long enough to pay for the seeks; live streams are read serially.
- `ocr/` is the OCR package. `ocr/__init__.py` re-exports only the result types, so importing it never loads torch or EasyOCR; `from src.ocr.results import OCRFieldResult, OCRResult` is what the later stages use, and the engine is imported explicitly from `src.ocr.engine`. `engine.py` (`EasyOCREngine`, `configure_torch_threads`) gates on T-0 and reads each frame's ROIs, using `parsing.py` (field kinds, allowlists, time/number/unit parsers), `regions.py` (ROI clamping and cropping, grayscale, narrowing value boxes to their text) and `cache.py` (`FieldCache`: static ROIs such as units and stage labels are reused while their pixel signature is unchanged, and re-read at least every `CACHE_REFRESH_FRAMES` frames). The rest go to `batcher.py` (`BatchedRecognizer`), which runs one recogniser pass over the ROIs of every frame in flight. On GPU that pass is `mixed.py`, a step-for-step copy of EasyOCR's `get_text` with a per-crop allowlist mask, so it depends on EasyOCR internals (pinned `<2.0`). `results.py` holds `OCRFieldResult` and `OCRResult`.
- ROI templates come from `svg_parser` and `template_registry`, using SVGs in `diagrams/`.
- `gpu_detector` decides whether EasyOCR runs on CUDA or CPU, and the result is reported in the pipeline status.
- `models` (ROI dataclasses from SVG parsing) and `enums` (`PipelineStatus`, `SeparationState`, `OCRFieldStatus`) are the shared types.
- `logging_config` and `logging_context` provide human-readable, uvicorn-style logging, with the frame sequence number carried in a contextvar.
- `api/` is the HTTP/WebSocket package; `src/server.py` is only the uvicorn entry point (`app = create_app()`). `app.py` (`create_app()`) adds GZip, the `/static` mount of `frontend/dist` and the routers. `runtime.py` creates the process singletons on import (`configure_logging()` first, then the `ConnectionManager`, the ROI templates and the `PipelineOrchestrator`), `websocket.py` holds the `/ws/telemetry` endpoint and its control-command handlers, `connections.py` `ConnectionManager` and `schemas.py` the pydantic models. The REST routes are in `routes/` (`flights`, `trajectories`, `missions`, `pipeline`), one `APIRouter` each.
- The API exposes `/ws/telemetry` (WebSocket), `GET /api/status`, `POST /api/validate-url`, `GET /api/previous-flights[/{name}]`, which reads `results_previous/`, and `GET /api/trajectories[/{name}]`, which serves the recorded ship paths in `trajectories/positions_ship<N>.csv` via `trajectories.py` (below-ground rows and repeated polls dropped), and `GET /api/missions`, `GET`/`PUT /api/missions/{id}`, which keep one JSON file per mission in `missions/` via `missions.py` (mission, ship and booster names and the Raptor serial in each engine position of both vehicles; files saved before Super Heavy had settings read with an empty booster). The id is the mission name as a file stem, `IFT 14` -> `ift-14`, matching `results_previous/ift-14.json`; the file is `missions/engines_ift-14.json`, so a recorded flight is never written to. Filled-in missions are committed. Clients are tracked by UUID4 in `ConnectionManager`, and each session carries a correlation ID (a client-supplied UUID4 is honoured) for command tracing.

**Frontend** (`frontend/src/`): `main.ts` wires the modules together. `websocket.ts` (reconnecting-websocket) feeds `StateManager` in `state.ts`, which is the single source of truth. The view modules (`telemetry-display`, `engine-visualizer`, `time-series-graphs`, `pipeline-controls`, `frame-preview`, `fps-meter`, `previous-flights`) render from that state. `ship-globe/` is the exception: it polls the public SpaceX tracker feed (`content.spacex.com/.../starship_tracker_public.json`) directly from the browser, keeps each `ship<N>.current` sample with altitude >= 0 as a track, and draws it on a draggable, zoomable d3-geo orthographic globe (left column; the active ship's data card is the right column) between the engines and the graphs. Recorded paths of previous ships come from `/api/trajectories` (`history.ts`) and can be toggled on and off individually. `StateManager` also keeps every received record for download: `telemetry-downloads.ts` (below the pipeline controls) saves them as a `results_previous/`-format flight file (speed and altitude in km/h and km, before separation both vehicles get the stack reading, after it each follows the stage assignment) or as raw records, and the globe's header saves each live track as `positions_ship<N>.csv` in the `trajectories/` format; the converters are in `downloads.ts`. The gear on each engine diagram opens `engine-settings.ts`, where the mission, the ship (Starship) or booster (Super Heavy, a wider panel with its 33 engines by ring) and the engine serials are entered (serials after a fixed `R`); circles keep their E-numbers and show the serial on hover. `engine-serials.ts` holds the serials in localStorage, `current-mission.ts` the mission shared by both vehicles' panels with the ship and booster, saving every change to the mission's file through `missions-api.ts`, and `mission-fields.ts` offers saved missions and recorded flights matching the typed name. `logger.ts` (leveled logging) and `correlation.ts` (correlation IDs for commands) are shared utilities. `types.ts` mirrors the backend message shapes, so change both sides together. `time-series-graphs/` is a folder: `index.ts` registers the Chart.js zoom and crosshair plugins and re-exports `TimeSeriesGraphs` from `graphs.ts`, with `types`, `panels` (series options and colours), `series-math` (MET parsing, dedup/gap insertion, acceleration in g derived from speed; options with `derive` set are computed rather than read from the store) and `crosshair-plugin` as leaf modules.

**Tests**: backend uses pytest with Hypothesis property tests. Frontend uses Vitest (jsdom) with fast-check, and specs sit next to the source as `*.test.ts`.

## Specs

`.kiro/specs/<feature>/{requirements,design,tasks}.md` hold the design docs for each major feature (`starship-telemetry-web-view`, `pipeline-parallelization`, `logging-improvements`). Check the relevant `design.md` before changing the pipeline, concurrency or logging.

The specs predate the `backend/src/pipeline/` package and still refer to `src/pipeline.py`; that code now lives in the package. The `RapidOCR-version` branch (EasyOCR swapped for RapidOCR) is abandoned.
