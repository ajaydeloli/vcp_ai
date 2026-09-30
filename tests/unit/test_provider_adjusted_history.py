"""Audit 2026-09-30 P0-1: Kite history is already adjusted as of its fetch time.

Kite rescales past bars for every split/bonus with an ex-date on or before the day they are
fetched. The adjustment engine must therefore apply only the actions after each bar's fetch
date, or pre-split history is adjusted twice. All data here is synthetic (AGENTS.md rule 10).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from vcp_scanner import cli_pipeline
from vcp_scanner.cli import main as cli_main
from vcp_scanner.config.models import UniverseConfig
from vcp_scanner.data.adjustment.builder import AdjustedPriceBuilder
from vcp_scanner.data.adjustment.engine import AdjustmentEngine
from vcp_scanner.data.providers.fake import make_candle
from vcp_scanner.data.providers.kite import KiteProvider
from vcp_scanner.data.quality.provider_adjustment import AdjustmentVerdict, classify_adjustment
from vcp_scanner.data.repositories.duckdb_corporate_action_repository import (
    DuckDBCorporateActionRepository,
)
from vcp_scanner.data.repositories.duckdb_market_repository import DuckDBMarketDataRepository
from vcp_scanner.data.repositories.duckdb_universe_repository import DuckDBUniverseRepository
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.data.universe.builder import UniverseBuilder
from vcp_scanner.domain.corporate_actions import CorporateActionAdjustment
from vcp_scanner.domain.market import PROVIDER_ADJUSTED_SOURCES, Candle

IID = "NSE_EQ|KADJ"
EX = date(2024, 1, 8)  # 2:1 split: pre-split raw 100 -> 50
DAYS = [d for d in (date(2024, 1, 1) + timedelta(days=i) for i in range(12)) if d.weekday() < 5]
BEFORE_SPLIT = datetime(2024, 1, 5, 13, 0, tzinfo=UTC)  # 18:30 IST on the 5th
AFTER_SPLIT = datetime(2024, 2, 1, 13, 0, tzinfo=UTC)
ON_EX_DATE = datetime(2024, 1, 8, 3, 0, tzinfo=UTC)  # 08:30 IST on the ex-date
EVE_OF_EX = datetime(2024, 1, 7, 15, 0, tzinfo=UTC)  # 20:30 IST the day before

SPLIT = CorporateActionAdjustment(
    resolution_id="R-SPLIT",
    instrument_id=IID,
    effective_date=EX,
    price_factor=0.5,
    volume_factor=2.0,
    cumulative_price_factor=0.5,
    cumulative_volume_factor=2.0,
    source="INTERNAL",
    calculation_version="1.1",
)


def _bar(d: date, close: float, provider: str, fetched: datetime | None) -> Candle:
    c = make_candle(IID, d, open_=close, high=close + 1, low=close - 1, close=close, volume=1000)
    return replace(c, provider=provider, ingested_at=fetched)


def _closes(rows: list[Any]) -> list[float]:
    return [round(r.close_adj, 6) for r in rows]


# ---------------------------------------------------------------------------- invariants


def test_kite_capability_matches_the_provider_adjusted_registry() -> None:
    caps = KiteProvider(api_key="unused").get_capabilities()
    assert caps.adjusted_prices is True
    assert (KiteProvider.PROVIDER_NAME in PROVIDER_ADJUSTED_SOURCES) is caps.adjusted_prices


# ---------------------------------------------------------------------------- engine


def test_kite_backfill_after_the_split_is_not_adjusted_twice() -> None:
    # Fetched after the split: Kite already shows pre-split bars at 50.
    bars = [_bar(d, 50.0, "KITE", AFTER_SPLIT) for d in DAYS]
    rows = AdjustmentEngine().build_adjusted_rows(bars, [SPLIT], computed_at=AFTER_SPLIT)
    assert _closes(rows) == [50.0] * len(DAYS)  # before the fix: 25.0 for pre-split bars
    assert all(float(r.price_factor_applied) == 1.0 for r in rows)


def test_kite_bars_fetched_before_the_split_are_adjusted_locally() -> None:
    # Old bars were stored while still unadjusted (raw 100); later bars fetched after the split.
    bars = [
        _bar(d, 100.0, "KITE", BEFORE_SPLIT) if d < EX else _bar(d, 50.0, "KITE", AFTER_SPLIT)
        for d in DAYS
    ]
    rows = AdjustmentEngine().build_adjusted_rows(bars, [SPLIT], computed_at=AFTER_SPLIT)
    assert _closes(rows) == [50.0] * len(DAYS)  # one continuous post-split scale


@pytest.mark.parametrize(
    ("fetched", "expected_pre_split"),
    [(ON_EX_DATE, 50.0), (EVE_OF_EX, 100.0 * 0.5)],
    ids=["fetched-on-ex-date-ist", "fetched-on-eve"],
)
def test_ex_date_boundary_uses_the_ist_fetch_date(
    fetched: datetime, expected_pre_split: float
) -> None:
    # On the ex-date (IST) Kite has rescaled: stored 50, no local factor. On the eve it has not:
    # stored 100, local factor 0.5. Either way the adjusted series is continuous at 50.
    stored = 50.0 if fetched == ON_EX_DATE else 100.0
    bar = _bar(date(2024, 1, 5), stored, "KITE", fetched)
    (row,) = AdjustmentEngine().build_adjusted_rows([bar], [SPLIT], computed_at=AFTER_SPLIT)
    assert row.close_adj == pytest.approx(expected_pre_split)


def test_raw_sources_are_always_adjusted_whatever_their_fetch_time() -> None:
    bars = [_bar(d, 100.0 if d < EX else 50.0, "SYNTHETIC_FAKE", AFTER_SPLIT) for d in DAYS]
    rows = AdjustmentEngine().build_adjusted_rows(bars, [SPLIT], computed_at=AFTER_SPLIT)
    assert _closes(rows) == [50.0] * len(DAYS)


def test_apply_factors_uses_the_same_fetch_time_rule() -> None:
    engine = AdjustmentEngine()
    bars = [_bar(d, 50.0, "KITE", AFTER_SPLIT) for d in DAYS]
    applied = engine.apply_factors(bars, [SPLIT])
    rows = engine.build_adjusted_rows(bars, [SPLIT], computed_at=AFTER_SPLIT)
    assert [c.close for c in applied] == _closes(rows)


# ---------------------------------------------------------------------------- repository + builder


@pytest.mark.parametrize("scenario", ["backfill-after-split", "incremental-across-split"])
def test_builder_produces_one_continuous_series(scenario: str) -> None:
    store = DuckDBStore(":memory:")
    store.migrate()
    ca = DuckDBCorporateActionRepository(store)
    ca.save_adjustment(SPLIT, known_from=BEFORE_SPLIT)

    def save(bars: list[Candle], at: datetime) -> None:
        DuckDBMarketDataRepository(store, clock=lambda: at).save_daily(
            [replace(b, ingested_at=None) for b in bars]  # repo stamps its clock as fetch time
        )

    if scenario == "backfill-after-split":
        save([_bar(d, 50.0, "KITE", None) for d in DAYS], AFTER_SPLIT)
    else:
        save([_bar(d, 100.0, "KITE", None) for d in DAYS if d < EX], BEFORE_SPLIT)
        save([_bar(d, 50.0, "KITE", None) for d in DAYS if d >= EX], AFTER_SPLIT)

    market = DuckDBMarketDataRepository(store)
    AdjustedPriceBuilder(market, ca).build_for_instrument(IID, computed_at=AFTER_SPLIT)
    rows = market.load_adjusted_daily(IID, DAYS[0], DAYS[-1])
    assert _closes(rows) == [50.0] * len(DAYS)


# ---------------------------------------------------------------------------- universe true raw


def _universe_db(provider: str) -> DuckDBStore:
    """253 bars ending 2023-12-29; a 10:1 split on 2024-06-03 known now; bars fetched after it.

    For KITE the stored closes are rescaled (raw 150 shown as 15); for a raw source they are
    real 15-rupee bars.
    """
    store = DuckDBStore(":memory:")
    store.migrate()
    fetched = datetime(2024, 7, 1, 12, 0, tzinfo=UTC)
    store.conn.execute(
        """
        INSERT INTO daily_prices (
            instrument_id, trade_date, open_raw, high_raw, low_raw, close_raw, volume_raw,
            primary_provider, data_status, source_run_id, source_hash, known_from
        )
        SELECT 'U1', DATE '2023-12-29' - CAST(i AS INTEGER), 15, 15, 15, 15, 10000000,
               ?, 'OK', 'run', 'h', ?
        FROM range(0, 300) t(i)
        """,
        [provider, fetched],
    )
    store.conn.execute(
        "INSERT INTO security_master_history (instrument_id, series, exchange, valid_from, "
        "known_from) VALUES ('U1', 'EQ', 'NSE', '2000-01-01', ?)",
        [fetched],
    )
    DuckDBCorporateActionRepository(store).save_adjustment(
        replace(
            SPLIT,
            instrument_id="U1",
            effective_date=date(2024, 6, 3),
            price_factor=0.1,
            volume_factor=10.0,
            cumulative_price_factor=0.1,
            cumulative_volume_factor=10.0,
        ),
        known_from=fetched,
    )
    return store


@pytest.mark.parametrize(
    ("provider", "eligible", "price"), [("KITE", True, 150.0), ("SYNTHETIC_FAKE", False, 15.0)]
)
def test_universe_min_price_uses_the_price_that_actually_traded(
    provider: str, eligible: bool, price: float
) -> None:
    store = _universe_db(provider)
    builder = UniverseBuilder(
        DuckDBUniverseRepository(store), UniverseConfig(), clock=lambda: datetime.now(UTC)
    )
    _, memberships = builder.build_snapshot(date(2023, 12, 29))
    (m,) = memberships
    assert m.price == pytest.approx(price)
    assert m.eligible is eligible


# ---------------------------------------------------------------------------- verification


def _pair(prev_close: float, ex_open: float) -> list[Candle]:
    return [
        _bar(date(2024, 1, 5), prev_close, "KITE", None),
        replace(_bar(EX, ex_open, "KITE", None), open=ex_open),
    ]


@pytest.mark.parametrize(
    ("prev", "ex_open", "factor", "verdict"),
    [
        (50.0, 51.0, 0.5, AdjustmentVerdict.ADJUSTED),
        (100.0, 51.0, 0.5, AdjustmentVerdict.RAW),
        (100.0, 71.0, 0.5, AdjustmentVerdict.INCONCLUSIVE),  # halfway: neither
        (100.0, 90.0, 0.95, AdjustmentVerdict.INCONCLUSIVE),  # factor too small to tell
        (100.0, 10.2, 0.1, AdjustmentVerdict.RAW),
    ],
)
def test_classify_adjustment(
    prev: float, ex_open: float, factor: float, verdict: AdjustmentVerdict
) -> None:
    assert classify_adjustment("KADJ", EX, factor, _pair(prev, ex_open)).verdict is verdict


def test_classify_without_bars_on_both_sides_is_inconclusive() -> None:
    only_after = [_bar(EX, 50.0, "KITE", None)]
    result = classify_adjustment("KADJ", EX, 0.5, only_after)
    assert result.verdict is AdjustmentVerdict.INCONCLUSIVE


class _FakeKite:
    def __init__(self, prev: float, ex_open: float) -> None:
        self.bars = _pair(prev, ex_open)

    def get_historical_daily(self, instrument, start, end):  # noqa: ANN001, ANN201
        return list(self.bars)


@pytest.mark.parametrize(("prev", "code"), [(50.0, 0), (100.0, 2)])
def test_verify_command_exit_codes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, prev: float, code: int
) -> None:
    monkeypatch.setenv("KITE_API_KEY", "k")
    monkeypatch.setenv("KITE_ACCESS_TOKEN", "t")
    monkeypatch.setattr(
        cli_pipeline, "_build_market_provider", lambda key, token: _FakeKite(prev, 51.0)
    )
    argv = [
        "verify", "kite-adjustment", "--action", "KADJ:2024-01-08:SPLIT:10:5",
        "--db", str(tmp_path / "v.duckdb"), "--env-file", str(tmp_path / "none.env"),
    ]  # fmt: skip
    assert cli_main(argv) == code


def test_verify_command_rejects_a_bad_action_spec(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("KITE_API_KEY", "k")
    monkeypatch.setenv("KITE_ACCESS_TOKEN", "t")
    argv = [
        "verify", "kite-adjustment", "--action", "KADJ:2024-01-08:DIVIDEND:1:1",
        "--db", str(tmp_path / "v.duckdb"), "--env-file", str(tmp_path / "none.env"),
    ]  # fmt: skip
    assert cli_main(argv) == 1
