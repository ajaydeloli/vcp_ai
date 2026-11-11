"""VCP v2 scan of one day: universe, detector, trend measurements, stored in v2's own tables.

Research only (owner go 2026-11-10). Never part of the nightly run. It lives here, not under
``patterns/``, because it reads the database: strategy packages stay pure (architecture rules).

**Writes only ``vcp2_runs`` and ``vcp2_candidates``.** Those tables are created here, not in the
live schema. The day's universe, RS rank and Trend Template are computed in memory with v1's
own pure functions (``UniverseBuilder.build_snapshot`` without saving, ``compute_rs_rows``,
``evaluate_conditions``). That keeps the weekly v1 scans of a research copy untouched: v1's
backtest regime reads its own universe and Trend Template tables, which v2 must not add daily
rows to.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from typing import Any

from vcp_scanner.config.models import ScannerConfig
from vcp_scanner.data.repositories.duckdb_quality_repository import DuckDBDataQualityRepository
from vcp_scanner.data.repositories.duckdb_universe_repository import DuckDBUniverseRepository
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.data.universe.builder import UniverseBuilder
from vcp_scanner.domain.enums import TrendTemplateStatus
from vcp_scanner.domain.snapshot import LIVE_SNAPSHOT_ID
from vcp_scanner.domain.trend import RSPriceInput
from vcp_scanner.features.relative_strength import compute_rs_rows
from vcp_scanner.features.trend_template import (
    SESSIONS_52W,
    TrendInputs,
    derive_status,
    evaluate_conditions,
)
from vcp_scanner.patterns.vcp2.config import VCP2_VERSION, VCP2Config, vcp2_config_hash
from vcp_scanner.patterns.vcp2.detector import Bars, Candidate, detect

logger = logging.getLogger(__name__)
LOOKBACK_DAYS = 900  # calendar days of bars loaded: enough for the 65-week base, its run-up and
# the 252-session Trend Template window

DDL = (
    """CREATE TABLE IF NOT EXISTS vcp2_runs (
        run_id VARCHAR PRIMARY KEY, scan_date DATE NOT NULL, config_hash VARCHAR NOT NULL,
        version VARCHAR NOT NULL, universe INTEGER, with_base INTEGER, candidates INTEGER,
        no_base_json VARCHAR, seconds DOUBLE, code_commit VARCHAR,
        started_at TIMESTAMP WITH TIME ZONE, completed_at TIMESTAMP WITH TIME ZONE)""",
    """CREATE TABLE IF NOT EXISTS vcp2_candidates (
        scan_date DATE NOT NULL, instrument_id VARCHAR NOT NULL, config_hash VARCHAR NOT NULL,
        version VARCHAR NOT NULL, is_candidate BOOLEAN NOT NULL, unmet VARCHAR,
        base_start_date DATE, base_high DOUBLE, base_low DOUBLE, base_low_date DATE,
        base_bars INTEGER, base_depth_pct DOUBLE, window_bars INTEGER, window_extensions INTEGER,
        prior_advance_pct DOUBLE, prior_advance_long_pct DOUBLE, close_in_base_pct DOUBLE,
        contraction_count INTEGER, tightening_share DOUBLE, final_depth_pct DOUBLE,
        final_bars INTEGER, final_ended BOOLEAN, undercuts INTEGER, deepest_undercut_pct DOUBLE,
        undercut_recovery_bars INTEGER, shakeouts INTEGER,
        pivot_price DOUBLE, pivot_alt DOUBLE, distance_to_pivot_pct DOUBLE,
        right_side_range_pct DOUBLE, atr_ratio DOUBLE, days_since_final_low INTEGER,
        final_volume_ratio DOUBLE, up_down_volume DOUBLE, last5_volume_ratio DOUBLE,
        close DOUBLE, above_sma50_pct DOUBLE, above_sma150_pct DOUBLE, above_sma200_pct DOUBLE,
        sma50_over_sma200_pct DOUBLE, sma200_slope_pct DOUBLE, rs_rank INTEGER,
        trend_template_pass BOOLEAN, trend_template_failed VARCHAR,
        contractions_json VARCHAR,
        PRIMARY KEY (config_hash, scan_date, instrument_id))""",
    # added 2026-11-11 (owner's above-pivot rule); NULL on rows scanned before it existed
    "ALTER TABLE vcp2_candidates ADD COLUMN IF NOT EXISTS max_above_pivot_pct DOUBLE",
)

_SKIP = ("as_of", "contractions", "unmet", "is_candidate")
_SCALARS = [f for f in Candidate.__dataclass_fields__ if f not in _SKIP]
_COLUMN = {"pivot": "pivot_price"}  # PIVOT is a DuckDB keyword


@dataclass(frozen=True, slots=True)
class Trend:
    close: float
    above_sma50_pct: float | None
    above_sma150_pct: float | None
    above_sma200_pct: float | None
    sma50_over_sma200_pct: float | None
    sma200_slope_pct: float | None
    rs_rank: int | None
    trend_template_pass: bool | None
    trend_template_failed: str | None


def _sma(closes: list[float], end: int, n: int) -> float | None:
    """Simple average of the ``n`` closes ending at index ``end`` (inclusive)."""
    if end + 1 < n:
        return None
    return sum(closes[end - n + 1 : end + 1]) / n


def _pct(a: float | None, b: float | None) -> float | None:
    return (a / b - 1) * 100.0 if a is not None and b else None


def ensure_tables(store: DuckDBStore) -> None:
    for ddl in DDL:
        store.conn.execute(ddl)


def scan_day(
    store: DuckDBStore, day: date, cfg: ScannerConfig, v2: VCP2Config,
    code_commit: str | None = None,
) -> dict[str, object]:  # fmt: skip
    """Scan one session and store its v2 rows (replacing any earlier rows of the same day and
    configuration). Returns a short summary."""
    started = datetime.now(UTC)
    t0 = time.monotonic()
    ensure_tables(store)
    chash = vcp2_config_hash(v2)
    builder = UniverseBuilder(
        DuckDBUniverseRepository(store), cfg.universe,
        quality_gate=DuckDBDataQualityRepository(
            store, block_lifetime_bars=cfg.data.quality.block_lifetime_bars),
        gate_settings=cfg.data.quality,
    )  # fmt: skip
    _snapshot, members = builder.build_snapshot(as_of_date=day, known_at=started)
    ids = sorted(m.instrument_id for m in members if m.eligible)
    rows = store.conn.execute(
        """SELECT instrument_id, trade_date, high_adj, low_adj, close_adj, volume_adj
           FROM daily_prices_adjusted_current
           WHERE instrument_id IN (SELECT unnest(?)) AND computed_from_snapshot_id = ?
             AND trade_date <= ? AND trade_date > ? - INTERVAL 900 DAY
           ORDER BY instrument_id, trade_date""",
        [ids, LIVE_SNAPSHOT_ID, day, day]).fetchall()  # fmt: skip
    series: dict[str, Bars] = {}
    cols: dict[str, list[tuple[Any, ...]]] = {}
    for r in rows:
        cols.setdefault(r[0], []).append(r[1:])
    for iid, bars in cols.items():
        if bars and bars[-1][0] == day:
            series[iid] = Bars([b[0] for b in bars], [b[1] for b in bars], [b[2] for b in bars],
                               [b[3] for b in bars], [b[4] for b in bars])  # fmt: skip
    # RS rank among the day's universe, v1's own formula (rs-1.1.0)
    rs_cfg = cfg.strategy.rs
    rs_in = []
    for iid, b in series.items():
        n = len(b.close)
        lag = tuple(b.close[n - 1 - w] if n - 1 - w >= 0 else None for w in rs_cfg.windows_days)
        rs_in.append(RSPriceInput(iid, b.dates[-1], b.close[-1], lag))
    rs = {r.instrument_id: r.rs_rank for r in compute_rs_rows(rs_in, day, rs_cfg)}
    tt_cfg = cfg.strategy.trend_template
    look = tt_cfg.sma200_slope_lookback_days
    out = []
    no_base: dict[str, int] = {}
    for iid, b in series.items():
        det = detect(b, v2)
        if det.candidate is None:
            no_base[det.reason or "?"] = no_base.get(det.reason or "?", 0) + 1
            continue
        c = det.candidate
        closes = list(b.close)
        e = len(closes) - 1
        s50, s150, s200 = _sma(closes, e, 50), _sma(closes, e, 150), _sma(closes, e, 200)
        s200p = _sma(closes, e - look, 200) if e - look >= 0 else None
        hi52 = max(b.high[-SESSIONS_52W:]) if len(b.high) >= SESSIONS_52W else None
        lo52 = min(b.low[-SESSIONS_52W:]) if len(b.low) >= SESSIONS_52W else None
        conds = evaluate_conditions(TrendInputs(closes[e], s50, s150, s200, s200p, hi52, lo52,
                                                rs.get(iid)), tt_cfg)  # fmt: skip
        status = derive_status(conds)
        if len(closes) < SESSIONS_52W + look:  # v1's warm-up guard: no verdict before 273 bars
            status = TrendTemplateStatus.INSUFFICIENT_DATA
        failed = [x.name for x in conds if x.passed is False]
        tr = Trend(
            close=closes[e], above_sma50_pct=_pct(closes[e], s50),
            above_sma150_pct=_pct(closes[e], s150), above_sma200_pct=_pct(closes[e], s200),
            sma50_over_sma200_pct=_pct(s50, s200), sma200_slope_pct=_pct(s200, s200p),
            rs_rank=rs.get(iid),
            trend_template_pass=(None if status is TrendTemplateStatus.INSUFFICIENT_DATA
                                 else status is TrendTemplateStatus.PASS),
            trend_template_failed=",".join(failed) or None,
        )  # fmt: skip
        out.append([day, iid, chash, VCP2_VERSION, c.is_candidate, ",".join(c.unmet) or None,
                    *[getattr(c, f) for f in _SCALARS], *asdict(tr).values(),
                    json.dumps([asdict(x) for x in c.contractions], default=str)])  # fmt: skip
    con = store.conn
    con.execute("BEGIN")
    con.execute("DELETE FROM vcp2_candidates WHERE config_hash = ? AND scan_date = ?",
                [chash, day])  # fmt: skip
    con.execute("DELETE FROM vcp2_runs WHERE config_hash = ? AND scan_date = ?", [chash, day])
    if out:
        names = ["scan_date", "instrument_id", "config_hash", "version", "is_candidate", "unmet",
                 *[_COLUMN.get(f, f) for f in _SCALARS], *Trend.__dataclass_fields__,
                 "contractions_json"]  # fmt: skip
        con.executemany(f"INSERT INTO vcp2_candidates ({', '.join(names)}) "
                        f"VALUES ({', '.join(['?'] * len(names))})", out)  # noqa: S608
    secs = time.monotonic() - t0
    n_cand = sum(1 for r in out if r[4])
    con.execute("INSERT INTO vcp2_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", [
        f"vcp2-{day.isoformat()}-{chash[:12]}", day, chash, VCP2_VERSION, len(series), len(out),
        n_cand, json.dumps(no_base), secs, code_commit, started, datetime.now(UTC)])  # fmt: skip
    con.execute("COMMIT")
    return {"day": day, "universe": len(series), "with_base": len(out), "candidates": n_cand,
            "no_base": no_base, "seconds": round(secs, 1)}  # fmt: skip
