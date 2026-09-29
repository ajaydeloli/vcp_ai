from datetime import UTC, date, datetime

import pytest

from vcp_scanner.data.repositories.duckdb_universe_repository import DuckDBUniverseRepository
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.universe import SurvivorshipStatus, UniverseMembership, UniverseSnapshot


@pytest.fixture
def store():
    with DuckDBStore(":memory:") as s:
        s.migrate()
        yield s


def test_save_and_load_universe_snapshot(store):
    repo = DuckDBUniverseRepository(store)

    as_of = date(2023, 1, 1)

    snapshot = UniverseSnapshot(
        universe_snapshot_id="test_snap_1",
        universe_name="VCP_BASE",
        as_of_date=as_of,
        created_at=datetime.now(UTC),
        config_hash="abc1234",
        method_version="1.0",
        survivorship_status=SurvivorshipStatus.POINT_IN_TIME_COMPLETE,
    )

    memberships = [
        UniverseMembership(
            universe_snapshot_id="test_snap_1",
            instrument_id="inst_1",
            eligible=True,
            price=150.0,
            avg_traded_value=10_000_000.0,
        ),
        UniverseMembership(
            universe_snapshot_id="test_snap_1",
            instrument_id="inst_2",
            eligible=False,
            exclusion_reason="Price too low",
            price=5.0,
        ),
    ]

    repo.save_snapshot(snapshot, memberships)

    # Load should only return eligible instruments
    eligible_ids = repo.load_snapshot(as_of)
    assert eligible_ids == ["inst_1"]
