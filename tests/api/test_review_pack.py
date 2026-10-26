"""Agent step A5: the April 2027 review pack. The criteria of STRATEGY_SPECIFICATION 21.6 on
known numbers, the replay's portfolio and index, the ledger-against-replay check (21.3), and the
pack on the API fixture (a hand-made ledger the replay does not reproduce)."""

from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from vcp_scanner.reporting import review as R

from .conftest import Env


def test_fewer_than_30_trades_is_not_judged_and_says_until_when() -> None:
    crit = R.criteria(12, 2.0, 1.5, -5.0, 10.0, 3.0, 8)
    assert [c["met"] for c in crit] == [False, None, None, None, None]
    assert R.verdict(crit, date(2026, 10, 1)) == (
        "not judged: 12 of 30 closed trades; paper continues up to 2027-10-01"
    )


def test_a_strategy_works_only_when_every_criterion_is_met() -> None:
    ok = R.criteria(31, 1.6, 0.8, -12.0, 14.0, 6.0, 17)
    assert all(c["met"] for c in ok)
    assert R.verdict(ok, date(2026, 10, 1)) == "working: meets every criterion"
    deep = R.criteria(31, 1.6, 0.8, -21.0, 14.0, 6.0, 17)
    assert R.verdict(deep, date(2026, 10, 1)) == "not working: Portfolio max drawdown"
    lag = R.criteria(40, 1.2, 0.3, -8.0, 4.0, 6.0, 20)
    assert R.verdict(lag, date(2026, 10, 1)) == (
        "not working: Profit factor; Portfolio return above the equal-weight universe index"
    )
    # No losing trade at all: the profit factor is infinite, the criterion is met.
    assert R.criteria(30, None, 2.0, -3.0, 9.0, 1.0, 30)[1]["met"] is True
    assert R.criteria(30, 1.5, 0.5, -3.0, None, 1.0, 20)[4]["met"] is None  # a number missing


def test_index_return_compounds_the_sessions_after_the_start() -> None:
    rows = [SimpleNamespace(day=date(2026, 10, d), mean_return=r)
            for d, r in [(1, 0.5), (2, 0.10), (5, -0.05), (6, None), (7, 0.02)]]  # fmt: skip
    got = R.index_return(rows, date(2026, 10, 1), date(2026, 10, 6))  # the start day is out
    assert got == pytest.approx((1.10 * 0.95 - 1) * 100)
    assert R.index_return(rows, date(2026, 10, 7), date(2026, 10, 7)) is None


def test_equity_is_flat_before_the_first_entry_and_carried_over_gaps() -> None:
    days = [date(2026, 10, d) for d in (1, 2, 5, 6, 7)]
    curve = [(date(2026, 10, 5), 1.0), (date(2026, 10, 7), 0.9)]
    got = R._equity_from(date(2026, 10, 1), date(2026, 10, 7), curve, days)
    assert [v for _d, v in got] == [1.0, 1.0, 1.0, 1.0, 0.9]


def _pack(env: Env, day: date) -> dict[str, Any]:
    return R.build_pack(env.data_dir / "serving" / "vcp_serving.duckdb", "config",
                        env.data_dir / "agent" / "agent.duckdb", day)  # fmt: skip


def test_the_pack_on_the_fixture_reports_the_ledger_and_its_disagreement(ro_env: Env) -> None:
    pack = _pack(ro_env, date(2026, 10, 6))
    assert pack["state"].startswith("preview") and pack["total_closed"] == 1
    by = {s["strategy_id"]: s for s in pack["strategies"]}
    assert list(by) == ["vcp", "flat_base", "three_weeks_tight", "cup_handle", "double_bottom"]
    vcp = by["vcp"]
    assert vcp["through"] == "2026-10-05" and vcp["sessions"] == 2
    assert (vcp["ledger"]["closed"], vcp["ledger"]["avg_ret"], vcp["ledger"]["open"]) == (
        1, -5.15, 1)  # fmt: skip
    assert vcp["verdict"].startswith("not judged: 1 of 30")
    # The fixture's trades were written by hand: the replay of the frozen rules does not make
    # them, and the pack says which events differ (21.3).
    assert vcp["replay"]["agrees"] is False
    assert any("GAMMA" in x and x.startswith("EXIT")
               for x in vcp["replay"]["differences"]["stored_not_recomputed"])  # fmt: skip
    assert by["three_weeks_tight"]["replay"]["agrees"] is True
    assert vcp["replay"]["index_ret"] == pytest.approx(by["flat_base"]["replay"]["index_ret"])
    # Research of the cycle, every hypothesis listed; the candidate that never started is not
    # in the shadow results, only in the list.
    assert [h["outcome"] for h in pack["research"]["hypotheses"]] == ["rejected"]
    assert pack["candidates"] == [] and pack["candidate_list"][0]["status"] == "rejected"
    assert "display only" in pack["f7"]
    assert pack["decisions"][0] == ("vcp: not judged: 1 of 30 closed trades; paper continues "
                                    "up to 2027-10-01 (nothing to decide yet).")  # fmt: skip


def test_on_the_review_date_too_few_trades_postpone_the_review(ro_env: Env) -> None:
    pack = _pack(ro_env, date(2027, 4, 2))
    assert pack["state"].startswith("due, but only 1 closed paper trades in total")


def test_write_review_makes_the_html_and_the_json(ro_env: Env, tmp_path: Path) -> None:
    path = R.write_review(ro_env.data_dir / "serving" / "vcp_serving.duckdb", "config",
                          ro_env.data_dir / "agent" / "agent.duckdb", tmp_path,
                          date(2026, 10, 6))  # fmt: skip
    assert path == tmp_path / "review" / "2026-10-06.html"
    html = path.read_text()
    assert "Review pack (preview) 2026-10-06" in html and "Verdict per strategy" in html
    assert "differs from the replay" in html and "agrees with a replay" in html
    assert "Fundamentals (F7)" in html and "Decisions for the owner" in html
    data = json.loads(path.with_suffix(".json").read_text())
    assert data["version"] == "review-pack-1" and len(data["strategies"]) == 5


def test_the_weekly_summary_brings_the_pack_from_the_review_date(
    ro_env: Env, tmp_path: Path
) -> None:
    from vcp_scanner.reporting.build import _review_pack

    serving = ro_env.data_dir / "serving" / "vcp_serving.duckdb"
    assert _review_pack(serving, "config", ro_env.data_dir, tmp_path, date(2027, 3, 26)) == []
    [path] = _review_pack(serving, "config", ro_env.data_dir, tmp_path, date(2027, 4, 2))
    assert path.name == "2027-04-02.html"
    assert "Review pack (postponed) 2027-04-02" in path.read_text()  # 1 trade of 30


def test_the_agent_tool_returns_the_pack_without_the_equity_curves(
    ro_env: Env, tmp_path: Path
) -> None:
    from vcp_scanner.agent.server import build_server

    server = build_server(data_dir=str(ro_env.data_dir), agent_db=ro_env.data_dir / "agent" /
                          "agent.duckdb", repo_root=Path.cwd(), client=ro_env.client,
                          launcher=lambda pid: None)  # fmt: skip
    res = asyncio.run(server.call_tool("review_pack", {}))
    content = res[0] if isinstance(res, tuple) else res
    pack = json.loads(content[0].text)
    vcp = next(s for s in pack["strategies"] if s["strategy_id"] == "vcp")
    assert "equity" not in vcp["replay"] and vcp["ledger"]["closed"] == 1
