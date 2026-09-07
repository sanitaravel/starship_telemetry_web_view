#!/usr/bin/env bash
# Start frontend only (Linux/macOS). Ensures dependencies are installed first.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Ensure dependencies are installed
"$SCRIPT_DIR/setup.sh"

cd "$ROOT/frontend"

echo "Frontend starting at http://localhost:5173"
npm run dev
