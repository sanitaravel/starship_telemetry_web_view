@echo off
REM Start frontend only (Windows). Ensures dependencies are installed first.

setlocal
set ROOT=%~dp0..\..

call "%~dp0setup.bat"
if errorlevel 1 exit /b 1

cd /d "%ROOT%\frontend"

echo.
echo Frontend starting at http://localhost:5173
echo.
call npm run dev
endlocal
