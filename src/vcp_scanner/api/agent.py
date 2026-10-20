"""The agent's state for the dashboard's Agent page (AGENT_SPECIFICATION §5, step A3 stage 2):
proposals and the owner's decisions, the AI watchlist, the research lab and recent notebook
entries. Read-only: the agent's own file (``data/agent/agent.duckdb``) is opened read-only per
request. Deciding stays in the owner's terminal (``vcp agent approve|reject``): the page shows
the command to copy, it has no approve button, because any local program could press one.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb

from vcp_scanner.api import models as m
from vcp_scanner.data.providers._time import IST

MAX_PROPOSALS = 100
MAX_NOTES = 20


def _rows(con: Any, sql: str, args: list[Any] | None = None) -> list[tuple[Any, ...]]:
    try:
        return list(con.execute(sql, args or []).fetchall())
    except duckdb.CatalogException:  # an older agent file without this table
        return []


def agent_state(path: Path) -> m.AgentResponse:
    if not path.exists():
        return m.AgentResponse(
            as_of=None, data_time=datetime.now(IST), available=False,
            reason="The agent has not run yet (no data/agent/agent.duckdb).", pending=0,
            proposals=[], ai_watchlist=[], budget_left={}, hypotheses=[], notes=[],
        )  # fmt: skip
    try:
        con = duckdb.connect(str(path), read_only=True)
    except duckdb.Error as exc:
        return m.AgentResponse(
            as_of=None, data_time=datetime.now(IST), available=False,
            reason=f"The agent's database cannot be read right now: {exc}", pending=0,
            proposals=[], ai_watchlist=[], budget_left={}, hypotheses=[], notes=[],
        )  # fmt: skip
    try:
        proposals = [
            m.AgentProposal(
                proposal_id=int(r[0]), created_at=r[1], kind=str(r[2]), title=str(r[3]),
                change=json.loads(r[4]), rationale=str(r[5]), evidence=str(r[6]),
                status=str(r[7]), decided_by=r[8], decided_at=r[9], comment=r[10],
                result=r[11],
                approve_command=f"vcp agent approve {int(r[0])}" if r[7] == "PENDING" else None,
                reject_command=(f'vcp agent reject {int(r[0])} "reason"'
                                if r[7] == "PENDING" else None),
            )
            for r in _rows(con,
                "SELECT proposal_id, created_at, kind, title, payload_json, rationale, evidence,"
                " status, decided_by, decided_at, comment, apply_result FROM agent_proposals"
                " ORDER BY (status = 'PENDING') DESC, proposal_id DESC LIMIT ?",
                [MAX_PROPOSALS])
        ]  # fmt: skip
        watch = [
            m.AgentWatch(symbol=str(r[0]), since=r[1], proposal_id=int(r[2]), reason=str(r[3]))
            for r in _rows(con, "SELECT symbol, added_at, proposal_id, reason FROM agent_watchlist"
                                " ORDER BY symbol")
        ]  # fmt: skip
        runs: dict[int, list[m.AgentRun]] = {}
        run_sql = ("SELECT hypothesis_id, run_id, kind, window_name, start_date, end_date, ok,"
                   " summary, error FROM agent_runs ORDER BY run_id")
        for r in _rows(con, run_sql):
            runs.setdefault(int(r[0]), []).append(m.AgentRun(
                run_id=int(r[1]), kind=str(r[2]), window=str(r[3]), start=r[4], end=r[5],
                ok=r[6], summary=r[7] or None, error=r[8]))  # fmt: skip
        hyps = [
            m.AgentHypothesis(
                hypothesis_id=int(r[0]), created_at=r[1], strategy=str(r[2]), title=str(r[3]),
                metric=str(r[4]), decision_rule=str(r[5]), status=str(r[6]), outcome=r[7],
                conclusion=r[8], runs=runs.get(int(r[0]), []),
            )
            for r in _rows(con, "SELECT hypothesis_id, created_at, strategy, title, metric,"
                                " decision_rule, status, outcome, conclusion FROM agent_hypotheses"
                                " ORDER BY hypothesis_id DESC")
        ]  # fmt: skip
        from vcp_scanner.agent.lab import CYCLE, MAX_PER_STRATEGY, STRATEGIES

        used = dict(_rows(con, "SELECT strategy, count(*) FROM agent_hypotheses WHERE cycle = ?"
                               " GROUP BY 1", [CYCLE]))  # fmt: skip
        budget = {s: MAX_PER_STRATEGY - int(used.get(s, 0)) for s in STRATEGIES}
        notes = [
            m.AgentNote(
                note_id=int(r[0]), created_at=r[1], kind=str(r[2]), text=str(r[3]), source=str(r[4])
            )  # fmt: skip
            for r in _rows(
                con,
                "SELECT note_id, created_at, kind, text, source FROM agent_notes"
                " WHERE kind IN ('decision', 'finding') ORDER BY note_id DESC"
                " LIMIT ?",
                [MAX_NOTES],
            )  # fmt: skip
        ]
    finally:
        con.close()
    return m.AgentResponse(
        as_of=None, data_time=datetime.fromtimestamp(path.stat().st_mtime, IST), available=True,
        reason=None, pending=sum(p.status == "PENDING" for p in proposals), proposals=proposals,
        ai_watchlist=watch, budget_left=budget, hypotheses=hyps, notes=notes,
    )  # fmt: skip
