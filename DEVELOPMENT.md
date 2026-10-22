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
npx vitest run src/core/met.test.ts       # one file
```

No linter is configured for either side. Backend imports use the `src.` package prefix (e.g. `from src.models import ...`), so run everything from `backend/`.

**Versioning**: `VERSION` (repo root) holds the SemVer release, `MAJOR.MINOR.PATCH`. The full version adds the commit count (`git rev-list --count HEAD`) as a fourth, build number, e.g. `0.1.0.64`. It is computed by `backend/src/version.py` (FastAPI) and by `frontend/vite.config.ts` at build time (`__APP_VERSION__`, shown in the dashboard header). Bump with `python scripts/bump_version.py patch|minor|major|X.Y.Z`, which also updates the copies in `frontend/package.json` and `backend/pyproject.toml`; `tests/test_version.py` fails if they drift.

`results_previous/` is tracked with Git LFS. Without `git lfs install`/`git lfs pull` it contains pointer stubs, not real data.

**Test videos**: downloaded broadcast streams live in `test_videos/<flight>/` (`output.m3u8` plus `seg_*.ts`), e.g. `ift-12/` and `ift-13/`. The folder is local-only: it is excluded through `.git/info/exclude`, not `.gitignore`, so each checkout has to fetch its own copy. The IFT-12 and IFT-13 streams are trimmed to start about 30 s before T-0. Serve one with `python scripts/serve_test_videos.py ift-12` and point the pipeline at `http://localhost:8001/output.m3u8`; without a flight argument every flight is served at `http://localhost:8001/<flight>/output.m3u8`. `--port`, `--host` and `--dir` override the defaults, and the server sends HLS MIME types and `Access-Control-Allow-Origin: *`.

## Architecture

**Backend pipeline** (`backend/src/`): `frame_extractor` → `engine_analyzer` (Hough circles, colour classification against SVG-derived ROI templates) and `ocr_engine` (EasyOCR on defined regions) → `stage_assignment` (attributes values to Starship or Super Heavy by separation state) → `record_assembler` → `telemetry_record`, then a WebSocket broadcast in `server.py`.

- `pipeline.py` is the orchestrator. It owns `PipelineState`, handles Start/Stop commands, and wires all of the above together.
- Frames are processed concurrently. `concurrency_controller` bounds the in-flight work, and `reorder_buffer` restores frame order before records are assembled. Frame order matters because stage assignment and the telemetry timeline are stateful. `parallel_config` holds the tuning and the FPS meter.
- `frame_extractor` decodes a seekable file with `parallel_decoder` (several decoders over separate stretches, same frames as serial decoding) when the skip interval is long enough to pay for the seeks; live streams are read serially.
- `ocr_engine` reuses static ROIs (units, stage labels) from a cache while their pixels are unchanged, and sends the rest to `ocr_batcher`, which runs one recogniser pass over the ROIs of every frame in flight. On GPU that pass is `ocr_mixed`, a step-for-step copy of EasyOCR's `get_text` with a per-crop allowlist mask, so it depends on EasyOCR internals (pinned `<2.0`).
- ROI templates come from `svg_parser` and `template_registry`, using SVGs in `diagrams/`.
- `gpu_detector` decides whether EasyOCR runs on CUDA or CPU, and the result is reported in the pipeline status.
- `models` (ROI dataclasses from SVG parsing) and `enums` (`PipelineStatus`, `SeparationState`, `OCRFieldStatus`) are the shared types.
- `logging_config` and `logging_context` provide human-readable, uvicorn-style logging, with the frame sequence number carried in a contextvar.
- `server.py` (`create_app()`) exposes `/ws/telemetry` (WebSocket), `GET /api/status`, `POST /api/validate-url`, `GET /api/previous-flights[/{name}]`, which reads `results_previous/`, and `GET /api/trajectories[/{name}]`, which serves the recorded ship paths in `trajectories/positions_ship<N>.csv` via `trajectories.py` (below-ground rows and repeated polls dropped), and `GET /api/missions`, `GET`/`PUT /api/missions/{id}`, which keep one JSON file per mission in `missions/` via `missions.py` (mission, ship and booster names and the Raptor serial in each engine position of both vehicles; files saved before Super Heavy had settings read with an empty booster). The id is the mission name as a file stem, `IFT 14` -> `ift-14`, matching `results_previous/ift-14.json`; the file is `missions/engines_ift-14.json`, so a recorded flight is never written to. Filled-in missions are committed. Clients are tracked by UUID4 in `ConnectionManager`, and each session carries a correlation ID (a client-supplied UUID4 is honoured) for command tracing.

**Frontend** (`frontend/src/`): the code is in seven layers, `core → domain → api → state → ui → features → app`, and a module imports only from its own layer or any layer to its left. `src/architecture.test.ts` enforces this (type-only imports included) and finds import cycles; every module must live in a layer folder. `core/` holds the dependency-free pieces: `types.ts` (mirrors the backend message shapes, so change both sides together), `logger.ts` (leveled logging), `correlation.ts` (correlation IDs for commands), `fps-meter.ts`, the MET helpers (`met.ts`: MET parsing and formatting) and `met-clock.ts`, `app-version.ts` and the time-series and ship-track data types. `domain/` is pure logic: `engines/` (layout, detection, events), `series-math.ts` (dedup/gap insertion, acceleration in g derived from speed; options with `derive` set are computed rather than read from the store), `flights.ts` and `downloads.ts` (the converters for the downloads below). `api/` talks to the outside: `websocket.ts` (reconnecting-websocket), `status.ts`, `previous-flights.ts`, `trajectories.ts` (`/api/trajectories`), `ship-tracker-feed.ts` and `missions.ts` (the mission files). `state/` holds the stores: `StateManager` in `state-manager.ts` is the single source of truth, fed by the websocket, and also keeps every received record for download; `time-series-store.ts`, `ship-tracker.ts`, `engine-serials.ts` (serials in localStorage), `current-mission.ts` (the mission shared by both vehicles' panels with the ship and booster, saving every change to the mission's file through `api/missions.ts`) and `flight-colors.ts`. `ui/` is the shared widgets (`popover.ts`, `color-picker.ts`, `svg.ts`). `features/` has one module or folder per panel, each rendering from that state: `telemetry-display`, `engines/` (`visualizer.ts` and its diagram, legend and tooltip), `graphs/`, `pipeline-controls`, `frame-preview`, `header/` and `ship-globe/`. `features/graphs/` has `index.ts` registering the Chart.js zoom and crosshair plugins and re-exporting `TimeSeriesGraphs` from `graphs.ts`, with `panels` (series options and colours), `chart-config`, `chart-data`, `toolbar`, `export/` and `plugins/` (crosshair and engine-event markers) as leaf modules. `features/ship-globe/` is the exception to rendering from state: it polls the public SpaceX tracker feed (`content.spacex.com/.../starship_tracker_public.json`) directly from the browser, keeps each `ship<N>.current` sample with altitude >= 0 as a track, and draws it on a draggable, zoomable d3-geo orthographic globe (left column; the active ship's data card is the right column) between the engines and the graphs. Recorded paths of previous ships come from `/api/trajectories` (`api/trajectories.ts`) and can be toggled on and off individually. `features/telemetry-downloads.ts` (below the pipeline controls) saves the received records as a `results_previous/`-format flight file (speed and altitude in km/h and km, before separation both vehicles get the stack reading, after it each follows the stage assignment) or as raw records, and the globe's header saves each live track as `positions_ship<N>.csv` in the `trajectories/` format. The gear on each engine diagram opens `features/engines/settings-popover.ts`, where the mission, the ship (Starship) or booster (Super Heavy, a wider panel with its 33 engines by ring) and the engine serials are entered (serials after a fixed `R`); circles keep their E-numbers and show the serial on hover, and `mission-fields.ts` offers saved missions and recorded flights matching the typed name. `app/` is the top: `main.ts` wires the modules together on the HTML shell from `layout.ts`, and `styles/` holds the CSS partials that `styles/index.css` imports in order. `app-smoke.test.ts` snapshots the DOM of the whole dashboard, and `features/ship-globe/globe.trace.test.ts` hashes the globe's canvas calls (`GLOBE_TRACE_DUMP=1` dumps the full traces). Vitest runs with `isolate: false`, so tests restore what they stub, and a test that uses a module-level DOM singleton (the settings panel or colour popover) or mixes mocked and real copies of a module loads a private copy with `vi.resetModules()`.

**Tests**: backend uses pytest with Hypothesis property tests. Frontend uses Vitest (jsdom) with fast-check, and specs sit next to the source as `*.test.ts`.

## Specs

`.kiro/specs/<feature>/{requirements,design,tasks}.md` hold the design docs for each major feature (`starship-telemetry-web-view`, `pipeline-parallelization`, `logging-improvements`). Check the relevant `design.md` before changing the pipeline, concurrency or logging.

On `main`, `backend/src/pipeline.py` is a single file. The `refactor-modules` branch splits it into a `backend/src/pipeline/` package (spec: `module-refactoring-split` on that branch); its frontend half targets the multi-panel graphs code that `main` later reverted. When a package and a same-named `.py` or `.ts` file both exist, check which one is authoritative before editing. The `RapidOCR-version` branch swaps EasyOCR for RapidOCR, so `ocr_engine`, `gpu_detector` and the dependencies differ there.
