# Start both frontend and backend for Starship Telemetry Web View (PowerShell)
# Opens backend and frontend in side-by-side windows.
# Press Enter in this window to stop both services.

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")

Write-Host "============================================" -ForegroundColor Cyan
Write-Host " Starship Telemetry Web View - Starting..." -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# Ensure dependencies are installed before launching
& (Join-Path $PSScriptRoot "setup.ps1")
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

$backendDir = Join-Path $Root "backend"
$venvDir = Join-Path $backendDir ".venv"

if ($IsWindows -or $env:OS -match "Windows") {
    $activateScript = Join-Path $venvDir "Scripts\Activate.ps1"
} else {
    $activateScript = Join-Path $venvDir "bin/Activate.ps1"
}

# Log file the backend window writes to, so this launcher can detect readiness.
$backendLog = Join-Path ([System.IO.Path]::GetTempPath()) "starship-backend-$PID.log"
if (Test-Path $backendLog) { Remove-Item $backendLog -Force -ErrorAction SilentlyContinue }

$backendCmd = @"
`$Host.UI.RawUI.WindowTitle = 'Starship Backend'
Set-Location '$backendDir'
& '$activateScript'
Write-Host 'Backend running at http://127.0.0.1:8000' -ForegroundColor Green
Write-Host ''
# Tee output so the window stays interactive while the launcher watches the log.
uvicorn src.server:app --reload 2>&1 | Tee-Object -FilePath '$backendLog'
"@

$backendProcess = Start-Process pwsh -ArgumentList "-NoProfile", "-Command", $backendCmd -PassThru

Start-Sleep -Seconds 1

# Position backend window on the left half
if ($backendProcess.MainWindowHandle -ne [IntPtr]::Zero) {
    [WinPos]::MoveWindow($backendProcess.MainWindowHandle, $leftX, 0, $halfWidth, $height, $true) | Out-Null
}

# Wait until Uvicorn reports the app lifespan has finished starting up.
# Time out after 120s so a failed backend doesn't hang the launcher forever.
Write-Host "  Waiting for backend to finish startup..." -ForegroundColor Yellow
$readyTimeoutSeconds = 120
$waited = 0
$backendReady = $false
while ($waited -lt $readyTimeoutSeconds) {
    if ($backendProcess.HasExited) {
        Write-Host "  ERROR: Backend exited before startup completed. See its window for details." -ForegroundColor Red
        exit 1
    }
    if ((Test-Path $backendLog) -and
        (Select-String -Path $backendLog -Pattern 'Application startup complete.' -SimpleMatch -Quiet)) {
        $backendReady = $true
        break
    }
    Start-Sleep -Seconds 1
    $waited++
}

if (-not $backendReady) {
    Write-Host "  ERROR: Timed out after $readyTimeoutSeconds`s waiting for backend startup." -ForegroundColor Red
    Stop-Process -Id $backendProcess.Id -Force -ErrorAction SilentlyContinue
    exit 1
}
Write-Host "  Backend startup complete." -ForegroundColor Green

# --- Frontend ---
Write-Host "[2/2] Starting frontend (Vite dev server)..." -ForegroundColor Yellow

$frontendDir = Join-Path $Root "frontend"

$frontendCmd = @"
`$Host.UI.RawUI.WindowTitle = 'Starship Frontend'
Set-Location '$frontendDir'
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

if (Test-Path $backendLog) { Remove-Item $backendLog -Force -ErrorAction SilentlyContinue }

Write-Host "Done." -ForegroundColor Green
