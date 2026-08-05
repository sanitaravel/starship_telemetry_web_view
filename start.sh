#!/usr/bin/env bash
# Start both frontend and backend for Starship Telemetry Web View (Linux/macOS)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "============================================"
echo " Starship Telemetry Web View - Starting..."
echo "============================================"
echo ""

# --- Backend ---
echo "[1/2] Starting backend (FastAPI + Uvicorn)..."

cd "$SCRIPT_DIR/backend"

if [ ! -d ".venv" ]; then
    echo "  Creating virtual environment..."
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -e ".[dev]"
else
    source .venv/bin/activate
fi

uvicorn src.server:app --reload &
BACKEND_PID=$!
echo "  Backend PID: $BACKEND_PID"
echo "  Backend running at http://127.0.0.1:8000"
echo ""

# --- Frontend ---
echo "[2/2] Starting frontend (Vite dev server)..."

cd "$SCRIPT_DIR/frontend"

if [ ! -d "node_modules" ]; then
    echo "  Installing dependencies..."
    npm install
fi

npm run dev &
FRONTEND_PID=$!
echo "  Frontend PID: $FRONTEND_PID"
echo ""

echo "============================================"
echo " Both services are running."
echo "  - Backend:  http://127.0.0.1:8000"
echo "  - Frontend: http://localhost:5173"
echo ""
echo " Press Ctrl+C to stop both services."
echo "============================================"

# Trap Ctrl+C to kill both processes
cleanup() {
    echo ""
    echo "Stopping services..."
    kill $BACKEND_PID 2>/dev/null
    kill $FRONTEND_PID 2>/dev/null
    wait $BACKEND_PID 2>/dev/null
    wait $FRONTEND_PID 2>/dev/null
    echo "Done."
    exit 0
}

trap cleanup SIGINT SIGTERM

# Wait for both processes
wait
