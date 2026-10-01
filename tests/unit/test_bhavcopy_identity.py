"""Bhavcopy identity: symbol continuity, ISIN history, symbol reuse (audit step 2.2).

Real ISINs from the 2.0 spike: TATASTEEL INE081A01012 -> INE081A01020 the session after its
2022-07-28 split, IRCTC INE335Y01012 -> INE335Y01020 after 2021-10-28.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from vcp_scanner.data.identity import (
    mint_instrument_id,
    same_issuer_equity,
    symbol_from_instrument_id,
)
from vcp_scanner.data.ingestion.bhavcopy_identity import (
    BhavcopyIdentityResolver,
    IdentityOrderError,
)
from vcp_scanner.data.providers.nse_bhavcopy import parse_bhavcopy
from vcp_scanner.data.quality.events import identity_events
from vcp_scanner.data.repositories.duckdb_identity_repository import DuckDBIdentityRepository
from vcp_scanner.data.repositories.duckdb_instrument_repository import DuckDBInstrumentRepository
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.bhavcopy import BhavcopyRow
from vcp_scanner.domain.corporate_actions import CorporateActionResolution, CorporateActionStatus
from vcp_scanner.domain.enums import CorporateActionType, DataQualityFlag
from vcp_scanner.domain.events import EventSeverity
from vcp_scanner.domain.market import IdentifierPeriod, Instrument

AT = datetime(2026, 9, 30, 12, tzinfo=UTC)
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "bhavcopy"


def _row(d: date, symbol: str, isin: str, series: str = "EQ") -> BhavcopyRow:
    return BhavcopyRow(d, symbol, series, isin, 10, 11, 9, 10, 10, 10, 100, 1000.0, 5)


def _store(*seeds: Instrument) -> DuckDBStore:
    store = DuckDBStore(":memory:")
    store.migrate()
    if seeds:
        DuckDBInstrumentRepository(store, clock=lambda: AT).save_instruments(list(seeds))
    return store


def _run(store: DuckDBStore, d: date, rows: list[BhavcopyRow]):  # type: ignore[no-untyped-def]
    repo = DuckDBIdentityRepository(store)
    resolver = BhavcopyIdentityResolver(repo.load_state())
    day = resolver.resolve_day(d, rows)
    repo.apply(day, recorded_at=AT)
    return day


# --- helpers in data.identity ----------------------------------------------------------


def test_mint_disambiguates_and_symbol_round_trips() -> None:
    assert mint_instrument_id("NSE", "abc") == "NSE_EQ|ABC"
    iid = mint_instrument_id("NSE", "ABC", disambiguator="INE999B01012")
    assert iid == "NSE_EQ|ABC#INE999B01012"
    assert symbol_from_instrument_id(iid) == "ABC"


def test_same_issuer_equity() -> None:
    assert same_issuer_equity("INE081A01012", "INE081A01020")  # TATASTEEL split
    assert same_issuer_equity("INE335Y01012", "INE335Y01020")  # IRCTC split
    assert not same_issuer_equity("INE002A01018", "INE758E01017")  # RELIANCE vs JIOFIN
    assert not same_issuer_equity("INE081A01012", None)
    assert not same_issuer_equity("INE081A01012", "SHORT")


# --- resolution ------------------------------------------------------------------------


def test_seed_adopts_old_isin_then_records_split_isin_change() -> None:
    store = _store(Instrument("NSE_EQ|TATASTEEL", "TATASTEEL", isin="INE081A01020"))
    d1 = _run(store, date(2022, 7, 27), [_row(date(2022, 7, 27), "TATASTEEL", "INE081A01012")])
    assert d1.mapping == {("TATASTEEL", "EQ"): "NSE_EQ|TATASTEEL"}
    assert d1.new_instruments == []
    _run(store, date(2022, 7, 28), [_row(date(2022, 7, 28), "TATASTEEL", "INE081A01012")])
    d3 = _run(store, date(2022, 7, 29), [_row(date(2022, 7, 29), "TATASTEEL", "INE081A01020")])
    assert d3.mapping == {("TATASTEEL", "EQ"): "NSE_EQ|TATASTEEL"}

    periods = DuckDBIdentityRepository(store).load_periods("NSE_EQ|TATASTEEL")
    assert [(p.isin, p.valid_from, p.valid_to, p.change_reason) for p in periods] == [
        ("INE081A01012", date(2022, 7, 27), date(2022, 7, 29), "FIRST_SEEN"),
        ("INE081A01020", date(2022, 7, 29), None, "ISIN_CHANGE"),
    ]


def test_isin_change_is_linked_without_a_seed() -> None:
    store = _store()
    _run(store, date(2021, 10, 27), [_row(date(2021, 10, 27), "IRCTC", "INE335Y01012")])
    day = _run(store, date(2021, 10, 29), [_row(date(2021, 10, 29), "IRCTC", "INE335Y01020")])
    assert day.mapping[("IRCTC", "EQ")] == "NSE_EQ|IRCTC"
    assert day.new_instruments == []
    assert [p.change_reason for p in day.opened] == ["ISIN_CHANGE"]


def test_symbol_reused_by_another_company_gets_its_own_instrument() -> None:
    store = _store()
    _run(store, date(2021, 1, 4), [_row(date(2021, 1, 4), "ABC", "INE111A01011")])
    day = _run(store, date(2024, 3, 1), [_row(date(2024, 3, 1), "ABC", "INE999B01012")])
    assert day.mapping[("ABC", "EQ")] == "NSE_EQ|ABC#INE999B01012"
    old = DuckDBIdentityRepository(store).load_periods("NSE_EQ|ABC")
    assert [(p.isin, p.valid_to) for p in old] == [("INE111A01011", None)]


def test_seed_of_a_different_issuer_is_not_adopted_by_an_old_company() -> None:
    # EQUITY_L today: ABC belongs to the new company. The 2021 file shows the old ABC.
    store = _store(Instrument("NSE_EQ|ABC", "ABC", isin="INE999B01012"))
    old = _run(store, date(2021, 1, 4), [_row(date(2021, 1, 4), "ABC", "INE111A01011")])
    assert old.mapping[("ABC", "EQ")] == "NSE_EQ|ABC#INE111A01011"
    new = _run(store, date(2024, 3, 1), [_row(date(2024, 3, 1), "ABC", "INE999B01012")])
    assert new.mapping[("ABC", "EQ")] == "NSE_EQ|ABC"
    assert new.new_instruments == []


def test_delisted_name_becomes_an_inactive_instrument() -> None:
    store = _store()
    day = _run(store, date(2021, 1, 4), [_row(date(2021, 1, 4), "GONE", "INE123C01015", "BE")])
    assert day.mapping == {("GONE", "BE"): "NSE_EQ|GONE"}
    row = store.conn.execute(
        "SELECT isin, symbol, is_active FROM instruments WHERE instrument_id = 'NSE_EQ|GONE'"
    ).fetchone()
    assert row == ("INE123C01015", "GONE", False)
    series = store.conn.execute("SELECT series FROM daily_series").fetchall()
    assert series == [("BE",)]


def test_symbol_rename_keeps_the_instrument() -> None:
    store = _store()
    _run(store, date(2023, 1, 2), [_row(date(2023, 1, 2), "OLDNAME", "INE555D01010")])
    day = _run(store, date(2023, 6, 1), [_row(date(2023, 6, 1), "NEWNAME", "INE555D01010")])
    assert day.mapping[("NEWNAME", "EQ")] == "NSE_EQ|OLDNAME"
    assert [p.change_reason for p in day.opened] == ["SYMBOL_CHANGE"]


def test_differential_voting_shares_stay_separate() -> None:
    store = _store()
    d = date(2023, 1, 2)
    day = _run(
        store, d, [_row(d, "TATAMOTORS", "INE155A01022"), _row(d, "TATAMTRDVR", "INE155A01030")]
    )
    assert day.mapping[("TATAMOTORS", "EQ")] == "NSE_EQ|TATAMOTORS"
    assert day.mapping[("TATAMTRDVR", "EQ")] == "NSE_EQ|TATAMTRDVR"


# --- ordering and re-runs --------------------------------------------------------------


def test_days_must_advance_and_reruns_replay_the_stored_mapping() -> None:
    store = _store()
    d = date(2023, 1, 2)
    _run(store, d, [_row(d, "AAA", "INE777E01011")])
    repo = DuckDBIdentityRepository(store)
    resolver = BhavcopyIdentityResolver(repo.load_state())
    with pytest.raises(IdentityOrderError):
        resolver.resolve_day(d, [_row(d, "AAA", "INE777E01011")])

    replay = resolver.replay_day(d, [_row(d, "AAA", "INE777E01011")], repo.load_day_mapping(d))
    assert replay.replayed and replay.mapping == {("AAA", "EQ"): "NSE_EQ|AAA"}
    repo.apply(replay, recorded_at=AT)  # writes nothing
    assert store.conn.execute("SELECT count(*) FROM daily_series").fetchone() == (1,)

    with pytest.raises(IdentityOrderError, match="not in the first run"):
        resolver.replay_day(d, [_row(d, "NEW", "INE888F01012")], repo.load_day_mapping(d))


# --- real files ------------------------------------------------------------------------


def test_real_files_link_tatasteel_across_its_split() -> None:
    store = _store(
        Instrument("NSE_EQ|TATASTEEL", "TATASTEEL", isin="INE081A01020"),
        Instrument("NSE_EQ|TITAN", "TITAN", isin="INE280A01028"),
    )
    legacy = parse_bhavcopy(date(2022, 7, 28), (FIXTURES / "cm28JUL2022bhav.csv.zip").read_bytes())
    udiff = parse_bhavcopy(
        date(2026, 9, 29),
        (FIXTURES / "BhavCopy_NSE_CM_0_0_0_20260929_F_0000.csv.zip").read_bytes(),
    )
    first = _run(store, legacy.trade_date, legacy.rows)
    second = _run(store, udiff.trade_date, udiff.rows)
    assert first.mapping[("TATASTEEL", "EQ")] == second.mapping[("TATASTEEL", "EQ")]
    assert first.mapping[("TATASTEEL", "EQ")] == "NSE_EQ|TATASTEEL"
    periods = DuckDBIdentityRepository(store).load_periods("NSE_EQ|TATASTEEL")
    assert [p.isin for p in periods] == ["INE081A01012", "INE081A01020"]
    assert len(second.mapping) == len(udiff.rows)


# --- quality: unexplained ISIN changes -------------------------------------------------


def _periods() -> list[IdentifierPeriod]:
    return [
        IdentifierPeriod("I", "X", "INE081A01012", date(2022, 1, 3), date(2022, 7, 29)),
        IdentifierPeriod("I", "X", "INE081A01020", date(2022, 7, 29), None, "ISIN_CHANGE"),
    ]


def _split(ex: date) -> CorporateActionResolution:
    return CorporateActionResolution(
        "r", "I", CorporateActionType.SPLIT, CorporateActionStatus.CONFIRMED, ex_date=ex
    )


def test_isin_change_explained_by_a_nearby_split_raises_nothing() -> None:
    assert identity_events("I", _periods(), [_split(date(2022, 7, 28))], AT) == []


def test_unexplained_isin_change_is_a_non_blocking_warning() -> None:
    events = identity_events("I", _periods(), [_split(date(2021, 1, 1))], AT)
    assert len(events) == 1
    e = events[0]
    assert e.flag is DataQualityFlag.SYMBOL_MAPPING_UNCERTAIN
    assert e.severity is EventSeverity.WARNING and not e.blocks_signal
    assert e.trade_date == date(2022, 7, 29)


# --- step 2.6 rebuild: renamed + split stocks must keep one history ------------------


def test_renamed_and_split_stock_keeps_one_history_under_todays_id() -> None:
    # KPI Green: KPIGLOBAL (INE542W01017) -> renamed KPIGREEN (2022) -> split, INE542W01025.
    store = _store(Instrument("NSE_EQ|KPIGREEN", "KPIGREEN", isin="INE542W01025"))
    days = [date(2021, 1, 4), date(2022, 4, 27), date(2024, 7, 18)]
    first = _run(store, days[0], [_row(days[0], "KPIGLOBAL", "INE542W01017")])
    assert first.mapping[("KPIGLOBAL", "EQ")] == "NSE_EQ|KPIGREEN" and not first.new_instruments
    _run(store, days[1], [_row(days[1], "KPIGREEN", "INE542W01017")])
    _run(store, days[2], [_row(days[2], "KPIGREEN", "INE542W01025")])
    periods = DuckDBIdentityRepository(store).load_periods("NSE_EQ|KPIGREEN")
    assert [(p.symbol, p.isin, p.change_reason) for p in periods] == [
        ("KPIGLOBAL", "INE542W01017", "FIRST_SEEN"),
        ("KPIGREEN", "INE542W01017", "SYMBOL_CHANGE"),
        ("KPIGREEN", "INE542W01025", "ISIN_CHANGE"),
    ]
    # Corporate actions quoting the old ISIN resolve to the same instrument.
    from vcp_scanner.data.repositories.duckdb_instrument_repository import (
        DuckDBInstrumentResolver,
    )

    assert (
        DuckDBInstrumentResolver(store).resolve(isin="INE542W01017", symbol="KPIGLOBAL")
        == "NSE_EQ|KPIGREEN"
    )


def test_split_and_rename_on_the_same_day_continue_the_history() -> None:
    # SABTN (…036) -> SABTNL with a new ISIN (…044) on one day; no seed involved.
    store = _store()
    _run(store, date(2021, 1, 4), [_row(date(2021, 1, 4), "SABTN", "INE416A01036")])
    day = _run(store, date(2023, 5, 2), [_row(date(2023, 5, 2), "SABTNL", "INE416A01044")])
    assert day.mapping[("SABTNL", "EQ")] == "NSE_EQ|SABTN"
    assert [p.change_reason for p in day.opened] == ["ISIN_CHANGE+SYMBOL_CHANGE"]


def test_differential_voting_shares_are_not_taken_for_a_predecessor() -> None:
    # A later-issued line of the same issuer trading beside the ordinary shares stays apart,
    # and a seed is never adopted by a *later* ISIN or while its own symbol trades.
    store = _store(Instrument("NSE_EQ|TATAMOTORS", "TATAMOTORS", isin="INE155A01022"))
    d = date(2021, 1, 4)
    day = _run(store, d, [_row(d, "TATAMTRDVR", "INE155A01030")])
    assert day.mapping[("TATAMTRDVR", "EQ")] == "NSE_EQ|TATAMTRDVR"
    store2 = _store(Instrument("NSE_EQ|NEWCO", "NEWCO", isin="INE777Q01020"))
    both = _run(store2, d, [_row(d, "OLDCO", "INE777Q01012"), _row(d, "NEWCO", "INE777Q01020")])
    assert both.mapping[("OLDCO", "EQ")] == "NSE_EQ|OLDCO"
