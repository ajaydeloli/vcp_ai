"""Agent step A3: proposals are validated when made, only the owner decides (interactive
terminal), and approval applies through the same files the owner edits by hand."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from vcp_scanner.agent.proposals import ProposalError, Proposals
from vcp_scanner.agent.store import AgentStore

REPO = Path(__file__).resolve().parents[2]
CA = dict(symbol="ABC", action_type="SPLIT", ex_date="2026-10-13", ratio=[1, 10],
          evidence="Consolidation 10:1, notice https://example.org/x; 13.83 -> 138.3")  # fmt: skip


@pytest.fixture
def props(tmp_path: Path) -> Proposals:
    """A throwaway git repo with copies of the two manual files."""
    (tmp_path / "config").mkdir()
    for name in ("manual_corporate_actions.yaml", "manual_sectors.yaml"):
        shutil.copy(REPO / "config" / name, tmp_path / "config" / name)
    for cmd in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"],
                ["add", "."], ["commit", "-qm", "init"]):  # fmt: skip
        subprocess.run(["git", *cmd], cwd=tmp_path, check=True, capture_output=True)
    return Proposals(AgentStore(tmp_path / "agent.duckdb"), tmp_path, "config")


def test_invalid_proposals_are_refused_when_made(props: Proposals) -> None:
    with pytest.raises(ProposalError, match="kind must be"):
        props.create("strategy", "t", {}, "rationale x", "evidence x")
    with pytest.raises(ProposalError, match="corporate action invalid"):
        props.create("corporate_action", "t", {**CA, "ratio": None}, "rationale x", "evidence x")
    with pytest.raises(ProposalError, match="evidence must say"):
        props.create("corporate_action", "t", {**CA, "evidence": "trust me, it is right"}, "rationale x",
                     "evidence x")  # fmt: skip
    with pytest.raises(ProposalError, match="sector must be one of"):
        props.create("sector", "t", {"symbol": "ABC", "sector": "Tech"}, "rationale x", "e x")
    with pytest.raises(ProposalError, match="dq-"):
        props.create("resolve_event", "t", {"event_id": "x", "note": "n" * 30}, "r xxxxxxxx",
                     "e x")  # fmt: skip


def test_an_approved_corporate_action_is_appended_validated_and_committed(
    props: Proposals,
) -> None:
    p = props.create("corporate_action", "ABC SPLIT", CA, "it shows as a +900% jump", "the NSE notice")
    assert p.status == "PENDING"
    done = props.approve(p.proposal_id, "Ajay", "checked the notice")
    assert done.status == "APPLIED", done.apply_result
    assert "committed to git" in (done.apply_result or "")
    entries = yaml.safe_load((props.root / "config" / "manual_corporate_actions.yaml").read_text())
    last = entries["actions"][-1]
    assert (last["symbol"], last["ratio"], last["approved_by"]) == ("ABC", [1, 10], "Ajay")
    log = subprocess.run(["git", "log", "-1", "--format=%s"], cwd=props.root,
                         capture_output=True, text=True).stdout  # fmt: skip
    assert "proposal P1" in log and "approved by Ajay" in log
    with pytest.raises(ProposalError, match="not PENDING"):
        props.approve(p.proposal_id, "Ajay")
    assert any("approved by Ajay" in n.text for n in props.store.notes(kind="decision"))


def test_rejection_needs_a_reason_and_is_noted(props: Proposals) -> None:
    p = props.create("sector", "Sector ABC", {"symbol": "abc", "sector": "Chemicals"},
                     "NSE lists miss it", "NSE quote page")  # fmt: skip
    with pytest.raises(ProposalError, match="comment"):
        props.reject(p.proposal_id, "Ajay", "")
    done = props.reject(p.proposal_id, "Ajay", "wrong industry")
    assert done.status == "REJECTED" and done.comment == "wrong industry"
    assert any("Reason: wrong industry" in n.text for n in props.store.notes(kind="decision"))


def test_an_approved_sector_is_appended_and_a_duplicate_fails(props: Proposals) -> None:
    p = props.create("sector", "Sector ZZZNEW", {"symbol": "zzznew", "sector": "Chemicals",
                                                 "industry": "Specialty Chemicals"},
                     "NSE lists miss it", "NSE quote page")  # fmt: skip
    assert props.approve(p.proposal_id, "Ajay").status == "APPLIED"
    text = (props.root / "config" / "manual_sectors.yaml").read_text()
    assert '{symbol: ZZZNEW, sector: Chemicals, industry: "Specialty Chemicals"' in text
    again = props.create("sector", "Sector ZZZNEW", {"symbol": "ZZZNEW", "sector": "Power"},
                         "a second try", "NSE quote page")  # fmt: skip
    failed = props.approve(again.proposal_id, "Ajay")
    assert failed.status == "FAILED" and "already has a manual sector" in (failed.apply_result
                                                                            or "")  # fmt: skip


def test_watchlist_is_pre_approved_and_applied_at_once(props: Proposals) -> None:
    p = props.create("watchlist", "AI watchlist +X", {"add": ["x", "y"]}, "near pivots",
                     "near pivots")  # fmt: skip
    assert p.status == "APPLIED"
    props.create("watchlist", "AI watchlist -X", {"remove": ["X"]}, "broke down", "broke down")
    assert [s for s, *_ in props.watchlist()] == ["Y"]


def test_the_cli_refuses_to_decide_without_an_interactive_terminal(tmp_path: Path) -> None:
    done = subprocess.run([str(REPO / ".venv" / "bin" / "vcp"), "agent", "approve", "1", "--db",
                           str(tmp_path / "a.duckdb")], cwd=REPO, capture_output=True,
                          text=True, stdin=subprocess.DEVNULL)  # fmt: skip
    assert done.returncode == 1 and "interactive terminal" in done.stderr
