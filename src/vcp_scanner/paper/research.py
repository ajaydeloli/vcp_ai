"""Research paper ledgers (owner, 2026-11-07).

A research ledger replays one rule variant with the same engine, signals and dates as the
frozen paper rules (``ledger.RULE_SET``) and stores its decisions under its own rule set, so the
frozen ledger, its review (1 Apr 2027) and every view of it stay exactly as they are. Each
variant's rule is fixed here when it is added and is never tuned afterwards; a changed rule is
a new rule set. Research and display only: nothing here changes a scan, a score or the frozen
ledger.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date

from vcp_scanner.backtest.engine import EngineConfig

#: research-reclaim-v1 (owner, 2026-11-09): NIFTY 500 against its 50-day simple average
RECLAIM_INDEX = "Nifty 500"
RECLAIM_SMA = 50
#: no entry when the index closed above its average on each of this many sessions before the
#: entry day: the session before and the 20 before that, exactly as tested (T4a, 2012-2021)
RECLAIM_RUN = 21


def long_uptrend_days(
    closes: Sequence[tuple[date, float]], days: Iterable[date], sma: int = RECLAIM_SMA,
    run: int = RECLAIM_RUN,
) -> dict[date, bool]:  # fmt: skip
    """For each day: True when the index closed above its ``sma``-session simple average
    (closes up to and including that session) on each of the ``run`` index sessions before the
    day. Only sessions before the day are read, so the answer is known at the decision. False
    when there is not enough history to tell (the rule then lets the entry through)."""
    cl = sorted(closes)
    when = [d for d, _ in cl]
    px = [c for _, c in cl]
    above: list[bool | None] = []
    total = 0.0
    for k, c in enumerate(px):
        total += c
        if k >= sma:
            total -= px[k - sma]
        above.append(c > total / sma if k >= sma - 1 else None)
    out: dict[date, bool] = {}
    for d in days:
        k = bisect_left(when, d)  # index sessions strictly before d: 0 .. k-1
        window = above[k - run : k] if k >= run else []
        out[d] = len(window) == run and all(a is True for a in window)
    return out


@dataclass(frozen=True, slots=True)
class ResearchLedger:
    rule_set: str
    strategy_id: str
    #: what the variant changes, in words for the dashboard
    label: str
    #: first session written (the frozen ledger's start, so the two compare day for day)
    start: date
    #: the day the rule was fixed (pre-registered; see the project's validation diagnosis)
    decided: date
    max_above_sma50_pct: float | None = None
    #: choose between breakouts by score v2 (paper/score_v2.py) instead of the frozen score
    rank_by_score_v2: bool = False
    #: no entry while NIFTY 500 has closed above its 50-day average on each of the 21 sessions
    #: before the entry day (``long_uptrend_days``); the frozen regime still applies
    fresh_reclaim_only: bool = False

    def engine(
        self, frozen: EngineConfig, index_closes: Sequence[tuple[date, float]] | None = None
    ) -> EngineConfig:
        """The frozen rules with this variant's change. ``index_closes`` (NIFTY 500, oldest
        history first) is needed only by a fresh-reclaim ledger."""
        cfg = replace(frozen, max_above_sma50_pct=self.max_above_sma50_pct)
        if not self.fresh_reclaim_only:
            return cfg
        if frozen.regime is None or index_closes is None:
            raise ValueError(f"{self.rule_set} needs the frozen regime and NIFTY 500 closes")
        regime: Mapping[date, bool] = frozen.regime
        long_up = long_uptrend_days(index_closes, regime.keys())
        return replace(cfg, regime={d: on and not long_up[d] for d, on in regime.items()})


RESEARCH_LEDGERS: tuple[ResearchLedger, ...] = (
    ResearchLedger(
        rule_set="research-sma50-v1",
        strategy_id="vcp",
        label="No entry more than 10% above the 50-day average",
        start=date(2026, 10, 1),
        decided=date(2026, 11, 7),
        max_above_sma50_pct=10.0,
    ),
    # owner approval 2026-11-08: research-sma50-v1 with one change, the ranking
    ResearchLedger(
        rule_set="research-score2-v1",
        strategy_id="vcp",
        label="No entry more than 10% above the 50-day average; slots go to the higher score v2",
        start=date(2026, 10, 1),
        decided=date(2026, 11, 8),
        max_above_sma50_pct=10.0,
        rank_by_score_v2=True,
    ),
    # owner go 2026-11-09 after the 2012-2021 replication (T4a): the frozen rules with one
    # change, no entry in a long-established index uptrend
    ResearchLedger(
        rule_set="research-reclaim-v1",
        strategy_id="vcp",
        label="No entry while NIFTY 500 has held above its 50-day average for 21 sessions",
        start=date(2026, 10, 1),
        decided=date(2026, 11, 9),
        fresh_reclaim_only=True,
    ),
)


def needs_index(ledgers: Iterable[ResearchLedger]) -> bool:
    return any(r.fresh_reclaim_only for r in ledgers)


def research_ledgers(strategy_id: str | None = None) -> list[ResearchLedger]:
    """Every research ledger, or those of one strategy."""
    return [r for r in RESEARCH_LEDGERS if strategy_id is None or r.strategy_id == strategy_id]
