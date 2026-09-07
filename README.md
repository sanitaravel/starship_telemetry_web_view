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

Frames are processed concurrently with a bounded concurrency controller and reassembled in
order via a reorder buffer, so parallel OCR/vision work does not scramble the telemetry timeline.

### Dashboard

- Live speed/altitude values with time-series graphs (zoom and pan enabled)
- Engine status visualization (6 Starship + 33 Super Heavy engines)
- Pipeline controls (URL input, start/stop) with live frame preview and an FPS meter
- Previous flights view for reviewing past runs stored in `results_previous/`
- Auto-reconnecting WebSocket client to survive dropped connections
- Design tokens: JetBrains Mono, dark theme (#262626 bg, #FEFEFE text, #FF8014 accent)

## Tech Stack

| Layer    | Technology                              |
|----------|-----------------------------------------|
| Backend  | Python, FastAPI, OpenCV, EasyOCR, Torch |
| Frontend | TypeScript, Chart.js, WebSocket         |
| Testing  | pytest, Hypothesis, Vitest, fast-check  |

## Getting Started

### Prerequisites: Git LFS

The stored telemetry in `results_previous/` is tracked with [Git LFS](https://git-lfs.com/).
Install it **before cloning** so those files are pulled as real data instead of pointer stubs:

```bash
# Install Git LFS (see https://git-lfs.com for other platforms)
# macOS:            brew install git-lfs
# Debian/Ubuntu:    sudo apt install git-lfs
# Windows:          winget install GitHub.GitLFS  (or use Git for Windows)

# Enable it once per machine, then clone as usual
git lfs install
git clone <repo-url>
```

If you cloned before installing Git LFS, run `git lfs install` followed by `git lfs pull` to
fetch the actual `results_previous/` files.

All helper scripts live under `scripts/`, split by platform: `scripts/windows/` and `scripts/unix/`.

### One-time setup

The setup script creates the Python virtual environment, installs backend and frontend
dependencies, and is safe to re-run. It checks for `python`/`python3` and `npm` up front.

```bash
# Windows (PowerShell)
scripts\windows\setup.ps1

# Windows (cmd)
scripts\windows\setup.bat

# Linux/macOS
./scripts/unix/setup.sh
```

### Quick start (both services)

The launcher scripts run setup automatically, then start the backend and frontend together.

```bash
# Windows (PowerShell) — opens backend/frontend side-by-side
scripts\windows\start.ps1

# Windows (cmd)
scripts\windows\start.bat

# Linux/macOS
./scripts/unix/start.sh
```

- Backend: http://127.0.0.1:8000
- Frontend: http://localhost:5173

To run a single service, use the `start-backend` or `start-frontend` script for your platform
(e.g. `scripts\windows\start-backend.bat` or `./scripts/unix/start-frontend.sh`).

### Manual setup

The setup scripts install **runtime dependencies only** (`pip install -e .`). For development
work, install the dev extra to get pytest, Hypothesis, and httpx:

#### Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate    # Windows
# source .venv/bin/activate  # Linux/macOS
pip install -e ".[dev]"    # runtime + dev tools (drop [dev] for runtime only)
pytest
```

#### Frontend

```bash
cd frontend
npm install
npm run dev
npm test        # run the Vitest suite
```

#### Run the server

```bash
cd backend
.venv\Scripts\activate
uvicorn src.server:app --reload
```

## Project Structure

```
├── backend/
│   ├── src/              # Pipeline, OCR, vision, server, concurrency
│   ├── tests/            # pytest + Hypothesis tests
│   └── pyproject.toml    # Python project config
├── frontend/
│   ├── src/              # Dashboard modules + Vitest specs
│   └── package.json      # Frontend dependencies
├── diagrams/             # SVG engine diagrams and ROI templates
├── results_previous/     # Stored telemetry from past runs (Git LFS)
├── scripts/
│   ├── windows/          # setup / start scripts (.bat, .ps1)
│   └── unix/             # setup / start scripts (.sh)
└── README.md
```

Each platform folder contains `setup`, `start`, `start-backend`, and `start-frontend`
scripts. `setup` installs runtime dependencies (not the dev extra); the `start*` scripts run
setup first, then launch.

## GPU Support

The system auto-detects CUDA GPU availability at startup. If a CUDA-capable GPU is present, EasyOCR uses GPU-accelerated inference. Otherwise it falls back transparently to CPU mode with no configuration needed.

GPU acceleration requires a **CUDA build of PyTorch**. On Windows and Linux the setup scripts
**auto-detect an NVIDIA GPU** by checking for `nvidia-smi` (which ships with the NVIDIA driver):

- GPU present -> installs the CUDA build (`https://download.pytorch.org/whl/cu121` by default).
- No GPU -> installs the smaller CPU-only build (`.../whl/cpu`).

So a machine with an NVIDIA GPU gets acceleration out of the box, and a CPU-only machine avoids
downloading the large CUDA wheel. macOS always uses the default CPU/MPS build (no CUDA wheels
exist for it).

Notes:
- The CUDA wheel bundles its own CUDA runtime, so no system CUDA toolkit is needed — only a
  reasonably recent NVIDIA driver. Only NVIDIA/CUDA is supported (no AMD ROCm or Apple MPS).
- Detection is driver-based: `nvidia-smi` on `PATH` is the signal. A CUDA wheel still runs on a
  machine without a GPU (it falls back to CPU), it's just larger than needed.
- Override with `TORCH_CUDA`: set a specific channel like `cu124` or `cu128` to force that CUDA
  build, or `cpu` to force the CPU build. When left at `auto` (the default), set
  `TORCH_CUDA_VERSION` to change which CUDA channel is used when a GPU is found.

```bash
# Force a specific CUDA build
# Windows (PowerShell)
$env:TORCH_CUDA = "cu124"; scripts\windows\setup.ps1
# Windows (cmd)
set TORCH_CUDA=cu124 && scripts\windows\setup.bat
# Linux
TORCH_CUDA=cu124 ./scripts/unix/setup.sh

# Force CPU-only
TORCH_CUDA=cpu ./scripts/unix/setup.sh

# Keep auto-detection but use a different CUDA channel when a GPU is found
TORCH_CUDA_VERSION=cu124 ./scripts/unix/setup.sh
```

The dashboard reports the active backend (GPU device name or CPU) in the pipeline status.
