"""Empirical check: does a provider return split/bonus-adjusted history? (audit P0-1)

Zerodha states that Kite historical prices are adjusted for corporate actions, and the
adjustment engine relies on that (``PROVIDER_ADJUSTED_SOURCES``). This module turns one known
split/bonus into a verdict by looking at the overnight move across its ex-date in the bars the
provider returns *today*:

* **ADJUSTED**: the ex-date open is close to the previous close (the pre-split bars were rescaled);
* **RAW**: the ex-date open is close to ``previous close x price_factor`` (no rescaling);
* **INCONCLUSIVE**: neither is clearly closer, the factor is too small to tell, or bars are missing.

Pure function, no I/O; the CLI (``vcp verify kite-adjustment``) supplies the bars.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from vcp_scanner.domain.market import Candle

#: A factor closer to 1 than this (|ln f| < ln 1.2, i.e. under ~17-20 %) cannot be told apart
#: from an ordinary overnight move.
_MIN_LOG_FACTOR = math.log(1.2)


class AdjustmentVerdict(StrEnum):
    ADJUSTED = "ADJUSTED"
    RAW = "RAW"
    INCONCLUSIVE = "INCONCLUSIVE"


@dataclass(frozen=True, slots=True)
class AdjustmentCheck:
    symbol: str
    ex_date: date
    price_factor: float  # e.g. 0.5 for a 2:1 split
    prev_close: float | None
    ex_open: float | None
    observed_ratio: float | None  # ex_open / prev_close
    verdict: AdjustmentVerdict
    detail: str


def classify_adjustment(
    symbol: str, ex_date: date, price_factor: float, bars: list[Candle]
) -> AdjustmentCheck:
    """Classify one action from the provider's bars around ``ex_date`` (any order)."""

    def result(
        prev: float | None, ex_open: float | None, verdict: AdjustmentVerdict, why: str
    ) -> AdjustmentCheck:
        ratio = ex_open / prev if prev and ex_open else None
        return AdjustmentCheck(symbol, ex_date, price_factor, prev, ex_open, ratio, verdict, why)

    if not (price_factor > 0 and math.isfinite(price_factor)):
        return result(None, None, AdjustmentVerdict.INCONCLUSIVE, "invalid price factor")
    log_factor = math.log(price_factor)
    if abs(log_factor) < _MIN_LOG_FACTOR:
        return result(None, None, AdjustmentVerdict.INCONCLUSIVE, "factor too small to tell")

    ordered = sorted(bars, key=lambda c: c.timestamp)
    before = [c for c in ordered if c.timestamp.date() < ex_date]
    on_or_after = [c for c in ordered if c.timestamp.date() >= ex_date]
    if not before or not on_or_after:
        return result(None, None, AdjustmentVerdict.INCONCLUSIVE, "no bars on both sides")
    prev_close, ex_open = before[-1].close, on_or_after[0].open
    if prev_close <= 0 or ex_open <= 0:
        return result(prev_close, ex_open, AdjustmentVerdict.INCONCLUSIVE, "non-positive price")

    observed = math.log(ex_open / prev_close)
    d_adjusted = abs(observed)  # adjusted history: no jump expected
    d_raw = abs(observed - log_factor)  # raw history: jump of ln(price_factor) expected
    # A verdict needs the observed move within a quarter of the expected jump (in log terms) of
    # one hypothesis: a 2:1 split must open within ~16 % of either candidate price.
    margin = abs(log_factor) / 4
    if d_adjusted < margin and d_adjusted < d_raw:
        return result(prev_close, ex_open, AdjustmentVerdict.ADJUSTED, "no jump at ex-date")
    if d_raw < margin and d_raw < d_adjusted:
        return result(prev_close, ex_open, AdjustmentVerdict.RAW, "jump matches the factor")
    return result(prev_close, ex_open, AdjustmentVerdict.INCONCLUSIVE, "jump matches neither")
