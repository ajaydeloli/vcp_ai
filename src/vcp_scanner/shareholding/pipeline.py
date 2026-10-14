"""List, download, parse and store shareholding filings (F8). FUNDAMENTALS_SPECIFICATION §15.

As the results pipeline (§10): the network part runs with the database closed; the database is
opened read-only briefly first (what to fetch) and read-write at the end (record and parse).
Raw files are kept unchanged under ``<data>/raw/shareholding/<year>/<recordId>.xml``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from vcp_scanner.data.providers.nse_shareholding import ShpRef
from vcp_scanner.shareholding.parser import ShareholdingParseError, parse_shareholding

WINDOW_DAYS = 31
OVERLAP_DAYS = 3  # an update re-lists a few days before the last complete day
LISTING_TOLERANCE = 0.05  # parsed promoter % may differ from NSE's listing by rounding only
#: A file whose promoter % is further than this from NSE's own listing contradicts NSE: the
#: filer's XBRL is wrong (AYMSYNTEX June 2023: 0 % in the file, 73.95 % listed). Stored as
#: MISMATCH, never shown. Differences up to it are rounding or a revision and are kept.
MISMATCH_POINTS = 1.0
#: Downloads that fail in a row before a run stops (NSE refusing): the rest is left for the
#: next run, the files already fetched are recorded.
MAX_FAILURES_IN_A_ROW = 25


class Provider(Protocol):
    def list_range(self, start: date, end: date) -> list[ShpRef]: ...
    def list_symbol(self, symbol: str) -> list[ShpRef]: ...
    def download(self, url: str) -> bytes: ...


def is_quarter_end(d: date) -> bool:
    return (d.month, d.day) in ((3, 31), (6, 30), (9, 30), (12, 31))


def windows(start: date, end: date, days: int = WINDOW_DAYS) -> list[tuple[date, date]]:
    out: list[tuple[date, date]] = []
    d = start
    while d <= end:
        out.append((d, min(end, d + timedelta(days=days - 1))))
        d += timedelta(days=days)
    return out


@dataclass
class Listing:
    refs: list[ShpRef] = field(default_factory=list)
    complete_through: date | None = None  # every day up to here listed without an error
    errors: list[str] = field(default_factory=list)
    histories: list[str] = field(default_factory=list)  # symbols whose whole history was listed


def list_filings(
    provider: Provider, start: date, end: date, new_symbols: Sequence[str] = (),
    progress: Callable[[str], None] = lambda _m: None,
) -> Listing:  # fmt: skip
    out = Listing()
    complete = True
    for a, b in windows(start, end):
        try:
            refs = provider.list_range(a, b)
        except Exception as exc:  # noqa: BLE001 - reported, the next run lists it again
            out.errors.append(f"listing {a}..{b}: {exc}")
            complete = False
            continue
        out.refs.extend(refs)
        if complete:
            out.complete_through = b
        progress(f"listed {a}..{b}: {len(refs)} filings")
    for symbol in new_symbols:
        try:
            out.refs.extend(provider.list_symbol(symbol))
            out.histories.append(symbol)
        except Exception as exc:  # noqa: BLE001
            out.errors.append(f"history of {symbol}: {exc}")
    return out


def select(
    refs: Iterable[ShpRef], ids: Mapping[str, str], oldest: date, done: set[str]
) -> list[tuple[ShpRef, str]]:
    """Filings of our stocks, as on ``oldest`` or later, not stored yet; one per filing id."""
    seen: dict[str, tuple[ShpRef, str]] = {}
    for r in refs:
        if r.symbol in ids and r.as_on >= oldest and r.filing_id not in done:
            seen.setdefault(r.filing_id, (r, ids[r.symbol]))
    return sorted(seen.values(), key=lambda x: (x[0].broadcast_at, x[0].record_id))


def cache_path(root: Path, ref: ShpRef) -> Path:
    return root / f"{ref.as_on.year}" / f"{ref.record_id}.xml"


@dataclass(frozen=True)
class Downloaded:
    ref: ShpRef
    instrument_id: str
    path: Path | None
    sha256: str | None
    error: str | None


def download(
    provider: Provider, targets: Sequence[tuple[ShpRef, str]], root: Path,
    progress: Callable[[str], None] = lambda _m: None, every: int = 200,
) -> list[Downloaded]:  # fmt: skip
    out: list[Downloaded] = []
    failures = 0
    for k, (ref, iid) in enumerate(targets, 1):
        if failures >= MAX_FAILURES_IN_A_ROW:
            progress(f"stopped after {failures} failed downloads in a row; the remaining"
                     f" {len(targets) - k + 1} are left for the next run")  # fmt: skip
            break
        path = cache_path(root, ref)
        try:
            if path.is_file() and path.stat().st_size > 0:
                data = path.read_bytes()
            else:
                data = provider.download(ref.url)
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".part")
                tmp.write_bytes(data)
                tmp.replace(path)
            out.append(Downloaded(ref, iid, path, hashlib.sha256(data).hexdigest(), None))
            failures = 0
        except Exception as exc:  # noqa: BLE001 - recorded as FETCH_ERROR, retried next run
            out.append(Downloaded(ref, iid, None, None, f"{type(exc).__name__}: {exc}"))
            failures += 1
            reset = getattr(provider, "reset", None)
            if callable(reset):
                reset()  # a fresh connection: a stalled one hung the 2026-10-14 backfill
        if k % every == 0:
            progress(f"downloaded {k}/{len(targets)}")
    return out


def save_targets(path: Path, targets: Sequence[tuple[ShpRef, str]]) -> None:
    def plain(v: Any) -> Any:
        return v.isoformat() if isinstance(v, (date, datetime)) else v

    rows = [{**{k: plain(v) for k, v in vars(r).items()}, "instrument_id": iid}
            for r, iid in targets]  # fmt: skip
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows), encoding="utf-8")


def load_targets(path: Path) -> list[tuple[ShpRef, str]]:
    out: list[tuple[ShpRef, str]] = []
    for row in json.loads(path.read_text(encoding="utf-8")):
        iid = row.pop("instrument_id")
        row["as_on"] = date.fromisoformat(row["as_on"])
        row["broadcast_at"] = datetime.fromisoformat(row["broadcast_at"])
        out.append((ShpRef(**row), iid))
    return out


# ---- the database part ------------------------------------------------------------------------


class Conn(Protocol):
    def execute(self, query: str, parameters: Any = ...) -> Any: ...


def scope(conn: Conn, since: date) -> dict[str, str]:
    """Symbol -> instrument id of every stock eligible in a universe snapshot since ``since``
    (the fundamentals scope, §9)."""
    rows = conn.execute(
        "SELECT DISTINCT i.symbol, i.instrument_id FROM universe_memberships m"
        " JOIN universe_snapshots u USING (universe_snapshot_id)"
        " JOIN instruments i ON i.instrument_id = m.instrument_id"
        " WHERE m.exclusion_reason IS NULL AND u.as_of_date >= ?",
        [since],
    ).fetchall()
    return {str(s): str(i) for s, i in rows}


def stored_ok(conn: Conn) -> set[str]:
    """Filings that need no new download (a FETCH_ERROR is tried again)."""
    return {str(r[0]) for r in conn.execute(
        "SELECT filing_id FROM shareholding_filings"
        " WHERE status IN ('OK', 'PARSE_ERROR', 'MISMATCH')"
    ).fetchall()}  # fmt: skip


def stocks_with_filings(conn: Conn) -> set[str]:
    return {str(r[0]) for r in conn.execute(
        "SELECT DISTINCT instrument_id FROM shareholding_filings").fetchall()}  # fmt: skip


def complete_through(conn: Conn) -> date | None:
    row = conn.execute("SELECT max(complete_through) FROM shareholding_fetch_runs").fetchone()
    return row[0] if row and row[0] else None


@dataclass
class Recorded:
    new: int = 0
    parsed: int = 0
    parse_errors: int = 0
    fetch_errors: int = 0
    listing_mismatches: int = 0  # set aside: the file contradicts NSE's listing
    listing_rounding: int = 0  # small differences kept
    messages: list[str] = field(default_factory=list)


def record(conn: Conn, downloads: Sequence[Downloaded], now: datetime | None = None) -> Recorded:
    """Write the manifest and snapshot rows, then renumber revisions. Re-recording a filing
    replaces its rows (a FETCH_ERROR retried later)."""
    now = now or datetime.now(UTC)
    out = Recorded()
    for d in downloads:
        r = d.ref
        values = None
        status, error = "OK", None
        if d.error:
            status, error = "FETCH_ERROR", d.error
            out.fetch_errors += 1
        else:
            assert d.path is not None
            try:
                values = parse_shareholding(d.path.read_bytes())
            except ShareholdingParseError as exc:
                status, error = "PARSE_ERROR", str(exc)
                out.parse_errors += 1
                out.messages.append(f"{r.symbol} {r.as_on}: {exc}")
        if values is not None and r.promoter_pct is not None:
            gap = abs(values.promoter_pct - r.promoter_pct)
            if gap > MISMATCH_POINTS:
                status = "MISMATCH"
                error = (f"promoter {values.promoter_pct:.2f} % in the file,"
                         f" {r.promoter_pct:.2f} % in NSE's listing")  # fmt: skip
                out.listing_mismatches += 1
                out.messages.append(f"{r.symbol} {r.as_on}: {error} (set aside)")
                values = None
            elif gap > LISTING_TOLERANCE:
                out.listing_rounding += 1
        conn.execute("DELETE FROM shareholding_snapshots WHERE filing_id = ?", [r.filing_id])
        conn.execute("DELETE FROM shareholding_filings WHERE filing_id = ?", [r.filing_id])
        conn.execute(
            "INSERT INTO shareholding_filings VALUES"
            " (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?)",
            [r.filing_id, d.instrument_id, r.symbol, r.as_on, is_quarter_end(r.as_on),
             r.broadcast_at, r.revised, r.url, r.promoter_pct, r.public_pct, d.sha256,
             None if d.path is None else str(d.path), now, status, error],
        )  # fmt: skip
        out.new += 1
        if values is None:
            continue
        out.parsed += 1
        conn.execute(
            "INSERT INTO shareholding_snapshots VALUES"
            " (?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [r.filing_id, d.instrument_id, r.as_on, is_quarter_end(r.as_on), r.broadcast_at,
             values.layout, values.promoter_pct, values.promoter_pledged_pct, values.fii_pct,
             values.fdi_pct, values.dii_pct, values.mf_pct, values.insurance_pct,
             values.government_pct, values.non_institutions_pct, values.public_pct,
             values.other_pct, values.shareholders],
        )  # fmt: skip
    # revisions: 0 = the first filing of a stock and as-on date, in broadcast order
    conn.execute("""
        UPDATE shareholding_filings f SET revision_number = r.n FROM (
            SELECT filing_id, row_number() OVER (PARTITION BY instrument_id, as_on
                                                 ORDER BY broadcast_at, filing_id) - 1 AS n
            FROM shareholding_filings) r
        WHERE f.filing_id = r.filing_id AND f.revision_number <> r.n""")
    conn.execute("""
        UPDATE shareholding_snapshots s SET revision_number = f.revision_number
        FROM shareholding_filings f
        WHERE s.filing_id = f.filing_id AND s.revision_number <> f.revision_number""")
    return out


def record_run(conn: Conn, command: str, listed_from: date | None, listing: Listing,
               rec: Recorded, now: datetime | None = None) -> None:  # fmt: skip
    conn.execute(
        "INSERT INTO shareholding_fetch_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [now or datetime.now(UTC), command, listed_from, listing.complete_through,
         len(listing.refs), rec.new, rec.parse_errors, rec.fetch_errors],
    )  # fmt: skip


def update_start(today: date, through: date | None, first_days: int, oldest: date) -> date:
    if through is None:
        return max(oldest, today - timedelta(days=first_days))
    return max(oldest, through - timedelta(days=OVERLAP_DAYS))


def stored_downloads(conn: Conn, root: Path) -> list[Downloaded]:
    """Every filing whose file is in the cache, as it was listed (for ``reparse``)."""
    rows = conn.execute(
        "SELECT filing_id, instrument_id, symbol, as_on, broadcast_at, revised, url,"
        " listed_promoter_pct, listed_public_pct, sha256 FROM shareholding_filings"
        " WHERE status <> 'FETCH_ERROR' ORDER BY broadcast_at, filing_id"
    ).fetchall()
    out: list[Downloaded] = []
    for fid, iid, sym, as_on, when, revised, url, lp, lpub, sha in rows:
        ref = ShpRef(record_id=str(fid).split(":", 1)[1], symbol=str(sym), as_on=as_on,
                     broadcast_at=when, revised=bool(revised), new_layout=None, url=str(url),
                     promoter_pct=lp, public_pct=lpub)  # fmt: skip
        path = cache_path(root, ref)
        if path.is_file():
            out.append(Downloaded(ref, str(iid), path, sha, None))
    return out
