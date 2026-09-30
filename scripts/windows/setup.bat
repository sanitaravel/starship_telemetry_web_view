@echo off
REM Auto-setup dependencies for Starship Telemetry Web View (Windows)
REM Installs backend (Python venv + deps) and frontend (npm deps).
REM Safe to re-run: skips work that is already done.

setlocal
set ROOT=%~dp0..\..

REM PyTorch build selection.
REM   TORCH_CUDA=auto  (default) -> detect an NVIDIA GPU via nvidia-smi and
REM                                 install the CUDA build if found, else CPU.
REM   TORCH_CUDA=cu124 (or cu121, cu128, ...) -> force that CUDA build.
REM   TORCH_CUDA=cpu   -> force the CPU-only build.
REM The CUDA wheel bundles its own runtime, so no system CUDA toolkit is needed;
REM nvidia-smi ships with the NVIDIA driver, which is the actual requirement.
if "%TORCH_CUDA%"=="" set TORCH_CUDA=auto
REM CUDA version used when TORCH_CUDA=auto detects a GPU.
if "%TORCH_CUDA_VERSION%"=="" set TORCH_CUDA_VERSION=cu121

echo ============================================
echo  Starship Telemetry Web View - Setup
echo ============================================
echo.

REM --- Backend ---
echo [1/2] Setting up backend...
pushd "%ROOT%\backend"

where python >nul 2>&1
if errorlevel 1 (
    echo   ERROR: python not found on PATH. Install Python 3.11+ and retry.
    popd
    exit /b 1
)

if not exist .venv (
    echo   Creating virtual environment...
    python -m venv .venv
)
call .venv\Scripts\activate
echo   Installing backend dependencies...
python -m pip install --upgrade pip

REM Resolve which PyTorch channel to use (goto-based to avoid nested-if quirks).
set TORCH_CHANNEL=%TORCH_CUDA%
if /i not "%TORCH_CUDA%"=="auto" (
    echo   TORCH_CUDA=%TORCH_CUDA% -^> using PyTorch channel "%TORCH_CUDA%".
    goto :torch_resolved
)
where nvidia-smi >nul 2>&1
if errorlevel 1 (
    set TORCH_CHANNEL=cpu
    echo   No NVIDIA GPU detected -^> CPU-only PyTorch.
) else (
    set TORCH_CHANNEL=%TORCH_CUDA_VERSION%
    echo   NVIDIA GPU detected ^(nvidia-smi^) -^> CUDA PyTorch ^(%TORCH_CUDA_VERSION%^).
)
:torch_resolved

REM Install the selected PyTorch build first so the later editable install
REM sees torch/torchvision already satisfied and keeps this build.
pip install torch torchvision --index-url "https://download.pytorch.org/whl/%TORCH_CHANNEL%"

REM Runtime dependencies only. For the dev toolchain use: pip install -e ".[dev]"
pip install -e "."
call deactivate
popd
echo   Backend ready.
echo.

REM --- Frontend ---
echo [2/2] Setting up frontend...
pushd "%ROOT%\frontend"

where npm >nul 2>&1
if errorlevel 1 (
    echo   ERROR: npm not found on PATH. Install Node.js 18+ and retry.
    popd
    exit /b 1
)

echo   Installing frontend dependencies...
REM npm is a batch file (npm.cmd): without "call" control never returns here,
REM and its endlocal restores the directory saved by our setlocal (the repo root).
call npm install
if errorlevel 1 (
    echo   ERROR: npm install failed.
    popd
    exit /b 1
)
popd
echo   Frontend ready.
echo.

echo ============================================
echo  Setup complete.
echo ============================================
endlocal
