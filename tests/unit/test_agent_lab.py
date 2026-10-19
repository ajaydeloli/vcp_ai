"""Agent step A2: the research lab's rules (pre-registration, windows, budget), the study maths
and the backtest settings check (AGENT_SPECIFICATION §4)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from vcp_scanner.agent import backtest_run as br
from vcp_scanner.agent import study as st
from vcp_scanner.agent.lab import MAX_PER_STRATEGY, Lab, LabRuleError
from vcp_scanner.agent.store import AgentStore

DEV = date(2023, 1, 2)


@pytest.fixture
def lab(tmp_path: Path) -> Lab:
    return Lab(AgentStore(tmp_path / "agent.duckdb"))


def _register(lab: Lab, strategy: str = "vcp") -> int:
    return lab.register(strategy, "Failed breakouts in weak breadth",
                        "Breakouts fail more often when the regime is off",
                        "failed_breakout_rate by regime",
                        "regime-off rate 10 points above regime-on, n >= 100 each")  # fmt: skip


def test_registration_needs_every_field_and_writes_the_notebook(lab: Lab) -> None:
    with pytest.raises(LabRuleError, match="decision_rule is empty"):
        lab.register("vcp", "t", "i", "m", " ")
    with pytest.raises(LabRuleError, match="strategy must be one of"):
        lab.register("momentum", "t", "i", "m", "d")
    hid = _register(lab)
    assert lab.get(hid).status == "OPEN"
    notes = lab.store.notes(kind="hypothesis")
    assert notes[0].text.startswith(f"H{hid} registered (vcp)") and "Decision rule" in notes[0].text


def test_the_budget_is_per_strategy_per_cycle(lab: Lab) -> None:
    for _ in range(MAX_PER_STRATEGY):
        _register(lab)
    with pytest.raises(LabRuleError, match="test budget for vcp is used"):
        _register(lab)
    _register(lab, "flat_base")  # another strategy has its own budget
    assert lab.budget()["vcp"] == 0 and lab.budget()["flat_base"] == MAX_PER_STRATEGY - 1


def test_windows_development_freely_validation_once_after_development(lab: Lab) -> None:
    hid = _register(lab)
    h, s, e = lab.check(hid, "development", None, None)
    assert (s, e) == (date(2022, 2, 1), date(2024, 6, 30))
    with pytest.raises(LabRuleError, match="within the development window"):
        lab.check(hid, "development", date(2024, 1, 1), date(2024, 12, 31))
    with pytest.raises(LabRuleError, match="window must be"):
        lab.check(hid, "paper", None, None)  # after 2026-09-30: the ledgers' evidence
    with pytest.raises(LabRuleError, match="validation comes after development"):
        lab.check(hid, "validation", None, None)
    run = lab.start_run(hid, "study", "development", s, e, {})
    lab.finish_run(run, h, "development", True, "study ...")
    _h, vs, ve = lab.check(hid, "validation", None, None)
    assert (vs, ve) == (date(2024, 7, 1), date(2026, 9, 30))
    run = lab.start_run(hid, "study", "validation", vs, ve, {})
    lab.finish_run(run, h, "validation", True, "study ...")
    with pytest.raises(LabRuleError, match="already used its one validation run"):
        lab.check(hid, "validation", None, None)
    found = [n.text for n in lab.store.notes(kind="finding")]
    assert any("(validation)" in t and "sanity check only" in t for t in found)


def test_a_failed_development_run_does_not_open_validation(lab: Lab) -> None:
    hid = _register(lab)
    h, s, e = lab.check(hid, "development", None, None)
    lab.finish_run(lab.start_run(hid, "study", "development", s, e, {}), h, "development",
                   False, "", error="boom")  # fmt: skip
    with pytest.raises(LabRuleError, match="validation comes after development"):
        lab.check(hid, "validation", None, None)
    assert any("failed: boom" in n.text for n in lab.store.notes(kind="finding"))


def test_conclusion_closes_the_hypothesis(lab: Lab) -> None:
    hid = _register(lab)
    with pytest.raises(LabRuleError, match="outcome must be one of"):
        lab.conclude(hid, "great", "x")
    lab.conclude(hid, "rejected", "Regime-off failed rate 34% vs 32% on (n=151/1179).")
    assert lab.get(hid).outcome == "rejected"
    with pytest.raises(LabRuleError, match="concluded"):
        lab.check(hid, "development", None, None)
    with pytest.raises(LabRuleError, match="already concluded"):
        lab.conclude(hid, "supported", "x")
    with pytest.raises(LabRuleError, match="no hypothesis H999"):
        lab.get(999)


def _row(sym: str, day: date, ret20: float | None, **kw: object) -> st.LabelRow:
    base: dict[str, object] = dict(mfe=10.0, mae=-5.0, breakout=True, failed=False,
                                   classification="VCP", grade=2, status="FORMING",
                                   pivot_distance=3.0, score=65.0, regime_on=True)  # fmt: skip
    base.update(kw)
    return st.LabelRow(sym, day, {5: None, 10: None, 20: ret20, 40: None, 60: None},
                       **base)  # type: ignore[arg-type]  # fmt: skip


def test_study_groups_counts_and_flags_small_samples() -> None:
    rows = [_row("A", DEV, 10.0), _row("B", DEV, -4.0, failed=True),
            _row("C", DEV, 2.0, classification="VCP_LIKE", regime_on=False, score=45.0),
            _row("D", DEV, None, breakout=False)]  # fmt: skip
    res = st.study(rows, "classification", 20)
    vcp = next(g for g in res["groups"] if g["group"] == "VCP")
    assert (vcp["setups"], vcp["n"], vcp["mean_ret_pct"], vcp["win_rate"]) == (3, 2, 3.0, 0.5)
    assert vcp["failed_breakout_rate"] == 0.5 and vcp["breakout_within_20_rate"] == 0.667
    assert vcp["small_sample"] is True
    assert {g["group"] for g in st.study(rows, "score_band", 20)["groups"]} == {"<50", "60-70"}
    assert {g["group"] for g in st.study(rows, "regime", 20)["groups"]} == {
        "regime on", "regime off"}  # fmt: skip
    with pytest.raises(ValueError, match="horizon"):
        st.study(rows, "grade", 15)


def test_filters_and_one_per_stock_month() -> None:
    rows = [_row("A", date(2023, 1, 2), 1.0), _row("A", date(2023, 1, 20), 9.0),
            _row("A", date(2023, 2, 1), 3.0), _row("B", date(2023, 1, 5), 5.0, score=40.0,
                                                   regime_on=False)]  # fmt: skip
    one = st.one_per_stock_month(rows)
    assert [(r.instrument_id, r.day.month) for r in one] == [("A", 1), ("B", 1), ("A", 2)]
    assert one[0].rets[20] == 1.0  # the first setup of the month is kept
    kept = st.apply_filters(rows, st.StudyFilters(min_score=50, regime="on"))
    assert {r.instrument_id for r in kept} == {"A"}
    assert st.apply_filters(rows, st.StudyFilters(classes=("A_PLUS_VCP",))) == []


def test_backtest_settings_are_checked_and_the_command_targets_the_research_db() -> None:
    ok = br.BacktestRequest("vcp", date(2022, 2, 1), date(2024, 6, 30), "agent-H1-development",
                            rule="hold_low8", classes=("VCP", "A_PLUS_VCP"), regime="breadth50",
                            entry="cross_5")  # fmt: skip
    br.validate(ok, "config")
    for bad, msg in ((dict(rule="nope"), "rule must be"), (dict(entry="open"), "entry must"),
                     (dict(regime="vix"), "regime must"), (dict(classes=("XX",)), "unknown"),
                     (dict(min_score=150.0), "min_score"),
                     (dict(max_positions=0), "max_positions")):  # fmt: skip
        req = br.BacktestRequest("vcp", ok.start, ok.end, ok.period, **bad)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match=msg):
            br.validate(req, "config")
    cmd = br.command(ok, Path("data/golden_src.duckdb"), "config")
    assert cmd[1:5] == ["backtest", "run", "--db", "data/golden_src.duckdb"]
    assert "--classes" in cmd and "VCP,A_PLUS_VCP" in cmd and "--period" in cmd


def test_a_missing_research_db_is_a_plain_failure(tmp_path: Path) -> None:
    req = br.BacktestRequest("vcp", date(2022, 2, 1), date(2022, 3, 1), "agent-H1-development")
    out = br.run(req, tmp_path / "none.duckdb", "config", tmp_path)
    assert not out.ok and "not found" in (out.error or "")
