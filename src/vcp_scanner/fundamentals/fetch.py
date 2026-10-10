"""Fetch step of the fundamentals pipeline: list, download once, cache unchanged, record (F1).

Resumable by construction: a filing whose manifest row is OK and whose cached file still has the
recorded SHA-256 is never fetched again. A source that cannot be reached is a warning, never an
exception for the caller (spec §3): errors are counted and recorded as FETCH_ERROR so the next
run retries them.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from vcp_scanner.domain.errors import ProviderError
from vcp_scanner.fundamentals.base import (
    STATUS_FETCH_ERROR,
    STATUS_OK,
    FilingManifest,
    FilingRef,
    FundamentalProvider,
)

logger = logging.getLogger(__name__)

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass
class FetchReport:
    """Counts of one fetch run (all attributes are plain data for the CLI)."""

    listed: int = 0
    fetched: int = 0
    already_stored: int = 0
    errors: int = 0
    skipped_no_instrument: int = 0
    truncated: bool = False
    messages: list[str] = field(default_factory=list)


def cache_path_for(root: Path, ref: FilingRef) -> Path:
    """``<root>/<feed>/<symbol>/<record id>.xml``; names are made filesystem-safe."""
    return (
        root
        / _SAFE.sub("_", ref.source_feed)
        / _SAFE.sub("_", ref.symbol)
        / f"{_SAFE.sub('_', ref.source_record_id)}.xml"
    )


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _is_stored(manifest: FilingManifest, ref: FilingRef, root: Path) -> bool:
    row = manifest.get(ref.filing_id)
    if row is None or row.status != STATUS_OK or not row.cache_path or not row.sha256:
        return False
    path = root / row.cache_path
    if not path.is_file():
        return False
    return sha256_hex(path.read_bytes()) == row.sha256


def fetch_filings(
    provider: FundamentalProvider,
    manifest: FilingManifest,
    cache_root: Path,
    instrument_ids: Mapping[str, str],
    *,
    symbol: str | None = None,
    start: date | None = None,
    end: date | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> FetchReport:
    """List filings and store the ones not stored yet.

    ``instrument_ids`` maps an NSE symbol to our ``instrument_id``; filings of other symbols are
    skipped (counted), not stored: the manifest only holds stocks we scan.
    """
    report = FetchReport()
    try:
        listing = provider.list_filings(symbol=symbol, start=start, end=end)
    except ProviderError as exc:
        report.errors += 1
        report.messages.append(f"listing failed: {exc}")
        logger.warning("fundamentals listing failed: %s", exc)
        return report
    report.listed = len(listing.filings)
    report.truncated = listing.truncated
    if listing.truncated:
        report.messages.append("source returned its row limit: narrow the date range or symbol")

    for ref in listing.filings:
        instrument_id = instrument_ids.get(ref.symbol)
        if instrument_id is None:
            report.skipped_no_instrument += 1
            continue
        if _is_stored(manifest, ref, cache_root):
            report.already_stored += 1
            continue
        rel = cache_path_for(cache_root, ref).relative_to(cache_root)
        try:
            data = provider.download(ref.url)
            _write_atomic(cache_root / rel, data)
        except (ProviderError, OSError) as exc:
            report.errors += 1
            report.messages.append(f"{ref.filing_id}: {exc}")
            logger.warning("fundamentals fetch failed for %s: %s", ref.filing_id, exc)
            manifest.record(
                ref,
                instrument_id=instrument_id,
                status=STATUS_FETCH_ERROR,
                sha256=None,
                cache_path=None,
                fetched_at=clock(),
            )
            continue
        manifest.record(
            ref,
            instrument_id=instrument_id,
            status=STATUS_OK,
            sha256=sha256_hex(data),
            cache_path=rel.as_posix(),
            fetched_at=clock(),
        )
        report.fetched += 1
    return report
