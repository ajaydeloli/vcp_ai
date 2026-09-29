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
