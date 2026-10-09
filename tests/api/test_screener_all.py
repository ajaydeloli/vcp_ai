"""The Screener with "Universe only" unchecked (FRONTEND_SPECIFICATION 67.24): the stocks outside
the scan universe are added with prices only, and nothing else changes."""

from __future__ import annotations

from tests.api.conftest import Env
from tests.api.test_api import get


def _rows(env: Env, query: str) -> dict:
    return get(env, f"/api/v1/screener?page_size=100&{query}")


def test_the_universe_is_the_default_and_has_no_outside_stock(ro_env: Env) -> None:
    body = _rows(ro_env, "")
    assert "NEWCO" not in {r["symbol"] for r in body["rows"]}
    assert all(r["in_universe"] and r["outside_reason"] is None for r in body["rows"])


def test_unchecking_universe_adds_outside_stocks_with_prices_only(ro_env: Env) -> None:
    inside = _rows(ro_env, "")
    body = _rows(ro_env, "universe_only=false")
    outside = [r for r in body["rows"] if not r["in_universe"]]
    assert "NEWCO" in {r["symbol"] for r in outside}
    assert body["scanned"] == inside["scanned"] + len(outside)
    assert body["stage_counts"] == inside["stage_counts"]  # the scan's own counts do not move
    new = next(r for r in body["rows"] if r["symbol"] == "NEWCO")
    assert new["in_universe"] is False and new["close"] is not None
    scan_fields = (
        "stage", "trend_template_pass", "conditions_passed", "near_52w_high", "rs_rank",
        "trend_score", "grade", "status", "score", "pivot", "eligible",
    )  # fmt: skip
    assert all(new[k] is None for k in scan_fields)  # "not available", never 0
    # the stocks of the universe are the same rows as before
    keep = {r["symbol"]: r for r in body["rows"] if r["in_universe"]}
    assert keep == {r["symbol"]: r for r in inside["rows"]}


def test_a_scan_filter_hides_the_outside_stocks(ro_env: Env) -> None:
    body = _rows(ro_env, "universe_only=false&min_rs=1")
    assert body["total"] > 0 and all(r["in_universe"] for r in body["rows"])
    assert "NEWCO" not in {
        r["symbol"] for r in _rows(ro_env, "universe_only=false&tt_pass=true")["rows"]
    }


def test_eq_only_keeps_stocks_of_unknown_series_and_the_filter_is_accepted(ro_env: Env) -> None:
    for eq in ("true", "false"):
        body = _rows(ro_env, f"universe_only=false&eq_only={eq}")
        assert "NEWCO" in {r["symbol"] for r in body["rows"]}
