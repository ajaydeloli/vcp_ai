"""Checks before the look on the 2012-2021 history database (approved list, 2026-11-08).

Data only: nothing here reads a backtest, a trade or a forward label. Checks 1-5 of
claude/history_2012_2021_preregistration.md:

1. every weekly session has a completed score scan; failed steps in the backfill log;
2. per year: universe, Trend Template passes, VCP patterns by class, breakouts, eligible setups;
3. overlap Jan-Jun 2021: raw closes vs the live database, and whether each stock's adjusted
   closes differ from live by one constant factor (later corporate actions) only;
4. Market Health rows per session, and the first days with an Index trend score;
5. breadth50 regime-on days per year (the backtest's own regime series).

    .venv/bin/python research/history/prelook_checks.py
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[2]
HIST = ROOT / "data" / "research" / "vcp_research.duckdb"  # history_2011 retired 2026-11-10
LIVE = ROOT / "data" / "serving" / "vcp_serving.duckdb"
LOG = ROOT / "data" / "logs" / "history_backfill.log"
FIRST, LAST = date(2012, 7, 2), date(2021, 6, 30)


def show(title: str, rows: list[tuple], head: tuple[str, ...]) -> None:
    print(f"\n== {title}")
    print("  " + " | ".join(head))
    for r in rows:
        print("  " + " | ".join("" if v is None else str(round(v, 2) if isinstance(v, float)
                                                           else v) for v in r))  # fmt: skip


def main() -> None:
    con = duckdb.connect(str(HIST), read_only=True)
    q = con.execute

    # 1 -----------------------------------------------------------------------------------
    sessions = [r[0] for r in q(
        "SELECT trade_date FROM bhavcopy_files WHERE status = 'OK'"
        " AND trade_date BETWEEN ? AND ? ORDER BY 1", [FIRST, LAST]).fetchall()]  # fmt: skip
    week_end: dict[tuple[int, int], date] = {}
    for d in sessions:
        week_end[d.isocalendar()[:2]] = d
    weekly = set(week_end.values())
    by_type = dict(q("SELECT scan_type, count(DISTINCT as_of_date) FROM scan_runs"
                     " WHERE status = 'COMPLETED' GROUP BY 1").fetchall())  # fmt: skip
    score_days = {r[0] for r in q("SELECT as_of_date FROM scan_runs WHERE scan_type = 'SCORE'"
                                  " AND status = 'COMPLETED'").fetchall()}  # fmt: skip
    failed = [ln.strip() for ln in LOG.read_text().splitlines() if "FAILED" in ln]
    print(f"1. weekly sessions {len(weekly)}; completed scans by type {by_type}")
    print(f"   weekly sessions without a score scan: {sorted(weekly - score_days)[:10]}"
          f" ({len(weekly - score_days)}); score scans off the weekly dates:"
          f" {len(score_days - weekly)}; FAILED lines in the log: {len(failed)}")  # fmt: skip
    for ln in failed[:10]:
        print("   ", ln)

    # 2 -----------------------------------------------------------------------------------
    show("2. per year, average per weekly scan (and totals)", q("""
        WITH u AS (SELECT s.as_of_date d, count(*) FILTER (WHERE m.eligible) n
                   FROM universe_snapshots s
                   JOIN universe_memberships m USING (universe_snapshot_id)
                   GROUP BY 1),
             tt AS (SELECT as_of_date d, count(*) FILTER (WHERE status = 'PASS') n
                    FROM trend_template_results GROUP BY 1),
             p AS (SELECT as_of_date d,
                          count(*) FILTER (WHERE is_primary AND classification = 'VCP') vcp,
                          count(*) FILTER (WHERE is_primary AND classification = 'VCP_LIKE') lk,
                          count(*) FILTER (WHERE is_primary AND classification = 'A_PLUS_VCP') ap
                   FROM vcp_patterns GROUP BY 1),
             e AS (SELECT detected_as_of d, count(*) n FROM vcp_breakout_events GROUP BY 1),
             s AS (SELECT as_of_date d, count(*) FILTER (WHERE eligible) n FROM setup_scores
                   GROUP BY 1)
        SELECT year(u.d) y, count(*) scans, avg(u.n), avg(tt.n), avg(p.vcp), avg(p.lk),
               sum(p.ap), sum(coalesce(e.n, 0)), sum(coalesce(s.n, 0))
        FROM u LEFT JOIN tt USING (d) LEFT JOIN p USING (d) LEFT JOIN e USING (d)
               LEFT JOIN s USING (d)
        GROUP BY 1 ORDER BY 1""").fetchall(),
        ("year", "scans", "universe", "TT pass", "VCP", "VCP_LIKE", "A+ (total)",
         "breakouts (total)", "eligible setups (total)"))  # fmt: skip

    # 3 -----------------------------------------------------------------------------------
    q(f"ATTACH '{LIVE}' AS live (READ_ONLY)")
    raw = q("""
        SELECT count(*), count(*) FILTER (WHERE abs(h.close_raw / l.close_raw - 1) > 0.0001),
               count(DISTINCT h.instrument_id)
        FROM daily_prices h JOIN live.daily_prices l USING (instrument_id, trade_date)
        WHERE h.trade_date BETWEEN '2021-01-01' AND '2021-06-30'""").fetchone()
    adj = q("""
        WITH r AS (SELECT h.instrument_id, h.close_adj / l.close_adj ratio
                   FROM daily_prices_adjusted_current h
                   JOIN live.daily_prices_adjusted_current l USING (instrument_id, trade_date)
                   WHERE h.trade_date BETWEEN '2021-01-01' AND '2021-06-30')
        SELECT instrument_id, min(ratio), max(ratio), count(*) FROM r GROUP BY 1
        HAVING max(ratio) / min(ratio) - 1 > 0.01
        ORDER BY max(ratio) / min(ratio) DESC""").fetchall()
    consts = q("""
        WITH r AS (SELECT h.instrument_id, h.close_adj / l.close_adj ratio
                   FROM daily_prices_adjusted_current h
                   JOIN live.daily_prices_adjusted_current l USING (instrument_id, trade_date)
                   WHERE h.trade_date BETWEEN '2021-01-01' AND '2021-06-30')
        SELECT count(DISTINCT instrument_id),
               count(DISTINCT instrument_id) FILTER (WHERE abs(ratio - 1) > 0.001)
        FROM r""").fetchone()  # fmt: skip
    print(f"\n3. overlap Jan-Jun 2021: {raw[0]} bars of {raw[2]} stocks in both; raw closes"
          f" differing by > 0.01%: {raw[1]}")  # fmt: skip
    print(f"   adjusted closes: {consts[0]} stocks; {consts[1]} on a different basis (later"
          f" corporate actions, a constant factor); {len(adj)} whose factor varies by > 1%"
          " inside the window:")  # fmt: skip
    for r in adj[:25]:
        print("   ", r)

    # 4 -----------------------------------------------------------------------------------
    mh = q("""
        SELECT count(*), min(trade_date), max(trade_date),
               min(trade_date) FILTER (WHERE json_extract(pillars_json, '$[0].score') IS NOT NULL)
        FROM market_health_daily WHERE trade_date BETWEEN ? AND ?""", [FIRST, LAST]).fetchone()
    pillars = q("""
        SELECT p.id, min(m.trade_date), count(*)
        FROM market_health_daily m,
             unnest(from_json(m.pillars_json, '[{"id":"VARCHAR","score":"DOUBLE"}]')) t(p)
        WHERE p.score IS NOT NULL AND m.trade_date BETWEEN ? AND ? GROUP BY 1 ORDER BY 1""",
                [FIRST, LAST]).fetchall()  # fmt: skip
    print(f"\n4. Market Health rows {mh[0]} for {len(sessions)} sessions ({mh[1]} .. {mh[2]})")
    print("   first day with a score, and days with a score, per pillar:")
    for r in pillars:
        print("   ", r)

    # 5 -----------------------------------------------------------------------------------
    con.close()
    from vcp_scanner.backtest.regime import breadth_regime
    from vcp_scanner.config.loader import load_scanner_config, scan_config_hash
    from vcp_scanner.data.repositories.duckdb_backtest_repository import DuckDBBacktestRepository
    from vcp_scanner.data.storage.duckdb_store import DuckDBStore
    from vcp_scanner.domain.features import FEATURES_CALCULATION_VERSION

    with DuckDBStore(str(HIST)) as store:
        repo = DuckDBBacktestRepository(store)
        cfg_hash = scan_config_hash(load_scanner_config(str(ROOT / "config")))
        rows = repo.breadth(FIRST, LAST, cfg_hash, FEATURES_CALCULATION_VERSION)
    on = breadth_regime(rows)
    years: dict[int, list[int]] = {}
    for d, v in on.items():
        if FIRST <= d <= LAST:
            years.setdefault(d.year, [0, 0])
            years[d.year][0] += 1
            years[d.year][1] += int(v)
    show("5. breadth50 regime (>= 40% of members above their 50-day)",
         [(y, n, k, 100.0 * k / n) for y, (n, k) in sorted(years.items())],
         ("year", "sessions", "regime on", "on %"))  # fmt: skip


if __name__ == "__main__":
    main()
