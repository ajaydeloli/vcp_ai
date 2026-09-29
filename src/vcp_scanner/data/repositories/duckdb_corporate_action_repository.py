"""DuckDB-backed CorporateActionRepository.

Implements the CorporateActionRepository protocol (DATABASE_SCHEMA §16-17A).
All three tables (corporate_actions, corporate_action_resolution,
corporate_action_adjustments) are bitemporal: append-only with
known_from/known_to lifecycle management.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from vcp_scanner.domain.corporate_actions import (
    CorporateAction,
    CorporateActionAdjustment,
    CorporateActionResolution,
    CorporateActionStatus,
)
from vcp_scanner.domain.enums import CorporateActionType

if TYPE_CHECKING:
    from vcp_scanner.data.storage.duckdb_store import DuckDBStore

logger = logging.getLogger(__name__)


class DuckDBCorporateActionRepository:
    """CorporateActionRepository backed by DuckDB.

    Bitemporal contract:
    - Current rows have ``known_to IS NULL``.
    - Superseded rows have ``known_to`` set to the moment they were replaced.
    - Point-in-time reads filter ``known_from <= known_at``
      and ``(known_to IS NULL OR known_to > known_at)``.
    """

    def __init__(self, store: DuckDBStore) -> None:
        self._store = store

    # ------------------------------------------------------------------
    # Raw corporate actions (one row per source observation)
    # ------------------------------------------------------------------

    def save_corporate_action(
        self,
        action: CorporateAction,
        known_from: datetime,
    ) -> bool:
        """Append a raw corporate action observation from a single provider.

        Idempotent: an observation already held as current (same ``corporate_action_id``
        and source) is left untouched, which preserves its original ``known_from`` (the
        first-seen time the grace-period logic relies on). Returns True if a row was
        inserted, False if it was already known.
        """
        existing = self._store.conn.execute(
            """
            SELECT 1 FROM corporate_actions
            WHERE corporate_action_id = ? AND source = ? AND known_to IS NULL
            LIMIT 1
            """,
            [action.corporate_action_id, action.source],
        ).fetchone()
        if existing is not None:
            return False

        self._store.conn.execute(
            """
            INSERT INTO corporate_actions (
                corporate_action_id, instrument_id, isin,
                action_date, announcement_date, ex_date, record_date,
                action_type,
                ratio_numerator, ratio_denominator, cash_amount,
                old_symbol, new_symbol,
                source, source_record_id,
                created_at, known_from, known_to
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            [
                action.corporate_action_id,
                action.instrument_id,
                action.isin,
                action.action_date,
                action.announcement_date,
                action.ex_date,
                action.record_date,
                action.action_type.value,
                action.ratio_numerator,
                action.ratio_denominator,
                action.cash_amount,
                action.old_symbol,
                action.new_symbol,
                action.source,
                action.source_record_id,
                action.created_at,
                known_from,
            ],
        )
        return True

    def load_corporate_actions(
        self,
        instrument_id: str,
        *,
        known_at: datetime | None = None,
    ) -> list[CorporateAction]:
        """Load current (or as-of known_at) raw actions for an instrument."""
        if known_at is None:
            rows = self._store.conn.execute(
                """
                SELECT corporate_action_id, instrument_id, isin,
                       action_date, announcement_date, ex_date, record_date,
                       action_type,
                       ratio_numerator, ratio_denominator, cash_amount,
                       old_symbol, new_symbol,
                       source, source_record_id, created_at, known_from
                FROM corporate_actions
                WHERE instrument_id = ?
                  AND known_to IS NULL
                ORDER BY ex_date
                """,
                [instrument_id],
            ).fetchall()
        else:
            rows = self._store.conn.execute(
                """
                SELECT corporate_action_id, instrument_id, isin,
                       action_date, announcement_date, ex_date, record_date,
                       action_type,
                       ratio_numerator, ratio_denominator, cash_amount,
                       old_symbol, new_symbol,
                       source, source_record_id, created_at, known_from
                FROM corporate_actions
                WHERE instrument_id = ?
                  AND known_from <= ?
                  AND (known_to IS NULL OR known_to > ?)
                ORDER BY ex_date
                """,
                [instrument_id, known_at, known_at],
            ).fetchall()

        return [self._row_to_corporate_action(r) for r in rows]

    # ------------------------------------------------------------------
    # Resolution (cross-source reconciled records)
    # ------------------------------------------------------------------

    def save_resolution(
        self,
        resolution: CorporateActionResolution,
        known_from: datetime,
    ) -> None:
        """Append a resolution row, closing any prior current row
        for the same (instrument_id, action_type, ex_date)."""
        # Close existing current resolution for same logical key
        self._store.conn.execute(
            """
            UPDATE corporate_action_resolution
            SET known_to = ?
            WHERE instrument_id = ?
              AND action_type = ?
              AND ex_date = ?
              AND known_to IS NULL
            """,
            [
                known_from,
                resolution.instrument_id,
                resolution.action_type.value,
                resolution.ex_date,
            ],
        )

        # Insert new current row
        self._store.conn.execute(
            """
            INSERT INTO corporate_action_resolution (
                resolution_id, instrument_id, isin,
                action_type, ex_date,
                ratio_numerator, ratio_denominator, cash_amount,
                nse_action_id, upstox_action_id,
                status, conflict_fields,
                resolved_by, resolved_at,
                known_from, known_to
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            [
                resolution.resolution_id,
                resolution.instrument_id,
                resolution.isin,
                resolution.action_type.value,
                resolution.ex_date,
                resolution.ratio_numerator,
                resolution.ratio_denominator,
                resolution.cash_amount,
                resolution.nse_action_id,
                resolution.upstox_action_id,
                resolution.status.value,
                resolution.conflict_fields,
                resolution.resolved_by,
                resolution.resolved_at,
                known_from,
            ],
        )

    def load_resolutions(
        self,
        instrument_id: str,
        *,
        known_at: datetime | None = None,
    ) -> list[CorporateActionResolution]:
        """Load current (or as-of known_at) resolutions for an instrument."""
        if known_at is None:
            rows = self._store.conn.execute(
                """
                SELECT resolution_id, instrument_id, isin,
                       action_type, ex_date,
                       ratio_numerator, ratio_denominator, cash_amount,
                       nse_action_id, upstox_action_id,
                       status, conflict_fields,
                       resolved_by, resolved_at
                FROM corporate_action_resolution
                WHERE instrument_id = ?
                  AND known_to IS NULL
                ORDER BY ex_date
                """,
                [instrument_id],
            ).fetchall()
        else:
            rows = self._store.conn.execute(
                """
                SELECT resolution_id, instrument_id, isin,
                       action_type, ex_date,
                       ratio_numerator, ratio_denominator, cash_amount,
                       nse_action_id, upstox_action_id,
                       status, conflict_fields,
                       resolved_by, resolved_at
                FROM corporate_action_resolution
                WHERE instrument_id = ?
                  AND known_from <= ?
                  AND (known_to IS NULL OR known_to > ?)
                ORDER BY ex_date
                """,
                [instrument_id, known_at, known_at],
            ).fetchall()

        return [self._row_to_resolution(r) for r in rows]

    # ------------------------------------------------------------------
    # Adjustment factors (derived from resolutions)
    # ------------------------------------------------------------------

    def save_adjustment(
        self,
        adjustment: CorporateActionAdjustment,
        known_from: datetime,
    ) -> None:
        """Append an adjustment factor row, closing any prior current row
        for the same (instrument_id, effective_date)."""
        # Close existing current adjustment for same logical key
        self._store.conn.execute(
            """
            UPDATE corporate_action_adjustments
            SET known_to = ?
            WHERE instrument_id = ?
              AND effective_date = ?
              AND known_to IS NULL
            """,
            [
                known_from,
                adjustment.instrument_id,
                adjustment.effective_date,
            ],
        )

        # Insert new current row
        self._store.conn.execute(
            """
            INSERT INTO corporate_action_adjustments (
                resolution_id, instrument_id,
                effective_date,
                price_factor, volume_factor,
                cumulative_price_factor, cumulative_volume_factor,
                source, calculation_version,
                known_from, known_to
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            [
                adjustment.resolution_id,
                adjustment.instrument_id,
                adjustment.effective_date,
                adjustment.price_factor,
                adjustment.volume_factor,
                adjustment.cumulative_price_factor,
                adjustment.cumulative_volume_factor,
                adjustment.source,
                adjustment.calculation_version,
                known_from,
            ],
        )

    def replace_adjustments(
        self,
        instrument_id: str,
        adjustments: list[CorporateActionAdjustment],
        known_from: datetime,
    ) -> None:
        """Make ``adjustments`` the complete current factor set for an instrument.

        Every currently-open factor row is closed first, so a factor whose resolution
        became a PROVIDER_CONFLICT (or vanished) is retired rather than left behind.
        Closed rows stay in the table for as-of reads.
        """
        self._store.conn.execute(
            """
            UPDATE corporate_action_adjustments
            SET known_to = ?
            WHERE instrument_id = ? AND known_to IS NULL
            """,
            [known_from, instrument_id],
        )
        for adjustment in adjustments:
            self.save_adjustment(adjustment, known_from=known_from)

    def load_adjustments(
        self,
        instrument_id: str,
        *,
        known_at: datetime | None = None,
    ) -> list[CorporateActionAdjustment]:
        """Load current (or as-of known_at) adjustments for an instrument,
        ordered by effective_date ascending."""
        if known_at is None:
            rows = self._store.conn.execute(
                """
                SELECT resolution_id, instrument_id,
                       effective_date,
                       price_factor, volume_factor,
                       cumulative_price_factor, cumulative_volume_factor,
                       source, calculation_version
                FROM corporate_action_adjustments
                WHERE instrument_id = ?
                  AND known_to IS NULL
                ORDER BY effective_date
                """,
                [instrument_id],
            ).fetchall()
        else:
            rows = self._store.conn.execute(
                """
                SELECT resolution_id, instrument_id,
                       effective_date,
                       price_factor, volume_factor,
                       cumulative_price_factor, cumulative_volume_factor,
                       source, calculation_version
                FROM corporate_action_adjustments
                WHERE instrument_id = ?
                  AND known_from <= ?
                  AND (known_to IS NULL OR known_to > ?)
                ORDER BY effective_date
                """,
                [instrument_id, known_at, known_at],
            ).fetchall()

        return [self._row_to_adjustment(r) for r in rows]

    # ------------------------------------------------------------------
    # Private row mappers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_corporate_action(row: tuple[Any, ...]) -> CorporateAction:
        (
            ca_id,
            iid,
            isin,
            action_date,
            announcement_date,
            ex_date,
            record_date,
            action_type,
            ratio_num,
            ratio_den,
            cash,
            old_sym,
            new_sym,
            source,
            source_record_id,
            created_at,
            known_from,
        ) = row
        return CorporateAction(
            corporate_action_id=ca_id,
            instrument_id=iid,
            isin=isin,
            action_date=action_date,
            announcement_date=announcement_date,
            ex_date=ex_date,
            record_date=record_date,
            action_type=CorporateActionType(action_type),
            ratio_numerator=ratio_num,
            ratio_denominator=ratio_den,
            cash_amount=cash,
            old_symbol=old_sym,
            new_symbol=new_sym,
            source=source,
            source_record_id=source_record_id,
            created_at=created_at,
            # First time we saw it: drives the secondary-source grace period.
            ingested_at=known_from,
        )

    @staticmethod
    def _row_to_resolution(row: tuple[Any, ...]) -> CorporateActionResolution:
        (
            res_id,
            iid,
            isin,
            action_type,
            ex_date,
            ratio_num,
            ratio_den,
            cash,
            nse_id,
            upstox_id,
            status,
            conflict_fields,
            resolved_by,
            resolved_at,
        ) = row
        return CorporateActionResolution(
            resolution_id=res_id,
            instrument_id=iid,
            isin=isin,
            action_type=CorporateActionType(action_type),
            ex_date=ex_date,
            ratio_numerator=ratio_num,
            ratio_denominator=ratio_den,
            cash_amount=cash,
            nse_action_id=nse_id,
            upstox_action_id=upstox_id,
            status=CorporateActionStatus(status),
            conflict_fields=conflict_fields,
            resolved_by=resolved_by,
            resolved_at=resolved_at,
        )

    @staticmethod
    def _row_to_adjustment(row: tuple[Any, ...]) -> CorporateActionAdjustment:
        (
            res_id,
            iid,
            effective_date,
            pf,
            vf,
            cpf,
            cvf,
            source,
            calc_ver,
        ) = row
        return CorporateActionAdjustment(
            resolution_id=res_id,
            instrument_id=iid,
            effective_date=effective_date,
            price_factor=float(pf),
            volume_factor=float(vf),
            cumulative_price_factor=float(cpf),
            cumulative_volume_factor=float(cvf),
            source=source,
            calculation_version=calc_ver,
        )
