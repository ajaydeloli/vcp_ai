"""Relative strength ranking (``rs-1.1.0``, TREND_TEMPLATE_SPECIFICATION section 3).

Pure strategy code (audit Fix 6): the formula and ranking live here as plain Python; prices
come from and rows go to a ``RelativeStrengthRepository``. No storage engine is imported.

    R_n     = adj_close(t) / adj_close(t - n) - 1
    rs_raw  = sum(weight_k * R_k)        (default 0.40/0.20/0.20/0.20 over 63/126/189/252)
    rs-1.1.0 (default; audit P3-1, owner decision 2026-10-02):
    pct     = (count_below + 0.5 * (count_equal - 1)) / (N - 1)    (others only; N = 1: 1.0)
    rs_rank = 1 + floor(98 * pct)                                    -> 1 .. 99
    rs-1.0.0 (kept for re-running old scans): pct = (count_below + 0.5 * count_equal) / N,
    which counts the instrument's own half-weight, so the top rank was 98.

Statuses (AGENTS.md rule 4, missing is not zero):
- PASS               ranked; rs_raw / rs_rank / rs_percentile are set
- INSUFFICIENT_DATA  a window is missing or not finite; rs_raw, rank and percentile NULL and
                     the instrument is not in the ranking population
- STALE_DATA         more than ``rs_max_missed_sessions`` NSE sessions after the latest bar
                     up to the as-of date (audit P2-2); returns kept, rs_raw NULL, not ranked
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from datetime import date

from vcp_scanner.config.models import RSConfig, StalenessConfig
from vcp_scanner.data.repositories.base import DataQualityGate, RelativeStrengthRepository
from vcp_scanner.domain.sessions import missed_sessions_since
from vcp_scanner.domain.trend import RSPriceInput, RSRow

logger = logging.getLogger(__name__)

# relative_strength_snapshots stores exactly four return columns (ret_63..ret_252).
_RETURN_COLUMNS = 4


def _window_return(last: float, lagged: float | None) -> float | None:
    """``last / lagged - 1``; None when unavailable, zero-based or not finite (audit P1-9)."""
    if lagged is None or lagged == 0:
        return None
    value = last / lagged - 1
    return value if math.isfinite(value) else None


def compute_rs_rows(
    inputs: Sequence[RSPriceInput],
    as_of_date: date,
    config: RSConfig,
    *,
    sessions: Sequence[date] = (),
    max_missed_sessions: int = StalenessConfig().rs_max_missed_sessions,
) -> list[RSRow]:
    """Returns, raw score, status and rank for every input. Deterministic, no I/O.

    ``sessions`` is the sorted market calendar staleness is counted against (audit P2-2).
    """
    staged: list[tuple[str, tuple[float | None, ...], float | None, str]] = []
    for item in inputs:
        returns = tuple(_window_return(item.last_close, lag) for lag in item.lagged_closes)
        candidate: float | None = None
        present = [r for r in returns if r is not None]
        if len(present) == len(returns):
            # Left-to-right sum, the same evaluation order as the original SQL expression.
            terms = [w * r for w, r in zip(config.weights, present, strict=True)]
            candidate = terms[0]
            for term in terms[1:]:
                candidate += term
        missed = missed_sessions_since(sessions, item.last_trade_date, as_of_date)
        if missed > max_missed_sessions:
            staged.append((item.instrument_id, returns, None, "STALE_DATA"))
        elif candidate is None:
            staged.append((item.instrument_id, returns, None, "INSUFFICIENT_DATA"))
        else:
            staged.append((item.instrument_id, returns, candidate, "PASS"))

    ranked = [raw for _, _, raw, _ in staged if raw is not None]
    population = len(ranked)
    rows: list[RSRow] = []
    for iid, returns, raw, status in staged:
        pct: float | None = None
        rank: int | None = None
        if raw is not None and population:
            below = sum(1 for other in ranked if other < raw)
            equal = sum(1 for other in ranked if other == raw)
            if config.version == "rs-1.0.0":
                pct = (below + 0.5 * equal) / population
            elif population == 1:
                pct = 1.0
            else:
                pct = (below + 0.5 * (equal - 1)) / (population - 1)
            rank = 1 + math.floor(98 * pct)
        rows.append(RSRow(iid, returns, raw, rank, pct, population, status))
    return rows


class RelativeStrengthEngine:
    """Ranks the eligible members of a universe snapshot by relative strength.

    Prices come from the repository's data snapshot (``LIVE`` = unfrozen working data), which
    is distinct from the universe snapshot that defines the ranking population.
    """

    def __init__(
        self,
        repository: RelativeStrengthRepository,
        calculation_version: str | None = None,
        config: RSConfig | None = None,
        quality_gate: DataQualityGate | None = None,
        staleness: StalenessConfig | None = None,
    ) -> None:
        self._repo = repository
        self._staleness = staleness or StalenessConfig()
        # Instruments blocked by an unresolved data-quality event are left out of the ranking
        # population (audit P0-2): a suspected missed split would distort everyone's percentile.
        self._quality_gate = quality_gate
        self.config = config or RSConfig()
        self.calculation_version = calculation_version or self.config.version
        if len(self.config.windows_days) != _RETURN_COLUMNS:
            raise ValueError(
                f"relative_strength_snapshots stores {_RETURN_COLUMNS} return columns; "
                f"RSConfig.windows_days has {len(self.config.windows_days)}"
            )

    def compute_for_date(self, as_of_date: date, universe_snapshot_id: str) -> int:
        """Compute and store RS for all eligible, unblocked members; returns rows written."""
        members = self._repo.eligible_members(universe_snapshot_id)
        blocked = self._blocked(members, as_of_date)
        population = [m for m in members if m not in blocked]
        inputs = self._repo.load_rs_inputs(population, as_of_date, self.config.windows_days)
        rows = compute_rs_rows(
            inputs,
            as_of_date,
            self.config,
            sessions=self._repo.load_sessions(as_of_date, universe_snapshot_id),
            max_missed_sessions=self._staleness.rs_max_missed_sessions,
        )
        return self._repo.save_relative_strength(
            as_of_date, universe_snapshot_id, self.calculation_version, rows
        )

    def _blocked(self, members: Sequence[str], as_of_date: date) -> set[str]:
        if self._quality_gate is None:
            return set()
        blocked = sorted(self._quality_gate.blocked_instruments(members, as_of_date))
        if blocked:
            logger.warning(
                "RS: excluding %d instrument(s) blocked by data-quality events: %s",
                len(blocked),
                ", ".join(blocked[:10]) + (" ..." if len(blocked) > 10 else ""),
            )
        return set(blocked)
