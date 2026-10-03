"""Backtest summary numbers (PROJECT_DESIGN 38, 41; Phase 9 step 3)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import date

from vcp_scanner.backtest.engine import Trade


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2


def trade_stats(trades: Sequence[Trade]) -> dict[str, float | int | None]:
    rets = [t.ret_pct for t in trades]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    gross_loss = -sum(losses)
    holds = [(t.exit_day - t.entry_day).days for t in trades]
    return {
        "trades": len(rets),
        "win_rate": len(wins) / len(rets) if rets else None,
        "avg_ret": sum(rets) / len(rets) if rets else None,
        "median_ret": _median(rets),
        "avg_win": sum(wins) / len(wins) if wins else None,
        "avg_loss": sum(losses) / len(losses) if losses else None,
        "profit_factor": sum(wins) / gross_loss if gross_loss > 0 else None,
        "avg_hold_days": sum(holds) / len(holds) if holds else None,
    }


def equity_stats(curve: Sequence[tuple[date, float]]) -> dict[str, float | None]:
    if len(curve) < 2:
        return {"total_ret": None, "cagr": None, "max_drawdown": None, "sharpe": None}
    start, end = curve[0][1], curve[-1][1]
    years = (curve[-1][0] - curve[0][0]).days / 365.25
    peak, mdd = curve[0][1], 0.0
    for _, v in curve:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    daily = [b[1] / a[1] - 1 for a, b in zip(curve, curve[1:], strict=False) if a[1] > 0]
    mean = sum(daily) / len(daily)
    sd = math.sqrt(sum((x - mean) ** 2 for x in daily) / max(1, len(daily) - 1))
    return {
        "total_ret": (end / start - 1) * 100,
        "cagr": ((end / start) ** (1 / years) - 1) * 100 if years > 0 and end > 0 else None,
        "max_drawdown": mdd * 100,
        "sharpe": mean / sd * math.sqrt(252) if sd > 0 else None,
    }
