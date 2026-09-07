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

# Capture backend output to a log so we can wait for readiness while still
# streaming it to this terminal.
BACKEND_LOG="$(mktemp -t starship-backend.XXXXXX.log)"
uvicorn src.server:app --reload > >(tee "$BACKEND_LOG") 2>&1 &
BACKEND_PID=$!
deactivate || true
echo "  Backend PID: $BACKEND_PID"

# Wait until Uvicorn reports the app lifespan has finished starting up.
# Time out after 120s so a failed backend doesn't hang the launcher forever.
echo "  Waiting for backend to finish startup..."
READY_TIMEOUT=120
waited=0
until grep -q "Application startup complete." "$BACKEND_LOG" 2>/dev/null; do
    # Bail out if the backend process died before becoming ready.
    if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
        echo "  ERROR: Backend exited before startup completed. See output above." >&2
        rm -f "$BACKEND_LOG"
        exit 1
    fi
    if [ "$waited" -ge "$READY_TIMEOUT" ]; then
        echo "  ERROR: Timed out after ${READY_TIMEOUT}s waiting for backend startup." >&2
        kill "$BACKEND_PID" 2>/dev/null
        rm -f "$BACKEND_LOG"
        exit 1
    fi
    sleep 1
    waited=$((waited + 1))
done
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
    rm -f "$BACKEND_LOG" 2>/dev/null
    echo "Done."
    exit 0
}

trap cleanup SIGINT SIGTERM

# Wait for both processes
wait
