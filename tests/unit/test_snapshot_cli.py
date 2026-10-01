"""Stage 3 of audit finding P0-1: CLI commands read and write one explicit data snapshot.

``ingest adjusted-prices --known-at`` freezes a snapshot; ``compute features|rs|trend-template
--data-snapshot-id`` consume exactly that snapshot. LIVE (unfrozen) is the explicit default,
and frozen data is never silently fed to a LIVE run. All data is synthetic (AGENTS.md rule 10).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from vcp_scanner.cli import main as cli_main
from vcp_scanner.data.providers.fake import make_candle
from vcp_scanner.data.repositories.duckdb_market_repository import DuckDBMarketDataRepository
from vcp_scanner.data.storage.duckdb_store import DuckDBStore

IID = "NSE_EQ|CLICO"
DAYS = [d for d in (date(2024, 1, 1) + timedelta(days=i) for i in range(60)) if d.weekday() < 5]
INGESTED = datetime(2024, 3, 1, 12, 0, tzinfo=UTC)
KNOWN_AT = "2024-03-02T00:00:00+00:00"
SNAP = "snap-20240302T000000Z"
CONFIG_DIR = str(Path(__file__).resolve().parents[2] / "config")


@pytest.fixture()
def db(tmp_path: Path) -> str:
    """A database with raw bars ingested before KNOWN_AT."""
    path = tmp_path / "cli.duckdb"
    with DuckDBStore(path) as store:
        store.migrate()
        repo = DuckDBMarketDataRepository(store, clock=lambda: INGESTED)
        repo.save_daily(
            [
                make_candle(IID, d, open_=50.0, high=51.0, low=49.0, close=50.0, volume=1000)
                for d in DAYS
            ]
        )
    return str(path)


def _rows(db: str, sql: str) -> list[tuple]:
    with DuckDBStore(db) as store:
        return store.conn.execute(sql).fetchall()


def test_adjusted_prices_known_at_freezes_a_snapshot(
    db: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli_main(["ingest", "adjusted-prices", "--db", db, "--known-at", KNOWN_AT]) == 0
    out = capsys.readouterr().out
    assert f"Data snapshot     : {SNAP}" in out

    assert _rows(db, "SELECT data_snapshot_id FROM data_snapshots") == [(SNAP,)]
    assert _rows(db, "SELECT DISTINCT computed_from_snapshot_id FROM daily_prices_adjusted") == [
        (SNAP,)
    ]


def test_adjusted_prices_without_known_at_is_explicitly_live(
    db: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli_main(["ingest", "adjusted-prices", "--db", db]) == 0
    assert "LIVE (unfrozen" in capsys.readouterr().out
    assert _rows(db, "SELECT DISTINCT computed_from_snapshot_id FROM daily_prices_adjusted") == [
        ("LIVE",)
    ]
    assert _rows(db, "SELECT COUNT(*) FROM data_snapshots") == [(0,)]


def test_adjusted_prices_rejects_bad_known_at(db: str) -> None:
    assert cli_main(["ingest", "adjusted-prices", "--db", db, "--known-at", "yesterday"]) == 1


def test_adjusted_prices_known_at_before_ingest_builds_nothing(
    db: str, capsys: pytest.CaptureFixture[str]
) -> None:
    early = "2024-01-01T00:00:00+00:00"  # bars were not known yet
    assert cli_main(["ingest", "adjusted-prices", "--db", db, "--known-at", early]) == 0
    assert "Instruments built : 0" in capsys.readouterr().out
    assert _rows(db, "SELECT COUNT(*) FROM daily_prices_adjusted") == [(0,)]


def test_compute_features_uses_the_requested_snapshot(
    db: str, capsys: pytest.CaptureFixture[str]
) -> None:
    cli_main(["ingest", "adjusted-prices", "--db", db, "--known-at", KNOWN_AT])
    capsys.readouterr()

    assert cli_main(["compute", "features", "--db", db, "--data-snapshot-id", SNAP]) == 0
    assert f"Data snapshot      : {SNAP}" in capsys.readouterr().out
    assert _rows(db, "SELECT DISTINCT data_snapshot_id FROM technical_features_daily") == [(SNAP,)]
    assert _rows(db, "SELECT DISTINCT data_snapshot_id FROM weekly_prices") == [(SNAP,)]


def test_live_run_does_not_silently_consume_frozen_data(
    db: str, capsys: pytest.CaptureFixture[str]
) -> None:
    cli_main(["ingest", "adjusted-prices", "--db", db, "--known-at", KNOWN_AT])
    capsys.readouterr()

    assert cli_main(["compute", "features", "--db", db]) == 1  # default LIVE: nothing built
    assert "no adjusted prices found for data snapshot LIVE" in capsys.readouterr().err
    assert _rows(db, "SELECT COUNT(*) FROM technical_features_daily") == [(0,)]


@pytest.mark.parametrize(
    "argv",
    [
        ["compute", "features"],
        ["compute", "rs", "--as-of", "2024-03-01", "--config-dir", CONFIG_DIR],
        ["compute", "trend-template", "--as-of", "2024-03-01", "--config-dir", CONFIG_DIR],
    ],
)
def test_unknown_or_malformed_snapshot_is_rejected(
    db: str, argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli_main([*argv, "--db", db, "--data-snapshot-id", "snap-nope"]) == 1
    assert "unknown data snapshot 'snap-nope'" in capsys.readouterr().err
    assert cli_main([*argv, "--db", db, "--data-snapshot-id", "x'; DROP TABLE t;--"]) == 1
    assert "Invalid data snapshot id" in capsys.readouterr().err


def test_trend_template_tags_results_and_scan_id_with_the_snapshot(
    db: str, capsys: pytest.CaptureFixture[str]
) -> None:
    as_of = DAYS[-1]
    cli_main(["ingest", "adjusted-prices", "--db", db, "--known-at", KNOWN_AT])
    with DuckDBStore(db) as store:
        store.conn.execute(
            "INSERT INTO universe_snapshots VALUES "
            "('uni-1', 'u', ?, ?, 'h', 'v1', 'COMPLETE', NULL)",
            [as_of, INGESTED],
        )
        store.conn.execute(
            "INSERT INTO universe_memberships VALUES "
            "('uni-1', ?, TRUE, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)",
            [IID],
        )
    capsys.readouterr()

    argv = [
        "compute",
        "trend-template",
        "--db",
        db,
        "--as-of",
        as_of.isoformat(),
        "--config-dir",
        CONFIG_DIR,
        "--data-snapshot-id",
        SNAP,
    ]
    assert cli_main(argv) == 0
    assert f"Data snapshot: {SNAP}" in capsys.readouterr().out

    scans = _rows(db, "SELECT scan_id, data_snapshot_id FROM trend_template_results")
    assert len(scans) == 1
    scan_id, snapshot_id = scans[0]
    assert snapshot_id == SNAP and scan_id.endswith(f"-{SNAP}")
    assert _rows(db, "SELECT DISTINCT data_snapshot_id FROM weekly_context") == [(SNAP,)]
    assert _rows(db, "SELECT DISTINCT data_snapshot_id FROM trend_template_conditions") == [(SNAP,)]


def test_late_price_correction_never_changes_an_earlier_snapshots_features(
    db: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """End to end: raw -> adjusted -> features, before and after a provider correction."""
    latest_sma = (
        "SELECT sma_20 FROM technical_features_daily WHERE data_snapshot_id = '{snap}' "
        "ORDER BY trade_date DESC LIMIT 1"
    )
    snap_b = "snap-20240310T000000Z"

    cli_main(["ingest", "adjusted-prices", "--db", db, "--known-at", KNOWN_AT])
    cli_main(["compute", "features", "--db", db, "--data-snapshot-id", SNAP])
    before = _rows(db, latest_sma.format(snap=SNAP))
    assert before == [(pytest.approx(50.0),)]

    # The provider later corrects every bar (50 -> 80), learned after snapshot A.
    later = datetime(2024, 3, 5, 12, 0, tzinfo=UTC)
    with DuckDBStore(db) as store:
        DuckDBMarketDataRepository(store, clock=lambda: later).save_daily(
            [
                make_candle(IID, d, open_=80.0, high=81.0, low=79.0, close=80.0, volume=1000)
                for d in DAYS
            ]
        )

    # Rebuilding and recomputing snapshot A today reproduces it exactly...
    cli_main(["ingest", "adjusted-prices", "--db", db, "--known-at", KNOWN_AT])
    cli_main(["compute", "features", "--db", db, "--data-snapshot-id", SNAP])
    assert _rows(db, latest_sma.format(snap=SNAP)) == before

    # ...while a snapshot taken after the correction sees the new prices.
    cli_main(["ingest", "adjusted-prices", "--db", db, "--known-at", "2024-03-10T00:00:00+00:00"])
    cli_main(["compute", "features", "--db", db, "--data-snapshot-id", snap_b])
    assert _rows(db, latest_sma.format(snap=snap_b)) == [(pytest.approx(80.0),)]
    assert _rows(db, latest_sma.format(snap=SNAP)) == before
    capsys.readouterr()
