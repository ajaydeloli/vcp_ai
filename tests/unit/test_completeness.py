"""Daily-bar completeness (audit P0-4): observed calendar, interior holes, worker repair."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from vcp_scanner.config.models import CompletenessConfig
from vcp_scanner.data.ingestion.worker import IngestionWorker
from vcp_scanner.data.providers.fake import FakeMarketDataProvider, make_candle
from vcp_scanner.data.quality.completeness import CompletenessChecker, CompletenessStatus
from vcp_scanner.data.repositories.duckdb_market_repository import DuckDBMarketDataRepository
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.enums import DataQualityFlag
from vcp_scanner.domain.market import Instrument

START, END = date(2024, 1, 1), date(2024, 1, 19)
HOLIDAY = date(2024, 1, 10)  # a Wednesday nobody traded
SPECIAL_SATURDAY = date(2024, 1, 13)  # everybody traded
IDS = [f"A{i}" for i in range(6)]


def _sessions() -> list[date]:
    days = []
    d = START
    while d <= END:
        if d.weekday() < 5 and d != HOLIDAY:
            days.append(d)
        d += timedelta(days=1)
    return sorted([*days, SPECIAL_SATURDAY])


@pytest.fixture()
def env():
    store = DuckDBStore(":memory:")
    store.migrate()
    repo = DuckDBMarketDataRepository(store)
    yield store, repo
    store.close()


def _seed(repo, iid, dates):
    repo.save_daily([make_candle(iid, d) for d in dates])


def _seed_market(repo, *, skip: dict[str, set[date]] | None = None, ids=IDS):
    skip = skip or {}
    for iid in ids:
        _seed(repo, iid, [d for d in _sessions() if d not in skip.get(iid, set())])


def test_complete_instrument_ignores_holiday_and_counts_special_session(env) -> None:
    store, repo = env
    _seed_market(repo)
    report = CompletenessChecker(store).check("A0", START, END)
    assert report.status is CompletenessStatus.COMPLETE
    assert report.expected_sessions == len(_sessions())
    assert SPECIAL_SATURDAY in _sessions()
    assert HOLIDAY not in _sessions()


def test_interior_hole_is_reported(env) -> None:
    store, repo = env
    hole = date(2024, 1, 16)
    _seed_market(repo, skip={"A0": {hole}})
    report = CompletenessChecker(store).check("A0", START, END)
    assert report.status is CompletenessStatus.INCOMPLETE
    assert report.missing_sessions == (hole,)
    assert report.missing_ranges == ((hole, hole),)
    # the other instruments are unaffected
    assert CompletenessChecker(store).check("A1", START, END).status is CompletenessStatus.COMPLETE


def test_stale_tail_is_reported_as_one_merged_range(env) -> None:
    store, repo = env
    last = date(2024, 1, 12)
    _seed_market(repo, skip={"A1": {d for d in _sessions() if d > last}})
    report = CompletenessChecker(store).check("A1", START, END)
    assert report.status is CompletenessStatus.INCOMPLETE
    assert report.missing_ranges == ((SPECIAL_SATURDAY, END),)


def test_thin_cross_section_is_unverified_never_complete(env) -> None:
    store, repo = env
    _seed_market(repo, ids=["A0", "A1"])
    report = CompletenessChecker(store).check("A0", START, END)
    assert report.status is CompletenessStatus.UNVERIFIED


def test_instrument_without_bars_is_unverified(env) -> None:
    store, repo = env
    _seed_market(repo)
    assert CompletenessChecker(store).check("NOPE", START, END).status is (
        CompletenessStatus.UNVERIFIED
    )


def test_low_breadth_date_is_suspect_not_a_session(env) -> None:
    store, repo = env
    partial_day = date(2024, 1, 18)
    _seed_market(repo, skip={iid: {partial_day} for iid in IDS[1:]})  # only A0 has it
    checker = CompletenessChecker(store)
    scan = checker.scan(START, END)
    assert partial_day not in scan.sessions
    assert scan.suspect_dates == ((partial_day, 1, 6),)
    report = checker.check("A1", START, END, scan=scan)
    assert report.status is CompletenessStatus.COMPLETE
    assert report.suspect_dates == (partial_day,)


def test_thresholds_come_from_config(env) -> None:
    store, repo = env
    _seed_market(repo, skip={iid: {date(2024, 1, 18)} for iid in IDS[:2]})  # 4 of 6 present
    strict = CompletenessChecker(store, CompletenessConfig(min_breadth=0.9)).scan(START, END)
    loose = CompletenessChecker(store, CompletenessConfig(min_breadth=0.5)).scan(START, END)
    assert date(2024, 1, 18) not in strict.sessions
    assert date(2024, 1, 18) in loose.sessions


def test_incomplete_report_becomes_missing_candles_event(env) -> None:
    store, repo = env
    _seed_market(repo, skip={"A0": {date(2024, 1, 16)}})
    report = CompletenessChecker(store).check("A0", START, END)
    (event,) = report.to_events(datetime(2024, 1, 22, tzinfo=UTC))
    assert event.flag is DataQualityFlag.MISSING_CANDLES
    assert event.instrument_id == "A0"
    assert event.context and event.context["missing_sessions"] == 1


# ---------------------------------------------------------------------------
# IngestionWorker integration
# ---------------------------------------------------------------------------

A0 = Instrument(instrument_id="A0", symbol="A0", exchange="NSE")
RUN_TIME = datetime(2024, 1, 22, tzinfo=UTC)
HOLE = date(2024, 1, 16)


def _worker(store, repo, provider_dates):
    provider = FakeMarketDataProvider(
        candles_by_id={"A0": [make_candle("A0", d) for d in provider_dates]}
    )
    return provider, IngestionWorker(
        provider=provider, repository=repo, completeness=CompletenessChecker(store)
    )


def test_worker_refetches_interior_hole_that_head_tail_logic_cannot_see(env) -> None:
    store, repo = env
    _seed_market(repo, skip={"A0": {HOLE}})
    provider, worker = _worker(store, repo, _sessions())

    run = worker.ingest_instrument(A0, START, END, ingestion_time=RUN_TIME)

    assert run.status == "SUCCESS"
    assert worker.completeness_reports["A0"].status is CompletenessStatus.COMPLETE
    assert HOLE in {c.timestamp.date() for c in repo.load_daily("A0", START, END)}
    fetches = [c for c in provider.call_log if c["method"] == "get_historical_daily"]
    assert [(c["start"], c["end"]) for c in fetches] == [(HOLE, HOLE)]
    gapfill = store.conn.execute(
        "SELECT status, records_written FROM ingestion_runs WHERE dataset = 'daily_ohlcv_gapfill'"
    ).fetchall()
    assert gapfill == [("SUCCESS", 1)]
    assert worker.quality_events == []


def test_worker_marks_run_partial_when_gap_cannot_be_filled(env) -> None:
    store, repo = env
    _seed_market(repo, skip={"A0": {HOLE}})
    _, worker = _worker(store, repo, [d for d in _sessions() if d != HOLE])  # provider lacks it

    run = worker.ingest_instrument(A0, START, END, ingestion_time=RUN_TIME)

    assert run.status == "PARTIAL"
    assert worker.completeness_reports["A0"].missing_sessions == (HOLE,)
    (event,) = worker.quality_events
    assert event.flag is DataQualityFlag.MISSING_CANDLES
    stored = store.conn.execute(
        "SELECT status FROM ingestion_runs WHERE ingestion_run_id = ?", [run.ingestion_run_id]
    ).fetchone()
    assert stored == ("PARTIAL",)


def test_worker_without_checker_keeps_previous_behaviour(env) -> None:
    store, repo = env
    _seed_market(repo, skip={"A0": {HOLE}})
    provider = FakeMarketDataProvider(
        candles_by_id={"A0": [make_candle("A0", d) for d in _sessions()]}
    )
    worker = IngestionWorker(provider=provider, repository=repo)
    run = worker.ingest_instrument(A0, START, END, ingestion_time=RUN_TIME)
    assert run.status == "SUCCESS"
    assert provider.call_log == []
