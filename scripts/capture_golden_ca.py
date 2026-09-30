"""Capture REAL golden corporate-action data points (audit Fix 5b; DATA_SPECIFICATION §18A).

For each verified split/bonus this records, from two independent sources:

* NSE bhavcopy (official end-of-day file, true raw prices): the last session before the
  ex-date and the ex-date session;
* Kite historical candles (provider-adjusted as of the fetch time): the same two sessions.

The fixture lets offline tests check that ``raw_close x our factor == Kite's adjusted close``,
that the raw ex-date gap matches the factor (so the gap detector would see it), and that the
fetch-time-aware engine never adjusts Kite's bars twice.

Run once (needs network and a valid KITE_ACCESS_TOKEN in .env), then commit the JSON:

    python scripts/capture_golden_ca.py

Data is real market data (not synthetic, AGENTS.md rule 10); every record carries its sources.
"""

from __future__ import annotations

import csv
import io
import json
import os
import sys
import zipfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "corporate_actions"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
NEW_FORMAT_FROM = date(2024, 7, 8)  # NSE switched the CM bhavcopy format around this date

# Verified against news reports / company filings (see "sources"); ratios use the adjustment
# engine's conventions: SPLIT = (old face value, new face value), BONUS = (bonus, held).
ACTIONS = [
    {
        "symbol": "IRCTC",
        "ex_date": "2021-10-28",
        "components": [["SPLIT", 10, 2]],
        "sources": [
            "https://www.business-standard.com/amp/article/markets/irctc-rally-15-as-scrip-turns-ex-stock-split-in-1-5-121102800326_1.html"
        ],
    },
    {
        "symbol": "TATASTEEL",
        "ex_date": "2022-07-28",
        "components": [["SPLIT", 10, 1]],
        "sources": [
            "https://www.business-standard.com/article/markets/tata-steel-jumps-5-after-turning-ex-date-for-1-10-stock-split-122072800439_1.html"
        ],
    },
    {
        "symbol": "NESTLEIND",
        "ex_date": "2024-01-05",
        "components": [["SPLIT", 10, 1]],
        "sources": [
            "https://www.business-standard.com/markets/news/nestle-india-slips-2-on-profit-booking-trades-1-10-ex-stock-split-124010500223_1.html"
        ],
    },
    {
        "symbol": "RELIANCE",
        "ex_date": "2024-10-28",
        "components": [["BONUS", 1, 1]],
        "sources": [
            "https://www.angelone.in/news/share-market/reliance-industries-sets-oct-28-record-date-bonus-issue"
        ],
    },
    {
        "symbol": "BAJFINANCE",
        "ex_date": "2025-06-16",
        "components": [["SPLIT", 2, 1], ["BONUS", 4, 1]],
        "sources": [
            "https://groww.in/blog/bajaj-finance-trades-ex-date-for-bonus-and-split-investor-guide"
        ],
    },
    {
        "symbol": "NESTLEIND",
        "ex_date": "2025-08-08",
        "components": [["BONUS", 1, 1]],
        "sources": [
            "https://www.angelone.in/news/market-updates/nestle-india-bonus-shares-ex-date-today-aug-8"
        ],
    },
    {
        "symbol": "HDFCBANK",
        "ex_date": "2025-08-26",
        "components": [["BONUS", 1, 1]],
        "sources": [
            "https://hdfcsky.com/news/hdfc-bank-to-issue-11-bonus-shares-ex-date-on-august-26"
        ],
    },
]


def _bhavcopy_url(d: date) -> str:
    if d < NEW_FORMAT_FROM:
        mon = d.strftime("%b").upper()
        return (
            "https://nsearchives.nseindia.com/content/historical/EQUITIES/"
            f"{d.year}/{mon}/cm{d.strftime('%d')}{mon}{d.year}bhav.csv.zip"
        )
    return (
        "https://nsearchives.nseindia.com/content/cm/"
        f"BhavCopy_NSE_CM_0_0_0_{d.strftime('%Y%m%d')}_F_0000.csv.zip"
    )


def _bhav_row(session: requests.Session, d: date, symbol: str) -> dict | None:
    """The symbol's EQ row from the bhavcopy of ``d``; None if no file (holiday) or no row."""
    url = _bhavcopy_url(d)
    resp = session.get(url, timeout=30)
    if resp.status_code != 200:
        return None
    with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
        text = z.read(z.namelist()[0]).decode()
    for row in csv.DictReader(io.StringIO(text)):
        row = {k.strip(): (v or "").strip() for k, v in row.items() if k}
        if d < NEW_FORMAT_FROM:
            if row.get("SYMBOL") == symbol and row.get("SERIES") == "EQ":
                return {
                    "date": d.isoformat(), "open": float(row["OPEN"]),
                    "high": float(row["HIGH"]), "low": float(row["LOW"]),
                    "close": float(row["CLOSE"]), "prev_close": float(row["PREVCLOSE"]),
                    "volume": int(float(row["TOTTRDQTY"])), "isin": row.get("ISIN"),
                    "url": url,
                }  # fmt: skip
        elif row.get("TckrSymb") == symbol and row.get("SctySrs") == "EQ":
            return {
                "date": d.isoformat(), "open": float(row["OpnPric"]),
                "high": float(row["HghPric"]), "low": float(row["LwPric"]),
                "close": float(row["ClsPric"]), "prev_close": float(row["PrvsClsgPric"]),
                "volume": int(float(row["TtlTradgVol"])), "isin": row.get("ISIN"), "url": url,
            }  # fmt: skip
    return None


def _last_session_before(session: requests.Session, ex: date, symbol: str) -> dict:
    for back in range(1, 10):
        row = _bhav_row(session, ex - timedelta(days=back), symbol)
        if row is not None:
            return row
    raise RuntimeError(f"no bhavcopy session found before {ex} for {symbol}")


def _factor(action: dict) -> float:
    """Price factor of one action (all its same-day components), engine conventions."""
    f = 1.0
    for kind, num, den in action["components"]:
        f *= den / num if kind == "SPLIT" else den / (num + den)
    return f


def _annotate(records: list[dict]) -> None:
    """Add price_factor, cumulative_price_factor (incl. later actions of the same stock) and
    kite_residual = Kite prev close / (raw prev close x cumulative factor)."""
    for a in records:
        cumulative = 1.0
        for b in records:
            if b["symbol"] == a["symbol"] and b["ex_date"] >= a["ex_date"]:
                cumulative *= _factor(b)
        a["price_factor"] = round(_factor(a), 10)
        a["cumulative_price_factor"] = round(cumulative, 10)
        residual = a["kite"]["prev"]["close"] / (a["raw"]["prev"]["close"] * cumulative)
        a["kite_residual"] = round(residual, 6)
        if abs(residual - 1) > 0.005:
            a["kite_residual_reason"] = (
                "Kite also adjusts this history for later large dividends "
                "(TATASTEEL Rs 3.60 dividends in 2024 and 2025, each ~2.2% of price: the "
                "Kite/raw ratio steps by ~2.2% at each). The local engine models "
                "splits/bonuses only."
            )


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from vcp_scanner.data.providers.kite import KiteProvider
    from vcp_scanner.domain.market import Instrument

    load_dotenv(".env")
    kite = KiteProvider(os.environ["KITE_API_KEY"], os.environ["KITE_ACCESS_TOKEN"])
    session = requests.Session()
    session.headers["User-Agent"] = UA

    records = []
    for spec in ACTIONS:
        symbol, ex = spec["symbol"], date.fromisoformat(spec["ex_date"])
        raw_prev = _last_session_before(session, ex, symbol)
        raw_ex = _bhav_row(session, ex, symbol)
        if raw_ex is None:
            raise RuntimeError(f"{symbol}: no bhavcopy row on the stated ex-date {ex}")
        fetched_at = datetime.now(UTC)
        bars = kite.get_historical_daily(
            Instrument(f"NSE_EQ|{symbol}", symbol), ex - timedelta(days=12), ex
        )
        by_date = {b.timestamp.date().isoformat(): b for b in bars}
        k_prev, k_ex = by_date[raw_prev["date"]], by_date[raw_ex["date"]]
        records.append(
            {
                **spec,
                "isin": raw_ex["isin"],
                "raw": {"prev": raw_prev, "ex": raw_ex},
                "kite": {
                    "fetched_at": fetched_at.isoformat(timespec="seconds"),
                    "prev": {
                        "date": raw_prev["date"],
                        "open": k_prev.open,
                        "close": k_prev.close,
                        "volume": k_prev.volume,
                    },
                    "ex": {
                        "date": raw_ex["date"],
                        "open": k_ex.open,
                        "close": k_ex.close,
                        "volume": k_ex.volume,
                    },
                },  # fmt: skip
            }
        )
        ratio = raw_ex["open"] / raw_prev["close"]
        print(
            f"{symbol:<11} ex {ex}  raw prev close {raw_prev['close']:>9.2f}  raw ex open "
            f"{raw_ex['open']:>9.2f} (x{ratio:.3f})  kite prev close {k_prev.close:>9.2f}"
        )

    _annotate(records)
    payload = {
        "description": (
            "REAL market data (not synthetic). Golden corporate-action checks, audit Fix 5b. "
            "raw = NSE CM bhavcopy (official EOD file, unadjusted); kite = Kite Connect "
            "historical candles, adjusted as of kite.fetched_at."
        ),
        "captured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "actions": records,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "golden_actions.json").write_text(json.dumps(payload, indent=1) + "\n")
    print(f"wrote {OUT / 'golden_actions.json'} ({len(records)} actions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
