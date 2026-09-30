"""Tests for the adjusted-price pipeline: raw prices -> daily_prices_adjusted.

Before this module existed nothing wrote ``daily_prices_adjusted``, so the
raw -> adjusted -> features chain only worked in tests that seeded the table by hand.
All data here is synthetic (AGENTS.md rule 10).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from vcp_scanner.cli import main as cli_main
from vcp_scanner.data.adjustment.builder import (
    STATUS_BUILT,
    STATUS_FAILED,
    STATUS_NO_PRICES,
    AdjustedPriceBuilder,
)
from vcp_scanner.data.adjustment.engine import AdjustmentEngine
from vcp_scanner.data.providers.fake import make_candle
from vcp_scanner.data.repositories.duckdb_corporate_action_repository import (
    DuckDBCorporateActionRepository,
)
from vcp_scanner.data.repositories.duckdb_market_repository import DuckDBMarketDataRepository
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.corporate_actions import CorporateActionAdjustment
from vcp_scanner.features.daily_features import DailyFeatureEngine
from vcp_scanner.features.weekly_aggregation import WeeklyAggregationEngine

IID = "NSE_EQ|TESTCO"
T1 = datetime(2024, 2, 1, 12, 0, tzinfo=UTC)
T2 = datetime(2024, 2, 2, 12, 0, tzinfo=UTC)

# Ten consecutive weekdays: Mon 2024-01-01 .. Fri 2024-01-12.
DAYS = [d for d in (date(2024, 1, 1) + timedelta(days=i) for i in range(12)) if d.weekday() < 5]
EX_DATE = date(2024, 1, 8)  # second Monday: first bar at the post-split price


def _candles(instrument_id: str = IID):
    """Pre-split bars trade at 100 / volume 1000; post-split at 20 / volume 5000 (5-for-1)."""
    out = []
    for d in DAYS:
        pre = d < EX_DATE
        close = 100.0 if pre else 20.0
        out.append(
            make_candle(
                instrument_id,
                d,
                open_=close,
                high=close + 1,
                low=close - 1,
                close=close,
                volume=1000 if pre else 5000,
            )
        )
    return out


def _split_adjustment(instrument_id: str = IID) -> CorporateActionAdjustment:
    return CorporateActionAdjustment(
        resolution_id="RES-1",
        instrument_id=instrument_id,
        effective_date=EX_DATE,
        price_factor=0.2,
        volume_factor=5.0,
        cumulative_price_factor=0.2,
        cumulative_volume_factor=5.0,
        source="INTERNAL",
        calculation_version="1.0",
    )


class Env:
    def __init__(self) -> None:
        self.store = DuckDBStore(":memory:")
        self.store.migrate()
        self.market = DuckDBMarketDataRepository(self.store)
        self.ca = DuckDBCorporateActionRepository(self.store)
        self.builder = AdjustedPriceBuilder(self.market, self.ca)

    def seed_prices(self, instrument_id: str = IID) -> None:
        self.market.save_daily(_candles(instrument_id))

    def seed_split(self, instrument_id: str = IID) -> None:
        self.ca.save_adjustment(_split_adjustment(instrument_id), known_from=T1)

    def count(self, table: str = "daily_prices_adjusted") -> int:
        row = self.store.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()  # noqa: S608
        assert row is not None
        return int(row[0])


@pytest.fixture()
def env() -> Env:
    return Env()


# ---------------------------------------------------------------------------
# Engine: factor lookup and row construction
# ---------------------------------------------------------------------------


def test_rows_agree_with_apply_factors() -> None:
    """The persisted rows must use exactly the same factor rule as ``apply_factors``."""
    engine = AdjustmentEngine()
    candles = _candles()
    adjustments = [_split_adjustment()]

    rows = engine.build_adjusted_rows(candles, adjustments, computed_at=T1)
    applied = engine.apply_factors(candles, adjustments)

    assert len(rows) == len(applied) == len(DAYS)
    for row, cand in zip(rows, applied, strict=True):
        assert row.trade_date == cand.timestamp.date()
        assert row.open_adj == pytest.approx(cand.open)
        assert row.high_adj == pytest.approx(cand.high)
        assert row.low_adj == pytest.approx(cand.low)
        assert row.close_adj == pytest.approx(cand.close)
        assert row.volume_adj == pytest.approx(cand.volume)


def test_ex_date_bar_is_not_adjusted_and_earlier_bars_are() -> None:
    rows = AdjustmentEngine().build_adjusted_rows(_candles(), [_split_adjustment()], computed_at=T1)
    by_date = {r.trade_date: r for r in rows}

    # Pre-split 100 -> 20 on the post-split scale; volume 1000 -> 5000.
    assert by_date[date(2024, 1, 5)].close_adj == pytest.approx(20.0)
    assert by_date[date(2024, 1, 5)].volume_adj == pytest.approx(5000.0)
    assert by_date[date(2024, 1, 5)].price_factor_applied == Decimal("0.2")
    # The ex-date bar and later bars already trade at the post-split price.
    assert by_date[EX_DATE].close_adj == pytest.approx(20.0)
    assert by_date[EX_DATE].price_factor_applied == Decimal("1.0")
    assert by_date[date(2024, 1, 12)].volume_adj == pytest.approx(5000.0)


def test_missing_volume_stays_null_never_zero() -> None:
    candle = make_candle(IID, date(2024, 1, 2), volume=None)
    rows = AdjustmentEngine().build_adjusted_rows([candle], [_split_adjustment()], computed_at=T1)
    assert rows[0].volume_adj is None


def test_adjustment_version_is_deterministic_and_tracks_factors() -> None:
    engine = AdjustmentEngine()
    split = _split_adjustment()

    assert engine.adjustment_version([]).endswith("-none")
    assert engine.adjustment_version([split]) == engine.adjustment_version([split])
    assert engine.adjustment_version([split]) != engine.adjustment_version([])

    other = CorporateActionAdjustment(
        resolution_id="RES-2",
        instrument_id=IID,
        effective_date=EX_DATE,
        price_factor=0.5,
        volume_factor=2.0,
        cumulative_price_factor=0.5,
        cumulative_volume_factor=2.0,
        source="INTERNAL",
        calculation_version="1.0",
    )
    assert engine.adjustment_version([other]) != engine.adjustment_version([split])


# ---------------------------------------------------------------------------
# Repository: persistence, idempotency, versions
# ---------------------------------------------------------------------------


def test_save_and_load_round_trip(env: Env) -> None:
    engine = AdjustmentEngine()
    rows = engine.build_adjusted_rows(_candles(), [_split_adjustment()], computed_at=T1)

    assert env.market.save_adjusted_daily(rows) == len(DAYS)
    loaded = env.market.load_adjusted_daily(IID, DAYS[0], DAYS[-1])

    assert [r.trade_date for r in loaded] == DAYS
    assert loaded[0].close_adj == pytest.approx(20.0)
    assert loaded[0].price_factor_applied == Decimal("0.2")
    assert loaded[0].adjustment_version == rows[0].adjustment_version


def test_resaving_same_version_is_idempotent(env: Env) -> None:
    rows = AdjustmentEngine().build_adjusted_rows(_candles(), [], computed_at=T1)
    env.market.save_adjusted_daily(rows)
    env.market.save_adjusted_daily(rows)
    assert env.count() == len(DAYS)


def test_versions_coexist_and_current_is_the_latest(env: Env) -> None:
    engine = AdjustmentEngine()
    v_old = engine.build_adjusted_rows(_candles(), [], computed_at=T1)
    v_new = engine.build_adjusted_rows(_candles(), [_split_adjustment()], computed_at=T2)
    env.market.save_adjusted_daily(v_old)
    env.market.save_adjusted_daily(v_new)

    # Old history is retained under its own version (nothing silently rewritten)...
    assert env.count() == 2 * len(DAYS)
    old_rows = env.market.load_adjusted_daily(
        IID, DAYS[0], DAYS[-1], adjustment_version=v_old[0].adjustment_version
    )
    assert old_rows[0].close_adj == pytest.approx(100.0)

    # ...but readers see exactly one version, the newest.
    assert env.market.current_adjustment_version(IID) == v_new[0].adjustment_version
    current = env.market.load_adjusted_daily(IID, DAYS[0], DAYS[-1])
    assert len(current) == len(DAYS)
    assert current[0].close_adj == pytest.approx(20.0)
    assert env.count("daily_prices_adjusted_current") == len(DAYS)


def test_current_view_keeps_all_rows_of_a_version_with_mixed_computed_at(env: Env) -> None:
    """Choosing the current version must never drop rows inside that version."""
    for i, d in enumerate(DAYS):
        env.store.conn.execute(
            """
            INSERT INTO daily_prices_adjusted (
                instrument_id, trade_date, open_adj, high_adj, low_adj, close_adj,
                volume_adj, adjustment_version, price_factor_applied,
                volume_factor_applied, computed_at
            ) VALUES (?, ?, 1, 1, 1, 1, 1, 'v1', 1, 1, ?)
            """,
            [IID, d, T1 + timedelta(seconds=i)],
        )
    assert env.count("daily_prices_adjusted_current") == len(DAYS)


def test_current_version_none_when_never_built(env: Env) -> None:
    assert env.market.current_adjustment_version(IID) is None


# ---------------------------------------------------------------------------
# Builder: raw + stored factors -> adjusted table
# ---------------------------------------------------------------------------


def test_builder_writes_adjusted_history_from_stored_factors(env: Env) -> None:
    env.seed_prices()
    env.seed_split()

    result = env.builder.build_for_instrument(IID, computed_at=T1)

    assert result.status == STATUS_BUILT
    assert result.rows_written == len(DAYS)
    assert result.adjustments_applied == 1
    assert result.adjustment_version == AdjustmentEngine().adjustment_version(
        env.ca.load_adjustments(IID)
    )
    rows = env.market.load_adjusted_daily(IID, DAYS[0], DAYS[-1])
    assert rows[0].close_adj == pytest.approx(20.0)
    assert rows[-1].close_adj == pytest.approx(20.0)


def test_builder_without_corporate_actions_still_populates_adjusted_table(env: Env) -> None:
    """Downstream engines read only the adjusted table, so no-action stocks need rows too."""
    env.seed_prices()

    result = env.builder.build_for_instrument(IID, computed_at=T1)

    assert result.status == STATUS_BUILT
    assert result.adjustments_applied == 0
    assert result.adjustment_version is not None
    assert result.adjustment_version.endswith("-none")
    rows = env.market.load_adjusted_daily(IID, DAYS[0], DAYS[-1])
    assert all(r.price_factor_applied == Decimal("1.0") for r in rows)
    assert rows[0].close_adj == pytest.approx(100.0)


def test_rebuild_with_unchanged_inputs_is_idempotent(env: Env) -> None:
    env.seed_prices()
    env.seed_split()

    first = env.builder.build_for_instrument(IID, computed_at=T1)
    second = env.builder.build_for_instrument(IID, computed_at=T2)

    assert first.adjustment_version == second.adjustment_version
    assert env.count() == len(DAYS)


def test_new_corporate_action_creates_a_new_version_and_keeps_the_old(env: Env) -> None:
    env.seed_prices()
    before = env.builder.build_for_instrument(IID, computed_at=T1)
    env.seed_split()
    after = env.builder.build_for_instrument(IID, computed_at=T2)

    assert before.adjustment_version != after.adjustment_version
    assert env.count() == 2 * len(DAYS)
    assert env.market.current_adjustment_version(IID) == after.adjustment_version
    assert env.market.load_adjusted_daily(IID, DAYS[0], DAYS[0])[0].close_adj == pytest.approx(20.0)


def test_builder_reports_no_prices_and_writes_nothing(env: Env) -> None:
    result = env.builder.build_for_instrument("NSE_EQ|GHOST", computed_at=T1)
    assert result.status == STATUS_NO_PRICES
    assert result.rows_written == 0
    assert env.count() == 0


def test_build_all_covers_every_priced_instrument_and_isolates_failures(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    env.seed_prices("NSE_EQ|AAA")
    env.seed_prices("NSE_EQ|BBB")

    results = env.builder.build_all(computed_at=T1)
    assert {r.instrument_id: r.status for r in results} == {
        "NSE_EQ|AAA": STATUS_BUILT,
        "NSE_EQ|BBB": STATUS_BUILT,
    }

    original = env.builder.build_for_instrument

    def flaky(instrument_id: str, *, computed_at: datetime, snapshot=None):
        if instrument_id == "NSE_EQ|AAA":
            raise RuntimeError("boom")
        return original(instrument_id, computed_at=computed_at, snapshot=snapshot)

    monkeypatch.setattr(env.builder, "build_for_instrument", flaky)
    results = env.builder.build_all(computed_at=T2)
    statuses = {r.instrument_id: r.status for r in results}
    assert statuses == {"NSE_EQ|AAA": STATUS_FAILED, "NSE_EQ|BBB": STATUS_BUILT}


# ---------------------------------------------------------------------------
# The chain: raw -> adjusted -> features, and multi-version safety downstream
# ---------------------------------------------------------------------------


def test_raw_to_adjusted_to_features_chain_works_without_hand_seeding(env: Env) -> None:
    env.seed_prices()
    env.seed_split()
    env.builder.build_for_instrument(IID, computed_at=T1)

    DailyFeatureEngine(env.store).compute_for_instrument(IID)

    row = env.store.conn.execute(
        "SELECT COUNT(*) FROM technical_features_daily WHERE instrument_id = ?", [IID]
    ).fetchone()
    assert row is not None
    assert row[0] == len(DAYS)


def test_features_do_not_fan_out_or_use_stale_version_after_a_new_split(env: Env) -> None:
    """A split introduced after the first build must not double rows or leak 100 -> 20 gaps."""
    env.seed_prices()
    env.builder.build_for_instrument(IID, computed_at=T1)  # v1: unadjusted, split shows as -80%
    env.seed_split()
    env.builder.build_for_instrument(IID, computed_at=T2)  # v2: split-adjusted

    DailyFeatureEngine(env.store).compute_for_instrument(IID)

    count_row = env.store.conn.execute(
        "SELECT COUNT(*) FROM technical_features_daily WHERE instrument_id = ?", [IID]
    ).fetchone()
    assert count_row is not None
    assert count_row[0] == len(DAYS)  # one row per day, not one per (day, version)

    ret_row = env.store.conn.execute(
        "SELECT daily_return FROM technical_features_daily "
        "WHERE instrument_id = ? AND trade_date = ?",
        [IID, EX_DATE],
    ).fetchone()
    assert ret_row is not None
    # Adjusted history is continuous across the ex-date. The stale version would give -0.8.
    assert ret_row[0] == pytest.approx(0.0)

    # Window functions must see one row per day. If both versions were visible, the
    # 5-row window would already be full on day 3 (two rows per day), so this average
    # would be non-NULL too early. It must be NULL until day 5, then equal the adjusted
    # volume of the five pre-split days (1000 x 5 = 5000 each).
    avg_by_day = dict(
        env.store.conn.execute(
            "SELECT trade_date, volume_avg_5 FROM technical_features_daily WHERE instrument_id = ?",
            [IID],
        ).fetchall()
    )
    assert avg_by_day[date(2024, 1, 3)] is None
    assert avg_by_day[date(2024, 1, 5)] == pytest.approx(5000.0)


def test_weekly_volume_uses_only_the_current_version(env: Env) -> None:
    env.seed_prices()
    env.builder.build_for_instrument(IID, computed_at=T1)
    env.seed_split()
    env.builder.build_for_instrument(IID, computed_at=T2)

    WeeklyAggregationEngine(env.store).compute_for_instrument(IID)

    row = env.store.conn.execute(
        "SELECT volume FROM weekly_prices WHERE instrument_id = ? AND week_end = ?",
        [IID, date(2024, 1, 5)],
    ).fetchone()
    assert row is not None
    # 5 pre-split days x 1000 shares x 5 (split factor) = 25,000 in one version.
    # Summing both versions would give 30,000.
    assert row[0] == pytest.approx(25_000.0)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_adjusted_prices_builds_from_a_database_file(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = tmp_path / "vcp.duckdb"
    with DuckDBStore(db_path) as store:
        store.migrate()
        DuckDBMarketDataRepository(store).save_daily(_candles())
        DuckDBCorporateActionRepository(store).save_adjustment(_split_adjustment(), known_from=T1)

    assert cli_main(["ingest", "adjusted-prices", "--db", str(db_path)]) == 0
    out = capsys.readouterr().out
    assert "Instruments built : 1" in out
    assert f"Rows written      : {len(DAYS)}" in out

    with DuckDBStore(db_path) as store:
        rows = DuckDBMarketDataRepository(store).load_adjusted_daily(IID, DAYS[0], DAYS[-1])
    assert len(rows) == len(DAYS)
    assert rows[0].close_adj == pytest.approx(20.0)


def test_cli_adjusted_prices_reports_instruments_without_raw_prices(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = tmp_path / "vcp.duckdb"
    assert (
        cli_main(["ingest", "adjusted-prices", "--db", str(db_path), "--instrument", "NSE_EQ|X"])
        == 0
    )
    out = capsys.readouterr().out
    assert "Instruments built : 0" in out
    assert "Skipped (no raw)  : 1" in out
