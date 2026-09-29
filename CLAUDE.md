# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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

`results_previous/` is tracked with Git LFS. Without `git lfs install`/`git lfs pull` it contains pointer stubs, not real data.

## Architecture

**Backend pipeline** (`backend/src/`): `frame_extractor` → `engine_analyzer` (Hough circles, colour classification against SVG-derived ROI templates) and `ocr_engine` (EasyOCR on defined regions) → `stage_assignment` (attributes values to Starship or Super Heavy by separation state) → `record_assembler` → `telemetry_record`, then a WebSocket broadcast in `server.py`.

- `pipeline.py` is the orchestrator. It owns `PipelineState`, handles Start/Stop commands, and wires all of the above together.
- Frames are processed concurrently. `concurrency_controller` bounds the in-flight work, and `reorder_buffer` restores frame order before records are assembled. Frame order matters because stage assignment and the telemetry timeline are stateful. `parallel_config` holds the tuning and the FPS meter.
- ROI templates come from `svg_parser` and `template_registry`, using SVGs in `diagrams/`.
- `gpu_detector` decides whether EasyOCR runs on CUDA or CPU, and the result is reported in the pipeline status.
- `models` (ROI dataclasses from SVG parsing) and `enums` (`PipelineStatus`, `SeparationState`, `OCRFieldStatus`) are the shared types.
- `logging_config` and `logging_context` provide human-readable, uvicorn-style logging, with the frame sequence number carried in a contextvar.
- `server.py` (`create_app()`) exposes `/ws/telemetry` (WebSocket), `GET /api/status`, `POST /api/validate-url`, and `GET /api/previous-flights[/{name}]`, which reads `results_previous/`. Clients are tracked by UUID4 in `ConnectionManager`, and each session carries a correlation ID (a client-supplied UUID4 is honoured) for command tracing.

**Frontend** (`frontend/src/`): `main.ts` wires the modules together. `websocket.ts` (reconnecting-websocket) feeds `StateManager` in `state.ts`, which is the single source of truth. The view modules (`telemetry-display`, `engine-visualizer`, `time-series-graphs`, `pipeline-controls`, `frame-preview`, `fps-meter`, `previous-flights`) render from that state. `logger.ts` (leveled logging) and `correlation.ts` (correlation IDs for commands) are shared utilities. `types.ts` mirrors the backend message shapes, so change both sides together. `time-series-graphs/` is a folder: `index.ts` registers the Chart.js zoom and crosshair plugins and re-exports `TimeSeriesGraphs` from `graphs.ts`, with `types`, `panels` (series options and colours), `series-math` (MET parsing, dedup/gap insertion, acceleration in g derived from speed; options with `derive` set are computed rather than read from the store) and `crosshair-plugin` as leaf modules.

**Tests**: backend uses pytest with Hypothesis property tests. Frontend uses Vitest (jsdom) with fast-check, and specs sit next to the source as `*.test.ts`.

## Specs

`.kiro/specs/<feature>/{requirements,design,tasks}.md` hold the design docs for each major feature (`starship-telemetry-web-view`, `pipeline-parallelization`, `logging-improvements`). Check the relevant `design.md` before changing the pipeline, concurrency or logging.

On `main`, `backend/src/pipeline.py` is a single file. The `refactor-modules` branch splits it into a `backend/src/pipeline/` package (spec: `module-refactoring-split` on that branch); its frontend half targets the multi-panel graphs code that `main` later reverted. When a package and a same-named `.py` or `.ts` file both exist, check which one is authoritative before editing. The `RapidOCR-version` branch swaps EasyOCR for RapidOCR, so `ocr_engine`, `gpu_detector` and the dependencies differ there.
