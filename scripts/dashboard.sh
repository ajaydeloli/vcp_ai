#!/usr/bin/env bash
# Start the read-only dashboard: API on :8000, web page on :3000.
# Usage: scripts/dashboard.sh   (stop with Ctrl-C)
set -euo pipefail
cd "$(dirname "$0")/.."
(.venv/bin/vcp api serve) &
API=$!
# Telegram pivot alerts in market hours (config/alerts.yaml); stops by itself if not set up.
(sleep 20; exec .venv/bin/vcp alerts watch >> logs/alerts_watch.log 2>&1) &
WATCH=$!
trap 'kill $API $WATCH 2>/dev/null || true' EXIT
cd frontend
[ -d node_modules ] || npm ci
[ -d .next ] || npm run build
echo "Dashboard: http://localhost:3000/dashboard"
npm run start
