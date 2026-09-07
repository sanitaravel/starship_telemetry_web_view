#!/usr/bin/env bash
# Start both frontend and backend for Starship Telemetry Web View (Linux/macOS)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "============================================"
echo " Starship Telemetry Web View - Starting..."
echo "============================================"
echo ""

# Ensure dependencies are installed
"$SCRIPT_DIR/setup.sh"
echo ""

# --- Backend ---
echo "[1/2] Starting backend (FastAPI + Uvicorn)..."
cd "$ROOT/backend"
# shellcheck disable=SC1091
source .venv/bin/activate
uvicorn src.server:app --reload &
BACKEND_PID=$!
deactivate || true
echo "  Backend PID: $BACKEND_PID"
echo "  Backend running at http://127.0.0.1:8000"
echo ""

# --- Frontend ---
echo "[2/2] Starting frontend (Vite dev server)..."
cd "$ROOT/frontend"
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
