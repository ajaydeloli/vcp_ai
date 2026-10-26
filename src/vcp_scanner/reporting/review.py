"""The review pack for the April 2027 review (AGENT_SPECIFICATION step A5; STRATEGY_SPECIFICATION
21.1, 21.3, 21.6; FUNDAMENTALS_SPECIFICATION 12).

Read-only: the serving copy and the agent's own file. For each paper strategy:

* the ledger's record (closed trades, win rate, average, profit factor), as stored;
* a replay of the frozen rules from the paper start to the ledger's last closed session, with
  the same engine the ledger uses: the paper portfolio's equity (10 slots, equal size, open
  positions marked to market), its return and maximum drawdown, against the equal-weight universe
  index over the same sessions; and whether the stored ledger agrees with the replay (21.3), with
  every DIVERGENCE the ledger recorded (data fixes that changed a paper trade, 21.1);
* the five criteria of 21.6 and the verdict: not judged (fewer than 30 closed trades), working,
  or not working (which criteria failed).

Then the strategy candidates' shadow results (step A4), the research done this cycle (every
hypothesis, so a lone success among many is read as such), the F7 finding, open questions, and
the decisions the owner makes. Before 2027-04-01 the pack is a preview.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb

from vcp_scanner.api.context import MAX_DRAWDOWN_PCT, MIN_CLOSED_TRADES, MIN_PROFIT_FACTOR
from vcp_scanner.api.reports import REVIEW_FROM
from vcp_scanner.data.providers._time import IST

PACK_VERSION = "review-pack-1"
PAPER_MAX_MONTHS = 12  # 21.6: fewer than 30 trades, the paper phase continues up to 12 months
F7_FINDING = (
    "F7 (development period 2022-02 to 2024-06, 45,161 setups): the fundamental score does not "
    "order outcomes at the 60-session trade horizon in any strategy with enough dates (all "
    "strategies: IC +0.000; top minus bottom fifth -0.11 pp within dates); VCP shows a small "
    "rank correlation over 5 to 20 sessions that fades by 60; the hard gate's advantage "
    "disappears within dates. Recommendation: keep fundamentals display only; do not add them "
    "to the score. The validation period was not opened for it "
    "(FUNDAMENTALS_SPECIFICATION 12)."
)


@dataclass(frozen=True, slots=True)
class _Ro:
    """What the repositories need of a store: a connection (here read-only)."""

    conn: duckdb.DuckDBPyConnection


def index_return(rows: list[Any], start: date, through: date) -> float | None:
    """The equal-weight universe index (the members' mean daily return, compounded) over the
    sessions after ``start`` up to ``through``, in %."""
    level, seen = 1.0, False
    for r in rows:
        if start < r.day <= through and r.mean_return is not None:
            level *= 1.0 + r.mean_return
            seen = True
    return (level - 1.0) * 100 if seen else None


def _equity_from(start: date, through: date, curve: list[tuple[date, float]],
                 sessions: list[date]) -> list[tuple[date, float]]:  # fmt: skip
    """The portfolio's equity on every session from the start: 1.0 before the first entry."""
    by_day = dict(curve)
    out, value = [], 1.0
    for d in sessions:
        if start <= d <= through:
            value = by_day.get(d, value)
            out.append((d, value))
    return out


def criteria(
    closed: int, pf: float | None, avg: float | None, mdd: float | None,
    port_ret: float | None, idx_ret: float | None, wins: int,
) -> list[dict[str, Any]]:  # fmt: skip
    """The five criteria of 21.6. ``met`` is None when not judged (fewer than 30 trades) or a
    number is missing."""
    judged = closed >= MIN_CLOSED_TRADES

    def met(ok: bool | None) -> bool | None:
        return None if not judged or ok is None else ok

    pf_ok = (wins > 0) if pf is None else pf >= MIN_PROFIT_FACTOR  # no losing trade: met
    return [
        {"name": "Closed trades", "required": f">= {MIN_CLOSED_TRADES}", "value": closed,
         "met": judged},
        {"name": "Profit factor", "required": f">= {MIN_PROFIT_FACTOR}", "value": pf,
         "met": met(pf_ok if closed else None)},
        {"name": "Average trade after costs", "required": "> 0 %", "value": avg,
         "met": met(None if avg is None else avg > 0)},
        {"name": "Portfolio max drawdown", "required": f"no worse than {MAX_DRAWDOWN_PCT:.0f} %",
         "value": mdd, "met": met(None if mdd is None else mdd >= MAX_DRAWDOWN_PCT)},
        {"name": "Portfolio return above the equal-weight universe index",
         "required": f"above {idx_ret:+.2f} %" if idx_ret is not None else "above the index",
         "value": port_ret,
         "met": met(None if port_ret is None or idx_ret is None else port_ret > idx_ret)},
    ]  # fmt: skip


def verdict(crit: list[dict[str, Any]], start: date) -> str:
    closed = int(crit[0]["value"])
    if closed < MIN_CLOSED_TRADES:
        until = date(start.year + (start.month - 1 + PAPER_MAX_MONTHS) // 12,
                     (start.month - 1 + PAPER_MAX_MONTHS) % 12 + 1, start.day)  # fmt: skip
        return (f"not judged: {closed} of {MIN_CLOSED_TRADES} closed trades; paper continues "
                f"up to {until}")  # fmt: skip
    failed = [c["name"] for c in crit if c["met"] is not True]
    return "working: meets every criterion" if not failed else (
        "not working: " + "; ".join(failed))  # fmt: skip


def strategy_review(ro: _Ro, cfg: Any, config_dir: str, sid: str, start: date,
                    defaults: Any) -> dict[str, Any]:  # fmt: skip
    """One strategy: the stored ledger, the replay to the ledger's last closed session, the
    criteria and the verdict (module docstring)."""
    from vcp_scanner.backtest.engine import EngineConfig, run_portfolio, run_signals
    from vcp_scanner.backtest.metrics import equity_stats
    from vcp_scanner.backtest.regime import regime_by_day
    from vcp_scanner.cli_backtest import _strategy_hash
    from vcp_scanner.config.loader import scan_config_hash
    from vcp_scanner.data.repositories.duckdb_backtest_repository import (
        DuckDBBacktestRepository,
    )
    from vcp_scanner.data.repositories.duckdb_paper_repository import DuckDBPaperRepository
    from vcp_scanner.domain.features import FEATURES_CALCULATION_VERSION
    from vcp_scanner.domain.snapshot import LIVE_SNAPSHOT_ID
    from vcp_scanner.paper.ledger import RULE_SET, derive_events, plan_update
    from vcp_scanner.paper.status import summarize
    from vcp_scanner.patterns.registry import load_runtime
    from vcp_scanner.research.outcomes import ENGINE_RULES

    chash = _strategy_hash(cfg, sid, config_dir)
    stored = DuckDBPaperRepository(ro).events(RULE_SET, sid, chash)  # type: ignore[arg-type]
    closed_days = [e.event_date for e in stored if e.event_type == "DAY_CLOSED"]
    out: dict[str, Any] = {"strategy_id": sid, "config_hash": chash[:12], "rule_set": RULE_SET}
    if not closed_days:
        crit = criteria(0, None, None, None, None, None, 0)
        return {**out, "through": None, "sessions": 0, "criteria": crit,
                "verdict": verdict(crit, start), "ledger": None, "replay": None}  # fmt: skip
    through = max(closed_days)
    ids = sorted({e.instrument_id for e in stored if e.event_type == "ENTRY" and e.instrument_id})
    closes = {str(a): float(b) for a, b in ro.conn.execute(
        "SELECT instrument_id, arg_max(close_adj, trade_date) FROM daily_prices_adjusted_current"
        " WHERE computed_from_snapshot_id = ? AND trade_date <= ? AND instrument_id IN"
        " (SELECT unnest(?)) GROUP BY 1", [LIVE_SNAPSHOT_ID, through, ids]).fetchall()
        if b is not None}  # fmt: skip
    s = summarize(stored, closes)
    wins = sum(1 for e in stored if e.event_type == "EXIT" and float(e.meta["ret_pct"]) > 0)
    # The replay: the ledger's own code path (paper update), to the same day.
    repo = DuckDBBacktestRepository(ro, LIVE_SNAPSHOT_ID)  # type: ignore[arg-type]
    rows = repo.breadth(start, through, scan_config_hash(cfg), FEATURES_CALCULATION_VERSION)
    sessions = [r.day for r in rows if start <= r.day <= through]
    rule = next(r for r in ENGINE_RULES if r.name == defaults.rule)
    ecfg = EngineConfig(
        rule,
        min_volume_ratio=cfg.strategy.vcp.breakout.min_volume_ratio,
        entry=defaults.entry,
        regime=regime_by_day(defaults.regime, rows),
    )
    rt = load_runtime(config_dir, sid)
    classes = [t for t in rt.tiers if rt.grade(t) >= rt.min_grade]
    signals = repo.signals(start - timedelta(days=7), through, chash, classes, None,
                           eligible_only=True, strategy_id=sid)  # fmt: skip
    bars = repo.bars(sorted({x.instrument_id for x in signals}), start, through, 0)
    trades, _events = run_signals(signals, dict(bars), ecfg, include_open=True)
    trades = [t for t in trades if t.entry_day >= start]
    port = run_portfolio(trades, dict(bars), ecfg)
    curve = _equity_from(start, through, port.equity, sessions)
    eq = equity_stats(curve) if len(curve) >= 2 else {"total_ret": 0.0, "max_drawdown": 0.0}
    idx = index_return(rows, start, through)
    derived = derive_events(sid, signals, bars, ecfg, start, through, sessions)
    check = plan_update(sid, derived, [e for e in stored if e.event_type != "DIVERGENCE"],
                        through)  # fmt: skip
    divergences = [{"recorded_on": e.event_date.isoformat(), **e.meta}
                   for e in stored if e.event_type == "DIVERGENCE"]  # fmt: skip
    crit = criteria(s.closed, s.profit_factor, s.avg_ret if s.closed else None,
                    eq["max_drawdown"], eq["total_ret"], idx, wins)  # fmt: skip
    return {
        **out, "through": through.isoformat(), "sessions": len(sessions),
        "ledger": {"closed": s.closed, "win_rate": s.win_rate if s.closed else None,
                   "avg_ret": s.avg_ret if s.closed else None,
                   "profit_factor": s.profit_factor, "open": len(s.open),
                   "skipped_no_slot": s.skipped, "divergences": divergences},
        "replay": {"portfolio_ret": eq["total_ret"], "max_drawdown": eq["max_drawdown"],
                   "index_ret": idx, "taken": len(port.taken), "skipped": port.skipped,
                   "avg_exposure": port.avg_exposure,
                   "agrees": check.divergence is None,
                   "differences": None if check.divergence is None else check.divergence.meta,
                   "equity": [(d.isoformat(), round(v, 6)) for d, v in curve]},
        "criteria": crit, "verdict": verdict(crit, start),
    }  # fmt: skip


def _agent(agent_db: Path) -> dict[str, Any]:
    """Research this cycle and open questions, from the agent's own file (read-only)."""
    from vcp_scanner.agent.lab import CYCLE, MAX_PER_STRATEGY

    out: dict[str, Any] = {"cycle": CYCLE, "max_per_strategy": MAX_PER_STRATEGY,
                           "hypotheses": [], "questions": [], "candidates": []}  # fmt: skip
    if not agent_db.exists():
        return out
    con = duckdb.connect(str(agent_db), read_only=True)

    def rows(sql: str, args: list[Any] | None = None) -> list[tuple[Any, ...]]:
        try:
            return list(con.execute(sql, args or []).fetchall())
        except duckdb.CatalogException:
            return []

    try:
        out["hypotheses"] = [
            {"id": r[0], "strategy": r[1], "title": r[2], "decision_rule": r[3], "status": r[4],
             "outcome": r[5], "conclusion": r[6],
             "runs": r[7], "validation_runs": r[8]}
            for r in rows(
                "SELECT h.hypothesis_id, h.strategy, h.title, h.decision_rule, h.status,"
                " h.outcome, h.conclusion, count(r.run_id),"
                " count(r.run_id) FILTER (WHERE r.window_name = 'validation')"
                " FROM agent_hypotheses h LEFT JOIN agent_runs r USING (hypothesis_id)"
                " WHERE h.cycle = ? GROUP BY ALL ORDER BY h.hypothesis_id", [CYCLE])
        ]  # fmt: skip
        out["questions"] = [
            {"id": r[0], "on": str(r[1])[:10], "text": r[2], "source": r[3]}
            for r in rows("SELECT note_id, created_at, text, source FROM agent_notes"
                          " WHERE kind = 'question' ORDER BY note_id DESC LIMIT 30")
        ]  # fmt: skip
        out["candidates"] = [
            {"candidate_id": r[0], "strategy_id": r[1], "candidate_version": r[2],
             "status": r[3], "start_date": None if r[4] is None else str(r[4])}
            for r in rows("SELECT candidate_id, strategy_id, candidate_version, status,"
                          " start_date FROM strategy_candidates ORDER BY candidate_id")
        ]  # fmt: skip
    finally:
        con.close()
    return out


def build_pack(serving: Path, config_dir: str, agent_db: Path, today: date) -> dict[str, Any]:
    """The whole pack as plain data (also what the agent's ``review_pack`` tool returns)."""
    from vcp_scanner.agent.shadow import comparisons
    from vcp_scanner.backtest.periods import load_backtest_config
    from vcp_scanner.cli_paper import _paper_strategies
    from vcp_scanner.config import load_scanner_config

    cfg = load_scanner_config(config_dir)
    bcfg = load_backtest_config(config_dir)
    start = next(p.start for p in bcfg.periods if p.role == "test")
    con = duckdb.connect(str(serving), read_only=True)
    try:
        ro = _Ro(con)
        strategies = [strategy_review(ro, cfg, config_dir, sid, start, bcfg.defaults)
                      for sid in _paper_strategies(config_dir, None)]  # fmt: skip
        try:
            shadow = comparisons(con, config_dir, today)
        except duckdb.CatalogException:  # a serving copy older than the shadow table
            shadow = []
        surv = sorted({str(r[0]) for r in con.execute(
            "SELECT DISTINCT survivorship_status FROM scan_runs WHERE scan_type ="
            " 'TREND_TEMPLATE' AND as_of_date >= ? AND survivorship_status IS NOT NULL",
            [start]).fetchall()})  # fmt: skip
        prices = con.execute("SELECT max(trade_date) FROM bhavcopy_files WHERE status = 'OK'"
                             ).fetchone()[0]  # type: ignore[index]  # fmt: skip
    finally:
        con.close()
    total = sum(int(s["criteria"][0]["value"]) for s in strategies)
    due = today >= REVIEW_FROM
    if not due:
        state = f"preview: the review is on the first weekly summary on or after {REVIEW_FROM}"
    elif total < MIN_CLOSED_TRADES:
        state = (f"due, but only {total} closed paper trades in total (fewer than "
                 f"{MIN_CLOSED_TRADES}): 21.6 postpones the review")  # fmt: skip
    else:
        state = "due: decide per strategy"
    agent = _agent(agent_db)
    decisions = []
    for s in strategies:
        if int(s["criteria"][0]["value"]) < MIN_CLOSED_TRADES:
            decisions.append(f"{s['strategy_id']}: {s['verdict']} (nothing to decide yet).")
        else:
            decisions.append(f"{s['strategy_id']}: real money (a separate decision, with "
                             "position sizing), continue on paper, or retire. Pack: "
                             f"{s['verdict']}.")  # fmt: skip
    for c in shadow:
        if c.get("started"):
            ready = c.get("ready_to_judge")
            decisions.append(
                f"Candidate {c['candidate_id']}: " + ("promote (a new version; restarts that "
                "strategy's paper clock), keep shadowing, or retire." if ready else
                "not ready to judge (" + ", ".join(c.get("needs", [])) + "): keep shadowing "
                "or retire."))  # fmt: skip
    decisions.append("Fundamentals: keep them display only (the F7 recommendation), or write a "
                     "new hypothesis down before testing anything else.")  # fmt: skip
    return {
        "version": PACK_VERSION, "made_on": today.isoformat(),
        "made_at": datetime.now(IST).isoformat(timespec="seconds"),
        "review_from": REVIEW_FROM.isoformat(), "paper_start": start.isoformat(),
        "prices_to": None if prices is None else str(prices), "state": state,
        "total_closed": total,
        "rules": {"entry": bcfg.defaults.entry, "regime": bcfg.defaults.regime,
                  "exit": bcfg.defaults.rule, "positions": 10},
        "thresholds": {"min_closed_trades": MIN_CLOSED_TRADES,
                       "min_profit_factor": MIN_PROFIT_FACTOR,
                       "max_drawdown_pct": MAX_DRAWDOWN_PCT},
        "strategies": strategies, "candidates": shadow, "candidate_list": agent["candidates"],
        "research": {"cycle": agent["cycle"], "max_per_strategy": agent["max_per_strategy"],
                     "hypotheses": agent["hypotheses"]},
        "f7": F7_FINDING, "questions": agent["questions"], "decisions": decisions,
        "survivorship": surv,
    }  # fmt: skip


# ------------------------------------------------------------------------------ HTML


def _pct(v: Any, digits: int = 2) -> str:
    return "n/a" if v is None else f"{float(v):+.{digits}f} %"


def _met(v: bool | None) -> str:
    return ('<b class="ok">met</b>' if v is True else '<b class="warn">not met</b>'
            if v is False else '<span class="muted">not judged</span>')  # fmt: skip


def render_html(pack: dict[str, Any]) -> str:
    from vcp_scanner.reporting.build import esc, num, page, table

    sec: list[str] = [
        f"<p><b>{esc(pack['state'])}</b>. Paper since {pack['paper_start']}, prices to "
        f"{esc(pack['prices_to'])}; rules {esc(pack['rules']['entry'])}, "
        f"{esc(pack['rules']['regime'])}, {esc(pack['rules']['exit'])}, "
        f"{pack['rules']['positions']} positions. Criteria set before any paper result "
        "(STRATEGY_SPECIFICATION 21.6).</p>"
    ]
    rows = []
    for s in pack["strategies"]:
        led, rep = s["ledger"] or {}, s["replay"] or {}
        mdd = rep.get("max_drawdown")
        rows.append([esc(s["strategy_id"]), esc(s["verdict"]), esc(s["through"] or "-"),
                     str(led.get("closed", 0)), num(led.get("win_rate"), 1, " %"),
                     _pct(led.get("avg_ret")), num(led.get("profit_factor")),
                     _pct(rep.get("portfolio_ret")),
                     "n/a" if mdd is None else f"{mdd:.1f} %",
                     _pct(rep.get("index_ret"))])  # fmt: skip
    sec.append(
        "<h2>Verdict per strategy</h2>"
        + table(
            [
                "Strategy",
                "Verdict",
                "Ledger to",
                "Closed",
                "Win rate",
                "Average",
                "Profit factor",
                "Portfolio",
                "Max drawdown",
                "Universe index",
            ],
            rows,
            3,
        )
    )
    for s in pack["strategies"]:
        crit = [[esc(c["name"]), esc(c["required"]),
                 esc("-" if c["value"] is None else (f"{c['value']:.2f}" if isinstance(
                     c["value"], float) else c["value"])), _met(c["met"])]
                for c in s["criteria"]]  # fmt: skip
        rep = s["replay"]
        agree = (
            "<p class='muted'>No ledger yet.</p>"
            if rep is None
            else "<p class='ok'>The stored ledger agrees with a replay of the frozen rules to "
            f"{s['through']}.</p>"
            if rep["agrees"]
            else "<p class='warn'>The stored ledger differs from the replay: "
            f"{esc(json.dumps(rep['differences']))}. Explain before deciding (21.3).</p>"
        )
        divs = (s["ledger"] or {}).get("divergences") or []
        div = ("" if not divs else "<p class='warn'>Data fixes that changed a paper trade "
               f"(DIVERGENCE rows): {esc(json.dumps(divs))}</p>")  # fmt: skip
        sec.append(
            f"<h3>{esc(s['strategy_id'])} ({esc(s['config_hash'])})</h3>"
            + table(["Criterion", "Required", "Value", ""], crit)
            + agree
            + div
        )
    cands = [c for c in pack["candidates"] if c.get("started")]
    if cands:
        body = [[esc(c["candidate_id"]), esc(c["start_date"]),
                 f"{c['frozen']['closed']} / {_pct(c['frozen']['avg_ret'])}",
                 f"{c['candidate']['closed']} / {_pct(c['candidate']['avg_ret'])}",
                 "n/a" if c["avg_ret_diff"] is None else
                 f"{c['avg_ret_diff']:+.2f} pp ({c['avg_ret_diff_lo']:+.2f} to "
                 f"{c['avg_ret_diff_hi']:+.2f})",
                 "ready to judge" if c["ready_to_judge"] else esc(", ".join(c["needs"]))]
                for c in cands]  # fmt: skip
        sec.append(
            "<h2>Strategy candidates (shadow ledger)</h2>"
            + table(
                [
                    "Candidate",
                    "Since",
                    "Frozen: closed / avg",
                    "Candidate: closed / avg",
                    "Difference (95 % range)",
                    "Judge",
                ],
                body,
            )
        )
    else:
        sec.append("<h2>Strategy candidates (shadow ledger)</h2><p class='muted'>No candidate "
                   "has started.</p>")  # fmt: skip
    hyps = pack["research"]["hypotheses"]
    tried: dict[str, int] = {}
    for h in hyps:
        tried[h["strategy"]] = tried.get(h["strategy"], 0) + 1
    word = "hypothesis" if len(hyps) == 1 else "hypotheses"
    cap = pack["research"]["max_per_strategy"]
    sec.append(
        f"<h2>Research this cycle ({esc(pack['research']['cycle'])})</h2>"
        f"<p>{len(hyps)} {word} registered (at most {cap} per strategy): "
        + (", ".join(f"{esc(k)} {v}" for k, v in sorted(tried.items())) or "none")
        + ". Every one is listed, supported or not, so a "
        "lone success among several tries is read as such.</p>"
        + table(
            ["H", "Strategy", "Title", "Runs (validation)", "Outcome", "Conclusion"],
            [
                [
                    str(h["id"]),
                    esc(h["strategy"]),
                    esc(h["title"]),
                    f"{h['runs']} ({h['validation_runs']})",
                    esc(h["outcome"] or h["status"]),
                    esc(h["conclusion"] or ""),
                ]
                for h in hyps
            ],
        )  # fmt: skip
    )
    sec.append(f"<h2>Fundamentals (F7)</h2><p>{esc(pack['f7'])}</p>")
    if pack["questions"]:
        sec.append(
            "<h2>Open questions (notebook)</h2><ul>"
            + "".join(f"<li>{esc(q['on'])}: {esc(q['text'])}</li>" for q in pack["questions"])
            + "</ul>"
        )
    sec.append("<h2>Decisions for the owner</h2><ol>" + "".join(
        f"<li>{esc(d)}</li>" for d in pack["decisions"]) + "</ol><p class='muted'>Nothing here "
        "changes a strategy: a decision is recorded in the notebook and applied as a new "
        "version.</p>")  # fmt: skip
    sec.append("<h2>Limits</h2><ul><li>Survivorship of the paper-period scans: "
               f"{esc(', '.join(pack['survivorship']) or 'n/a')}.</li><li>The portfolio and "
               "index figures come from a replay of the frozen rules on the stored scans; the "
               "ledger is the record of what was decided each evening.</li><li>Six months of "
               "paper trading is one market phase; a verdict on 30 trades has a wide "
               "range.</li></ul>")  # fmt: skip
    state = pack["state"]
    title = ("Review pack" if state.startswith("due:") else "Review pack (postponed)"
             if state.startswith("due") else "Review pack (preview)")  # fmt: skip
    when = datetime.fromisoformat(pack["made_at"])
    return page(f"{title} {pack['made_on']}", sec, when)


def write_review(serving: Path, config_dir: str, agent_db: Path, out_dir: Path,
                 today: date | None = None) -> Path:  # fmt: skip
    """``reports/review/<date>.html`` and the same pack as ``.json``."""
    day = today or datetime.now(IST).date()
    pack = build_pack(serving, config_dir, agent_db, day)
    folder = out_dir / "review"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{day}.html"
    path.write_text(render_html(pack), encoding="utf-8")
    (folder / f"{day}.json").write_text(json.dumps(pack, indent=1, default=str),
                                        encoding="utf-8")  # fmt: skip
    return path
