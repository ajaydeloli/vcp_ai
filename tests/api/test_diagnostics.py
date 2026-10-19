"""Read-only diagnostics endpoints (AGENT_SPECIFICATION A1.5): data-quality events, one stock's
corporate actions and gaps, raw against adjusted prices, scan runs with the evening-run log, and
one stored backtest's trades."""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

import duckdb

from tests.api.conftest import Env
from tests.api.fixture import AS_OF, PREV, T0, iid, put
from vcp_scanner.serving import refresh_serving_copy


def get(env: Env, path: str, status: int = 200) -> Any:
    r = env.client.get(path)
    assert r.status_code == status, r.text
    return r.json()


def _event(c: Any, eid: str, sym: str, kind: str, sev: str, blocks: bool, status: str,
           day: date) -> None:  # fmt: skip
    put(c, "data_quality_events", event_id=eid, instrument_id=iid(sym), trade_date=day,
        dataset="daily_prices", severity=sev, blocks_signal=blocks, event_type=kind,
        description=f"{kind} on {day}", detected_at=T0, status=status,
        context=json.dumps({"ex_date": day.isoformat()}))  # fmt: skip


def _seed(env: Env) -> None:
    with duckdb.connect(str(env.db)) as c:
        _event(c, "dq-1", "ALPHA", "UNEXPLAINED_GAP", "HIGH", True, "OPEN", AS_OF)
        _event(c, "dq-2", "BETA", "CORPORATE_ACTION_UNMODELLED", "WARNING", False, "OPEN", PREV)
        _event(c, "dq-3", "ALPHA", "CORPORATE_ACTION_UNRESOLVED", "CRITICAL", True, "RESOLVED",
               date(2026, 8, 21))  # fmt: skip
        put(c, "corporate_action_resolution", resolution_id="r1", instrument_id=iid("ALPHA"),
            action_type="BONUS", ex_date=date(2026, 8, 21), ratio_numerator=1.0,
            ratio_denominator=2.0, status="CONFIRMED", known_from=T0)  # fmt: skip
        put(c, "corporate_action_adjustments", resolution_id="r1", instrument_id=iid("ALPHA"),
            effective_date=date(2026, 8, 21), price_factor=0.6667, volume_factor=1.5,
            cumulative_price_factor=0.6667, cumulative_volume_factor=1.5, source="INTERNAL",
            calculation_version="1.2", known_from=T0)  # fmt: skip
        # raw prices: a 1:2 bonus gap on 21 Aug (adjusted) and an unexplained -20 % gap on AS_OF
        rows = [(date(2026, 8, 20), 300.0), (date(2026, 8, 21), 200.0), (PREV, 210.0),
                (AS_OF, 168.0)]  # fmt: skip
        for day, px in rows:
            put(c, "daily_prices", instrument_id=iid("ALPHA"), trade_date=day, open_raw=px,
                high_raw=px, low_raw=px, close_raw=px, volume_raw=1000, primary_provider="NSE",
                data_status="OK", source_run_id="t", source_hash=day.isoformat(),
                known_from=T0)  # fmt: skip
    refresh_serving_copy(env.db)


def test_data_quality_lists_open_events_with_counts_and_filters(env: Env) -> None:
    _seed(env)
    body = get(env, "/api/v1/data-quality")
    assert body["total"] == 2 and [e["event_id"] for e in body["events"]] == ["dq-1", "dq-2"]
    counts = {(c["event_type"], c["severity"]): (c["open"], c["blocking"])
              for c in body["open_counts"]}  # fmt: skip
    assert counts[("UNEXPLAINED_GAP", "HIGH")] == (1, 1)
    assert body["events"][0]["symbol"] == "ALPHA" and body["events"][0]["context"]["ex_date"]
    assert get(env, "/api/v1/data-quality?status=all&symbol=alpha")["total"] == 2
    one = get(env, "/api/v1/data-quality?status=resolved")["events"]
    assert [e["event_id"] for e in one] == ["dq-3"]
    assert get(env, "/api/v1/data-quality?event_type=unexplained_gap")["total"] == 1
    get(env, "/api/v1/data-quality?status=maybe", 422)
    get(env, "/api/v1/data-quality?symbol=NOPE", 404)


def test_corporate_actions_show_records_factors_events_and_gaps(env: Env) -> None:
    _seed(env)
    body = get(env, "/api/v1/stocks/alpha/corporate-actions")
    assert body["symbol"] == "ALPHA" and body["gap_threshold_pct"] == 10.0
    assert [(a["action_type"], a["status"]) for a in body["actions"]] == [("BONUS", "CONFIRMED")]
    assert body["adjustments"][0]["price_factor"] == 0.6667
    assert {e["event_id"] for e in body["events"]} == {"dq-1", "dq-3"}
    gaps = {g["day"]: (g["gap_pct"], g["adjusted"]) for g in body["gaps"]}
    assert gaps == {AS_OF.isoformat(): (-20.0, False), "2026-08-21": (-33.33, True)}


def test_prices_put_raw_next_to_adjusted(env: Env) -> None:
    _seed(env)
    rows = get(env, "/api/v1/stocks/ALPHA/prices?days=2")["rows"]
    assert [r["day"] for r in rows] == [PREV.isoformat(), AS_OF.isoformat()]  # oldest first
    assert rows[-1]["close"] == 168.0 and rows[-1]["provider"] == "NSE"
    assert rows[-1]["close_adj"] is not None  # from the fixture's adjusted bars
    get(env, "/api/v1/stocks/ALPHA/prices?days=0", 422)


def test_scan_runs_with_the_evening_run_log(env: Env) -> None:
    logs = env.data_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "daily_runs.log").write_text(
        f"{PREV} 19:15 IST | prices to {PREV} | all steps OK\n"
        f"{AS_OF} 19:15 IST | prices to {AS_OF} | scanned {AS_OF} | all steps OK\n"
    )
    (logs / f"daily_run_{AS_OF}.out").write_text(
        "[19:15:01] INFO  starting\n"
        "[19:15:02] WARNING [nse] Failed to initialize NSE session: 403\n"
        "[19:16:02] WARNING [nse] Failed to initialize NSE session: 403\n"
        "Warning: 1 price-affecting NSE record(s) this scanner does not model\n"
    )
    body = get(env, "/api/v1/scan-runs")
    assert body["as_of"] == AS_OF.isoformat() and body["run_summary"].startswith(str(AS_OF))
    assert body["log_warnings"] == [
        "WARNING [nse] Failed to initialize NSE session: 403",  # once, timestamp dropped
        "Warning: 1 price-affecting NSE record(s) this scanner does not model",
    ]
    assert body["runs"] and {r["status"] for r in body["runs"]} == {"COMPLETED"}
    empty = get(env, f"/api/v1/scan-runs?date={AS_OF + timedelta(days=30)}")
    assert empty["runs"] == [] and empty["run_summary"] is None and empty["log_warnings"] == []


def test_backtest_detail_pairs_entries_with_exits(env: Env) -> None:
    research = env.data_dir / "golden_src.duckdb"
    events = [
        ("ALPHA", date(2023, 1, 2), "BREAKOUT", 100.0, {}),
        ("ALPHA", date(2023, 1, 9), "STOP", 92.0, {"ret_pct": -8.0}),
        ("BETA", date(2024, 3, 1), "BREAKOUT", 50.0, {}),
        ("BETA", date(2024, 5, 1), "TIME_EXIT", 60.0, {"ret_pct": 20.0}),
        ("GAMMA", date(2024, 6, 3), "BREAKOUT", 10.0, {}),
        ("GAMMA", date(2024, 6, 28), "OPEN_AT_END", None, {"entry_day": "2024-06-03"}),
        ("GAMMA", date(2024, 6, 1), "ENTRY_SIGNAL", None, {}),  # not a trade event
    ]
    with duckdb.connect(str(research)) as c:
        for sym, day, kind, px, meta in events:
            put(c, "backtest_events", backtest_id="bt-dev", instrument_id=iid(sym),
                event_date=day, event_type=kind, price=px,
                metadata_json=json.dumps(meta))  # fmt: skip
    body = get(env, "/api/v1/backtests/bt-dev?trades=2")
    assert body["run"]["backtest_id"] == "bt-dev" and body["trades_total"] == 3
    assert [(t["symbol"], t["exit"]) for t in body["trades"]] == [
        ("GAMMA", "OPEN_AT_END"), ("BETA", "TIME_EXIT")]  # fmt: skip
    assert body["trades"][0]["exit_day"] is None
    exits = {g["key"]: (g["trades"], g["avg_ret"], g["win_rate"]) for g in body["by_exit"]}
    assert exits == {"STOP": (1, -8.0, 0.0), "TIME_EXIT": (1, 20.0, 1.0),
                     "OPEN_AT_END": (1, None, None)}  # fmt: skip
    assert [(g["key"], g["trades"]) for g in body["by_year"]] == [("2023", 1), ("2024", 2)]
    get(env, "/api/v1/backtests/nope", 404)
