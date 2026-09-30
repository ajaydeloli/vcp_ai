"""Stage 1 of audit finding P0-1: the data-snapshot boundary for adjusted prices.

Adjusted prices must be reproducible "as known then": a later price correction or a later
corporate action must not change a series built from an earlier snapshot. All data is
synthetic (AGENTS.md rule 10).
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from vcp_scanner.data.adjustment.builder import STATUS_BUILT, AdjustedPriceBuilder
from vcp_scanner.data.providers.fake import make_candle
from vcp_scanner.data.repositories.duckdb_corporate_action_repository import (
    DuckDBCorporateActionRepository,
)
from vcp_scanner.data.repositories.duckdb_feature_repository import DuckDBFeatureRepository
from vcp_scanner.data.repositories.duckdb_market_repository import DuckDBMarketDataRepository
from vcp_scanner.data.repositories.duckdb_snapshot_repository import DuckDBSnapshotRepository
from vcp_scanner.data.schema import DailyPriceAdjustedRow
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.corporate_actions import CorporateActionAdjustment
from vcp_scanner.domain.snapshot import (
    LIVE_SNAPSHOT_ID,
    snapshot_id_for,
    validate_snapshot_id,
)

IID = "NSE_EQ|SNAPCO"
DAYS = [d for d in (date(2024, 1, 1) + timedelta(days=i) for i in range(12)) if d.weekday() < 5]
EX_DATE = date(2024, 1, 8)

T_INGEST = datetime(2024, 2, 1, 12, 0, tzinfo=UTC)
KNOWN_1 = datetime(2024, 2, 1, 18, 0, tzinfo=UTC)  # snapshot 1 sees only the first ingest
T_LATER = datetime(2024, 2, 5, 12, 0, tzinfo=UTC)  # correction + split learned here
KNOWN_2 = datetime(2024, 2, 6, 12, 0, tzinfo=UTC)
COMPUTED = datetime(2024, 2, 7, 12, 0, tzinfo=UTC)


class Env:
    """Store whose market repository clock is controllable, to stage 'learning' over time."""

    def __init__(self) -> None:
        self.store = DuckDBStore(":memory:")
        self.store.migrate()
        self.now = T_INGEST
        self.market = DuckDBMarketDataRepository(self.store, clock=lambda: self.now)
        self.ca = DuckDBCorporateActionRepository(self.store)
        self.snapshots = DuckDBSnapshotRepository(self.store)
        self.builder = AdjustedPriceBuilder(self.market, self.ca)

    def ingest(self, close: float = 100.0) -> None:
        self.market.save_daily(
            [
                make_candle(
                    IID, d, open_=close, high=close + 1, low=close - 1, close=close, volume=1000
                )
                for d in DAYS
            ]
        )

    def closes(self, snapshot_id: str) -> list[float]:
        rows = self.market.load_adjusted_daily(
            IID, date(1900, 1, 1), date(2999, 1, 1), data_snapshot_id=snapshot_id
        )
        return [r.close_adj for r in rows]


@pytest.fixture()
def env() -> Env:
    return Env()


def _split() -> CorporateActionAdjustment:
    return CorporateActionAdjustment(
        resolution_id="RES-S",
        instrument_id=IID,
        effective_date=EX_DATE,
        price_factor=0.5,
        volume_factor=2.0,
        cumulative_price_factor=0.5,
        cumulative_volume_factor=2.0,
        source="INTERNAL",
        calculation_version="1.0",
    )


# ---------------------------------------------------------------------------
# Snapshot identity and repository
# ---------------------------------------------------------------------------


def test_snapshot_id_is_deterministic_and_utc() -> None:
    a = snapshot_id_for(datetime(2024, 2, 1, 18, 0, tzinfo=UTC))
    assert a == "snap-20240201T180000Z"
    assert snapshot_id_for(datetime(2024, 2, 1, 18, 0, tzinfo=UTC)) == a


def test_snapshot_id_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        snapshot_id_for(datetime(2024, 2, 1, 18, 0))  # noqa: DTZ001 - naive on purpose


@pytest.mark.parametrize("bad", ["", "a b", "x'; DROP TABLE t; --", "-lead", "a" * 200])
def test_validate_snapshot_id_rejects_unsafe_ids(bad: str) -> None:
    with pytest.raises(ValueError, match="Invalid data snapshot id"):
        validate_snapshot_id(bad)


def test_snapshot_create_is_idempotent(env: Env) -> None:
    first = env.snapshots.create(KNOWN_1, created_at=COMPUTED, description="first")
    again = env.snapshots.create(KNOWN_1, created_at=COMPUTED + timedelta(days=9))
    assert again == first  # the original row wins; no fork, no overwrite
    count = env.store.conn.execute("SELECT COUNT(*) FROM data_snapshots").fetchone()
    assert count is not None and count[0] == 1


def test_snapshot_latest_and_load(env: Env) -> None:
    assert env.snapshots.latest() is None
    s1 = env.snapshots.create(KNOWN_1, created_at=COMPUTED)
    s2 = env.snapshots.create(KNOWN_2, created_at=COMPUTED)
    assert env.snapshots.latest() == s2
    assert env.snapshots.load(s1.data_snapshot_id) == s1
    assert env.snapshots.load("snap-nope") is None


# ---------------------------------------------------------------------------
# Point-in-time behaviour: the core of P0-1
# ---------------------------------------------------------------------------


def test_later_correction_and_split_do_not_change_earlier_snapshot(env: Env) -> None:
    env.ingest(close=100.0)
    snap1 = env.snapshots.create(KNOWN_1, created_at=COMPUTED)
    env.builder.build_all(computed_at=COMPUTED, snapshot=snap1)
    before = env.closes(snap1.data_snapshot_id)
    assert before == [100.0] * len(DAYS)

    # Later, the provider corrects every bar and a split is discovered.
    env.now = T_LATER
    env.ingest(close=110.0)
    env.ca.save_adjustment(_split(), known_from=T_LATER)

    snap2 = env.snapshots.create(KNOWN_2, created_at=COMPUTED)
    env.builder.build_all(computed_at=COMPUTED + timedelta(days=1), snapshot=snap2)

    # Snapshot 1 is byte-for-byte what it was; snapshot 2 sees the correction AND the split.
    assert env.closes(snap1.data_snapshot_id) == before
    after = env.closes(snap2.data_snapshot_id)
    assert after[0] == pytest.approx(55.0)  # 110 * 0.5, before the ex-date
    assert after[-1] == pytest.approx(110.0)  # on/after the ex-date


def test_rebuilding_an_earlier_snapshot_after_new_knowledge_gives_same_rows(env: Env) -> None:
    env.ingest(close=100.0)
    snap1 = env.snapshots.create(KNOWN_1, created_at=COMPUTED)
    env.builder.build_all(computed_at=COMPUTED, snapshot=snap1)
    before = env.closes(snap1.data_snapshot_id)

    env.now = T_LATER
    env.ingest(close=110.0)
    env.ca.save_adjustment(_split(), known_from=T_LATER)

    # Recomputing snapshot 1 today must still reproduce the original series.
    result = env.builder.build_for_instrument(
        IID, computed_at=COMPUTED + timedelta(days=30), snapshot=snap1
    )
    assert result.status == STATUS_BUILT
    assert result.data_snapshot_id == snap1.data_snapshot_id
    assert env.closes(snap1.data_snapshot_id) == before


def test_snapshot_rebuild_is_idempotent(env: Env) -> None:
    env.ingest()
    snap = env.snapshots.create(KNOWN_1, created_at=COMPUTED)
    env.builder.build_all(computed_at=COMPUTED, snapshot=snap)
    env.builder.build_all(computed_at=COMPUTED + timedelta(hours=1), snapshot=snap)
    count = env.store.conn.execute("SELECT COUNT(*) FROM daily_prices_adjusted").fetchone()
    assert count is not None and count[0] == len(DAYS)


def test_snapshot_before_any_data_yields_no_prices(env: Env) -> None:
    env.ingest()
    early = env.snapshots.create(datetime(2024, 1, 1, tzinfo=UTC), created_at=COMPUTED)
    result = env.builder.build_for_instrument(IID, computed_at=COMPUTED, snapshot=early)
    assert result.status == "NO_PRICES"
    assert env.closes(early.data_snapshot_id) == []


def test_live_and_snapshot_series_are_isolated(env: Env) -> None:
    env.ingest(close=100.0)
    snap = env.snapshots.create(KNOWN_1, created_at=COMPUTED)
    env.builder.build_all(computed_at=COMPUTED, snapshot=snap)
    env.builder.build_all(computed_at=COMPUTED)  # LIVE

    env.now = T_LATER
    env.ingest(close=120.0)
    env.builder.build_all(computed_at=COMPUTED + timedelta(days=5))  # LIVE moves on

    assert env.closes(snap.data_snapshot_id) == [100.0] * len(DAYS)
    assert env.closes(LIVE_SNAPSHOT_ID) == [120.0] * len(DAYS)
    assert env.market.current_adjustment_version(IID, snap.data_snapshot_id) is not None
    assert env.market.current_adjustment_version(IID, "snap-unbuilt") is None


def test_feature_repository_reads_only_its_snapshot(env: Env) -> None:
    env.ingest(close=100.0)
    snap = env.snapshots.create(KNOWN_1, created_at=COMPUTED)
    env.builder.build_all(computed_at=COMPUTED, snapshot=snap)
    env.now = T_LATER
    env.ingest(close=120.0)
    env.builder.build_all(computed_at=COMPUTED + timedelta(days=5))  # LIVE

    frozen = DuckDBFeatureRepository(env.store, snap.data_snapshot_id)
    live = DuckDBFeatureRepository(env.store)
    as_of = DAYS[-1]
    assert frozen.load_adjusted_closes(IID, as_of, 1)[0].close == 100.0
    assert live.load_adjusted_closes(IID, as_of, 1)[0].close == 120.0
    # A snapshot that was never built for this instrument yields nothing, not LIVE data.
    empty = DuckDBFeatureRepository(env.store, "snap-unbuilt")
    assert empty.load_adjusted_closes(IID, as_of, 5) == []


def test_feature_repository_rejects_unsafe_snapshot_id(env: Env) -> None:
    with pytest.raises(ValueError, match="Invalid data snapshot id"):
        DuckDBFeatureRepository(env.store, "x'; DROP TABLE t; --")


# ---------------------------------------------------------------------------
# Schema and migration
# ---------------------------------------------------------------------------

_LEGACY_ADJUSTED = """
CREATE TABLE daily_prices_adjusted (
    instrument_id VARCHAR NOT NULL, trade_date DATE NOT NULL,
    open_adj DOUBLE NOT NULL, high_adj DOUBLE NOT NULL, low_adj DOUBLE NOT NULL,
    close_adj DOUBLE NOT NULL, volume_adj DOUBLE,
    adjustment_version VARCHAR NOT NULL,
    price_factor_applied DECIMAL(18,8) NOT NULL, volume_factor_applied DECIMAL(18,8) NOT NULL,
    computed_from_snapshot_id VARCHAR, computed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (instrument_id, trade_date, adjustment_version)
)
"""


def test_migration_moves_legacy_adjusted_rows_to_live() -> None:
    store = DuckDBStore(":memory:")
    store.conn.execute(_LEGACY_ADJUSTED)
    store.conn.execute(
        "INSERT INTO daily_prices_adjusted VALUES "
        "('X', '2024-01-02', 1, 2, 0.5, 1.5, 10, 'v1', 1, 1, NULL, '2024-02-01T00:00:00Z')"
    )
    store.migrate()

    rows = store.conn.execute(
        "SELECT instrument_id, close_adj, computed_from_snapshot_id FROM daily_prices_adjusted"
    ).fetchall()
    assert rows == [("X", 1.5, LIVE_SNAPSHOT_ID)]
    # The view is recreated and exposes the migrated row.
    view = store.conn.execute(
        "SELECT COUNT(*) FROM daily_prices_adjusted_current WHERE computed_from_snapshot_id = ?",
        [LIVE_SNAPSHOT_ID],
    ).fetchone()
    assert view is not None and view[0] == 1
    # Same version, different snapshot is now a legal, separate row (key includes snapshot).
    store.conn.execute(
        "INSERT INTO daily_prices_adjusted VALUES "
        "('X', '2024-01-02', 1, 2, 0.5, 9.9, 10, 'v1', 1, 1, 'snap-a', '2024-02-01T00:00:00Z')"
    )
    # Migration is idempotent.
    store.migrate()
    count = store.conn.execute("SELECT COUNT(*) FROM daily_prices_adjusted").fetchone()
    assert count is not None and count[0] == 2


def test_adjusted_row_none_snapshot_is_stored_as_live(env: Env) -> None:
    row = DailyPriceAdjustedRow(
        instrument_id=IID,
        trade_date=DAYS[0],
        open_adj=1.0,
        high_adj=2.0,
        low_adj=0.5,
        close_adj=1.5,
        volume_adj=None,
        adjustment_version="v1",
        price_factor_applied=Decimal("1"),
        volume_factor_applied=Decimal("1"),
        computed_at=COMPUTED,
    )
    env.market.save_adjusted_daily([row])
    stored = env.store.conn.execute(
        "SELECT computed_from_snapshot_id FROM daily_prices_adjusted"
    ).fetchone()
    assert stored == (LIVE_SNAPSHOT_ID,)
    loaded = env.market.load_adjusted_daily(IID, DAYS[0], DAYS[0])
    assert loaded[0].computed_from_snapshot_id is None  # LIVE reads back as "unfrozen"


# ---------------------------------------------------------------------------
# Guard: nobody may read the cross-snapshot view without naming a snapshot
# ---------------------------------------------------------------------------


def test_every_read_of_adjusted_view_filters_by_snapshot() -> None:
    src = Path(__file__).resolve().parents[2] / "src" / "vcp_scanner"
    view_read = re.compile(r"\bFROM\s+daily_prices_adjusted_current\b", re.IGNORECASE)
    snapshot_filter = re.compile(r"computed_from_snapshot_id\s*=\s*\?")
    offenders: list[str] = []
    for path in src.rglob("*.py"):
        text = path.read_text()
        reads = len(view_read.findall(text))
        filters = len(snapshot_filter.findall(text))
        if reads and filters < reads:
            offenders.append(f"{path.relative_to(src)}: {reads} view read(s), {filters} filter(s)")
    assert not offenders, "Unfiltered reads of daily_prices_adjusted_current: " + "; ".join(
        offenders
    )
