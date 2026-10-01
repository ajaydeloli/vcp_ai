"""Audit P1-8b (D2): universe snapshot ids come from their inputs; scans can name one."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from vcp_scanner.config.models import UniverseConfig
from vcp_scanner.data.repositories.duckdb_universe_repository import DuckDBUniverseRepository
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.data.universe.builder import UniverseBuilder, universe_snapshot_id

D = date(2026, 9, 30)
K = datetime(2026, 10, 1, 12, 51, 3, 616299, tzinfo=UTC)
IST = timezone(timedelta(hours=5, minutes=30))


def test_id_is_a_function_of_its_inputs() -> None:
    a = universe_snapshot_id(D, K, "c0ffee", "2.0")
    assert a == universe_snapshot_id(D, K, "c0ffee", "2.0")
    assert a.startswith("uv_20260930_") and len(a) == len("uv_20260930_") + 10
    assert a == universe_snapshot_id(D, K.astimezone(IST), "c0ffee", "2.0")  # same instant
    others = {
        universe_snapshot_id(date(2026, 9, 29), K, "c0ffee", "2.0"),
        universe_snapshot_id(D, K + timedelta(microseconds=1), "c0ffee", "2.0"),
        universe_snapshot_id(D, K, "decaf0", "2.0"),
        universe_snapshot_id(D, K, "c0ffee", "2.1"),
    }
    assert a not in others and len(others) == 4


@pytest.fixture()
def store():
    s = DuckDBStore(":memory:")
    s.migrate()
    yield s
    s.close()


def test_rebuild_with_the_same_cutoff_replaces_instead_of_forking(store: DuckDBStore) -> None:
    repo = DuckDBUniverseRepository(store)
    builder = UniverseBuilder(repo, UniverseConfig())
    first, members = builder.build_snapshot(D, known_at=K)
    repo.save_snapshot(first, members)
    again, members = builder.build_snapshot(D, known_at=K)
    repo.save_snapshot(again, members)
    assert again.universe_snapshot_id == first.universe_snapshot_id
    (n,) = store.conn.execute("SELECT count(*) FROM universe_snapshots").fetchone()
    assert n == 1
    later, members = builder.build_snapshot(D, known_at=K + timedelta(hours=1))
    repo.save_snapshot(later, members)
    assert later.universe_snapshot_id != first.universe_snapshot_id


def test_load_snapshot_by_id_or_latest(store: DuckDBStore) -> None:
    repo = DuckDBUniverseRepository(store)
    builder = UniverseBuilder(repo, UniverseConfig())
    old, m = builder.build_snapshot(D, known_at=K)
    repo.save_snapshot(old, m)
    new, m = builder.build_snapshot(D, known_at=K + timedelta(hours=1))
    repo.save_snapshot(new, m)
    assert repo.latest_snapshot_id(D) == new.universe_snapshot_id
    # An earlier cutoff is chosen only when named: "latest" would pick the newer snapshot.
    assert repo.load_snapshot(D, old.universe_snapshot_id) == []
    with pytest.raises(ValueError, match="not a snapshot for"):
        repo.load_snapshot(date(2026, 9, 29), old.universe_snapshot_id)
    assert repo.latest_snapshot_id(date(2026, 9, 29)) is None


def test_trend_repo_reads_rs_of_the_named_universe(store: DuckDBStore) -> None:
    from vcp_scanner.data.repositories.duckdb_trend_repository import DuckDBTrendRepository

    for uid, created, rank in (("uv_old", K, 91.0), ("uv_new", K + timedelta(hours=1), 55.0)):
        store.conn.execute(
            "INSERT INTO universe_snapshots (universe_snapshot_id, universe_name, as_of_date,"
            " created_at, config_hash, method_version, survivorship_status)"
            " VALUES (?, 'VCP_BASE', ?, ?, 'h', '2.0', 'PARTIAL')",
            [uid, D, created],
        )
        store.conn.execute(
            "INSERT INTO relative_strength_snapshots (as_of_date, instrument_id, rs_raw, rs_rank,"
            " population_size, rs_status, universe_snapshot_id, calculation_version,"
            " data_snapshot_id) VALUES (?, 'NSE_EQ|X', 1.0, ?, 10, 'OK', ?, 'rs-1.1.0', 'LIVE')",
            [D, rank, uid],
        )
    latest = DuckDBTrendRepository(store).load_relative_strength("NSE_EQ|X", D, "rs-1.1.0")
    named = DuckDBTrendRepository(store, rs_universe_snapshot_id="uv_old").load_relative_strength(
        "NSE_EQ|X", D, "rs-1.1.0"
    )
    assert latest is not None and latest.rs_rank == 55.0
    assert named is not None and named.rs_rank == 91.0
