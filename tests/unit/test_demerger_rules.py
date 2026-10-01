"""Demerger factors: the 0.02 floor, the no-change band and hand-entered factors
(owner decision 2026-10-01). Prices are real NSE bhavcopy values where a symbol is named.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from vcp_scanner.data.adjustment.engine import AdjustmentEngine
from vcp_scanner.data.ingestion.ca_worker import CorporateActionIngestionWorker
from vcp_scanner.data.providers.manual_ca import ManualCorporateActionProvider, load_manual_entries
from vcp_scanner.data.reconciliation.engine import ReconciliationConfig, ReconciliationEngine
from vcp_scanner.data.repositories.duckdb_corporate_action_repository import (
    DuckDBCorporateActionRepository,
)
from vcp_scanner.data.repositories.duckdb_market_repository import (
    DuckDBMarketDataRepository,
    FinalDailyBar,
)
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.corporate_actions import (
    CorporateActionResolution,
    CorporateActionStatus,
    ExDatePrices,
    derived_factor,
    factor_unknown,
)
from vcp_scanner.domain.enums import CorporateActionType as T
from vcp_scanner.domain.errors import ConfigError
from vcp_scanner.domain.market import Instrument

AT = datetime(2026, 10, 1, 12, tzinfo=UTC)


def _demerger(num: float | None = None, den: float | None = None) -> CorporateActionResolution:
    return CorporateActionResolution(
        "r", "I", T.DEMERGER, CorporateActionStatus.SINGLE_SOURCE, ex_date=date(2025, 3, 10),
        ratio_numerator=num, ratio_denominator=den,
    )  # fmt: skip


def test_kesoramind_large_demerger_is_accepted() -> None:
    # Cement business to UltraTech: 204.72 -> 10.23 (factor 0.04997, under the old 0.05 floor).
    got = derived_factor(_demerger(), ExDatePrices(204.72, 10.23, raw=True))
    assert got is not None and got[0] == pytest.approx(0.049971, abs=1e-6)


@pytest.mark.parametrize(("open_", "expected"), [(346.20, (1.0, 1.0)), (349.0, (1.0, 1.0)),
                                                 (350.0, None)])  # fmt: skip
def test_open_at_or_just_above_prior_close_means_nothing_left(
    open_: float, expected: tuple[float, float] | None
) -> None:
    # DALMIASUG 2025-10-31 opened exactly at its prior close, 346.20. Up to +1 % is factor 1.0.
    prices = ExDatePrices(346.20, open_, raw=True)
    assert derived_factor(_demerger(), prices) == expected
    assert factor_unknown(_demerger(), prices) is (expected is None)


def test_hand_entered_factor_wins_even_without_an_ex_date_bar() -> None:
    manual = _demerger(0.9079, 1.0)
    assert derived_factor(manual, ExDatePrices(43.8, None, raw=True)) == (0.9079, 1.0)
    assert not factor_unknown(manual, ExDatePrices(43.8, None, raw=True))


ENTRY = """actions:
  - symbol: UEL
    action_type: DEMERGER
    ex_date: 2024-05-22
    {field}
    evidence: scheme document, parent value retained 90 percent
    approved_by: Ajay
    entered_on: 2026-10-01
"""


@pytest.mark.parametrize(
    ("field", "message"),
    [("ratio: [1, 2]", "price_factor"), ("cash_amount: 5", "price_factor"),
     ("price_factor: 1.5", "less than or equal to 1")],
)  # fmt: skip
def test_manual_demerger_needs_a_valid_price_factor(
    tmp_path: Path, field: str, message: str
) -> None:
    p = tmp_path / "m.yaml"
    p.write_text(ENTRY.format(field=field), encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        load_manual_entries(p)


def test_price_factor_only_for_demergers(tmp_path: Path) -> None:
    p = tmp_path / "m.yaml"
    p.write_text(ENTRY.format(field="price_factor: 0.9").replace("DEMERGER", "BONUS")
                 .replace("    price_factor", "    ratio: [1, 1]\n    price_factor"),
                 encoding="utf-8")  # fmt: skip
    with pytest.raises(ConfigError, match="only for DEMERGER"):
        load_manual_entries(p)


class _Provider:
    def get_actions(self, start, end, instruments=None):  # noqa: ANN001, ANN201
        return []


def test_worker_applies_a_manual_demerger_factor(tmp_path: Path) -> None:
    store = DuckDBStore(":memory:")
    store.migrate()
    market = DuckDBMarketDataRepository(store)
    iid = "NSE_EQ|UEL"
    market.save_final_daily(
        [FinalDailyBar(iid, date(2024, 5, 21), 43.8, 43.8, 43.8, 43.8, 10, "BE"),
         FinalDailyBar(iid, date(2024, 5, 27), 40.0, 40.0, 40.0, 40.0, 10, "BE")],
        known_from=AT, source_run_id="t",
    )  # fmt: skip
    p = tmp_path / "m.yaml"
    p.write_text(ENTRY.format(field="price_factor: 0.9"), encoding="utf-8")
    repo = DuckDBCorporateActionRepository(store)
    CorporateActionIngestionWorker(
        _Provider(), _Provider(), repo, ReconciliationEngine(ReconciliationConfig()),
        AdjustmentEngine(), market=market, manual_provider=ManualCorporateActionProvider(p),
    ).run(date(2024, 5, 1), date(2024, 5, 31), [Instrument(iid, "UEL")], known_at=AT)  # fmt: skip
    (factor,) = repo.load_adjustments(iid)
    assert factor.effective_date == date(2024, 5, 22)
    assert float(factor.price_factor) == pytest.approx(0.9)
