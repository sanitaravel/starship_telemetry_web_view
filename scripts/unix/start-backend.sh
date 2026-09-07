#!/usr/bin/env bash
# Start backend only (Linux/macOS). Ensures dependencies are installed first.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Ensure dependencies are installed
"$SCRIPT_DIR/setup.sh"

cd "$ROOT/backend"
# shellcheck disable=SC1091
source .venv/bin/activate

echo "Backend running at http://127.0.0.1:8000"
uvicorn src.server:app --reload
