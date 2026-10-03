"""Event engine: replay scan signals day by day (PROJECT_DESIGN 38-40; Phase 9 step 3).

Signals come from stored scans (``setup_scores`` + the primary VCP pattern): an eligible setup
(VCP_LIKE or better, forming / pivot-ready / breakout) is put on a watch list on its scan date
(``ENTRY_SIGNAL``). Each following session uses only that session's bar:

* **entry** (``BREAKOUT``): the first close above the pivot within ``watch_days`` sessions on
  volume >= ``min_volume_ratio`` x the mean of the 50 sessions before it (the detector's
  breakout rule); the trade is filled at that close (cost included);
* the watch ends without a trade after ``watch_days`` sessions (``INVALIDATION``, reason
  ``NO_BREAKOUT``), or when a newer scan of the same stock replaces it;
* **exit** by the exit rule (``outcomes.TradeRule``): stop (``STOP``), target (``EXIT_SIGNAL``)
  or the close ``horizon`` sessions after entry (``TIME_EXIT``); a bar touching both stop and
  target counts as the stop.

One position per stock at a time. Two views:

* **signals**: every trade on its own (no capital limit), the plain edge of the rule;
* **portfolio**: at most ``max_positions`` open at once, each sized 1 / ``max_positions`` of
  equity at entry; when more breakouts arrive on one day than free slots, the higher setup
  score wins. Equity is marked to market daily on closes.

Costs: ``cost_bps`` per side (default 15 bps, about NSE delivery STT + charges + slippage).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from vcp_scanner.research.outcomes import TradeRule

VOLUME_BASE = 50


@dataclass(frozen=True, slots=True)
class Signal:
    instrument_id: str
    scan_date: date
    pivot: float
    score: float | None
    final_low: float | None
    classification: str


@dataclass(frozen=True, slots=True)
class Bar:
    day: date
    high: float
    low: float
    close: float
    volume: float | None


@dataclass(frozen=True, slots=True)
class Event:
    instrument_id: str
    day: date
    kind: str  # ENTRY_SIGNAL | BREAKOUT | STOP | EXIT_SIGNAL | TIME_EXIT | INVALIDATION
    price: float | None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Trade:
    instrument_id: str
    scan_date: date
    entry_day: date
    entry: float
    exit_day: date
    exit: float
    exit_kind: str
    ret_pct: float  # after costs
    score: float | None
    classification: str


@dataclass(frozen=True, slots=True)
class EngineConfig:
    rule: TradeRule
    watch_days: int = 20
    horizon: int = 60
    min_volume_ratio: float = 1.5
    cost_bps: float = 15.0
    max_positions: int = 10


def _stop_price(entry: float, rule: TradeRule, final_low: float | None) -> float:
    stop_ret = -rule.stop_pct
    if rule.stop_at_final_low and final_low is not None and final_low < entry:
        stop_ret = max(stop_ret, (final_low * 0.995 / entry - 1) * 100)
    return entry * (1 + stop_ret / 100)


def _breakout(bars: Sequence[Bar], i: int, pivot: float, ratio: float) -> bool:
    b = bars[i]
    if b.close <= pivot or b.volume is None or i < VOLUME_BASE:
        return False
    base = [x.volume for x in bars[i - VOLUME_BASE : i]]
    if any(v is None for v in base):
        return False
    mean = sum(v for v in base if v is not None) / VOLUME_BASE
    return mean > 0 and b.volume >= ratio * mean


def run_signals(
    signals: Sequence[Signal], bars: dict[str, list[Bar]], cfg: EngineConfig
) -> tuple[list[Trade], list[Event]]:
    """Every trade on its own (module docstring). Bars are per instrument, oldest first, and
    must include the 50 sessions before the first signal."""
    cost = cfg.cost_bps / 10_000
    trades: list[Trade] = []
    events: list[Event] = []
    by_inst: dict[str, list[Signal]] = defaultdict(list)
    for s in sorted(signals, key=lambda s: (s.instrument_id, s.scan_date)):
        by_inst[s.instrument_id].append(s)
    for iid, sigs in by_inst.items():
        bs = bars.get(iid, [])
        start_of = {s.scan_date: s for s in sigs}
        watch: tuple[Signal, int] | None = None  # (signal, last watch index)
        held: tuple[Signal, int, float, float, float | None] | None = None
        for k, b in enumerate(bs):
            sig = start_of.get(b.day)
            if held is not None:
                s, ek, entry, stop, target = held
                kind = price = None
                if b.low <= stop:
                    kind, price = "STOP", stop
                elif target is not None and b.high >= target:
                    kind, price = "EXIT_SIGNAL", target
                elif k - ek >= cfg.horizon:
                    kind, price = "TIME_EXIT", b.close
                if kind is not None and price is not None:
                    net = (price * (1 - cost)) / (entry * (1 + cost)) - 1
                    trades.append(Trade(iid, s.scan_date, bs[ek].day, entry, b.day, price, kind,
                                        net * 100, s.score, s.classification))  # fmt: skip
                    events.append(Event(iid, b.day, kind, price, {"ret_pct": round(net * 100, 4)}))
                    held = None
                if sig is not None:
                    skip = {"pivot": sig.pivot, "skipped": "IN_POSITION"}
                    events.append(Event(iid, b.day, "ENTRY_SIGNAL", None, skip))
                continue
            if sig is not None:
                if watch is not None:
                    events.append(Event(iid, b.day, "INVALIDATION", None,
                                        {"reason": "REPLACED_BY_NEWER_SCAN"}))  # fmt: skip
                events.append(Event(iid, b.day, "ENTRY_SIGNAL", None,
                                    {"pivot": sig.pivot, "score": sig.score}))  # fmt: skip
                watch = (sig, k + cfg.watch_days)
                continue  # the scan date's own bar is history, not an entry
            if watch is None:
                continue
            s, last = watch
            if _breakout(bs, k, s.pivot, cfg.min_volume_ratio):
                entry = b.close
                stop = _stop_price(entry, cfg.rule, s.final_low)
                tp = cfg.rule.target_pct
                target = None if tp is None else entry * (1 + tp / 100)
                held = (s, k, entry, stop, target)
                events.append(Event(iid, b.day, "BREAKOUT", entry,
                                    {"pivot": s.pivot, "stop": round(stop, 4)}))  # fmt: skip
                watch = None
            elif k >= last:
                events.append(Event(iid, b.day, "INVALIDATION", None, {"reason": "NO_BREAKOUT"}))
                watch = None
        if held is not None:  # censored: fewer than ``horizon`` sessions left in the data
            events.append(Event(iid, bs[-1].day, "OPEN_AT_END", None,
                                {"entry_day": bs[held[1]].day.isoformat()}))  # fmt: skip
    trades.sort(key=lambda t: (t.entry_day, t.instrument_id))
    return trades, events


@dataclass(frozen=True, slots=True)
class Portfolio:
    taken: list[Trade]
    skipped: int  # trades not taken: no free slot
    equity: list[tuple[date, float]]  # daily mark-to-market, starting at 1.0
    avg_exposure: float  # mean share of slots in use


def run_portfolio(
    trades: Sequence[Trade], bars: dict[str, list[Bar]], cfg: EngineConfig
) -> Portfolio:
    """At most ``max_positions`` trades at once, equal size at entry, higher score first on a
    crowded day (module docstring). Trades are the ``run_signals`` trades."""
    cost = cfg.cost_bps / 10_000
    closes: dict[str, dict[date, float]] = {
        iid: {b.day: b.close for b in bs} for iid, bs in bars.items()
    }
    days = sorted({d for c in closes.values() for d in c})
    if not trades or not days:
        return Portfolio([], 0, [], 0.0)
    first = min(t.entry_day for t in trades)
    days = [d for d in days if d >= first]
    by_entry: dict[date, list[Trade]] = defaultdict(list)
    for t in trades:
        by_entry[t.entry_day].append(t)
    cash = 1.0
    open_pos: list[tuple[Trade, float]] = []  # (trade, shares)
    taken: list[Trade] = []
    skipped = 0
    curve: list[tuple[date, float]] = []
    used = 0.0
    last_close: dict[str, float] = {}
    for d in days:
        for iid, c in closes.items():
            if d in c:
                last_close[iid] = c[d]
        # exits first, so a slot freed today can be reused today
        still = []
        for t, sh in open_pos:
            if t.exit_day == d:
                cash += sh * t.exit * (1 - cost)
            else:
                still.append((t, sh))
        open_pos = still
        value = cash + sum(sh * last_close.get(t.instrument_id, t.entry) for t, sh in open_pos)
        for t in sorted(by_entry.get(d, []), key=lambda t: -(t.score or 0.0)):
            if len(open_pos) >= cfg.max_positions:
                skipped += 1
                continue
            alloc = min(value / cfg.max_positions, cash)
            if alloc <= 0:
                skipped += 1
                continue
            sh = alloc / (t.entry * (1 + cost))
            cash -= alloc
            open_pos.append((t, sh))
            taken.append(t)
        value = cash + sum(sh * last_close.get(t.instrument_id, t.entry) for t, sh in open_pos)
        curve.append((d, value))
        used += len(open_pos) / cfg.max_positions
    return Portfolio(taken, skipped, curve, used / len(days))
