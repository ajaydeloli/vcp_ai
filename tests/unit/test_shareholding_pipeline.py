"""Shareholding pipeline (F8): listing rows, selection, download cache, recording, revisions.

A fake NSE serves the saved filings; the database is in memory. No network."""

from __future__ import annotations

import gzip
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tests.api.fixture import put
from vcp_scanner.data.providers._time import IST
from vcp_scanner.data.providers.nse_shareholding import ShpRef, parse_listing
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.shareholding import pipeline as pl
from vcp_scanner.shareholding.config import ShareholdingConfig, load_shareholding_config

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "shareholding"
RELIANCE_NEW = gzip.decompress((FIX / "reliance_2026q1fy27_new.xml.gz").read_bytes())
JSW_NEW = gzip.decompress((FIX / "jswsteel_2026q1fy27_new.xml.gz").read_bytes())
T0 = datetime(2026, 7, 16, 19, 24, tzinfo=IST)
OLDEST = date(2022, 6, 30)


def ref(rid: str, symbol: str, as_on: date, when: datetime, **kw: Any) -> ShpRef:
    base: dict[str, Any] = {
        "record_id": rid, "symbol": symbol, "as_on": as_on, "broadcast_at": when,
        "revised": False, "new_layout": True, "url": f"https://archive.example/{rid}.xml",
        "promoter_pct": None, "public_pct": None,
    }  # fmt: skip
    base.update(kw)
    return ShpRef(**base)


class FakeNse:
    def __init__(self, refs: list[ShpRef], files: dict[str, bytes]) -> None:
        self.refs, self.files, self.downloads = refs, files, 0
        self.fail: set[str] = set()

    def list_range(self, start: date, end: date) -> list[ShpRef]:
        return [r for r in self.refs if start <= r.broadcast_at.date() <= end]

    def list_symbol(self, symbol: str) -> list[ShpRef]:
        return [r for r in self.refs if r.symbol == symbol]

    def download(self, url: str) -> bytes:
        self.downloads += 1
        if url in self.fail:
            raise RuntimeError("HTTP 403")
        return self.files[url]


@pytest.fixture
def conn() -> Any:
    store = DuckDBStore(":memory:")
    store.migrate()
    c = store.conn
    put(c, "universe_snapshots", universe_snapshot_id="U", universe_name="u",
        as_of_date=date(2026, 9, 1))  # fmt: skip
    for sym in ("RELIANCE", "JSWSTEEL"):
        iid = f"NSE_EQ|{sym}"
        put(c, "instruments", instrument_id=iid, symbol=sym)
        put(c, "universe_memberships", universe_snapshot_id="U", instrument_id=iid, eligible=True)
    return c


def run(conn: Any, nse: FakeNse, root: Path, start: date, end: date) -> pl.Recorded:
    ids = pl.scope(conn, date(2023, 10, 1))
    listing = pl.list_filings(nse, start, end)
    targets = pl.select(listing.refs, ids, OLDEST, pl.stored_ok(conn))
    rec = pl.record(conn, pl.download(nse, targets, root))
    pl.record_run(conn, "backfill", start, listing, rec)
    return rec


def test_listing_rows_from_nse() -> None:
    sample = [
        {"recordId": "211570", "symbol": "RELIANCE", "date": "30-JUN-2026",
         "broadcastDate": "16-JUL-2026 19:24:44", "revisedData": "N", "newColumn": True,
         "xbrl": "https://nsearchives.nseindia.com/corporate/xbrl/SHP_1694620.xml",
         "pr_and_prgrp": "50.48", "public_val": "49.52"},
        {"recordId": "1", "symbol": "X", "date": "30-JUN-2026", "broadcastDate": "-", "xbrl": "-"},
    ]  # fmt: skip
    refs, skipped = parse_listing(sample)
    assert skipped == 1 and len(refs) == 1
    r = refs[0]
    assert (r.filing_id, r.as_on, r.revised, r.promoter_pct) == (
        "NSE_SHP:211570", date(2026, 6, 30), False, 50.48)  # fmt: skip
    assert r.broadcast_at == datetime(2026, 7, 16, 19, 24, 44, tzinfo=IST)


def test_windows_cover_the_range_without_gaps() -> None:
    w = pl.windows(date(2026, 1, 1), date(2026, 3, 15))
    assert w[0][0] == date(2026, 1, 1) and w[-1][1] == date(2026, 3, 15)
    assert all(b + timedelta(days=1) == c for (_, b), (c, _) in zip(w, w[1:], strict=False))


def test_select_keeps_our_recent_unseen_filings_once() -> None:
    a = ref("1", "RELIANCE", date(2026, 6, 30), T0)
    old = ref("2", "RELIANCE", date(2021, 9, 30), T0)
    other = ref("3", "TCS", date(2026, 6, 30), T0)
    got = pl.select([a, a, old, other], {"RELIANCE": "NSE_EQ|RELIANCE"}, OLDEST, done=set())
    assert [r.record_id for r, _ in got] == ["1"]
    assert pl.select([a], {"RELIANCE": "x"}, OLDEST, done={"NSE_SHP:1"}) == []


def test_record_parses_numbers_revisions_and_listing_check(conn: Any, tmp_path: Path) -> None:
    first = ref("10", "RELIANCE", date(2026, 6, 30), T0, promoter_pct=50.48)
    revised = ref("11", "RELIANCE", date(2026, 6, 30), T0 + timedelta(days=3), revised=True)
    jsw = ref("12", "JSWSTEEL", date(2026, 6, 30), T0, promoter_pct=40.0)  # listing disagrees
    files = {first.url: RELIANCE_NEW, revised.url: RELIANCE_NEW, jsw.url: JSW_NEW}
    nse = FakeNse([first, revised, jsw], files)
    rec = run(conn, nse, tmp_path, date(2026, 7, 1), date(2026, 7, 31))
    assert (rec.new, rec.parsed, rec.parse_errors, rec.listing_mismatches) == (3, 3, 0, 1)
    rows = conn.execute(
        "SELECT filing_id, revision_number, is_quarter_end FROM shareholding_filings"
        " WHERE instrument_id = 'NSE_EQ|RELIANCE' ORDER BY 1"
    ).fetchall()
    assert rows == [("NSE_SHP:10", 0, True), ("NSE_SHP:11", 1, True)]
    pr, pledged, fii = conn.execute(
        "SELECT promoter_pct, promoter_pledged_pct, fii_pct FROM shareholding_snapshots"
        " WHERE filing_id = 'NSE_SHP:12'"
    ).fetchone()
    assert (round(pr, 2), round(pledged, 2), round(fii, 2)) == (44.29, 11.6, 10.91)
    assert pl.complete_through(conn) == date(2026, 7, 31)
    # a second run finds nothing new and downloads nothing
    before = nse.downloads
    assert run(conn, nse, tmp_path, date(2026, 7, 1), date(2026, 7, 31)).new == 0
    assert nse.downloads == before


def test_a_failed_download_is_kept_as_an_error_and_retried(conn: Any, tmp_path: Path) -> None:
    r = ref("20", "RELIANCE", date(2026, 6, 30), T0)
    nse = FakeNse([r], {r.url: RELIANCE_NEW})
    nse.fail.add(r.url)
    assert run(conn, nse, tmp_path, date(2026, 7, 1), date(2026, 7, 31)).fetch_errors == 1
    assert "NSE_SHP:20" not in pl.stored_ok(conn)  # FETCH_ERROR: selected again next time
    nse.fail.clear()
    assert run(conn, nse, tmp_path, date(2026, 7, 1), date(2026, 7, 31)).parsed == 1
    assert conn.execute("SELECT status FROM shareholding_filings").fetchall() == [("OK",)]


def test_a_cached_file_is_not_downloaded_again(tmp_path: Path) -> None:
    r = ref("30", "RELIANCE", date(2026, 6, 30), T0)
    pl.cache_path(tmp_path, r).parent.mkdir(parents=True)
    pl.cache_path(tmp_path, r).write_bytes(RELIANCE_NEW)
    nse = FakeNse([r], {})
    got = pl.download(nse, [(r, "NSE_EQ|RELIANCE")], tmp_path)
    assert nse.downloads == 0 and got[0].error is None


def test_event_filings_are_kept_but_not_quarter_ends() -> None:
    assert pl.is_quarter_end(date(2026, 6, 30)) and not pl.is_quarter_end(date(2024, 10, 29))


def test_targets_survive_a_save_and_load(tmp_path: Path) -> None:
    r = ref("40", "RELIANCE", date(2026, 6, 30), T0, promoter_pct=50.48)
    pl.save_targets(tmp_path / "t.json", [(r, "NSE_EQ|RELIANCE")])
    assert pl.load_targets(tmp_path / "t.json") == [(r, "NSE_EQ|RELIANCE")]


def test_update_starts_a_few_days_before_the_last_complete_day() -> None:
    today = date(2026, 10, 13)
    assert pl.update_start(today, None, 10, OLDEST) == date(2026, 10, 3)
    assert pl.update_start(today, date(2026, 10, 12), 10, OLDEST) == date(2026, 10, 9)


def test_config_file_and_its_limits(tmp_path: Path) -> None:
    from vcp_scanner.domain.errors import ConfigError

    assert load_shareholding_config(tmp_path) == ShareholdingConfig()
    repo = Path(__file__).resolve().parents[2] / "config"
    assert load_shareholding_config(repo).site_interval_seconds >= 1.0
    (tmp_path / "shareholding.yaml").write_text("site_interval_seconds: 0.2\n")
    with pytest.raises(ConfigError, match="not allowed"):
        load_shareholding_config(tmp_path)
    (tmp_path / "shareholding.yaml").write_text("histroy_from: 2022-01-01\n")
    with pytest.raises(ConfigError, match="unknown keys"):
        load_shareholding_config(tmp_path)
