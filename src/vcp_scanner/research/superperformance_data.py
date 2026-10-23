"""Data for the superperformance study: everything read point in time from the local databases
(prices, the per-day universe, RS and Trend Template as the Market Health builder rebuilds them,
fundamentals, shareholding, sectors, the market-health label, and the weekly VCP scans of the
research database), and the tables the study writes. Nothing here calls a provider."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from vcp_scanner.research.superperformance import (
    STUDY_VERSION,
    Context,
    Fundamental,
    Holding,
    Run,
    Stock,
    gain_band,
)

NIFTY500 = "Nifty 500"
LIVE_STATUSES = ("FORMING", "PIVOT_READY", "BREAKOUT")
SURVIVORSHIP = "PARTIAL"
SURVIVORSHIP_DETAIL = (
    "universe rebuilt per day from prices (EQ series, price, 20- and 50-day traded value, 253 "
    "bars); ASM/GSM history starts 2026-10-01, so earlier surveillance exclusions are not "
    "applied; stocks that stopped trading are kept; sectors are today's classification"
)
_COLS = ("iid", "d", "c", "h", "l", "v", "ok", "rs_rank", "tt", "sma_50", "sma_200", "high_252",
         "volume_avg_50", "vol50")  # fmt: skip


def build_universe(con: Any, config_dir: str | Path) -> None:
    """The temp table ``mh_stock``: per stock and session the point-in-time universe, RS rank
    and Trend Template, built by the Market Health Score's own rule (one rule for all days)."""
    from vcp_scanner.config.loader import load_scanner_config
    from vcp_scanner.domain.features import FEATURES_CALCULATION_VERSION
    from vcp_scanner.domain.snapshot import LIVE_SNAPSHOT_ID
    from vcp_scanner.market_health.features import _stock_sql
    from vcp_scanner.market_health.settings import load_settings

    cfg = load_scanner_config(config_dir)
    settings = load_settings(Path(config_dir) / "market_health.yaml")
    uni = cfg.universe
    con.execute(
        _stock_sql(settings, cfg.strategy.trend_template, uni),
        [list(uni.eligible_series), FEATURES_CALCULATION_VERSION, LIVE_SNAPSHOT_ID,
         LIVE_SNAPSHOT_ID],
    )  # fmt: skip


def _sectors(con: Any) -> dict[str, str]:
    rows = con.execute(
        "SELECT instrument_id, arg_max(sector, valid_from) FROM instrument_classification"
        " WHERE sector IS NOT NULL GROUP BY 1"
    ).fetchall()
    return {str(r[0]): str(r[1]) for r in rows}


def _symbols(con: Any) -> dict[str, str]:
    rows = con.execute(
        "SELECT instrument_id, arg_max(symbol, coalesce(valid_from, DATE '1900-01-01'))"
        " FROM instruments GROUP BY 1"
    ).fetchall()
    return {str(r[0]): str(r[1]) for r in rows}


def _fundamentals(con: Any) -> dict[str, list[Fundamental]]:
    out: dict[str, list[Fundamental]] = defaultdict(list)
    for iid, day, eps, sales, acc in con.execute(
        "SELECT instrument_id, as_of_date, arg_max(eps_yoy, computed_at),"
        " arg_max(revenue_yoy, computed_at), arg_max(eps_acceleration, computed_at)"
        " FROM fundamental_metrics GROUP BY 1, 2 ORDER BY 1, 2"
    ).fetchall():
        out[str(iid)].append(Fundamental(day, eps, sales, acc))
    return dict(out)


def _holdings(con: Any) -> dict[str, list[Holding]]:
    """Per stock, the one-quarter promoter and FII changes as known from each day a filing became
    available (IST; a filing after the 15:30 close counts from the next day)."""
    rows = con.execute(
        "SELECT instrument_id, as_on, revision_number,"
        " CAST(timezone('Asia/Kolkata', available_at) + INTERVAL '8 hours 30 minutes' AS DATE),"
        " promoter_pct, fii_pct FROM shareholding_snapshots WHERE is_quarter_end"
        " ORDER BY 1, 4, 2, 3"
    ).fetchall()
    by: dict[str, list[tuple[date, date, int, float | None, float | None]]] = defaultdict(list)
    for iid, as_on, rev, avail, prom, fii in rows:
        by[str(iid)].append((avail, as_on, int(rev or 0), prom, fii))
    out: dict[str, list[Holding]] = {}
    for iid, filings in by.items():
        known: dict[date, tuple[int, float | None, float | None]] = {}
        timeline: list[Holding] = []
        for avail, as_on, rev, prom, fii in filings:
            if as_on not in known or rev >= known[as_on][0]:
                known[as_on] = (rev, prom, fii)
            ends = sorted(known)
            if len(ends) < 2 or (ends[-1] - ends[-2]).days > 100:
                change = Holding(avail, None, None)
            else:
                (_r1, p1, f1), (_r0, p0, f0) = known[ends[-1]], known[ends[-2]]
                change = Holding(
                    avail,
                    None if p1 is None or p0 is None else p1 - p0,
                    None if f1 is None or f0 is None else f1 - f0,
                )
            if timeline and timeline[-1].available == avail:
                timeline[-1] = change
            else:
                timeline.append(change)
        out[iid] = timeline
    return out


def _vcp_sql(table: str) -> str:
    live = ", ".join(f"'{s}'" for s in LIVE_STATUSES)
    return (
        "SELECT instrument_id, as_of_date, arg_max(CASE"
        f" WHEN classification IN ('VCP', 'A_PLUS_VCP') AND status IN ({live}) THEN 'VCP'"
        f" WHEN classification = 'VCP_LIKE' AND status IN ({live}) THEN 'VCP_LIKE'"
        f" ELSE 'NONE' END, created_at) FROM {table}"
        " WHERE is_primary AND algorithm_version = ? AND as_of_date > ? GROUP BY 1, 2"
    )


def _vcp(con: Any, research: Any | None) -> tuple[dict[str, list[tuple[date, str]]], str]:
    """Each stock's VCP class per scan day: the research database's weekly scans, then the main
    database's daily scans after them; both with the current detector version."""
    from vcp_scanner.versioning import VCP_ALGORITHM_VERSION as V

    out: dict[str, list[tuple[date, str]]] = defaultdict(list)
    last = date(1900, 1, 1)
    sources = []
    if research is not None:
        for iid, d, cls in research.execute(_vcp_sql("vcp_patterns"), [V, last]).fetchall():
            out[str(iid)].append((d, str(cls)))
            last = max(last, d)
        sources.append("research database weekly scans")
    for iid, d, cls in con.execute(_vcp_sql("vcp_patterns"), [V, last]).fetchall():
        out[str(iid)].append((d, str(cls)))
    sources.append(f"main database scans after {last}")
    for v in out.values():
        v.sort()
    return dict(out), f"{V}: " + ", ".join(sources)


def load_context(con: Any, research: Any | None) -> tuple[Context, dict[str, str], str]:
    """The study's context (``build_universe`` first); also the stock symbols and where the VCP
    scans came from."""
    sectors = _sectors(con)
    calendar = [r[0] for r in con.execute(
        "SELECT DISTINCT d FROM mh_stock ORDER BY 1").fetchall()]  # fmt: skip
    nifty = dict(con.execute(
        "SELECT trade_date, close FROM index_daily WHERE index_name = ?", [NIFTY500]
    ).fetchall())  # fmt: skip
    version = con.execute(
        "SELECT settings_version FROM market_health_daily GROUP BY 1 ORDER BY max(computed_at)"
        " DESC LIMIT 1"
    ).fetchone()  # fmt: skip
    labels = {} if version is None else dict(con.execute(
        "SELECT trade_date, label FROM market_health_daily WHERE settings_version = ?",
        [version[0]]).fetchall())  # fmt: skip
    con.execute("CREATE OR REPLACE TEMP TABLE sp_sector (iid VARCHAR, sector VARCHAR)")
    con.executemany("INSERT INTO sp_sector VALUES (?, ?)", list(sectors.items()))
    sector_share: dict[tuple[str, date], float] = {}
    for sec, d, n, k in con.execute(
        "SELECT s.sector, m.d, count(*) FILTER (WHERE m.tt_eval), count(*) FILTER (WHERE m.tt)"
        " FROM mh_stock m JOIN sp_sector s USING (iid) GROUP BY 1, 2"
    ).fetchall():
        if n >= 5:
            sector_share[(str(sec), d)] = k / n
    tt_share = {d: k / n for d, n, k in con.execute(
        "SELECT d, count(*) FILTER (WHERE tt_eval), count(*) FILTER (WHERE tt) FROM mh_stock"
        " GROUP BY 1").fetchall() if n}  # fmt: skip
    sector_eligible = {(str(sec), int(y)): int(n) for sec, y, n in con.execute(
        "SELECT coalesce(s.sector, 'Unclassified'), year(m.d), count(DISTINCT m.iid)"
        " FROM mh_stock m LEFT JOIN sp_sector s USING (iid) WHERE m.ok GROUP BY 1, 2"
    ).fetchall()}  # fmt: skip
    vol_cut = {d: float(v) for d, v in con.execute(
        "SELECT d, quantile_cont(vol50, 2 / 3) FROM mh_stock WHERE ok AND vol50 IS NOT NULL"
        " GROUP BY 1").fetchall() if v is not None}  # fmt: skip
    vcp, vcp_source = _vcp(con, research)
    ctx = Context(
        calendar=calendar, nifty={d: float(c) for d, c in nifty.items()},
        market_label={d: str(x) for d, x in labels.items()},
        sector_tt_share=sector_share, tt_share=tt_share,
        fundamentals=_fundamentals(con), holdings=_holdings(con), vcp=vcp,
        sector_eligible=sector_eligible, vol_cut=vol_cut,
    )  # fmt: skip
    return ctx, sectors, vcp_source


def iter_stocks(con: Any, sectors: dict[str, str]) -> Iterator[Stock]:
    """Every stock with prices, one at a time (a column table in memory, one stock's lists)."""
    symbols = _symbols(con)
    sizes = con.execute("SELECT iid, count(*) FROM mh_stock GROUP BY 1 ORDER BY 1").fetchall()
    table = con.execute(
        f"SELECT {', '.join(_COLS)} FROM mh_stock ORDER BY iid, d"
    ).fetch_arrow_table()  # fmt: skip
    offset = 0
    for iid, size in sizes:
        part = table.slice(offset, int(size)).to_pydict()
        offset += int(size)
        assert part["iid"][0] == iid and part["iid"][-1] == iid
        yield Stock(
            iid=str(iid), symbol=symbols.get(str(iid), str(iid).split("|")[-1]),
            sector=sectors.get(str(iid)), days=part["d"], close=part["c"], high=part["h"],
            low=part["l"], volume=[float(x or 0.0) for x in part["v"]],
            ok=[bool(x) for x in part["ok"]],
            rs=[None if x is None else int(x) for x in part["rs_rank"]], tt=part["tt"],
            sma50=part["sma_50"], sma200=part["sma_200"], high252=part["high_252"],
            vavg50=part["volume_avg_50"], vol50=part["vol50"],
        )  # fmt: skip


# ----------------------------------------------------------------------------- storing

RUN_COLUMNS = (
    "study_id",
    "run_id",
    "instrument_id",
    "symbol",
    "sector",
    "launch_date",
    "launch_close",
    "peak_date",
    "peak_close",
    "gain_pct",
    "gain_band",
    "sessions_to_peak",
    "complete",
    "to20",
    "to50",
    "to100",
    "max_pullback_pct",
    "pullbacks_10",
    "breakout_date",
    "breakout_gain_pct",
    "bases_count",
    "launch_stage",
    "launch_rs",
    "launch_tt",
    "launch_dist_high_pct",
    "launch_rs_led",
    "launch_eps_yoy",
    "launch_sales_yoy",
    "launch_eps_accel",
    "launch_promoter_chg",
    "launch_fii_chg",
    "launch_market",
    "base_length",
    "base_depth_pct",
    "base_tight_pct",
    "base_dryup",
    "vcp_before_breakout",
    "pct_above_50",
    "closes_below_50",
    "weeks_above_10w_pct",
    "rs90_share",
    "updown_volume",
    "climax_week",
    "climax_gain_pct",
    "climax_near_top",
    "heavy_break_sessions",
    "heavy_break_drawdown_pct",
    "rs_rollover_sessions",
    "stage34_sessions",
    "stage34",
    "giveback_pct",
    "giveback_of_gain_pct",
    "giveback_final",
    "sector_peers",
)


def run_row(study_id: str, run_id: int, r: Run) -> tuple[Any, ...]:
    first = r.bases[0] if r.bases else None
    lf = r.launch
    vals: dict[str, Any] = {
        "study_id": study_id, "run_id": run_id, "instrument_id": r.iid, "symbol": r.symbol,
        "sector": r.sector, "launch_date": r.launch_date, "launch_close": r.launch_close,
        "peak_date": r.peak_date, "peak_close": r.peak_close, "gain_pct": r.gain_pct,
        "gain_band": gain_band(r.gain_pct), "sessions_to_peak": r.sessions_to_peak,
        "complete": r.complete, "to20": r.to20, "to50": r.to50, "to100": r.to100,
        "max_pullback_pct": r.max_pullback_pct,
        "pullbacks_10": sum(x >= 10 for x in r.pullbacks),
        "breakout_date": r.base_dates[0][1] if r.base_dates else None,
        "breakout_gain_pct": r.breakout_gain_pct, "bases_count": max(0, len(r.bases) - 1),
        "launch_stage": lf.get("stage"), "launch_rs": lf.get("rs"), "launch_tt": lf.get("tt"),
        "launch_dist_high_pct": lf.get("dist_high_pct"), "launch_rs_led": lf.get("rs_led"),
        "launch_eps_yoy": lf.get("eps_yoy"), "launch_sales_yoy": lf.get("sales_yoy"),
        "launch_eps_accel": lf.get("eps_accel"), "launch_promoter_chg": lf.get("promoter_chg"),
        "launch_fii_chg": lf.get("fii_chg"), "launch_market": lf.get("market"),
        "base_length": first.length if first else None,
        "base_depth_pct": first.depth_pct if first else None,
        "base_tight_pct": first.tight_pct if first else None,
        "base_dryup": first.dryup if first else None,
        "vcp_before_breakout": r.vcp_before_breakout,
        "pct_above_50": r.pct_above_50, "closes_below_50": r.closes_below_50,
        "weeks_above_10w_pct": r.weeks_above_10w_pct, "rs90_share": r.rs90_share,
        "updown_volume": r.updown_volume, "climax_week": r.climax_week,
        "climax_gain_pct": r.climax_gain_pct, "climax_near_top": r.climax_near_top,
        "heavy_break_sessions": r.heavy_break_sessions,
        "heavy_break_drawdown_pct": r.heavy_break_drawdown_pct,
        "rs_rollover_sessions": r.rs_rollover_sessions, "stage34_sessions": r.stage34_sessions,
        "stage34": r.stage34, "giveback_pct": r.giveback_pct,
        "giveback_of_gain_pct": r.giveback_of_gain_pct, "giveback_final": r.giveback_final,
        "sector_peers": r.sector_peers,
    }  # fmt: skip
    return tuple(vals[c] for c in RUN_COLUMNS)


def store(
    con: Any, runs: list[Run], summary: dict[str, Any], source: str, vcp_source: str,
    now: datetime | None = None,
) -> str:  # fmt: skip
    """Replace the stored study with this one (one study is kept; it is rebuilt from the data
    at any time). Returns the study id."""
    now = now or datetime.now(UTC)
    study_id = f"sp-{now:%Y%m%dT%H%M%S}"
    run_rows = [run_row(study_id, k + 1, r) for k, r in enumerate(runs)]
    base_rows = [
        (study_id, k + 1, j + 1, b_start, b_day, b.high, b.depth_pct, b.length)
        for k, r in enumerate(runs)
        for j, (b, (b_start, b_day)) in enumerate(zip(r.bases, r.base_dates, strict=True))
    ]
    marks = ", ".join("?" for _ in RUN_COLUMNS)
    con.execute("BEGIN")
    try:
        for t in ("superperformance_bases", "superperformance_runs", "superperformance_studies"):
            con.execute(f"DELETE FROM {t}")
        con.execute(
            "INSERT INTO superperformance_studies VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [study_id, now, STUDY_VERSION, date.fromisoformat(summary["data_end"]), source,
             vcp_source, SURVIVORSHIP, SURVIVORSHIP_DETAIL,
             json.dumps(summary["params"]), json.dumps(summary, default=str)],
        )  # fmt: skip
        if run_rows:
            con.executemany(
                f"INSERT INTO superperformance_runs ({', '.join(RUN_COLUMNS)}) VALUES ({marks})",
                run_rows,
            )
        if base_rows:
            con.executemany(
                "INSERT INTO superperformance_bases VALUES (?, ?, ?, ?, ?, ?, ?, ?)", base_rows
            )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return study_id
