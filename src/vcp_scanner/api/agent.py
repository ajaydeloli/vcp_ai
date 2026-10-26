"""The agent's state for the dashboard's Agent page (AGENT_SPECIFICATION §5, step A3 stage 2):
proposals and the owner's decisions, the AI watchlist, the research lab and recent notebook
entries. Read-only: the agent's own file (``data/agent/agent.duckdb``) is opened read-only per
request. Deciding stays in the owner's terminal (``vcp agent approve|reject``): the page shows
the command to copy, it has no approve button, because any local program could press one.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb

from vcp_scanner.api import models as m
from vcp_scanner.data.providers._time import IST

MAX_PROPOSALS = 100
MAX_NOTES = 20
PROMOTION_MIN_TRADES = 30  # AGENT_SPECIFICATION section 7.2 (owner)
PROMOTION_MIN_MONTHS = 3


def _rows(con: Any, sql: str, args: list[Any] | None = None) -> list[tuple[Any, ...]]:
    try:
        return list(con.execute(sql, args or []).fetchall())
    except duckdb.CatalogException:  # an older agent file without this table
        return []


MAX_DIFF_ROWS = 40


def agent_state(path: Path, today: date | None = None, config_dir: str | Path | None = None,
                serving: Any = None) -> m.AgentResponse:  # fmt: skip
    """``today`` = the app's clock (the review countdown); the real date when not given.
    ``config_dir`` and ``serving`` (the serving copy, ``ServingDb``) add the strategy
    candidates' shadow comparison (step A4)."""
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
        run_sql = (
            "SELECT hypothesis_id, run_id, kind, window_name, start_date, end_date, ok,"
            " summary, error, params_json, result_json FROM agent_runs ORDER BY run_id"
        )
        for r in _rows(con, run_sql):
            runs.setdefault(int(r[0]), []).append(m.AgentRun(
                run_id=int(r[1]), kind=str(r[2]), window=str(r[3]), start=r[4], end=r[5],
                ok=r[6], summary=r[7] or None, error=r[8],
                **_details(str(r[2]), r[9], r[10])))  # fmt: skip
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
        cands = _candidates(con, today or datetime.now(IST).date(), config_dir, serving)
    finally:
        con.close()
    return m.AgentResponse(
        as_of=None, data_time=datetime.fromtimestamp(path.stat().st_mtime, IST), available=True,
        reason=None, pending=sum(p.status == "PENDING" for p in proposals), proposals=proposals,
        ai_watchlist=watch, budget_left=budget, hypotheses=hyps, notes=notes, review=review(today),
        candidates=cands,
    )  # fmt: skip


def _candidates(con: Any, today: date, config_dir: str | Path | None,
                serving: Any) -> list[m.AgentCandidate]:  # fmt: skip
    """Built candidates (``strategy_candidates``) with their changes from the proposal, the
    scan diff and, for one on main that has started, the shadow comparison."""
    rows = _rows(con,
        "SELECT c.candidate_id, c.strategy_id, c.base_version, c.candidate_version, c.status,"
        " c.proposal_id, c.start_date, c.diff_json, p.payload_json FROM strategy_candidates c"
        " LEFT JOIN agent_proposals p USING (proposal_id)"
        " ORDER BY (c.status = 'active') DESC, c.updated_at DESC")  # fmt: skip
    if not rows:
        return []
    diffs: dict[str, list[m.CandidateDiffRow]] = {}
    for r in _rows(con,
                   "SELECT candidate_id, as_of_date, symbol, change, frozen_class,"
                   " candidate_class, frozen_score, candidate_score FROM ("
                   " SELECT *, row_number() OVER (PARTITION BY candidate_id ORDER BY as_of_date"
                   " DESC, change, symbol) AS n FROM candidate_scan_diff) WHERE n <= ?",
                   [MAX_DIFF_ROWS]):  # fmt: skip
        diffs.setdefault(str(r[0]), []).append(
            m.CandidateDiffRow(
                date=r[1],
                symbol=str(r[2]),
                change=str(r[3]),
                frozen_class=r[4],
                candidate_class=r[5],
                frozen_score=_num(r[6]),
                candidate_score=_num(r[7]),
            )
        )
    on_main: dict[str, Any] = {}
    if config_dir is not None:
        from vcp_scanner.agent.candidates import load_candidates

        try:
            on_main = {c.candidate_id: c for c in load_candidates(config_dir)}
        except Exception:  # a broken candidate file must not break the page
            on_main = {}
    out = []
    for cid, sid, base, version, status, pid, start, diff_json, payload_json in rows:
        payload = _load(payload_json) or {}
        shadow, error = None, None
        cand = on_main.get(str(cid))
        if cand is not None and cand.start_date is not None and serving is not None:
            try:
                from vcp_scanner.agent.shadow import comparison

                with serving.cursor() as cur:
                    cmp = comparison(cur, str(config_dir), cand, today)
                shadow = m.CandidateShadow(**{k: cmp[k] for k in m.CandidateShadow.model_fields})
            except Exception as exc:  # e.g. a serving copy older than the shadow table
                error = f"{type(exc).__name__}: {str(exc)[:200]}"
        out.append(m.AgentCandidate(
            candidate_id=str(cid), strategy_id=str(sid), base_version=str(base),
            candidate_version=str(version), status=str(status), proposal_id=int(pid),
            hypothesis_id=payload.get("hypothesis_id"),
            start_date=cand.start_date if cand is not None else start,
            changes=list(payload.get("overrides", [])), scan_diff=_load(diff_json),
            diff_rows=diffs.get(str(cid), []), shadow=shadow, shadow_error=error,
        ))  # fmt: skip
    return out


def review(today: date | None = None) -> m.AgentReview:
    from vcp_scanner.agent.lab import CYCLE, MAX_PER_STRATEGY
    from vcp_scanner.api.reports import REVIEW_FROM

    now = today or datetime.now(IST).date()
    return m.AgentReview(
        cycle=CYCLE, review_from=REVIEW_FROM, days_to_review=max(0, (REVIEW_FROM - now).days),
        max_per_strategy=MAX_PER_STRATEGY, promotion_min_trades=PROMOTION_MIN_TRADES,
        promotion_min_months=PROMOTION_MIN_MONTHS,
    )  # fmt: skip


def _load(raw: Any) -> Any:
    if raw is None or raw == "":
        return None
    try:
        return json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return None


def _num(x: Any) -> float | None:
    return float(x) if isinstance(x, int | float) and not isinstance(x, bool) else None


def _change(params: Any) -> str | None:
    """A what-if change in words: weights {pattern: 60, rs: 0} -> "pattern 60, rs 0"."""
    if not isinstance(params, dict):
        return None
    parts = []
    for key in ("weights", "sub_weights", "bounds"):
        val = params.get(key)
        if isinstance(val, dict):
            parts += [
                f"{k} {v:g}" if isinstance(v, int | float) else f"{k} {v}" for k, v in val.items()
            ]
    return ", ".join(parts) or None


def _group(g: Any, name: str) -> m.StudyGroup | None:
    if not isinstance(g, dict):
        return None
    n = _num(g.get("n"))
    return m.StudyGroup(group=str(g.get("group", name)), n=None if n is None else int(n),
                        mean_ret_pct=_num(g.get("mean_ret_pct")), win_rate=_num(g.get("win_rate")),
                        small_sample=bool(g.get("small_sample", False)))  # fmt: skip


def _pick(d: dict[str, Any], suffix: str) -> Any:
    """The value of the ``top_<n>...<suffix>`` key (the N is the run's ``top_n``)."""
    return next((v for k, v in d.items() if k.startswith("top_") and k.endswith(suffix)), None)


def _top(d: dict[str, Any]) -> float | None:
    return _num(_pick(d, "_per_day_mean_pct"))


def _details(kind: str, params_json: Any, result_json: Any) -> dict[str, Any]:
    """Chartable figures of a run from its stored result; a result in an unknown shape gives
    none (the summary line still shows)."""
    params, res = _load(params_json), _load(result_json)
    if not isinstance(res, dict):
        return {}
    if kind == "what_if":
        b, w = res.get("baseline") or {}, res.get("what_if") or {}
        rc = res.get("ranking_change") or {}
        top_n = params.get("top_n") if isinstance(params, dict) else None
        kept = next(
            (v for k, v in rc.items() if k.startswith("top_") and k.endswith("_kept_share")), None
        )
        return {
            "change": _change(params),
            "what_if": m.WhatIfMetrics(
                days=w.get("days_ranked"), setups=w.get("setups_with_outcome"),
                ic=_num(w.get("ic_mean")), ic_t=_num(w.get("ic_t")),
                top_per_day_pct=_top(w),
                top_minus_bottom_fifth_pct=_num(w.get("top_minus_bottom_fifth_pct")),
                all_mean_pct=_num(w.get("all_mean_pct")),
                baseline_ic=_num(b.get("ic_mean")), baseline_ic_t=_num(b.get("ic_t")),
                baseline_top_per_day_pct=_top(b),
                baseline_top_minus_bottom_fifth_pct=_num(b.get("top_minus_bottom_fifth_pct")),
                top_kept_share=_num(kept),
                horizon=params.get("horizon") if isinstance(params, dict) else None,
                top_n=top_n if isinstance(top_n, int) else None,
            ),
        }  # fmt: skip
    if kind == "study":
        groups = [g for g in (_group(x, "") for x in res.get("groups") or []) if g]
        return {"study_overall": _group({**(res.get("overall") or {}), "group": "All"}, "All"),
                "study_groups": groups}  # fmt: skip
    if kind == "backtest" and res.get("backtest_id"):
        return {"backtest_id": str(res["backtest_id"])}
    return {}
