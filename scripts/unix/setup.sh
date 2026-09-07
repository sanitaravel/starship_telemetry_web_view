#!/usr/bin/env bash
# Auto-setup dependencies for Starship Telemetry Web View (Linux/macOS)
# Installs backend (Python venv + deps) and frontend (npm deps).
# Safe to re-run: skips work that is already done.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# PyTorch build selection (CUDA applies to Linux only; macOS uses the default
# CPU/MPS build since no CUDA wheels exist for it).
#   TORCH_CUDA=auto  (default) -> detect an NVIDIA GPU via nvidia-smi and
#                                 install the CUDA build if found, else CPU.
#   TORCH_CUDA=cu124 (or cu121, cu128, ...) -> force that CUDA build.
#   TORCH_CUDA=cpu   -> force the CPU-only build.
# The CUDA wheel bundles its own runtime, so no system CUDA toolkit is needed;
# nvidia-smi ships with the NVIDIA driver, which is the actual requirement.
TORCH_CUDA="${TORCH_CUDA:-auto}"
# CUDA version used when TORCH_CUDA=auto detects a GPU.
TORCH_CUDA_VERSION="${TORCH_CUDA_VERSION:-cu121}"

echo "============================================"
echo " Starship Telemetry Web View - Setup"
echo "============================================"
echo ""

# --- Backend ---
echo "[1/2] Setting up backend..."
cd "$ROOT/backend"

if ! command -v python3 >/dev/null 2>&1; then
    echo "  ERROR: python3 not found on PATH. Install Python 3.11+ and retry."
    exit 1
fi

if [ ! -d ".venv" ]; then
    echo "  Creating virtual environment..."
    python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
echo "  Installing backend dependencies..."
python -m pip install --upgrade pip

# Resolve which PyTorch channel to use. macOS has no CUDA wheels, so it always
# uses the default build (pip's default index, which provides CPU/MPS).
os_name="$(uname -s)"
torch_channel=""
if [ "$os_name" = "Linux" ]; then
    torch_channel="$TORCH_CUDA"
    if [ "$TORCH_CUDA" = "auto" ]; then
        if command -v nvidia-smi >/dev/null 2>&1; then
            torch_channel="$TORCH_CUDA_VERSION"
            echo "  NVIDIA GPU detected (nvidia-smi) -> CUDA PyTorch ($torch_channel)."
        else
            torch_channel="cpu"
            echo "  No NVIDIA GPU detected -> CPU-only PyTorch."
        fi
    else
        echo "  TORCH_CUDA=$TORCH_CUDA -> using PyTorch channel '$torch_channel'."
    fi
else
    echo "  $os_name detected -> using default PyTorch build (no CUDA wheels)."
fi

# Install the selected PyTorch build first (Linux) so the later editable
# install sees torch/torchvision already satisfied and keeps this build.
if [ -n "$torch_channel" ]; then
    pip install torch torchvision --index-url "https://download.pytorch.org/whl/$torch_channel"
fi

# Runtime dependencies only. For the dev toolchain use: pip install -e ".[dev]"
pip install -e "."
deactivate
echo "  Backend ready."
echo ""

# --- Frontend ---
echo "[2/2] Setting up frontend..."
cd "$ROOT/frontend"

if ! command -v npm >/dev/null 2>&1; then
    echo "  ERROR: npm not found on PATH. Install Node.js 18+ and retry."
    exit 1
fi

echo "  Installing frontend dependencies..."
npm install
echo "  Frontend ready."
echo ""

echo "============================================"
echo " Setup complete."
echo "============================================"
