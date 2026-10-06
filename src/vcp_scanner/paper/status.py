"""Paper results from the ledger (STRATEGY_SPECIFICATION 21.3, 21.6): closed trades, open
positions marked at the latest close, skipped entries, divergences. Pure."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from vcp_scanner.paper.ledger import PaperEvent


@dataclass(frozen=True, slots=True)
class OpenPosition:
    instrument_id: str
    entry_day: date
    entry: float
    stop: float | None
    last: float | None
    open_pct: float  # vs the entry, before exit costs; 0 when no close is known


@dataclass(frozen=True, slots=True)
class PaperSummary:
    through: date | None
    closed: int
    win_rate: float
    avg_ret: float
    profit_factor: float | None  # None: no losing trade (or no trade)
    open: list[OpenPosition]
    skipped: int
    divergences: int

    @property
    def profit_factor_text(self) -> str:
        return "-" if self.profit_factor is None else f"{self.profit_factor:.2f}"


def summarize(events: Sequence[PaperEvent], last_close: Mapping[str, float]) -> PaperSummary:
    closed_days = [e.event_date for e in events if e.event_type == "DAY_CLOSED"]
    rets = [float(e.meta["ret_pct"]) for e in events if e.event_type == "EXIT"]
    exited = {(e.instrument_id, e.meta.get("entry_day")) for e in events if e.event_type == "EXIT"}
    opens = []
    for e in events:
        if e.event_type != "ENTRY" or e.price is None or e.instrument_id is None:
            continue
        if (e.instrument_id, e.event_date.isoformat()) in exited:
            continue
        last = last_close.get(e.instrument_id)
        pct = 0.0 if last is None else (last / e.price - 1.0) * 100.0
        stop = e.meta.get("stop")
        opens.append(
            OpenPosition(
                e.instrument_id,
                e.event_date,
                e.price,
                None if stop is None else float(stop),
                last,
                pct,
            )
        )
    gains = sum(r for r in rets if r > 0)
    losses = -sum(r for r in rets if r < 0)
    n = len(rets)
    return PaperSummary(
        through=max(closed_days) if closed_days else None, closed=n,
        win_rate=100.0 * sum(r > 0 for r in rets) / n if n else 0.0,
        avg_ret=sum(rets) / n if n else 0.0,
        profit_factor=gains / losses if losses > 0 else None,
        open=opens, skipped=sum(e.event_type == "SKIPPED_NO_SLOT" for e in events),
        divergences=sum(e.event_type == "DIVERGENCE" for e in events),
    )  # fmt: skip
