"""What the alerts say (owner decision 2026-10-21): the evening summary after the daily run and
the intraday pivot alerts. ``get(path, **params)`` reads the dashboard API (in-process for the
evening message, the running API for the intraday watcher), so alerts show exactly what the
dashboard shows. Monitoring only: no alert is a trade signal or advice.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from vcp_scanner.alerts.telegram import EveningConfig, IntradayConfig

Get = Callable[..., Any]
NAMES = {"vcp": "VCP", "flat_base": "Flat base", "three_weeks_tight": "3 weeks tight",
         "cup_handle": "Cup & handle", "double_bottom": "Double bottom"}  # fmt: skip
SESSION_MIN = 375  # 09:15-15:30 IST


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
    return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class Watched:
    symbol: str
    pivot: float
    strategy: str
    why: str  # "setup" | "AI watchlist" | "your list"
    score: float | None = None
    grade: str | None = None
    stop: float | None = None
    rs_rank: float | None = None
    sector: str | None = None
    avg_volume: float | None = None  # mean of the last 50 daily volumes


def _setup_fields(r: dict[str, Any]) -> dict[str, Any]:
    return {"score": r.get("score"), "grade": r.get("grade"), "stop": r.get("stop"),
            "rs_rank": r.get("rs_rank")}  # fmt: skip


def _enrich(get: Get, w: Watched) -> Watched:
    """Sector, RS rank and the 50-day average volume; anything missing stays None."""
    extra: dict[str, Any] = {}
    try:
        rows = get("screener", q=w.symbol, universe_only="false").get("rows", [])
        row = next((r for r in rows if r.get("symbol") == w.symbol), None)
        if row:
            extra["sector"] = row.get("sector")
            if w.rs_rank is None:
                extra["rs_rank"] = row.get("rs_rank")
    except Exception:  # noqa: BLE001 - details are optional
        pass
    try:
        vols = [b["volume"] for b in get(f"stocks/{w.symbol}/bars", days=80).get("bars", [])
                if b.get("volume")][-50:]  # fmt: skip
        if len(vols) >= 20:
            extra["avg_volume"] = sum(vols) / len(vols)
    except Exception:  # noqa: BLE001
        pass
    return replace(w, **extra)


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
                best[r["symbol"]] = Watched(
                    r["symbol"], float(pivot), s, "setup", **_setup_fields(r)
                )
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
            best[sym] = Watched(
                sym, float(top["pivot"]), str(top.get("strategy_id", "")), why, **_setup_fields(top)
            )
    return sorted((_enrich(get, w) for w in best.values()), key=lambda w: w.symbol)


def _qty(v: float) -> str:
    if v >= 1e7:
        return f"{v / 1e7:.2f} Cr"
    if v >= 1e5:
        return f"{v / 1e5:.2f} L"
    return f"{v:,.0f}"


def _volume_line(w: Watched, q: dict[str, Any], now: datetime | None) -> str | None:
    vol = q.get("volume")
    if not vol:
        return None
    line = f"Volume so far {_qty(float(vol))}"
    if w.avg_volume:
        share = float(vol) / w.avg_volume
        line += f" = {share * 100:.0f}% of the 50-day avg"
        if now is not None:
            elapsed = (now.hour * 60 + now.minute) - (9 * 60 + 15)
            if 15 <= elapsed < SESSION_MIN:
                line += f" (pace {share * SESSION_MIN / elapsed:.1f}x a normal day)"
    return line


def pivot_alerts(watched: list[Watched], quotes: dict[str, float], cfg: IntradayConfig,
                 live: dict[str, dict[str, Any]] | None = None, market: str | None = None,
                 now: datetime | None = None) -> list[tuple[str, str, str]]:  # fmt: skip
    """(kind, symbol, text) for each watched stock now near or above its pivot. ``live`` holds
    the full quotes (day change, high, low, volume) and ``market`` the market-health line."""
    out = []
    for w in watched:
        last = quotes.get(w.symbol)
        if last is None or w.pivot <= 0:
            continue
        gap = (last / w.pivot - 1) * 100  # + above the pivot
        if gap >= 0:
            kind = "CROSSED"
            head = f"CROSSED: {w.symbol} is +{gap:.1f}% above its pivot {_px(w.pivot)}"
        elif -gap <= cfg.approach_pct:
            kind = "APPROACHING"
            head = f"APPROACHING: {w.symbol} is {-gap:.1f}% below its pivot {_px(w.pivot)}"
        else:
            continue
        q = (live or {}).get(w.symbol, {})
        lines = [("\U0001f7e2 " if kind == "CROSSED" else "\U0001f7e1 ") + head]
        setup = [NAMES.get(w.strategy, w.strategy)]
        if w.grade:
            setup.append(f"grade {w.grade}")
        if w.score is not None:
            setup.append(f"score {float(w.score):.0f}")
        if w.rs_rank is not None:
            setup.append(f"RS {float(w.rs_rank):.0f}")
        lines.append(" | ".join(setup) + (f" | {w.sector}" if w.sector else "") + f" ({w.why})")
        price = f"Price {_px(last)}"
        if q.get("change_pct") is not None:
            price += f", day {float(q['change_pct']):+.1f}%"
        if q.get("low") is not None and q.get("high") is not None:
            price += f" (range {_px(q['low'])} - {_px(q['high'])})"
        lines.append(price)
        limit = w.pivot * (1 + cfg.chase_warn_pct / 100)
        if gap > cfg.chase_warn_pct:
            lines.append(f"\u26A0\uFE0F {gap:.1f}% above: past the {cfg.chase_warn_pct:g}% entry "
                         f"limit {_px(limit)}")  # fmt: skip
        else:
            lines.append(f"Buy zone {_px(w.pivot)} - {_px(limit)} (pivot +{cfg.chase_warn_pct:g}%)")
        if w.stop:
            risk = (1 - float(w.stop) / last) * 100
            lines.append(f"Stop {_px(w.stop)}: risk {risk:.1f}% from this price")
        vol = _volume_line(w, q, now)
        if vol:
            lines.append(vol)
        if market:
            lines.append(market)
        lines.append("Volume confirms only at the close." if kind == "CROSSED"
                     else "No action until it crosses the pivot.")  # fmt: skip
        if now is not None:
            lines[-1] += f" {now:%H:%M} IST"
        out.append((kind, w.symbol, "\n".join(lines)))
    return out
