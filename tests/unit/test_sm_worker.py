"""Unit tests for SecurityMasterIngestionWorker (Phase 3)."""

from __future__ import annotations

from datetime import date

import pytest

from vcp_scanner.data.ingestion.sm_worker import SecurityMasterIngestionWorker
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.market import SecurityRecord, SurveillanceRecord

# ---------------------------------------------------------------------------
# Fake providers for testing
# ---------------------------------------------------------------------------


class FakeSecurityMasterProvider:
    """Returns canned SecurityRecord lists."""

    def __init__(self, records: list[SecurityRecord]) -> None:
        self._records = records

    def get_security_history(self, start: date, end: date) -> list[SecurityRecord]:
        return self._records


class FakeSurveillanceProvider:
    """Returns canned SurveillanceRecord lists."""

    def __init__(self, records: list[SurveillanceRecord]) -> None:
        self._records = records

    def get_flags(self, start: date, end: date) -> list[SurveillanceRecord]:
        return self._records


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def store():
    with DuckDBStore(":memory:") as s:
        s.migrate()
        yield s


# ---------------------------------------------------------------------------
# Security Master tests
# ---------------------------------------------------------------------------


def test_security_master_initial_insert(store):
    """First ingestion should insert all records."""
    records = [
        SecurityRecord(
            instrument_id="NSE_EQ|RELIANCE",
            symbol="RELIANCE",
            exchange="NSE",
            valid_from=date(2000, 1, 1),
            isin="INE002A01018",
            series="EQ",
            listing_date=date(1977, 11, 29),
        ),
        SecurityRecord(
            instrument_id="NSE_EQ|TCS",
            symbol="TCS",
            exchange="NSE",
            valid_from=date(2004, 8, 25),
            isin="INE467B01029",
            series="EQ",
            listing_date=date(2004, 8, 25),
        ),
    ]

    worker = SecurityMasterIngestionWorker(
        store=store,
        security_master_provider=FakeSecurityMasterProvider(records),
        surveillance_provider=FakeSurveillanceProvider([]),
    )

    result = worker.run(start=date(2000, 1, 1), end=date(2024, 1, 1))
    assert result["security_inserted"] == 2
    assert result["security_unchanged"] == 0

    # Verify rows in DB
    rows = store.conn.execute(
        "SELECT instrument_id FROM security_master_history WHERE known_to IS NULL"
    ).fetchall()
    assert len(rows) == 2


def test_security_master_idempotent_no_change(store):
    """Re-ingesting same data should not create duplicate rows."""
    records = [
        SecurityRecord(
            instrument_id="NSE_EQ|INFY",
            symbol="INFY",
            exchange="NSE",
            valid_from=date(1993, 2, 8),
            isin="INE009A01021",
            series="EQ",
        ),
    ]

    sm_provider = FakeSecurityMasterProvider(records)
    worker = SecurityMasterIngestionWorker(
        store=store,
        security_master_provider=sm_provider,
        surveillance_provider=FakeSurveillanceProvider([]),
    )

    # First run
    r1 = worker.run(start=date(2000, 1, 1), end=date(2024, 1, 1))
    assert r1["security_inserted"] == 1

    # Second run — nothing changed
    r2 = worker.run(start=date(2000, 1, 1), end=date(2024, 1, 1))
    assert r2["security_inserted"] == 0
    assert r2["security_unchanged"] == 1

    # Only 1 current row
    rows = store.conn.execute(
        "SELECT * FROM security_master_history WHERE known_to IS NULL"
    ).fetchall()
    assert len(rows) == 1


def test_security_master_supersession_on_change(store):
    """When data changes, old row must be closed and new row inserted."""
    v1 = SecurityRecord(
        instrument_id="NSE_EQ|HDFC",
        symbol="HDFC",
        exchange="NSE",
        valid_from=date(1995, 1, 1),
        isin="INE001A01036",
        series="EQ",
    )

    worker = SecurityMasterIngestionWorker(
        store=store,
        security_master_provider=FakeSecurityMasterProvider([v1]),
        surveillance_provider=FakeSurveillanceProvider([]),
    )
    worker.run(start=date(2000, 1, 1), end=date(2024, 1, 1))

    # Now the ISIN changes (e.g. merger/demerger)
    v2 = SecurityRecord(
        instrument_id="NSE_EQ|HDFC",
        symbol="HDFC",
        exchange="NSE",
        valid_from=date(1995, 1, 1),
        isin="INE001A01099",  # changed
        series="EQ",
    )

    worker2 = SecurityMasterIngestionWorker(
        store=store,
        security_master_provider=FakeSecurityMasterProvider([v2]),
        surveillance_provider=FakeSurveillanceProvider([]),
    )
    r = worker2.run(start=date(2000, 1, 1), end=date(2024, 1, 1))
    assert r["security_inserted"] == 1

    # Old row should be closed
    closed = store.conn.execute(
        "SELECT isin FROM security_master_history "
        "WHERE instrument_id = 'NSE_EQ|HDFC' AND known_to IS NOT NULL"
    ).fetchall()
    assert len(closed) == 1
    assert closed[0][0] == "INE001A01036"

    # New row should be current
    current = store.conn.execute(
        "SELECT isin FROM security_master_history "
        "WHERE instrument_id = 'NSE_EQ|HDFC' AND known_to IS NULL"
    ).fetchall()
    assert len(current) == 1
    assert current[0][0] == "INE001A01099"


# ---------------------------------------------------------------------------
# Surveillance Flag tests
# ---------------------------------------------------------------------------


def test_surveillance_flags_insert(store):
    """New flags should be inserted into surveillance_flags_history."""
    flags = [
        SurveillanceRecord(
            instrument_id="NSE_EQ|YESBANK",
            flag="ASM",
            valid_from=date(2024, 1, 1),
            source="NSE",
            extra={"stage": "II"},
        ),
    ]

    worker = SecurityMasterIngestionWorker(
        store=store,
        security_master_provider=FakeSecurityMasterProvider([]),
        surveillance_provider=FakeSurveillanceProvider(flags),
    )

    r = worker.run(start=date(2024, 1, 1), end=date(2024, 6, 1))
    assert r["flags_inserted"] == 1
    assert r["flags_closed"] == 0

    rows = store.conn.execute(
        "SELECT flag_type, stage FROM surveillance_flags_history "
        "WHERE instrument_id = 'NSE_EQ|YESBANK' AND known_to IS NULL"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "ASM"
    assert rows[0][1] == "II"


def test_surveillance_flags_close_when_removed(store):
    """When a flag disappears from the feed, it should be closed."""
    # First run: YESBANK has ASM
    flags_v1 = [
        SurveillanceRecord(
            instrument_id="NSE_EQ|YESBANK",
            flag="ASM",
            valid_from=date(2024, 1, 1),
            source="NSE",
        ),
    ]

    worker1 = SecurityMasterIngestionWorker(
        store=store,
        security_master_provider=FakeSecurityMasterProvider([]),
        surveillance_provider=FakeSurveillanceProvider(flags_v1),
    )
    worker1.run(start=date(2024, 1, 1), end=date(2024, 3, 1))

    # Second run: ASM removed from YESBANK
    worker2 = SecurityMasterIngestionWorker(
        store=store,
        security_master_provider=FakeSecurityMasterProvider([]),
        surveillance_provider=FakeSurveillanceProvider([]),  # empty feed
    )
    r = worker2.run(start=date(2024, 3, 1), end=date(2024, 6, 1))
    assert r["flags_closed"] == 1

    # The old row should have known_to and valid_to set
    closed = store.conn.execute(
        "SELECT known_to, valid_to FROM surveillance_flags_history "
        "WHERE instrument_id = 'NSE_EQ|YESBANK' AND known_to IS NOT NULL"
    ).fetchall()
    assert len(closed) == 1
    assert closed[0][0] is not None  # known_to set
    assert closed[0][1] is not None  # valid_to set


def test_surveillance_flags_idempotent_existing_flag(store):
    """Re-ingesting same active flag should not create duplicates."""
    flags = [
        SurveillanceRecord(
            instrument_id="NSE_EQ|RCOM",
            flag="T2T",
            valid_from=date(2023, 1, 1),
            source="NSE",
        ),
    ]

    worker = SecurityMasterIngestionWorker(
        store=store,
        security_master_provider=FakeSecurityMasterProvider([]),
        surveillance_provider=FakeSurveillanceProvider(flags),
    )

    # Run twice
    worker.run(start=date(2023, 1, 1), end=date(2024, 1, 1))
    r2 = worker.run(start=date(2023, 1, 1), end=date(2024, 1, 1))

    assert r2["flags_inserted"] == 0  # already exists

    rows = store.conn.execute(
        "SELECT * FROM surveillance_flags_history "
        "WHERE instrument_id = 'NSE_EQ|RCOM' AND known_to IS NULL"
    ).fetchall()
    assert len(rows) == 1  # no duplicates


# ---------------------------------------------------------------------------
# Review fixes: multi-period history, stage changes, non-snapshot providers
# ---------------------------------------------------------------------------


def _sm_worker(store, records=(), flags=()):
    return SecurityMasterIngestionWorker(
        store=store,
        security_master_provider=FakeSecurityMasterProvider(list(records)),
        surveillance_provider=FakeSurveillanceProvider(list(flags)),
    )


def test_multi_period_history_is_kept_whole(store):
    """Two periods for one instrument must both stay current, not collapse to the last."""
    periods = [
        SecurityRecord(
            instrument_id="NSE_EQ|ABC",
            symbol="OLDABC",
            exchange="NSE",
            valid_from=date(2010, 1, 1),
            valid_to=date(2019, 12, 31),
            isin="INE000A01010",
            series="EQ",
            source="NSE",
        ),
        SecurityRecord(
            instrument_id="NSE_EQ|ABC",
            symbol="ABC",
            exchange="NSE",
            valid_from=date(2020, 1, 1),
            isin="INE000A01010",
            series="EQ",
            source="NSE",
        ),
    ]
    _sm_worker(store, records=periods).run(start=date(2010, 1, 1), end=date(2024, 1, 1))

    rows = store.conn.execute(
        "SELECT symbol FROM security_master_history "
        "WHERE instrument_id = 'NSE_EQ|ABC' AND known_to IS NULL ORDER BY valid_from"
    ).fetchall()
    assert [r[0] for r in rows] == ["OLDABC", "ABC"]


def test_asm_stage_change_supersedes_old_stage(store):
    def flag(stage):
        return SurveillanceRecord(
            instrument_id="NSE_EQ|YESBANK",
            flag="ASM",
            valid_from=date(2024, 1, 1),
            source="NSE",
            extra={"stage": stage},
        )

    _sm_worker(store, flags=[flag("II")]).run(start=date(2024, 1, 1), end=date(2024, 3, 1))
    r = _sm_worker(store, flags=[flag("III")]).run(start=date(2024, 1, 1), end=date(2024, 3, 1))

    assert r["flags_inserted"] == 1
    current = store.conn.execute(
        "SELECT stage FROM surveillance_flags_history WHERE known_to IS NULL"
    ).fetchall()
    assert current == [("III",)]
    total = store.conn.execute("SELECT COUNT(*) FROM surveillance_flags_history").fetchone()[0]
    assert total == 2  # stage II kept as history


def test_absent_flags_kept_when_provider_returns_event_history(store):
    flag = SurveillanceRecord(
        instrument_id="NSE_EQ|YESBANK",
        flag="ASM",
        valid_from=date(2024, 1, 1),
        source="NSE",
        extra={"stage": "II"},
    )
    _sm_worker(store, flags=[flag]).run(start=date(2024, 1, 1), end=date(2024, 3, 1))

    history_provider = FakeSurveillanceProvider([])
    history_provider.returns_active_snapshot = False
    worker = SecurityMasterIngestionWorker(
        store=store,
        security_master_provider=FakeSecurityMasterProvider([]),
        surveillance_provider=history_provider,
    )
    r = worker.run(start=date(2023, 1, 1), end=date(2023, 6, 1))

    assert r["flags_closed"] == 0


def test_record_without_source_uses_default_provider_name(store):
    """Regression: rec.source empty used to raise AttributeError (no PROVIDER_NAME)."""
    flag = SurveillanceRecord(
        instrument_id="NSE_EQ|NOSRC", flag="T2T", valid_from=date(2024, 1, 1), source=None
    )
    _sm_worker(store, flags=[flag]).run(start=date(2024, 1, 1), end=date(2024, 3, 1))
    row = store.conn.execute(
        "SELECT source FROM surveillance_flags_history WHERE instrument_id = 'NSE_EQ|NOSRC'"
    ).fetchone()
    assert row == ("NSE",)


# ---------------------------------------------------------------------------
# Audit P1-1: the live listing seeds the instruments table
# ---------------------------------------------------------------------------


def _listed(symbol: str, isin: str, series: str = "EQ") -> SecurityRecord:
    return SecurityRecord(
        instrument_id=f"NSE_EQ|{symbol}",
        symbol=symbol,
        exchange="NSE",
        valid_from=date(2010, 1, 1),
        isin=isin,
        series=series,
        listing_date=date(2010, 1, 1),
        source="NSE",
    )


def _instrument_rows(store):
    return store.conn.execute(
        "SELECT instrument_id, symbol, isin, segment, is_active FROM instruments "
        "ORDER BY instrument_id"
    ).fetchall()


def test_live_listing_seeds_instruments(store):
    from vcp_scanner.data.repositories.duckdb_instrument_repository import (
        DuckDBInstrumentRepository,
    )

    stats = _sm_worker(store, [_listed("AAA", "INE000A"), _listed("BBB", "INE000B", "BE")]).run(
        start=date(2020, 1, 1), end=date(2024, 1, 1)
    )
    assert stats["instruments_upserted"] == 2
    assert _instrument_rows(store) == [
        ("NSE_EQ|AAA", "AAA", "INE000A", "EQ", True),
        ("NSE_EQ|BBB", "BBB", "INE000B", "BE", True),
    ]
    # what `vcp ingest market` reads
    loaded = DuckDBInstrumentRepository(store).load_instruments()
    assert {(i.instrument_id, i.isin, i.series) for i in loaded} == {
        ("NSE_EQ|AAA", "INE000A", "EQ"),
        ("NSE_EQ|BBB", "INE000B", "BE"),
    }


def test_symbol_rename_keeps_the_instrument_id(store):
    from vcp_scanner.data.repositories.duckdb_instrument_repository import (
        DuckDBInstrumentResolver,
    )

    def worker(records):
        return SecurityMasterIngestionWorker(
            store=store,
            security_master_provider=FakeSecurityMasterProvider(records),
            surveillance_provider=FakeSurveillanceProvider([]),
            resolver=DuckDBInstrumentResolver(store),
        )

    worker([_listed("OLDNAME", "INE123X")]).run(start=date(2020, 1, 1), end=date(2024, 1, 1))
    worker([_listed("NEWNAME", "INE123X")]).run(start=date(2020, 1, 1), end=date(2024, 1, 1))

    assert _instrument_rows(store) == [("NSE_EQ|OLDNAME", "NEWNAME", "INE123X", "EQ", True)]


def test_instrument_that_left_the_listing_is_deactivated_not_deleted(store):
    _sm_worker(store, [_listed("AAA", "INE000A"), _listed("GONE", "INE000G")]).run(
        start=date(2020, 1, 1), end=date(2024, 1, 1)
    )
    stats = _sm_worker(store, [_listed("AAA", "INE000A")]).run(
        start=date(2020, 1, 1), end=date(2024, 1, 1)
    )
    assert stats["instruments_deactivated"] == 1
    assert _instrument_rows(store)[1] == ("NSE_EQ|GONE", "GONE", "INE000G", "EQ", False)


def test_delisted_records_never_create_instruments(store):
    delisted = SecurityRecord(
        instrument_id="NSE_EQ|DEAD",
        symbol="DEAD",
        exchange="NSE",
        valid_from=date(2005, 1, 1),
        valid_to=date(2015, 1, 1),
        isin="INE000D",
        delisting_date=date(2015, 1, 1),
        source="NSE_DELISTED",
    )
    SecurityMasterIngestionWorker(
        store=store,
        security_master_provider=FakeSecurityMasterProvider([_listed("AAA", "INE000A")]),
        surveillance_provider=FakeSurveillanceProvider([]),
        delisting_provider=FakeSecurityMasterProvider([delisted]),
    ).run(start=date(2000, 1, 1), end=date(2024, 1, 1))
    assert [r[0] for r in _instrument_rows(store)] == ["NSE_EQ|AAA"]


def test_each_run_records_which_lists_were_collected(store):
    """Daily run: the collection day is stored per list, even when a list is empty."""
    flag = SurveillanceRecord(
        instrument_id="NSE_EQ|YESBANK", flag="ASM", valid_from=date(2024, 1, 1), source="NSE"
    )
    worker = _sm_worker(store, flags=[flag])
    worker._surv_provider.collected_flag_types = frozenset({"ASM", "GSM"})
    worker.run(start=date(2024, 1, 1), end=date(2024, 3, 1))
    rows = store.conn.execute(
        "SELECT flag_type, record_count FROM surveillance_collections ORDER BY 1"
    ).fetchall()
    assert rows == [("ASM", 1), ("GSM", 0)]
