#!/usr/bin/env bash
# Evening pipeline for the VCP scanner (see src/vcp_scanner/daily.py).
# Schedule it on weekdays after NSE publishes the day's bhavcopy (about 19:00 IST); a run
# after skipped evenings catches up everything except those days' ASM/GSM lists.
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p data/logs
LOG="data/logs/daily_run_$(date +%Y-%m-%d).out"
STATE=data/logs/daily_run.state
touch "$LOG"
OFFSET=$(stat -c %s "$LOG")
exec >>"$LOG" 2>&1
# One run at a time: two scheduled runs can start together after the PC wakes up.
exec 9>data/logs/daily_run.lock
if ! flock -n 9; then
    echo "=== $(TZ=Asia/Kolkata date '+%Y-%m-%d %H:%M') IST: another run is in progress; skipped"
    exit 0
fi
# Progress for the dashboard (vcp_scanner/run_state.py): who is running, since when, and, from
# the EXIT trap, how it ended. A run killed with its console never reaches the trap; the
# dashboard then sees a dead pid and reports the run as stopped.
BEFORE=$(grep -c . data/logs/daily_runs.log 2>/dev/null || true)
STARTED=$(TZ=Asia/Kolkata date -Iseconds)
write_state() {
    printf 'pid=%s\nstarted=%s\nlog=%s\nlog_offset=%s\nsummaries_before=%s\nfinished=%s\nexit=%s\n' \
        "$$" "$STARTED" "$LOG" "$OFFSET" "${BEFORE:-0}" "$1" "$2" >"$STATE.tmp" && mv -f "$STATE.tmp" "$STATE"
}
write_state "" ""
trap 'rc=$?; write_state "$(TZ=Asia/Kolkata date -Iseconds)" "$rc"' EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
echo "=== start $(TZ=Asia/Kolkata date '+%Y-%m-%d %H:%M') IST"
.venv/bin/vcp run daily --db data/vcp_scanner.duckdb --config-dir config --env-file .env --serving-copy
code=$?
echo "=== end $(TZ=Asia/Kolkata date '+%Y-%m-%d %H:%M') IST, exit $code"
exit $code
