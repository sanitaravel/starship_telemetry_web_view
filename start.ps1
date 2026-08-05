# Start both frontend and backend for Starship Telemetry Web View (PowerShell)
# Opens backend and frontend in side-by-side windows.
# Press Enter in this window to stop both services.

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "============================================" -ForegroundColor Cyan
Write-Host " Starship Telemetry Web View - Starting..." -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# Get screen dimensions
Add-Type -AssemblyName System.Windows.Forms
$screen = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
$halfWidth = [math]::Floor($screen.Width / 2)
$height = $screen.Height
$leftX = $screen.Left
$rightX = $screen.Left + $halfWidth

# Helper to position a window
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class WinPos {
    [DllImport("user32.dll")]
    public static extern bool MoveWindow(IntPtr hWnd, int X, int Y, int nWidth, int nHeight, bool bRepaint);
}
"@

# --- Backend ---
Write-Host "[1/2] Starting backend (FastAPI + Uvicorn)..." -ForegroundColor Yellow

$backendDir = Join-Path $ScriptDir "backend"
$venvDir = Join-Path $backendDir ".venv"

if ($IsWindows -or $env:OS -match "Windows") {
    $activateScript = Join-Path $venvDir "Scripts\Activate.ps1"
} else {
    $activateScript = Join-Path $venvDir "bin/Activate.ps1"
}

$backendCmd = @"
`$Host.UI.RawUI.WindowTitle = 'Starship Backend'
Set-Location '$backendDir'
if (-not (Test-Path '.venv')) {
    Write-Host 'Creating virtual environment...' -ForegroundColor Yellow
    python3 -m venv .venv
    & '$activateScript'
    pip install -e '.[dev]'
} else {
    & '$activateScript'
}
Write-Host 'Backend running at http://127.0.0.1:8000' -ForegroundColor Green
Write-Host ''
uvicorn src.server:app --reload
"@

$backendProcess = Start-Process pwsh -ArgumentList "-NoProfile", "-Command", $backendCmd -PassThru

Start-Sleep -Seconds 1

# Position backend window on the left half
if ($backendProcess.MainWindowHandle -ne [IntPtr]::Zero) {
    [WinPos]::MoveWindow($backendProcess.MainWindowHandle, $leftX, 0, $halfWidth, $height, $true) | Out-Null
}

# --- Frontend ---
Write-Host "[2/2] Starting frontend (Vite dev server)..." -ForegroundColor Yellow

$frontendDir = Join-Path $ScriptDir "frontend"

$frontendCmd = @"
`$Host.UI.RawUI.WindowTitle = 'Starship Frontend'
Set-Location '$frontendDir'
if (-not (Test-Path 'node_modules')) {
    Write-Host 'Installing dependencies...' -ForegroundColor Yellow
    npm install
}
Write-Host 'Frontend running at http://localhost:5173' -ForegroundColor Green
Write-Host ''
npm run dev
"@

$frontendProcess = Start-Process pwsh -ArgumentList "-NoProfile", "-Command", $frontendCmd -PassThru

Start-Sleep -Seconds 1

# Position frontend window on the right half
if ($frontendProcess.MainWindowHandle -ne [IntPtr]::Zero) {
    [WinPos]::MoveWindow($frontendProcess.MainWindowHandle, $rightX, 0, $halfWidth, $height, $true) | Out-Null
}

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host " Both services are running (side-by-side)." -ForegroundColor Cyan
Write-Host "  - Backend:  http://127.0.0.1:8000 (left)" -ForegroundColor Green
Write-Host "  - Frontend: http://localhost:5173 (right)" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Press Enter to STOP both services..." -ForegroundColor Red

Read-Host | Out-Null

Write-Host ""
Write-Host "Stopping services..." -ForegroundColor Yellow

# Kill process trees
function Stop-Tree($proc) {
    if ($proc -and -not $proc.HasExited) {
        # Kill children first
        Get-CimInstance Win32_Process | Where-Object { $_.ParentProcessId -eq $proc.Id } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    }
}

Stop-Tree $backendProcess
Stop-Tree $frontendProcess

Write-Host "Done." -ForegroundColor Green
