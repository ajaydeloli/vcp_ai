"""Read-only diagnostics over the serving copy (AGENT_SPECIFICATION step A1.5): data-quality
events, one stock's corporate actions and price gaps, raw against adjusted prices, and the scan
runs of a day with the evening run's log lines. Nothing here writes or recomputes."""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from vcp_scanner.api import models as m
from vcp_scanner.api.queries import Cur
from vcp_scanner.domain.snapshot import LIVE_SNAPSHOT_ID as LIVE

#: A raw open this far from the previous raw close is listed as a gap.
GAP_PCT = 10.0
#: Log lines kept per run (warnings and errors only).
MAX_LOG_LINES = 40

_SYMBOL = "coalesce(i.symbol, split_part(e.instrument_id, '|', 2))"


def _f(x: Any) -> float | None:
    return None if x is None else float(x)


def _json(text: Any) -> Any:
    if text is None or not isinstance(text, str):
        return text
    try:
        return json.loads(text)
    except ValueError:
        return text


def data_quality(
    cur: Cur, *, status: str | None, severity: str | None, event_type: str | None,
    instrument_id: str | None, limit: int,
) -> tuple[list[m.DataQualityCount], list[m.DataQualityEvent], int]:  # fmt: skip
    """Counts of open events by type and severity, and the matching events (newest trade day
    first), with the total before ``limit``."""
    counts = [
        m.DataQualityCount(
            event_type=str(r[0]), severity=str(r[1]), open=int(r[2]), blocking=int(r[3])
        )  # fmt: skip
        for r in cur.execute(
            "SELECT event_type, severity, count(*), count(*) FILTER (WHERE blocks_signal)"
            " FROM data_quality_events WHERE status = 'OPEN' GROUP BY ALL ORDER BY 3 DESC"
        ).fetchall()
    ]
    where, params = [], []
    if status:
        where.append("upper(e.status) = upper(?)")
        params.append(status)
    if severity:
        where.append("upper(e.severity) = upper(?)")
        params.append(severity)
    if event_type:
        where.append("upper(e.event_type) = upper(?)")
        params.append(event_type)
    if instrument_id:
        where.append("e.instrument_id = ?")
        params.append(instrument_id)
    sql_where = (" WHERE " + " AND ".join(where)) if where else ""
    total = cur.execute(f"SELECT count(*) FROM data_quality_events e{sql_where}", params).fetchone()
    rows = cur.execute(
        f"SELECT e.event_id, {_SYMBOL}, e.trade_date, e.event_type, e.severity, e.blocks_signal,"
        " e.status, e.description, e.resolved_by, e.resolution_note, e.context"
        " FROM data_quality_events e LEFT JOIN instruments i ON i.instrument_id = e.instrument_id"
        f"{sql_where} ORDER BY e.trade_date DESC NULLS LAST, e.event_id LIMIT {int(limit)}",
        params,
    ).fetchall()
    events = [
        m.DataQualityEvent(
            event_id=str(r[0]), symbol=str(r[1]), trade_date=r[2], event_type=str(r[3]),
            severity=str(r[4]), blocks_signal=bool(r[5]), status=str(r[6]), description=r[7],
            resolved_by=r[8], resolution_note=r[9], context=_json(r[10]),
        )
        for r in rows
    ]  # fmt: skip
    return counts, events, int(total[0]) if total else 0


def corporate_actions(cur: Cur, instrument_id: str) -> tuple[
    list[m.CorporateActionRow], list[m.AdjustmentRow], list[m.DataQualityEvent], list[m.PriceGap]
]:  # fmt: skip
    """The stock's current corporate-action records, the price factors in force, its
    corporate-action and gap events, and every raw open-to-previous-close gap of GAP_PCT % or
    more (whether an adjustment falls on that day)."""
    actions = [
        m.CorporateActionRow(
            action_type=str(r[0]), ex_date=r[1], ratio_numerator=_f(r[2]),
            ratio_denominator=_f(r[3]), cash_amount=_f(r[4]), status=str(r[5]),
            conflict_fields=r[6], resolved_by=r[7],
        )
        for r in cur.execute(
            "SELECT action_type, ex_date, ratio_numerator, ratio_denominator, cash_amount, status,"
            " conflict_fields, resolved_by FROM corporate_action_resolution"
            " WHERE instrument_id = ? AND known_to IS NULL ORDER BY ex_date DESC NULLS LAST",
            [instrument_id],
        ).fetchall()
    ]  # fmt: skip
    adjustments = [
        m.AdjustmentRow(
            effective_date=r[0],
            price_factor=_f(r[1]),
            volume_factor=_f(r[2]),
            cumulative_price_factor=_f(r[3]),
            source=r[4],
        )  # fmt: skip
        for r in cur.execute(
            "SELECT effective_date, price_factor, volume_factor, cumulative_price_factor, source"
            " FROM corporate_action_adjustments WHERE instrument_id = ? AND known_to IS NULL"
            " ORDER BY effective_date DESC",
            [instrument_id],
        ).fetchall()
    ]
    _counts, events, _n = data_quality(
        cur, status=None, severity=None, event_type=None, instrument_id=instrument_id, limit=200
    )
    adjusted_on = {a.effective_date for a in adjustments}
    gaps = [
        m.PriceGap(
            day=r[0],
            prev_close=_f(r[1]),
            open=_f(r[2]),
            close=_f(r[3]),
            gap_pct=round(float(r[4]), 2),
            adjusted=r[0] in adjusted_on,
        )  # fmt: skip
        for r in cur.execute(
            """
            SELECT trade_date, pc, open_raw, close_raw, (open_raw / pc - 1) * 100 FROM (
                SELECT trade_date, open_raw, close_raw,
                       lag(close_raw) OVER (ORDER BY trade_date) pc
                FROM daily_prices WHERE instrument_id = ? AND known_to IS NULL
            ) WHERE pc > 0 AND abs(open_raw / pc - 1) * 100 >= ?
            ORDER BY trade_date DESC
            """,
            [instrument_id, GAP_PCT],
        ).fetchall()
    ]
    return actions, adjustments, events, gaps


def prices(cur: Cur, instrument_id: str, days: int) -> list[m.PriceRow]:
    """Raw prices as stored (newest last) next to the adjusted close and the factor applied."""
    rows = cur.execute(
        """
        SELECT r.trade_date, r.open_raw, r.high_raw, r.low_raw, r.close_raw, r.volume_raw,
               a.close_adj, a.price_factor_applied, r.primary_provider, r.data_status
        FROM daily_prices r
        LEFT JOIN daily_prices_adjusted_current a
          ON a.instrument_id = r.instrument_id AND a.trade_date = r.trade_date
         AND a.computed_from_snapshot_id = ?
        WHERE r.instrument_id = ? AND r.known_to IS NULL
        ORDER BY r.trade_date DESC LIMIT ?
        """,
        [LIVE, instrument_id, days],
    ).fetchall()
    rows.reverse()
    return [
        m.PriceRow(
            day=r[0],
            open=_f(r[1]),
            high=_f(r[2]),
            low=_f(r[3]),
            close=_f(r[4]),
            volume=_f(r[5]),
            close_adj=_f(r[6]),
            factor=_f(r[7]),
            provider=r[8],
            data_status=r[9],
        )  # fmt: skip
        for r in rows
    ]


def scan_runs(cur: Cur, day: date) -> list[m.ScanRunRow]:
    rows = cur.execute(
        "SELECT strategy_id, scan_type, status, scan_config_hash, code_commit, code_dirty,"
        " survivorship_status, counts, versions, started_at, completed_at FROM scan_runs"
        " WHERE as_of_date = ? ORDER BY strategy_id, scan_type, started_at DESC",
        [day],
    ).fetchall()
    seen: set[tuple[str, str]] = set()
    out = []
    for r in rows:  # the newest run per strategy and scan type
        key = (str(r[0]), str(r[1]))
        if key in seen:
            continue
        seen.add(key)
        out.append(
            m.ScanRunRow(
                strategy_id=key[0], scan_type=key[1], status=str(r[2]), config_hash=r[3],
                code_commit=r[4], code_dirty=r[5], survivorship=r[6], counts=_json(r[7]),
                versions=_json(r[8]), started_at=r[9], completed_at=r[10],
            )
        )  # fmt: skip
    return out


def latest_scan_run_day(cur: Cur) -> date | None:
    row = cur.execute("SELECT max(as_of_date) FROM scan_runs").fetchone()
    return row[0] if row else None


_STAMP = re.compile(r"^\[[^\]]*\]\s*")


def run_log(data_dir: Path, run_day: date) -> tuple[str | None, list[str]]:
    """The evening run's summary line for ``run_day`` (from logs/daily_runs.log) and its
    warning and error lines (timestamps dropped, duplicates once, at most MAX_LOG_LINES)."""
    logs = data_dir / "logs"
    summary = None
    runs = logs / "daily_runs.log"
    if runs.exists():
        for line in runs.read_text(errors="replace").splitlines():
            if line.startswith(run_day.isoformat()):
                summary = line.strip()
    lines: list[str] = []
    out = logs / f"daily_run_{run_day.isoformat()}.out"
    if out.exists():
        seen: set[str] = set()
        for line in out.read_text(errors="replace").splitlines():
            if not re.search(r"\b(WARNING|ERROR|CRITICAL)\b|^Warning:|^Error:|Traceback", line):
                continue
            text = _STAMP.sub("", line).strip()[:300]
            if text and text not in seen:
                seen.add(text)
                lines.append(text)
            if len(lines) >= MAX_LOG_LINES:
                break
    return summary, lines
