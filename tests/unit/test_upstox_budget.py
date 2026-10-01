"""Upstox under its rate limit: per-run budget, rotation and 429 handling (owner decision
2026-10-01, after the 22:00 run hit HTTP 429). Synthetic data."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from unittest.mock import MagicMock

from vcp_scanner.data.adjustment.engine import AdjustmentEngine
from vcp_scanner.data.identity import deterministic_action_id
from vcp_scanner.data.ingestion.ca_worker import (
    CorporateActionIngestionWorker,
    select_secondary_instruments,
)
from vcp_scanner.data.providers.upstox_ca import UpstoxCorporateActionProvider
from vcp_scanner.data.reconciliation.engine import ReconciliationConfig, ReconciliationEngine
from vcp_scanner.data.repositories.duckdb_corporate_action_repository import (
    DuckDBCorporateActionRepository,
)
from vcp_scanner.data.repositories.duckdb_secondary_check_repository import (
    DuckDBSecondaryCheckRepository,
)
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.corporate_actions import CorporateAction
from vcp_scanner.domain.enums import CorporateActionType as T
from vcp_scanner.domain.market import Instrument

T0 = datetime(2026, 10, 1, 13, 45, tzinfo=UTC)
WINDOW = (date(2026, 8, 2), date(2026, 10, 1))


def _inst(sym: str) -> Instrument:
    return Instrument(f"NSE_EQ|{sym}", sym, isin=f"INE{sym}01")


INSTS = [_inst(s) for s in ("AAA", "BBB", "CCC", "DDD", "EEE")]


def _resp(status: int, data: list | None = None, headers: dict | None = None) -> MagicMock:
    r = MagicMock(status_code=status)
    r.json.return_value = {"data": data or []}
    r.text = ""
    r.headers = headers or {}
    return r


# ---------------------------------------------------------------- selection (pure)


def test_selection_puts_nse_actions_first_then_least_recently_checked() -> None:
    last = {
        "NSE_EQ|AAA": T0 - timedelta(days=1),
        "NSE_EQ|BBB": T0 - timedelta(days=3),
        "NSE_EQ|DDD": T0 - timedelta(days=2),
    }  # CCC and EEE never checked
    picked = select_secondary_instruments(INSTS, {"NSE_EQ|AAA"}, last, 4)
    assert [i.symbol for i in picked] == ["AAA", "CCC", "EEE", "BBB"]


def test_selection_is_everything_within_budget() -> None:
    assert select_secondary_instruments(INSTS, set(), {}, 10) == sorted(
        INSTS, key=lambda i: i.instrument_id
    )


# ---------------------------------------------------------------- worker rotation


class _Nse:
    def __init__(self, actions: list[CorporateAction]) -> None:
        self.actions = actions

    def get_actions(self, start, end, instruments=None):  # noqa: ANN001, ANN201
        return list(self.actions)


def _split(sym: str, ex: date) -> CorporateAction:
    iid = f"NSE_EQ|{sym}"
    return CorporateAction(
        corporate_action_id=deterministic_action_id("NSE", iid, "SPLIT", ex, 10.0, 2.0),
        instrument_id=iid, action_type=T.SPLIT, source="NSE", created_at=T0,
        ex_date=ex, ratio_numerator=10.0, ratio_denominator=2.0,
    )  # fmt: skip


def _upstox() -> UpstoxCorporateActionProvider:
    p = UpstoxCorporateActionProvider("tok")
    p._session = MagicMock()
    p._session.get.return_value = _resp(404)
    return p


def test_worker_rotates_through_instruments_under_the_budget() -> None:
    store = DuckDBStore(":memory:")
    store.migrate()
    checks = DuckDBSecondaryCheckRepository(store)
    upstox = _upstox()
    worker = CorporateActionIngestionWorker(
        _Nse([_split("EEE", date(2026, 9, 20))]), upstox,
        DuckDBCorporateActionRepository(store),
        ReconciliationEngine(ReconciliationConfig(secondary_grace_days=3)), AdjustmentEngine(),
        secondary_budget=2, secondary_checks=checks,
    )  # fmt: skip

    worker.run(*WINDOW, INSTS, known_at=T0)
    assert upstox.queried_instrument_ids == {"NSE_EQ|EEE", "NSE_EQ|AAA"}  # EEE has a split
    assert worker.secondary_requested == 2

    worker.run(*WINDOW, INSTS, known_at=T0 + timedelta(hours=3))
    assert upstox.queried_instrument_ids == {"NSE_EQ|EEE", "NSE_EQ|BBB"}

    worker.run(*WINDOW, INSTS, known_at=T0 + timedelta(hours=6))
    assert upstox.queried_instrument_ids == {"NSE_EQ|EEE", "NSE_EQ|CCC"}
    assert set(checks.last_checked("UPSTOX")) == {
        "NSE_EQ|AAA",
        "NSE_EQ|BBB",
        "NSE_EQ|CCC",
        "NSE_EQ|EEE",
    }  # DDD is next


def test_no_budget_asks_every_instrument() -> None:
    store = DuckDBStore(":memory:")
    store.migrate()
    upstox = _upstox()
    worker = CorporateActionIngestionWorker(
        _Nse([]), upstox, DuckDBCorporateActionRepository(store),
        ReconciliationEngine(ReconciliationConfig()), AdjustmentEngine(),
    )  # fmt: skip
    worker.run(*WINDOW, INSTS, known_at=T0)
    assert len(upstox.queried_instrument_ids) == 5


def test_check_log_upserts_the_latest_time() -> None:
    store = DuckDBStore(":memory:")
    store.migrate()
    checks = DuckDBSecondaryCheckRepository(store)
    checks.record_checked("UPSTOX", ["NSE_EQ|AAA"], T0)
    checks.record_checked("UPSTOX", ["NSE_EQ|AAA", "NSE_EQ|BBB"], T0 + timedelta(hours=1))
    assert checks.last_checked("UPSTOX") == {
        "NSE_EQ|AAA": T0 + timedelta(hours=1),
        "NSE_EQ|BBB": T0 + timedelta(hours=1),
    }
    assert checks.last_checked("OTHER") == {}


# ---------------------------------------------------------------- HTTP 429


def _provider(*responses: MagicMock) -> tuple[UpstoxCorporateActionProvider, list[float]]:
    waits: list[float] = []
    p = UpstoxCorporateActionProvider("tok", min_request_interval_seconds=0, sleep=waits.append)
    p._session = MagicMock()
    p._session.get.side_effect = list(responses)
    return p, waits


def test_one_429_waits_retry_after_and_continues() -> None:
    p, waits = _provider(_resp(200), _resp(429, headers={"Retry-After": "7"}), _resp(200),
                         _resp(200))  # fmt: skip
    p.get_actions(*WINDOW, INSTS[:3])
    assert 7.0 in waits
    assert len(p.queried_instrument_ids) == 3 and p.rate_limited is None


def test_repeated_429_stops_asking_without_failing_the_run() -> None:
    p, waits = _provider(_resp(200), _resp(429), _resp(429))
    assert p.get_actions(*WINDOW, INSTS) == []
    assert p.queried_instrument_ids == {"NSE_EQ|AAA"}
    assert p.rate_limited is not None and "after 1 of 5" in p.rate_limited
    assert p._session.get.call_count == 3  # nothing asked after the stop
    assert 30.0 in waits  # no Retry-After header: default wait


def test_retry_after_is_capped() -> None:
    p, waits = _provider(_resp(429, headers={"Retry-After": "3600"}), _resp(200))
    p.get_actions(*WINDOW, INSTS[:1])
    assert max(waits) == 60.0
