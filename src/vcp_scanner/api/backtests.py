"""Stored backtest runs (FRONTEND_SPECIFICATION 67.26): read-only, from the research database.

The runs live in the research database (``vcp backtest run`` stores them there), not in the serving
copy. The file is opened read-only for each request. Nothing is run or recomputed here; a value the
run did not store is ``null``, never 0.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb

from vcp_scanner.api import models as m
from vcp_scanner.data.providers._time import IST

#: The frozen paper rules (config/backtest.yaml defaults, STRATEGY_SPECIFICATION 21.3).
PAPER = {"entry": "cross_5", "regime": "breadth50", "rule": "hold_low8", "max_positions": 10}
PAPER_COST_BPS = 15.0


def _f(v: Any) -> float | None:
    return None if v is None else float(v)


def _pct(v: Any) -> float | None:
    return None if v is None else float(v) * 100.0


def _i(v: Any) -> int | None:
    return None if v is None else int(v)


def _stats(d: dict[str, Any]) -> m.BacktestStats:
    return m.BacktestStats(
        trades=_i(d.get("trades")), win_rate_pct=_pct(d.get("win_rate")),
        avg_return_pct=_f(d.get("avg_ret")), median_return_pct=_f(d.get("median_ret")),
        profit_factor=_f(d.get("profit_factor")), avg_win_pct=_f(d.get("avg_win")),
        avg_loss_pct=_f(d.get("avg_loss")), avg_hold_days=_f(d.get("avg_hold_days")),
    )  # fmt: skip


def _portfolio(d: dict[str, Any]) -> m.BacktestPortfolio:
    return m.BacktestPortfolio(
        **_stats(d).model_dump(), total_return_pct=_f(d.get("total_ret")),
        cagr_pct=_f(d.get("cagr")), max_drawdown_pct=_f(d.get("max_drawdown")),
        sharpe=_f(d.get("sharpe")), avg_exposure_pct=_pct(d.get("avg_exposure")),
        skipped=_i(d.get("skipped")),
    )  # fmt: skip


def _run(row: tuple[Any, ...]) -> m.BacktestRun:
    (bid, sid, algo, period, start, end, surv, settings_json, metrics_json, done) = row
    s: dict[str, Any] = json.loads(settings_json or "{}")
    metrics: dict[str, Any] = json.loads(metrics_json or "{}")
    cost = _f(s.get("cost_bps"))
    classes = s.get("classes")
    is_paper = (
        all(s.get(k) == v for k, v in PAPER.items())
        and cost == PAPER_COST_BPS
        and not s.get("baseline")
    )
    return m.BacktestRun(
        backtest_id=str(bid), strategy_id=str(sid), algorithm_version=algo, period=period,
        start_date=start, end_date=end, survivorship=surv, entry=s.get("entry"),
        regime=s.get("regime"), rule=s.get("rule"), cost_bps=cost,
        max_positions=_i(s.get("max_positions")),
        classes=[str(c) for c in classes] if isinstance(classes, list) else [],
        min_score=_f(s.get("min_score")), baseline=bool(s.get("baseline")),
        paper_rules=is_paper, completed_at=done.astimezone(IST) if done else None,
        every_trade=_stats(metrics.get("every_trade") or {}),
        portfolio=_portfolio(metrics.get("portfolio") or {}),
    )  # fmt: skip


def stored_runs(path: Path | None) -> m.BacktestsResponse:
    """Every completed run, newest first; ``available`` is false (with the reason) when the
    research database is missing or cannot be read right now."""
    now = datetime.now(IST)

    def gone(reason: str) -> m.BacktestsResponse:
        return m.BacktestsResponse(
            as_of=None, data_time=now, available=False, reason=reason, runs=[]
        )

    if path is None or not path.exists():
        return gone(
            f"The research database was not found ({path}). Start the API with --research-db."
        )
    try:
        con = duckdb.connect(str(path), read_only=True)
    except duckdb.Error as exc:
        return gone(f"The research database cannot be opened right now: {exc}")
    try:
        rows = con.execute(
            "SELECT backtest_id, strategy_id, algorithm_version, period_name, start_date, end_date,"
            " survivorship_status, settings_json, metrics_json, completed_at FROM backtest_runs"
            " WHERE status = 'COMPLETED' ORDER BY started_at DESC"
        ).fetchall()
    except duckdb.Error as exc:
        return gone(f"The research database has no stored backtest runs: {exc}")
    finally:
        con.close()
    runs = [_run(r) for r in rows]
    stamp = datetime.fromtimestamp(path.stat().st_mtime, IST)
    return m.BacktestsResponse(
        as_of=max((r.end_date for r in runs), default=None), data_time=stamp, available=True,
        reason=None, runs=runs,
    )  # fmt: skip
