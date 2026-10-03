"""Look-ahead check (PROJECT_DESIGN 40; Phase 9 step 5): rebuild a stored scan from a copy of
the database in which everything after the as-of date has been removed, and compare.

Acceptance (Phase 9): "historical scans use only information available at the time". If the
rebuilt Trend Template, VCP and score results equal the stored ones, nothing after the as-of
date influenced them.

Variants:

* ``prices``: delete prices after the as-of date (raw, adjusted, bhavcopy files) and recompute
  every feature from scratch. Adjustments for corporate actions stay as stored. Any difference
  is a look-ahead bug.
* ``corporate-actions``: also delete corporate actions with an ex-date after the as-of date and
  rebuild the adjusted prices from scratch. Differences measure corporate-action leakage: today's
  adjusted prices include splits and bonuses that happened later. Historical corporate actions
  were collected in 2026, so their knowledge timestamps cannot be used; the ex-date is the
  availability proxy.

The work happens on a temporary copy; the source database is only read.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from vcp_scanner.data.storage.duckdb_store import DuckDBStore

VARIANTS = ("prices", "corporate-actions")


@dataclass(frozen=True, slots=True)
class Diff:
    name: str
    compared: int
    differing: int
    examples: list[str]


def _rows(conn: Any, sql: str, params: list[Any]) -> dict[str, tuple[Any, ...]]:
    out = {}
    for r in conn.execute(sql, params).fetchall():
        out[str(r[0])] = tuple(round(x, 6) if isinstance(x, float) else x for x in r[1:])
    return out


def compare(src: Any, work: Any, as_of: date, scan_hash12: str) -> list[Diff]:
    """Compare the as-of scan between two DuckDB connections."""
    d = as_of.isoformat()
    checks = [
        ("trend_template",
         "SELECT instrument_id, status, trend_template_pass, weekly_stage, rs_rank"
         " FROM trend_template_results WHERE scan_id = ?", f"trend-{d}-{scan_hash12}"),
        ("vcp",
         "SELECT instrument_id, classification, status, pivot_price, contraction_count"
         " FROM vcp_patterns WHERE scan_id = ? AND is_primary", f"vcp-{d}-{scan_hash12}"),
        ("scores",
         "SELECT instrument_id, eligible, final_setup_score, ranking_percentile"
         " FROM setup_scores WHERE scan_id = ?", f"score-{d}-{scan_hash12}"),
    ]  # fmt: skip
    out = []
    for name, sql, scan_id in checks:
        a, b = _rows(src, sql, [scan_id]), _rows(work, sql, [scan_id])
        keys = sorted(set(a) | set(b))
        bad = [k for k in keys if a.get(k) != b.get(k)]
        out.append(Diff(name, len(keys), len(bad),
                        [f"{k}: {a.get(k)} -> {b.get(k)}" for k in bad[:5]]))  # fmt: skip
    return out


def truncate(store: DuckDBStore, as_of: date, variant: str) -> None:
    """Remove what was not known on ``as_of`` (module docstring)."""
    q = store.conn.execute
    q("DELETE FROM daily_prices WHERE trade_date > ?", [as_of])
    q("DELETE FROM bhavcopy_files WHERE trade_date > ?", [as_of])
    q("DELETE FROM daily_prices_adjusted WHERE trade_date > ?", [as_of])
    q("DELETE FROM technical_features_daily")
    q("DELETE FROM weekly_prices")
    if variant == "corporate-actions":
        q("DELETE FROM corporate_actions WHERE ex_date > ?", [as_of])
        q("DELETE FROM daily_prices_adjusted")  # rebuilt with only the remaining actions


def run_check(
    src_db: Path, as_of: date, variant: str, work_dir: Path, config_dir: str,
    scan_hash12: str, cli: Callable[[list[str]], int],
) -> list[Diff]:  # fmt: skip
    """Copy, truncate, rebuild the as-of scan with ``cli`` (the ``vcp`` entry point), compare."""
    if variant not in VARIANTS:
        raise ValueError(f"variant must be one of {VARIANTS}")
    work_dir.mkdir(parents=True, exist_ok=True)
    work = work_dir / f"lookahead_{as_of:%Y%m%d}_{variant}.duckdb"
    shutil.copyfile(src_db, work)
    try:
        with DuckDBStore(work) as store:
            truncate(store, as_of, variant)
        db = ["--db", str(work)]
        cfg = ["--config-dir", config_dir]
        steps: list[list[str]] = []
        if variant == "corporate-actions":
            steps.append(["ingest", "adjusted-prices", *db])
        steps += [
            ["compute", "features", *db],
            ["ingest", "universe", "--as-of", as_of.isoformat(), *db, *cfg],
            ["compute", "rs", "--as-of", as_of.isoformat(), *db, *cfg],
            ["compute", "trend-template", "--as-of", as_of.isoformat(), *db, *cfg],
            ["compute", "vcp", "--as-of", as_of.isoformat(), *db, *cfg],
            ["compute", "scores", "--as-of", as_of.isoformat(), *db, *cfg],
        ]
        for argv in steps:
            if cli(argv) != 0:
                raise RuntimeError(f"step failed: vcp {' '.join(argv[:2])}")
        import duckdb

        src = duckdb.connect(str(src_db), read_only=True)
        w = duckdb.connect(str(work), read_only=True)
        try:
            return compare(src, w, as_of, scan_hash12)
        finally:
            src.close()
            w.close()
    finally:
        work.unlink(missing_ok=True)
        Path(str(work) + ".wal").unlink(missing_ok=True)
