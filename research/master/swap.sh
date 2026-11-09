#!/usr/bin/env bash
# Switch the live database to the merged master (owner go 2026-11-09 10:35 IST).
# Holds the daily run's lock throughout, backs live up, rebuilds the master from that backup,
# checks it, and only then replaces data/vcp_scanner.duckdb (atomic rename). Rollback: copy the
# pre-swap backup back over data/vcp_scanner.duckdb.
set -euo pipefail
cd "$(dirname "$0")/../.."
STAMP=$(date +%Y%m%d_%H%M%S)
BK="data/backups_manual/vcp_scanner_preswap_${STAMP}.duckdb"
CHECK="data/logs/master_swap_check_${STAMP}.out"
REUSE="${1:-}"  # a pre-swap backup the current master was already built from
exec 9>data/logs/daily_run.lock
flock -n 9 || { echo "a daily run is in progress; nothing done"; exit 1; }
echo "$(date -u +%FT%TZ) lock held"
if [ -n "$REUSE" ]; then
  # live must be byte for byte the backup the master was built from
  cmp -s data/vcp_scanner.duckdb "$REUSE" || { echo "STOP: live changed since $REUSE"; exit 2; }
  [ ! -e data/vcp_scanner.duckdb.wal ] || { echo "STOP: live has a write-ahead log"; exit 2; }
  BK="$REUSE"; echo "reusing the master built from $BK (live identical to it)"
else
.venv/bin/python - "$BK" <<'PY'
import shutil, sys, duckdb
from pathlib import Path
from vcp_scanner.backup import check_database
src, dst = Path("data/vcp_scanner.duckdb"), Path(sys.argv[1])
err = check_database(src, checkpoint=True)
if err: sys.exit(f"live check failed: {err}")
if Path(str(src) + ".wal").exists(): sys.exit("live has a write-ahead log after checkpoint")
part = dst.with_suffix(".partial"); shutil.copy2(src, part)
q = ("SELECT (SELECT count(*) FROM daily_prices), (SELECT count(*) FROM paper_events),"
     " (SELECT count(*) FROM scan_runs), (SELECT count(*) FROM corporate_actions)")
a = duckdb.connect(str(src), read_only=True).execute(q).fetchone()
b = duckdb.connect(str(part), read_only=True).execute(q).fetchone()
if a != b: sys.exit(f"backup differs: {a} vs {b}")
part.rename(dst); print("backup", dst, a)
PY
.venv/bin/python -u research/master/build_master.py --source "$BK"
fi
.venv/bin/python -u research/master/check_master.py --skip rescan --backup "$BK" > "$CHECK" 2>&1
grep -E '^(prices|quality|health|paper)' "$CHECK"
grep -q 'adjusted bars from 2021 in both; 0 differ (0 stocks); 0 live bars missing' "$CHECK" || { echo "STOP: prices differ"; exit 2; }
grep -qE '^health: [0-9]+ live days compared, 0 changed' "$CHECK" || { echo "STOP: market health changed"; exit 2; }
grep -qE '^quality: .* new, 0 gone vs live' "$CHECK" || { echo "STOP: quality events removed"; exit 2; }
grep -q 'DIVERGENCE' "$CHECK" && { echo "STOP: paper divergence"; exit 2; }
grep -q '^paper: exit 0' "$CHECK" || { echo "STOP: paper update failed"; exit 2; }
.venv/bin/python - <<'PY'
from vcp_scanner.backup import check_database
err = check_database("data/master_build/vcp_master.duckdb", checkpoint=True)
if err: raise SystemExit(f"master check failed: {err}")
PY
cp data/master_build/vcp_master.duckdb data/vcp_scanner.duckdb.new
mv -f data/vcp_scanner.duckdb.new data/vcp_scanner.duckdb
echo "$(date -u +%FT%TZ) SWAPPED: data/vcp_scanner.duckdb is the master (rollback: $BK)"
.venv/bin/python -c "from vcp_scanner.serving import refresh_serving_copy as r; print('serving', r('data/vcp_scanner.duckdb'))"
echo "$(date -u +%FT%TZ) done; lock released"
