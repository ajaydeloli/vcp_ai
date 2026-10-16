"""Shareholding pattern in the dashboard API (F8; FUNDAMENTALS_SPECIFICATION §15). Display only.

Fixture (tests/api/fixture.py ``add_shareholding``): ALPHA has quarter ends Mar-25 (SPLIT
layout), Jun-25, Sep-25, Mar-26 and Jun-26 (filed 15 Jul, revised 1 Aug), all broadcast at
18:00 IST; Dec-25 is missing; an event filing of 15 May 2026 and a Sep-26 filing broadcast after
AS_OF must never show. BETA has none.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from tests.api.conftest import Env
from vcp_scanner.api.shareholding import quarter_back

URL = "/api/v1/stocks/{}/fundamentals"


def get(env: Env, url: str, status: int = 200) -> dict[str, Any]:
    r = env.client.get(url)
    assert r.status_code == status, r.text
    body: dict[str, Any] = r.json()
    return body


def holding(env: Env, symbol: str = "ALPHA", on: str | None = None) -> dict[str, Any]:
    return get(env, URL.format(symbol) + (f"?date={on}" if on else ""))


def test_quarter_back() -> None:
    assert quarter_back(date(2026, 6, 30), 1) == date(2026, 3, 31)
    assert quarter_back(date(2026, 3, 31), 1) == date(2025, 12, 31)
    assert quarter_back(date(2026, 6, 30), 4) == date(2025, 6, 30)
    assert quarter_back(date(2025, 12, 31), 2) == date(2025, 6, 30)


def test_latest_quarter_uses_the_latest_revision_with_changes(ro_env: Env) -> None:
    s = holding(ro_env)["shareholding"]
    assert s["as_on"] == "2026-06-30" and s["revision"] == 1 and s["layout"] == "NEW"
    assert s["available_at"] == "2026-08-01T18:00:00+05:30"
    assert s["promoter_pct"] == 48.5 and s["promoter_pledged_pct"] == 3.0
    assert s["promoter_change_1q"] == pytest.approx(-0.5)  # against Mar-26
    assert s["promoter_change_4q"] == pytest.approx(-1.5)  # against Jun-25
    assert s["fii_change_1q"] == pytest.approx(1.0) and s["fii_change_4q"] == pytest.approx(2.0)
    assert s["dii_change_1q"] == pytest.approx(0.5) and s["dii_change_4q"] == pytest.approx(2.5)
    assert s["pledged_change_1q"] == pytest.approx(2.0) and s["pledge_rising"]
    assert s["shareholders_change_1q_pct"] == pytest.approx(1.0)
    assert not s["stale"]


def test_history_has_quarter_ends_only_and_nothing_from_the_future(ro_env: Env) -> None:
    quarters = holding(ro_env)["shareholding_quarters"]
    ends = [q["as_on"] for q in quarters]
    assert ends == ["2025-03-31", "2025-06-30", "2025-09-30", "2026-03-31", "2026-06-30"]
    assert quarters[-1]["revision"] == 1


def test_a_past_date_sees_only_what_was_broadcast_by_its_close(ro_env: Env) -> None:
    # The first Jun-26 filing came at 18:00 IST on 15 Jul: from 16 Jul, before the revision.
    assert holding(ro_env, on="2026-07-15")["shareholding"]["as_on"] == "2026-03-31"
    s = holding(ro_env, on="2026-07-16")["shareholding"]
    assert s["as_on"] == "2026-06-30" and s["revision"] == 0 and s["promoter_pct"] == 48.0
    assert s["promoter_change_1q"] == pytest.approx(-1.0)


def test_a_missing_quarter_is_na_never_zero(ro_env: Env) -> None:
    s = holding(ro_env, on="2026-06-01")["shareholding"]  # the 15 May event filing is ignored
    assert s["as_on"] == "2026-03-31"
    assert s["promoter_change_1q"] is None and s["fii_change_1q"] is None  # Dec-25 missing
    assert s["pledged_change_1q"] is None and not s["pledge_rising"]
    assert s["promoter_change_4q"] == pytest.approx(-1.0)  # against Mar-25


def test_no_pledge_change_across_the_layout_change(ro_env: Env) -> None:
    s = holding(ro_env, on="2025-08-01")["shareholding"]
    assert s["as_on"] == "2025-06-30" and s["promoter_change_1q"] == 0.0
    assert s["pledged_change_1q"] is None and not s["pledge_rising"]  # Mar-25 is SPLIT


def test_stale_when_the_next_filing_is_overdue(ro_env: Env) -> None:
    s = holding(ro_env, on="2026-02-16")["shareholding"]
    assert s["as_on"] == "2025-09-30" and s["stale"]


def test_none_without_filings(ro_env: Env) -> None:
    body = holding(ro_env, "BETA")
    assert body["shareholding"] is None and body["shareholding_quarters"] == []


def test_fundamentals_table_columns_filters_and_sort(ro_env: Env) -> None:
    rows = {r["symbol"]: r for r in get(ro_env, "/api/v1/fundamentals")["rows"]}
    alpha = rows["ALPHA"]
    assert alpha["shareholding_as_on"] == "2026-06-30" and not alpha["shareholding_stale"]
    assert alpha["promoter_pct"] == 48.5 and alpha["pledged_pct"] == 3.0
    assert alpha["promoter_change_1q"] == pytest.approx(-0.5)
    assert alpha["fii_pct"] == 23.0 and alpha["fii_change_1q"] == pytest.approx(1.0)
    assert alpha["dii_pct"] == 12.5 and alpha["dii_change_1q"] == pytest.approx(0.5)
    assert alpha["pledge_rising"] is True
    assert rows["BETA"]["promoter_pct"] is None and rows["BETA"]["pledge_rising"] is None

    def symbols(query: str) -> set[str]:
        return {r["symbol"] for r in get(ro_env, f"/api/v1/fundamentals?{query}")["rows"]}

    assert symbols("min_promoter_pct=40") == {"ALPHA"}
    assert symbols("max_pledged_pct=2") == {"BETA"}  # BETA has no pledge filed: it passes
    assert symbols("fii_buying=true") == {"ALPHA"}
    assert symbols("pledge_rising=true") == {"ALPHA"}
    top = get(ro_env, "/api/v1/fundamentals?sort=fii_change_1q&page_size=1")["rows"]
    assert top[0]["symbol"] == "ALPHA"
    get(ro_env, "/api/v1/fundamentals?max_pledged_pct=101", 422)


def test_the_screener_has_no_shareholding(ro_env: Env) -> None:
    row = get(ro_env, "/api/v1/screener?page_size=100")["rows"][0]
    assert "promoter_pct" not in row and "pledged_pct" not in row
    get(ro_env, "/api/v1/screener?sort=promoter_pct", 422)
