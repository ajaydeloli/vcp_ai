"""Dashboard API v1 against the fixture database (FRONTEND_SPECIFICATION 67.3)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import duckdb
import pytest

from tests.api.conftest import Env
from tests.api.fixture import AS_OF, PREV, iid
from vcp_scanner.data.providers._time import IST
from vcp_scanner.serving import refresh_serving_copy

FROZEN = {
    "vcp": "64da9482a769", "flat_base": "c4918be30855", "three_weeks_tight": "9ed3b8e7c31a",
    "cup_handle": "e36477f23b36", "double_bottom": "dd7107637b1b",
}  # fmt: skip


def get(env: Env, url: str, status: int = 200) -> dict[str, Any]:
    r = env.client.get(url)
    assert r.status_code == status, r.text
    body: dict[str, Any] = r.json()
    return body


def test_strategies_are_the_five_frozen_ones(ro_env: Env) -> None:
    body = get(ro_env, "/api/v1/strategies")
    assert {s["strategy_id"]: s["config_hash"] for s in body["strategies"]} == FROZEN
    flat = next(s for s in body["strategies"] if s["strategy_id"] == "flat_base")
    assert flat["stage"] == "paper" and flat["min_grade"] == 2
    ranked = {t["name"]: t["ranked"] for t in flat["tiers"]}
    assert ranked["FLAT_BASE"] and not ranked["FLAT_BASE_LIKE"]


def test_every_response_carries_as_of_and_data_time(ro_env: Env) -> None:
    urls = ["status", "strategies", "summary", "market", "setups", "setups/overlap",
            "stocks/ALPHA/bars", "stocks/ALPHA/setups", "activity", "paper"]  # fmt: skip
    for u in urls:
        body = get(ro_env, f"/api/v1/{u}")
        assert "as_of" in body and "data_time" in body, u
        stamp = datetime.fromisoformat(body["data_time"])
        assert stamp.utcoffset() == IST.utcoffset(None) or stamp.tzinfo is not None
    assert get(ro_env, "/api/v1/summary")["as_of"] == AS_OF.isoformat()


def test_setups_is_the_ranked_list_best_score_first(ro_env: Env) -> None:
    rows = get(ro_env, "/api/v1/setups?strategy=vcp")["rows"]
    assert [r["symbol"] for r in rows] == ["ALPHA", "BETA"]  # GAMMA is not ranked
    alpha = rows[0]
    assert (alpha["classification"], alpha["grade"], alpha["status"]) == (
        "A_PLUS_VCP",
        3,
        "PIVOT_READY",
    )
    assert (alpha["score"], alpha["rs_rank"], alpha["pivot"]) == (91.0, 91, 120.0)
    assert alpha["company"] == "Alpha Industries Ltd" and rows[1]["company"] == "Beta Ltd"
    assert alpha["stop"] == 122.0 and alpha["base_days"] == 50  # last contraction's trough
    assert rows[1]["breakout_date"] == AS_OF.isoformat() and alpha["breakout_date"] is None


def test_setup_price_and_change_come_from_the_bars(ro_env: Env) -> None:
    alpha = get(ro_env, "/api/v1/setups?strategy=vcp")["rows"][0]
    bars = get(ro_env, "/api/v1/stocks/ALPHA/bars?days=30")["bars"]
    last, prev = bars[-1], bars[-2]
    assert last["day"] == AS_OF.isoformat()
    assert alpha["close"] == last["close"]
    assert alpha["change_pct"] == pytest.approx((last["close"] / prev["close"] - 1) * 100)


def test_missing_values_are_null_never_zero(ro_env: Env) -> None:
    rows = get(ro_env, "/api/v1/setups?strategy=vcp&eligible=false")["rows"]
    gamma = next(r for r in rows if r["symbol"] == "GAMMA")
    assert gamma["pivot"] is None and gamma["pivot_distance_pct"] is None
    assert gamma["company"] is None and gamma["percentile"] is None
    assert gamma["eligible"] is False


def test_setups_filters(ro_env: Env) -> None:
    assert [
        r["symbol"] for r in get(ro_env, "/api/v1/setups?strategy=vcp&min_grade=3")["rows"]
    ] == ["ALPHA"]
    assert [r["symbol"] for r in get(ro_env, "/api/v1/setups?strategy=vcp&status=breakout")["rows"]
            ] == ["BETA"]  # fmt: skip
    flat = get(ro_env, "/api/v1/setups?strategy=flat_base")["rows"]
    assert [r["symbol"] for r in flat] == ["GAMMA", "ALPHA"]  # score 80 before 70
    body = get(ro_env, f"/api/v1/setups?strategy=vcp&date={PREV}")
    assert body["rows"] == [] and body["as_of"] == PREV.isoformat()
    assert len(get(ro_env, "/api/v1/setups?strategy=vcp&limit=1")["rows"]) == 1
    get(ro_env, "/api/v1/setups?strategy=nope", 404)
    get(ro_env, "/api/v1/setups?strategy=vcp&min_grade=9", 422)


def test_overlap_lists_stocks_on_several_lists(ro_env: Env) -> None:
    rows = get(ro_env, "/api/v1/setups/overlap")["rows"]
    assert [r["symbol"] for r in rows] == ["ALPHA"]
    assert {e["strategy_id"] for e in rows[0]["strategies"]} == {"vcp", "flat_base", "cup_handle"}


def test_summary_counts(ro_env: Env) -> None:
    body = get(ro_env, "/api/v1/summary")
    assert (body["universe_size"], body["scanned"], body["trend_template_pass"]) == (63, 3, 3)
    by = {s["strategy_id"]: s for s in body["strategies"]}
    assert by["vcp"] == {"strategy_id": "vcp", "ranked": 2, "grade2_plus": 2, "breakouts": 1,
                         "mean_top10_score": 88.5}  # fmt: skip
    assert by["flat_base"]["ranked"] == 2 and by["double_bottom"]["ranked"] == 0
    assert by["double_bottom"]["mean_top10_score"] is None  # no setups: null, not 0
    assert get(ro_env, f"/api/v1/summary?date={PREV}")["strategies"][0]["ranked"] == 0


def test_market_series_follows_the_frozen_regime(ro_env: Env) -> None:
    body = get(ro_env, "/api/v1/market?days=60")
    assert "not NIFTY" in body["label"] and body["regime_rule"] == "breadth50"
    days = body["days"]
    assert len(days) == 60 and days[0]["index"] == pytest.approx(100.0)
    last = days[-1]
    assert last["day"] == AS_OF.isoformat() and last["breadth_pct"] == pytest.approx(100 * 20 / 63)
    assert last["regime_on"] is False  # 31.7 % < 40 %
    assert days[-10]["regime_on"] is True and days[-10]["breadth_pct"] == pytest.approx(
        100 * 45 / 63
    )
    assert last["index_ma50"] is not None and days[0]["index_ma50"] is not None
    assert body["regime_days_on_last_20"] == sum(d["regime_on"] for d in days[-20:]) == 18


def test_bars_have_averages_null_until_enough_history(ro_env: Env) -> None:
    bars = get(ro_env, "/api/v1/stocks/ALPHA/bars?days=260")["bars"]
    assert len(bars) == 260 and all(b["sma200"] is not None for b in bars)
    last = bars[-1]
    assert last["sma20"] == pytest.approx(sum(b["close"] for b in bars[-20:]) / 20)
    new = get(ro_env, "/api/v1/stocks/NEWCO/bars?days=260")
    assert len(new["bars"]) == 60 and new["company"] == "NEWCO Ltd" and new["adjusted"] is True
    b = new["bars"]
    assert b[-1]["sma50"] is not None and b[-1]["sma200"] is None  # 60 bars: no 200-day average
    assert b[0]["sma20"] is None and b[18]["sma20"] is None and b[19]["sma20"] is not None
    assert get(ro_env, f"/api/v1/stocks/{iid('ALPHA')}/bars?days=20")["symbol"] == "ALPHA"
    assert get(ro_env, "/api/v1/stocks/alpha/bars?days=20")["symbol"] == "ALPHA"
    get(ro_env, "/api/v1/stocks/NOPE/bars", 404)


def test_stock_setups_carry_marks_conditions_and_score_parts(ro_env: Env) -> None:
    body = get(ro_env, "/api/v1/stocks/ALPHA/setups")
    assert body["weekly_stage"] == "STAGE_2" and body["trend_template_pass"] is True
    assert [c["name"] for c in body["conditions"]] == ["close_above_sma150", "sma200_rising",
                                                       "rs_rank_min"]  # fmt: skip
    by = {s["strategy_id"]: s for s in body["setups"]}
    assert set(by) == {"vcp", "flat_base", "cup_handle"}
    vcp = by["vcp"]
    assert [c["sequence"] for c in vcp["contractions"]] == [1, 2]
    assert vcp["contractions"][0]["trough_price"] == 118.0 and vcp["points"] == []
    assert {p["component"] for p in vcp["score_parts"]} == {"TREND", "VCP"}
    assert vcp["details"]["base_high"] is not None and vcp["details"]["unmet_rules"] == {}
    cup = by["cup_handle"]
    assert [p["label"] for p in cup["points"]] == ["L", "B"] and cup["contractions"] == []
    bars = {b["day"]: b for b in get(ro_env, "/api/v1/stocks/ALPHA/bars?days=260")["bars"]}
    assert cup["points"][0]["price"] == bars["2026-08-03"]["high"]  # a lip is drawn on the high
    assert cup["points"][1]["price"] == bars["2026-08-20"]["low"]  # a bottom on the low
    assert body["close"] == bars[AS_OF.isoformat()]["close"]
    # a stock with one setup, and a date with none
    assert [s["strategy_id"] for s in get(ro_env, "/api/v1/stocks/BETA/setups")["setups"]] == [
        "vcp"
    ]
    empty = get(ro_env, f"/api/v1/stocks/ALPHA/setups?date={PREV}")
    assert empty["setups"] == [] and empty["conditions"] == [] and empty["weekly_stage"] is None


def test_activity_is_newest_first_with_every_source(ro_env: Env) -> None:
    events = get(ro_env, "/api/v1/activity?days=7")["events"]
    kinds = {e["kind"] for e in events}
    assert {"BREAKOUT", "PAPER_ENTRY", "PAPER_EXIT", "DAILY_RUN"} <= kinds
    brk = next(e for e in events if e["kind"] == "BREAKOUT")
    assert brk["symbol"] == "BETA" and "pivot 140.00" in brk["text"] and "1.8x" in brk["text"]
    ex = next(e for e in events if e["kind"] == "PAPER_EXIT")
    assert ex["symbol"] == "GAMMA" and "-5.15" in ex["text"]
    days = [e["day"] for e in events]
    assert days == sorted(days, reverse=True)
    run = next(e for e in events if e["kind"] == "DAILY_RUN" and e["day"] == AS_OF.isoformat())
    assert run["time"] is not None and "all steps OK" in run["text"]
    assert get(ro_env, "/api/v1/activity?days=1")["as_of"] == AS_OF.isoformat()


def test_paper_panel(ro_env: Env) -> None:
    body = get(ro_env, "/api/v1/paper")
    assert body["rule_set"] == "paper-v1" and body["review_from"] == "2027-04-01"
    assert body["open_positions"] == 1 and body["as_of"] == AS_OF.isoformat()
    vcp = next(s for s in body["strategies"] if s["strategy_id"] == "vcp")
    assert (vcp["closed"], vcp["win_rate_pct"], vcp["avg_return_pct"]) == (1, 0.0, -5.15)
    assert vcp["profit_factor"] == 0.0  # one losing trade, no gain
    assert vcp["ledger_through"] == AS_OF.isoformat()
    [pos] = vcp["open"]
    last = get(ro_env, "/api/v1/stocks/BETA/bars?days=20")["bars"][-1]["close"]
    assert (pos["symbol"], pos["entry"], pos["stop"], pos["last"]) == ("BETA", 100.0, 92.0, last)
    assert pos["open_pct"] == pytest.approx((last / 100.0 - 1) * 100)
    crit = {c["name"]: c for c in vcp["criteria"]}
    assert crit["Closed trades"]["value"] == 1.0 and crit["Closed trades"]["met"] is False
    assert crit["Profit factor"]["met"] is None  # fewer than 30 closed trades: not judged
    assert crit["Portfolio max drawdown"]["value"] is None  # not computed in v1: null
    idle = next(s for s in body["strategies"] if s["strategy_id"] == "flat_base")
    assert idle["closed"] == 0 and idle["win_rate_pct"] is None and idle["avg_return_pct"] is None
    assert idle["open"] == []


def test_status_is_clean_when_everything_is_current(ro_env: Env) -> None:
    body = get(ro_env, "/api/v1/status")
    assert body["prices_date"] == AS_OF.isoformat() and body["warnings"] == []
    assert body["daily_run"].startswith("2026-10-05 19:15 IST")
    assert body["newest_backup"] == "vcp_scanner_20261005_134501.duckdb"
    assert 0 < body["backup_age_days"] < 1
    assert all(s["latest_scan"] == AS_OF.isoformat() == s["ledger_through"]
               for s in body["strategies"])  # fmt: skip


def test_status_warnings(env: Env) -> None:
    # (a) no new prices for two sessions: Wed 7 Oct 21:00 is after Mon's data, Tue and Wed missing
    env.set_now(datetime(2026, 10, 7, 21, 0, tzinfo=IST))
    assert any("No new prices for 2 sessions" in w for w in get(env, "/api/v1/status")["warnings"])
    env.set_now(datetime(2026, 10, 6, 21, 0, tzinfo=IST))  # one missing session: no warning
    assert get(env, "/api/v1/status")["warnings"] == []
    # (d) backups older than 7 days
    env.set_now(datetime(2026, 10, 14, 14, 0, tzinfo=IST))
    assert any("older than 7 days" in w for w in get(env, "/api/v1/status")["warnings"])
    # (b) a strategy without a scan and (c) a paper ledger that is behind
    env.set_now(datetime(2026, 10, 6, 14, 0, tzinfo=IST))
    with duckdb.connect(str(env.db)) as con:
        con.execute("DELETE FROM setup_scores WHERE strategy_id = 'double_bottom'")
        con.execute("DELETE FROM paper_events WHERE strategy_id = 'cup_handle'"
                    " AND event_type = 'DAY_CLOSED' AND event_date = ?", [AS_OF])  # fmt: skip
    refresh_serving_copy(env.db)
    warnings = get(env, "/api/v1/status")["warnings"]
    assert any(w.startswith("double_bottom: no scan for 2026-10-05") for w in warnings)
    assert any(w.startswith("cup_handle: paper ledger not updated for 2026-10-05")
               for w in warnings)  # fmt: skip


def test_no_backup_is_a_warning(env: Env) -> None:
    for f in (env.data_dir / "backups").iterdir():
        f.unlink()
    assert "No database backup found." in get(env, "/api/v1/status")["warnings"]


def test_a_missing_serving_copy_is_a_503_with_the_remedy(env: Env) -> None:
    (env.data_dir / "serving" / "vcp_serving.duckdb").unlink()
    r = env.client.get("/api/v1/status")
    assert r.status_code == 503 and "vcp run daily --serving-copy" in r.json()["detail"]


def test_a_replaced_serving_copy_is_picked_up_without_restart(env: Env) -> None:
    assert len(get(env, "/api/v1/market?days=60")["days"]) == 60  # fills the cache
    before = get(env, "/api/v1/summary")["strategies"][0]["ranked"]
    with duckdb.connect(str(env.db)) as con:
        con.execute("UPDATE setup_scores SET eligible = TRUE WHERE strategy_id = 'vcp'")
    refresh_serving_copy(env.db)
    assert get(env, "/api/v1/summary")["strategies"][0]["ranked"] == before + 1
    assert len(get(env, "/api/v1/market?days=60")["days"]) == 60
