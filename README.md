# Starship Telemetry Web View

Real-time telemetry extraction and display system for SpaceX Starship livestreams.

## Overview

This system captures frames from a Starship livestream, extracts telemetry data (speed, altitude, mission time, engine status) using computer vision and OCR, and displays it in a live web dashboard.

### Pipeline

1. **Frame Extraction** — Captures frames from a livestream via OpenCV
2. **Engine Analysis** — Detects engine indicators using Hough Circle Detection and classifies status by color
3. **OCR Extraction** — Reads text values (time, speed, altitude, stage labels) from defined regions using EasyOCR
4. **Stage Assignment** — Attributes telemetry to the correct vehicle based on separation state
5. **WebSocket Broadcast** — Pushes structured telemetry records to connected dashboard clients

### Dashboard

- Live speed/altitude values with time-series graphs
- Engine status visualization (6 Starship + 33 Super Heavy engines)
- Pipeline controls (URL input, start/stop)
- Design tokens: JetBrains Mono, dark theme (#262626 bg, #FEFEFE text, #FF8014 accent)

## Tech Stack

| Layer    | Technology                              |
|----------|-----------------------------------------|
| Backend  | Python, FastAPI, OpenCV, EasyOCR, Torch |
| Frontend | TypeScript, Chart.js, WebSocket         |
| Testing  | pytest, Hypothesis, Vitest, fast-check  |

## Getting Started

### Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate    # Windows
# source .venv/bin/activate  # Linux/macOS
pip install -e ".[dev]"
pytest
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

### Run the server

```bash
cd backend
.venv\Scripts\activate
uvicorn src.server:app --reload
```

## Project Structure

```
├── backend/
│   ├── src/            # Application source code
│   ├── tests/          # pytest + Hypothesis tests
│   └── pyproject.toml  # Python project config
├── frontend/
│   └── package.json    # Frontend dependencies
├── diagrams/           # SVG engine diagrams and ROI templates
└── README.md
```

## GPU Support

The system auto-detects CUDA GPU availability at startup. If a CUDA-capable GPU is present, EasyOCR uses GPU-accelerated inference. Otherwise it falls back transparently to CPU mode with no configuration needed.
