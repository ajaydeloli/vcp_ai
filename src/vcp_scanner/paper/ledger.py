"""Paper ledger (STRATEGY_SPECIFICATION 21.3; monitoring phase M3).

The frozen rules (``config/backtest.yaml`` defaults: entry, regime, exit; 10 positions) are
replayed with the event engine from the paper start up to the latest scanned session, and what
they decide becomes ledger events:

* ``WATCH`` (an eligible setup goes on the watch list on its scan date), ``WATCH_EXPIRED``
  (no entry within the watch days, or replaced by a newer scan);
* ``ENTRY`` (a breakout the 10-slot paper portfolio takes; price = the entry close, with the
  stop) and ``SKIPPED_NO_SLOT`` (an entry the full portfolio could not take);
* ``EXIT`` (stop or time exit of a taken position; with the return after costs);
* ``DAY_CLOSED`` (one per strategy and trading day, with the regime that day): how far the
  ledger has been written.

Only events after the last ``DAY_CLOSED`` are appended (a missed evening is caught up in date
order). Events up to it are recomputed and compared with the stored ones; a difference (usually
a data fix) is appended once as a ``DIVERGENCE`` event and nothing stored is changed. Pure: no
storage here (``paper_repository`` reads and writes).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from vcp_scanner.backtest.engine import Bar, EngineConfig, Signal, run_portfolio, run_signals

RULE_SET = "paper-v1"
LEDGER_TYPES = ("WATCH", "WATCH_EXPIRED", "ENTRY", "SKIPPED_NO_SLOT", "EXIT", "DAY_CLOSED")


@dataclass(frozen=True, slots=True)
class PaperEvent:
    strategy_id: str
    instrument_id: str | None
    event_date: date
    event_type: str
    price: float | None = None
    scan_date: date | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def key(self) -> tuple[Any, ...]:
        """What identifies an event when stored and recomputed ledgers are compared."""
        price = None if self.price is None else round(self.price, 4)
        return (self.event_type, self.instrument_id, self.event_date, self.scan_date, price)

    def event_id(self, rule_set: str, config_hash: str) -> str:
        raw = json.dumps([rule_set, self.strategy_id, config_hash, *map(str, self.key())])
        return "pe-" + hashlib.sha256(raw.encode()).hexdigest()[:24]


def derive_events(
    strategy_id: str, signals: Sequence[Signal], bars: Mapping[str, list[Bar]],
    cfg: EngineConfig, start: date, through: date, trading_days: Iterable[date],
) -> list[PaperEvent]:  # fmt: skip
    """The ledger the frozen rules give for ``start`` .. ``through`` (module docstring).
    ``bars`` must end at ``through``; ``trading_days`` are the sessions to close."""
    trades, events = run_signals(signals, dict(bars), cfg, include_open=True)
    trades = [t for t in trades if t.entry_day >= start]
    port = run_portfolio(trades, dict(bars), cfg)
    taken = {id(t) for t in port.taken}
    stops = {(e.instrument_id, e.day): e.meta.get("stop") for e in events
             if e.kind == "BREAKOUT"}  # fmt: skip
    out: list[PaperEvent] = []
    for e in events:
        if e.kind == "ENTRY_SIGNAL" and "skipped" not in e.meta:
            out.append(
                PaperEvent(
                    strategy_id,
                    e.instrument_id,
                    e.day,
                    "WATCH",
                    None,
                    e.day,
                    {"pivot": e.meta.get("pivot"), "score": e.meta.get("score")},
                )
            )
        elif e.kind == "INVALIDATION":
            out.append(PaperEvent(strategy_id, e.instrument_id, e.day, "WATCH_EXPIRED", None,
                                  None, {"reason": e.meta.get("reason")}))  # fmt: skip
    for t in trades:
        if id(t) not in taken:
            out.append(PaperEvent(strategy_id, t.instrument_id, t.entry_day, "SKIPPED_NO_SLOT",
                                  t.entry, t.scan_date, {"score": t.score}))  # fmt: skip
            continue
        out.append(PaperEvent(strategy_id, t.instrument_id, t.entry_day, "ENTRY", t.entry,
                              t.scan_date, {"stop": stops.get((t.instrument_id, t.entry_day)),
                                            "score": t.score,
                                            "classification": t.classification}))  # fmt: skip
        if t.exit_kind != "OPEN":
            out.append(PaperEvent(strategy_id, t.instrument_id, t.exit_day, "EXIT", t.exit,
                                  t.scan_date, {"kind": t.exit_kind,
                                                "ret_pct": round(t.ret_pct, 4),
                                                "entry_day": t.entry_day.isoformat()}))  # fmt: skip
    for d in sorted(set(trading_days)):
        if start <= d <= through:
            regime = None if cfg.regime is None else bool(cfg.regime.get(d, False))
            out.append(PaperEvent(strategy_id, None, d, "DAY_CLOSED", None, None,
                                  {"regime_on": regime}))  # fmt: skip
    out = [e for e in out if start <= e.event_date <= through]
    out.sort(key=lambda e: (e.event_date, LEDGER_TYPES.index(e.event_type), e.instrument_id or ""))
    return out


@dataclass(frozen=True, slots=True)
class LedgerUpdate:
    new: list[PaperEvent]
    divergence: PaperEvent | None
    last_closed: date | None


def plan_update(
    strategy_id: str, derived: Sequence[PaperEvent], stored: Sequence[PaperEvent],
    through: date,
) -> LedgerUpdate:  # fmt: skip
    """What to append: derived events after the last stored ``DAY_CLOSED``, and one
    ``DIVERGENCE`` if the recomputed events up to it differ from the stored ones (unless the
    same difference was already recorded)."""
    closed = [e.event_date for e in stored if e.event_type == "DAY_CLOSED"]
    last = max(closed) if closed else None
    new = [e for e in derived if last is None or e.event_date > last]
    divergence = None
    if last is not None:
        old_stored = {e.key() for e in stored if e.event_type in LEDGER_TYPES}
        old_derived = {e.key() for e in derived if e.event_date <= last}
        missing = sorted(map(_show, old_stored - old_derived))
        extra = sorted(map(_show, old_derived - old_stored))
        if missing or extra:
            meta = {"stored_not_recomputed": missing, "recomputed_not_stored": extra}
            seen = [e.meta for e in stored if e.event_type == "DIVERGENCE"]
            if meta not in seen:
                divergence = PaperEvent(strategy_id, None, through, "DIVERGENCE", None, None, meta)
    return LedgerUpdate(new, divergence, last)


def _show(key: tuple[Any, ...]) -> str:
    return " ".join("-" if x is None else str(x) for x in key)
