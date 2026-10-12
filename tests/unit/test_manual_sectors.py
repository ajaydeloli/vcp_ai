"""Hand-entered sectors (config/manual_sectors.yaml, DATA_SPECIFICATION §48.1): validation, NSE
always winning, history kept, and the stale-entry warning."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from vcp_scanner.api.context import Context
from vcp_scanner.api.reports import status
from vcp_scanner.data.providers.manual_sectors import (
    ManualSector,
    load_manual_sectors,
    stale_entries,
)
from vcp_scanner.data.repositories.duckdb_classification_repository import (
    SOURCE_MANUAL,
    Classification,
    DuckDBClassificationRepository,
)
from vcp_scanner.data.storage.duckdb_store import DuckDBStore

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"


def write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "manual_sectors.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_the_shipped_file_loads_empty() -> None:
    assert load_manual_sectors(REPO_CONFIG / "manual_sectors.yaml") == ([], [])


def test_entries_are_validated_and_bad_ones_named(tmp_path: Path) -> None:
    p = write(
        tmp_path,
        """
sectors:
  - {symbol: 20microns, sector: Metals & Mining, industry: Industrial Minerals, as_of: 2026-10-12}
  - {symbol: TYPO, sector: Metal & Mining, as_of: 2026-10-12}
  - {symbol: NODATE, sector: Power}
  - {symbol: 20MICRONS, sector: Power, as_of: 2026-10-12}
  - {sector: Power, as_of: 2026-10-12}
""",
    )
    entries, problems = load_manual_sectors(p)
    assert entries == [
        ManualSector("20MICRONS", "Metals & Mining", "Industrial Minerals", date(2026, 10, 12))
    ]
    assert problems == [
        "TYPO: 'Metal & Mining' is not one of NSE's sectors",
        "NODATE: as_of must be a date (YYYY-MM-DD)",
        "20MICRONS: listed twice",
        "entry 5: no symbol",
    ]
    assert load_manual_sectors(tmp_path / "missing.yaml") == ([], [])
    assert load_manual_sectors(write(tmp_path, "sectors: ["))[1][0].startswith(
        "manual_sectors.yaml: not valid YAML"
    )


def test_stale_after_a_year() -> None:
    e = [ManualSector("A", "Power", None, date(2025, 10, 1)),
         ManualSector("B", "Power", None, date(2026, 1, 1))]  # fmt: skip
    assert [x.symbol for x in stale_entries(e, date(2026, 10, 12))] == ["A"]


@pytest.fixture
def repo() -> DuckDBClassificationRepository:
    store = DuckDBStore(":memory:")
    store.migrate()
    return DuckDBClassificationRepository(store)


def test_nse_always_wins_and_history_is_kept(repo: DuckDBClassificationRepository) -> None:
    # a manual sector for a stock NSE already classifies is not applied
    repo.apply([Classification("I1", "Capital Goods")], date(2026, 10, 9))
    r = repo.apply([Classification("I1", "Power", source=SOURCE_MANUAL)], date(2026, 10, 12))
    assert (r.kept_nse, r.inserted, r.changed) == (1, 0, 0)
    assert repo.current("I1").sector == "Capital Goods"  # type: ignore[union-attr]
    # a manual stock: entered, then corrected by hand (old row closed, new one opened)
    repo.apply([Classification("I2", "Power", source=SOURCE_MANUAL)], date(2026, 10, 12))
    r = repo.apply([Classification("I2", "Utilities", source=SOURCE_MANUAL)], date(2026, 11, 6))
    assert r.changed == 1
    # NSE covers it later with a different sector: NSE replaces it, and the report says so
    r = repo.apply([Classification("I2", "Capital Goods")], date(2027, 3, 26))
    assert r.changed == 1 and r.replaced_manual == [("I2", "Utilities", "Capital Goods")]
    rows = repo._conn.execute(
        "SELECT sector, classification_source, valid_from, valid_to FROM"
        " instrument_classification WHERE instrument_id = 'I2' ORDER BY valid_from"
    ).fetchall()
    assert rows == [
        ("Power", SOURCE_MANUAL, date(2026, 10, 12), date(2026, 11, 6)),
        ("Utilities", SOURCE_MANUAL, date(2026, 11, 6), date(2027, 3, 26)),
        ("Capital Goods", "NSE_INDEX_LIST", date(2027, 3, 26), None),
    ]
    # the same NSE sector as the manual one: the source still switches to NSE, quietly
    repo.apply([Classification("I3", "Power", source=SOURCE_MANUAL)], date(2026, 10, 12))
    r = repo.apply([Classification("I3", "Power")], date(2027, 3, 26))
    assert r.changed == 1 and r.replaced_manual == []
    assert repo.current("I3").source == "NSE_INDEX_LIST"  # type: ignore[union-attr]


def test_status_warns_about_old_and_unusable_manual_entries(tmp_path: Path) -> None:
    write(
        tmp_path,
        """
sectors:
  - {symbol: OLDONE, sector: Power, as_of: 2025-09-01}
  - {symbol: FRESH, sector: Power, as_of: 2026-09-01}
  - {symbol: TYPO, sector: Powr, as_of: 2026-09-01}
""",
    )
    store = DuckDBStore(":memory:")
    store.migrate()
    ctx = Context("h", "breadth50", (), tmp_path, tmp_path)
    now = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
    warnings = status(store.conn, ctx, now, now).warnings
    assert any(w.startswith("1 manual sector is over a year old (OLDONE)") for w in warnings)
    assert (
        "config/manual_sectors.yaml: 1 entry not used (TYPO: 'Powr' is not one of NSE's"
        " sectors)." in warnings
    )
