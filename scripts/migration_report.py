"""Before/after report for the move to NSE bhavcopy as the raw price source (audit step 2.6).

Read-only. Compares a database built on Kite history ("before") with one rebuilt from the
bhavcopy ("after") and writes a Markdown report:

1. raw data coverage and provenance;
2. adjusted closes of the instruments both databases price, with every difference in the
   before/after ratio attributed to a corporate action (same logic as
   ``vcp verify kite-crosscheck``: the "before" series is Kite-derived);
3. corporate actions, factors and open data-quality events in "after";
4. universe, RS and Trend Template on the as-of date, per common instrument.

Usage: python scripts/migration_report.py --before data/fix5b.duckdb \
           --after data/migr26.duckdb --as-of 2026-09-29 --out report.md
"""

from __future__ import annotations

import argparse
import math
from datetime import date
from pathlib import Path

import duckdb

from vcp_scanner.data.quality.kite_crosscheck import crosscheck
from vcp_scanner.domain.corporate_actions import CorporateActionResolution, CorporateActionStatus
from vcp_scanner.domain.enums import CorporateActionType


def _adjusted(con: duckdb.DuckDBPyConnection, iid: str) -> list[tuple[date, float]]:
    return [
        (d, float(c))
        for d, c in con.execute(
            "SELECT trade_date, close_adj FROM daily_prices_adjusted_current"
            " WHERE instrument_id = ? AND computed_from_snapshot_id = 'LIVE' ORDER BY 1",
            [iid],
        ).fetchall()
    ]


def _resolutions(con: duckdb.DuckDBPyConnection, iid: str) -> list[CorporateActionResolution]:
    rows = con.execute(
        "SELECT resolution_id, action_type, status, ex_date FROM corporate_action_resolution"
        " WHERE instrument_id = ? AND known_to IS NULL",
        [iid],
    ).fetchall()
    return [
        CorporateActionResolution(
            str(r), iid, CorporateActionType(t), CorporateActionStatus(s), ex_date=d
        )
        for r, t, s, d in rows
    ]


def _scan(con: duckdb.DuckDBPyConnection, as_of: date) -> str | None:
    """The scan on ``as_of`` that evaluated the most instruments (the full run)."""
    row = con.execute(
        "SELECT scan_id FROM trend_template_results WHERE as_of_date = ?"
        " GROUP BY scan_id ORDER BY count(*) DESC, scan_id DESC LIMIT 1",
        [as_of],
    ).fetchone()
    return None if row is None else str(row[0])


def _table(header: list[str], rows: list[list[object]]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join("" if v is None else str(v) for v in r) + " |" for r in rows]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", required=True)
    ap.add_argument("--after", required=True)
    ap.add_argument("--as-of", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    as_of = date.fromisoformat(args.as_of)
    b = duckdb.connect(args.before, read_only=True)
    a = duckdb.connect(args.after, read_only=True)
    md: list[str] = [f"# Bhavcopy migration report (as of {as_of})", ""]

    # 1. Raw coverage -----------------------------------------------------------------
    md += ["## 1. Raw data", ""]
    cov = []
    for name, con in (("before", b), ("after", a)):
        for prov, n, inst, lo, hi in con.execute(
            "SELECT primary_provider, count(*), count(DISTINCT instrument_id), min(trade_date),"
            " max(trade_date) FROM daily_prices WHERE known_to IS NULL GROUP BY 1 ORDER BY 1"
        ).fetchall():
            cov.append([name, prov, n, inst, lo, hi])
    md += [_table(["db", "provider", "current bars", "instruments", "from", "to"], cov), ""]
    sup = a.execute(
        "SELECT primary_provider, count(*) FROM daily_prices WHERE known_to IS NOT NULL GROUP BY 1"
    ).fetchall()
    files = a.execute(
        "SELECT status, count(*) FROM bhavcopy_files GROUP BY 1 ORDER BY 1"
    ).fetchall()
    inactive = a.execute("SELECT count(*) FROM instruments WHERE NOT is_active").fetchone()
    changes = a.execute(
        "SELECT change_reason, count(*) FROM instrument_identifier_history"
        " WHERE change_reason <> 'FIRST_SEEN' GROUP BY 1 ORDER BY 1"
    ).fetchall()
    md += [
        f"- Superseded bars in after: {dict(sup)}",
        f"- Bhavcopy manifest: {dict(files)}",
        "- Inactive instruments in after (delisted, SME, not in EQUITY_L): "
        f"{inactive[0] if inactive else 0}",
        f"- Identifier changes: {dict(changes)}",
        "",
    ]

    # 2. Adjusted closes ----------------------------------------------------------------
    common = sorted(
        {r[0] for r in b.execute("SELECT DISTINCT instrument_id FROM daily_prices").fetchall()}
        & {r[0] for r in a.execute("SELECT DISTINCT instrument_id FROM daily_prices").fetchall()}
    )
    md += [
        "## 2. Adjusted closes, before (Kite-derived) vs after (bhavcopy + our factors)",
        "",
        "Step = before/after ratio change across an ex-date window (before applied a factor"
        " that after does not, or a different one).",
        "",
    ]
    rows = []
    detail = []
    for iid in common:
        before, after = _adjusted(b, iid), _adjusted(a, iid)
        rep = crosscheck(after, before, _resolutions(a, iid))
        bmap, amap = dict(before), dict(after)
        days = sorted(bmap.keys() & amap.keys())
        first = bmap[days[0]] / amap[days[0]] if days else None
        last = bmap[days[-1]] / amap[days[-1]] if days else None
        worst = max((abs(math.log(bmap[d] / amap[d])) for d in days), default=0.0)
        rows.append(
            [iid.split("|")[1], rep.common_days, f"{first:.4f}" if first else "",
             f"{last:.4f}" if last else "", f"{math.expm1(worst) * 100:.1f}%",
             sum(f.severity == "INFO" for f in rep.findings), len(rep.warnings)]
        )  # fmt: skip
        for f in rep.findings:
            detail.append(
                [iid.split("|")[1], f"{f.previous_date}..{f.trade_date}", f.kind.value,
                 f.severity, f"{f.step:.5f}", ",".join(f.actions)]
            )  # fmt: skip
    md += [
        _table(
            [
                "symbol",
                "common days",
                "before/after first day",
                "before/after last day",
                "max diff",
                "info",
                "warnings",
            ],
            rows,
        ),  # fmt: skip
        "",
        _table(["symbol", "window", "kind", "severity", "step", "actions"], detail),
        "",
    ]

    # 3. Corporate actions and quality -------------------------------------------------
    md += ["## 3. Corporate actions and data quality (after)", ""]
    res = a.execute(
        "SELECT action_type, status, count(*) FROM corporate_action_resolution"
        " WHERE known_to IS NULL GROUP BY 1, 2 ORDER BY 1, 2"
    ).fetchall()
    md += [_table(["action", "status", "count"], [list(r) for r in res]), ""]
    fac = a.execute(
        "SELECT r.action_type, count(*) FROM corporate_action_adjustments j"
        " JOIN corporate_action_resolution r"
        "   ON r.instrument_id = j.instrument_id AND r.ex_date = j.effective_date"
        "  AND r.known_to IS NULL AND r.action_type IN ('SPLIT','BONUS','RIGHTS','DEMERGER')"
        " WHERE j.known_to IS NULL GROUP BY 1 ORDER BY 1"
    ).fetchall()
    md += [f"- Factors by action type (a date can hold several): {dict(fac)}", ""]
    ev = a.execute(
        "SELECT event_type, severity, blocks_signal, count(*), count(DISTINCT instrument_id)"
        " FROM data_quality_events WHERE status = 'OPEN' GROUP BY 1, 2, 3 ORDER BY 4 DESC"
    ).fetchall()
    md += [
        _table(["event", "severity", "blocks", "events", "instruments"], [list(r) for r in ev]),
        "",
    ]

    # 4. Universe / RS / Trend Template ------------------------------------------------
    md += [f"## 4. Universe, RS and Trend Template on {as_of}", ""]

    def tt(con: duckdb.DuckDBPyConnection) -> dict[str, tuple[object, ...]]:
        out = {}
        for iid, status, ok, stage, rs in con.execute(
            "SELECT instrument_id, status, trend_template_pass, weekly_stage, rs_rank"
            " FROM trend_template_results WHERE as_of_date = ? AND scan_id = ?",
            [as_of, _scan(con, as_of)],
        ).fetchall():
            failed = [
                str(r[0])
                for r in con.execute(
                    "SELECT condition_id FROM trend_template_conditions WHERE instrument_id = ?"
                    " AND as_of_date = ? AND NOT passed ORDER BY 1",
                    [iid, as_of],
                ).fetchall()
            ]
            out[iid] = (status, ok, stage, rs, ",".join(sorted(set(failed), key=str)))
        return out

    tb, ta = tt(b), tt(a)
    for name, con in (("before", b), ("after", a)):
        u = con.execute(
            "SELECT count(*), sum(CASE WHEN eligible THEN 1 ELSE 0 END) FROM universe_memberships"
            " WHERE universe_snapshot_id = (SELECT universe_snapshot_id FROM universe_snapshots"
            " WHERE as_of_date = ? ORDER BY created_at DESC LIMIT 1)",
            [as_of],
        ).fetchone()
        t = con.execute(
            "SELECT count(*), sum(CASE WHEN trend_template_pass THEN 1 ELSE 0 END)"
            " FROM trend_template_results WHERE as_of_date = ? AND scan_id = ?",
            [as_of, _scan(con, as_of)],
        ).fetchone()
        md.append(f"- {name}: universe screened/eligible {u}; Trend Template evaluated/passed {t}")
    md.append("")
    rows = []
    for iid in common:
        x, y = tb.get(iid), ta.get(iid)
        rows.append(
            [iid.split("|")[1], x and x[0], y and y[0], x and x[2], y and y[2], x and x[3],
             y and y[3], x and x[4], y and y[4]]
        )  # fmt: skip
    md += [
        "RS ranks are not comparable one to one: before ranked 20 stocks, after ranks the whole"
        " eligible market.",
        "",
        _table(
            [
                "symbol",
                "TT before",
                "TT after",
                "stage before",
                "stage after",
                "RS before",
                "RS after",
                "failed before",
                "failed after",
            ],
            rows,
        ),  # fmt: skip
        "",
    ]
    passed = a.execute(
        "SELECT r.instrument_id, r.rs_rank, r.weekly_stage FROM trend_template_results r"
        " WHERE r.as_of_date = ? AND r.scan_id = ? AND r.trend_template_pass"
        " ORDER BY r.rs_rank DESC",
        [as_of, _scan(a, as_of)],
    ).fetchall()
    md += [f"Trend Template passes in after: {len(passed)}", ""]
    md += [_table(["instrument", "RS", "stage"], [list(r) for r in passed[:40]]), ""]
    Path(args.out).write_text("\n".join(md))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
