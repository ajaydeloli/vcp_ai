"""The stored Market Health Score (``market_health_daily``, MARKET_HEALTH_SPECIFICATION §7) for
the Market Overview: the latest day in full and the history of the score. Display only."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from vcp_scanner.api import models as m
from vcp_scanner.api.queries import Cur

HISTORY_SESSIONS = 252  # a year of history; the card shows 3 to 12 months of it

PILLAR_TITLES = {
    "index_trend": "Index trend",
    "breadth": "Breadth",
    "leadership": "Leadership",
    "accumulation": "Accumulation / distribution",
    "breakouts": "Breakout environment",
}
PART_LABELS = {
    "index_trend": {
        "above_50": "NIFTY 500 vs its 50-day average, %",
        "above_150": "NIFTY 500 vs its 150-day average, %",
        "above_200": "NIFTY 500 vs its 200-day average, %",
        "stacked": "Averages stacked 50 > 150 > 200 (of 2)",
        "rising_200": "200-day average over 20 sessions, %",
        "nifty50": "NIFTY 50 above its 50-day average",
        "smallcap250": "Smallcap 250 above its 50-day average",
    },
    "breadth": {
        "above_50": "Stocks above their 50-day average, %",
        "above_150": "Stocks above their 150-day average, %",
        "above_200": "Stocks above their 200-day average, %",
        "ad_trend": "Net advances over 20 sessions, share",
        "highs_lows": "New highs / (highs + lows), 10-day",
        "change": "Change in stocks above the 50-day, 10 sessions, pp",
        "vqi_gap": "VQI minus NIFTY 500 over a month, pp",
    },
    "leadership": {
        "tt_share": "Stocks passing the Trend Template, %",
        "leader_change": "Leaders vs 20 sessions ago, %",
        "highs_20": "Stocks at a 20-day high, %",
        "highs_52": "Stocks at a 52-week high, %",
        "sectors": "Sector indices in an uptrend, %",
        "leaders_vs_vqi": "Leaders of 20 sessions ago vs VQI, pp",
    },
    "accumulation": {
        "distribution": "Distribution days in 25 (NIFTY 500 60%, VQI 40%)",
        "accumulation_days": "NIFTY 500 accumulation days in 25",
        "up_down_value": "Traded value up / down, 10 sessions",
        "clustering": "NIFTY 500 distribution days in 10",
        "up_on_volume": "Accumulation share of volume days",
    },
    "breakouts": {
        "success": "Breakouts 5% above the pivot after 10 sessions, %",
        "held": "Breakouts holding the pivot 5 sessions, %",
        "failure": "Breakouts 3% below the pivot within 10, %",
        "median_return": "Median return after 5 and 10 sessions, %",
        "near_pivot": "Stocks set up near a pivot, %",
    },
}


def _pillars(raw: list[dict[str, Any]]) -> list[m.HealthScorePillar]:
    known = sum(p["weight"] for p in raw if p["score"] is not None)
    out = []
    for p in raw:
        labels = PART_LABELS.get(p["id"], {})
        out.append(m.HealthScorePillar(
            id=p["id"], title=PILLAR_TITLES.get(p["id"], p["id"]), weight=p["weight"],
            score=None if p["score"] is None else round(p["score"], 1),
            contribution=None if p["score"] is None or not known
            else round(p["score"] * p["weight"] / known, 1),
            note=p["note"],
            parts=[m.HealthScorePart(
                id=x["id"], label=labels.get(x["id"], x["id"]),
                reading=None if x["reading"] is None else round(x["reading"], 3),
                points=None if x["points"] is None else round(x["points"], 1), weight=x["weight"],
            ) for x in p["parts"]],
        ))  # fmt: skip
    return out


def _version(cur: Cur, end: date) -> str | None:
    """The settings version of the latest stored day on or before ``end``."""
    top = cur.execute(
        "SELECT settings_version FROM market_health_daily WHERE trade_date <= ?"
        " ORDER BY trade_date DESC, computed_at DESC LIMIT 1",
        [end],
    ).fetchone()
    return None if top is None else str(top[0])


def health_score(cur: Cur, end: date) -> m.HealthScore | None:
    """The latest stored day on or before ``end`` under the settings version that scored it,
    with the history of that version."""
    version = _version(cur, end)
    if version is None:
        return None
    rows = cur.execute(
        "SELECT trade_date, raw_score, raw_label, label, thrust FROM market_health_daily"
        " WHERE settings_version = ? AND trade_date <= ? ORDER BY trade_date DESC LIMIT ?",
        [version, end, HISTORY_SESSIONS],
    ).fetchall()[::-1]
    last = cur.execute(
        "SELECT trade_date, raw_score, raw_label, label, complete, exposure, pillars_json,"
        " caps_json, missing_json, features_json FROM market_health_daily"
        " WHERE settings_version = ? AND trade_date = ?",
        [version, rows[-1][0]],
    ).fetchone()
    assert last is not None
    thrusts = [r[0] for r in rows if r[4]]
    return m.HealthScore(
        day=last[0], settings_version=version, raw=last[1], raw_label=last[2], label=last[3],
        complete=bool(last[4]), exposure=last[5], pillars=_pillars(json.loads(last[6])),
        caps=[m.HealthScoreCap(**c) for c in json.loads(last[7])],
        missing=json.loads(last[8]), last_thrust=thrusts[-1] if thrusts else None,
        judged=int(json.loads(last[9]).get("judged") or 0),
        history=[m.HealthScoreDay(day=r[0], raw=r[1], raw_label=r[2], label=r[3],
                                  thrust=bool(r[4])) for r in rows],
    )  # fmt: skip


CHART_DAYS = 250  # sessions in the cards' charts
NIFTY50, SMALLCAP = "Nifty 50", "Nifty Smallcap 250"


def _rebase(values: list[float | None], base: float | None) -> list[float | None]:
    return [None if v is None or not base else 100.0 * v / base for v in values]


def score_points(cur: Cur, end: date) -> list[m.HealthScorePoint]:
    """The score's own readings per session (the latest settings version), oldest first, for
    the cards: one source for the score and the charts beside it."""
    version = _version(cur, end)
    if version is None:
        return []
    rows = cur.execute(
        "SELECT trade_date, vqi, features_json FROM market_health_daily"
        " WHERE settings_version = ? AND trade_date <= ? ORDER BY trade_date DESC LIMIT ?",
        [version, end, CHART_DAYS],
    ).fetchall()[::-1]
    if not rows:
        return []
    days = [r[0] for r in rows]
    feats = [json.loads(r[2]) for r in rows]
    closes: dict[str, dict[date, float]] = {NIFTY50: {}, SMALLCAP: {}}
    for name, d, c in cur.execute(
        "SELECT index_name, trade_date, close FROM index_daily WHERE trade_date BETWEEN ? AND ?"
        " AND close > 0 AND index_name IN (?, ?)",
        [days[0], days[-1], NIFTY50, SMALLCAP],
    ).fetchall():
        closes[str(name)][d] = float(c)

    def first(values: list[float | None]) -> float | None:
        return next((v for v in values if v), None)

    n500 = [f.get("n500_close") for f in feats]
    base = first(n500)
    n50 = [closes[NIFTY50].get(d) for d in days]
    small = [closes[SMALLCAP].get(d) for d in days]
    vqi = [None if r[1] is None else float(r[1]) for r in rows]
    series = {
        "nifty500": _rebase(n500, base),
        "n500_ma50": _rebase([f.get("n500_sma50") for f in feats], base),
        "n500_ma150": _rebase([f.get("n500_sma150") for f in feats], base),
        "n500_ma200": _rebase([f.get("n500_sma200") for f in feats], base),
        "nifty50": _rebase(n50, first(n50)),
        "smallcap250": _rebase(small, first(small)),
        "vqi": _rebase(vqi, first(vqi)),
    }
    out = []
    for i, (d, f) in enumerate(zip(days, feats, strict=True)):
        hi, lo = f.get("new_highs"), f.get("new_lows")
        share = f.get("highs_lows")
        out.append(m.HealthScorePoint(
            day=d, **{k: v[i] for k, v in series.items()}, kind=f.get("n500_kind"),
            above50=f.get("pct_above_50"), above150=f.get("pct_above_150"),
            above200=f.get("pct_above_200"), ad_ratio=f.get("ad_ratio"),
            ad_line=f.get("ad_line"), thrust_ratio=f.get("thrust_ratio"),
            thrust=bool(f.get("thrust")),
            net_highs=None if hi is None or lo is None else float(hi - lo),
            highs_share=None if share is None else 100.0 * share,
            tt_share=f.get("tt_share"), leaders=f.get("leaders"),
        ))  # fmt: skip
    return out


def score_warnings(score: m.HealthScore | None) -> list[m.HealthWarning]:
    """The divergence warning above the cards is the score's divergence cap, word for word, so
    the warning and the cap always agree."""
    if score is None:
        return []
    return [
        m.HealthWarning(id="divergence", text=c.reason) for c in score.caps if c.id == "divergence"
    ]
