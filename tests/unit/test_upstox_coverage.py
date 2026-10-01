"""Upstox corporate actions: coverage window and split-ratio convention (audit Fix 5b follow-up).

Live check (2026-09-30): Upstox returns only about the last 12 months of events per ISIN
(TATASTEEL/RELIANCE/HDFCBANK/BAJFINANCE: one 2026 dividend each, none of their 2022-2025
splits/bonuses) and writes KOTAKBANK's face-value 5 -> 1 split as "1:5". Payload shapes below
mirror those responses; values are synthetic.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from unittest.mock import MagicMock

from vcp_scanner.data.adjustment.engine import AdjustmentEngine
from vcp_scanner.data.identity import deterministic_action_id
from vcp_scanner.data.ingestion.ca_worker import CorporateActionIngestionWorker
from vcp_scanner.data.providers.upstox_ca import UpstoxCorporateActionProvider
from vcp_scanner.data.reconciliation.engine import ReconciliationConfig, ReconciliationEngine
from vcp_scanner.data.repositories.duckdb_corporate_action_repository import (
    DuckDBCorporateActionRepository,
)
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.corporate_actions import CorporateAction, CorporateActionStatus
from vcp_scanner.domain.enums import CorporateActionType as T
from vcp_scanner.domain.market import Instrument

IID = "NSE_EQ|KOTAKBANK"
KOTAK = Instrument(IID, "KOTAKBANK", isin="INE237A01036")
T0 = datetime(2026, 9, 30, 9, 0, tzinfo=UTC)
WINDOW = (date(2021, 1, 1), date(2026, 9, 29))


def _upstox(payload: list[dict]) -> UpstoxCorporateActionProvider:
    provider = UpstoxCorporateActionProvider("tok")
    response = MagicMock(status_code=200)
    response.json.return_value = {"data": payload}
    provider._session = MagicMock()
    provider._session.get.return_value = response
    return provider


LIVE_SHAPE = [
    {"name": "Split", "expiry_date": "14 Jan 2026", "ratio": "1:5", "amount": None},
    {"name": "Dividend", "expiry_date": "17 Jul 2026", "ratio": None, "amount": 2.5},
]


# ---------------------------------------------------------------------------- provider


def test_split_ratio_is_converted_to_face_value_convention() -> None:
    (split,) = [
        a for a in _upstox(LIVE_SHAPE).get_actions(*WINDOW, [KOTAK]) if a.action_type is T.SPLIT
    ]
    # NSE / engine convention: (old face value, new face value) = (5, 1) -> price factor 0.2
    assert (split.ratio_numerator, split.ratio_denominator) == (5.0, 1.0)


def test_bonus_ratio_keeps_bonus_to_held_order() -> None:
    payload = [{"name": "Bonus", "expiry_date": "20 Feb 2026", "ratio": "4:1"}]
    (bonus,) = _upstox(payload).get_actions(*WINDOW, [KOTAK])
    assert (bonus.ratio_numerator, bonus.ratio_denominator) == (4.0, 1.0)


def test_coverage_starts_at_the_earliest_record_even_outside_the_window() -> None:
    provider = _upstox(LIVE_SHAPE)
    provider.get_actions(date(2026, 3, 1), date(2026, 9, 29), [KOTAK])  # window excludes Jan
    assert provider.coverage_start == {IID: date(2026, 1, 14)}
    assert provider.queried_instrument_ids == {IID}


def test_no_records_means_no_coverage() -> None:
    provider = _upstox([])
    provider.get_actions(*WINDOW, [KOTAK])
    assert provider.queried_instrument_ids == {IID}
    assert provider.coverage_start == {}


# ---------------------------------------------------------------------------- reconciliation


def _nse_split(ex: date, num: float = 5.0, den: float = 1.0) -> CorporateAction:
    return CorporateAction(
        corporate_action_id=deterministic_action_id("NSE", IID, "SPLIT", ex, num, den),
        instrument_id=IID, action_type=T.SPLIT, source="NSE", created_at=T0,
        ex_date=ex, ratio_numerator=num, ratio_denominator=den,
    )  # fmt: skip


class _Nse:
    def __init__(self, actions: list[CorporateAction]) -> None:
        self.actions = actions

    def get_actions(self, start, end, instruments=None):  # noqa: ANN001, ANN201
        return list(self.actions)


def _worker(nse: list[CorporateAction], upstox: UpstoxCorporateActionProvider):
    store = DuckDBStore(":memory:")
    store.migrate()
    repo = DuckDBCorporateActionRepository(store)
    worker = CorporateActionIngestionWorker(
        _Nse(nse), upstox, repo,
        ReconciliationEngine(ReconciliationConfig(secondary_grace_days=3)), AdjustmentEngine(),
    )  # fmt: skip
    return worker, repo


def _status(repo: DuckDBCorporateActionRepository, ex: date) -> CorporateActionStatus:
    (res,) = [r for r in repo.load_resolutions(IID) if r.ex_date == ex]
    return res.status


def test_same_split_from_nse_and_upstox_is_confirmed() -> None:
    worker, repo = _worker([_nse_split(date(2026, 1, 14))], _upstox(LIVE_SHAPE))
    worker.run(*WINDOW, [KOTAK], known_at=T0)
    assert _status(repo, date(2026, 1, 14)) is CorporateActionStatus.CONFIRMED


def test_old_split_before_upstox_coverage_does_not_become_a_conflict() -> None:
    """A 2022 split Upstox no longer serves: Upstox's silence is not evidence against NSE."""
    old = date(2022, 7, 28)
    worker, repo = _worker([_nse_split(old, 10, 1)], _upstox(LIVE_SHAPE))
    worker.run(*WINDOW, [KOTAK], known_at=T0 - timedelta(days=30))
    worker.run(*WINDOW, [KOTAK], known_at=T0)  # well past the grace period
    assert _status(repo, old) is CorporateActionStatus.SINGLE_SOURCE
    # the 2022 factor is kept; the 2026 split that only Upstox reports is a conflict (no factor)
    assert _status(repo, date(2026, 1, 14)) is CorporateActionStatus.PROVIDER_CONFLICT
    assert [a.effective_date for a in repo.load_adjustments(IID)] == [old]


def test_split_inside_upstox_coverage_that_upstox_lacks_is_still_a_conflict() -> None:
    recent = date(2026, 5, 4)  # after Upstox's earliest record (2026-01-14)
    worker, repo = _worker([_nse_split(recent)], _upstox(LIVE_SHAPE))
    worker.run(*WINDOW, [KOTAK], known_at=T0 - timedelta(days=30))
    worker.run(*WINDOW, [KOTAK], known_at=T0)
    assert _status(repo, recent) is CorporateActionStatus.PROVIDER_CONFLICT


def test_instrument_without_upstox_records_never_escalates() -> None:
    old = date(2025, 6, 16)
    worker, repo = _worker([_nse_split(old, 2, 1)], _upstox([]))
    worker.run(*WINDOW, [KOTAK], known_at=T0 - timedelta(days=30))
    worker.run(*WINDOW, [KOTAK], known_at=T0)
    assert _status(repo, old) is CorporateActionStatus.SINGLE_SOURCE


def test_expired_upstox_token_runs_nse_only_without_escalating() -> None:
    """Owner decision 2026-10-01: HTTP 401 from Upstox downgrades the run to NSE-only."""
    expired = UpstoxCorporateActionProvider("tok")
    expired._session = MagicMock()
    expired._session.get.return_value = MagicMock(status_code=401, text="Invalid token")
    recent = date(2026, 5, 4)  # would be a conflict if Upstox had answered without it
    worker, repo = _worker([_nse_split(recent)], expired)
    worker.run(*WINDOW, [KOTAK], known_at=T0 - timedelta(days=30))
    worker.run(*WINDOW, [KOTAK], known_at=T0)
    assert worker.secondary_unavailable is not None and "401" in worker.secondary_unavailable
    assert _status(repo, recent) is CorporateActionStatus.SINGLE_SOURCE
    assert len(repo.load_adjustments(IID)) == 1  # the NSE split still feeds a factor


# ---------------------------------------------------------------------------- zero amount


def test_zero_amount_is_not_reported_and_rights_are_confirmed() -> None:
    """Live 2026-10-02: Upstox sends amount 0.0 on rights (NATCOPHARM 2:21, ex 2026-10-01).
    Read as a 0 issue price it disagreed with NSE's 750 and made a PROVIDER_CONFLICT."""
    ex = date(2026, 9, 15)
    payload = [{"name": "Rights", "expiry_date": "15 Sep 2026", "ratio": "2:21", "amount": 0.0}]
    (rights,) = _upstox(payload).get_actions(*WINDOW, [KOTAK])
    assert rights.cash_amount is None
    nse = CorporateAction(
        corporate_action_id=deterministic_action_id("NSE", IID, "RIGHTS", ex, 2.0, 21.0, 750.0),
        instrument_id=IID, action_type=T.RIGHTS, source="NSE", created_at=T0,
        ex_date=ex, ratio_numerator=2.0, ratio_denominator=21.0, cash_amount=750.0,
    )  # fmt: skip
    worker, repo = _worker([nse], _upstox(payload))
    worker.run(*WINDOW, [KOTAK], known_at=T0)
    assert _status(repo, ex) is CorporateActionStatus.CONFIRMED
