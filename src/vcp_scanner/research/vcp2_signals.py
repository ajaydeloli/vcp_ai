"""Daily VCP v2 candidates as backtest signals (research only; owner request 2026-11-11).

The event engine (``backtest/engine.py``) treats a signal's scan date as history and starts a
20-session watch from the next session; a newer signal of the same stock replaces the watch.
That suits weekly scans. v2 scans every session, so a stock that is a candidate day after day
would be re-signalled every day and could never be entered. A candidate day therefore becomes a
signal only when it starts something new:

* the stock's first candidate day, or the first after a session it was not a candidate;
* the pivot moved (by more than ``pivot_tolerance_pct``): a new contraction, a new setup;
* ``rearm_sessions`` scan sessions have passed since the last signal while it stayed a
  candidate, so a long-lived base keeps being watched after the 20-session watch ends.

Pure: rows in, signals out. The engine is not changed (the paper ledgers depend on it).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from vcp_scanner.backtest.engine import Signal


@dataclass(frozen=True, slots=True)
class CandidateDay:
    instrument_id: str
    scan_date: date
    pivot: float
    final_low: float | None
    score: float | None


def daily_signals(
    rows: Sequence[CandidateDay], scan_days: Sequence[date], rearm_sessions: int = 20,
    pivot_tolerance_pct: float = 0.01, classification: str = "VCP2",
) -> list[Signal]:  # fmt: skip
    """The candidate days that start something new (module docstring), as engine signals."""
    index = {d: k for k, d in enumerate(sorted(scan_days))}
    by_inst: dict[str, list[CandidateDay]] = defaultdict(list)
    for r in rows:
        by_inst[r.instrument_id].append(r)
    out: list[Signal] = []
    for iid, days in by_inst.items():
        days.sort(key=lambda r: r.scan_date)
        prev_k: int | None = None  # scan-session index of the previous candidate day
        last: tuple[int, float] | None = None  # (index, pivot) of the last signal
        for r in days:
            k = index[r.scan_date]
            new = (
                last is None
                or prev_k != k - 1
                or abs(r.pivot / last[1] - 1) * 100 > pivot_tolerance_pct
                or k - last[0] >= rearm_sessions
            )
            if new:
                out.append(Signal(iid, r.scan_date, r.pivot, r.score, r.final_low,
                                  classification))  # fmt: skip
                last = (k, r.pivot)
            prev_k = k
    out.sort(key=lambda s: (s.scan_date, s.instrument_id))
    return out
