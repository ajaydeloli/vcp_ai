"""Recent listings (FRONTEND_SPECIFICATION 67.23): main-board stocks with fewer than 253 bars.

Display only. These stocks are outside the scan universe (UniverseConfig.min_history_days), so
nothing here is read by, or written to, the universe, the scans, the scores, the strategies or the
paper ledger. It reads the adjusted prices of the serving copy and computes plain measures; a
measure that needs more bars than the stock has is None ("not available"), never 0.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from vcp_scanner.api import models as m
from vcp_scanner.api.queries import Cur, _f
from vcp_scanner.domain.snapshot import LIVE_SNAPSHOT_ID as LIVE

#: the universe excludes a stock with fewer bars than this, so this page holds the rest
FULL_HISTORY_BARS = 253
RANGE_BARS = 10  # window of the recent price range


def _pct(a: float | None, b: float | None) -> float | None:
    return None if a is None or not b else (a / b - 1.0) * 100.0


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _sma(close: list[float | None], k: int) -> float | None:
    """Mean of the last k closes; None until k bars exist (or if one of them is missing)."""
    w = close[-k:]
    return sum(w) / k if len(close) >= k and None not in w else None  # type: ignore[arg-type]


def recent_listings(cur: Cur, data_time: datetime) -> m.IposResponse:
    last = cur.execute(
        "SELECT max(trade_date) FROM daily_prices_adjusted_current"
        " WHERE computed_from_snapshot_id = ?",
        [LIVE],
    ).fetchone()
    day = last[0] if last else None
    if day is None:
        return m.IposResponse(
            as_of=None, data_time=data_time, full_history_bars=FULL_HISTORY_BARS, rows=[]
        )
    rows = cur.execute(
        """
        WITH short AS (
            SELECT instrument_id FROM daily_prices_adjusted_current
            WHERE computed_from_snapshot_id = ?
            GROUP BY instrument_id
            HAVING count(*) < ? AND max(trade_date) = ?
        )
        SELECT p.instrument_id, p.trade_date, p.open_adj, p.high_adj, p.low_adj, p.close_adj,
               p.volume_adj
        FROM daily_prices_adjusted_current p JOIN short USING (instrument_id)
        WHERE p.computed_from_snapshot_id = ?
        ORDER BY p.instrument_id, p.trade_date
        """,
        [LIVE, FULL_HISTORY_BARS, day, LIVE],
    ).fetchall()
    by_id: dict[str, list[tuple[Any, ...]]] = defaultdict(list)
    for r in rows:
        by_id[r[0]].append(r)
    names = {
        r[0]: (r[1], r[2])
        for r in cur.execute(
            "SELECT i.instrument_id, i.symbol, i.company_name FROM instruments i"
        ).fetchall()
        if r[0] in by_id
    }
    # a stock traded in a series other than EQ (SME, trade-to-trade, ...) is not a main-board
    # listing; a stock with no series record is kept
    series = {
        r[0]: r[1]
        for r in cur.execute(
            "SELECT instrument_id, series FROM daily_series WHERE trade_date = ?", [day]
        ).fetchall()
    }

    out: list[m.IpoRow] = []
    for iid, bars in by_id.items():
        if iid not in names or series.get(iid, "EQ") != "EQ":
            continue
        n = len(bars)
        high = [_f(b[3]) for b in bars]
        low = [_f(b[4]) for b in bars]
        close = [_f(b[5]) for b in bars]
        vol = [_f(b[6]) for b in bars]
        c = close[-1]
        first_close = close[0]
        first_high = high[0]
        known_high = [x for x in high if x is not None]
        known_low = [x for x in low[-RANGE_BARS:] if x is not None]
        recent_high = [x for x in high[-RANGE_BARS:] if x is not None]
        top = max(known_high) if known_high else None

        s20, s50 = _sma(close, 20), _sma(close, 50)
        traded = [
            cl * v
            for cl, v in zip(close[-20:], vol[-20:], strict=True)
            if cl is not None and v is not None
        ]
        symbol, company = names[iid]
        out.append(
            m.IpoRow(
                instrument_id=iid,
                symbol=symbol,
                company=company,
                listing_date=bars[0][1],
                bars=n,
                ipo_open=_f(bars[0][2]),
                ipo_close=first_close,
                first_day_high=first_high,
                close=c,
                change_pct=_pct(c, close[-2]) if n >= 2 else None,
                since_listing_pct=_pct(c, first_close),
                vs_first_day_high_pct=_pct(c, first_high),
                high_since_listing=top,
                from_high_pct=_pct(c, top),
                sma20=s20,
                sma50=s50,
                vs_sma20_pct=_pct(c, s20),
                vs_sma50_pct=_pct(c, s50),
                range_pct=(
                    (max(recent_high) - min(known_low)) / c * 100.0
                    if n >= RANGE_BARS and recent_high and known_low and c
                    else None
                ),
                avg_traded_value=_mean(traded),
            )
        )
    out.sort(key=lambda r: (r.listing_date, r.symbol), reverse=True)
    return m.IposResponse(
        as_of=day, data_time=data_time, full_history_bars=FULL_HISTORY_BARS, rows=out
    )
