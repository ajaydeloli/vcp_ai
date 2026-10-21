"""What the alerts say (owner decision 2026-10-21): the evening summary after the daily run and
the intraday pivot alerts. ``get(path, **params)`` reads the dashboard API (in-process for the
evening message, the running API for the intraday watcher), so alerts show exactly what the
dashboard shows. Monitoring only: no alert is a trade signal or advice.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from vcp_scanner.alerts.telegram import EveningConfig, IntradayConfig

Get = Callable[..., Any]
NAMES = {"vcp": "VCP", "flat_base": "Flat base", "three_weeks_tight": "3 weeks tight",
         "cup_handle": "Cup & handle", "double_bottom": "Double bottom"}  # fmt: skip
FOOTER = "Research tool, not trading advice."


def _pct(x: Any, d: int = 1) -> str:
    return "-" if x is None else f"{float(x):.{d}f}%"


def _px(x: Any) -> str:
    return "-" if x is None else f"{float(x):,.2f}"


def evening_message(get: Get, cfg: EveningConfig) -> str:
    status = get("status")
    lines = [f"VCP scanner - evening {status.get('prices_date') or ''}".strip()]
    run = status.get("daily_run") or ""
    ok = "all steps OK" in run
    lines.append(("Run OK" if ok else "RUN PROBLEM") + (f": {run.split(' | ', 1)[-1]}" if run else
                                                        ": no summary"))  # fmt: skip
    for w in (status.get("warnings") or [])[:3]:
        lines.append(f"Warning: {w}")
    try:
        score = get("market/health").get("score") or {}
        if score.get("label"):
            lines.append(f"Market health: {score['label']} {score.get('raw', '')}"
                         f" (exposure {score.get('exposure', '-')})")  # fmt: skip
    except Exception:  # noqa: BLE001 - the summary must still go out
        lines.append("Market health: not available")
    day = status.get("prices_date")
    events = [e for e in get("activity", days=1).get("events", [])
              if e.get("kind") == "BREAKOUT" and (day is None or e.get("day") == day)]  # fmt: skip
    if events:
        lines.append("")
        lines.append(f"Breakouts ({len(events)}):")
        for e in events[:15]:
            sid = str(e.get("strategy_id") or "")
            lines.append(f"- {e.get('symbol')} [{NAMES.get(sid, sid)}] {e.get('text', '')}")
    near: list[tuple[float, str]] = []
    for s in cfg.strategies:
        try:
            rows = get("setups", strategy=s, limit=200).get("rows", [])
        except Exception:  # noqa: BLE001
            continue
        for r in rows:
            d = r.get("pivot_distance_pct")
            if d is not None and 0 <= d <= cfg.near_pivot_pct and r.get("status") != "BREAKOUT":
                near.append(
                    (
                        float(d),
                        f"- {r['symbol']} [{NAMES.get(s, s)}] {_pct(d)} below "
                        f"pivot {_px(r.get('pivot'))}, score {r.get('score') or 0:.0f}",
                    )
                )
    if near:
        near.sort()
        lines.append("")
        lines.append(f"Within {cfg.near_pivot_pct:g}% of the pivot ({len(near)}):")
        lines += [t for _, t in near[: cfg.max_setups]]
        if len(near) > cfg.max_setups:
            lines.append(f"... and {len(near) - cfg.max_setups} more on the dashboard")
    lines += ["", FOOTER]
    return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class Watched:
    symbol: str
    pivot: float
    strategy: str
    why: str  # "setup" | "AI watchlist" | "your list"


def watch_list(get: Get, cfg: IntradayConfig, ai_watchlist: Iterable[str]) -> list[Watched]:
    """Setups of the latest scan within ``near_pivot_pct`` below their pivot, plus the AI's and
    the owner's symbols with their best setup's pivot. One entry per symbol (nearest pivot)."""
    best: dict[str, Watched] = {}
    for s in NAMES:
        try:
            rows = get("setups", strategy=s, limit=500).get("rows", [])
        except Exception:  # noqa: BLE001
            continue
        for r in rows:
            d, pivot = r.get("pivot_distance_pct"), r.get("pivot")
            if pivot is None or d is None or not 0 <= d <= cfg.near_pivot_pct:
                continue
            if r["symbol"] not in best:
                best[r["symbol"]] = Watched(r["symbol"], float(pivot), s, "setup")
    extra = [(x.upper(), "your list") for x in cfg.symbols]
    if cfg.include_ai_watchlist:
        extra += [(x.upper(), "AI watchlist") for x in ai_watchlist]
    for sym, why in extra:
        if sym in best:
            continue
        try:
            setups = get(f"stocks/{sym}/setups").get("setups", [])
        except Exception:  # noqa: BLE001 - an unknown symbol is skipped
            continue
        with_pivot = [x for x in setups if x.get("pivot") is not None]
        if with_pivot:
            top = max(with_pivot, key=lambda x: x.get("score") or 0)
            best[sym] = Watched(sym, float(top["pivot"]), str(top.get("strategy_id", "")), why)
    return sorted(best.values(), key=lambda w: w.symbol)


def pivot_alerts(watched: list[Watched], quotes: dict[str, float],
                 cfg: IntradayConfig) -> list[tuple[str, str, str]]:  # fmt: skip
    """(kind, symbol, text) for each watched stock now near or above its pivot."""
    out = []
    for w in watched:
        last = quotes.get(w.symbol)
        if last is None or w.pivot <= 0:
            continue
        gap = (last / w.pivot - 1) * 100  # + above the pivot
        name = NAMES.get(w.strategy, w.strategy)
        if gap >= 0:
            chase = (f" - {gap:.1f}% above: past the {cfg.chase_warn_pct:g}% entry limit"
                     if gap > cfg.chase_warn_pct else "")  # fmt: skip
            out.append(("CROSSED", w.symbol,
                        f"{w.symbol} crossed its pivot {_px(w.pivot)} [{name}, {w.why}]: "
                        f"{_px(last)} (+{gap:.1f}%){chase}. Volume confirms only at the close. "
                        + FOOTER))  # fmt: skip
        elif -gap <= cfg.approach_pct:
            out.append(("APPROACHING", w.symbol,
                        f"{w.symbol} is {-gap:.1f}% below its pivot {_px(w.pivot)} "
                        f"[{name}, {w.why}]: {_px(last)}. " + FOOTER))  # fmt: skip
    return out
