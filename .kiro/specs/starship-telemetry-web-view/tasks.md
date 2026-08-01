# Implementation Plan: Starship Telemetry Web View

## Overview

This plan implements a real-time telemetry extraction and display system for SpaceX Starship livestreams. The backend is Python with FastAPI, OpenCV, and EasyOCR. The frontend is TypeScript/JavaScript with WebSocket connectivity, time-series graphs, and SVG engine visualizations. Tasks are ordered to build foundational data models and parsing first, then processing pipeline components, then the web layer, and finally integration wiring.

## Tasks

- [x] 1. Set up project structure, dependencies, and core data models
  - [x] 1.1 Create Python project structure with pyproject.toml and install dependencies
    - Create directory structure: `backend/`, `backend/src/`, `backend/tests/`, `frontend/`
    - Add pyproject.toml with dependencies: fastapi, uvicorn, opencv-python-headless, easyocr, torch, numpy, pydantic, hypothesis, pytest
    - Add frontend package.json with dependencies: typescript, vitest, fast-check, chart.js (or lightweight charting lib), reconnecting-websocket
    - Set up pytest configuration and hypothesis profile
    - _Requirements: 1.1, 3.1, 4.1, 7.1_

  - [x] 1.2 Implement GPU Detector module
    - Create `backend/src/gpu_detector.py` with `AccelerationBackend` enum, `GPUCapabilities` dataclass, and `detect_gpu()` function
    - Implement CUDA detection via `torch.cuda.is_available()` with exception handling for driver mismatches
    - _Requirements: 3.5, 3.6 (GPU Strategy from design)_

  - [x] 1.3 Implement core data model interfaces and enums
    - Create `backend/src/models.py` with `ROIRect`, `ROICircle`, `EngineSubgroup`, `EngineGroup`, `ROIConfiguration`, `ParseError` dataclasses
    - Create `backend/src/enums.py` with `EngineStatus`, `PipelineStatus`, `SeparationState`, `OCRFieldStatus` enums
    - _Requirements: 1.4, 3.9, 6.1_

  - [x] 1.4 Implement TelemetryRecord Pydantic model with serialization/deserialization
    - Create `backend/src/telemetry_record.py` with `TelemetryRecord` Pydantic model
    - Implement `serialize_telemetry_record()` and `deserialize_telemetry_record()` functions
    - Include `ValidationError` model for missing/invalid field reporting
    - _Requirements: 6.1, 6.2, 6.3, 9.1, 9.2, 9.3, 9.4_

  - [x] 1.5 Write property tests for TelemetryRecord serialization
    - **Property 15: Telemetry Record Serialization Round-Trip**
    - **Property 16: Validation Error on Missing Fields**
    - **Validates: Requirements 9.3, 9.4**

- [x] 2. Implement SVG Template Parser and Template Registry
  - [x] 2.1 Implement SVG Template Parser
    - Create `backend/src/svg_parser.py` with `parse_roi_template()` function
    - Parse named groups and rectangles extracting bounding box coordinates (x, y, width, height) relative to 1920×1080
    - Parse circle elements within engine groups extracting (cx, cy, r)
    - Return `ParseError` with descriptive messaging for malformed/missing regions
    - Implement `serialize_roi_configuration()` for round-trip support
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

  - [x] 2.2 Write property tests for SVG Template Parser
    - **Property 1: SVG Template Round-Trip**
    - **Property 2: Malformed SVG Error Completeness**
    - **Validates: Requirements 1.3, 1.5**

  - [x] 2.3 Implement Template Registry
    - Create `backend/src/template_registry.py` with `TemplateRegistry` class
    - Implement `register()`, `get()`, `list_templates()`, and `get_default()` methods
    - Register default `starship_rois` template from the provided SVG file
    - Return `TemplateNotFoundError` for missing template names
    - _Requirements: 8.1, 8.2, 8.3, 8.4_

  - [x] 2.4 Write property test for Template Registry
    - **Property 14: Template Registry Round-Trip**
    - **Validates: Requirements 8.1**

- [ ] 3. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Implement Frame Extractor
  - [x] 4.1 Implement Frame Extractor with OpenCV VideoCapture
    - Create `backend/src/frame_extractor.py` with `FrameExtractor` class
    - Implement `validate()` to check video source URL reachability
    - Implement `start()` / `stop()` for pipeline control with async frame capture loop
    - Implement frame resize to 1920×1080 regardless of source resolution
    - Implement `on_frame()` and `on_status_change()` callback registration
    - Implement exponential backoff reconnection (1s, 2s, 4s, 8s, max 30s) on disconnection
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8_

  - [x] 4.2 Write property test for Frame Resize Invariant
    - **Property 3: Frame Resize Invariant**
    - **Validates: Requirements 2.8**

- [x] 5. Implement Engine Analyzer
  - [x] 5.1 Implement Engine Analyzer with Hough Circle Detection
    - Create `backend/src/engine_analyzer.py` with `analyze_engines()` function and `EngineAnalyzerConfig`
    - Crop frame to engine group bounding box, convert to grayscale
    - Apply HoughCircles with user-tuned params (dp=1.0, minDist=7, param1=50, param2=10)
    - Match detected circles to expected SVG positions within distance tolerance (default 5px)
    - Classify engine color via HSV: active (high V + high S), inactive (low V), undetected (no circle found)
    - Report detection accuracy metric per frame
    - Produce complete status map: 6 Starship engines + 33 Super Heavy engines
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10_

  - [x] 5.2 Write property tests for Engine Analyzer
    - **Property 4: Engine Color Classification Correctness**
    - **Property 5: Circle Position Matching Determinism**
    - **Property 6: Engine Status Map Completeness**
    - **Validates: Requirements 3.4, 3.6, 3.7, 3.9**

- [x] 6. Implement OCR Engine
  - [x] 6.1 Implement EasyOCR-based text extraction
    - Create `backend/src/ocr_engine.py` with `EasyOCREngine` class
    - Initialize EasyOCR Reader with GPU flag from `GPUCapabilities`
    - Implement ROI intersection check: skip OCR for text regions overlapping engine bounding boxes with detected engines
    - Crop frame to each non-occluded ROI and call `reader.readtext()`
    - Parse numeric values (speed/altitude) as float, time as T±HH:MM:SS
    - Mark low-confidence or empty results as `UNAVAILABLE`, engine-occluded as `OCCLUDED_BY_ENGINES`
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7_

  - [x] 6.2 Write property tests for OCR Engine logic
    - **Property 7: OCR Occlusion Logic Consistency**
    - **Property 8: Numeric and Time Parsing Correctness**
    - **Validates: Requirements 4.1, 4.4, 4.5, 4.7**

- [ ] 7. Implement Stage Assignment and Record Assembly
  - [ ] 7.1 Implement Stage Assignment logic
    - Create `backend/src/stage_assignment.py` with `StageAssigner` class
    - Track session-level separation state flag (pre → post, never reverts)
    - Pre-separation: assign all data to "super_heavy"
    - Post-separation with labels: assign per stage_L / stage_R text
    - Post-separation without labels: assign all to "starship"
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5_

  - [ ]* 7.2 Write property tests for Stage Assignment
    - **Property 9: Pre-Separation Assignment Invariant**
    - **Property 10: Post-Separation Label-Based Assignment**
    - **Property 11: Separation State Monotonicity**
    - **Validates: Requirements 5.1, 5.2, 5.4**

  - [ ] 7.3 Implement Record Assembler
    - Create `backend/src/record_assembler.py` with assembly function
    - Combine engine analysis, OCR results, and stage assignment into a single `TelemetryRecord`
    - Assign monotonically increasing sequence numbers
    - Include units from OCR unit regions
    - _Requirements: 6.1, 6.2, 6.3_

  - [ ]* 7.4 Write property tests for Record Assembly
    - **Property 12: Telemetry Record Assembly Completeness**
    - **Property 13: Sequence Number Monotonicity**
    - **Validates: Requirements 6.1, 6.2, 6.3**

- [ ] 8. Checkpoint - Ensure all backend tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 9. Implement WebSocket Server and Pipeline Orchestration
  - [ ] 9.1 Implement FastAPI WebSocket server and REST endpoints
    - Create `backend/src/server.py` with FastAPI app
    - Implement `/ws/telemetry` WebSocket endpoint supporting broadcast to multiple clients
    - Implement `/api/status` GET endpoint returning pipeline status and GPU capabilities
    - Implement `/api/validate-url` POST endpoint for livestream URL validation
    - Mount frontend static files at `/static`
    - _Requirements: 7.2, 7.3, 7.4, 7.5, 7.15, 7.17_

  - [ ] 9.2 Implement Pipeline Orchestrator
    - Create `backend/src/pipeline.py` that wires Frame Extractor → Engine Analyzer → OCR → Stage Assignment → Record Assembly → WebSocket broadcast
    - Handle Start/Stop control commands from WebSocket clients
    - Manage `PipelineState` including status transitions, current template, sequence counter
    - Forward status changes to all connected Dashboard clients
    - _Requirements: 2.3, 2.4, 2.5, 2.6, 7.4, 7.5, 7.17_

- [ ] 10. Implement Frontend Dashboard
  - [ ] 10.1 Set up frontend project structure and build tooling
    - Create `frontend/` with TypeScript config, HTML entry point, CSS with design tokens
    - Apply design tokens: JetBrains Mono font, #262626 background, #FEFEFE text, #FF8014 accent
    - Set up Vitest and fast-check for frontend testing
    - Define TypeScript interfaces: `TelemetryRecord`, `WebSocketMessage`, `PipelineStatus`, `ControlCommand`
    - _Requirements: 7.1_

  - [ ] 10.2 Implement WebSocket client and state management
    - Create WebSocket connection module with auto-reconnection
    - Parse incoming `WebSocketMessage` and dispatch to state handlers
    - Implement `ControlCommand` sending (start, stop, validate_url)
    - Maintain frontend state: pipeline status, latest telemetry record, time-series store
    - _Requirements: 7.15, 9.2_

  - [ ] 10.3 Implement Pipeline Controls UI
    - Create URL input field with submit functionality
    - Display stream validation status (active, unreachable, checking)
    - Show Start button when validated (pipeline stopped), Stop button when running
    - Display pipeline status badge (stopped, running, disconnected)
    - _Requirements: 7.2, 7.3, 7.4, 7.5, 7.17_

  - [ ] 10.4 Implement Telemetry Display
    - Display mission elapsed time prominently
    - Display speed and altitude values with units for both vehicles
    - Display stage labels for left and right stages
    - Show placeholder "--" for unavailable or occluded fields
    - _Requirements: 7.6, 7.7, 7.11, 7.16_

  - [ ] 10.5 Implement Engine Visualizer with SVG diagrams
    - Render Super Heavy engine diagram (3 rings: inner/middle/outer) from superheavy_engine_diagram.svg layout coordinates
    - Render Starship engine diagram (atmo + vacuum groups) from starship_engine_diagram.svg layout coordinates
    - Scale diagrams to readable display size (not native SVG viewport dimensions)
    - Display engine ID text inside each circle (e.g., "E1", "E2") in all-caps
    - Color-code circles by status: active (bright fill), inactive (dimmed/dark), undetected (grey)
    - _Requirements: 7.12, 7.13, 7.14_

  - [ ] 10.6 Implement Time-Series Graphs
    - Create interactive line charts for speed and altitude per vehicle (Super Heavy and Starship)
    - Use mission elapsed time on x-axis, update as new records arrive
    - Implement zoom via mouse drag or scroll interaction
    - Add reset button on each graph to restore full time scale
    - Maintain time-series data store in memory
    - _Requirements: 7.8, 7.9, 7.10_

  - [ ]* 10.7 Write frontend property tests
    - TelemetryRecord deserialization round-trip (fast-check)
    - Time-series store ordering preservation
    - Engine status rendering completeness (all 39 engines rendered)
    - **Validates: Requirements 9.3, 7.12, 7.8**

- [ ] 11. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 12. Integration wiring and end-to-end validation
  - [ ] 12.1 Wire frontend build output to FastAPI static serving
    - Configure frontend build to output to `frontend/dist/`
    - Configure FastAPI to serve `frontend/dist/` as static files
    - Add root route redirecting to Dashboard HTML
    - _Requirements: 7.1_

  - [ ] 12.2 Load default template and verify full pipeline
    - Parse `starship_rois.svg` on startup and register in Template Registry
    - Verify end-to-end flow: static test frame → Engine Analyzer → OCR → Stage Assignment → Record → WebSocket → Dashboard update
    - Ensure <500ms update latency from record availability to dashboard display
    - _Requirements: 8.3, 7.15_

  - [ ]* 12.3 Write integration tests
    - Backend integration: static test frame through full pipeline producing valid TelemetryRecord JSON
    - WebSocket integration: FastAPI test client connects and receives well-formed messages
    - Frame Extractor: validates a local test video file as source
    - _Requirements: 2.1, 6.1, 9.1_

- [ ] 13. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document (Properties 1–16)
- Unit tests validate specific examples and edge cases
- Backend uses Python with Hypothesis for property-based testing and pytest for unit tests
- Frontend uses TypeScript with fast-check for property-based testing and Vitest for unit tests
- GPU acceleration is auto-detected at startup; system works on CPU-only machines without configuration changes

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "1.3"] },
    { "id": 2, "tasks": ["1.4", "2.1"] },
    { "id": 3, "tasks": ["1.5", "2.2", "2.3"] },
    { "id": 4, "tasks": ["2.4", "4.1", "10.1"] },
    { "id": 5, "tasks": ["4.2", "5.1", "10.2"] },
    { "id": 6, "tasks": ["5.2", "6.1", "10.3"] },
    { "id": 7, "tasks": ["6.2", "7.1", "10.4"] },
    { "id": 8, "tasks": ["7.2", "7.3", "10.5"] },
    { "id": 9, "tasks": ["7.4", "9.1", "10.6"] },
    { "id": 10, "tasks": ["9.2", "10.7"] },
    { "id": 11, "tasks": ["12.1"] },
    { "id": 12, "tasks": ["12.2"] },
    { "id": 13, "tasks": ["12.3"] }
  ]
}
```
