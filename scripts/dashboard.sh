#!/usr/bin/env bash
# Start the read-only dashboard: API on :8000, web page on :3000.
# Usage: scripts/dashboard.sh   (stop with Ctrl-C)
set -euo pipefail
cd "$(dirname "$0")/.."
(vcp api serve) &
API=$!
trap 'kill $API 2>/dev/null || true' EXIT
cd frontend
[ -d node_modules ] || npm ci
[ -d .next ] || npm run build
echo "Dashboard: http://localhost:3000/dashboard"
npm run start
