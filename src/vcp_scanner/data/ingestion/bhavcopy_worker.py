"""Daily-file ingestion of NSE bhavcopy bars (audit step 2.3; DATA_SPECIFICATION 21.2).

One file per calendar day, in date order:

1. Skip a day the manifest already has as OK (file cached) or NO_SESSION, unless ``refresh``.
2. Download (or read the cache). A 404 is classified: NO_SESSION is recorded and the run
   moves on; PENDING or any ERROR **stops the run**, because identity must be resolved in
   date order and a later day cannot be written before an earlier one exists.
3. Parse, resolve identity (or replay it on a re-run), write the bars set-based
   (:meth:`DuckDBMarketDataRepository.save_final_daily`), then mark the day OK. The manifest
   is written last, so an interrupted day is simply redone.

When one instrument has rows in two series on the same day, the bar from the most regular
series wins (EQ, then BE, BZ, SM, ST) and the choice is stored in ``selection_reason``.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from vcp_scanner.data.ingestion.bhavcopy_identity import BhavcopyIdentityResolver, DayIdentity
from vcp_scanner.data.providers._time import IST
from vcp_scanner.data.providers.nse_bhavcopy import (
    NseBhavcopyProvider,
    bhavcopy_format,
    bhavcopy_url,
    classify_missing,
    parse_bhavcopy,
)
from vcp_scanner.data.repositories.duckdb_bhavcopy_repository import DuckDBBhavcopyRepository
from vcp_scanner.data.repositories.duckdb_identity_repository import DuckDBIdentityRepository
from vcp_scanner.data.repositories.duckdb_market_repository import (
    DuckDBMarketDataRepository,
    FinalDailyBar,
)
from vcp_scanner.data.schema import IngestionRunRow
from vcp_scanner.domain.bhavcopy import (
    BhavcopyFileRecord,
    BhavcopyFileStatus,
    BhavcopyRow,
    RejectedBhavcopyRow,
)
from vcp_scanner.domain.errors import VCPScannerError
from vcp_scanner.domain.market import FINAL_PRICE_SOURCE

logger = logging.getLogger(__name__)

SERIES_PRIORITY: tuple[str, ...] = ("EQ", "BE", "BZ", "SM", "ST")


@dataclass
class BhavcopyIngestSummary:
    run_id: str
    days_ok: int = 0
    days_no_session: int = 0
    days_skipped: int = 0  # already ingested
    stopped_at: date | None = None
    stop_status: BhavcopyFileStatus | None = None
    stop_detail: str | None = None
    rows_received: int = 0
    rows_rejected: int = 0
    bars_written: int = 0
    bars_unchanged: int = 0
    superseded: Counter[str] = field(default_factory=Counter)
    new_instruments: int = 0
    identifier_changes: int = 0

    @property
    def status(self) -> str:
        if self.stop_status is BhavcopyFileStatus.ERROR:
            return "FAILED" if self.days_ok == 0 else "PARTIAL"
        return "SUCCESS"


def pick_bars(rows: list[BhavcopyRow], day: DayIdentity) -> list[FinalDailyBar]:
    """One bar per instrument: the row from the highest-priority series."""
    rank = {s: i for i, s in enumerate(SERIES_PRIORITY)}
    best: dict[str, BhavcopyRow] = {}
    for row in rows:
        iid = day.mapping[(row.symbol, row.series)]
        held = best.get(iid)
        if held is None or rank.get(row.series, 99) < rank.get(held.series, 99):
            best[iid] = row
    return [
        FinalDailyBar(iid, r.trade_date, r.open, r.high, r.low, r.close, r.volume, r.series)
        for iid, r in best.items()
    ]


class BhavcopyIngestionWorker:
    def __init__(
        self,
        provider: NseBhavcopyProvider,
        market: DuckDBMarketDataRepository,
        manifest: DuckDBBhavcopyRepository,
        identity: DuckDBIdentityRepository,
        *,
        clock: Callable[[], datetime],
        code_version: str | None = None,
    ) -> None:
        self._provider = provider
        self._market = market
        self._manifest = manifest
        self._identity = identity
        self._clock = clock
        self._code_version = code_version
        self._holidays: set[date] | None = None
        self._holidays_loaded = False

    def run(self, start: date, end: date, *, refresh: bool = False) -> BhavcopyIngestSummary:
        started = self._clock()
        today = started.astimezone(IST).date()
        end = min(end, today)
        summary = BhavcopyIngestSummary(run_id=f"bhav-{uuid.uuid4()}")
        resolver = BhavcopyIdentityResolver(self._identity.load_state())
        known = self._manifest.latest_files(start, end)

        day = start
        while day <= end:
            try:
                stop = self._one_day(day, today, known.get(day), resolver, summary, refresh)
            except VCPScannerError as exc:
                self._record(day, BhavcopyFileStatus.ERROR, detail=str(exc))
                stop = (BhavcopyFileStatus.ERROR, str(exc))
            if stop is not None:
                summary.stopped_at, (summary.stop_status, summary.stop_detail) = day, stop
                break
            day += timedelta(days=1)

        self._market.save_ingestion_run(
            IngestionRunRow(
                ingestion_run_id=summary.run_id,
                provider=FINAL_PRICE_SOURCE,
                dataset="daily_prices",
                started_at=started,
                completed_at=self._clock(),
                status=summary.status,
                requested_start=start,
                requested_end=end,
                records_received=summary.rows_received,
                records_written=summary.bars_written,
                records_rejected=summary.rows_rejected,
                error_count=int(summary.stop_status is BhavcopyFileStatus.ERROR),
                code_version=self._code_version,
                source_metadata_json=json.dumps(
                    {
                        "days_ok": summary.days_ok,
                        "days_no_session": summary.days_no_session,
                        "days_skipped": summary.days_skipped,
                        "superseded": dict(summary.superseded),
                        "stopped_at": _text(summary.stopped_at),
                        "stop_status": _text(summary.stop_status),
                    }
                ),
            )
        )
        return summary

    # ------------------------------------------------------------------

    def _one_day(
        self,
        day: date,
        today: date,
        known: BhavcopyFileRecord | None,
        resolver: BhavcopyIdentityResolver,
        summary: BhavcopyIngestSummary,
        refresh: bool,
    ) -> tuple[BhavcopyFileStatus, str] | None:
        if known is not None and not refresh:
            if known.status is BhavcopyFileStatus.NO_SESSION:
                summary.days_skipped += 1
                return None
            if known.status is BhavcopyFileStatus.OK and self._provider.cache_path(day).exists():
                summary.days_skipped += 1
                return None

        download = self._provider.fetch_day(day, refresh=refresh)
        if download.status == "NOT_FOUND":
            status = classify_missing(day, today, self._holiday_list(day, today))
            self._record(day, status, detail="HTTP 404")
            if status is BhavcopyFileStatus.NO_SESSION:
                summary.days_no_session += 1
                return None
            return status, "not published yet"

        assert download.payload is not None
        parsed = parse_bhavcopy(day, download.payload)

        state = resolver.state
        if state.last_resolved is not None and day <= state.last_resolved:
            identity = resolver.replay_day(day, parsed.rows, self._identity.load_day_mapping(day))
        else:
            identity = resolver.resolve_day(day, parsed.rows)
        now = self._clock()
        self._identity.apply(identity, recorded_at=now)

        saved = self._market.save_final_daily(
            pick_bars(parsed.rows, identity), known_from=now, source_run_id=summary.run_id
        )
        self._record(
            day,
            BhavcopyFileStatus.OK,
            sha256=download.sha256,
            row_count=len(parsed.rows),
            rejected=len(parsed.rejected),
            cache_path=None if download.cache_path is None else str(download.cache_path),
            detail=_detail(parsed.rejected, parsed.skipped_series),
        )
        summary.days_ok += 1
        summary.rows_received += len(parsed.rows)
        summary.rows_rejected += len(parsed.rejected)
        summary.bars_written += saved.written
        summary.bars_unchanged += saved.unchanged
        summary.superseded.update(saved.superseded)
        summary.new_instruments += len(identity.new_instruments)
        summary.identifier_changes += sum(p.change_reason != "FIRST_SEEN" for p in identity.opened)
        return None

    def _holiday_list(self, day: date, today: date) -> set[date] | None:
        """NSE's holiday list, fetched once and only when a recent 404 needs it."""
        if (today - day).days >= 3 or self._holidays_loaded:
            return self._holidays
        self._holidays_loaded = True
        try:
            self._holidays = self._provider.get_trading_holidays()
        except VCPScannerError as exc:
            logger.warning("NSE holiday list unavailable (%s); recent 404s stay PENDING", exc)
        return self._holidays

    def _record(
        self,
        day: date,
        status: BhavcopyFileStatus,
        *,
        sha256: str | None = None,
        row_count: int = 0,
        rejected: int = 0,
        cache_path: str | None = None,
        detail: str | None = None,
    ) -> None:
        self._manifest.record_file(
            BhavcopyFileRecord(
                trade_date=day,
                status=status,
                url=bhavcopy_url(day),
                file_format=bhavcopy_format(day),
                sha256=sha256,
                row_count=row_count,
                rejected_count=rejected,
                cache_path=cache_path,
                detail=detail,
            ),
            recorded_at=self._clock(),
        )


def _detail(rejected: list[RejectedBhavcopyRow], skipped: dict[str, int]) -> str:
    parts: dict[str, object] = {"skipped": skipped}
    if rejected:
        parts["rejected"] = [f"{r.symbol}/{r.series}: {r.reason}" for r in rejected[:20]]
    return json.dumps(parts, sort_keys=True)


def _text(value: object) -> str | None:
    return None if value is None else str(value)
