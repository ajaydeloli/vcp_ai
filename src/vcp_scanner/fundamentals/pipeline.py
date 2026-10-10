"""Fundamentals pipeline for many stocks: list, download, record, parse, views (F4).

The network part (listing and downloading) touches only the raw cache, never the database, so
a long backfill does not hold the database lock: the database is opened briefly before (what is
already stored) and after (record, parse, compute views). A download already in the cache is not
fetched again, so an interrupted backfill resumes where it stopped.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Protocol

from vcp_scanner.domain.errors import ProviderError
from vcp_scanner.fundamentals.base import (
    STATUS_FETCH_ERROR,
    STATUS_OK,
    FilingManifest,
    FilingRef,
    FundamentalProvider,
)
from vcp_scanner.fundamentals.fetch import _write_atomic, cache_path_for, sha256_hex
from vcp_scanner.fundamentals.metrics import (
    FundamentalView,
    ShareAction,
    SnapshotData,
    compute_view,
    month_end_shift,
    view_dates,
)

logger = logging.getLogger(__name__)

Progress = Callable[[str], None]


def _quiet(_: str) -> None:
    return None


def min_period_end(history_from: date) -> date:
    """Oldest period end fetched: 15 months before ``history_from``, so the first views there
    have a year-ago quarter and the quarter before it (YoY and acceleration)."""
    return month_end_shift(history_from - timedelta(days=1), -15)


def windows(start: date, end: date, days: int = 31) -> list[tuple[date, date]]:
    out: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        stop = min(cursor + timedelta(days=days - 1), end)
        out.append((cursor, stop))
        cursor = stop + timedelta(days=1)
    return out


@dataclass
class Listing:
    refs: list[FilingRef] = field(default_factory=list)
    incomplete: list[str] = field(default_factory=list)  # windows that failed or ran short
    #: Every filing broadcast up to this date was listed (the end of the last window before the
    #: first failed one); None when the first window failed. The next update starts here.
    complete_through: date | None = None
    #: Stocks whose whole history was listed (per-stock listing).
    histories_listed: list[str] = field(default_factory=list)


def list_range(
    provider: FundamentalProvider, start: date, end: date, progress: Progress = _quiet
) -> Listing:
    """Every result filing broadcast in ``[start, end]`` (all stocks), month by month."""
    result = Listing()
    seen: set[str] = set()
    spans = windows(start, end)
    failed = False
    for n, (a, b) in enumerate(spans, 1):
        try:
            listing = provider.list_filings(start=a, end=b)
        except ProviderError as exc:
            result.incomplete.append(f"{a}..{b}: {exc}")
            logger.warning("fundamentals listing %s..%s failed: %s", a, b, exc)
            failed = True
            continue
        if listing.truncated:
            result.incomplete.append(f"{a}..{b}: source returned fewer rows than it reported")
            failed = True
        elif not failed:
            result.complete_through = b
        for ref in listing.filings:
            if ref.filing_id not in seen:
                seen.add(ref.filing_id)
                result.refs.append(ref)
        progress(f"listed {a}..{b} ({n}/{len(spans)}): {len(result.refs)} filings so far")
    return result


def list_histories(
    provider: FundamentalProvider, symbols: Sequence[str], progress: Progress = _quiet
) -> Listing:
    """The whole filing history of each stock (for stocks new to the scope)."""
    result = Listing()
    for n, symbol in enumerate(symbols, 1):
        try:
            listing = provider.list_filings(symbol=symbol)
        except ProviderError as exc:
            result.incomplete.append(f"{symbol}: {exc}")
            continue
        if listing.truncated:
            result.incomplete.append(f"{symbol}: source returned fewer rows than it reported")
            continue
        result.refs.extend(listing.filings)
        result.histories_listed.append(symbol)
        if n % 20 == 0:
            progress(f"histories listed {n}/{len(symbols)}")
    return result


def update_start(
    today: date, complete_through: date | None, first_days: int, overlap_days: int = 3
) -> date:
    """Where an update starts listing: a few days before the last date listed completely
    (filings are sometimes listed late), or ``first_days`` back when there is no such date."""
    if complete_through is None:
        return today - timedelta(days=first_days)
    return min(today, complete_through - timedelta(days=overlap_days))


def select_targets(
    refs: Iterable[FilingRef], instrument_ids: Mapping[str, str], oldest_period_end: date
) -> list[tuple[FilingRef, str]]:
    """Filings of our stocks, recent enough; STANDALONE only where no CONSOLIDATED is listed
    for the same period (basis policy prefer_consolidated, so the standalone would be unused)."""
    ours = [(r, instrument_ids[r.symbol]) for r in refs
            if r.symbol in instrument_ids and r.period_end >= oldest_period_end]  # fmt: skip
    consolidated = {(r.symbol, r.period_end, r.period_type) for r, _ in ours
                    if r.statement_basis == "CONSOLIDATED"}  # fmt: skip
    return [(r, iid) for r, iid in ours
            if not (r.statement_basis == "STANDALONE"
                    and (r.symbol, r.period_end, r.period_type) in consolidated)]  # fmt: skip


@dataclass(frozen=True)
class Downloaded:
    ref: FilingRef
    instrument_id: str
    cache_path: str | None
    sha256: str | None
    error: str | None = None


def download_missing(
    provider: FundamentalProvider,
    targets: Sequence[tuple[FilingRef, str]],
    cache_root: Path,
    progress: Progress = _quiet,
    *,
    stop_after_failures: int = 20,
) -> list[Downloaded]:
    """Download every target not yet in the cache (no database access).

    Stops after ``stop_after_failures`` failures in a row: the source is then refusing us (rate
    limit, outage), and more requests would only prolong the block. What was downloaded is
    returned; the rest is fetched by the next run.
    """
    out: list[Downloaded] = []
    fetched = streak = 0
    for n, (ref, iid) in enumerate(targets, 1):
        path = cache_path_for(cache_root, ref)
        rel = path.relative_to(cache_root).as_posix()
        if not path.is_file():
            try:
                _write_atomic(path, provider.download(ref.url))
                fetched += 1
                streak = 0
            except (ProviderError, OSError) as exc:
                out.append(Downloaded(ref, iid, None, None, str(exc)))
                streak += 1
                if streak >= stop_after_failures:
                    progress(f"stopped after {streak} failures in a row at {n}/{len(targets)}:"
                             f" {exc}. Run again later; nothing is lost.")  # fmt: skip
                    break
                continue
        out.append(Downloaded(ref, iid, rel, sha256_hex(path.read_bytes())))
        if n % 100 == 0:
            progress(f"downloaded {n}/{len(targets)} ({fetched} new)")
    return out


def save_targets(path: Path, targets: Sequence[tuple[FilingRef, str]]) -> None:
    """Keep a backfill's target list so a resumed run needs no new listing."""
    rows = [{**{k: (v.isoformat() if isinstance(v, date | datetime) else v)
                for k, v in ref.__dict__.items()}, "instrument_id": iid}
            for ref, iid in targets]  # fmt: skip
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    tmp.write_text(json.dumps(rows))
    tmp.replace(path)


def load_targets(path: Path) -> list[tuple[FilingRef, str]]:
    out: list[tuple[FilingRef, str]] = []
    for row in json.loads(path.read_text()):
        iid = row.pop("instrument_id")
        row["period_end"] = date.fromisoformat(row["period_end"])
        row["broadcast_at"] = datetime.fromisoformat(row["broadcast_at"])
        out.append((FilingRef(**row), iid))
    return out


def record_downloads(
    manifest: FilingManifest,
    downloads: Iterable[Downloaded],
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> tuple[int, int]:
    """Write the manifest rows; returns (recorded OK, errors). An OK row stays OK."""
    ok = errors = 0
    for d in downloads:
        existing = manifest.get(d.ref.filing_id)
        if d.error is not None:
            errors += 1
            if existing is None or existing.status != STATUS_OK:
                manifest.record(d.ref, instrument_id=d.instrument_id, status=STATUS_FETCH_ERROR,
                                sha256=None, cache_path=None, fetched_at=clock())  # fmt: skip
            continue
        if existing is not None and existing.status == STATUS_OK and existing.sha256 == d.sha256:
            continue
        if existing is not None and existing.status not in (STATUS_OK, STATUS_FETCH_ERROR):
            continue  # PARSE_ERROR / NOT_APPLICABLE: kept until reset by hand
        manifest.record(d.ref, instrument_id=d.instrument_id, status=STATUS_OK,
                        sha256=d.sha256, cache_path=d.cache_path, fetched_at=clock())  # fmt: skip
        ok += 1
    return ok, errors


# --- stored views -------------------------------------------------------------------------------


class ViewStore(Protocol):
    def instruments_with_snapshots(self) -> list[str]: ...

    def snapshots_for(self, instrument_id: str) -> list[SnapshotData]: ...

    def share_actions(self, instrument_id: str) -> list[ShareAction]: ...

    def view_dates_stored(self, instrument_id: str) -> set[date]: ...

    def write_views(
        self, instrument_id: str, views: Sequence[FundamentalView], staleness_days: int
    ) -> None: ...


def update_views(
    store: ViewStore,
    *,
    staleness_days: int,
    min_availability: float,
    instruments: Iterable[str] | None = None,
) -> int:
    """Store the view of every date on which a stock's fundamentals changed and that has no
    row yet. Returns the number of rows written."""
    written = 0
    for iid in instruments if instruments is not None else store.instruments_with_snapshots():
        snaps = store.snapshots_for(iid)
        dates = view_dates(snaps)
        stored = store.view_dates_stored(iid)
        new_dates = [d for d in dates if d not in stored]
        if not new_dates:
            continue
        # A filing that arrived late (an earlier date than rows already stored) changes the
        # later views too: recompute from the earliest new date on.
        todo = [d for d in dates if d >= min(new_dates)]
        actions = store.share_actions(iid)
        views = [
            compute_view(snaps, d, actions, staleness_days=staleness_days,
                         min_availability=min_availability)
            for d in todo
        ]  # fmt: skip
        views = [v for v in views if v.snapshot_id is not None]
        store.write_views(iid, views, staleness_days)
        written += len(views)
    return written
