"""Universe calculation and generation (Phase 3).

Strategy code (audit Fix 6): the eligibility rules are a pure function
(``evaluate_eligibility``) over ``UniverseCandidate`` facts; all point-in-time data access is
behind ``UniverseInputRepository``. No storage engine is imported here.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import replace
from datetime import date, datetime
from typing import TYPE_CHECKING

from vcp_scanner.config.models import UniverseConfig
from vcp_scanner.domain.enums import SurvivorshipStatus
from vcp_scanner.domain.market import PROVIDER_ADJUSTED_SOURCES
from vcp_scanner.domain.universe import UniverseCandidate, UniverseMembership, UniverseSnapshot
from vcp_scanner.infrastructure.clock import Clock, utc_now

if TYPE_CHECKING:
    from vcp_scanner.data.repositories.base import DataQualityGate, UniverseInputRepository

logger = logging.getLogger(__name__)

UNIVERSE_METHOD_VERSION = "1.1"


def evaluate_eligibility(
    candidate: UniverseCandidate, config: UniverseConfig, as_of_date: date
) -> tuple[bool, str | None]:
    """Apply the universe rules in order; the first failing rule names the exclusion.

    Order: staleness, exchange, series, minimum price (price that actually traded), 20-day
    and 50-day average traded value (raw close x raw volume), history length, ASM/GSM, T2T.
    """
    c = config
    if c_last := candidate.last_trade_date:
        stale_days = (as_of_date - c_last).days
        if stale_days > c.max_staleness_days:
            return False, f"Stale: last trade {c_last} is {stale_days}d before {as_of_date}"
    if candidate.exchange != c.exchange:
        return False, f"Exchange {candidate.exchange} != {c.exchange}"
    if candidate.series not in c.eligible_series:
        return False, f"Series {candidate.series} not in {c.eligible_series}"
    price = candidate.last_price
    if price is None or price < c.min_close_price:
        return False, f"Price {price} < {c.min_close_price}"
    tv20 = candidate.avg_traded_value_20d
    if tv20 is None or tv20 < c.min_daily_turnover_inr:
        return False, f"Traded value {tv20} < {c.min_daily_turnover_inr}"
    tv50 = candidate.avg_traded_value_50d
    if tv50 is None or tv50 < c.min_avg_traded_value_50d_inr:
        return False, f"50d traded value {tv50} < {c.min_avg_traded_value_50d_inr}"
    history = candidate.days_history
    if history is None or history < c.min_history_days:
        return False, f"History {history or 0} bars < {c.min_history_days}"
    if c.exclude_asm_gsm and (candidate.asm_flag == "YES" or candidate.gsm_flag == "YES"):
        return False, "ASM/GSM flag active"
    if c.exclude_trade_to_trade and candidate.t2t_flag == "YES":
        return False, "T2T flag active"
    return True, None


class UniverseBuilder:
    """Builds point-in-time universe snapshots."""

    def __init__(
        self,
        repository: UniverseInputRepository,
        config: UniverseConfig,
        *,
        clock: Clock = utc_now,
        quality_gate: DataQualityGate | None = None,
    ) -> None:
        self._repo = repository
        self._config = config
        self._clock = clock
        # Optional (audit P0-2). When set, instruments with an unresolved signal-blocking
        # data-quality event become ineligible, judged as known at the snapshot's created_at.
        self._quality_gate = quality_gate

    def _apply_quality_gate(
        self, memberships: list[UniverseMembership], as_of_date: date, known_at: datetime
    ) -> list[UniverseMembership]:
        if self._quality_gate is None:
            return memberships
        eligible_ids = [m.instrument_id for m in memberships if m.eligible]
        blocked = self._quality_gate.blocked_instruments(
            eligible_ids, as_of_date, known_at=known_at
        )
        if not blocked:
            return memberships
        logger.warning("Universe: %d instrument(s) blocked by data-quality events.", len(blocked))
        return [
            replace(
                m,
                eligible=False,
                exclusion_reason="Data quality blocked: " + ", ".join(blocked[m.instrument_id]),
            )
            if m.instrument_id in blocked
            else m
            for m in memberships
        ]

    def _hash_config(self) -> str:
        """Return a deterministic hash of the universe configuration."""
        data = self._config.model_dump(mode="json")
        encoded = json.dumps(data, sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:8]

    def _survivorship_status(self, known_at: datetime) -> SurvivorshipStatus:
        """Derive the survivorship label from what the security master actually holds.

        PROJECT_DESIGN 14A: ``POINT_IN_TIME_COMPLETE`` needs delisted names and historical
        membership. Nothing in the data can prove completeness, so it is granted only when
        the operator attests coverage (``survivorship_coverage_verified``) AND delisting
        records exist. Otherwise the label is honest about the gap, and results must not be
        used to validate thresholds (``SurvivorshipStatus.may_validate_thresholds``).
        """
        if self._repo.count_known_delistings(known_at) == 0:
            return SurvivorshipStatus.BIASED  # current listings only
        if not self._config.survivorship_coverage_verified:
            return SurvivorshipStatus.PARTIAL  # some delisted names, completeness unproven
        return SurvivorshipStatus.POINT_IN_TIME_COMPLETE

    def build_snapshot(
        self, as_of_date: date, *, known_at: datetime | None = None
    ) -> tuple[UniverseSnapshot, list[UniverseMembership]]:
        """Calculate universe memberships as of the given date.

        Evaluates liquidity, price, and exclusions purely using data known on or before as_of_date.

        ``known_at`` is the knowledge cutoff: only rows with ``known_from <= known_at`` (and
        not yet superseded at that time) are used, so a past snapshot can be rebuilt as it
        would have looked then, ignoring later restatements and back-filled history.
        Default: the injected clock, i.e. everything known now. It is recorded as the
        snapshot's ``created_at``, which therefore always means "as known at".
        """
        logger.info("Building universe snapshot for %s...", as_of_date)
        created_at = known_at if known_at is not None else self._clock()
        if created_at.tzinfo is None:
            raise ValueError("known_at must be timezone-aware")
        snapshot_id = f"uv_{as_of_date.strftime('%Y%m%d')}_{uuid.uuid4().hex[:6]}"

        candidates = self._repo.load_universe_candidates(
            as_of_date, created_at, sorted(PROVIDER_ADJUSTED_SOURCES)
        )
        memberships = []
        for cand in candidates:
            eligible, reason = evaluate_eligibility(cand, self._config, as_of_date)
            memberships.append(
                UniverseMembership(
                    universe_snapshot_id=snapshot_id,
                    instrument_id=cand.instrument_id,
                    eligible=eligible,
                    exclusion_reason=reason,
                    avg_traded_value=(
                        float(cand.avg_traded_value_20d) if cand.avg_traded_value_20d else None
                    ),
                    price=float(cand.last_price) if cand.last_price else None,
                    instrument_type="EQUITY",
                    series=cand.series,
                    asm_flag=cand.asm_flag,
                    gsm_flag=cand.gsm_flag,
                    t2t_flag=cand.t2t_flag,
                )
            )

        memberships = self._apply_quality_gate(memberships, as_of_date, created_at)

        snapshot = UniverseSnapshot(
            universe_snapshot_id=snapshot_id,
            universe_name="VCP_BASE",
            as_of_date=as_of_date,
            created_at=created_at,
            config_hash=self._hash_config(),
            method_version=UNIVERSE_METHOD_VERSION,
            survivorship_status=self._survivorship_status(created_at),
        )

        eligible_count = sum(m.eligible for m in memberships)
        logger.info(
            "Generated %d memberships, %d eligible.",
            len(memberships),
            eligible_count,
        )
        return snapshot, memberships
