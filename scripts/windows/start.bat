@echo off
REM Start both frontend and backend for Starship Telemetry Web View (Windows)
REM Opens backend and frontend in side-by-side windows.
REM Press Enter in this window to stop both services.

setlocal
set "ROOT=%~dp0..\.."
set "BACKEND_DIR=%ROOT%\backend"
set "FRONTEND_DIR=%ROOT%\frontend"
set READY_TIMEOUT_SECONDS=120

REM ANSI colours (supported by the Windows 10+ console)
for /f %%e in ('echo prompt $E ^| cmd') do set "ESC=%%e"
set "CYAN=%ESC%[36m"
set "YELLOW=%ESC%[33m"
set "GREEN=%ESC%[32m"
set "RED=%ESC%[31m"
set "RESET=%ESC%[0m"

echo %CYAN%============================================%RESET%
echo %CYAN% Starship Telemetry Web View - Starting...%RESET%
echo %CYAN%============================================%RESET%
echo.

REM Ensure dependencies are installed before launching
call "%~dp0setup.bat"
if errorlevel 1 exit /b 1
echo.

REM --- Backend ---
echo %YELLOW%[1/2] Starting backend (FastAPI + Uvicorn)...%RESET%

REM Log file the backend window writes to, so this launcher can detect readiness.
REM The backend window inherits this variable.
set "BACKEND_LOG=%TEMP%\starship-backend-%RANDOM%%RANDOM%.log"
if exist "%BACKEND_LOG%" del /f /q "%BACKEND_LOG%" >nul 2>&1

REM Tee output so the window stays interactive while the launcher watches the log.
REM The tee is PowerShell's; its script is passed base64-encoded (UTF-16LE) so no
REM quoting survives three shells. It decodes to:
REM   $input | Tee-Object -FilePath $env:BACKEND_LOG
set "TEE_CMD=powershell -NoProfile -EncodedCommand JABpAG4AcAB1AHQAIAB8ACAAVABlAGUALQBPAGIAagBlAGMAdAAgAC0ARgBpAGwAZQBQAGEAdABoACAAJABlAG4AdgA6AEIAQQBDAEsARQBOAEQAXwBMAE8ARwA="

REM Start-Process -PassThru gives us the window's PID, so we can watch it and
REM stop exactly the processes we started.
set BACKEND_PID=
for /f "usebackq delims=" %%P in (`powershell -NoProfile -Command "(Start-Process cmd -WorkingDirectory $env:BACKEND_DIR -ArgumentList ('/c title Starship Backend&& call .venv\Scripts\activate.bat&& echo Backend running at http://127.0.0.1:8000&& echo.&& uvicorn src.server:app --reload 2>&1 | ' + $env:TEE_CMD) -PassThru).Id"`) do set "BACKEND_PID=%%P"
if not defined BACKEND_PID (
    echo %RED%  ERROR: Failed to start the backend window.%RESET%
    exit /b 1
)

ping -n 2 127.0.0.1 >nul

REM Position backend window on the left half
call :position %BACKEND_PID% 0

REM Wait until Uvicorn reports the app lifespan has finished starting up.
REM Time out after 120s so a failed backend doesn't hang the launcher forever.
echo %YELLOW%  Waiting for backend to finish startup...%RESET%
set WAITED=0

:wait_backend
tasklist /fi "PID eq %BACKEND_PID%" /fo csv /nh 2>nul | find """%BACKEND_PID%""" >nul
if errorlevel 1 goto :backend_exited
REM "type" decodes the UTF-16 log that Windows PowerShell's Tee-Object writes.
if exist "%BACKEND_LOG%" (
    type "%BACKEND_LOG%" 2>nul | find "Application startup complete." >nul
    if not errorlevel 1 goto :backend_ready
)
if %WAITED% geq %READY_TIMEOUT_SECONDS% goto :backend_timeout
ping -n 2 127.0.0.1 >nul
set /a WAITED+=1
goto :wait_backend

:backend_exited
echo %RED%  ERROR: Backend exited before startup completed. See its window for details.%RESET%
del /f /q "%BACKEND_LOG%" >nul 2>&1
exit /b 1

:backend_timeout
echo %RED%  ERROR: Timed out after %READY_TIMEOUT_SECONDS%s waiting for backend startup.%RESET%
taskkill /pid %BACKEND_PID% /t /f >nul 2>&1
del /f /q "%BACKEND_LOG%" >nul 2>&1
exit /b 1

:backend_ready
echo %GREEN%  Backend startup complete.%RESET%

REM --- Frontend ---
echo %YELLOW%[2/2] Starting frontend (Vite dev server)...%RESET%

set FRONTEND_PID=
for /f "usebackq delims=" %%P in (`powershell -NoProfile -Command "(Start-Process cmd -WorkingDirectory $env:FRONTEND_DIR -ArgumentList '/c title Starship Frontend&& echo Frontend running at http://localhost:5173&& echo.&& npm run dev' -PassThru).Id"`) do set "FRONTEND_PID=%%P"
if not defined FRONTEND_PID (
    echo %RED%  ERROR: Failed to start the frontend window.%RESET%
    taskkill /pid %BACKEND_PID% /t /f >nul 2>&1
    del /f /q "%BACKEND_LOG%" >nul 2>&1
    exit /b 1
)

ping -n 2 127.0.0.1 >nul

REM Position frontend window on the right half
call :position %FRONTEND_PID% 1

echo.
echo %CYAN%============================================%RESET%
echo %CYAN% Both services are running (side-by-side).%RESET%
echo %GREEN%  - Backend:  http://127.0.0.1:8000 (left)%RESET%
echo %GREEN%  - Frontend: http://localhost:5173 (right)%RESET%
echo %CYAN%============================================%RESET%
echo.

set /p "_=%RED%Press Enter to STOP both services...%RESET%"

echo.
echo %YELLOW%Stopping services...%RESET%

REM Kill process trees (the windows and everything they started)
taskkill /pid %BACKEND_PID% /t /f >nul 2>&1
taskkill /pid %FRONTEND_PID% /t /f >nul 2>&1

if exist "%BACKEND_LOG%" del /f /q "%BACKEND_LOG%" >nul 2>&1

echo %GREEN%Done.%RESET%
endlocal
exit /b 0

REM Move a process's main window to one half of the primary screen.
REM   %1 = process ID, %2 = 0 for the left half, 1 for the right half
:position
powershell -NoProfile -Command "Add-Type -AssemblyName System.Windows.Forms; Add-Type -Name WinPos -Namespace W -MemberDefinition '[DllImport(\"user32.dll\")] public static extern bool MoveWindow(System.IntPtr hWnd, int X, int Y, int nWidth, int nHeight, bool bRepaint);'; $s = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea; $hw = [math]::Floor($s.Width / 2); $p = Get-Process -Id %1 -ErrorAction SilentlyContinue; if ($p -and $p.MainWindowHandle -ne [IntPtr]::Zero) { [W.WinPos]::MoveWindow($p.MainWindowHandle, $s.Left + %2 * $hw, 0, $hw, $s.Height, $true) | Out-Null }"
exit /b 0
