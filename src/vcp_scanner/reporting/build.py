"""Daily and weekly HTML reports (STRATEGY_SPECIFICATION 21.4/21.5; monitoring step M4).

Read-only: the reports are built from the serving copy through the dashboard API's own query
functions, so they always agree with the dashboard. Nothing here writes to a database. A missing
value is shown as "not available", never as 0.
"""

from __future__ import annotations

import html
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb

from vcp_scanner.api import models as m
from vcp_scanner.api import queries as q
from vcp_scanner.api import reports as r
from vcp_scanner.api.context import Context, build_context
from vcp_scanner.api.db import ServingDb
from vcp_scanner.data.providers._time import IST

NA = "not available"
START = date(2026, 10, 1)  # paper start (STRATEGY_SPECIFICATION 21.3)
NOTE = "Watch list for paper monitoring, not trade instructions. Strategies are frozen."
CSS = (
    "body{font:14px system-ui,sans-serif;margin:24px auto;max-width:1000px;padding:0 12px;"
    "color:#1b1f24}h1{font-size:22px}h2{font-size:17px;margin-top:28px;border-bottom:1px solid "
    "#d0d7de;padding-bottom:4px}table{border-collapse:collapse;width:100%;margin:8px 0}"
    "th,td{text-align:left;padding:4px 8px;border-bottom:1px solid #eaeef2}"
    "th{background:#f6f8fa}td.n,th.n{text-align:right}.warn{color:#9a6700}.bad{color:#cf222e}"
    ".ok{color:#1a7f37}.muted{color:#57606a}"
)


def esc(v: object) -> str:
    return html.escape(str(v))


def num(v: float | None, digits: int = 2, suffix: str = "") -> str:
    return NA if v is None else f"{v:.{digits}f}{suffix}"


def table(head: list[str], rows: list[list[str]], numeric_from: int = 99) -> str:
    if not rows:
        return '<p class="muted">None.</p>'
    th = "".join(
        f'<th class="n">{esc(h)}</th>' if i >= numeric_from else f"<th>{esc(h)}</th>"
        for i, h in enumerate(head)
    )
    body = "".join(
        "<tr>"
        + "".join(
            f'<td class="n">{c}</td>' if i >= numeric_from else f"<td>{c}</td>"
            for i, c in enumerate(row)
        )
        + "</tr>"
        for row in rows
    )
    return f"<table><tr>{th}</tr>{body}</table>"


def page(title: str, sections: list[str], generated: datetime) -> str:
    return (
        f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{esc(title)}</title>'
        f"<style>{CSS}</style></head><body><h1>{esc(title)}</h1>"
        f'<p class="muted">{esc(NOTE)} Generated {generated:%Y-%m-%d %H:%M} IST.</p>'
        + "".join(sections)
        + "</body></html>"
    )


# --- shared pieces -----------------------------------------------------------------------


def regime_section(days: list[m.MarketDay], rule: str) -> str:
    if not days:
        return f"<h2>Market regime</h2><p>{NA}</p>"
    d = days[-1]
    state = "ON" if d.regime_on else "OFF"
    cls = "ok" if d.regime_on else "bad"
    above = (
        NA if d.index is None or d.index_ma50 is None
        else ("above" if d.index > d.index_ma50 else "below")
    )  # fmt: skip
    return (
        f'<h2>Market regime</h2><p>Regime <b class="{cls}">{state}</b> on {d.day} '
        f"(rule: {esc(rule)}). Stocks above their 50-day average: {num(d.breadth_pct, 1, ' %')}. "
        f"Equal-weight index of our universe: {num(d.index, 1)}, {above} its 50-day average "
        f"({num(d.index_ma50, 1)}).</p>"
    )


def paper_tables(paper: m.PaperResponse) -> str:
    out = []
    for s in paper.strategies:
        rows = [
            [esc(o.symbol), str(o.entry_day), num(o.entry), num(o.stop), num(o.last),
             num(o.open_pct, 2, " %")]
            for o in s.open
        ]  # fmt: skip
        out.append(
            f"<h3>{esc(s.strategy_id)}</h3>"
            + table(["Stock", "Entered", "Entry", "Stop", "Last", "Open P/L"], rows, 2)
        )
    return "".join(out)


def health_section(st: m.StatusResponse) -> str:
    items = [f"Prices up to: {st.prices_date or NA}"]
    items.append(f"Last daily run: {esc(st.daily_run) if st.daily_run else NA}")
    items.append(
        f"Newest backup: {esc(st.newest_backup) if st.newest_backup else NA}"
        + ("" if st.backup_age_days is None else f" ({st.backup_age_days:.1f} days old)")
    )
    warns = "".join(f'<li class="warn">{esc(w)}</li>' for w in st.warnings)
    return (
        "<h2>Daily run health</h2><ul>"
        + "".join(f"<li>{i}</li>" for i in items)
        + (warns or '<li class="ok">No warnings.</li>')
        + "</ul>"
    )


# --- daily -------------------------------------------------------------------------------


def daily_html(
    cur: duckdb.DuckDBPyConnection, ctx: Context, day: date, now: datetime,
    data_time: datetime,
) -> str:  # fmt: skip
    status = r.status(cur, ctx, now, data_time)
    days = [d for d in q.market_days(cur, ctx, day, 60) if d.day <= day]
    sections = [regime_section(days, ctx.regime_rule)]

    sections.append("<h2>Ranked setups by strategy (grade 2 or better)</h2>")
    for spec in ctx.strategies:
        rows = q.setup_rows(cur, ctx, spec, day, eligible=True, min_grade=2)
        rows.sort(key=lambda x: -(x.score if x.score is not None else -1.0))
        sections.append(
            f"<h3>{esc(spec.strategy_id)} <span class='muted'>({len(rows)})</span></h3>"
            + table(
                ["Stock", "Class", "Grade", "Score", "Close", "Pivot", "To pivot", "Stop"],
                [
                    [esc(x.symbol), esc(x.classification), str(x.grade), num(x.score, 1),
                     num(x.close), num(x.pivot), num(x.pivot_distance_pct, 1, " %"),
                     num(x.stop)]
                    for x in rows[:20]
                ],
                3,
            )
            + ('<p class="muted">Top 20 shown.</p>' if len(rows) > 20 else "")
        )  # fmt: skip

    multi = [o for o in q.overlap(cur, ctx, day) if len(o.strategies) > 1]
    sections.append(
        "<h2>Stocks on more than one list</h2>"
        + table(
            ["Stock", "Strategies"],
            [
                [
                    esc(o.symbol),
                    esc(", ".join(f"{e.strategy_id} (grade {e.grade})" for e in o.strategies)),
                ]
                for o in multi
            ],
        )
    )

    events = r.activity(cur, ctx, day, 0, data_time).events
    today = [e for e in events if e.day == day and e.kind in ("PAPER_ENTRY", "PAPER_EXIT")]
    sections.append(
        "<h2>Paper trades today</h2>"
        + table(
            ["Strategy", "Stock", "What"],
            [[esc(e.strategy_id), esc(e.symbol or NA), esc(e.text)] for e in today],
        )
    )
    sections.append("<h2>Open paper positions</h2>" + paper_tables(r.paper(cur, ctx, data_time)))
    sections.append(health_section(status))
    return page(f"Daily report {day}", sections, now)


# --- weekly ------------------------------------------------------------------------------


def week_bounds(day: date) -> tuple[date, date, str]:
    iso = day.isocalendar()
    monday = day - timedelta(days=day.weekday())
    return monday, monday + timedelta(days=6), f"{iso.year}-W{iso.week:02d}"


def weekly_html(
    cur: duckdb.DuckDBPyConnection, ctx: Context, day: date, now: datetime,
    data_time: datetime,
) -> str:  # fmt: skip
    monday, _sunday, label = week_bounds(day)
    status = r.status(cur, ctx, now, data_time)
    paper = r.paper(cur, ctx, data_time)
    days = [d for d in q.market_days(cur, ctx, day, 60) if d.day <= day]
    this_week = [d for d in days if d.day >= monday]

    sections = [regime_section(days, ctx.regime_rule)]
    if this_week:
        first, last = this_week[0], this_week[-1]
        idx = (
            NA if not first.index or last.index is None
            else f"{(last.index / first.index - 1) * 100:+.2f} %"
        )  # fmt: skip
        sections.append(
            "<h2>Breadth and index this week</h2><ul>"
            f"<li>Stocks above their 50-day average: {num(first.breadth_pct, 1, ' %')} on "
            f"{first.day} to {num(last.breadth_pct, 1, ' %')} on {last.day}.</li>"
            f"<li>Equal-weight index of our universe over the week: {idx}.</li>"
            f"<li>Regime ON on {sum(d.regime_on for d in this_week)} of {len(this_week)} "
            "sessions.</li></ul>"
        )  # fmt: skip

    events = r.activity(cur, ctx, day, (day - monday).days, data_time).events
    paper_ev = [e for e in events if e.kind.startswith("PAPER_") and e.day >= monday]
    rows = []
    for s in paper.strategies:
        mine = [e for e in paper_ev if e.strategy_id == s.strategy_id]
        rows.append([
            esc(s.strategy_id),
            str(sum(e.kind == "PAPER_ENTRY" for e in mine)),
            str(sum(e.kind == "PAPER_EXIT" for e in mine)),
            str(s.closed),
            num(s.win_rate_pct, 1, " %"),
            num(s.avg_return_pct, 2, " %"),
            num(s.profit_factor),
            str(len(s.open)),
        ])  # fmt: skip
    sections.append(
        f"<h2>Paper results since {START}</h2>"
        + table(
            ["Strategy", "Entries this week", "Exits this week", "Closed in total", "Win rate",
             "Average trade", "Profit factor", "Open now"],
            rows, 1,
        )
        + "<p class='muted'>Portfolio return and drawdown against the equal-weight index are "
        f"{NA} in the reports: they need a portfolio replay, judged at the review on or after "
        f"{paper.review_from}. Closed trades needed per strategy: {paper.min_closed_trades}.</p>"
    )  # fmt: skip
    sections.append(
        "<h2>Trades this week</h2>"
        + table(
            ["Day", "Strategy", "Stock", "What"],
            [
                [str(e.day), esc(e.strategy_id), esc(e.symbol or NA), esc(e.text)]
                for e in sorted(paper_ev, key=lambda e: e.day)
                if e.kind in ("PAPER_ENTRY", "PAPER_EXIT")
            ],
        )
    )
    sections.append("<h2>Open paper positions</h2>" + paper_tables(paper))

    issues = [
        e for e in events
        if e.kind == "DAILY_RUN" and e.day >= monday and ("FAILED" in e.text or "NONE" in e.text)
    ]  # fmt: skip
    sections.append(
        "<h2>Data issues this week</h2><ul>"
        + "".join(f'<li class="warn">{esc(w)}</li>' for w in status.warnings)
        + "".join(f'<li class="warn">{e.day}: {esc(e.text)}</li>' for e in issues)
        + ("" if status.warnings or issues else '<li class="ok">None recorded.</li>')
        + "</ul>"
    )
    return page(f"Weekly summary {label}", sections, now)


# --- writing -----------------------------------------------------------------------------


def _build(
    serving: Path, config_dir: str | Path, data_dir: str | Path,
    make: Callable[..., str], day: date | None, now: datetime,
) -> tuple[str, date]:  # fmt: skip
    ctx = build_context(config_dir, data_dir)
    db = ServingDb(serving)
    with db.cursor() as cur:
        use = day or q.latest_prices_date(cur)
        if use is None:
            raise ValueError("no prices in the serving copy")
        return make(cur, ctx, use, now, db.data_time()), use


def write_daily(
    serving: Path, config_dir: str | Path, data_dir: str | Path, out_dir: Path,
    day: date | None = None, now: datetime | None = None,
) -> Path:  # fmt: skip
    text, used = _build(serving, config_dir, data_dir, daily_html, day, now or datetime.now(IST))
    path = out_dir / "daily" / f"{used}.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def write_weekly(
    serving: Path, config_dir: str | Path, data_dir: str | Path, out_dir: Path,
    day: date | None = None, now: datetime | None = None,
) -> Path:  # fmt: skip
    text, used = _build(serving, config_dir, data_dir, weekly_html, day, now or datetime.now(IST))
    path = out_dir / "weekly" / f"{week_bounds(used)[2]}.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _latest_day(serving: Path) -> date | None:
    with ServingDb(serving).cursor() as cur:
        return q.latest_prices_date(cur)


def write_reports(
    serving: Path, config_dir: str | Path, data_dir: str | Path, out_dir: Path
) -> list[Path]:  # fmt: skip
    """The daily report, plus the weekly summary when the latest prices are from a Friday."""
    paths = [write_daily(serving, config_dir, data_dir, out_dir)]
    latest = _latest_day(serving)
    if latest is not None and latest.weekday() == 4:
        paths.append(write_weekly(serving, config_dir, data_dir, out_dir, latest))
    return paths


__all__: list[Any] = ["write_daily", "write_weekly", "write_reports", "week_bounds"]
