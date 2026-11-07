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
# Rebuild when the frontend changed since the last build (an old build shows removed pages).
if [ ! -f .next/BUILD_ID ] || [ -n "$(find src public package.json package-lock.json next.config.* -newer .next/BUILD_ID -print -quit 2>/dev/null)" ]; then
    npm run build
fi
echo "Dashboard: http://localhost:3000/dashboard"
npm run start
