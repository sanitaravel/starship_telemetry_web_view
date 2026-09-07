@echo off
REM Start backend only (Windows). Ensures dependencies are installed first.

setlocal
set ROOT=%~dp0..\..

call "%~dp0setup.bat"
if errorlevel 1 exit /b 1

cd /d "%ROOT%\backend"
call .venv\Scripts\activate

echo.
echo Backend running at http://127.0.0.1:8000
echo.
uvicorn src.server:app --reload
endlocal
