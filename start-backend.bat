@echo off
REM Start backend only (Windows)

cd /d %~dp0backend

if not exist .venv (
    echo Creating virtual environment...
    python -m venv .venv
    call .venv\Scripts\activate
    pip install -e ".[dev]"
) else (
    call .venv\Scripts\activate
)

echo.
echo Backend running at http://127.0.0.1:8000
echo.
uvicorn src.server:app --reload
