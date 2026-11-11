"""Daily VCP v2 scans of the research copy (owner go 2026-11-10: build v2).

Every NSE session in the range, oldest first. Resumable: a day already scanned with the current
v2 configuration is skipped. Writes only v2's own tables (``research/vcp2_scan.py``); the weekly
v1 scans in the same file are never touched. Never part of the nightly run.

    .venv/bin/python research/vcp2/run_scans.py 2012-07-02 2026-09-30
"""

from __future__ import annotations

import argparse
import time
from datetime import UTC, date, datetime
from pathlib import Path

from vcp_scanner.config import load_scanner_config
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.patterns.vcp2.config import load_vcp2_config, vcp2_config_hash
from vcp_scanner.research.vcp2_scan import ensure_tables, scan_day
from vcp_scanner.versioning import code_state

ROOT = Path(__file__).resolve().parents[2]


def log(path: Path, text: str) -> None:
    line = f"{datetime.now(UTC):%Y-%m-%dT%H:%M:%SZ} {text}"
    print(line, flush=True)
    with path.open("a") as f:
        f.write(line + "\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("first")
    ap.add_argument("last")
    ap.add_argument("--db", default=str(ROOT / "data" / "research" / "database_research.duckdb"))
    ap.add_argument("--log", default=str(ROOT / "data" / "logs" / "vcp2_scans.log"))
    a = ap.parse_args()
    first, last = date.fromisoformat(a.first), date.fromisoformat(a.last)
    logp = Path(a.log)
    cfg = load_scanner_config(ROOT / "config")
    v2 = load_vcp2_config(ROOT / "config")
    chash = vcp2_config_hash(v2)
    commit, dirty = code_state()
    code = f"{commit}{' (dirty)' if dirty else ''}" if commit else None
    with DuckDBStore(a.db) as store:
        ensure_tables(store)
        days = [r[0] for r in store.conn.execute(
            "SELECT trade_date FROM bhavcopy_files WHERE status = 'OK' AND row_count > 0"
            " AND trade_date BETWEEN ? AND ? ORDER BY 1", [first, last]).fetchall()]  # fmt: skip
        done = {r[0] for r in store.conn.execute(
            "SELECT scan_date FROM vcp2_runs WHERE config_hash = ?", [chash]).fetchall()}
        todo = [d for d in days if d not in done]
        log(logp, f"vcp2 {chash[:12]} {first}..{last}: {len(days)} sessions, "
                  f"done {len(done)}, to do {len(todo)}")  # fmt: skip
        t0 = time.monotonic()
        for k, d in enumerate(todo, 1):
            try:
                s = scan_day(store, d, cfg, v2, code)
            except Exception as exc:  # one bad day must not stop a 14-year run
                log(logp, f"FAILED {d}: {type(exc).__name__}: {exc}")
                continue
            if k % 20 == 0 or k == len(todo):
                left = (time.monotonic() - t0) / k * (len(todo) - k) / 3600
                log(logp, f"{d} universe {s['universe']} base {s['with_base']} candidates "
                          f"{s['candidates']} in {s['seconds']}s ({k}/{len(todo)}; "
                          f"about {left:.1f} h left)")  # fmt: skip
    log(logp, "finished")


if __name__ == "__main__":
    main()
