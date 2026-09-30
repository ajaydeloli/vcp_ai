"""Data-quality scanner: runs the gap safety net and syncs corporate-action conflicts.

DATA_SPECIFICATION 18A layers: NSE + Upstox corporate actions are reconciled, and a gap
detector on raw prices is the safety net for actions both feeds missed. Before P0-2 the gap
detector was never invoked and no event was stored. This service is the one place that

* runs the gap detector over an instrument's raw bars and current reconciled actions,
* derives CORPORATE_ACTION_UNRESOLVED events from current PROVIDER_CONFLICT resolutions, and
* persists both with ``sync_events``, so re-runs are idempotent and events clear themselves
  when the cause disappears (an action is added, a conflict is confirmed).

It never edits prices and never "fixes" a gap (18A).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from vcp_scanner.data.quality.events import corporate_action_events, identity_events
from vcp_scanner.data.reconciliation.gap_detector import GapDetector
from vcp_scanner.data.repositories.base import CorporateActionRepository
from vcp_scanner.data.repositories.duckdb_identity_repository import DuckDBIdentityRepository
from vcp_scanner.data.repositories.duckdb_market_repository import DuckDBMarketDataRepository
from vcp_scanner.data.repositories.duckdb_quality_repository import DuckDBDataQualityRepository
from vcp_scanner.domain.enums import DataQualityFlag

_FAR_PAST = date(1900, 1, 1)
_FAR_FUTURE = date(2999, 12, 31)


@dataclass(frozen=True, slots=True)
class ScanSummary:
    instruments: int = 0
    gap_events: int = 0  # unexplained gaps currently detected
    conflict_events: int = 0  # unresolved corporate-action conflicts currently detected
    identity_events: int = 0  # ISIN changes no split explains (warnings, audit step 2.2)
    blocking: int = 0  # of those, how many block signals
    opened: int = 0  # events seen for the first time in this scan
    resolved: int = 0  # events closed because their condition cleared


class QualityScanner:
    def __init__(
        self,
        market: DuckDBMarketDataRepository,
        corporate_actions: CorporateActionRepository,
        quality: DuckDBDataQualityRepository,
        gap_detector: GapDetector,
        *,
        conflict_blocks_signals: bool = True,
        identity: DuckDBIdentityRepository | None = None,
    ) -> None:
        self._market = market
        self._ca = corporate_actions
        self._quality = quality
        self._gaps = gap_detector
        self._conflict_blocks = conflict_blocks_signals
        self._identity = identity

    def scan(self, instrument_ids: Sequence[str], *, detected_at: datetime) -> ScanSummary:
        """Scan each instrument independently. ``detected_at`` is injected (no clock reads)."""
        totals = dict.fromkeys(ScanSummary.__slots__, 0)
        for iid in instrument_ids:
            one = self.scan_instrument(iid, detected_at=detected_at)
            for name in totals:
                totals[name] += getattr(one, name)
        totals["instruments"] = len(instrument_ids)
        return ScanSummary(**totals)

    def scan_instrument(self, instrument_id: str, *, detected_at: datetime) -> ScanSummary:
        candles = self._market.load_daily(instrument_id, _FAR_PAST, _FAR_FUTURE)
        resolutions = self._ca.load_resolutions(instrument_id)

        gap_events = self._gaps.detect(candles, resolutions, detected_at)
        conflict_events = corporate_action_events(
            instrument_id,
            resolutions,
            detected_at,
            conflict_blocks_signals=self._conflict_blocks,
        )
        gap_sync = self._quality.sync_events(
            instrument_id, DataQualityFlag.UNEXPLAINED_GAP, gap_events, at=detected_at
        )
        conflict_sync = self._quality.sync_events(
            instrument_id,
            DataQualityFlag.CORPORATE_ACTION_UNRESOLVED,
            conflict_events,
            at=detected_at,
        )
        id_events = []
        opened = gap_sync.opened + conflict_sync.opened
        resolved = gap_sync.resolved + conflict_sync.resolved
        if self._identity is not None:
            id_events = identity_events(
                instrument_id,
                self._identity.load_periods(instrument_id),
                resolutions,
                detected_at,
            )
            id_sync = self._quality.sync_events(
                instrument_id, DataQualityFlag.SYMBOL_MAPPING_UNCERTAIN, id_events, at=detected_at
            )
            opened += id_sync.opened
            resolved += id_sync.resolved
        return ScanSummary(
            instruments=1,
            gap_events=len(gap_events),
            conflict_events=len(conflict_events),
            identity_events=len(id_events),
            blocking=sum(e.blocks_signal for e in [*gap_events, *conflict_events, *id_events]),
            opened=opened,
            resolved=resolved,
        )
