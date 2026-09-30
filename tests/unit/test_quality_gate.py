"""Audit finding P0-2, part 3: signal producers consult the gate.

Trend Template, RS ranking and the universe builder must not emit normal-looking results for
an instrument with an unresolved signal-blocking data-quality event, and must answer
point-in-time for frozen snapshots. Synthetic data only (AGENTS.md rule 10).
"""

from __future__ import annotations

import inspect
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from vcp_scanner import cli
from vcp_scanner.cli import main as cli_main
from vcp_scanner.config.models import UniverseConfig
from vcp_scanner.data.providers.fake import make_candle
from vcp_scanner.data.repositories.duckdb_market_repository import DuckDBMarketDataRepository
from vcp_scanner.data.repositories.duckdb_quality_repository import DuckDBDataQualityRepository
from vcp_scanner.data.repositories.duckdb_trend_repository import DuckDBTrendRepository
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.data.universe.builder import UniverseBuilder
from vcp_scanner.domain.enums import DataQualityFlag, TrendTemplateStatus
from vcp_scanner.domain.events import DataQualityEvent, EventSeverity, make_event_id
from vcp_scanner.domain.trend import TREND_CONDITION_NAMES
from vcp_scanner.features.relative_strength import RelativeStrengthEngine
from vcp_scanner.features.trend_template import TrendTemplateEngine

GAP = DataQualityFlag.UNEXPLAINED_GAP
T0 = datetime(2024, 3, 1, 12, 0, tzinfo=UTC)
AS_OF = date(2024, 3, 29)
EARLY = date(2022, 6, 1)  # before the 2023-01-01 universe as-of date


def _event(iid: str, *, detected_at: datetime = T0, trade_date: date | None = date(2024, 1, 2)):
    return DataQualityEvent(
        event_id=make_event_id(GAP, iid, "k"),
        instrument_id=iid,
        flag=GAP,
        severity=EventSeverity.HIGH,
        detected_at=detected_at,
        description="suspected missed split",
        trade_date=trade_date,
        blocks_signal=True,
    )


@pytest.fixture()
def store():
    s = DuckDBStore(":memory:")
    s.migrate()
    yield s
    s.close()


# ---------------------------------------------------------------------------
# Trend Template engine
# ---------------------------------------------------------------------------


class _Gate:
    def __init__(self, blocked: dict[str, tuple[str, ...]]) -> None:
        self.blocked = blocked
        self.calls: list[tuple[list[str], date]] = []

    def blocked_instruments(self, instrument_ids, as_of_date, *, known_at=None):  # noqa: ANN001
        self.calls.append((list(instrument_ids), as_of_date))
        return {i: f for i, f in self.blocked.items() if i in instrument_ids}


class _ExplodingRepo:
    """Any data access fails: proves the gate is consulted before data is read."""

    def __getattr__(self, name: str):
        raise AssertionError(f"data was read ({name}) although the instrument is blocked")


class _EmptyFeatures(_ExplodingRepo):
    def load_adjusted_closes(self, instrument_id, as_of, limit):  # noqa: ANN001, ANN201
        return []


def test_blocked_instrument_gets_its_own_status_before_any_data_is_read() -> None:
    gate = _Gate({"BAD": (GAP.value,)})
    engine = TrendTemplateEngine(_ExplodingRepo(), _ExplodingRepo(), quality_gate=gate)

    result = engine.evaluate("BAD", AS_OF)

    assert result.status is TrendTemplateStatus.DATA_QUALITY_BLOCKED
    assert result.blocked_by == (GAP.value,)
    assert result.passed is False
    assert result.trend_template_pass is None  # blocked is not a FAIL (missing is not zero)
    assert [c.name for c in result.conditions] == list(TREND_CONDITION_NAMES)  # all ten rows
    assert all(c.passed is None for c in result.conditions)


def test_evaluate_many_asks_the_gate_once_and_only_blocks_the_bad_ones() -> None:
    gate = _Gate({"BAD": (GAP.value,)})
    engine = TrendTemplateEngine(_EmptyFeatures(), _ExplodingRepo(), quality_gate=gate)

    results = engine.evaluate_many(["OK1", "BAD", "OK2"], AS_OF)

    assert [r.instrument_id for r in results] == ["OK1", "BAD", "OK2"]
    assert [r.status for r in results] == [
        TrendTemplateStatus.DATA_NOT_READY,  # no bars: unaffected by the gate
        TrendTemplateStatus.DATA_QUALITY_BLOCKED,
        TrendTemplateStatus.DATA_NOT_READY,
    ]
    assert len(gate.calls) == 1 and gate.calls[0] == (["OK1", "BAD", "OK2"], AS_OF)


def test_without_a_gate_the_engine_is_unchanged() -> None:
    engine = TrendTemplateEngine(_EmptyFeatures(), _ExplodingRepo())
    assert engine.evaluate("X", AS_OF).status is TrendTemplateStatus.DATA_NOT_READY


def test_blocked_result_is_persisted_with_its_reasons(store: DuckDBStore) -> None:
    gate = _Gate({"BAD": (GAP.value, DataQualityFlag.MISSING_CANDLES.value)})
    engine = TrendTemplateEngine(_ExplodingRepo(), _ExplodingRepo(), quality_gate=gate)
    results = engine.evaluate_many(["BAD"], AS_OF)

    DuckDBTrendRepository(store).save_trend_template_results("scan-1", "cfg", results)

    row = store.conn.execute(
        "SELECT status, trend_template_pass, blocked_by FROM trend_template_results"
    ).fetchone()
    assert row == ("DATA_QUALITY_BLOCKED", None, f"{GAP.value},MISSING_CANDLES")
    assert store.conn.execute("SELECT COUNT(*) FROM trend_template_conditions").fetchone() == (10,)


def test_blocked_by_column_is_added_to_an_existing_database(store: DuckDBStore) -> None:
    store.conn.execute("ALTER TABLE trend_template_results DROP COLUMN blocked_by")
    assert "blocked_by" not in store._column_nullability("trend_template_results")
    store.migrate()
    assert "blocked_by" in store._column_nullability("trend_template_results")
    store.migrate()  # idempotent


# ---------------------------------------------------------------------------
# RS ranking population
# ---------------------------------------------------------------------------


def _seed_rising(store: DuckDBStore, iid: str, slope: float) -> None:
    store.conn.execute(
        """
        INSERT INTO daily_prices_adjusted (
            instrument_id, trade_date, open_adj, high_adj, low_adj, close_adj, volume_adj,
            adjustment_version, price_factor_applied, volume_factor_applied, computed_at
        )
        SELECT ?, ?::DATE - INTERVAL (i) DAY, 100, 100, 100, 100 + (260 - i) * ?, 100,
               'v1', 1, 1, current_timestamp
        FROM range(0, 260) t(i)
        """,
        [iid, AS_OF, slope],
    )


def _seed_universe(store: DuckDBStore, members: list[str]) -> None:
    store.conn.execute(
        "INSERT INTO universe_snapshots VALUES ('uni', 'u', ?, ?, 'h', 'v1', 'COMPLETE')",
        [AS_OF, T0],
    )
    for m in members:
        store.conn.execute(
            "INSERT INTO universe_memberships VALUES "
            "('uni', ?, TRUE, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)",
            [m],
        )


def test_rs_leaves_blocked_instruments_out_of_the_population(store: DuckDBStore) -> None:
    for iid, slope in (("T1", 1.0), ("T2", 2.0), ("T3", 3.0)):
        _seed_rising(store, iid, slope)
    _seed_universe(store, ["T1", "T2", "T3"])
    quality = DuckDBDataQualityRepository(store)
    quality.sync_events("T3", GAP, [_event("T3")], at=T0)

    engine = RelativeStrengthEngine(store, quality_gate=quality)
    engine.compute_for_date(AS_OF, "uni")

    rows = dict(
        store.conn.execute(
            "SELECT instrument_id, population_size FROM relative_strength_snapshots"
        ).fetchall()
    )
    assert rows == {"T1": 2, "T2": 2}  # T3 (the strongest) is excluded, not ranked


def test_rs_without_a_gate_ranks_everyone(store: DuckDBStore) -> None:
    for iid, slope in (("T1", 1.0), ("T2", 2.0), ("T3", 3.0)):
        _seed_rising(store, iid, slope)
    _seed_universe(store, ["T1", "T2", "T3"])
    DuckDBDataQualityRepository(store).sync_events("T3", GAP, [_event("T3")], at=T0)

    RelativeStrengthEngine(store).compute_for_date(AS_OF, "uni")
    count = store.conn.execute("SELECT COUNT(*) FROM relative_strength_snapshots").fetchone()
    assert count == (3,)


# ---------------------------------------------------------------------------
# Universe builder
# ---------------------------------------------------------------------------


def _universe_store(store: DuckDBStore, ids: list[str], known_from: datetime) -> None:
    for iid in ids:
        store.conn.execute(
            """
            INSERT INTO daily_prices (
                instrument_id, trade_date, open_raw, high_raw, low_raw, close_raw,
                volume_raw, primary_provider, data_status, source_run_id, source_hash, known_from
            )
            SELECT CAST(? AS VARCHAR), CAST(? AS DATE) - CAST(i AS INTEGER),
                   100, 100, 100, 100, 100000, 'MOCK', 'OK', 'run', 'hash',
                   CAST(? AS TIMESTAMPTZ)
            FROM range(0, 253) t(i)
            """,
            [iid, date(2023, 1, 1), known_from],
        )
        store.conn.execute(
            "INSERT INTO security_master_history (instrument_id, series, exchange, valid_from,"
            " known_from) VALUES (?, 'EQ', 'NSE', '2000-01-01', ?)",
            [iid, known_from],
        )


def _universe_config() -> UniverseConfig:
    return UniverseConfig(
        exchange="NSE",
        min_close_price=10.0,
        min_daily_turnover_inr=5_000_000.0,
        min_avg_traded_value_50d_inr=5_000_000.0,
        eligible_series=["EQ"],
    )


def test_universe_excludes_blocked_instruments_with_a_reason(store: DuckDBStore) -> None:
    known = datetime.now(UTC) - timedelta(days=2)
    _universe_store(store, ["GOOD", "BAD"], known)
    quality = DuckDBDataQualityRepository(store)
    quality.sync_events("BAD", GAP, [_event("BAD", detected_at=known, trade_date=EARLY)], at=known)

    builder = UniverseBuilder(store, _universe_config(), quality_gate=quality)
    _, members = builder.build_snapshot(as_of_date=date(2023, 1, 1))

    by_id = {m.instrument_id: m for m in members}
    assert by_id["GOOD"].eligible is True
    assert by_id["BAD"].eligible is False
    assert by_id["BAD"].exclusion_reason == f"Data quality blocked: {GAP.value}"


def test_universe_gate_is_point_in_time(store: DuckDBStore) -> None:
    known = datetime.now(UTC) - timedelta(days=3)
    _universe_store(store, ["BAD"], known)
    quality = DuckDBDataQualityRepository(store)
    detected = known + timedelta(days=2)  # detected after the snapshot's known_at
    quality.sync_events(
        "BAD", GAP, [_event("BAD", detected_at=detected, trade_date=EARLY)], at=detected
    )

    builder = UniverseBuilder(store, _universe_config(), quality_gate=quality)
    _, before = builder.build_snapshot(
        as_of_date=date(2023, 1, 1), known_at=known + timedelta(days=1)
    )
    _, after = builder.build_snapshot(as_of_date=date(2023, 1, 1), known_at=detected)
    assert before[0].eligible is True  # an earlier snapshot could not have known
    assert after[0].eligible is False


def test_universe_without_a_gate_is_unchanged(store: DuckDBStore) -> None:
    known = datetime.now(UTC) - timedelta(days=2)
    _universe_store(store, ["BAD"], known)
    DuckDBDataQualityRepository(store).sync_events(
        "BAD", GAP, [_event("BAD", trade_date=EARLY)], at=known
    )
    _, members = UniverseBuilder(store, _universe_config()).build_snapshot(
        as_of_date=date(2023, 1, 1)
    )
    assert members[0].eligible is True


# ---------------------------------------------------------------------------
# CLI end to end
# ---------------------------------------------------------------------------

DAYS = [d for d in (date(2024, 1, 1) + timedelta(days=i) for i in range(60)) if d.weekday() < 5]
INGESTED = datetime(2024, 3, 1, 12, 0, tzinfo=UTC)
KNOWN_AT = "2024-03-02T00:00:00+00:00"
SNAP = "snap-20240302T000000Z"
CONFIG_DIR = str(Path(__file__).resolve().parents[2] / "config")
IID = "NSE_EQ|CLIQ"


@pytest.fixture()
def db(tmp_path: Path) -> str:
    """Raw bars with a 2:1 split-like collapse mid-series and no corporate action to explain it."""
    path = tmp_path / "q.duckdb"
    with DuckDBStore(path) as s:
        s.migrate()
        repo = DuckDBMarketDataRepository(s, clock=lambda: INGESTED)
        candles = []
        for i, d in enumerate(DAYS):
            p = 100.0 if i < 30 else 50.0
            candles.append(
                make_candle(IID, d, open_=p, high=p + 1, low=p - 1, close=p, volume=1000)
            )
        repo.save_daily(candles)
        s.conn.execute(
            "INSERT INTO universe_snapshots VALUES ('uni-1', 'u', ?, ?, 'h', 'v1', 'COMPLETE')",
            [DAYS[-1], INGESTED],
        )
        s.conn.execute(
            "INSERT INTO universe_memberships VALUES "
            "('uni-1', ?, TRUE, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)",
            [IID],
        )
    return str(path)


def _rows(db: str, sql: str, params: list | None = None) -> list[tuple]:
    with DuckDBStore(db) as s:
        return s.conn.execute(sql, params or []).fetchall()


def _trend(db: str, *extra: str) -> int:
    return cli_main(
        [
            "compute",
            "trend-template",
            "--db",
            db,
            "--as-of",
            DAYS[-1].isoformat(),
            "--config-dir",
            CONFIG_DIR,
            *extra,
        ]
    )


def test_cli_scan_list_block_resolve_cycle(db: str, capsys: pytest.CaptureFixture[str]) -> None:
    cli_main(["ingest", "adjusted-prices", "--db", db])
    assert cli_main(["quality", "scan", "--db", db, "--config-dir", CONFIG_DIR]) == 0
    assert "Blocking signals        : 1" in capsys.readouterr().out

    # The gap is listed as a blocking event.
    assert cli_main(["quality", "list", "--db", db]) == 0
    listing = capsys.readouterr().out
    assert "BLOCKS" in listing and GAP.value in listing and IID in listing

    # Trend Template refuses to produce a normal-looking result and records why.
    assert _trend(db) == 0
    assert "DATA_QUALITY_BLOCKED" in capsys.readouterr().out
    assert _rows(
        db, "SELECT status, trend_template_pass, blocked_by FROM trend_template_results"
    ) == [("DATA_QUALITY_BLOCKED", None, GAP.value)]

    # A human decides the collapse was genuine; the block lifts and the audit trail stays.
    (event_id,) = _rows(db, "SELECT event_id FROM data_quality_events")[0]
    assert (
        cli_main(
            ["quality", "resolve", event_id, "--by", "ajay", "--note", "real demerger", "--db", db]
        )
        == 0
    )
    assert _trend(db) == 0
    status = _rows(db, "SELECT status, blocked_by FROM trend_template_results")
    assert status and status[0][0] != "DATA_QUALITY_BLOCKED" and status[0][1] is None
    assert _rows(db, "SELECT resolved_by, resolution_note FROM data_quality_events") == [
        ("ajay", "real demerger")
    ]

    # Re-scanning must not reopen a human-resolved gap.
    cli_main(["quality", "scan", "--db", db, "--config-dir", CONFIG_DIR])
    assert _rows(db, "SELECT status FROM data_quality_events") == [("RESOLVED",)]
    assert cli_main(["quality", "list", "--db", db]) == 0
    assert "No open data-quality events." in capsys.readouterr().out


def test_cli_resolve_rejects_bad_requests(db: str, capsys: pytest.CaptureFixture[str]) -> None:
    cli_main(["quality", "scan", "--db", db, "--config-dir", CONFIG_DIR])
    (event_id,) = _rows(db, "SELECT event_id FROM data_quality_events")[0]
    capsys.readouterr()
    args = ["quality", "resolve", event_id, "--note", "x", "--db", db]
    assert cli_main([*args, "--by", "system"]) == 1
    assert "must name a person" in capsys.readouterr().err
    assert cli_main(["quality", "resolve", "dq-nope", "--by", "a", "--note", "x", "--db", db]) == 1
    assert "no OPEN event" in capsys.readouterr().err
    assert _rows(db, "SELECT status FROM data_quality_events") == [("OPEN",)]  # untouched


def test_frozen_snapshot_ignores_events_detected_after_it(
    db: str, capsys: pytest.CaptureFixture[str]
) -> None:
    cli_main(["ingest", "adjusted-prices", "--db", db])  # LIVE
    cli_main(["ingest", "adjusted-prices", "--db", db, "--known-at", KNOWN_AT])
    cli_main(["quality", "scan", "--db", db, "--config-dir", CONFIG_DIR])  # detected "now"
    capsys.readouterr()

    assert _trend(db, "--data-snapshot-id", SNAP) == 0
    assert _trend(db) == 0

    by_snapshot = dict(_rows(db, "SELECT data_snapshot_id, status FROM trend_template_results"))
    assert by_snapshot["LIVE"] == "DATA_QUALITY_BLOCKED"
    # The snapshot predates the detection, so it reproduces exactly as it would have then.
    assert by_snapshot[SNAP] != "DATA_QUALITY_BLOCKED"


def test_cli_entry_points_wire_the_gate() -> None:
    """Every production path that emits signals or a ranking population passes a gate."""
    from vcp_scanner import cli_pipeline

    assert "quality_gate=_quality_gate(" in inspect.getsource(
        cli_pipeline.run_compute_trend_template
    )
    assert "quality_gate=_quality_gate(" in inspect.getsource(cli_pipeline.run_compute_rs)
    assert "quality_gate=DuckDBDataQualityRepository(" in inspect.getsource(cli.main)
    assert "quality_repository=DuckDBDataQualityRepository(" in inspect.getsource(
        cli_pipeline.run_market_ingest
    )
    assert "quality_repository=DuckDBDataQualityRepository(" in inspect.getsource(
        cli_pipeline.run_corporate_actions
    )
