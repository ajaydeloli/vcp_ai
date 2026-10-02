"""Hand-entered corporate actions: file, provider, reconciliation, worker (audit 2.7d)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from vcp_scanner.data.adjustment.engine import AdjustmentEngine
from vcp_scanner.data.ingestion.ca_worker import CorporateActionIngestionWorker
from vcp_scanner.data.providers.manual_ca import (
    MANUAL_SOURCE,
    ManualCorporateActionProvider,
    load_manual_entries,
)
from vcp_scanner.data.reconciliation.engine import ReconciliationConfig, ReconciliationEngine
from vcp_scanner.data.repositories.duckdb_corporate_action_repository import (
    DuckDBCorporateActionRepository,
)
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.corporate_actions import CorporateAction, CorporateActionStatus
from vcp_scanner.domain.enums import CorporateActionType as T
from vcp_scanner.domain.errors import ConfigError
from vcp_scanner.domain.market import Instrument

AT = datetime(2026, 10, 1, 12, tzinfo=UTC)
REPO_FILE = Path(__file__).resolve().parents[2] / "config" / "manual_corporate_actions.yaml"

ENTRY = """
  - symbol: JSLL
    isin: INE0J5801011
    action_type: SPLIT
    ex_date: 2025-06-12
    ratio: [10, 2]
    evidence: NSE record-date notice, record date 2025-06-12
    approved_by: Ajay
    entered_on: 2026-10-01
"""


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "manual.yaml"
    p.write_text(body, encoding="utf-8")
    return p


# --- the file -----------------------------------------------------------------------------


def test_repository_file_holds_the_verified_overrides() -> None:
    got = {(e.symbol, e.action_type, e.ex_date, e.ratio) for e in load_manual_entries(REPO_FILE)}
    assert got == {
        ("DTIL", T.BONUS, date(2021, 8, 5), (1.0, 2.0)),
        ("GICL", T.SPLIT, date(2025, 10, 15), (10.0, 5.0)),
        ("GICL", T.BONUS, date(2025, 10, 15), (1.0, 1.0)),
        ("JSLL", T.SPLIT, date(2025, 6, 12), (10.0, 2.0)),
        ("MAXIND", T.CAPITAL_REDUCTION, date(2022, 7, 26), None),
        ("EASTSILK", T.CAPITAL_REDUCTION, date(2024, 11, 22), None),
        ("UEL", T.DEMERGER, date(2024, 5, 22), None),
    }


def test_missing_file_means_no_overrides(tmp_path: Path) -> None:
    assert load_manual_entries(tmp_path / "absent.yaml") == []


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (("ratio: [10, 2]", "ratio: null"), "needs a ratio"),
        (("ratio: [10, 2]", "ratio: [2, 2]"), "must differ"),
        (("ratio: [10, 2]", "ratio: [10, 0]"), "greater than 0"),
        (("evidence: NSE record-date notice, record date 2025-06-12", "evidence: x"), "evidence"),
        (("isin: INE0J5801011", "isin: 341033"), "isin"),
        (("approved_by: Ajay", "approved_by: Ajay\n    note: extra"), "note"),
    ],
    ids=["no-ratio", "same-face-values", "zero", "no-evidence", "bad-isin", "unknown-field"],
)
def test_invalid_entries_raise(tmp_path: Path, change: tuple[str, str], message: str) -> None:
    body = "actions:" + ENTRY.replace(*change)
    with pytest.raises(ConfigError, match=message):
        load_manual_entries(_write(tmp_path, body))


def test_rights_needs_an_issue_price(tmp_path: Path) -> None:
    body = "actions:" + ENTRY.replace("action_type: SPLIT", "action_type: RIGHTS")
    with pytest.raises(ConfigError, match="cash_amount"):
        load_manual_entries(_write(tmp_path, body))


def test_duplicate_entries_raise(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Duplicate"):
        load_manual_entries(_write(tmp_path, "actions:" + ENTRY + ENTRY))


# --- the provider -------------------------------------------------------------------------


def test_provider_serves_every_entry_whatever_the_window(tmp_path: Path) -> None:
    p = ManualCorporateActionProvider(_write(tmp_path, "actions:" + ENTRY), clock=lambda: AT)
    (a,) = p.get_actions(date(2026, 9, 1), date(2026, 9, 30))  # window long after the ex-date
    assert (a.source, a.instrument_id, a.isin) == (MANUAL_SOURCE, "NSE_EQ|JSLL", "INE0J5801011")
    assert (a.action_type, a.ex_date, a.ratio_numerator, a.ratio_denominator) == (
        T.SPLIT, date(2025, 6, 12), 10.0, 2.0,
    )  # fmt: skip
    assert "record-date notice" in (a.source_record_id or "")
    assert p.get_actions(date(2026, 9, 1), date(2026, 9, 30))[0].corporate_action_id == (
        a.corporate_action_id
    )  # deterministic: re-ingesting saves nothing new


def test_provider_honours_the_instrument_filter(tmp_path: Path) -> None:
    p = ManualCorporateActionProvider(_write(tmp_path, "actions:" + ENTRY))
    assert p.get_actions(date(2025, 1, 1), date(2025, 12, 31), [Instrument("NSE_EQ|X", "X")]) == []


# --- reconciliation -----------------------------------------------------------------------


def _action(source: str, num: float, den: float) -> CorporateAction:
    return CorporateAction(
        f"{source}-1", "I", T.SPLIT, source, AT, ex_date=date(2025, 6, 12),
        ratio_numerator=num, ratio_denominator=den, ingested_at=AT,
    )  # fmt: skip


def test_manual_action_alone_is_a_manual_override() -> None:
    (r,) = ReconciliationEngine().reconcile("I", [_action("MANUAL", 10, 2)], date(2026, 10, 1))
    res = r.resolution
    assert res.status is CorporateActionStatus.MANUAL_OVERRIDE
    assert (res.ratio_numerator, res.ratio_denominator, res.conflict_fields) == (10, 2, None)


def test_manual_action_wins_over_feeds_that_disagree() -> None:
    actions = [_action("NSE", 10, 1), _action("UPSTOX", 10, 5), _action("MANUAL", 10, 2)]
    (r,) = ReconciliationEngine().reconcile("I", actions, date(2026, 10, 1))
    assert r.resolution.status is CorporateActionStatus.MANUAL_OVERRIDE
    assert (r.resolution.ratio_numerator, r.resolution.ratio_denominator) == (10, 2)
    assert r.resolution.nse_action_id == "NSE-1"  # the feed record stays linked


# --- end to end through the worker --------------------------------------------------------


class _Provider:
    def __init__(self, actions: list[CorporateAction]) -> None:
        self.actions = actions

    def get_actions(self, start, end, instruments=None):  # noqa: ANN001, ANN201
        return list(self.actions)


def test_worker_turns_a_manual_entry_into_an_adjustment_factor(tmp_path: Path) -> None:
    store = DuckDBStore(":memory:")
    store.migrate()
    repo = DuckDBCorporateActionRepository(store)
    worker = CorporateActionIngestionWorker(
        _Provider([]), _Provider([]), repo, ReconciliationEngine(ReconciliationConfig()),
        AdjustmentEngine(),
        manual_provider=ManualCorporateActionProvider(_write(tmp_path, "actions:" + ENTRY)),
    )  # fmt: skip
    worker.run(date(2026, 9, 1), date(2026, 9, 30), [Instrument("NSE_EQ|JSLL", "JSLL")],
               known_at=AT)  # fmt: skip
    assert worker.manual_count == 1
    (res,) = repo.load_resolutions("NSE_EQ|JSLL")
    assert res.status is CorporateActionStatus.MANUAL_OVERRIDE
    (factor,) = repo.load_adjustments("NSE_EQ|JSLL")
    assert factor.effective_date == date(2025, 6, 12)
    assert float(factor.price_factor) == pytest.approx(0.2)
