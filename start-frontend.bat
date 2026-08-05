@echo off
REM Start frontend only (Windows)

cd /d %~dp0frontend

if not exist node_modules (
    echo Installing dependencies...
    npm install
)

echo.
echo Frontend starting at http://localhost:5173
echo.
npm run dev
