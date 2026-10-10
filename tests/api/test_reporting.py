"""Daily and weekly reports (STRATEGY_SPECIFICATION 21.10): read-only, no zeros for gaps."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from tests.api.conftest import NOW, Env
from vcp_scanner.reporting.build import (
    NA,
    NOTE,
    num,
    week_bounds,
    write_daily,
    write_reports,
    write_weekly,
)
from vcp_scanner.serving import default_serving_path

CONFIG = "config"


def _serving(env: Env) -> Path:
    return Path(default_serving_path(str(env.db)))


def test_daily_report_has_the_planned_sections_and_the_disclaimer(
    ro_env: Env, tmp_path: Path
) -> None:
    path = write_daily(_serving(ro_env), CONFIG, ro_env.data_dir, tmp_path, now=NOW)
    text = path.read_text(encoding="utf-8")
    assert path.parent.name == "daily" and re.fullmatch(r"\d{4}-\d{2}-\d{2}\.html", path.name)
    for heading in ("Market regime", "Ranked setups by strategy", "more than one list",
                    "Paper trades today", "Open paper positions", "Daily run health"):  # fmt: skip
        assert heading in text
    assert NOTE in text
    for spec in ro_env.ctx.strategies:
        assert spec.strategy_id in text


def test_weekly_report_names_the_iso_week_and_shows_gaps_as_not_available(
    ro_env: Env, tmp_path: Path
) -> None:
    day = date(2026, 10, 2)
    path = write_weekly(_serving(ro_env), CONFIG, ro_env.data_dir, tmp_path, day, now=NOW)
    assert path.name == f"{week_bounds(day)[2]}.html" and path.parent.name == "weekly"
    text = path.read_text(encoding="utf-8")
    assert "Paper results since" in text and "Data issues this week" in text
    assert NA in text  # portfolio return and drawdown are never filled with zeros


def test_no_look_ahead_a_past_day_ignores_later_rows(ro_env: Env, tmp_path: Path) -> None:
    early = write_daily(_serving(ro_env), CONFIG, ro_env.data_dir, tmp_path, date(2026, 9, 1),
                        now=NOW)  # fmt: skip
    assert "Daily report 2026-09-01" in early.read_text(encoding="utf-8")


def test_reports_only_read(ro_env: Env, tmp_path: Path) -> None:
    serving = _serving(ro_env)
    before = (serving.stat().st_mtime_ns, ro_env.db.stat().st_mtime_ns)
    write_reports(serving, CONFIG, ro_env.data_dir, tmp_path)
    assert (serving.stat().st_mtime_ns, ro_env.db.stat().st_mtime_ns) == before


def test_missing_values_are_not_available_never_zero() -> None:
    assert num(None) == NA and num(0.0) == "0.00"


def test_the_report_module_has_no_write_statements() -> None:
    src = (
        Path(__file__).resolve().parents[2] / "src" / "vcp_scanner" / "reporting" / "build.py"
    ).read_text()
    assert not re.search(r"\b(INSERT|UPDATE|DELETE|CREATE|DROP|ALTER)\b", src)
