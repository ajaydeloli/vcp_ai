"""DuckDB-backed Universe Repository."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date, datetime
from typing import TYPE_CHECKING

from vcp_scanner.domain.universe import (
    SurvivorshipEvidence,
    UniverseCandidate,
    UniverseMembership,
    UniverseSnapshot,
)

if TYPE_CHECKING:
    from vcp_scanner.data.storage.duckdb_store import DuckDBStore

logger = logging.getLogger(__name__)


class DuckDBUniverseRepository:
    """UniverseRepository backed by DuckDB."""

    def __init__(self, store: DuckDBStore) -> None:
        self._store = store

    def save_snapshot(
        self,
        snapshot: UniverseSnapshot,
        memberships: list[UniverseMembership],
    ) -> None:
        """Persist a universe snapshot and its memberships.

        Snapshot ids are deterministic (audit P1-8): rebuilding with the same as-of date,
        cutoff and config yields the same id, and the rebuild replaces the stored rows
        instead of failing on the key or forking a second snapshot.
        """
        sid = snapshot.universe_snapshot_id
        self._store.conn.execute(
            "DELETE FROM universe_memberships WHERE universe_snapshot_id = ?", [sid]
        )
        self._store.conn.execute(
            "DELETE FROM universe_snapshots WHERE universe_snapshot_id = ?", [sid]
        )
        # Insert snapshot
        self._store.conn.execute(
            """
            INSERT INTO universe_snapshots (
                universe_snapshot_id, universe_name, as_of_date, created_at,
                config_hash, method_version, survivorship_status, survivorship_detail
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                snapshot.universe_snapshot_id,
                snapshot.universe_name,
                snapshot.as_of_date,
                snapshot.created_at,
                snapshot.config_hash,
                snapshot.method_version,
                snapshot.survivorship_status.value,
                snapshot.survivorship_detail,
            ],
        )

        # Insert memberships
        if memberships:
            # We use executemany for bulk insert
            rows = [
                (
                    m.universe_snapshot_id,
                    m.instrument_id,
                    m.eligible,
                    m.exclusion_reason,
                    m.avg_traded_value,
                    m.price,
                    m.instrument_type,
                    m.series,
                    m.asm_flag,
                    m.gsm_flag,
                    m.t2t_flag,
                )
                for m in memberships
            ]
            self._store.conn.executemany(
                """
                INSERT INTO universe_memberships (
                    universe_snapshot_id, instrument_id, eligible, exclusion_reason,
                    avg_traded_value, price, instrument_type, series,
                    asm_flag, gsm_flag, t2t_flag
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def latest_snapshot_id(self, as_of_date: date) -> str | None:
        """The most recently created snapshot for ``as_of_date`` (``created_at`` = cutoff)."""
        row = self._store.conn.execute(
            """
            SELECT universe_snapshot_id
            FROM universe_snapshots
            WHERE as_of_date = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            [as_of_date],
        ).fetchone()
        return str(row[0]) if row else None

    def snapshot_as_of(self, snapshot_id: str) -> date | None:
        """The as-of date of a stored snapshot, or None if there is no such snapshot."""
        row = self._store.conn.execute(
            "SELECT as_of_date FROM universe_snapshots WHERE universe_snapshot_id = ?",
            [snapshot_id],
        ).fetchone()
        return row[0] if row else None

    def load_snapshot(self, as_of_date: date, snapshot_id: str | None = None) -> list[str]:
        """Load eligible instrument IDs of a universe snapshot for ``as_of_date``.

        ``snapshot_id`` names the snapshot explicitly (audit P1-8: a scan records which one it
        used, and a reproduction must use that one). Without it the most recently created
        snapshot for the date is used.
        """
        if snapshot_id is None:
            snapshot_id = self.latest_snapshot_id(as_of_date)
            if snapshot_id is None:
                return []
        elif self.snapshot_as_of(snapshot_id) != as_of_date:
            raise ValueError(f"universe snapshot {snapshot_id} is not a snapshot for {as_of_date}")

        rows = self._store.conn.execute(
            """
            SELECT instrument_id
            FROM universe_memberships
            WHERE universe_snapshot_id = ?
              AND eligible = TRUE
            """,
            [snapshot_id],
        ).fetchall()

        return [r[0] for r in rows]

    # ------------------------------------------------------------------ universe inputs

    def load_universe_candidates(
        self,
        as_of_date: date,
        known_at: datetime,
        provider_adjusted_sources: Sequence[str],
        *,
        include_provisional: bool = False,
    ) -> list[UniverseCandidate]:
        """Point-in-time statistics per instrument (``UniverseInputRepository``).

        Only rows known at ``known_at`` (and not superseded by then) with trade dates on or
        before ``as_of_date`` are used. Liquidity uses raw close x raw volume. The last price
        of a provider-adjusted source is divided by the split/bonus factors that provider had
        already applied (audit P0-1), so it is the price that actually traded.
        """
        # Audit step 2.5: today's PROVISIONAL Kite bar counts only when asked for.
        provisional_filter = "" if include_provisional else "AND data_status <> 'PROVISIONAL'"
        query = f"""
        WITH windowed AS (
            SELECT
                instrument_id,
                trade_date,
                close_raw,
                primary_provider,
                CAST(timezone('Asia/Kolkata', known_from) AS DATE) AS fetched_date,
                -- close x volume is unchanged by a split/bonus adjustment (price and volume
                -- factors cancel), so it is the true traded value for raw and
                -- provider-adjusted bars alike.
                (close_raw * volume_raw) as traded_value,
                ROW_NUMBER() OVER(PARTITION BY instrument_id ORDER BY trade_date DESC) as rn
            FROM daily_prices
            WHERE trade_date <= ?
              AND known_from <= ?
              AND (known_to IS NULL OR known_to > ?)
              {provisional_filter}
        ),
        -- Audit P0-1: a provider-adjusted bar (Kite) was already scaled by every split/bonus
        -- with trade_date < ex_date <= its fetch date. Undo those factors to recover the price
        -- that actually traded, which is what the minimum-price rule is about.
        provider_undo AS (
            SELECT
                w.instrument_id,
                EXP(SUM(LN(CAST(a.price_factor AS DOUBLE)))) AS applied_pf
            FROM windowed w
            JOIN corporate_action_adjustments a
              ON a.instrument_id = w.instrument_id
             AND a.effective_date > w.trade_date
             AND a.effective_date <= w.fetched_date
             AND a.known_from <= ?
             AND (a.known_to IS NULL OR a.known_to > ?)
            WHERE w.rn = 1
              AND list_contains(CAST(? AS VARCHAR[]), upper(w.primary_provider))
            GROUP BY w.instrument_id
        ),
        recent_stats AS (
            SELECT
                w.instrument_id,
                MAX(CASE WHEN rn = 1 THEN close_raw / COALESCE(u.applied_pf, 1.0) END)
                    as last_price,
                MAX(CASE WHEN rn = 1 THEN trade_date END) as last_trade_date,
                AVG(CASE WHEN rn <= 20 THEN traded_value END) as avg_traded_value_20d,
                AVG(CASE WHEN rn <= 50 THEN traded_value END) as avg_traded_value_50d,
                -- rn counts every bar known on or before as_of_date, so MAX(rn) is the
                -- full history length that UniverseConfig.min_history_days is judged against.
                MAX(rn) as days_history
            FROM windowed w
            LEFT JOIN provider_undo u ON u.instrument_id = w.instrument_id
            GROUP BY w.instrument_id
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
        -- Audit P0-4: the series each stock actually traded in on its last session on/before
        -- as_of_date, from that day's NSE bhavcopy (EQ preferred when a stock had two rows).
        -- Today's EQUITY_L series back-dated over history is only the fallback for
        -- instruments without bhavcopy data.
        day_series AS (
            SELECT instrument_id, series
            FROM daily_series
            WHERE trade_date <= ?
              AND recorded_at <= ?
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY instrument_id
                ORDER BY trade_date DESC,
                         CASE series WHEN 'EQ' THEN 0 WHEN 'BE' THEN 1 WHEN 'BZ' THEN 2
                                     WHEN 'SM' THEN 3 ELSE 4 END
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
            COALESCE(ds.series, sm.series) AS series,
            CASE WHEN ds.series IS NOT NULL THEN 'NSE' ELSE sm.exchange END AS exchange,
            sf.asm_flag,
            sf.gsm_flag,
            -- Trade-to-trade means the BE/BZ series that day; the bhavcopy gives its history.
            CASE
                WHEN ds.series IS NULL THEN sf.t2t_flag
                WHEN ds.series IN ('BE', 'BZ') THEN 'YES'
            END AS t2t_flag
        FROM recent_stats r
        LEFT JOIN sec_master sm ON r.instrument_id = sm.instrument_id
        LEFT JOIN day_series ds ON r.instrument_id = ds.instrument_id
        LEFT JOIN surv_flags sf ON r.instrument_id = sf.instrument_id
        """
        rows = self._store.conn.execute(
            query,
            [
                as_of_date,
                known_at,
                known_at,  # windowed
                known_at,
                known_at,
                sorted(s.upper() for s in provider_adjusted_sources),  # provider_undo
                as_of_date,
                as_of_date,
                known_at,
                known_at,  # sec_master
                as_of_date,
                known_at,  # day_series
                as_of_date,
                as_of_date,
                known_at,
                known_at,  # surv_flags
            ],
        ).fetchall()
        return [
            UniverseCandidate(
                instrument_id=row[0],
                last_price=row[1],
                avg_traded_value_20d=row[2],
                avg_traded_value_50d=row[3],
                days_history=row[4],
                last_trade_date=row[5],
                series=row[6],
                exchange=row[7],
                asm_flag=row[8],
                gsm_flag=row[9],
                t2t_flag=row[10],
            )
            for row in rows
        ]

    def count_known_delistings(self, known_at: datetime) -> int:
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
        return int(row[0]) if row else 0

    def survivorship_evidence(
        self, as_of_date: date, known_at: datetime, window_start: date
    ) -> SurvivorshipEvidence:
        """What the data can prove about a snapshot's completeness (audit P0-4)."""
        conn = self._store.conn
        has_bhavcopy = conn.execute(
            "SELECT min(trade_date) FROM bhavcopy_files WHERE status = 'OK' AND recorded_at <= ?",
            [known_at],
        ).fetchone()
        first_price_day = has_bhavcopy[0] if has_bhavcopy else None
        missing: int | None = None
        if first_price_day is not None:
            settled = conn.execute(
                """
                SELECT count(DISTINCT trade_date) FROM bhavcopy_files
                WHERE trade_date BETWEEN ? AND ? AND recorded_at <= ?
                  AND status IN ('OK', 'NO_SESSION')
                """,
                [window_start, as_of_date, known_at],
            ).fetchone()
            days = (as_of_date - window_start).days + 1
            missing = days - int(settled[0] if settled else 0)
        starts: dict[str, date | None] = {}
        for flag in ("ASM", "GSM"):
            row = conn.execute(
                "SELECT min(collected_on) FROM surveillance_collections"
                " WHERE flag_type = ? AND recorded_at <= ?",
                [flag, known_at],
            ).fetchone()
            starts[flag] = row[0] if row else None
        on_day = frozenset(
            str(r[0])
            for r in conn.execute(
                "SELECT flag_type FROM surveillance_collections"
                " WHERE collected_on = ? AND recorded_at <= ?",
                [as_of_date, known_at],
            ).fetchall()
        )
        return SurvivorshipEvidence(
            window_start=window_start,
            missing_price_days=missing,
            first_price_day=first_price_day,
            flag_history_start=starts,
            delistings=self.count_known_delistings(known_at),
            flag_collected_on=on_day,
        )
