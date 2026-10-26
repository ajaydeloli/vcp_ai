"""``vcp agent ...``: the MCP server for Claude Code and its notebook (AGENT_SPECIFICATION)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from vcp_scanner.cli_pipeline import _err

DEFAULT_AGENT_DB = "data/agent/agent.duckdb"


def add_agent_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    agent = subparsers.add_parser(
        "agent", help="The AI's MCP server and notebook (AGENT_SPECIFICATION)"
    )
    sub = agent.add_subparsers(dest="agent_command")
    sv = sub.add_parser("serve", help="Run the MCP server over stdio (Claude Code starts this)")
    sv.add_argument("--config-dir", default="config")
    sv.add_argument("--data-dir", default="data")
    nt = sub.add_parser("notes", help="Show the notebook, newest first")
    nt.add_argument("--kind", default=None)
    nt.add_argument("--search", default=None)
    nt.add_argument("--limit", type=int, default=30)
    nt.add_argument("--db", default=DEFAULT_AGENT_DB)
    au = sub.add_parser("audit", help="Show the last tool calls the AI made")
    au.add_argument("--limit", type=int, default=50)
    au.add_argument("--db", default=DEFAULT_AGENT_DB)
    sd = sub.add_parser("seed", help="Add the standing decisions to the notebook (idempotent)")
    sd.add_argument("--db", default=DEFAULT_AGENT_DB)
    ad = sub.add_parser("note", help="Add your own note or decision to the notebook")
    ad.add_argument("kind")
    ad.add_argument("text")
    ad.add_argument("--tags", default=None)
    ad.add_argument("--db", default=DEFAULT_AGENT_DB)
    hy = sub.add_parser("hypotheses", help="Show the research lab: hypotheses, runs, budget")
    hy.add_argument("--db", default=DEFAULT_AGENT_DB)
    pr = sub.add_parser("proposals", help="Show the AI's proposals (newest first)")
    pr.add_argument("--status", default=None, help="PENDING, APPROVED, REJECTED, APPLIED, FAILED")
    pr.add_argument("--db", default=DEFAULT_AGENT_DB)
    ap = sub.add_parser("approve", help="Approve and apply a proposal (owner, interactive)")
    ap.add_argument("proposal_id", type=int)
    ap.add_argument("--comment", default=None)
    ap.add_argument("--by", default=None, help="Your name (default: git user.name)")
    ap.add_argument("--db", default=DEFAULT_AGENT_DB)
    rj = sub.add_parser("reject", help="Reject a proposal with a reason the AI reads")
    rj.add_argument("proposal_id", type=int)
    rj.add_argument("comment")
    rj.add_argument("--by", default=None, help="Your name (default: git user.name)")
    rj.add_argument("--db", default=DEFAULT_AGENT_DB)
    # Strategy candidates (step A4)
    cl = sub.add_parser(
        "candidates", help="Show strategy candidates, scan diffs and shadow results"
    )
    cl.add_argument("--config-dir", default="config")
    cl.add_argument("--main-db", default="data/vcp_scanner.duckdb")
    cl.add_argument("--db", default=DEFAULT_AGENT_DB)
    cb = sub.add_parser("candidate-build", help="Build a candidate proposal (started by the AI's "
                        "propose_candidate; runs in the background)")  # fmt: skip
    cb.add_argument("proposal_id", type=int)
    cb.add_argument("--config-dir", default="config")
    cb.add_argument("--main-db", default="data/vcp_scanner.duckdb")
    cb.add_argument("--db", default=DEFAULT_AGENT_DB)
    sh = sub.add_parser("shadow", help="Shadow ledger of active candidates (the daily run)")
    sh.add_argument("shadow_command", choices=("update", "compare"))
    sh.add_argument("--config-dir", default="config")
    sh.add_argument("--main-db", default="data/vcp_scanner.duckdb")
    rt = sub.add_parser("retire", help="Stop shadowing a candidate (owner, interactive)")
    rt.add_argument("candidate_id")
    rt.add_argument("reason")
    rt.add_argument("--by", default=None, help="Your name (default: git user.name)")
    rt.add_argument("--config-dir", default="config")
    rt.add_argument("--db", default=DEFAULT_AGENT_DB)


def run_agent(args: argparse.Namespace) -> int:
    from vcp_scanner.agent.store import AgentStore

    cmd = getattr(args, "agent_command", None)
    if cmd == "serve":
        from vcp_scanner.agent.server import serve

        serve(args.config_dir, args.data_dir)
        return 0
    if cmd is None:
        _err("Usage: vcp agent {serve,notes,audit,seed,note,hypotheses,proposals,approve,reject,"
             "candidates,shadow,retire} ...")  # fmt: skip
        return 1
    store = AgentStore(Path(getattr(args, "db", DEFAULT_AGENT_DB)))
    if cmd == "seed":
        from vcp_scanner.agent.seed import seed_notebook

        print(f"Seeded {seed_notebook(store)} entries")
        return 0
    if cmd == "note":
        try:
            note_id = store.add_note(args.kind, args.text, args.tags, source="owner")
        except ValueError as exc:
            _err(str(exc))
            return 1
        print(f"Added note {note_id}")
        return 0
    if cmd == "notes":
        for n in store.notes(text=args.search, kind=args.kind, limit=args.limit):
            print(f"#{n.note_id} {n.created_at:%Y-%m-%d} [{n.kind}/{n.source}] {n.text}")
        return 0
    if cmd == "audit":
        for at, tool, args_json, ok, error, ms in store.audit_rows(args.limit):
            mark = "ok" if ok else f"ERROR {error}"
            print(f"{at:%Y-%m-%d %H:%M:%S} {tool} {args_json} {ms} ms {mark}")
        return 0
    if cmd == "hypotheses":
        from vcp_scanner.agent.lab import CYCLE, Lab

        lab = Lab(store)
        left = ", ".join(f"{k} {v}" for k, v in lab.budget().items())
        print(f"Test budget left this review cycle ({CYCLE}): {left}")
        for h in lab.find():
            print(f"H{h.hypothesis_id} [{h.strategy}] {h.status}"
                  f"{' ' + h.outcome if h.outcome else ''}: {h.title}")  # fmt: skip
            print(f"    decision rule: {h.decision_rule}")
            for r in reversed(lab.runs(h.hypothesis_id)):
                mark = "ok" if r["ok"] else f"failed: {r['error']}"
                print(f"    run {r['run_id']} {r['kind']} {r['window']} {mark}"
                      f"{': ' + r['summary'][:160] if r['summary'] else ''}")  # fmt: skip
            if h.conclusion:
                print(f"    conclusion: {h.conclusion}")
        return 0
    if cmd in ("proposals", "approve", "reject"):
        return _proposals(cmd, args, store)
    if cmd in ("candidates", "candidate-build", "shadow", "retire"):
        return _candidates(cmd, args, store)
    _err(f"Unknown agent command: {cmd}")
    return 1


def _owner_name(given: str | None) -> str:
    import subprocess

    if given and given.strip():
        return given.strip()
    done = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True)
    return done.stdout.strip() or "owner"


def _show(p: object) -> None:
    from vcp_scanner.agent.proposals import Proposal

    assert isinstance(p, Proposal)
    print(f"P{p.proposal_id} [{p.kind}] {p.status} {p.created_at:%Y-%m-%d %H:%M}: {p.title}")
    print(f"    change   : {json.dumps(p.payload, ensure_ascii=False)}")
    print(f"    why      : {p.rationale}")
    print(f"    evidence : {p.evidence}")
    if p.decided_by:
        print(f"    decided  : {p.decided_by} {p.decided_at:%Y-%m-%d %H:%M}"
              f"{' - ' + p.comment if p.comment else ''}")  # fmt: skip
    if p.apply_result:
        print(f"    result   : {p.apply_result}")


def _proposals(cmd: str, args: argparse.Namespace, store: object) -> int:
    import sys

    from vcp_scanner.agent.proposals import ProposalError, Proposals
    from vcp_scanner.agent.store import AgentStore

    assert isinstance(store, AgentStore)
    props = Proposals(store, Path.cwd(), getattr(args, "config_dir", "config"))
    try:
        if cmd == "proposals":
            found = props.find(status=args.status)
            if not found:
                print("No proposals" + (f" with status {args.status}" if args.status else ""))
            for p in found:
                _show(p)
            return 0
        # Deciding is the owner's: an interactive terminal and a typed confirmation. The AI's
        # shell (Claude Code) is not interactive, so it cannot approve its own proposal.
        if not sys.stdin.isatty():
            _err(f"`vcp agent {cmd}` must be run by the owner in an interactive terminal")
            return 1
        p = props.get(args.proposal_id)
        _show(p)
        typed = input(f"Type {p.proposal_id} to {cmd} this proposal: ").strip()
        if typed != str(p.proposal_id):
            print("Not confirmed; nothing changed.")
            return 1
        by = _owner_name(args.by)
        if cmd == "approve":
            done = props.approve(p.proposal_id, by, args.comment)
        else:
            done = props.reject(p.proposal_id, by, args.comment)
        _show(done)
        return 0 if done.status in ("APPLIED", "REJECTED") else 1
    except ProposalError as exc:
        _err(str(exc))
        return 1


def _candidates(cmd: str, args: argparse.Namespace, store: object) -> int:  # noqa: C901
    import sys
    from datetime import datetime

    from vcp_scanner.agent import candidate_build, shadow
    from vcp_scanner.agent.candidates import load_candidates
    from vcp_scanner.agent.store import AgentStore
    from vcp_scanner.cli import main as cli_main
    from vcp_scanner.data.providers._time import IST

    assert isinstance(store, AgentStore)
    root = Path.cwd()
    today = datetime.now(IST).date()
    if cmd == "shadow":
        if args.shadow_command == "update":
            results = shadow.update(args.main_db, args.config_dir, cli_main)
            if not results:
                print("Shadow ledger: no active candidates")
            for r in results:
                print(f"  {r.candidate_id:22} {'ok' if r.ok else 'FAILED'}: {r.text}")
            return 0 if all(r.ok for r in results) else 1
        import duckdb

        conn = duckdb.connect(args.main_db, read_only=True)
        try:
            rows = shadow.comparisons(conn, args.config_dir, today)
        finally:
            conn.close()
        print(json.dumps(rows, indent=2, default=str) if rows else "No started candidates")
        return 0
    if cmd == "candidate-build":
        from vcp_scanner.agent.proposals import Proposals
        from vcp_scanner.backup import default_backup_dir

        props = Proposals(store, root, args.config_dir)
        print(candidate_build.build(props, args.proposal_id, root, args.config_dir, cli_main,
                                    default_backup_dir(args.main_db)))  # fmt: skip
        return 0 if props.get(args.proposal_id).status == "PENDING" else 1
    if cmd == "retire":
        if not sys.stdin.isatty():
            _err("`vcp agent retire` must be run by the owner in an interactive terminal")
            return 1
        typed = input(f"Type {args.candidate_id} to stop shadowing it: ").strip()
        if typed != args.candidate_id:
            print("Not confirmed; nothing changed.")
            return 1
        try:
            by = _owner_name(args.by)
            print(candidate_build.retire(root, args.config_dir, args.candidate_id, by,
                                         args.reason, store.path))  # fmt: skip
        except (ValueError, RuntimeError) as exc:
            _err(str(exc))
            return 1
        store.add_note("decision", f"Candidate {args.candidate_id} retired: {args.reason}",
                       tags="candidate", source="owner")  # fmt: skip
        return 0
    cands = load_candidates(args.config_dir)
    if not cands:
        print("No strategy candidates on main (proposals: `vcp agent proposals`)")
        return 0
    import duckdb

    conn = duckdb.connect(args.main_db, read_only=True)
    try:
        compared = {c["candidate_id"]: c for c in shadow.comparisons(conn, args.config_dir, today)}
    finally:
        conn.close()
    for c in cands:
        print(f"{c.candidate_id} [{c.strategy_id}] {c.status} {c.candidate_version} "
              f"(H{c.hypothesis_id}, P{c.proposal_id}) from {c.start_date or '-'}")  # fmt: skip
        for o in c.overrides:
            print(f"    {o.file} {o.path}: {o.old!r} -> {o.new!r}")
        cmp = compared.get(c.candidate_id)
        if cmp and cmp.get("started"):
            f, s = cmp["frozen"], cmp["candidate"]
            print(f"    frozen    : {f['closed']} closed, avg {f['avg_ret']}, open {f['open']}")
            print(f"    candidate : {s['closed']} closed, avg {s['avg_ret']}, open {s['open']}")
            print("    " + ("ready to judge" if cmp["ready_to_judge"]
                            else "needs " + ", ".join(cmp["needs"])))  # fmt: skip
    return 0
