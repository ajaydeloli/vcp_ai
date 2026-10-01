"""Universe calculation and generation (Phase 3).

Strategy code (audit Fix 6): the eligibility rules are a pure function
(``evaluate_eligibility``) over ``UniverseCandidate`` facts; all point-in-time data access is
behind ``UniverseInputRepository``. No storage engine is imported here.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

from vcp_scanner.config.loader import compute_config_hash
from vcp_scanner.config.models import QualityGateConfig, StalenessConfig, UniverseConfig
from vcp_scanner.domain.enums import SurvivorshipStatus
from vcp_scanner.domain.market import PROVIDER_ADJUSTED_SOURCES
from vcp_scanner.domain.sessions import missed_sessions_since
from vcp_scanner.domain.universe import (
    SurvivorshipEvidence,
    UniverseCandidate,
    UniverseMembership,
    UniverseSnapshot,
)
from vcp_scanner.infrastructure.clock import Clock, utc_now

if TYPE_CHECKING:
    from vcp_scanner.data.repositories.base import DataQualityGate, UniverseInputRepository

logger = logging.getLogger(__name__)

#: 2.0 (audit P0-4): series and trade-to-trade status per date from the NSE bhavcopy; the
#: survivorship label is derived from the data (with reasons), no longer attested in config.
UNIVERSE_METHOD_VERSION = "2.0"

#: Calendar days of history a snapshot depends on: 253 sessions of minimum history plus the
#: 50-day traded-value window fit in about 380 calendar days.
SURVIVORSHIP_WINDOW_DAYS = 380


def derive_survivorship(
    evidence: SurvivorshipEvidence, as_of_date: date
) -> tuple[SurvivorshipStatus, str | None]:
    """Survivorship label and reasons from what the data proves (audit P0-4).

    POINT_IN_TIME_COMPLETE needs (1) a settled NSE bhavcopy entry for every calendar day of
    the look-back window, so delisted and suspended names are present exactly as they traded,
    and (2) ASM and GSM lists collected on the as-of date itself (they have no public history:
    a day the daily security-master run was skipped stays unknown). Without bhavcopy
    data and without any delisting record the population is today's listings only: BIASED.
    Anything in between is PARTIAL, with the reasons recorded.
    """
    reasons: list[str] = []
    if evidence.missing_price_days is None:
        if evidence.delistings == 0:
            return SurvivorshipStatus.BIASED, "prices cover current listings only"
        reasons.append("no NSE bhavcopy history: delisted names lack prices")
    elif evidence.missing_price_days > 0:
        first = evidence.first_price_day
        reasons.append(
            f"bhavcopy history incomplete: {evidence.missing_price_days} day(s) missing since "
            f"{evidence.window_start}" + (f" (history starts {first})" if first else "")
        )
    for flag in ("ASM", "GSM"):
        start = evidence.flag_history_start.get(flag)
        if start is None:
            reasons.append(f"{flag} list never collected")
        elif start > as_of_date:
            reasons.append(f"{flag} history starts {start}")
        elif flag not in evidence.flag_collected_on:
            reasons.append(f"{flag} list not collected on {as_of_date}")
    if not reasons:
        return SurvivorshipStatus.POINT_IN_TIME_COMPLETE, None
    return SurvivorshipStatus.PARTIAL, "; ".join(reasons)


def evaluate_eligibility(
    candidate: UniverseCandidate,
    config: UniverseConfig,
    as_of_date: date,
    *,
    sessions: Sequence[date] = (),
    max_missed_sessions: int = StalenessConfig().universe_max_missed_sessions,
) -> tuple[bool, str | None]:
    """Apply the universe rules in order; the first failing rule names the exclusion.

    Order: staleness, exchange, series, minimum price (price that actually traded), 20-day
    and 50-day average traded value (raw close x raw volume), history length, ASM/GSM, T2T.
    Staleness counts the market ``sessions`` after the last trade up to the as-of date
    (audit P2-2); an empty calendar cannot show staleness.
    """
    c = config
    if c_last := candidate.last_trade_date:
        missed = missed_sessions_since(sessions, c_last, as_of_date)
        if missed > max_missed_sessions:
            return False, (
                f"Stale: last trade {c_last}, {missed} NSE sessions before {as_of_date} "
                f"(max {max_missed_sessions})"
            )
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


def universe_snapshot_id(
    as_of_date: date, known_at: datetime, config_hash: str, method_version: str
) -> str:
    """Deterministic id from the snapshot's inputs (audit P1-8, D2).

    The same as-of date, knowledge cutoff, config and method give the same id, so rebuilding a
    past snapshot "as known at" its cutoff lands on the id a scan recorded. The cutoff is
    normalised to UTC with microseconds, the precision ``created_at`` is stored with.
    """
    key = "|".join(
        [
            as_of_date.isoformat(),
            known_at.astimezone(UTC).isoformat(timespec="microseconds"),
            config_hash,
            method_version,
        ]
    )
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:10]
    return f"uv_{as_of_date.strftime('%Y%m%d')}_{digest}"


class UniverseBuilder:
    """Builds point-in-time universe snapshots."""

    def __init__(
        self,
        repository: UniverseInputRepository,
        config: UniverseConfig,
        *,
        clock: Clock = utc_now,
        quality_gate: DataQualityGate | None = None,
        include_provisional: bool = False,
        gate_settings: QualityGateConfig | None = None,
        staleness: StalenessConfig | None = None,
    ) -> None:
        self._repo = repository
        # Audit step 2.5: today's PROVISIONAL Kite bar is used only when explicitly allowed.
        self._include_provisional = include_provisional
        self._config = config
        self._clock = clock
        # Optional (audit P0-2). When set, instruments with an unresolved signal-blocking
        # data-quality event become ineligible, judged as known at the snapshot's created_at.
        self._quality_gate = quality_gate
        # Audit P1-8 (D3): the gate's settings (block lifetime, absence threshold) change
        # eligibility, so they belong in the snapshot's config hash when a gate is used.
        self._gate_settings = gate_settings
        # Audit P2-2: staleness in missed NSE sessions (data.quality.staleness).
        if staleness is None:
            staleness = gate_settings.staleness if gate_settings else StalenessConfig()
        self._staleness = staleness

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
        """Short hash of the settings that decide eligibility, with the same canonical
        serialisation as the scan config hash (``compute_config_hash``; audit P2-6)."""
        data: dict[str, object] = {
            "universe": self._config.model_dump(mode="json"),
            "staleness": self._staleness.model_dump(mode="json"),
        }
        if self._gate_settings is not None:
            data["gate"] = self._gate_settings.model_dump(mode="json")
        return compute_config_hash(data)[:8]

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
        snapshot_id = universe_snapshot_id(
            as_of_date, created_at, self._hash_config(), UNIVERSE_METHOD_VERSION
        )

        candidates = self._repo.load_universe_candidates(
            as_of_date,
            created_at,
            sorted(PROVIDER_ADJUSTED_SOURCES),
            include_provisional=self._include_provisional,
        )
        sessions = self._repo.load_sessions(as_of_date, created_at)
        max_missed = self._staleness.universe_max_missed_sessions
        memberships = []
        for cand in candidates:
            eligible, reason = evaluate_eligibility(
                cand,
                self._config,
                as_of_date,
                sessions=sessions,
                max_missed_sessions=max_missed,
            )
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

        evidence = self._repo.survivorship_evidence(
            as_of_date, created_at, as_of_date - timedelta(days=SURVIVORSHIP_WINDOW_DAYS)
        )
        status, detail = derive_survivorship(evidence, as_of_date)
        snapshot = UniverseSnapshot(
            universe_snapshot_id=snapshot_id,
            universe_name="VCP_BASE",
            as_of_date=as_of_date,
            created_at=created_at,
            config_hash=self._hash_config(),
            method_version=UNIVERSE_METHOD_VERSION,
            survivorship_status=status,
            survivorship_detail=detail,
        )

        eligible_count = sum(m.eligible for m in memberships)
        logger.info(
            "Generated %d memberships, %d eligible.",
            len(memberships),
            eligible_count,
        )
        return snapshot, memberships
