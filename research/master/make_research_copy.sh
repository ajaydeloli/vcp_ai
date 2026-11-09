#!/usr/bin/env bash
# Step 5 (owner go 2026-11-10 00:46 IST): the research copy of the master database.
# Copies live under the daily run's lock, removes the copied daily live scans (research scans
# are weekly; a VCP scan reads earlier scans, so the two must never share a file), then runs
# the weekly scans 2012-07-02 .. 2026-09-30 and forward labels. Live is only read.
set -euo pipefail
cd "$(dirname "$0")/../.."
OUT=data/research/vcp_research.duckdb
LOG=data/logs/research_copy.log
mkdir -p data/research
if [ -e "$OUT" ]; then echo "STOP: $OUT exists; move it away first"; exit 2; fi
(
  exec 9>data/logs/daily_run.lock
  flock -n 9 || { echo "STOP: a daily run is in progress"; exit 2; }
  .venv/bin/python - "$OUT" <<'PY'
import shutil, sys, duckdb
from pathlib import Path
from vcp_scanner.backup import check_database
src, dst = Path("data/vcp_scanner.duckdb"), Path(sys.argv[1])
err = check_database(src, checkpoint=True)
if err: sys.exit(f"live check failed: {err}")
part = dst.with_suffix(".partial"); shutil.copy2(src, part)
q = "SELECT (SELECT count(*) FROM daily_prices), (SELECT count(*) FROM scan_runs)"
a = duckdb.connect(str(src), read_only=True).execute(q).fetchone()
b = duckdb.connect(str(part), read_only=True).execute(q).fetchone()
if a != b: sys.exit(f"copy differs: {a} vs {b}")
part.rename(dst); print("copied live ->", dst, a)
PY
)
echo "$(date -u +%FT%TZ) lock released"
.venv/bin/python - "$OUT" <<'PY'
import sys, duckdb
c = duckdb.connect(sys.argv[1])
views = {r[0] for r in c.execute("SELECT table_name FROM information_schema.tables WHERE table_type = 'VIEW'").fetchall()}
tables = ["vcp_contractions", "vcp_pivots", "vcp_scan_run_results", "vcp_status_history",
          "vcp_breakout_events", "vcp_patterns", "score_components", "setup_scores",
          "scan_run_results", "trend_template_conditions", "trend_template_results",
          "relative_strength_snapshots", "universe_memberships", "universe_snapshots",
          "strategy_scan_run_results", "strategy_setup_status_history", "strategy_breakout_events",
          "strategy_setups", "forward_labels", "scan_runs", "paper_events"]
c.execute("BEGIN")
for t in tables:
    if t in views: continue
    n = c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
    c.execute(f"DELETE FROM {t}")
    print(f"wiped {t}: {n}")
c.execute("COMMIT"); c.execute("CHECKPOINT"); c.close()
PY
.venv/bin/python -u research/history/backfill_scans.py 2012-07-02 2026-09-30 --steps sessions,after \
    --db "$OUT" --log "$LOG"
echo "$(date -u +%FT%TZ) research copy done"
