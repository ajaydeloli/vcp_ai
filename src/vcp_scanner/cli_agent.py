"""``vcp agent ...``: the MCP server for Claude Code and its notebook (AGENT_SPECIFICATION)."""

from __future__ import annotations

import argparse
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


def run_agent(args: argparse.Namespace) -> int:
    from vcp_scanner.agent.store import AgentStore

    cmd = getattr(args, "agent_command", None)
    if cmd == "serve":
        from vcp_scanner.agent.server import serve

        serve(args.config_dir, args.data_dir)
        return 0
    if cmd is None:
        _err("Usage: vcp agent {serve,notes,audit,seed,note} ...")
        return 1
    store = AgentStore(Path(args.db))
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
    _err(f"Unknown agent command: {cmd}")
    return 1
