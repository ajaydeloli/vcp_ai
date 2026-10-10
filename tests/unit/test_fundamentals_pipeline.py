"""Fundamentals F4: backfill/update pipeline, stored views, migration, daily switch."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from tests.unit.test_fundamentals_parse import load, old_ref
from vcp_scanner.data.repositories.duckdb_fundamental_repository import (
    DuckDBFundamentalRepository,
)
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.errors import ProviderError
from vcp_scanner.fundamentals.base import FilingListing, FilingRef
from vcp_scanner.fundamentals.pipeline import (
    download_missing,
    list_range,
    min_period_end,
    record_downloads,
    select_targets,
    update_views,
    windows,
)
from vcp_scanner.fundamentals.snapshots import parse_filings

NOW = datetime(2026, 10, 10, tzinfo=UTC)


def test_min_period_end_and_windows() -> None:
    assert min_period_end(date(2023, 10, 1)) == date(2022, 6, 30)
    w = windows(date(2024, 1, 1), date(2024, 3, 5))
    assert w[0] == (date(2024, 1, 1), date(2024, 1, 31)) and w[-1][1] == date(2024, 3, 5)
    assert all(b >= a for a, b in w)


def test_select_targets_prefers_consolidated_and_drops_old_and_foreign() -> None:
    con = old_ref("1", "31-Dec-2024", "Quarterly", "Consolidated", "16-Jan-2025 20:15:20")
    std = old_ref("2", "31-Dec-2024", "Quarterly", "Non-Consolidated", "16-Jan-2025 20:20:21")
    std_only = old_ref("3", "30-Sep-2024", "Quarterly", "Non-Consolidated", "14-Oct-2024 19:00:00")
    old = old_ref("4", "31-Mar-2022", "Quarterly", "Consolidated", "22-Apr-2022 19:00:00")
    other = FilingRef(**{**con.__dict__, "symbol": "OTHER", "source_record_id": "5"})
    picked = select_targets([con, std, std_only, old, other], {"RELIANCE": "I"}, date(2022, 6, 30))
    assert [r.source_record_id for r, _ in picked] == ["1", "3"]


class Provider:
    def __init__(self, refs: list[FilingRef], files: dict[str, bytes]) -> None:
        self.refs = refs
        self.files = files
        self.downloads: list[str] = []
        self.fail_listing: set[date] = set()

    def list_filings(self, *, symbol: Any = None, start: Any = None, end: Any = None) -> Any:
        if any(start <= d <= end for d in self.fail_listing):
            raise ProviderError("blocked")
        return FilingListing([r for r in self.refs if start <= r.broadcast_at.date() <= end])

    def download(self, url: str) -> bytes:
        self.downloads.append(url)
        if url not in self.files:
            raise ProviderError("404")
        return self.files[url]


@pytest.fixture
def repo() -> DuckDBFundamentalRepository:
    store = DuckDBStore(":memory:")
    store.migrate()
    return DuckDBFundamentalRepository(store)


def refs_and_files() -> tuple[list[FilingRef], dict[str, bytes]]:
    q3fy24 = old_ref("1", "31-Dec-2023", "Quarterly", "Consolidated", "19-Jan-2024 19:17:54")
    q3fy25 = old_ref("2", "31-Dec-2024", "Quarterly", "Consolidated", "16-Jan-2025 20:15:20")
    return [q3fy24, q3fy25], {q3fy24.url: load("reliance_q3fy24_con"),
                              q3fy25.url: load("reliance_q3fy25_con")}  # fmt: skip


def run_pipeline(repo: DuckDBFundamentalRepository, provider: Provider, root: Path) -> Any:
    listing = list_range(provider, date(2023, 12, 1), date(2025, 2, 28))
    targets = select_targets(listing.refs, {"RELIANCE": "INS1"}, date(2022, 6, 30))
    downloads = download_missing(provider, targets, root)
    recorded = record_downloads(repo, downloads, clock=lambda: NOW)
    parsed = parse_filings(repo, root)
    views = update_views(repo, staleness_days=120, min_availability=0.5)
    return listing, recorded, parsed, views


def test_backfill_end_to_end_resume_and_views(repo: DuckDBFundamentalRepository,
                                              tmp_path: Path) -> None:  # fmt: skip
    refs, files = refs_and_files()
    provider = Provider(refs, files)
    listing, recorded, parsed, views = run_pipeline(repo, provider, tmp_path)
    assert not listing.incomplete and recorded == (2, 0) and parsed.parsed == 2
    assert views == 2  # one view per date on which a filing became usable
    conn = repo._conn
    rows = conn.execute(
        "SELECT as_of_date, period_end, eps, eps_yoy, statuses_json FROM fundamental_metrics"
        " ORDER BY as_of_date"
    ).fetchall()
    assert [r[0] for r in rows] == [date(2024, 1, 20), date(2025, 1, 17)]  # evening filings
    assert rows[1][2] == 13.70
    # No bonus factor in this database: 13.70 against 25.52 as filed (see the next test).
    assert rows[1][3] == pytest.approx((13.70 - 25.52) / 25.52 * 100)
    assert json.loads(rows[1][4])["eps_yoy"] == "AVAILABLE"
    q = conn.execute(
        "SELECT stale_after, fundamental_hard_gate_pass, fundamental_gate_reason"
        " FROM fundamental_data_quality WHERE as_of_date = DATE '2025-01-17'"
    ).fetchone()
    assert q is not None and q[0] == date(2025, 4, 30)
    assert q[1] is True and q[2] is None  # 4 of 7 metrics (filed D/E 0 counts)
    # A second run downloads nothing and writes nothing.
    before = len(provider.downloads)
    _, recorded2, parsed2, views2 = run_pipeline(repo, provider, tmp_path)
    assert len(provider.downloads) == before
    assert (recorded2, parsed2.parsed, views2) == ((0, 0), 0, 0)


def test_eps_growth_uses_the_bonus_from_the_corporate_action_tables(
    repo: DuckDBFundamentalRepository, tmp_path: Path
) -> None:
    conn = repo._conn
    conn.execute(
        "INSERT INTO corporate_action_resolution (resolution_id, instrument_id, action_type,"
        " ex_date, status, known_from) VALUES ('r1', 'INS1', 'BONUS', DATE '2024-10-28', 'OK',"
        " now())"
    )
    conn.execute(
        "INSERT INTO corporate_action_adjustments VALUES ('r1', 'INS1', DATE '2024-10-28', 0.5,"
        " 2.0, 0.5, 2.0, 'NSE', 'v1', now(), NULL)"
    )
    refs, files = refs_and_files()
    run_pipeline(repo, Provider(refs, files), tmp_path)
    eps_yoy = conn.execute(
        "SELECT eps_yoy FROM fundamental_metrics WHERE as_of_date = DATE '2025-01-17'"
    ).fetchone()
    assert eps_yoy is not None and round(eps_yoy[0], 2) == 7.37


def test_failures_are_warnings_and_retried(repo: DuckDBFundamentalRepository,
                                           tmp_path: Path) -> None:  # fmt: skip
    refs, files = refs_and_files()
    provider = Provider(refs, {refs[0].url: files[refs[0].url]})  # the second file is missing
    provider.fail_listing = {date(2025, 1, 16)}  # the window holding 16 Jan 2025 fails
    listing, recorded, parsed, _ = run_pipeline(repo, provider, tmp_path)
    assert listing.incomplete and recorded == (1, 0) and parsed.parsed == 1
    # The next run sees the window and the file: the late filing is added, views recomputed.
    provider.fail_listing = set()
    provider.files = files
    _, recorded2, parsed2, views2 = run_pipeline(repo, provider, tmp_path)
    assert recorded2 == (1, 0) and parsed2.parsed == 1 and views2 == 1


def test_late_filing_recomputes_later_views(repo: DuckDBFundamentalRepository,
                                            tmp_path: Path) -> None:  # fmt: skip
    refs, files = refs_and_files()
    run_pipeline(repo, Provider(refs[1:], files), tmp_path)  # Dec 2024 only
    first = repo._conn.execute(
        "SELECT eps_yoy, statuses_json FROM fundamental_metrics").fetchall()  # fmt: skip
    assert first[0][0] is None and json.loads(first[0][1])["eps_yoy"] == "MISSING"
    run_pipeline(repo, Provider(refs, files), tmp_path)  # Dec 2023 arrives late
    rows = repo._conn.execute(
        "SELECT as_of_date, statuses_json FROM fundamental_metrics ORDER BY 1"
    ).fetchall()
    assert len(rows) == 2
    assert json.loads(rows[1][1])["eps_yoy"] != "MISSING"  # the 2025 view now sees 2023


def test_migration_replaces_the_empty_pre_f4_tables(tmp_path: Path) -> None:
    import duckdb

    db = tmp_path / "old.duckdb"
    conn = duckdb.connect(str(db))
    conn.execute("CREATE TABLE fundamental_metrics (fundamental_snapshot_id VARCHAR PRIMARY KEY,"
                 " revenue DOUBLE)")  # fmt: skip
    conn.close()
    store = DuckDBStore(db)
    store.migrate()
    cols = {r[0] for r in store.conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name ="
        " 'fundamental_metrics'").fetchall()}  # fmt: skip
    assert {"instrument_id", "as_of_date", "statuses_json"} <= cols


def test_daily_switch_reads_the_config(tmp_path: Path) -> None:
    import shutil

    from vcp_scanner.daily import fundamentals_daily

    assert fundamentals_daily("config") is True
    cfg = tmp_path / "config"
    shutil.copytree("config", cfg)
    data = (cfg / "data.yaml").read_text().replace("daily_update: true", "daily_update: false")
    (cfg / "data.yaml").write_text(data)
    assert fundamentals_daily(str(cfg)) is False
