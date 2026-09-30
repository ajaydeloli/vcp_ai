"""Golden corporate actions on REAL data (DATA_SPECIFICATION §18A; audit Fix 5b).

``tests/fixtures/corporate_actions/golden_actions.json`` was captured once by
``scripts/capture_golden_ca.py`` from two independent sources: NSE's official bhavcopy (true
raw prices) and Kite Connect candles (adjusted as of the fetch time). It is real market data,
labelled as such. The tests are offline.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from vcp_scanner.data.adjustment.engine import AdjustmentEngine
from vcp_scanner.data.providers.nse_ca import parse_ratio
from vcp_scanner.data.quality.provider_adjustment import AdjustmentVerdict, classify_adjustment
from vcp_scanner.data.reconciliation.gap_detector import GapDetector
from vcp_scanner.domain.corporate_actions import (
    CorporateActionResolution,
    CorporateActionStatus,
)
from vcp_scanner.domain.enums import CorporateActionType, Timeframe
from vcp_scanner.domain.market import Candle

pytestmark = pytest.mark.regression

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "corporate_actions"
GOLDEN = json.loads((FIXTURE / "golden_actions.json").read_text())
ACTIONS = GOLDEN["actions"]
IDS = [f"{a['symbol']}-{a['ex_date']}" for a in ACTIONS]


def _resolutions(action: dict[str, Any]) -> list[CorporateActionResolution]:
    return [
        CorporateActionResolution(
            resolution_id=f"{action['symbol']}-{i}",
            instrument_id=f"NSE_EQ|{action['symbol']}",
            action_type=CorporateActionType(kind),
            status=CorporateActionStatus.CONFIRMED,
            ex_date=date.fromisoformat(action["ex_date"]),
            ratio_numerator=float(num),
            ratio_denominator=float(den),
        )
        for i, (kind, num, den) in enumerate(action["components"])
    ]


def _bar(action: dict[str, Any], side: str, source: str) -> Candle:
    row = action[source][side]
    d = date.fromisoformat(row["date"])
    fetched = datetime.fromisoformat(action["kite"]["fetched_at"]) if source == "kite" else None
    price = row["open"] if side == "ex" else row["close"]
    return Candle(
        instrument_id=f"NSE_EQ|{action['symbol']}",
        timestamp=datetime(d.year, d.month, d.day, tzinfo=UTC),
        timeframe=Timeframe.DAILY,
        open=row["open"],
        high=max(row["open"], row["close"], price),
        low=min(row["open"], row["close"], price),
        close=row["close"],
        volume=row["volume"],
        provider="KITE" if source == "kite" else "NSE_BHAVCOPY",
        ingested_at=fetched,
    )


def test_fixture_is_real_and_covers_the_required_cases() -> None:
    assert "REAL market data" in GOLDEN["description"]
    assert len(ACTIONS) >= 5  # DATA_SPECIFICATION 18A: 5-10 real splits/bonuses
    kinds = {k for a in ACTIONS for k, _, _ in a["components"]}
    assert kinds == {"SPLIT", "BONUS"}
    assert any(len(a["components"]) > 1 for a in ACTIONS)  # same-day split + bonus


@pytest.mark.parametrize("action", ACTIONS, ids=IDS)
def test_engine_factor_matches_the_fixture(action: dict[str, Any]) -> None:
    (adj,) = AdjustmentEngine().compute_factors(_resolutions(action))
    assert adj.cumulative_price_factor == pytest.approx(action["price_factor"], rel=1e-9)


@pytest.mark.parametrize("action", ACTIONS, ids=IDS)
def test_raw_bhavcopy_jumps_by_the_factor_on_the_ex_date(action: dict[str, Any]) -> None:
    """Confirms the ex-date and ratio from NSE's own raw file, independent of Kite."""
    check = classify_adjustment(
        action["symbol"],
        date.fromisoformat(action["ex_date"]),
        action["price_factor"],
        [_bar(action, "prev", "raw"), _bar(action, "ex", "raw")],
    )
    assert check.verdict is AdjustmentVerdict.RAW, check


@pytest.mark.parametrize("action", ACTIONS, ids=IDS)
def test_kite_history_is_already_adjusted(action: dict[str, Any]) -> None:
    check = classify_adjustment(
        action["symbol"],
        date.fromisoformat(action["ex_date"]),
        action["price_factor"],
        [_bar(action, "prev", "kite"), _bar(action, "ex", "kite")],
    )
    assert check.verdict is AdjustmentVerdict.ADJUSTED, check


def _symbol_history(action: dict[str, Any]) -> list[CorporateActionResolution]:
    """Every golden action of the same stock (Kite's history reflects all of them)."""
    return [
        r for other in ACTIONS if other["symbol"] == action["symbol"] for r in _resolutions(other)
    ]


@pytest.mark.parametrize("action", ACTIONS, ids=IDS)
def test_local_adjustment_of_raw_prices_reproduces_kite(action: dict[str, Any]) -> None:
    """raw close x our cumulative factor == Kite's adjusted close (independent sources agree).

    The cumulative factor includes later actions of the same stock (NESTLEIND's 2024 split is
    followed by its 2025 bonus), exactly as the engine applies them.
    """
    factors = AdjustmentEngine().compute_factors(_symbol_history(action))
    (row,) = AdjustmentEngine().build_adjusted_rows(
        [_bar(action, "prev", "raw")], factors, computed_at=datetime.now(UTC)
    )
    assert float(row.price_factor_applied) == pytest.approx(action["cumulative_price_factor"])
    kite_close = action["kite"]["prev"]["close"]
    if "kite_residual_reason" in action:
        # Kite applied additional (dividend) adjustments the local engine does not model.
        assert row.close_adj * action["kite_residual"] == pytest.approx(kite_close, rel=1e-4)
        assert 0.90 < action["kite_residual"] < 0.99
    else:
        assert row.close_adj == pytest.approx(kite_close, rel=0.005)


@pytest.mark.parametrize("action", ACTIONS, ids=IDS)
def test_kite_bars_fetched_after_the_action_are_not_adjusted_again(action: dict[str, Any]) -> None:
    factors = AdjustmentEngine().compute_factors(_resolutions(action))
    bars = [_bar(action, "prev", "kite"), _bar(action, "ex", "kite")]
    rows = AdjustmentEngine().build_adjusted_rows(bars, factors, computed_at=datetime.now(UTC))
    assert [r.close_adj for r in rows] == [b.close for b in bars]
    assert all(float(r.price_factor_applied) == 1.0 for r in rows)


@pytest.mark.parametrize("action", ACTIONS, ids=IDS)
def test_gap_net_flags_the_raw_jump_only_without_the_action(action: dict[str, Any]) -> None:
    raw = [_bar(action, "prev", "raw"), _bar(action, "ex", "raw")]
    detector = GapDetector()
    (event,) = detector.detect(raw, [], datetime.now(UTC))
    assert event.blocks_signal is True  # every golden action is split-like
    assert detector.detect(raw, _resolutions(action), datetime.now(UTC)) == []


@pytest.mark.parametrize(
    ("text", "kind", "expected"),
    [
        # NSE subject strings in the formats the live feed returned for these actions.
        ("FACE VALUE SPLIT (SUB-DIVISION) - FROM RS 10/- PER SHARE TO RS 2/- PER SHARE",
         CorporateActionType.SPLIT, (10.0, 2.0)),
        ("FACE VALUE SPLIT (SUB-DIVISION) - FROM RS 10/- PER SHARE TO RE 1/- PER SHARE",
         CorporateActionType.SPLIT, (10.0, 1.0)),
        ("BONUS 1:1", CorporateActionType.BONUS, (1.0, 1.0)),
        ("BONUS 4:1", CorporateActionType.BONUS, (4.0, 1.0)),
    ],
)  # fmt: skip
def test_nse_subject_formats_parse_to_the_verified_ratios(
    text: str, kind: CorporateActionType, expected: tuple[float, float]
) -> None:
    assert parse_ratio(text, kind) == expected


def test_only_tatasteel_needs_the_documented_dividend_residual() -> None:
    flagged = {a["symbol"] for a in ACTIONS if "kite_residual_reason" in a}
    assert flagged == {"TATASTEEL"}


def test_combined_bajfinance_factor_is_one_tenth() -> None:
    (baj,) = [a for a in ACTIONS if a["symbol"] == "BAJFINANCE"]
    assert math.isclose(baj["price_factor"], 0.1)
