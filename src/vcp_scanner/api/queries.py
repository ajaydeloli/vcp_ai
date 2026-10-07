"""SQL behind the API endpoints, all against the read-only serving copy (live snapshot)."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import duckdb

from vcp_scanner.api import models as m
from vcp_scanner.api.context import Context, StrategySpec
from vcp_scanner.backtest.regime import DayBreadth, breadth_regime, ew_regime
from vcp_scanner.domain.features import FEATURES_CALCULATION_VERSION
from vcp_scanner.domain.snapshot import LIVE_SNAPSHOT_ID as LIVE
from vcp_scanner.research.strategy_review import POINTS

Cur = duckdb.DuckDBPyConnection


def _f(x: Any) -> float | None:
    return None if x is None else float(x)


def latest_prices_date(cur: Cur) -> date | None:
    row = cur.execute("SELECT max(trade_date) FROM bhavcopy_files WHERE status = 'OK'").fetchone()
    return row[0] if row else None


def latest_scan(cur: Cur, spec: StrategySpec) -> date | None:
    row = cur.execute(
        "SELECT max(as_of_date) FROM setup_scores WHERE strategy_id = ? AND config_hash = ?"
        " AND data_snapshot_id = ?",
        [spec.strategy_id, spec.config_hash, LIVE],
    ).fetchone()
    return row[0] if row else None


def ledger_through(cur: Cur, rule_set: str, spec: StrategySpec) -> date | None:
    row = cur.execute(
        "SELECT max(event_date) FROM paper_events WHERE rule_set = ? AND strategy_id = ?"
        " AND config_hash = ? AND event_type = 'DAY_CLOSED'",
        [rule_set, spec.strategy_id, spec.config_hash],
    ).fetchone()
    return row[0] if row else None


def resolve_symbol(cur: Cur, symbol: str) -> tuple[str, str, str | None] | None:
    """(instrument id, symbol, company) of a symbol or instrument id, active listing first."""
    row = cur.execute(
        "SELECT instrument_id, symbol, company_name FROM instruments"
        " WHERE upper(symbol) = upper(?) OR instrument_id = ?"
        " ORDER BY is_active DESC NULLS LAST, valid_to DESC NULLS FIRST LIMIT 1",
        [symbol, symbol],
    ).fetchone()
    return None if row is None else (str(row[0]), str(row[1]), row[2])


def search_symbols(cur: Cur, text: str, limit: int) -> list[m.SearchHit]:
    """Instruments whose symbol starts with, or whose company name contains, ``text``; an exact
    symbol first, then symbol prefixes, then names (top-bar search box)."""
    needle = text.strip().upper()
    rows = cur.execute(
        "SELECT instrument_id, symbol, company_name FROM instruments"
        " WHERE upper(symbol) LIKE ? || '%'"
        " OR upper(coalesce(company_name, '')) LIKE '%' || ? || '%'"
        " ORDER BY (upper(symbol) = ?) DESC, (upper(symbol) LIKE ? || '%') DESC,"
        " is_active DESC NULLS LAST, symbol LIMIT ?",
        [needle, needle, needle, needle, limit],
    ).fetchall()
    return [m.SearchHit(instrument_id=str(r[0]), symbol=str(r[1]), company=r[2]) for r in rows]


def tiers(spec: StrategySpec) -> list[m.Tier]:
    return [m.Tier(name=k, grade=g, ranked=g >= spec.min_grade) for k, g in spec.grades.items()]


# --- setups ---------------------------------------------------------------------------------

_SETUP_SQL = """
SELECT p.instrument_id, coalesce(i.symbol, p.instrument_id), i.company_name,
       p.classification, p.grade, p.status, p.confirmation_state, s.eligible,
       s.final_setup_score, s.trend_score, s.vcp_score, s.volume_score, s.rs_score,
       s.ranking_percentile, t.rs_rank, p.pivot_price, p.pivot_distance_pct,
       p.stop_reference_price, p.base_start_date, p.base_end_date, p.base_duration_days,
       p.base_depth_pct, b.breakout_date
FROM setups p
JOIN setup_scores s
  ON s.strategy_id = p.strategy_id AND s.instrument_id = p.instrument_id
 AND s.as_of_date = p.as_of_date AND s.config_hash = p.config_hash
 AND s.data_snapshot_id = p.data_snapshot_id
LEFT JOIN instruments i ON i.instrument_id = p.instrument_id
LEFT JOIN breakout_events b ON b.breakout_event_id = p.breakout_event_id
LEFT JOIN trend_template_results t
  ON t.instrument_id = p.instrument_id AND t.as_of_date = p.as_of_date
 AND t.config_hash = ? AND t.data_snapshot_id = ?
WHERE p.strategy_id = ? AND p.config_hash = ? AND p.data_snapshot_id = ?
  AND p.as_of_date = ? AND p.is_primary
"""


def _closes(
    cur: Cur, ids: Sequence[str], day: date
) -> dict[str, tuple[float | None, float | None]]:
    """(close on ``day``, change % vs the previous close) per instrument."""
    out: dict[str, tuple[float | None, float | None]] = {}
    if not ids:
        return out
    rows = cur.execute(
        """
        SELECT instrument_id, trade_date, close_adj, rn FROM (
            SELECT instrument_id, trade_date, close_adj,
                   row_number() OVER (PARTITION BY instrument_id ORDER BY trade_date DESC) rn
            FROM daily_prices_adjusted_current
            WHERE computed_from_snapshot_id = ? AND trade_date <= ? AND trade_date > ?
              AND instrument_id IN (SELECT unnest(?))
        ) WHERE rn <= 2
        """,
        [LIVE, day, day - timedelta(days=14), list(ids)],
    ).fetchall()
    last: dict[str, float | None] = {}
    prev: dict[str, float | None] = {}
    for iid, d, close, rn in rows:
        if rn == 1 and d == day:
            last[iid] = _f(close)
        elif rn == 2 or (rn == 1 and d != day):
            prev[iid] = _f(close)
    for iid in ids:
        c, p = last.get(iid), prev.get(iid)
        out[iid] = (c, None if c is None or not p else (c / p - 1.0) * 100.0)
    return out


def setup_rows(
    cur: Cur, ctx: Context, spec: StrategySpec, day: date, *, eligible: bool = True,
    min_grade: int | None = None, status: str | None = None, limit: int | None = None,
) -> list[m.SetupRow]:  # fmt: skip
    sql = _SETUP_SQL
    params: list[Any] = [ctx.scan_hash, LIVE, spec.strategy_id, spec.config_hash, LIVE, day]
    if eligible:
        sql += " AND s.eligible"
    if min_grade is not None:
        sql += " AND p.grade >= ?"
        params.append(min_grade)
    if status is not None:
        sql += " AND upper(p.status) = upper(?)"
        params.append(status)
    sql += " ORDER BY s.eligible DESC, s.final_setup_score DESC NULLS LAST, 2"
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    rows = cur.execute(sql, params).fetchall()
    closes = _closes(cur, [str(r[0]) for r in rows], day)
    out = []
    for r in rows:
        close, change = closes[str(r[0])]
        out.append(
            m.SetupRow(
                strategy_id=spec.strategy_id, instrument_id=str(r[0]), symbol=str(r[1]),
                company=r[2], classification=str(r[3]), grade=int(r[4]), status=str(r[5]),
                confirmation_state=r[6], eligible=bool(r[7]), score=_f(r[8]),
                trend_score=_f(r[9]), pattern_score=_f(r[10]), volume_score=_f(r[11]),
                rs_score=_f(r[12]), percentile=_f(r[13]),
                rs_rank=None if r[14] is None else int(r[14]), close=close, change_pct=change,
                pivot=_f(r[15]), pivot_distance_pct=_f(r[16]), stop=_f(r[17]), base_start=r[18],
                base_end=r[19], base_days=None if r[20] is None else int(r[20]),
                base_depth_pct=_f(r[21]), breakout_date=r[22],
            )
        )  # fmt: skip
    return out


def overlap(cur: Cur, ctx: Context, day: date) -> list[m.OverlapRow]:
    by: dict[str, list[m.SetupRow]] = defaultdict(list)
    for spec in ctx.strategies:
        for row in setup_rows(cur, ctx, spec, day, eligible=True):
            by[row.instrument_id].append(row)
    rows = []
    for iid, items in by.items():
        if len(items) < 2:
            continue
        rows.append(
            m.OverlapRow(
                instrument_id=iid, symbol=items[0].symbol, company=items[0].company,
                strategies=[m.OverlapEntry(strategy_id=r.strategy_id,
                                           classification=r.classification, grade=r.grade,
                                           score=r.score) for r in items],
            )
        )  # fmt: skip
    rows.sort(key=lambda r: (-len(r.strategies), r.symbol))
    return rows


# --- screener -------------------------------------------------------------------------------

SCREENER_SORTS = (
    "symbol", "rs_rank", "trend_score", "conditions_passed", "close", "change_pct", "score",
    "grade", "pivot_distance_pct",
)  # fmt: skip

_SCREENER_SQL = """
SELECT t.instrument_id, coalesce(i.symbol, t.instrument_id), i.company_name, t.weekly_stage,
       t.trend_template_pass, t.rs_rank, t.trend_score, c.passed, c.total, c.near_high
FROM trend_template_results t
LEFT JOIN instruments i ON i.instrument_id = t.instrument_id
LEFT JOIN (
    SELECT instrument_id,
           count(DISTINCT condition_id) FILTER (WHERE passed) AS passed,
           count(DISTINCT condition_id) AS total,
           bool_or(passed) FILTER (WHERE condition_name = 'near_52w_high') AS near_high
    FROM trend_template_conditions
    WHERE as_of_date = ? AND config_hash = ? AND data_snapshot_id = ?
    GROUP BY instrument_id
) c ON c.instrument_id = t.instrument_id
WHERE t.as_of_date = ? AND t.config_hash = ? AND t.data_snapshot_id = ?
"""


def screener(
    cur: Cur, ctx: Context, spec: StrategySpec, day: date, f: SimpleNamespace,
    data_time: datetime,
) -> m.ScreenerResponse:  # fmt: skip
    """Every stock of the Trend Template scan on ``day`` with its strategy setup (if any),
    filtered, sorted and paged on the server. ``f`` carries the query parameters."""
    base = cur.execute(
        _SCREENER_SQL, [day, ctx.scan_hash, LIVE, day, ctx.scan_hash, LIVE]
    ).fetchall()
    ids = [str(r[0]) for r in base]
    closes = _closes(cur, ids, day)
    setups = {r.instrument_id: r for r in setup_rows(cur, ctx, spec, day, eligible=False)}
    stage_counts: dict[str, int] = defaultdict(int)
    rows: list[m.ScreenerRow] = []
    for r in base:
        iid = str(r[0])
        close, change = closes[iid]
        s = setups.get(iid)
        stage_counts[str(r[3] or "UNKNOWN")] += 1
        rows.append(
            m.ScreenerRow(
                instrument_id=iid, symbol=str(r[1]), company=r[2], stage=r[3],
                trend_template_pass=r[4], conditions_passed=None if r[7] is None else int(r[7]),
                conditions_total=None if r[8] is None else int(r[8]), near_52w_high=r[9],
                rs_rank=None if r[5] is None else int(r[5]), trend_score=_f(r[6]), close=close,
                change_pct=change, classification=s.classification if s else None,
                grade=s.grade if s else None, status=s.status if s else None,
                score=s.score if s else None,
                pivot_distance_pct=s.pivot_distance_pct if s else None,
                eligible=s.eligible if s else None,
            )
        )  # fmt: skip
    needle = (f.q or "").strip().upper()

    def keep(x: m.ScreenerRow) -> bool:
        checks = (
            not needle or needle in x.symbol.upper() or needle in (x.company or "").upper(),
            not f.symbols or x.symbol.upper() in f.symbols,
            not f.stages or x.stage in f.stages,
            f.tt_pass is None or x.trend_template_pass == f.tt_pass,
            f.near_high is None or x.near_52w_high == f.near_high,
            f.min_rs is None or (x.rs_rank is not None and x.rs_rank >= f.min_rs),
            f.min_conditions is None
            or (x.conditions_passed is not None and x.conditions_passed >= f.min_conditions),
            not f.has_setup or x.eligible is True,
            f.min_grade is None or (x.grade is not None and x.grade >= f.min_grade),
            f.status is None or (x.status or "").upper() == f.status.upper(),
        )
        return all(checks)

    kept = [x for x in rows if keep(x)]
    key = f.sort
    present = [x for x in kept if getattr(x, key) is not None]
    absent = [x for x in kept if getattr(x, key) is None]
    present.sort(key=lambda x: (getattr(x, key), x.symbol), reverse=f.descending)
    if key == "symbol":
        present.sort(key=lambda x: x.symbol, reverse=f.descending)
    ordered = present + sorted(absent, key=lambda x: x.symbol)
    start = (f.page - 1) * f.page_size
    return m.ScreenerResponse(
        as_of=day, data_time=data_time, strategy_id=spec.strategy_id, scanned=len(rows),
        total=len(ordered), page=f.page, page_size=f.page_size,
        stage_counts=dict(sorted(stage_counts.items())), rows=ordered[start : start + f.page_size],
    )  # fmt: skip


def screener_day(cur: Cur, ctx: Context) -> date | None:
    row = cur.execute(
        "SELECT max(as_of_date) FROM trend_template_results WHERE config_hash = ?"
        " AND data_snapshot_id = ?",
        [ctx.scan_hash, LIVE],
    ).fetchone()
    return row[0] if row else None


def summary(cur: Cur, ctx: Context, day: date, data_time: datetime) -> m.SummaryResponse:
    universe = cur.execute(
        """
        SELECT count(*) FILTER (WHERE u.eligible) FROM (
            SELECT universe_snapshot_id FROM scan_runs
            WHERE scan_type = 'TREND_TEMPLATE' AND status = 'COMPLETED' AND as_of_date = ?
              AND scan_config_hash = ? AND data_snapshot_id = ?
            ORDER BY completed_at DESC LIMIT 1
        ) r JOIN universe_memberships u USING (universe_snapshot_id)
        """,
        [day, ctx.scan_hash, LIVE],
    ).fetchone()
    scan = cur.execute(
        "SELECT count(*), count(*) FILTER (WHERE trend_template_pass) FROM trend_template_results"
        " WHERE as_of_date = ? AND config_hash = ? AND data_snapshot_id = ?",
        [day, ctx.scan_hash, LIVE],
    ).fetchone()
    scanned = int(scan[0]) if scan and scan[0] else None
    parts = []
    for spec in ctx.strategies:
        rows = setup_rows(cur, ctx, spec, day, eligible=True)
        top = [r.score for r in rows[:10] if r.score is not None]
        b = cur.execute(
            "SELECT count(*) FROM breakout_events WHERE strategy_id = ? AND config_hash = ?"
            " AND breakout_date = ?",
            [spec.strategy_id, spec.config_hash, day],
        ).fetchone()
        parts.append(
            m.SummaryStrategy(
                strategy_id=spec.strategy_id, ranked=len(rows),
                grade2_plus=sum(r.grade >= 2 for r in rows), breakouts=int(b[0]) if b else 0,
                mean_top10_score=sum(top) / len(top) if top else None,
            )
        )  # fmt: skip
    return m.SummaryResponse(
        as_of=day, data_time=data_time,
        universe_size=int(universe[0]) if universe and universe[0] else None, scanned=scanned,
        trend_template_pass=int(scan[1]) if scanned and scan else None, strategies=parts,
    )  # fmt: skip


# --- market ---------------------------------------------------------------------------------


def market_days(cur: Cur, ctx: Context, end: date, days: int) -> list[m.MarketDay]:
    from vcp_scanner.data.repositories.duckdb_backtest_repository import DuckDBBacktestRepository

    # The repository only uses ``store.conn``; the serving copy is read-only.
    repo = DuckDBBacktestRepository(SimpleNamespace(conn=cur), LIVE)  # type: ignore[arg-type]
    start = end - timedelta(days=int(days * 7 / 5) + 10)
    rows: list[DayBreadth] = [
        r for r in repo.breadth(start, end, ctx.scan_hash, FEATURES_CALCULATION_VERSION)
        if r.day <= end
    ]  # fmt: skip
    rows.sort(key=lambda r: r.day)
    regime = ew_regime(rows) if ctx.regime_rule == "ew50" else breadth_regime(rows)
    index: list[float] = []
    level = 1.0
    for r in rows:
        if r.mean_return is not None:
            level *= 1.0 + r.mean_return
        index.append(level)
    shown = rows[-days:]
    base = index[len(rows) - len(shown)] if shown else 1.0
    out = []
    for k, r in enumerate(shown):
        i = len(rows) - len(shown) + k
        ma = sum(index[i - 49 : i + 1]) / 50 if i >= 49 else None
        out.append(
            m.MarketDay(
                day=r.day,
                breadth_pct=100.0 * r.above / r.with_average if r.with_average else None,
                index=100.0 * index[i] / base,
                index_ma50=None if ma is None else 100.0 * ma / base,
                regime_on=bool(regime.get(r.day, False)),
            )
        )  # fmt: skip
    return out


# --- stocks ---------------------------------------------------------------------------------


def bars(cur: Cur, instrument_id: str, days: int, end: date | None = None) -> list[m.Bar]:
    """Adjusted bars (oldest first) with 20/50/200-day averages computed from the closes; an
    average is null until that many bars exist."""
    rows = cur.execute(
        "SELECT trade_date, open_adj, high_adj, low_adj, close_adj, volume_adj"
        " FROM daily_prices_adjusted_current WHERE computed_from_snapshot_id = ?"
        " AND instrument_id = ? AND (? IS NULL OR trade_date <= ?)"
        " ORDER BY trade_date DESC LIMIT ?",
        [LIVE, instrument_id, end, end, days + 199],
    ).fetchall()
    rows.reverse()
    closes = [_f(r[4]) for r in rows]

    def sma(i: int, n: int) -> float | None:
        window = closes[i - n + 1 : i + 1] if i >= n - 1 else []
        return sum(window) / n if window and None not in window else None  # type: ignore[arg-type]

    out = [
        m.Bar(day=r[0], open=_f(r[1]), high=_f(r[2]), low=_f(r[3]), close=_f(r[4]),
              volume=_f(r[5]), sma20=sma(i, 20), sma50=sma(i, 50), sma200=sma(i, 200))
        for i, r in enumerate(rows)
    ]  # fmt: skip
    return out[-days:]


def stock_setups(
    cur: Cur, ctx: Context, instrument_id: str, symbol: str, company: str | None, day: date,
    data_time: datetime,
) -> m.StockSetupsResponse:  # fmt: skip
    tt = cur.execute(
        "SELECT weekly_stage, weekly_stage2_pass, trend_template_pass FROM"
        " trend_template_results WHERE instrument_id = ? AND as_of_date = ? AND config_hash = ?"
        " AND data_snapshot_id = ? LIMIT 1",
        [instrument_id, day, ctx.scan_hash, LIVE],
    ).fetchone()
    conditions = [
        m.Condition(name=str(r[0]), measurement=_f(r[1]), threshold=_f(r[2]),
                    passed=None if r[3] is None else bool(r[3]))
        for r in cur.execute(
            "SELECT condition_name, measurement, threshold, passed FROM trend_template_conditions"
            " WHERE instrument_id = ? AND as_of_date = ? AND config_hash = ?"
            " AND data_snapshot_id = ? ORDER BY condition_id",
            [instrument_id, day, ctx.scan_hash, LIVE],
        ).fetchall()
    ]  # fmt: skip
    chart = {b.day: b for b in bars(cur, instrument_id, 900, day)}
    setups: list[m.StockSetup] = []
    for spec in ctx.strategies:
        row = cur.execute(
            """
            SELECT p.setup_id, p.classification, p.grade, p.status, s.eligible,
                   s.final_setup_score, p.pivot_price, p.stop_reference_price, p.base_start_date,
                   p.base_end_date, p.base_depth_pct, b.breakout_date, p.details_json, s.scan_id,
                   p.base_high, p.base_low, p.prior_advance_pct, p.dryup_volume_ratio,
                   p.unmet_rules, p.invalidation_reasons, p.confirmation_state
            FROM setups p
            JOIN setup_scores s
              ON s.strategy_id = p.strategy_id AND s.instrument_id = p.instrument_id
             AND s.as_of_date = p.as_of_date AND s.config_hash = p.config_hash
             AND s.data_snapshot_id = p.data_snapshot_id
            LEFT JOIN breakout_events b ON b.breakout_event_id = p.breakout_event_id
            WHERE p.strategy_id = ? AND p.config_hash = ? AND p.data_snapshot_id = ?
              AND p.instrument_id = ? AND p.as_of_date = ? AND p.is_primary
            """,
            [spec.strategy_id, spec.config_hash, LIVE, instrument_id, day],
        ).fetchone()
        if row is None:
            continue
        details: dict[str, Any] = json.loads(row[12] or "{}")
        details.update(
            base_high=_f(row[14]), base_low=_f(row[15]), prior_advance_pct=_f(row[16]),
            dryup_volume_ratio=_f(row[17]), confirmation_state=row[20],
            invalidation_reasons=row[19], unmet_rules=json.loads(row[18] or "{}"),
        )  # fmt: skip
        points = []
        for key, (label, col, _up) in POINTS.items():
            when = details.get(key)
            if isinstance(when, str):
                d = date.fromisoformat(when)
                bar = chart.get(d)
                price = None if bar is None else (bar.high if col == "h" else bar.low)
                points.append(m.Point(label=label, day=d, price=price))
        contractions = [
            m.Contraction(sequence=int(c[0]), peak_date=c[1], peak_price=_f(c[2]),
                          trough_date=c[3], trough_price=_f(c[4]), depth_pct=_f(c[5]))
            for c in cur.execute(
                "SELECT sequence_number, peak_date, peak_price, trough_date, trough_price,"
                " depth_pct FROM vcp_contractions WHERE vcp_pattern_id = ? ORDER BY 1",
                [row[0]],
            ).fetchall()
        ] if spec.strategy_id == "vcp" else []  # fmt: skip
        parts = [
            m.ScorePart(component=str(c[0]), sub_component=str(c[1]), raw=_f(c[2]),
                        normalized=_f(c[3]), points=_f(c[4]), max_points=_f(c[5]))
            for c in cur.execute(
                "SELECT component, sub_component, raw_measurement, normalized_0_100, points,"
                " max_points FROM score_components WHERE scan_id = ? AND instrument_id = ?"
                " ORDER BY component, weight_within_component DESC",
                [row[13], instrument_id],
            ).fetchall()
        ]  # fmt: skip
        setups.append(
            m.StockSetup(
                strategy_id=spec.strategy_id, classification=str(row[1]), grade=int(row[2]),
                status=str(row[3]), eligible=bool(row[4]), score=_f(row[5]), pivot=_f(row[6]),
                stop=_f(row[7]), base_start=row[8], base_end=row[9], base_depth_pct=_f(row[10]),
                breakout_date=row[11], points=points, contractions=contractions,
                score_parts=parts, details=details,
            )
        )  # fmt: skip
    today = chart.get(day)
    return m.StockSetupsResponse(
        as_of=day, data_time=data_time, symbol=symbol, company=company,
        close=None if today is None else today.close,
        weekly_stage=None if tt is None else tt[0],
        weekly_stage2_pass=None if tt is None or tt[1] is None else bool(tt[1]),
        trend_template_pass=None if tt is None or tt[2] is None else bool(tt[2]),
        conditions=conditions, setups=setups,
    )  # fmt: skip
