"""Agent step A4: strategy candidates. Overrides are checked against the frozen files, a
candidate runs from its own config copy (never the frozen files), the build works on a branch in
a separate worktree, approval merges it and starts the shadow ledger, and the shadow ledger is
kept apart from the frozen one."""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest

from vcp_scanner.agent import candidate_build as cb
from vcp_scanner.agent import candidates as C
from vcp_scanner.agent import shadow as sh
from vcp_scanner.agent.lab import Lab
from vcp_scanner.agent.proposals import ProposalError, Proposals
from vcp_scanner.agent.store import AgentStore
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.paper.ledger import PaperEvent

REPO = Path(__file__).resolve().parents[2]
CFG = REPO / "config"
SPEC = {"old": "max depth 15 % for FLAT_BASE", "new": "max depth 12 % for FLAT_BASE",
        "why": "shallower bases held better in the development window",
        "evidence": "run 7 (development), 212 setups, see notebook"}  # fmt: skip


def _other_rule() -> str:
    from vcp_scanner.research.outcomes import ENGINE_RULES

    return next(r.name for r in ENGINE_RULES if r.name != "hold_low8")


# ------------------------------------------------------------------------------ overrides


def test_overrides_are_checked_against_the_frozen_files() -> None:
    ok = C.validate_overrides(CFG, "flat_base", [
        {"file": "strategies/flat_base.yaml", "path": "classification.flat_base.max_depth_pct",
         "new": 12}])  # fmt: skip
    assert ok[0].old == 15 and ok[0].new == 12
    bad = [
        ({"file": "strategies/flat_base.yaml", "path": "algorithm_version", "new": "x"},
         "may change"),
        ({"file": "strategies/cup_handle.yaml", "path": "detector.min_duration_days", "new": 9},
         "may change"),  # another strategy's file
        ({"file": "strategy.yaml", "path": "vcp.min_contractions", "new": 3}, "may change"),
        ({"file": "strategies/flat_base.yaml", "path": "classification.nope", "new": 1},
         "has no setting"),
        ({"file": "strategies/flat_base.yaml", "path": "classification.flat_base.max_depth_pct",
          "new": "12"}, "same kind"),
        ({"file": "strategies/flat_base.yaml", "path": "classification.flat_base.max_depth_pct",
          "new": 15}, "already"),
        ({"file": "strategies/flat_base.yaml", "path": "classification.flat_base"}, "missing"),
    ]  # fmt: skip
    for change, msg in bad:
        with pytest.raises(C.CandidateError, match=msg):
            C.validate_overrides(CFG, "flat_base", [change])
    with pytest.raises(C.CandidateError, match="at most"):
        C.validate_overrides(CFG, "flat_base", [{"file": "x", "path": str(i), "new": 1}
                                                for i in range(6)])  # fmt: skip
    with pytest.raises(C.CandidateError, match="at least one"):
        C.validate_overrides(CFG, "flat_base", [])


def test_vcp_candidates_change_only_vcp_parts_of_the_shared_files() -> None:
    import yaml

    doc = yaml.safe_load((CFG / "strategy.yaml").read_text())
    key = next(k for k, v in doc["vcp"].items() if isinstance(v, (int, float))
               and not isinstance(v, bool))  # fmt: skip
    new = doc["vcp"][key] + 1
    assert C.validate_overrides(CFG, "vcp", [{"file": "strategy.yaml", "path": f"vcp.{key}",
                                              "new": new}])  # fmt: skip
    with pytest.raises(C.CandidateError, match="may change"):
        C.validate_overrides(
            CFG, "vcp", [{"file": "strategy.yaml", "path": "trend_template.min_rs_rank", "new": 1}]
        )
    # Paper rules: allowed for any strategy, only the three defaults.
    assert C.validate_overrides(CFG, "vcp", [{"file": "backtest.yaml", "path": "defaults.rule",
                                              "new": _other_rule()}])  # fmt: skip
    with pytest.raises(C.CandidateError, match="may change"):
        C.validate_overrides(CFG, "vcp", [{"file": "backtest.yaml", "path": "min_test_months",
                                           "new": 1}])  # fmt: skip


def test_a_candidate_runs_from_its_own_copy_and_never_edits_the_frozen_files(
    tmp_path: Path,
) -> None:
    before = {p: p.read_bytes() for p in CFG.rglob("*.yaml")}
    ov = C.validate_overrides(CFG, "flat_base", [
        {"file": "strategies/flat_base.yaml", "path": "classification.flat_base.max_depth_pct",
         "new": 12}])  # fmt: skip
    cand = C.materialize(CFG, ov, tmp_path / "c1")
    assert not (cand / "live.yaml").exists() and not (cand / C.CANDIDATES_DIR).exists()
    checks = C.check(CFG, cand, "flat_base", paper_only=False)
    assert checks["frozen_hash"] != checks["candidate_hash"]
    # Other strategies keep the frozen hash in the candidate copy.
    assert C.strategy_hash(cand, "cup_handle") == C.strategy_hash(CFG, "cup_handle")
    assert {p: p.read_bytes() for p in CFG.rglob("*.yaml")} == before
    # Only paper rules: the scan (hash) is unchanged; a scan change claimed as paper-only fails.
    paper = C.validate_overrides(CFG, "flat_base", [{"file": "backtest.yaml",
                                                     "path": "defaults.rule",
                                                     "new": _other_rule()}])  # fmt: skip
    p_dir = C.materialize(CFG, paper, tmp_path / "c2")
    assert C.check(CFG, p_dir, "flat_base", paper_only=True)["candidate_hash"] == \
        checks["frozen_hash"]  # fmt: skip
    with pytest.raises(C.CandidateError, match="paper-rule"):
        C.check(CFG, cand, "flat_base", paper_only=True)


def test_an_invalid_candidate_config_is_refused(tmp_path: Path) -> None:
    ov = C.validate_overrides(CFG, "flat_base", [
        {"file": "strategies/flat_base.yaml", "path": "classification.flat_base.max_depth_pct",
         "new": -1}])  # fmt: skip
    with pytest.raises(C.CandidateError, match="does not validate"):
        C.check(CFG, C.materialize(CFG, ov, tmp_path / "bad"), "flat_base", paper_only=False)


def test_candidate_file_round_trip_and_ids(tmp_path: Path) -> None:
    cand = C.Candidate("flat_base-c1", "flat_base", "flat_base-1.0.0", "flat_base-1.0.0+c1", 3,
                       5, "active", date(2026, 10, 27),
                       [C.Override("strategies/flat_base.yaml", "a.b", 1, 2)], SPEC)  # fmt: skip
    path = C.candidate_path(tmp_path, cand.candidate_id)
    C.write_file(path, cand)
    assert C.read_file(path) == cand
    assert [c.candidate_id for c in C.load_candidates(tmp_path, "active")] == ["flat_base-c1"]
    assert C.next_candidate_id(tmp_path, "flat_base", ["flat_base-c2"]) == "flat_base-c3"
    assert C.valid_id("double_bottom-c12") and not C.valid_id("Flat base")


def test_scan_steps_vcp_needs_its_own_trend_template() -> None:
    v = [a[:2] for a in C.scan_steps("vcp", date(2026, 10, 1), "db", "cand")]
    assert v == [["compute", "trend-template"], ["compute", "vcp"], ["compute", "scores"]]
    f = C.scan_steps("flat_base", date(2026, 10, 1), "db", "cand")
    assert [a[:2] for a in f] == [["compute", "setups"], ["compute", "scores"]]
    assert all("--config-dir" in a and a[a.index("--config-dir") + 1] == "cand" for a in f)


# ------------------------------------------------------------------------------ scan diff


def _scores(conn: Any, rows: list[tuple[str, str, date, str, bool, float]]) -> None:
    for h, iid, d, cls, ok, score in rows:
        conn.execute(
            "INSERT INTO setup_scores (scan_id, strategy_id, instrument_id, as_of_date,"
            " classification, eligible, final_setup_score, fundamental_available,"
            " weights_renormalized, trend_weight, vcp_weight, volume_weight, rs_weight,"
            " fundamental_weight, scoring_version, config_hash, data_snapshot_id, created_at)"
            " VALUES (?, 'flat_base', ?, ?, ?, ?, ?, FALSE, FALSE, 1, 1, 1, 1, 0, 's', ?, 'LIVE',"
            " ?)",
            [f"{h}-{d}", iid, d, cls, ok, score, h, datetime(2026, 10, 1, tzinfo=UTC)],
        )


def test_scan_diff_names_added_dropped_reclassified_and_rescored(tmp_path: Path) -> None:
    d = date(2026, 10, 5)
    with DuckDBStore(str(tmp_path / "m.duckdb")) as store:
        store.migrate()
        _scores(store.conn, [
            ("F", "A", d, "FLAT_BASE", True, 70.0), ("C", "A", d, "FLAT_BASE", True, 70.5),
            ("F", "B", d, "FLAT_BASE", True, 60.0),                 # dropped
            ("C", "D", d, "FLAT_BASE", True, 55.0),                 # added
            ("F", "E", d, "FLAT_BASE", True, 50.0), ("C", "E", d, "TIGHT_FLAT_BASE", True, 50.0),
            ("F", "G", d, "FLAT_BASE", True, 40.0), ("C", "G", d, "FLAT_BASE", True, 45.0),
            ("C", "H", d, "FLAT_BASE", False, 80.0),                # not eligible: ignored
        ])  # fmt: skip
        rows = C.scan_diff(store.conn, "flat_base", "F", "C", [d])
        assert C.scanned_days(store.conn, "flat_base", "F", date(2026, 10, 1)) == [d]
    got = {r["instrument_id"]: r["change"] for r in rows}
    assert got == {"B": "DROPPED", "D": "ADDED", "E": "RECLASSIFIED", "G": "RESCORED"}
    s = C.diff_summary(rows, [d], {d: 4})
    assert s["changes"] == {"ADDED": 1, "DROPPED": 1, "RECLASSIFIED": 1, "RESCORED": 1}
    assert s["frozen_eligible"] == 4 and s["stocks"] == 4


# ------------------------------------------------------------------------------ comparison


def test_ledger_stats_and_the_comparison_say_what_is_still_needed() -> None:
    ev = [("ENTRY", date(2026, 10, 2), {}), ("EXIT", date(2026, 10, 9), {"ret_pct": 10.0}),
          ("ENTRY", date(2026, 10, 3), {}), ("EXIT", date(2026, 10, 10), {"ret_pct": -5.0}),
          ("ENTRY", date(2026, 10, 4), {})]  # fmt: skip
    s = C.ledger_stats(ev)
    assert (s.closed, s.open, s.win_rate, s.avg_ret, s.profit_factor) == (2, 1, 50.0, 2.5, 2.0)
    assert s.max_drawdown == 5.0
    out = C.compare(s, s, date(2026, 10, 1), date(2026, 10, 26))
    assert out["avg_ret_diff"] is None  # fewer than 5 trades a side: no range claimed
    assert not out["ready_to_judge"] and out["needs"] == ["28 more closed shadow trades",
                                                          "66 more days"]  # fmt: skip
    many = C.ledger_stats([("EXIT", date(2026, 10, 1), {"ret_pct": float(r)})
                           for r in [5, -3, 8, -2, 4] * 6])  # fmt: skip
    ready = C.compare(many, many, date(2026, 6, 1), date(2026, 10, 26))
    assert ready["ready_to_judge"] and ready["avg_ret_diff"] == 0.0
    assert ready["avg_ret_diff_lo"] < 0 < ready["avg_ret_diff_hi"]


# ------------------------------------------------------------------------------ proposals


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A throwaway git repo (branch main) with a copy of config/."""
    root = tmp_path / "proj"
    root.mkdir()
    shutil.copytree(CFG, root / "config", ignore=shutil.ignore_patterns("live.yaml"))
    for cmd in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"],
                ["config", "user.name", "t"], ["add", "."], ["commit", "-qm", "init"]):  # fmt: skip
        subprocess.run(["git", *cmd], cwd=root, check=True, capture_output=True)
    return root


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True,
                          text=True).stdout.strip()  # fmt: skip


def test_a_candidate_needs_a_registered_hypothesis_of_its_strategy(repo: Path) -> None:
    store = AgentStore(repo / "agent.duckdb")
    props = Proposals(store, repo, "config")
    payload = {"strategy_id": "flat_base", "hypothesis_id": 1, "spec": SPEC, "overrides": [
        {"file": "strategies/flat_base.yaml", "path": "classification.flat_base.max_depth_pct",
         "new": 12}]}  # fmt: skip
    with pytest.raises(ProposalError, match="no hypothesis H1"):
        props.create("candidate", "t", payload, "rationale x", "evidence x")
    hid = Lab(store).register("cup_handle", "title", "idea", "metric", "decision rule")
    with pytest.raises(ProposalError, match="is about cup_handle"):
        props.create(
            "candidate", "t", {**payload, "hypothesis_id": hid}, "rationale x", "evidence xx"
        )
    hid = Lab(store).register("flat_base", "title", "idea", "metric", "decision rule")
    with pytest.raises(ProposalError, match="spec.why"):
        props.create(
            "candidate",
            "t",
            {**payload, "hypothesis_id": hid, "spec": {**SPEC, "why": ""}},
            "rationale x",
            "e x",
        )
    with pytest.raises(ProposalError, match="may change"):
        props.create("candidate", "t", {**payload, "hypothesis_id": hid, "overrides": [
            {"file": "strategies/flat_base.yaml", "path": "stage", "new": "live"}]},
                     "rationale x", "evidence xx")  # fmt: skip


def test_propose_build_approve_merges_on_main_and_starts_the_shadow(
    repo: Path, tmp_path: Path
) -> None:
    store = AgentStore(repo / "agent.duckdb")
    launched: list[int] = []
    props = Proposals(store, repo, "config", launcher=launched.append)
    hid = Lab(store).register("flat_base", "title", "idea", "metric", "decision rule")
    # Paper rules only: no scan to diff, so the build needs no backup.
    payload = {"strategy_id": "flat_base", "hypothesis_id": hid, "spec": SPEC,
               "overrides": [{"file": "backtest.yaml", "path": "defaults.rule",
                              "new": _other_rule()}]}  # fmt: skip
    p = props.create("candidate", "Candidate flat_base rule", payload, "rationale x", "evidence xx")
    assert p.status == "BUILDING" and launched == [p.proposal_id]
    assert p.payload["candidate_id"] == "flat_base-c1"
    assert p.payload["candidate_version"] == "flat_base-1.0.0+c1"
    # The owner cannot approve while it is being built.
    with pytest.raises(ProposalError, match="not PENDING"):
        props.approve(p.proposal_id, "owner")
    head_before = _git(repo, "rev-parse", "main")
    line = cb.build(props, p.proposal_id, repo, "config", lambda argv: 0, tmp_path / "nobackups",
                    work_dir=tmp_path / "work")  # fmt: skip
    assert "built" in line, line
    built = props.get(p.proposal_id)
    assert built.status == "PENDING" and built.payload["scan_diff"]["paper_rules_only"]
    # The build left main and the owner's working tree alone; the branch holds the file.
    assert _git(repo, "rev-parse", "main") == head_before
    assert not (repo / "config" / "candidates").exists()
    assert "config/candidates/flat_base-c1.yaml" in _git(
        repo, "show", "--name-only", "--format=", "candidate/flat_base-c1"
    )
    assert cb.worktree_dir(repo, "flat_base-c1").exists()
    done = props.approve(p.proposal_id, "owner")
    assert done.status == "APPLIED", done.apply_result
    cand = C.read_file(C.candidate_path(repo / "config", "flat_base-c1"))
    assert cand.status == "active" and cand.start_date is not None
    assert "active from" in _git(repo, "log", "-1", "--format=%s")
    assert not cb.worktree_dir(repo, "flat_base-c1").exists()
    con = duckdb.connect(str(store.path), read_only=True)
    try:
        assert con.execute("SELECT status FROM strategy_candidates").fetchall() == [("active",)]
    finally:
        con.close()
    # Next candidate of the same strategy gets the next id.
    p2 = props.create("candidate", "t", payload, "rationale x", "evidence xx")
    assert p2.payload["candidate_id"] == "flat_base-c2"


def test_a_failed_build_is_recorded_and_a_rejection_keeps_the_branch(
    repo: Path, tmp_path: Path
) -> None:
    store = AgentStore(repo / "agent.duckdb")
    props = Proposals(store, repo, "config")
    hid = Lab(store).register("flat_base", "title", "idea", "metric", "decision rule")
    payload = {"strategy_id": "flat_base", "hypothesis_id": hid, "spec": SPEC, "overrides": [
        {"file": "strategies/flat_base.yaml", "path": "classification.flat_base.max_depth_pct",
         "new": 12}]}  # fmt: skip
    p = props.create("candidate", "t", payload, "rationale x", "evidence xx")
    # A scan change needs a backup to diff on: none here, so the build fails, said plainly.
    cb.build(props, p.proposal_id, repo, "config", lambda argv: 0, tmp_path / "nobackups",
             work_dir=tmp_path / "work")  # fmt: skip
    failed = props.get(p.proposal_id)
    assert failed.status == "BUILD_FAILED" and "no backup" in (failed.apply_result or "")
    p2 = props.create("candidate", "t", {**payload, "overrides": [
        {"file": "backtest.yaml", "path": "defaults.rule", "new": _other_rule()}]}, "r xxxxxxxxxx",
                      "evidence xx")  # fmt: skip
    cb.build(props, p2.proposal_id, repo, "config", lambda argv: 0, tmp_path / "nobackups",
             work_dir=tmp_path / "work")  # fmt: skip
    props.reject(p2.proposal_id, "owner", "not now, wait for more trades")
    cid = p2.payload["candidate_id"]
    assert _git(repo, "branch", "--list", f"candidate/{cid}")  # kept: nothing deleted
    assert not cb.worktree_dir(repo, cid).exists()


# ------------------------------------------------------------------------------ shadow


def test_shadow_rows_are_separate_append_only_and_compared_since_the_start(
    tmp_path: Path,
) -> None:
    db = str(tmp_path / "m.duckdb")
    start = date(2026, 10, 12)
    with DuckDBStore(db) as store:
        store.migrate()
        repo = sh.ShadowRepository(store.conn)
        ev = [PaperEvent("flat_base", "A", date(2026, 10, 13), "ENTRY", 10.0, start),
              PaperEvent("flat_base", "A", date(2026, 10, 20), "EXIT", 11.0, start,
                         {"ret_pct": 10.0, "entry_day": "2026-10-13"})]  # fmt: skip
        assert repo.append("flat_base-c1", "C", ev, None) == 2
        assert repo.append("flat_base-c1", "C", ev, None) == 0  # same events: ignored
        assert store.conn.execute("SELECT count(*) FROM paper_events").fetchone()[0] == 0
        frozen = C.strategy_hash(CFG, "flat_base")
        for e, d, meta in [("ENTRY", date(2026, 10, 1), {}),  # before the start: left out
                           ("EXIT", date(2026, 10, 14), {"ret_pct": -4.0,
                                                         "entry_day": "2026-10-01"}),
                           ("ENTRY", date(2026, 10, 15), {}),
                           ("EXIT", date(2026, 10, 21), {"ret_pct": 3.0,
                                                         "entry_day": "2026-10-15"})]:  # fmt: skip
            store.conn.execute(
                "INSERT INTO paper_events VALUES (?, 'paper-v1', 'flat_base', ?, 'A', ?, ?, NULL,"
                " NULL, ?, NULL, ?)", [f"e{d}{e}", frozen, d, e, json.dumps(meta),
                                       datetime.now(UTC)])  # fmt: skip
        cand = C.Candidate("flat_base-c1", "flat_base", "v", "v+c1", 1, 1, "active", start, [],
                           SPEC)  # fmt: skip
        out = sh.comparison(store.conn, str(CFG), cand, date(2026, 10, 26))
    assert out["frozen"]["closed"] == 1 and out["frozen"]["avg_ret"] == 3.0
    assert out["candidate"]["closed"] == 1 and out["candidate"]["avg_ret"] == 10.0
    assert out["days"] == 14 and not out["ready_to_judge"]


def test_shadow_update_with_no_active_candidate_does_nothing(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    assert sh.update(str(tmp_path / "m.duckdb"), str(CFG), lambda a: calls.append(a) or 0,
                     work_dir=tmp_path / "w") == []  # fmt: skip
    assert calls == []


def test_shadow_update_scans_only_days_the_frozen_run_scanned(repo: Path, tmp_path: Path) -> None:
    cfg = repo / "config"
    ov = C.validate_overrides(cfg, "flat_base", [
        {"file": "strategies/flat_base.yaml", "path": "classification.flat_base.max_depth_pct",
         "new": 12}])  # fmt: skip
    C.write_file(
        C.candidate_path(cfg, "flat_base-c1"),
        C.Candidate(
            "flat_base-c1", "flat_base", "v", "v+c1", 1, 1, "active", date(2026, 10, 12), ov, SPEC
        ),
    )
    frozen = C.strategy_hash(cfg, "flat_base")
    db = str(tmp_path / "m.duckdb")
    with DuckDBStore(db) as store:
        store.migrate()
        _scores(
            store.conn,
            [
                (frozen, "A", date(2026, 10, 9), "FLAT_BASE", True, 50.0),
                (frozen, "A", date(2026, 10, 12), "FLAT_BASE", True, 50.0),
                (frozen, "A", date(2026, 10, 13), "FLAT_BASE", True, 50.0),
            ],
        )
    calls: list[list[str]] = []
    out = sh.update(db, str(cfg), lambda a: calls.append(a) or 0, work_dir=tmp_path / "w")
    assert [r.ok for r in out] == [True], out
    days = sorted({a[a.index("--as-of") + 1] for a in calls})
    assert days == ["2026-10-12", "2026-10-13"]  # from the start, not before; no later day
    assert all(a[a.index("--config-dir") + 1].endswith("flat_base-c1/config") for a in calls)
    # A failing scan is reported for that candidate, never raised.
    out = sh.update(db, str(cfg), lambda a: 1, work_dir=tmp_path / "w")
    assert not out[0].ok and "scan failed" in out[0].text


def test_the_weekly_report_shows_started_candidates_only(repo: Path, tmp_path: Path) -> None:
    from vcp_scanner.api.context import Context
    from vcp_scanner.reporting.build import shadow_section

    cfg = repo / "config"
    ctx = Context(scan_hash="h", regime_rule="breadth50", strategies=(), data_dir=tmp_path,
                  config_dir=cfg)  # fmt: skip
    with DuckDBStore(str(tmp_path / "m.duckdb")) as store:
        store.migrate()
        assert shadow_section(store.conn, ctx, date(2026, 10, 30)) == ""  # nothing started
        C.write_file(C.candidate_path(cfg, "flat_base-c1"), C.Candidate(
            "flat_base-c1", "flat_base", "v", "v+c1", 1, 1, "active", date(2026, 10, 27), [],
            SPEC))  # fmt: skip
        html = shadow_section(store.conn, ctx, date(2026, 10, 30))
    assert "Strategy candidates (shadow ledger)" in html and "flat_base-c1 (active" in html
    assert "needs 30 more closed shadow trades, 88 more days" in html
