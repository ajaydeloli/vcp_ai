"""``run_study`` (AGENT_SPECIFICATION §3 tier 1, step A2): forward outcomes of a strategy's stored
setups, grouped one way, over a lab window. Read-only: it reads the research database's labels
(``forward_labels``), setups and scores, and the regime is computed the way the backtester
computes it. Nothing is re-detected or re-scored here.

The labels are those of each strategy's frozen config (the hash the live labels carry), so an
earlier config's labels are never mixed in. ``one_per_stock_month`` keeps each stock's first
setup of a month, so a long base seen on twenty scan days counts once.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb

HORIZONS = (5, 10, 20, 40, 60)
GROUPS = ("classification", "grade", "status", "score_band", "regime", "pivot_band", "year",
          "none")  # fmt: skip
SMALL_N = 30
MAX_ROWS = 200_000


@dataclass(slots=True)
class LabelRow:
    instrument_id: str
    day: date
    rets: dict[int, float | None]
    mfe: float | None
    mae: float | None
    breakout: bool | None
    failed: bool | None
    classification: str
    grade: int
    status: str
    pivot_distance: float | None
    score: float | None
    regime_on: bool | None = None
    keys: dict[str, Any] = field(default_factory=dict)


def _band(x: float | None, edges: tuple[float, ...], names: tuple[str, ...]) -> str:
    if x is None:
        return "unknown"
    for edge, name in zip(edges, names, strict=False):
        if x < edge:
            return name
    return names[-1]


KEYS: dict[str, Callable[[LabelRow], str]] = {
    "classification": lambda r: r.classification,
    "grade": lambda r: f"grade {r.grade}",
    "status": lambda r: r.status,
    "score_band": lambda r: _band(
        r.score, (50, 60, 70, 80), ("<50", "50-60", "60-70", "70-80", ">=80")
    ),  # fmt: skip
    "regime": lambda r: (
        "unknown" if r.regime_on is None else "regime on" if r.regime_on else "regime off"
    ),  # fmt: skip
    "pivot_band": lambda r: _band(
        r.pivot_distance,
        (0, 2, 5, 10),
        ("above pivot", "0-2% below", "2-5% below", "5-10% below", ">=10% below"),
    ),  # fmt: skip
    "year": lambda r: str(r.day.year),
    "none": lambda r: "all",
}


def frozen_hash(serving: Path, strategy: str) -> str | None:
    """The config hash the live labels of ``strategy`` carry (its frozen version)."""
    con = duckdb.connect(str(serving), read_only=True)
    try:
        row = con.execute(
            "SELECT config_hash FROM forward_labels WHERE strategy_id = ?"
            " GROUP BY 1 ORDER BY count(*) DESC LIMIT 1",
            [strategy],
        ).fetchone()
    finally:
        con.close()
    return str(row[0]) if row else None


_SETUP_COLS = "strategy_id, instrument_id, as_of_date, config_hash, classification, grade," \
              " status, pivot_distance_pct, created_at"  # fmt: skip


def load_rows(research: Any, strategy: str, config_hash: str, start: date,
              end: date) -> list[LabelRow]:  # fmt: skip
    rets = ", ".join(f"f.ret_{h}" for h in HORIZONS)
    sql = f"""
        WITH p AS (
            SELECT * FROM (
                SELECT {_SETUP_COLS} FROM setups
                WHERE is_primary AND data_snapshot_id = 'LIVE' AND strategy_id = ?
                UNION ALL
                SELECT {_SETUP_COLS} FROM strategy_setups
                WHERE is_primary AND data_snapshot_id = 'LIVE' AND strategy_id = ?
            ) QUALIFY row_number() OVER (PARTITION BY strategy_id, instrument_id, as_of_date,
                                         config_hash ORDER BY created_at DESC) = 1
        ), s AS (
            SELECT strategy_id, instrument_id, as_of_date, config_hash, final_setup_score
            FROM setup_scores WHERE data_snapshot_id = 'LIVE' AND strategy_id = ?
            QUALIFY row_number() OVER (PARTITION BY strategy_id, instrument_id, as_of_date,
                                       config_hash ORDER BY created_at DESC) = 1
        )
        SELECT f.instrument_id, f.as_of_date, {rets}, f.mfe_60, f.mae_60,
               f.breakout_within_20, f.failed_breakout, p.classification, p.grade, p.status,
               p.pivot_distance_pct, s.final_setup_score
        FROM forward_labels f
        JOIN p ON p.strategy_id = f.strategy_id AND p.instrument_id = f.instrument_id
              AND p.as_of_date = f.as_of_date AND p.config_hash = f.config_hash
        LEFT JOIN s ON s.strategy_id = f.strategy_id AND s.instrument_id = f.instrument_id
              AND s.as_of_date = f.as_of_date AND s.config_hash = f.config_hash
        WHERE f.strategy_id = ? AND f.config_hash = ? AND f.as_of_date BETWEEN ? AND ?
        ORDER BY f.as_of_date, f.instrument_id
        LIMIT {MAX_ROWS}
    """
    rows = research.execute(
        sql, [strategy, strategy, strategy, strategy, config_hash, start, end]
    ).fetchall()
    n = len(HORIZONS)

    def f(x: Any) -> float | None:
        return None if x is None else float(x)

    return [
        LabelRow(
            instrument_id=str(r[0]), day=r[1],
            rets={h: f(r[2 + k]) for k, h in enumerate(HORIZONS)},
            mfe=f(r[2 + n]), mae=f(r[3 + n]),
            breakout=None if r[4 + n] is None else bool(r[4 + n]),
            failed=None if r[5 + n] is None else bool(r[5 + n]),
            classification=str(r[6 + n]), grade=int(r[7 + n] or 0), status=str(r[8 + n]),
            pivot_distance=f(r[9 + n]), score=f(r[10 + n]),
        )
        for r in rows
    ]  # fmt: skip


def attach_regime(research: Any, rows: list[LabelRow], start: date, end: date,
                  scan_hash: str, features_version: str) -> None:  # fmt: skip
    """breadth50, as the backtester and the frozen paper rules compute it (STRATEGY_SPEC 20.3)."""
    from vcp_scanner.backtest.regime import breadth_regime
    from vcp_scanner.data.repositories.duckdb_backtest_repository import (
        DuckDBBacktestRepository,
    )

    repo = DuckDBBacktestRepository(SimpleNamespace(conn=research), "LIVE")  # type: ignore[arg-type]
    regime = breadth_regime(repo.breadth(start, end, scan_hash, features_version))
    for r in rows:
        r.regime_on = regime.get(r.day)


def one_per_stock_month(rows: list[LabelRow]) -> list[LabelRow]:
    seen: set[tuple[str, int, int]] = set()
    out = []
    for r in sorted(rows, key=lambda r: (r.day, r.instrument_id)):
        k = (r.instrument_id, r.day.year, r.day.month)
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def _r(x: float | None, d: int = 2) -> float | None:
    return None if x is None else round(x, d)


def summarize(rows: list[LabelRow], horizon: int) -> dict[str, Any]:
    rets = [r.rets[horizon] for r in rows if r.rets.get(horizon) is not None]
    rets_f = [float(x) for x in rets if x is not None]
    n = len(rets_f)
    mean = statistics.fmean(rets_f) if rets_f else None
    se = statistics.stdev(rets_f) / n**0.5 if n > 1 else None
    breakouts = [r for r in rows if r.breakout]
    failed = [r for r in breakouts if r.failed is not None]
    mfe = [r.mfe for r in rows if r.mfe is not None]
    mae = [r.mae for r in rows if r.mae is not None]
    return {
        "setups": len(rows), "n": n, "mean_ret_pct": _r(mean),
        "median_ret_pct": _r(statistics.median(rets_f)) if rets_f else None,
        "std_err_pct": _r(se), "win_rate": _r(sum(x > 0 for x in rets_f) / n, 3) if n else None,
        "mean_mfe_60_pct": _r(statistics.fmean(mfe)) if mfe else None,
        "mean_mae_60_pct": _r(statistics.fmean(mae)) if mae else None,
        "breakout_within_20_rate": _r(len(breakouts) / len(rows), 3) if rows else None,
        "failed_breakout_rate": _r(sum(bool(r.failed) for r in failed) / len(failed), 3)
        if failed else None,
        "small_sample": n < SMALL_N,
    }  # fmt: skip


@dataclass(frozen=True, slots=True)
class StudyFilters:
    classes: tuple[str, ...] = ()
    statuses: tuple[str, ...] = ()
    min_score: float | None = None
    regime: str = "any"  # any | on | off


def apply_filters(rows: list[LabelRow], flt: StudyFilters) -> list[LabelRow]:
    out = rows
    if flt.classes:
        out = [r for r in out if r.classification in flt.classes]
    if flt.statuses:
        out = [r for r in out if r.status in flt.statuses]
    if flt.min_score is not None:
        out = [r for r in out if r.score is not None and r.score >= flt.min_score]
    if flt.regime in ("on", "off"):
        want = flt.regime == "on"
        out = [r for r in out if r.regime_on is want]
    return out


def study(rows: list[LabelRow], group_by: str, horizon: int) -> dict[str, Any]:
    if group_by not in KEYS:
        raise ValueError(f"group_by must be one of {', '.join(GROUPS)}")
    if horizon not in HORIZONS:
        raise ValueError(f"horizon must be one of {', '.join(map(str, HORIZONS))}")
    by: dict[str, list[LabelRow]] = defaultdict(list)
    for r in rows:
        by[KEYS[group_by](r)].append(r)
    groups = [{"group": k, **summarize(v, horizon)} for k, v in sorted(by.items())]
    return {"overall": summarize(rows, horizon), "groups": groups}
