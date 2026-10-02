"""CAPITAL_REDUCTION: its own action class, never a price factor, always a warning for
manual review (owner decision 2026-10-02; MAXIND 2022, EASTSILK 2024)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from vcp_scanner.data.adjustment.engine import AdjustmentEngine
from vcp_scanner.data.identity import deterministic_action_id
from vcp_scanner.data.ingestion.ca_worker import CorporateActionIngestionWorker
from vcp_scanner.data.providers.manual_ca import (
    ManualActionEntry,
    ManualCorporateActionProvider,
    load_manual_entries,
)
from vcp_scanner.data.quality.events import unmodelled_action_events
from vcp_scanner.data.reconciliation.engine import ReconciliationConfig, ReconciliationEngine
from vcp_scanner.data.repositories.duckdb_corporate_action_repository import (
    DuckDBCorporateActionRepository,
)
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.corporate_actions import CorporateAction, CorporateActionStatus
from vcp_scanner.domain.enums import CorporateActionType as T
from vcp_scanner.domain.market import Instrument

IID = "NSE_EQ|MAXIND"
EX = date(2022, 7, 26)
T0 = datetime(2026, 10, 2, 6, tzinfo=UTC)
REPO_ROOT = Path(__file__).resolve().parents[2]


def test_manual_capital_reduction_takes_details_but_never_a_ratio() -> None:
    base = dict(
        symbol="MAXIND", action_type="CAPITAL_REDUCTION", ex_date=EX,
        evidence="NCLT order, tender at Rs 85", approved_by="Ajay", entered_on=date(2026, 10, 2),
    )  # fmt: skip
    e = ManualActionEntry(
        **base,
        reduction_kind="VOLUNTARY_TENDER",
        shares_before=53786261,
        shares_after=43029009,
        cash_amount=85,
    )
    assert "price adjustment NONE" in e.record_text()
    assert "53,786,261 -> 43,029,009" in e.record_text()
    with pytest.raises(ValidationError, match="no ratio"):
        ManualActionEntry(**base, reduction_kind="OTHER", ratio=(5, 4))
    with pytest.raises(ValidationError, match="reduction_kind"):
        ManualActionEntry(**base)
    with pytest.raises(ValidationError, match="only for CAPITAL_REDUCTION"):
        ManualActionEntry(**{**base, "action_type": "BONUS"}, ratio=(1, 1), shares_before=10)


def _nse(action_type: T) -> CorporateAction:
    return CorporateAction(
        corporate_action_id=deterministic_action_id("NSE", IID, action_type.value, EX),
        instrument_id=IID, action_type=action_type, source="NSE", created_at=T0, ex_date=EX,
        source_record_id="CAPITAL REDUCTION PURSUANT TO NCLT ORDER",
    )  # fmt: skip


class _Feed:
    def __init__(self, actions: list[CorporateAction]) -> None:
        self.actions = actions

    def get_actions(self, start, end, instruments=None):  # noqa: ANN001, ANN201
        return list(self.actions)


def _run(nse: list[CorporateAction], manual_file: Path | None):
    store = DuckDBStore(":memory:")
    store.migrate()
    repo = DuckDBCorporateActionRepository(store)
    worker = CorporateActionIngestionWorker(
        _Feed(nse), _Feed([]), repo, ReconciliationEngine(ReconciliationConfig()),
        AdjustmentEngine(),
        manual_provider=ManualCorporateActionProvider(manual_file) if manual_file else None,
    )  # fmt: skip
    worker.run(date(2022, 1, 1), date(2022, 12, 31), [Instrument(IID, "MAXIND")], known_at=T0)
    return repo


def test_unreviewed_reduction_warns_and_never_adjusts() -> None:
    repo = _run([_nse(T.UNMODELLED), _nse(T.CAPITAL_REDUCTION)], None)
    assert repo.load_adjustments(IID) == []
    (event,) = unmodelled_action_events(IID, repo.load_resolutions(IID), T0)  # one, not two
    assert event.blocks_signal is False
    assert event.context["manual_review"] == "PENDING"
    assert event.context["price_adjustment"] == "NONE"


def test_reviewed_reduction_still_warns_marked_reviewed(tmp_path: Path) -> None:
    f = tmp_path / "manual.yaml"
    f.write_text(
        "actions:\n  - symbol: MAXIND\n    action_type: CAPITAL_REDUCTION\n"
        "    ex_date: 2022-07-26\n    reduction_kind: VOLUNTARY_TENDER\n"
        "    evidence: NCLT order, tender at Rs 85\n    approved_by: Ajay\n"
        "    entered_on: 2026-10-02\n"
    )
    repo = _run([_nse(T.CAPITAL_REDUCTION)], f)
    statuses = {(r.action_type, r.status) for r in repo.load_resolutions(IID)}
    assert (T.CAPITAL_REDUCTION, CorporateActionStatus.MANUAL_OVERRIDE) in statuses
    assert repo.load_adjustments(IID) == []
    (event,) = unmodelled_action_events(IID, repo.load_resolutions(IID), T0)
    assert event.context["manual_review"] == "DONE" and "reviewed" in event.description


def test_the_project_file_holds_the_reviewed_entries() -> None:
    entries = {
        (e.symbol, e.action_type)
        for e in load_manual_entries(REPO_ROOT / "config" / "manual_corporate_actions.yaml")
    }
    assert ("MAXIND", T.CAPITAL_REDUCTION) in entries
    assert ("EASTSILK", T.CAPITAL_REDUCTION) in entries
    assert ("UEL", T.DEMERGER) in entries
