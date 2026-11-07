#!/usr/bin/env bash
# Start the dashboard at Windows logon (Task Scheduler "VCP dashboard", headless).
# Skips if it is already running; output goes to data/logs/dashboard.out.
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p data/logs logs
LOG=data/logs/dashboard.out
if (exec 3<>/dev/tcp/127.0.0.1/3000) 2>/dev/null || (exec 3<>/dev/tcp/127.0.0.1/8000) 2>/dev/null; then
    echo "=== $(TZ=Asia/Kolkata date '+%Y-%m-%d %H:%M') IST: dashboard already running; skipped" >>"$LOG"
    exit 0
fi
echo "=== start $(TZ=Asia/Kolkata date '+%Y-%m-%d %H:%M') IST" >>"$LOG"
exec bash scripts/dashboard.sh >>"$LOG" 2>&1
