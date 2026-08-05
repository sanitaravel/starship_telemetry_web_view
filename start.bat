@echo off
REM Start both frontend and backend for Starship Telemetry Web View (Windows)
REM Opens side-by-side windows. Press any key in this window to stop both.

echo ============================================
echo  Starship Telemetry Web View - Starting...
echo ============================================
echo.

echo [1/2] Starting backend (FastAPI + Uvicorn)...
start "Starship Backend" cmd /c "cd /d %~dp0backend && if not exist .venv (echo Creating virtual environment... && python -m venv .venv && call .venv\Scripts\activate && pip install -e ".[dev]") else (call .venv\Scripts\activate) && uvicorn src.server:app --reload"

timeout /t 1 /nobreak >nul

echo [2/2] Starting frontend (Vite dev server)...
start "Starship Frontend" cmd /c "cd /d %~dp0frontend && if not exist node_modules (echo Installing dependencies... && npm install) && npm run dev"

timeout /t 1 /nobreak >nul

REM Use PowerShell to tile the windows side-by-side
powershell -NoProfile -Command ^
  "Add-Type @'`n"^
  "using System; using System.Runtime.InteropServices;`n"^
  "public class W { [DllImport(\"user32.dll\")] public static extern bool MoveWindow(IntPtr h, int x, int y, int w, int h2, bool r);`n"^
  "  [DllImport(\"user32.dll\")] public static extern IntPtr FindWindow(string c, string t); }`n"^
  "'@;`n"^
  "Add-Type -AssemblyName System.Windows.Forms;`n"^
  "$s = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea;`n"^
  "$hw = [math]::Floor($s.Width/2);`n"^
  "$b = [W]::FindWindow($null,'Starship Backend');`n"^
  "$f = [W]::FindWindow($null,'Starship Frontend');`n"^
  "if($b -ne [IntPtr]::Zero){[W]::MoveWindow($b,$s.Left,0,$hw,$s.Height,$true)|Out-Null}`n"^
  "if($f -ne [IntPtr]::Zero){[W]::MoveWindow($f,$s.Left+$hw,0,$hw,$s.Height,$true)|Out-Null}"

echo.
echo ============================================
echo  Both services are running (side-by-side).
echo  - Backend:  http://127.0.0.1:8000 (left)
echo  - Frontend: http://localhost:5173 (right)
echo ============================================
echo.
echo Press any key to STOP both services...
pause >nul

echo.
echo Stopping services...
taskkill /fi "WINDOWTITLE eq Starship Backend*" /f >nul 2>&1
taskkill /fi "WINDOWTITLE eq Starship Frontend*" /f >nul 2>&1
taskkill /im uvicorn.exe /f >nul 2>&1
taskkill /im node.exe /f >nul 2>&1
echo Done.
