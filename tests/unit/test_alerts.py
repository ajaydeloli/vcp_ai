"""Telegram alerts: config, the channel (no token leaks, one retry), once-a-day sending, the
evening summary and the intraday pivot alerts."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from vcp_scanner.alerts.messages import Watched, evening_message, pivot_alerts, watch_list
from vcp_scanner.alerts.telegram import (
    AlertLog,
    AlertsConfig,
    EveningConfig,
    IntradayConfig,
    TelegramChannel,
    load_alerts_config,
    send_once,
)

DAY = date(2026, 10, 21)


def test_the_shipped_config_loads_and_a_missing_file_turns_alerts_off(tmp_path: Path) -> None:
    cfg = load_alerts_config("config")
    assert cfg.enabled and cfg.intraday.poll_seconds >= 20 and cfg.evening.near_pivot_pct > 0
    assert load_alerts_config(tmp_path).enabled is False
    with pytest.raises(ValueError):
        AlertsConfig.model_validate({"intraday": {"poll_seconds": 5}})


def test_the_channel_sends_retries_once_and_never_shows_the_token() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def post(url: str, data: dict[str, Any]) -> Any:
        calls.append((url, data))
        return MagicMock(status_code=200)

    ok, err = TelegramChannel("SECRET:TOKEN", "42", post).send("hello")
    assert ok and err is None and calls[0][1] == {"chat_id": "42", "text": "hello",
                                                  "disable_web_page_preview": True}  # fmt: skip
    bad = TelegramChannel("SECRET:TOKEN", "42", lambda u, d: MagicMock(status_code=401))
    ok, err = bad.send("x")
    assert not ok and err == "Telegram HTTP 401" and "SECRET" not in err


def test_an_alert_goes_out_once_a_day_and_failures_are_recorded(tmp_path: Path) -> None:
    log = AlertLog(tmp_path / "alerts.duckdb")
    sent: list[str] = []
    ch = TelegramChannel(
        "t", "c", lambda u, d: sent.append(d["text"]) or MagicMock(status_code=200)
    )
    assert send_once(ch, log, DAY, "CROSSED", "ABC", "ABC crossed")
    assert not send_once(ch, log, DAY, "CROSSED", "ABC", "ABC crossed again")
    assert send_once(ch, log, DAY, "APPROACHING", "ABC", "near")  # another kind
    assert not send_once(None, log, DAY, "EVENING", None, "summary")  # not configured
    assert send_once(ch, log, DAY, "EVENING", None, "summary")  # a failed one does not block
    assert len(sent) == 3
    rows = log.recent()
    assert any(r[1] == "EVENING" and r[3] is False and "not configured" in r[4] for r in rows)


def _get(responses: dict[str, Any]) -> Any:
    def get(path: str, **params: Any) -> Any:
        key = f"{path}?strategy={params['strategy']}" if "strategy" in params else path
        return responses.get(key, {"rows": []})

    return get


API = {
    "status": {
        "prices_date": "2026-10-21",
        "daily_run": "2026-10-21 19:15 IST | prices to "
        "2026-10-21 | scanned 2026-10-21 | all steps OK",
        "warnings": [],
    },  # fmt: skip
    "market/health": {"score": {"label": "Adverse", "raw": 22.7, "exposure": "none"}},
    "activity": {
        "events": [
            {
                "day": "2026-10-21",
                "kind": "BREAKOUT",
                "strategy_id": "vcp",
                "symbol": "KROSS",
                "text": "Breakout above pivot 283.00, volume 13.7x average",
            },
            {
                "day": "2026-10-20",
                "kind": "BREAKOUT",
                "strategy_id": "vcp",
                "symbol": "OLD",
                "text": "yesterday",
            },
        ]
    },  # fmt: skip
    "setups?strategy=vcp": {
        "rows": [
            {
                "symbol": "NEAR",
                "pivot": 100.0,
                "pivot_distance_pct": 0.5,
                "status": "PIVOT_READY",
                "score": 61.2,
            },
            {
                "symbol": "FAR",
                "pivot": 50.0,
                "pivot_distance_pct": 8.0,
                "status": "FORMING",
                "score": 70.0,
            },
            {
                "symbol": "DONE",
                "pivot": 10.0,
                "pivot_distance_pct": -3.0,
                "status": "BREAKOUT",
                "score": 65.0,
            },
        ]
    },  # fmt: skip
}


def test_the_evening_message_says_run_market_breakouts_and_near_pivot() -> None:
    text = evening_message(_get(API), EveningConfig(strategies=["vcp"]))
    assert text.startswith("VCP scanner - evening 2026-10-21")
    assert "Run OK" in text and "Market health: Adverse 22.7 (exposure none)" in text
    assert "KROSS [VCP]" in text and "OLD" not in text  # only today's breakouts
    assert "NEAR [VCP] 0.5% below pivot 100.00, score 61" in text and "FAR" not in text
    assert "Research tool" not in text
    failed = {**API, "status": {**API["status"], "daily_run": "... | FAILED: prices"}}
    assert "RUN PROBLEM" in evening_message(_get(failed), EveningConfig(strategies=["vcp"]))


def test_the_watch_list_takes_near_setups_and_the_ai_watchlist() -> None:
    api = {**API, "stocks/MINE/setups": {"setups": [
        {"strategy_id": "flat_base", "pivot": 200.0, "score": 50},
        {"strategy_id": "vcp", "pivot": 210.0, "score": 60}]}}  # fmt: skip
    got = watch_list(_get(api), IntradayConfig(near_pivot_pct=3.0), ["mine", "NOSETUP"])
    assert [(w.symbol, w.pivot, w.why) for w in got] == [("MINE", 210.0, "AI watchlist"),
                                                        ("NEAR", 100.0, "setup")]  # fmt: skip


def test_pivot_alerts_approaching_crossed_and_the_chase_warning() -> None:
    w = [Watched("A", 100.0, "vcp", "setup"), Watched("B", 100.0, "vcp", "setup"),
         Watched("C", 100.0, "vcp", "setup"), Watched("D", 100.0, "vcp", "setup")]  # fmt: skip
    got = pivot_alerts(w, {"A": 99.5, "B": 101.0, "C": 107.0, "D": 97.0},
                       IntradayConfig(approach_pct=1.0, chase_warn_pct=5.0))  # fmt: skip
    kinds = {sym: (kind, text) for kind, sym, text in got}
    assert kinds["A"][0] == "APPROACHING" and "0.5% below" in kinds["A"][1]
    assert kinds["B"][0] == "CROSSED" and "+1.0%" in kinds["B"][1]
    assert "past the 5% entry limit" in kinds["C"][1] and "D" not in kinds
    assert "Research tool" not in kinds["B"][1]


def test_pivot_alerts_carry_the_setup_risk_volume_and_market_details() -> None:
    from datetime import datetime, timedelta, timezone

    w = [Watched("A", 100.0, "vcp", "setup", score=71.6, grade="A", stop=94.0, rs_rank=85,
                 sector="Chemicals", avg_volume=100_000)]  # fmt: skip
    live = {"A": {"last_price": 101.0, "change_pct": 2.04, "low": 98.0, "high": 101.5,
                  "volume": 60_000}}  # fmt: skip
    now = datetime(
        2026, 10, 21, 11, 15, tzinfo=timezone(timedelta(hours=5, minutes=30))
    )  # 120 of 375 minutes into the session
    (kind, _, text), = pivot_alerts(w, {"A": 101.0}, IntradayConfig(chase_warn_pct=5.0), live,
                                    "Market health: Adverse 22.7, exposure none", now)  # fmt: skip
    assert kind == "CROSSED"
    assert "VCP | grade A | score 72 | RS 85 | Chemicals (setup)" in text
    assert "Price 101.00, day +2.0% (range 98.00 - 101.50)" in text
    assert "Buy zone 100.00 - 105.00 (pivot +5%)" in text
    assert "Stop 94.00: risk 6.9% from this price" in text
    assert "Volume so far 60,000 = 60% of the 50-day avg (pace 1.9x a normal day)" in text
    assert "Market health: Adverse 22.7" in text and "11:15 IST" in text
    assert text.endswith("Chart: https://www.tradingview.com/chart/?symbol=NSE%3AA")


def test_the_tradingview_link_maps_special_symbols() -> None:
    from vcp_scanner.alerts.messages import tradingview_url

    assert tradingview_url("M&M").endswith("NSE%3AM_M")
    assert tradingview_url("BAJAJ-AUTO").endswith("NSE%3ABAJAJ_AUTO")


def test_the_watcher_state_file_has_prices_and_gaps(tmp_path: Path) -> None:
    import json

    from vcp_scanner.cli_alerts import write_watch_state

    path = tmp_path / "alerts" / "watch_state.json"
    write_watch_state(path, "open", [Watched("A", 100.0, "vcp", "setup"),
                                     Watched("B", 50.0, "vcp", "AI watchlist")],
                      {"A": 101.5}, None)  # fmt: skip
    body = json.loads(path.read_text())
    assert body["market"] == "open" and body["updated_at"].endswith("+05:30")
    assert [(w["symbol"], w["gap_pct"]) for w in body["watching"]] == [("A", 1.5), ("B", None)]
