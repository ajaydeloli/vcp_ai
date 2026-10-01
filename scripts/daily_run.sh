#!/usr/bin/env bash
# Evening pipeline for the VCP scanner (see src/vcp_scanner/daily.py).
# Schedule it on weekdays after NSE publishes the day's bhavcopy (about 19:00 IST); a run
# after skipped evenings catches up everything except those days' ASM/GSM lists.
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p data/logs
exec >>"data/logs/daily_run_$(date +%Y-%m-%d).out" 2>&1
# One run at a time: two scheduled runs can start together after the PC wakes up.
exec 9>data/logs/daily_run.lock
if ! flock -n 9; then
    echo "=== $(TZ=Asia/Kolkata date '+%Y-%m-%d %H:%M') IST: another run is in progress; skipped"
    exit 0
fi
echo "=== start $(TZ=Asia/Kolkata date '+%Y-%m-%d %H:%M') IST"
.venv/bin/vcp run daily --db data/vcp_scanner.duckdb --config-dir config --env-file .env
code=$?
echo "=== end $(TZ=Asia/Kolkata date '+%Y-%m-%d %H:%M') IST, exit $code"
exit $code
