"""Generating a past report (FRONTEND_SPECIFICATION 67.27) and the weekly rule
(STRATEGY_SPECIFICATION 21.10): the weekly summary follows the last trading day of the week."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from tests.api.conftest import Env
from vcp_scanner.reporting.build import week_is_over, write_reports
from vcp_scanner.serving import default_serving_path

URL = "/api/v1/reports/generate"
THU, FRI, MON = date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 5)


def test_a_past_daily_report_is_written_and_says_it_was_rebuilt(env: Env) -> None:
    r = env.client.post(URL, json={"kind": "daily", "date": "2026-10-01"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "2026-10-01.html" and r.json()["kind"] == "daily"
    text = (env.data_dir / "reports" / "daily" / "2026-10-01.html").read_text(encoding="utf-8")
    assert "Daily report 2026-10-01" in text and "Rebuilt on" in text
    names = [f["name"] for f in env.client.get("/api/v1/reports").json()["files"]]
    assert "2026-10-01.html" in names


def test_the_latest_day_has_no_rebuilt_note(env: Env) -> None:
    assert env.client.post(URL, json={"kind": "daily", "date": "2026-10-05"}).status_code == 200
    text = (env.data_dir / "reports" / "daily" / "2026-10-05.html").read_text(encoding="utf-8")
    assert "Rebuilt on" not in text


def test_a_weekly_report_for_any_day_uses_the_weeks_last_session(env: Env) -> None:
    # Friday 2 Oct is a holiday: the week's last session is Thursday 1 Oct, ISO week 2026-W40
    r = env.client.post(URL, json={"kind": "weekly", "date": "2026-10-02"})
    assert r.status_code == 200 and r.json()["name"] == "2026-W40.html"


def test_days_that_cannot_have_a_report_are_refused_with_a_reason(env: Env) -> None:
    holiday = env.client.post(URL, json={"kind": "daily", "date": "2026-10-02"})
    assert holiday.status_code == 400 and "not a trading session" in holiday.json()["detail"]
    future = env.client.post(URL, json={"kind": "daily", "date": "2026-10-06"})
    assert future.status_code == 400 and "after the latest prices" in future.json()["detail"]
    assert env.client.post(URL, json={"kind": "weekly", "date": "2026-10-12"}).status_code == 400


def test_only_a_json_body_with_a_known_kind_is_accepted(env: Env) -> None:
    bad_kind = env.client.post(URL, json={"kind": "../x", "date": "2026-10-01"})
    assert bad_kind.status_code == 422
    plain = env.client.post(
        URL, content="kind=daily&date=2026-10-01", headers={"content-type": "text/plain"}
    )
    assert plain.status_code == 422
    assert not (env.data_dir / "reports" / "daily" / "2026-10-01.html").exists()


def test_the_week_is_over_on_friday_or_when_only_holidays_follow() -> None:
    thu, wed = date(2026, 10, 8), date(2026, 10, 7)
    assert week_is_over(date(2026, 10, 9), None)  # Friday
    assert not week_is_over(thu, None)  # without a holiday list only Friday counts
    assert week_is_over(thu, {date(2026, 10, 9)})  # Friday is a holiday
    assert not week_is_over(thu, {date(2026, 10, 12)})
    assert week_is_over(wed, {thu, date(2026, 10, 9)})
    assert not week_is_over(wed, {date(2026, 10, 9)})  # Thursday is a session


def test_the_run_catches_up_a_missing_weekly_summary_once(env: Env, tmp_path: Path) -> None:
    serving = Path(default_serving_path(str(env.db)))
    first = write_reports(serving, "config", env.data_dir, tmp_path)
    assert [p.name for p in first] == [
        "2026-10-05.html",
        "2026-W40.html",
    ]  # Monday: W40 was missing
    second = write_reports(serving, "config", env.data_dir, tmp_path)
    assert [p.name for p in second] == ["2026-10-05.html"]  # now it exists
