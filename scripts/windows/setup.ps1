# Auto-setup dependencies for Starship Telemetry Web View (PowerShell)
# Installs backend (Python venv + deps) and frontend (npm deps).
# Safe to re-run: skips work that is already done.

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")

# PyTorch build selection.
#   $env:TORCH_CUDA = "auto"  (default) -> detect an NVIDIA GPU via nvidia-smi
#                                           and install the CUDA build if found,
#                                           otherwise the CPU build.
#   $env:TORCH_CUDA = "cu124" (or cu121, cu128, ...) -> force that CUDA build.
#   $env:TORCH_CUDA = "cpu"   -> force the CPU-only build.
# The CUDA wheel bundles its own runtime, so no system CUDA toolkit is needed;
# nvidia-smi ships with the NVIDIA driver, which is the actual requirement.
$TorchCuda = if ($env:TORCH_CUDA) { $env:TORCH_CUDA } else { "auto" }
# CUDA version used when TORCH_CUDA=auto detects a GPU.
$TorchCudaVersion = if ($env:TORCH_CUDA_VERSION) { $env:TORCH_CUDA_VERSION } else { "cu121" }

Write-Host "============================================" -ForegroundColor Cyan
Write-Host " Starship Telemetry Web View - Setup" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# --- Backend ---
Write-Host "[1/2] Setting up backend..." -ForegroundColor Yellow
$backendDir = Join-Path $Root "backend"

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "python not found on PATH. Install Python 3.11+ and retry."
}

Push-Location $backendDir
try {
    if (-not (Test-Path ".venv")) {
        Write-Host "  Creating virtual environment..."
        python -m venv .venv
    }
    & ".venv\Scripts\Activate.ps1"
    Write-Host "  Installing backend dependencies..."
    python -m pip install --upgrade pip

    # Resolve which PyTorch channel to use.
    $torchChannel = $TorchCuda
    if ($TorchCuda -eq "auto") {
        if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
            $torchChannel = $TorchCudaVersion
            Write-Host "  NVIDIA GPU detected (nvidia-smi) -> CUDA PyTorch ($torchChannel)." -ForegroundColor Green
        } else {
            $torchChannel = "cpu"
            Write-Host "  No NVIDIA GPU detected -> CPU-only PyTorch." -ForegroundColor DarkGray
        }
    } else {
        Write-Host "  TORCH_CUDA=$TorchCuda -> using PyTorch channel '$torchChannel'."
    }

    # Install the selected PyTorch build first so the later editable install
    # sees torch/torchvision already satisfied and keeps this build.
    pip install torch torchvision --index-url "https://download.pytorch.org/whl/$torchChannel"

    # Runtime dependencies only. For the dev toolchain use: pip install -e ".[dev]"
    pip install -e "."
    deactivate
} finally {
    Pop-Location
}
Write-Host "  Backend ready." -ForegroundColor Green
Write-Host ""

# --- Frontend ---
Write-Host "[2/2] Setting up frontend..." -ForegroundColor Yellow
$frontendDir = Join-Path $Root "frontend"

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw "npm not found on PATH. Install Node.js 18+ and retry."
}

Push-Location $frontendDir
try {
    Write-Host "  Installing frontend dependencies..."
    npm install
} finally {
    Pop-Location
}
Write-Host "  Frontend ready." -ForegroundColor Green
Write-Host ""

Write-Host "============================================" -ForegroundColor Cyan
Write-Host " Setup complete." -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
