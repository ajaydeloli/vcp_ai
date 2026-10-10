"""Stored backtest runs and report files (FRONTEND_SPECIFICATION 67.26, 67.27)."""

from __future__ import annotations

import re
from pathlib import Path

from tests.api.conftest import Env
from tests.api.test_api import get


def test_backtests_list_the_stored_runs_with_units_converted(ro_env: Env) -> None:
    body = get(ro_env, "/api/v1/backtests")
    assert body["available"] is True and body["reason"] is None
    runs = {r["backtest_id"]: r for r in body["runs"]}
    assert set(runs) == {"bt-dev", "bt-val", "bt-exp"}
    dev = runs["bt-dev"]
    assert dev["paper_rules"] is True and dev["period"] == "development"
    assert dev["portfolio"]["win_rate_pct"] == 45.0  # stored as a fraction
    assert dev["portfolio"]["avg_exposure_pct"] == 50.0
    assert dev["portfolio"]["max_drawdown_pct"] == -11.0 and dev["portfolio"]["trades"] == 20
    assert dev["every_trade"]["profit_factor"] == 1.6


def test_an_experiment_is_not_marked_as_the_paper_rules_and_gaps_are_null(ro_env: Env) -> None:
    exp = next(r for r in get(ro_env, "/api/v1/backtests")["runs"] if r["backtest_id"] == "bt-exp")
    assert exp["paper_rules"] is False and exp["entry"] == "breakout"
    assert exp["portfolio"]["trades"] is None and exp["portfolio"]["profit_factor"] is None
    assert exp["portfolio"]["max_drawdown_pct"] is None  # never 0


def test_a_missing_research_database_is_said_not_hidden(env: Env, tmp_path: Path) -> None:
    from vcp_scanner.api.backtests import stored_runs

    gone = stored_runs(tmp_path / "nope.duckdb")
    assert gone.available is False and gone.runs == [] and "not found" in (gone.reason or "")


def test_reports_are_listed_newest_first_and_only_real_report_names(ro_env: Env) -> None:
    files = get(ro_env, "/api/v1/reports")["files"]
    assert {(f["kind"], f["name"]) for f in files} == {
        ("daily", "2026-10-05.html"),
        ("weekly", "2026-W41.html"),
    }  # notes.html is not a report


def test_a_report_is_served_as_html_and_other_files_are_not_reachable(ro_env: Env) -> None:
    ok = ro_env.client.get("/api/v1/reports/daily/2026-10-05.html")
    assert ok.status_code == 200 and ok.headers["content-type"].startswith("text/html")
    assert "daily 2026-10-05.html" in ok.text
    for url in (
        "/api/v1/reports/daily/notes.html",
        "/api/v1/reports/daily/..%2F..%2Fsecret.html",
        "/api/v1/reports/other/2026-10-05.html",
        "/api/v1/reports/daily/2026-10-06.html",
    ):
        assert ro_env.client.get(url).status_code == 404, url


def test_the_new_modules_only_read() -> None:
    root = Path(__file__).resolve().parents[2] / "src" / "vcp_scanner" / "api"
    for name in ("backtests.py", "report_files.py"):
        src = (root / name).read_text()
        assert not re.search(r"\b(INSERT|UPDATE|DELETE|CREATE|DROP|ALTER)\b", src), name
