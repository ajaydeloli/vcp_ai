"""Universe calculation and generation (Phase 3)."""

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
from vcp_scanner.domain.universe import UniverseMembership, UniverseSnapshot
from vcp_scanner.infrastructure.clock import Clock, utc_now

if TYPE_CHECKING:
    from vcp_scanner.data.repositories.base import DataQualityGate
    from vcp_scanner.data.storage.duckdb_store import DuckDBStore

logger = logging.getLogger(__name__)


class UniverseBuilder:
    """Builds point-in-time universe snapshots."""

    def __init__(
        self,
        store: DuckDBStore,
        config: UniverseConfig,
        *,
        clock: Clock = utc_now,
        quality_gate: DataQualityGate | None = None,
    ) -> None:
        self._store = store
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
        row = self._store.conn.execute(
            """
            SELECT COUNT(*)
            FROM security_master_history
            WHERE delisting_date IS NOT NULL
              AND known_from <= ?
              AND (known_to IS NULL OR known_to > ?)
            """,
            [known_at, known_at],
        ).fetchone()
        delisted_known = int(row[0]) if row else 0
        if delisted_known == 0:
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
        logger.info(f"Building universe snapshot for {as_of_date}...")
        created_at = known_at if known_at is not None else self._clock()
        if created_at.tzinfo is None:
            raise ValueError("known_at must be timezone-aware")
        snapshot_id = f"uv_{as_of_date.strftime('%Y%m%d')}_{uuid.uuid4().hex[:6]}"

        # This query calculates the 20- and 50-day average traded value and the last close price.
        # We enforce point-in-time correctness by bounding the trade_date.
        # Because we're looking at historical point in time, we use `close_raw` and `volume_raw`.

        query = """
        WITH windowed AS (
            SELECT
                instrument_id,
                trade_date,
                close_raw,
                (close_raw * volume_raw) as traded_value,
                ROW_NUMBER() OVER(PARTITION BY instrument_id ORDER BY trade_date DESC) as rn
            FROM daily_prices
            WHERE trade_date <= ?
              AND known_from <= ?
              AND (known_to IS NULL OR known_to > ?)
        ),
        recent_stats AS (
            SELECT
                instrument_id,
                MAX(CASE WHEN rn = 1 THEN close_raw END) as last_price,
                MAX(CASE WHEN rn = 1 THEN trade_date END) as last_trade_date,
                AVG(CASE WHEN rn <= 20 THEN traded_value END) as avg_traded_value_20d,
                AVG(CASE WHEN rn <= 50 THEN traded_value END) as avg_traded_value_50d,
                -- rn counts every bar known on or before as_of_date, so MAX(rn) is the
                -- full history length that UniverseConfig.min_history_days is judged against.
                MAX(rn) as days_history
            FROM windowed
            GROUP BY instrument_id
        ),
        -- Get the security master status as of as_of_date
        sec_master AS (
            SELECT
                instrument_id,
                series,
                exchange
            FROM security_master_history
            WHERE valid_from <= ?
              AND (valid_to IS NULL OR valid_to >= ?)
              AND known_from <= ?
              AND (known_to IS NULL OR known_to > ?)
            -- Use the most recent entry if there are overlaps
            QUALIFY ROW_NUMBER() OVER(
                PARTITION BY instrument_id ORDER BY valid_from DESC, known_from DESC
            ) = 1
        ),
        -- Get active surveillance flags
        surv_flags AS (
            SELECT
                instrument_id,
                MAX(CASE WHEN flag_type = 'ASM' THEN 'YES' END) as asm_flag,
                MAX(CASE WHEN flag_type = 'GSM' THEN 'YES' END) as gsm_flag,
                MAX(CASE WHEN flag_type = 'T2T' THEN 'YES' END) as t2t_flag
            FROM surveillance_flags_history
            WHERE valid_from <= ?
              AND (valid_to IS NULL OR valid_to >= ?)
              AND known_from <= ?
              AND (known_to IS NULL OR known_to > ?)
            GROUP BY instrument_id
        )
        SELECT
            r.instrument_id,
            r.last_price,
            r.avg_traded_value_20d,
            r.avg_traded_value_50d,
            r.days_history,
            r.last_trade_date,
            sm.series,
            sm.exchange,
            sf.asm_flag,
            sf.gsm_flag,
            sf.t2t_flag
        FROM recent_stats r
        LEFT JOIN sec_master sm ON r.instrument_id = sm.instrument_id
        LEFT JOIN surv_flags sf ON r.instrument_id = sf.instrument_id
        """

        # Execute query passing as_of_date and created_at correctly
        rows = self._store.conn.execute(
            query,
            [
                as_of_date,
                created_at,
                created_at,  # windowed
                as_of_date,
                as_of_date,
                created_at,
                created_at,  # sec_master
                as_of_date,
                as_of_date,
                created_at,
                created_at,  # surv_flags
            ],
        ).fetchall()

        memberships = []
        for row in rows:
            (
                instrument_id,
                last_price,
                avg_traded_value,
                avg_traded_value_50d,
                days_history,
                last_trade_date,
                series,
                exchange,
                asm_flag,
                gsm_flag,
                t2t_flag,
            ) = row

            eligible = True
            exclusion_reason = None

            # Staleness gate: if the most recent price is more than max_staleness_days
            # calendar days before as_of_date, treat the instrument as no longer trading.
            max_stale = self._config.max_staleness_days
            if last_trade_date is not None:
                stale_days = (as_of_date - last_trade_date).days
                if stale_days > max_stale:
                    eligible = False
                    exclusion_reason = (
                        f"Stale: last trade {last_trade_date} is {stale_days}d before {as_of_date}"
                    )

            if eligible:
                if exchange != self._config.exchange:
                    eligible = False
                    exclusion_reason = f"Exchange {exchange} != {self._config.exchange}"
                elif series not in self._config.eligible_series:
                    eligible = False
                    exclusion_reason = f"Series {series} not in {self._config.eligible_series}"
                elif last_price is None or last_price < self._config.min_close_price:
                    eligible = False
                    exclusion_reason = f"Price {last_price} < {self._config.min_close_price}"
                elif (
                    avg_traded_value is None
                    or avg_traded_value < self._config.min_daily_turnover_inr
                ):
                    eligible = False
                    exclusion_reason = (
                        f"Traded value {avg_traded_value} < {self._config.min_daily_turnover_inr}"
                    )
                elif (
                    avg_traded_value_50d is None
                    or avg_traded_value_50d < self._config.min_avg_traded_value_50d_inr
                ):
                    eligible = False
                    exclusion_reason = (
                        f"50d traded value {avg_traded_value_50d} < "
                        f"{self._config.min_avg_traded_value_50d_inr}"
                    )
                elif days_history is None or days_history < self._config.min_history_days:
                    eligible = False
                    exclusion_reason = (
                        f"History {days_history or 0} bars < {self._config.min_history_days}"
                    )
                elif self._config.exclude_asm_gsm and (asm_flag == "YES" or gsm_flag == "YES"):
                    eligible = False
                    exclusion_reason = "ASM/GSM flag active"
                elif self._config.exclude_trade_to_trade and t2t_flag == "YES":
                    eligible = False
                    exclusion_reason = "T2T flag active"

            memberships.append(
                UniverseMembership(
                    universe_snapshot_id=snapshot_id,
                    instrument_id=instrument_id,
                    eligible=eligible,
                    exclusion_reason=exclusion_reason,
                    avg_traded_value=float(avg_traded_value) if avg_traded_value else None,
                    price=float(last_price) if last_price else None,
                    instrument_type="EQUITY",
                    series=series,
                    asm_flag=asm_flag,
                    gsm_flag=gsm_flag,
                    t2t_flag=t2t_flag,
                )
            )

        memberships = self._apply_quality_gate(memberships, as_of_date, created_at)

        snapshot = UniverseSnapshot(
            universe_snapshot_id=snapshot_id,
            universe_name="VCP_BASE",
            as_of_date=as_of_date,
            created_at=created_at,
            config_hash=self._hash_config(),
            method_version="1.1",
            survivorship_status=self._survivorship_status(created_at),
        )

        eligible_count = sum(m.eligible for m in memberships)
        logger.info(
            "Generated %d memberships, %d eligible.",
            len(memberships),
            eligible_count,
        )
        return snapshot, memberships
