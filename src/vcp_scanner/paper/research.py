"""Research paper ledgers (owner, 2026-11-07).

A research ledger replays one rule variant with the same engine, signals and dates as the
frozen paper rules (``ledger.RULE_SET``) and stores its decisions under its own rule set, so the
frozen ledger, its review (1 Apr 2027) and every view of it stay exactly as they are. Each
variant's rule is fixed here when it is added and is never tuned afterwards; a changed rule is
a new rule set. Research and display only: nothing here changes a scan, a score or the frozen
ledger.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date

from vcp_scanner.backtest.engine import EngineConfig


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
    max_above_sma50_pct: float

    def engine(self, frozen: EngineConfig) -> EngineConfig:
        """The frozen rules with this variant's one change."""
        return replace(frozen, max_above_sma50_pct=self.max_above_sma50_pct)


RESEARCH_LEDGERS: tuple[ResearchLedger, ...] = (
    ResearchLedger(
        rule_set="research-sma50-v1",
        strategy_id="vcp",
        label="No entry more than 10% above the 50-day average",
        start=date(2026, 10, 1),
        decided=date(2026, 11, 7),
        max_above_sma50_pct=10.0,
    ),
)


def research_ledgers(strategy_id: str | None = None) -> list[ResearchLedger]:
    """Every research ledger, or those of one strategy."""
    return [r for r in RESEARCH_LEDGERS if strategy_id is None or r.strategy_id == strategy_id]
