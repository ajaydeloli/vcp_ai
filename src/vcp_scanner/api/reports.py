"""Status, activity and paper panel (FRONTEND_SPECIFICATION 67.3), from the serving copy."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from vcp_scanner.api import models as m
from vcp_scanner.api.context import (
    MAX_DRAWDOWN_PCT,
    MIN_CLOSED_TRADES,
    MIN_PROFIT_FACTOR,
    Context,
    StrategySpec,
)
from vcp_scanner.api.queries import Cur, _f, latest_prices_date, latest_scan, ledger_through
from vcp_scanner.backup import BACKUP_RE, list_backups
from vcp_scanner.data.providers._time import IST
from vcp_scanner.domain.snapshot import LIVE_SNAPSHOT_ID as LIVE
from vcp_scanner.paper.ledger import RULE_SET, PaperEvent
from vcp_scanner.paper.status import summarize

REVIEW_FROM = date(2027, 4, 1)  # STRATEGY_SPECIFICATION 21.6
PUBLISH_HOUR = 20  # today's prices are not expected before the evening run has finished


def _last_line(path: Path) -> str | None:
    try:
        lines = [x for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    except OSError:
        return None
    return lines[-1] if lines else None


def _backup(ctx: Context, now: datetime) -> tuple[str | None, float | None]:
    found = list_backups(ctx.data_dir / "backups", "vcp_scanner")
    if not found:
        return None, None
    match = BACKUP_RE.match(found[0].name)
    if match is None:
        return found[0].name, None
    made = datetime.strptime(match.group("ts"), "%Y%m%d_%H%M%S").replace(tzinfo=UTC)
    return found[0].name, (now - made).total_seconds() / 86400.0


def _missing_sessions(cur: Cur, last: date, now: datetime) -> int:
    """Weekdays after the last prices date that are not recorded as non-sessions (holidays); the
    current day only counts once its evening run is due."""
    holidays = {
        r[0]
        for r in cur.execute(
            "SELECT trade_date FROM bhavcopy_files WHERE status = 'NO_SESSION' AND trade_date > ?",
            [last],
        ).fetchall()
    }
    last_due = now.date() if now.hour >= PUBLISH_HOUR else now.date() - timedelta(days=1)
    days = (last + timedelta(days=k) for k in range(1, (last_due - last).days + 1))
    return sum(1 for d in days if d.weekday() < 5 and d not in holidays)


def status(cur: Cur, ctx: Context, now: datetime, data_time: datetime) -> m.StatusResponse:
    prices = latest_prices_date(cur)
    per = [
        m.StrategyDates(strategy_id=s.strategy_id, latest_scan=latest_scan(cur, s),
                        ledger_through=ledger_through(cur, RULE_SET, s))
        for s in ctx.strategies
    ]  # fmt: skip
    warnings: list[str] = []
    if prices is not None:
        missing = _missing_sessions(cur, prices, now)
        if missing >= 2:
            warnings.append(f"No new prices for {missing} sessions (latest {prices}).")
    newest = max((p.latest_scan for p in per if p.latest_scan), default=None)
    for p in per:
        if newest and (p.latest_scan is None or p.latest_scan < newest):
            warnings.append(f"{p.strategy_id}: no scan for {newest} (latest {p.latest_scan}).")
        if newest and (p.ledger_through is None or p.ledger_through < newest):
            warnings.append(f"{p.strategy_id}: paper ledger not updated for {newest} "
                            f"(through {p.ledger_through}).")  # fmt: skip
    name, age = _backup(ctx, now)
    if name is None:
        warnings.append("No database backup found.")
    elif age is None or age > 7:
        warnings.append(f"Newest database backup is older than 7 days ({name}).")
    return m.StatusResponse(
        as_of=prices, data_time=data_time, prices_date=prices, strategies=per,
        daily_run=_last_line(ctx.data_dir / "logs" / "daily_runs.log"), newest_backup=name,
        backup_age_days=age, warnings=warnings,
    )  # fmt: skip


# --- paper ----------------------------------------------------------------------------------


def _events(cur: Cur, spec: StrategySpec) -> list[PaperEvent]:
    rows = cur.execute(
        "SELECT strategy_id, instrument_id, event_date, event_type, price, scan_date,"
        " metadata_json FROM paper_events WHERE rule_set = ? AND strategy_id = ?"
        " AND config_hash = ? ORDER BY event_date, recorded_at, event_id",
        [RULE_SET, spec.strategy_id, spec.config_hash],
    ).fetchall()
    return [PaperEvent(r[0], r[1], r[2], r[3], r[4], r[5], json.loads(r[6] or "{}"))
            for r in rows]  # fmt: skip


def _symbols(cur: Cur, ids: list[str]) -> dict[str, str]:
    if not ids:
        return {}
    rows = cur.execute(
        "SELECT instrument_id, symbol FROM instruments WHERE instrument_id IN (SELECT unnest(?))",
        [ids],
    ).fetchall()
    return {str(a): str(b) for a, b in rows}


def paper(cur: Cur, ctx: Context, data_time: datetime) -> m.PaperResponse:
    out: list[m.PaperStrategy] = []
    through: list[date] = []
    for spec in ctx.strategies:
        events = _events(cur, spec)
        ids = sorted(
            {e.instrument_id for e in events if e.event_type == "ENTRY" and e.instrument_id}
        )
        closes = {
            str(a): float(b)
            for a, b in cur.execute(
                "SELECT instrument_id, arg_max(close_adj, trade_date) FROM"
                " daily_prices_adjusted_current WHERE computed_from_snapshot_id = ?"
                " AND instrument_id IN (SELECT unnest(?)) GROUP BY 1",
                [LIVE, ids],
            ).fetchall()
            if b is not None
        }  # fmt: skip
        s = summarize(events, closes)
        names = _symbols(cur, ids)
        n = s.closed
        pf = s.profit_factor
        judged = n >= MIN_CLOSED_TRADES
        criteria = [
            m.Criterion(name="Closed trades", required=f">= {MIN_CLOSED_TRADES}",
                        value=float(n), met=judged),
            m.Criterion(name="Profit factor", required=f">= {MIN_PROFIT_FACTOR}", value=pf,
                        met=(pf >= MIN_PROFIT_FACTOR) if judged and pf is not None else None),
            m.Criterion(name="Average trade after costs", required="> 0 %",
                        value=s.avg_ret if n else None,
                        met=(s.avg_ret > 0) if judged else None),
            m.Criterion(name="Portfolio max drawdown",
                        required=f"no worse than {MAX_DRAWDOWN_PCT:.0f} %", value=None, met=None),
            m.Criterion(name="Portfolio return above the equal-weight universe index",
                        required="above", value=None, met=None),
        ]  # fmt: skip
        if s.through:
            through.append(s.through)
        out.append(
            m.PaperStrategy(
                strategy_id=spec.strategy_id, ledger_through=s.through, closed=n,
                win_rate_pct=s.win_rate if n else None, avg_return_pct=s.avg_ret if n else None,
                profit_factor=pf,
                open=[
                    m.OpenPosition(
                        instrument_id=o.instrument_id, symbol=names.get(o.instrument_id,
                                                                        o.instrument_id),
                        entry_day=o.entry_day, entry=o.entry, stop=o.stop, last=o.last,
                        open_pct=None if o.last is None else o.open_pct,
                    )
                    for o in s.open
                ],
                skipped_no_slot=s.skipped, divergences=s.divergences, criteria=criteria,
            )
        )  # fmt: skip
    return m.PaperResponse(
        as_of=max(through) if through else None, data_time=data_time, rule_set=RULE_SET,
        review_from=REVIEW_FROM, min_closed_trades=MIN_CLOSED_TRADES,
        open_positions=sum(len(p.open) for p in out), strategies=out,
    )  # fmt: skip


# --- activity -------------------------------------------------------------------------------


def _paper_text(kind: str, price: float | None, meta: dict[str, Any]) -> str:
    if kind == "ENTRY":
        stop = meta.get("stop")
        return f"Paper entry at {price:.2f}" + (f", stop {float(stop):.2f}" if stop else "")
    if kind == "EXIT":
        return (
            f"Paper exit ({meta.get('kind')}), {float(meta.get('ret_pct', 0)):+.2f} % after costs"
        )
    if kind == "SKIPPED_NO_SLOT":
        return "Paper entry skipped: no free slot"
    return "Paper ledger divergence recorded"


def _run_time(line: str) -> tuple[date, datetime] | None:
    try:
        stamp = datetime.strptime(line.split(" IST", 1)[0], "%Y-%m-%d %H:%M").replace(tzinfo=IST)
    except ValueError:
        return None
    return stamp.date(), stamp


def activity(
    cur: Cur, ctx: Context, end: date, days: int, data_time: datetime
) -> m.ActivityResponse:
    start = end - timedelta(days=days)
    events: list[m.ActivityEvent] = []
    for spec in ctx.strategies:
        sid = spec.strategy_id
        for _iid, sym, d, pivot, vol in cur.execute(
            "SELECT b.instrument_id, coalesce(i.symbol, b.instrument_id), b.breakout_date,"
            " b.pivot_price, b.volume_ratio FROM breakout_events b LEFT JOIN instruments i"
            " USING (instrument_id) WHERE b.strategy_id = ? AND b.config_hash = ?"
            " AND b.breakout_date BETWEEN ? AND ?",
            [sid, spec.config_hash, start, end],
        ).fetchall():
            text = f"Breakout above pivot {float(pivot):.2f}" if pivot else "Breakout"
            if vol:
                text += f", volume {float(vol):.1f}x average"
            events.append(m.ActivityEvent(day=d, time=None, kind="BREAKOUT", strategy_id=sid,
                                          symbol=str(sym), text=text))  # fmt: skip
        for iid, sym, d, kind, price, meta in cur.execute(
            "SELECT e.instrument_id, coalesce(i.symbol, e.instrument_id), e.event_date,"
            " e.event_type, e.price, e.metadata_json FROM paper_events e LEFT JOIN instruments i"
            " USING (instrument_id) WHERE e.rule_set = ? AND e.strategy_id = ?"
            " AND e.config_hash = ? AND e.event_type IN"
            " ('ENTRY', 'EXIT', 'SKIPPED_NO_SLOT', 'DIVERGENCE') AND e.event_date BETWEEN ? AND ?",
            [RULE_SET, sid, spec.config_hash, start, end],
        ).fetchall():
            events.append(
                m.ActivityEvent(day=d, time=None, kind=f"PAPER_{kind}", strategy_id=sid,
                                symbol=None if iid is None else str(sym),
                                text=_paper_text(kind, _f(price), json.loads(meta or "{}")))
            )  # fmt: skip
    for scan_type, sid, d, done in cur.execute(
        "SELECT scan_type, strategy_id, as_of_date, max(completed_at) FROM scan_runs"
        " WHERE status = 'COMPLETED' AND as_of_date BETWEEN ? AND ?"
        " GROUP BY scan_type, strategy_id, as_of_date",
        [start, end],
    ).fetchall():
        events.append(
            m.ActivityEvent(day=d, time=done.astimezone(IST) if done else None, kind="SCAN",
                            strategy_id=sid, symbol=None,
                            text=f"{scan_type.replace('_', ' ').title()} scan for {d} completed")
        )  # fmt: skip
    log = ctx.data_dir / "logs" / "daily_runs.log"
    try:
        lines = log.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        parsed = _run_time(line)
        if parsed and start <= parsed[0] <= end:
            events.append(m.ActivityEvent(day=parsed[0], time=parsed[1], kind="DAILY_RUN",
                                          strategy_id=None, symbol=None,
                                          text=line.split(" | ", 1)[-1]))  # fmt: skip
    events.sort(key=lambda e: (e.day, e.time or datetime.min.replace(tzinfo=IST)), reverse=True)
    return m.ActivityResponse(as_of=end, data_time=data_time, events=events)
