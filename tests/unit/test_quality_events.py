"""Audit finding P0-2, part 1: data-quality events are persisted, idempotent and gate-able.

Covers the event store and lifecycle (open / auto-close / human-resolve), the point-in-time
gate query, the producers (completeness, gap safety net, corporate-action conflicts) and the
scanner that runs them. All data is synthetic (AGENTS.md rule 10).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from vcp_scanner.config.models import CompletenessConfig, UnexplainedGapConfig
from vcp_scanner.data.ingestion.worker import IngestionWorker
from vcp_scanner.data.providers.fake import FakeMarketDataProvider, make_candle
from vcp_scanner.data.quality.completeness import CompletenessChecker
from vcp_scanner.data.quality.events import corporate_action_events
from vcp_scanner.data.quality.scanner import QualityScanner
from vcp_scanner.data.reconciliation.gap_detector import GapDetector
from vcp_scanner.data.repositories.duckdb_corporate_action_repository import (
    DuckDBCorporateActionRepository,
)
from vcp_scanner.data.repositories.duckdb_market_repository import DuckDBMarketDataRepository
from vcp_scanner.data.repositories.duckdb_quality_repository import DuckDBDataQualityRepository
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.corporate_actions import (
    CorporateActionResolution,
    CorporateActionStatus,
)
from vcp_scanner.domain.enums import CorporateActionType, DataQualityFlag
from vcp_scanner.domain.events import (
    SYSTEM_RESOLVER,
    DataQualityEvent,
    EventSeverity,
    EventStatus,
    make_event_id,
)
from vcp_scanner.domain.market import Instrument

IID = "NSE_EQ|QUAL"
T0 = datetime(2024, 3, 1, 12, 0, tzinfo=UTC)
GAP_FLAG = DataQualityFlag.UNEXPLAINED_GAP
CA_FLAG = DataQualityFlag.CORPORATE_ACTION_UNRESOLVED


@pytest.fixture()
def store():
    s = DuckDBStore(":memory:")
    s.migrate()
    yield s
    s.close()


@pytest.fixture()
def quality(store: DuckDBStore) -> DuckDBDataQualityRepository:
    return DuckDBDataQualityRepository(store)


def _event(
    key: str = "2024-02-01",
    *,
    iid: str = IID,
    flag: DataQualityFlag = GAP_FLAG,
    blocks: bool = True,
    trade_date: date | None = date(2024, 2, 1),
    detected_at: datetime = T0,
    description: str = "gap",
) -> DataQualityEvent:
    return DataQualityEvent(
        event_id=make_event_id(flag, iid, key),
        instrument_id=iid,
        flag=flag,
        severity=EventSeverity.HIGH,
        detected_at=detected_at,
        description=description,
        context={"k": key, "n": 1},
        trade_date=trade_date,
        blocks_signal=blocks,
    )


# ---------------------------------------------------------------------------
# Identity and lifecycle
# ---------------------------------------------------------------------------


def test_event_id_is_deterministic_and_keyed() -> None:
    a = make_event_id(GAP_FLAG, IID, "2024-02-01")
    assert a == make_event_id(GAP_FLAG, IID, "2024-02-01")
    assert a != make_event_id(GAP_FLAG, IID, "2024-02-02")
    assert a != make_event_id(GAP_FLAG, "OTHER", "2024-02-01")
    assert a != make_event_id(CA_FLAG, IID, "2024-02-01")


def test_sync_opens_event_and_is_idempotent(quality: DuckDBDataQualityRepository) -> None:
    first = quality.sync_events(IID, GAP_FLAG, [_event()], at=T0)
    again = quality.sync_events(IID, GAP_FLAG, [_event()], at=T0 + timedelta(days=1))
    assert (first.opened, first.resolved) == (1, 0)
    assert (again.opened, again.resolved) == (0, 0)  # same condition: refreshed, not duplicated
    (stored,) = quality.load_events()
    assert stored.status is EventStatus.OPEN
    assert stored.detected_at == T0  # first detection time is kept
    assert stored.context == {"k": "2024-02-01", "n": 1}


def test_sync_refreshes_details_of_a_still_open_event(quality: DuckDBDataQualityRepository) -> None:
    quality.sync_events(IID, GAP_FLAG, [_event(description="old", blocks=False)], at=T0)
    quality.sync_events(IID, GAP_FLAG, [_event(description="new", blocks=True)], at=T0)
    (stored,) = quality.load_events()
    assert stored.description == "new" and stored.blocks_signal is True


def test_sync_closes_cleared_conditions_and_reopens_if_they_return(
    quality: DuckDBDataQualityRepository,
) -> None:
    quality.sync_events(IID, GAP_FLAG, [_event()], at=T0)
    cleared = quality.sync_events(IID, GAP_FLAG, [], at=T0 + timedelta(days=1))
    assert cleared.resolved == 1
    (stored,) = quality.load_events()
    assert stored.status is EventStatus.RESOLVED
    assert stored.resolved_by == SYSTEM_RESOLVER
    assert stored.resolved_at == T0 + timedelta(days=1)

    quality.sync_events(IID, GAP_FLAG, [_event()], at=T0 + timedelta(days=2))
    (again,) = quality.load_events()
    assert again.status is EventStatus.OPEN and again.resolved_by is None


def test_sync_only_touches_its_own_instrument_and_flag(
    quality: DuckDBDataQualityRepository,
) -> None:
    quality.sync_events(IID, GAP_FLAG, [_event()], at=T0)
    quality.sync_events(IID, CA_FLAG, [_event("x", flag=CA_FLAG)], at=T0)
    quality.sync_events("OTHER", GAP_FLAG, [_event(iid="OTHER")], at=T0)

    quality.sync_events(IID, GAP_FLAG, [], at=T0 + timedelta(days=1))

    open_ids = {(e.instrument_id, e.flag) for e in quality.load_events(open_only=True)}
    assert open_ids == {(IID, CA_FLAG), ("OTHER", GAP_FLAG)}


def test_sync_rejects_events_of_another_instrument_or_flag(
    quality: DuckDBDataQualityRepository,
) -> None:
    with pytest.raises(ValueError, match="does not belong"):
        quality.sync_events(IID, GAP_FLAG, [_event(iid="OTHER")], at=T0)
    with pytest.raises(ValueError, match="does not belong"):
        quality.sync_events(IID, CA_FLAG, [_event()], at=T0)


def test_human_resolution_is_final(quality: DuckDBDataQualityRepository) -> None:
    quality.sync_events(IID, GAP_FLAG, [_event()], at=T0)
    (event,) = quality.load_events()
    assert quality.resolve(
        event.event_id, resolved_by="ajay", note="earnings gap, checked", resolved_at=T0
    )

    # The detector still sees the gap: the human decision must survive.
    quality.sync_events(IID, GAP_FLAG, [_event(description="changed")], at=T0 + timedelta(days=1))
    (kept,) = quality.load_events()
    assert kept.status is EventStatus.RESOLVED
    assert kept.resolved_by == "ajay"
    assert kept.resolution_note == "earnings gap, checked"
    assert kept.description == "gap"  # not overwritten either


def test_resolve_validation_and_unknown_ids(quality: DuckDBDataQualityRepository) -> None:
    quality.sync_events(IID, GAP_FLAG, [_event()], at=T0)
    (event,) = quality.load_events()
    with pytest.raises(ValueError, match="person"):
        quality.resolve(event.event_id, resolved_by="system", note="x", resolved_at=T0)
    with pytest.raises(ValueError, match="person"):
        quality.resolve(event.event_id, resolved_by="  ", note="x", resolved_at=T0)
    with pytest.raises(ValueError, match="note"):
        quality.resolve(event.event_id, resolved_by="ajay", note=" ", resolved_at=T0)
    assert quality.resolve("dq-nope", resolved_by="ajay", note="x", resolved_at=T0) is False
    assert quality.load_events(open_only=True)  # nothing was closed by the failed attempts
    # A second resolution of an already-resolved event does nothing.
    assert quality.resolve(event.event_id, resolved_by="ajay", note="x", resolved_at=T0)
    assert not quality.resolve(event.event_id, resolved_by="ajay", note="y", resolved_at=T0)


def test_corporate_action_conflict_cannot_be_closed_by_hand(
    quality: DuckDBDataQualityRepository,
) -> None:
    """DATABASE_SCHEMA 17A: a conflict clears only when its resolution is superseded."""
    conflict = _event("SPLIT|2024-02-01", flag=CA_FLAG)
    quality.sync_events(IID, CA_FLAG, [conflict], at=T0)
    with pytest.raises(ValueError, match="cannot be closed by hand"):
        quality.resolve(conflict.event_id, resolved_by="ajay", note="looks fine", resolved_at=T0)
    assert quality.blocked_instruments([IID], date(2024, 3, 1)) == {IID: (CA_FLAG.value,)}

    # It does clear the proper way: the conflict disappears from what the detector sees.
    quality.sync_events(IID, CA_FLAG, [], at=T0 + timedelta(days=1))
    assert quality.blocked_instruments([IID], date(2024, 3, 1)) == {}


# ---------------------------------------------------------------------------
# The gate query
# ---------------------------------------------------------------------------


def test_gate_blocks_only_open_blocking_events_from_their_trade_date(
    quality: DuckDBDataQualityRepository,
) -> None:
    quality.sync_events(IID, GAP_FLAG, [_event(trade_date=date(2024, 2, 1))], at=T0)
    ids = [IID, "CLEAN"]
    assert quality.blocked_instruments(ids, date(2024, 1, 31)) == {}  # before the event
    assert quality.blocked_instruments(ids, date(2024, 2, 1)) == {IID: (GAP_FLAG.value,)}
    assert quality.blocked_instruments(ids, date(2025, 1, 1)) == {IID: (GAP_FLAG.value,)}
    assert quality.blocked_instruments([], date(2025, 1, 1)) == {}
    assert quality.blocked_instruments(["CLEAN"], date(2025, 1, 1)) == {}


def test_gate_ignores_warnings_and_resolved_events(
    quality: DuckDBDataQualityRepository,
) -> None:
    quality.sync_events(IID, GAP_FLAG, [_event("a", blocks=False)], at=T0)
    assert quality.blocked_instruments([IID], date(2025, 1, 1)) == {}
    quality.sync_events(IID, CA_FLAG, [_event("b", flag=CA_FLAG)], at=T0)
    assert quality.blocked_instruments([IID], date(2025, 1, 1)) == {IID: (CA_FLAG.value,)}
    quality.sync_events(IID, CA_FLAG, [], at=T0)
    assert quality.blocked_instruments([IID], date(2025, 1, 1)) == {}


def test_gate_null_trade_date_applies_to_every_date(
    quality: DuckDBDataQualityRepository,
) -> None:
    quality.sync_events(IID, GAP_FLAG, [_event(trade_date=None)], at=T0)
    assert IID in quality.blocked_instruments([IID], date(1990, 1, 1))


def test_gate_reports_all_flags_sorted(quality: DuckDBDataQualityRepository) -> None:
    quality.sync_events(IID, GAP_FLAG, [_event("a")], at=T0)
    quality.sync_events(IID, CA_FLAG, [_event("b", flag=CA_FLAG)], at=T0)
    flags = quality.blocked_instruments([IID], date(2025, 1, 1))[IID]
    assert flags == tuple(sorted([GAP_FLAG.value, CA_FLAG.value]))


def test_gate_is_point_in_time_with_known_at(quality: DuckDBDataQualityRepository) -> None:
    quality.sync_events(IID, GAP_FLAG, [_event(detected_at=T0)], at=T0)
    as_of = date(2024, 3, 1)
    # Detected at T0: an earlier snapshot could not have known about it.
    assert quality.blocked_instruments([IID], as_of, known_at=T0 - timedelta(days=1)) == {}
    assert IID in quality.blocked_instruments([IID], as_of, known_at=T0)

    # Resolved a week later: still blocking as known before the resolution, clear after it.
    (event,) = quality.load_events()
    quality.resolve(
        event.event_id, resolved_by="ajay", note="ok", resolved_at=T0 + timedelta(days=7)
    )
    assert IID in quality.blocked_instruments([IID], as_of, known_at=T0 + timedelta(days=3))
    assert quality.blocked_instruments([IID], as_of, known_at=T0 + timedelta(days=8)) == {}
    # Without a cutoff only currently-open events count.
    assert quality.blocked_instruments([IID], as_of) == {}


def test_constructor_known_at_is_the_default_cutoff(store: DuckDBStore) -> None:
    quality = DuckDBDataQualityRepository(store)
    quality.sync_events(IID, GAP_FLAG, [_event(detected_at=T0)], at=T0)
    early = DuckDBDataQualityRepository(store, known_at=T0 - timedelta(days=1))
    late = DuckDBDataQualityRepository(store, known_at=T0 + timedelta(days=1))
    assert early.blocked_instruments([IID], date(2024, 3, 1)) == {}
    assert IID in late.blocked_instruments([IID], date(2024, 3, 1))


# ---------------------------------------------------------------------------
# Gap detector: blocking rules (DATA_SPECIFICATION 18A)
# ---------------------------------------------------------------------------


def _bars(prices: list[tuple[float, float]], start: date = date(2024, 1, 1)):
    """(open, close) pairs on consecutive weekdays."""
    out, d = [], start
    for o, c in prices:
        while d.weekday() >= 5:
            d += timedelta(days=1)
        out.append(make_candle(IID, d, open_=o, high=max(o, c), low=min(o, c), close=c, volume=1))
        d += timedelta(days=1)
    return out


def test_split_like_gap_blocks_but_ordinary_gap_only_warns() -> None:
    detector = GapDetector(UnexplainedGapConfig(gap_pct=30))
    split_like = detector.detect(_bars([(100, 100), (50, 50)]), [], T0)  # 2:1
    genuine = detector.detect(_bars([(100, 100), (150, 150)]), [], T0)  # +50%: never a split
    assert [e.blocks_signal for e in split_like] == [True]
    assert [e.blocks_signal for e in genuine] == [False]
    assert split_like[0].trade_date == date(2024, 1, 2)
    assert (
        split_like[0].event_id == detector.detect(_bars([(100, 100), (50, 50)]), [], T0)[0].event_id
    )


def test_gap_explained_by_a_resolved_action_raises_nothing() -> None:
    resolution = CorporateActionResolution(
        resolution_id="R",
        instrument_id=IID,
        action_type=CorporateActionType.SPLIT,
        status=CorporateActionStatus.CONFIRMED,
        ex_date=date(2024, 1, 2),
        ratio_numerator=2.0,  # audit P0-3: only a split with a usable ratio explains a gap
        ratio_denominator=1.0,
    )
    detector = GapDetector(UnexplainedGapConfig(gap_pct=30))
    assert detector.detect(_bars([(100, 100), (50, 50)]), [resolution], T0) == []


# ---------------------------------------------------------------------------
# Corporate-action conflicts
# ---------------------------------------------------------------------------


def _resolution(status: CorporateActionStatus, rid: str = "R1") -> CorporateActionResolution:
    return CorporateActionResolution(
        resolution_id=rid,
        instrument_id=IID,
        action_type=CorporateActionType.SPLIT,
        status=status,
        ex_date=date(2024, 2, 15),
        ratio_numerator=10.0,
        ratio_denominator=1.0,
        conflict_fields="ratio",
        nse_action_id="N1",
    )


def test_only_provider_conflicts_become_events() -> None:
    resolutions = [
        _resolution(CorporateActionStatus.PROVIDER_CONFLICT),
        _resolution(CorporateActionStatus.CONFIRMED, rid="R2"),
        _resolution(CorporateActionStatus.SINGLE_SOURCE, rid="R3"),
    ]
    (event,) = corporate_action_events(IID, resolutions, T0)
    assert event.flag is CA_FLAG
    assert event.severity is EventSeverity.CRITICAL
    assert event.blocks_signal is True
    assert event.trade_date == date(2024, 2, 15)  # bars before the ex-date are unaffected
    assert event.dataset == "corporate_actions"
    assert "ratio" in event.description


def test_conflict_blocking_follows_the_config_switch() -> None:
    (event,) = corporate_action_events(
        IID,
        [_resolution(CorporateActionStatus.PROVIDER_CONFLICT)],
        T0,
        conflict_blocks_signals=False,
    )
    assert event.blocks_signal is False


# ---------------------------------------------------------------------------
# Audit P0-3: only an applied split/bonus explains a gap; unknown ratios block
# ---------------------------------------------------------------------------


def _gap_resolution(
    action_type: CorporateActionType = CorporateActionType.SPLIT,
    status: CorporateActionStatus = CorporateActionStatus.CONFIRMED,
    num: float | None = 2.0,
    den: float | None = 1.0,
) -> CorporateActionResolution:
    return CorporateActionResolution(
        resolution_id="G",
        instrument_id=IID,
        action_type=action_type,
        status=status,
        ex_date=date(2024, 1, 2),
        ratio_numerator=num,
        ratio_denominator=den,
    )


@pytest.mark.parametrize(
    "resolution",
    [
        _gap_resolution(num=None, den=None),  # ratio could not be read -> factor 1.0
        _gap_resolution(num=0.0, den=1.0),
        _gap_resolution(num=float("nan"), den=1.0),
        _gap_resolution(status=CorporateActionStatus.PROVIDER_CONFLICT),  # factor withheld
        _gap_resolution(action_type=CorporateActionType.DIVIDEND, num=None, den=None),
        _gap_resolution(
            action_type=CorporateActionType.RIGHTS,
            status=CorporateActionStatus.PROVIDER_CONFLICT,
        ),
    ],
    ids=["no-ratio", "zero-ratio", "nan-ratio", "conflict", "dividend", "rights-conflict"],
)
def test_gap_is_not_explained_by_an_action_that_does_not_adjust_prices(
    resolution: CorporateActionResolution,
) -> None:
    detector = GapDetector(UnexplainedGapConfig(gap_pct=30))
    (event,) = detector.detect(_bars([(100, 100), (50, 50)]), [resolution], T0)
    assert event.flag is GAP_FLAG
    assert event.blocks_signal is True  # 2:1 drop is split-like


@pytest.mark.parametrize(
    "status",
    [
        CorporateActionStatus.CONFIRMED,
        CorporateActionStatus.SINGLE_SOURCE,
        CorporateActionStatus.MANUAL_OVERRIDE,
    ],
)
def test_gap_is_explained_by_an_applied_split_or_bonus(status: CorporateActionStatus) -> None:
    detector = GapDetector(UnexplainedGapConfig(gap_pct=30))
    # Ratios that halve the price: split 2:1 (face value 2 -> 1) and bonus 1:1. Since audit
    # 2.7c the action must also fit the jump's size; a bonus 2:1 (price x 1/3) would not.
    for action_type, num in ((CorporateActionType.SPLIT, 2.0), (CorporateActionType.BONUS, 1.0)):
        res = _gap_resolution(action_type=action_type, status=status, num=num, den=1.0)
        assert detector.detect(_bars([(100, 100), (50, 50)]), [res], T0) == []
    misfit = _gap_resolution(action_type=CorporateActionType.BONUS, status=status, num=2.0)
    assert len(detector.detect(_bars([(100, 100), (50, 50)]), [misfit], T0)) == 1


@pytest.mark.parametrize(
    "status",
    [
        CorporateActionStatus.CONFIRMED,
        CorporateActionStatus.SINGLE_SOURCE,
        CorporateActionStatus.MANUAL_OVERRIDE,
    ],
)
@pytest.mark.parametrize("action_type", [CorporateActionType.SPLIT, CorporateActionType.BONUS])
def test_applied_split_or_bonus_without_ratio_blocks_even_when_conflicts_only_warn(
    status: CorporateActionStatus, action_type: CorporateActionType
) -> None:
    res = _gap_resolution(action_type=action_type, status=status, num=None, den=None)
    (event,) = corporate_action_events(IID, [res], T0, conflict_blocks_signals=False)
    assert event.flag is CA_FLAG
    assert event.blocks_signal is True
    assert event.severity is EventSeverity.CRITICAL
    assert event.trade_date == date(2024, 1, 2)
    assert event.context is not None and event.context["cause"] == "ratio_unknown"
    # distinct from the conflict event for the same action, so both can coexist in history
    conflict = replace(res, status=CorporateActionStatus.PROVIDER_CONFLICT)
    (conflict_event,) = corporate_action_events(IID, [conflict], T0)
    assert conflict_event.event_id != event.event_id


def test_actions_with_a_usable_ratio_or_no_price_effect_raise_no_ratio_event() -> None:
    resolutions = [
        _gap_resolution(),  # applied split with ratio
        _gap_resolution(action_type=CorporateActionType.DIVIDEND, num=None, den=None),
        _gap_resolution(action_type=CorporateActionType.RIGHTS, num=None, den=None),
    ]
    assert corporate_action_events(IID, resolutions, T0) == []


def test_ratio_unknown_event_cannot_be_closed_by_hand(
    quality: DuckDBDataQualityRepository,
) -> None:
    res = _gap_resolution(num=None, den=None)
    events = corporate_action_events(IID, [res], T0)
    quality.sync_events(IID, CA_FLAG, events, at=T0)
    with pytest.raises(ValueError):
        quality.resolve(events[0].event_id, resolved_by="alice", note="looks fine", resolved_at=T0)
    assert IID in quality.blocked_instruments([IID], date(2024, 3, 1))


def test_scanner_blocks_unparsed_split_and_clears_when_ratio_arrives(scanner_env) -> None:
    market, ca, quality, scanner = scanner_env
    market.save_daily(_bars([(100, 100), (50, 50)]))
    ca.save_resolution(_gap_resolution(num=None, den=None), known_from=T0)

    summary = scanner.scan([IID], detected_at=T0)
    # the unparsed split no longer hides the gap, and it raises its own blocking event
    assert (summary.gap_events, summary.conflict_events, summary.blocking) == (1, 1, 2)
    assert set(quality.blocked_instruments([IID], date(2024, 3, 1))[IID]) == {
        GAP_FLAG.value,
        CA_FLAG.value,
    }

    ca.save_resolution(_gap_resolution(), known_from=T0 + timedelta(days=1))
    again = scanner.scan([IID], detected_at=T0 + timedelta(days=2))
    assert again.resolved == 2
    assert quality.blocked_instruments([IID], date(2024, 3, 1)) == {}


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


@pytest.fixture()
def scanner_env(store: DuckDBStore):
    market = DuckDBMarketDataRepository(store)
    ca = DuckDBCorporateActionRepository(store)
    quality = DuckDBDataQualityRepository(store)
    scanner = QualityScanner(market, ca, quality, GapDetector(UnexplainedGapConfig(gap_pct=30)))
    return market, ca, quality, scanner


def test_scanner_persists_gap_and_conflict_and_is_idempotent(scanner_env) -> None:
    market, ca, quality, scanner = scanner_env
    market.save_daily(_bars([(100, 100), (100, 100), (50, 50), (50, 50)]))
    ca.save_resolution(_resolution(CorporateActionStatus.PROVIDER_CONFLICT), known_from=T0)

    summary = scanner.scan([IID], detected_at=T0)
    assert (summary.gap_events, summary.conflict_events, summary.blocking) == (1, 1, 2)
    assert summary.opened == 2

    again = scanner.scan([IID], detected_at=T0 + timedelta(days=1))
    assert again.opened == 0 and again.resolved == 0
    assert len(quality.load_events()) == 2
    flags = quality.blocked_instruments([IID], date(2024, 3, 1))[IID]
    assert set(flags) == {GAP_FLAG.value, CA_FLAG.value}


def test_scanner_clears_events_when_the_cause_disappears(scanner_env) -> None:
    market, ca, quality, scanner = scanner_env
    market.save_daily(_bars([(100, 100), (100, 100), (50, 50), (50, 50)]))
    ca.save_resolution(_resolution(CorporateActionStatus.PROVIDER_CONFLICT), known_from=T0)
    scanner.scan([IID], detected_at=T0)

    # The conflict is confirmed (supersedes the row) and a confirmed split explains the gap.
    ca.save_resolution(
        _resolution(CorporateActionStatus.CONFIRMED), known_from=T0 + timedelta(days=1)
    )
    explained = CorporateActionResolution(
        resolution_id="R-split",
        instrument_id=IID,
        action_type=CorporateActionType.SPLIT,
        status=CorporateActionStatus.CONFIRMED,
        ex_date=date(2024, 1, 3),
        ratio_numerator=2.0,
        ratio_denominator=1.0,
    )
    ca.save_resolution(explained, known_from=T0 + timedelta(days=1))

    summary = scanner.scan([IID], detected_at=T0 + timedelta(days=2))
    assert summary.resolved == 2
    assert quality.blocked_instruments([IID], date(2024, 3, 1)) == {}


def test_scanner_isolates_instruments(scanner_env) -> None:
    market, _, quality, scanner = scanner_env
    market.save_daily(_bars([(100, 100), (50, 50)]))
    market.save_daily(
        [make_candle("CLEAN", date(2024, 1, 1)), make_candle("CLEAN", date(2024, 1, 2))]
    )
    scanner.scan([IID, "CLEAN"], detected_at=T0)
    assert set(quality.blocked_instruments([IID, "CLEAN"], date(2024, 3, 1))) == {IID}


# ---------------------------------------------------------------------------
# Completeness producer and worker persistence
# ---------------------------------------------------------------------------

START, END = date(2024, 1, 1), date(2024, 1, 19)
HOLE = date(2024, 1, 16)
A0 = Instrument(instrument_id="A0", symbol="A0", exchange="NSE")
IDS = [f"A{i}" for i in range(6)]


def _sessions() -> list[date]:
    days, d = [], START
    while d <= END:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def _worker(store, repo, quality, provider_dates):
    provider = FakeMarketDataProvider(
        candles_by_id={"A0": [make_candle("A0", d) for d in provider_dates]}
    )
    return IngestionWorker(
        provider=provider,
        repository=repo,
        completeness=CompletenessChecker(store, CompletenessConfig()),
        quality_repository=quality,
    )


def _seed_market(repo, *, hole: bool) -> None:
    for iid in IDS:
        dates = [d for d in _sessions() if not (hole and iid == "A0" and d == HOLE)]
        repo.save_daily([make_candle(iid, d) for d in dates])


def test_completeness_event_is_deterministic_blocking_and_dated(store: DuckDBStore) -> None:
    repo = DuckDBMarketDataRepository(store)
    _seed_market(repo, hole=True)
    report = CompletenessChecker(store).check("A0", START, END)
    (a,) = report.to_events(T0)
    (b,) = report.to_events(T0 + timedelta(days=1))
    assert a.event_id == b.event_id
    assert a.blocks_signal is True and a.trade_date == HOLE


def test_worker_persists_unfillable_hole_then_clears_it_when_filled(store: DuckDBStore) -> None:
    repo = DuckDBMarketDataRepository(store)
    quality = DuckDBDataQualityRepository(store)
    _seed_market(repo, hole=True)

    # Provider lacks the bar: the hole stays and a blocking event is persisted.
    worker = _worker(store, repo, quality, [d for d in _sessions() if d != HOLE])
    worker.ingest_instrument(A0, START, END, ingestion_time=T0)
    assert quality.blocked_instruments(["A0"], END) == {
        "A0": (DataQualityFlag.MISSING_CANDLES.value,)
    }
    assert quality.blocked_instruments(["A0"], HOLE - timedelta(days=1)) == {}

    # A later run finds the bar: the event closes itself, no human needed.
    worker = _worker(store, repo, quality, _sessions())
    worker.ingest_instrument(A0, START, END, ingestion_time=T0 + timedelta(days=1))
    assert quality.blocked_instruments(["A0"], END) == {}
    (event,) = quality.load_events()
    assert event.status is EventStatus.RESOLVED and event.resolved_by == SYSTEM_RESOLVER


def test_worker_without_repository_persists_nothing(store: DuckDBStore) -> None:
    repo = DuckDBMarketDataRepository(store)
    _seed_market(repo, hole=True)
    provider = FakeMarketDataProvider(
        candles_by_id={"A0": [make_candle("A0", d) for d in _sessions() if d != HOLE]}
    )
    worker = IngestionWorker(
        provider=provider, repository=repo, completeness=CompletenessChecker(store)
    )
    worker.ingest_instrument(A0, START, END, ingestion_time=T0)
    assert worker.quality_events  # still reported in memory, as before
    count = store.conn.execute("SELECT COUNT(*) FROM data_quality_events").fetchone()
    assert count == (0,)


@pytest.mark.parametrize("action_type", [CorporateActionType.RIGHTS, CorporateActionType.DEMERGER])
def test_rights_and_demerger_explain_their_ex_date_gap(action_type: CorporateActionType) -> None:
    """Audit step 2.4: their factor is derived from raw ex-date prices; when it cannot be, a
    separate blocking CORPORATE_ACTION_UNRESOLVED event (cause factor_unknown) is raised."""
    detector = GapDetector(UnexplainedGapConfig(gap_pct=30))
    resolution = _gap_resolution(action_type=action_type, num=None, den=None)
    assert detector.detect(_bars([(100, 100), (50, 50)]), [resolution], T0) == []
