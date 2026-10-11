"""Fundamentals in the dashboard API (F5; FUNDAMENTALS_SPECIFICATION §11). Display only.

Fixture (tests/api/fixture.py ``add_fundamentals``): ALPHA has quarters Jun-25..Jun-26, each
published 20 days after its end at 18:00 IST, Mar-26 estimated, equity 400 cr and borrowings
100 cr on Mar-26, and a Sep-26 quarter published after AS_OF. BETA has one quarter, GAMMA none.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.api.conftest import Env


def get(env: Env, url: str, status: int = 200) -> dict[str, Any]:
    r = env.client.get(url)
    assert r.status_code == status, r.text
    body: dict[str, Any] = r.json()
    return body


def test_stock_summary_known_answers(ro_env: Env) -> None:
    body = get(ro_env, "/api/v1/stocks/ALPHA/fundamentals")
    assert body["as_of"] == "2026-10-05" and body["reason"] is None
    s = body["summary"]
    assert s["period_end"] == "2026-06-30" and s["basis"] == "CONSOLIDATED"
    assert s["available_at"] == "2026-07-20T18:00:00+05:30"  # IST, publication time
    assert s["eps"] == 3.0 and s["revenue"] == 125e7
    assert s["eps_yoy"] == pytest.approx(50.0)  # 3.0 against 2.0
    assert s["sales_yoy"] == pytest.approx(25.0)  # 125 against 100 crore
    assert s["eps_qoq"] == pytest.approx((3.0 - 2.8) / 2.8 * 100)
    assert s["roe"] == pytest.approx((2.2 + 2.5 + 2.8 + 3.0) / 400 * 100)
    assert s["debt_to_equity"] == pytest.approx(0.25)
    assert s["operating_margin"] == pytest.approx(20 / 125 * 100)
    assert s["statuses"]["eps_yoy"] == "AVAILABLE" and not s["stale"]
    assert s["quarters_available"] == 5


def test_quarters_oldest_first_and_never_from_the_future(ro_env: Env) -> None:
    quarters = get(ro_env, "/api/v1/stocks/ALPHA/fundamentals")["quarters"]
    ends = [x["period_end"] for x in quarters]
    assert ends == ["2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"]
    assert [x["estimated"] for x in quarters] == [False, False, False, True, False]
    assert quarters[0]["eps_yoy"] is None and quarters[0]["eps_yoy_status"] == "MISSING"
    assert quarters[-1]["eps_yoy"] == pytest.approx(50.0)
    assert quarters[-1]["available_at"] == "2026-07-20T18:00:00+05:30"


def test_a_past_date_sees_only_what_was_published_by_then(ro_env: Env) -> None:
    body = get(ro_env, "/api/v1/stocks/ALPHA/fundamentals?date=2026-03-01")
    assert body["summary"]["period_end"] == "2025-12-31"  # published 19 Jan 2026
    assert [x["period_end"] for x in body["quarters"]][-1] == "2025-12-31"
    # Published at 18:00 IST on 20 Jul: not usable at that day's close, only the next day.
    assert (
        get(ro_env, "/api/v1/stocks/ALPHA/fundamentals?date=2026-07-20")["summary"]["period_end"]
        == "2026-03-31"
    )
    assert (
        get(ro_env, "/api/v1/stocks/ALPHA/fundamentals?date=2026-07-21")["summary"]["period_end"]
        == "2026-06-30"
    )


def test_missing_is_null_with_a_reason_never_zero(ro_env: Env) -> None:
    beta = get(ro_env, "/api/v1/stocks/BETA/fundamentals")["summary"]
    assert beta["eps"] == 4.0 and beta["eps_yoy"] is None
    assert beta["statuses"]["eps_yoy"] == "MISSING" and beta["roe"] is None
    assert not beta["gate_pass"] and beta["gate_reason"] == "INSUFFICIENT_DATA"
    gamma = get(ro_env, "/api/v1/stocks/GAMMA/fundamentals")
    assert gamma["summary"] is None and gamma["quarters"] == []
    assert gamma["reason"] == "No results filed yet"
    get(ro_env, "/api/v1/stocks/NOPE/fundamentals", 404)


def test_stale_after_the_staleness_limit(ro_env: Env) -> None:
    # Jun-26 is the latest quarter; 120 days after its end (28 Oct 2026) the view is stale.
    late = get(ro_env, "/api/v1/stocks/BETA/fundamentals?date=2026-10-29")["summary"]
    assert late["stale"] and late["stale_after"] == "2026-10-28"


def test_table_counts_sorts_and_puts_missing_last(ro_env: Env) -> None:
    body = get(ro_env, "/api/v1/fundamentals")
    assert body["as_of"] == "2026-10-05"
    assert (body["universe"], body["covered"], body["total"]) == (4, 2, 2)
    rows = body["rows"]
    assert [r["symbol"] for r in rows] == ["ALPHA", "BETA"]  # BETA's EPS growth is null
    alpha = rows[0]
    assert alpha["rs_rank"] == 91 and alpha["sector"] == "Capital Goods"
    assert alpha["eps_yoy"] == pytest.approx(50.0)
    asc = get(ro_env, "/api/v1/fundamentals?sort=eps_yoy&direction=asc")["rows"]
    assert [r["symbol"] for r in asc] == ["ALPHA", "BETA"]  # a null is last either way
    by_eps = get(ro_env, "/api/v1/fundamentals?sort=symbol&direction=desc")["rows"]
    assert [r["symbol"] for r in by_eps] == ["BETA", "ALPHA"]


def test_table_filters(ro_env: Env) -> None:
    def symbols(query: str) -> list[str]:
        return [r["symbol"] for r in get(ro_env, f"/api/v1/fundamentals?{query}")["rows"]]

    assert symbols("min_eps_yoy=40") == ["ALPHA"]
    assert symbols("min_eps_yoy=60") == []
    assert symbols("min_sales_yoy=20") == ["ALPHA"]
    assert symbols("sector=healthcare") == ["BETA"]
    assert symbols("q=alp") == ["ALPHA"]
    assert symbols("min_availability=0.5") == ["ALPHA"]
    assert symbols("accelerating=true") == []  # no quarter of ALPHA has growth a year before
    assert symbols("current_only=true") == ["ALPHA", "BETA"]
    get(ro_env, "/api/v1/fundamentals?sort=price", 422)


def test_screener_shows_and_filters_eps_and_sales_growth(ro_env: Env) -> None:
    rows = {r["symbol"]: r for r in get(ro_env, "/api/v1/screener?page_size=100")["rows"]}
    assert rows["ALPHA"]["eps_yoy"] == pytest.approx(50.0)
    assert rows["ALPHA"]["sales_yoy"] == pytest.approx(25.0)
    assert rows["GAMMA"]["eps_yoy"] is None and rows["F00"]["sales_yoy"] is None
    hits = get(ro_env, "/api/v1/screener?min_eps_yoy=10")["rows"]
    assert [r["symbol"] for r in hits] == ["ALPHA"]
    top = get(ro_env, "/api/v1/screener?sort=eps_yoy&page_size=2")["rows"]
    assert [r["symbol"] for r in top] == ["ALPHA", "BETA"]  # BETA (null) after ALPHA
