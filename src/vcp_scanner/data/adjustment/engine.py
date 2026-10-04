"""Price Adjustment Engine (DATA_SPECIFICATION §19-21).

Consumes CorporateActionResolution records and computes cumulative price
and volume factors.
Applies these factors to daily_prices to yield daily_prices_adjusted.
"""

from __future__ import annotations

import bisect
import hashlib
import logging
from collections.abc import Callable, Iterable, Mapping
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from vcp_scanner.data.schema import DailyPriceAdjustedRow
from vcp_scanner.domain.corporate_actions import (
    PRICE_DERIVED_ACTIONS,
    PRICE_SCALING_ACTIONS,
    CorporateActionAdjustment,
    CorporateActionResolution,
    CorporateActionStatus,
    ExDatePrices,
    derived_factor,
    ratio_unconfirmed,
    rescale_prior_close,
    status_allows_adjustment,
    superseded_by_unmodelled,
)
from vcp_scanner.domain.enums import CorporateActionType
from vcp_scanner.domain.market import FINAL_PRICE_SOURCE, PROVIDER_ADJUSTED_SOURCES, Candle

logger = logging.getLogger(__name__)

#: 1.1 (audit P0-1): factors are fetch-time aware for provider-adjusted sources. A bar from a
#: provider in ``PROVIDER_ADJUSTED_SOURCES`` is only adjusted for actions whose ex-date is after
#: both its trade date and its fetch date (IST); the provider already applied the earlier ones.
#: 1.2 (audit step 2.4): rights issues and demergers get factors derived from raw (bhavcopy)
#: prices around their ex-date.
CALCULATION_VERSION = "1.2"

_IST = ZoneInfo("Asia/Kolkata")


def provider_applied_through(candle: Candle) -> date | None:
    """Last ex-date the provider had already applied to this bar, or None for raw sources.

    A provider-adjusted bar fetched on IST date F already reflects every action with
    ex-date <= F (Kite adjusts before the ex-date session). Without a known fetch time the bar
    is treated as raw, which is the historical behavior.
    """
    if candle.provider.upper() not in PROVIDER_ADJUSTED_SOURCES or candle.ingested_at is None:
        return None
    return candle.ingested_at.astimezone(_IST).date()


def _factor_threshold(candle: Candle) -> date:
    """Actions with ex-date strictly after this date still need to be applied locally."""
    trade_date = candle.timestamp.date()
    applied_through = provider_applied_through(candle)
    if applied_through is None:
        return trade_date
    return max(trade_date, applied_through)


class AdjustmentEngine:
    """Computes derived adjustment factors from resolved corporate actions."""

    def compute_factors(
        self,
        resolutions: list[CorporateActionResolution],
        ex_prices: Mapping[date, ExDatePrices] | None = None,
    ) -> list[CorporateActionAdjustment]:
        """Compute cumulative adjustment factors from a time-series of resolutions.

        Args:
            resolutions: All current resolutions for an instrument, in
                any order (they will be sorted by ex_date internally).
            ex_prices: Raw prices around ex-dates (:func:`ex_date_prices`). Rights issues and
                demergers get a factor only when their ex-date has raw prices here and the
                factor can be derived; otherwise they are left out (an underivable factor on
                raw prices is reported as a blocking quality event, not guessed). Splits and
                bonuses with raw prices here are checked against the ex-date gap (Fix C11):
                a factor the prices do not show is withheld.

        Returns:
            List of CorporateActionAdjustment rows ordered by effective_date.
        """
        if not resolutions:
            return []

        # Sort resolutions descending by ex_date so we can build backward cumulative factors.
        # Note: In standard price adjustment, today's price is 1.0.
        # An action in the past divides historical prices to match today's scale.

        # Only CONFIRMED / SINGLE_SOURCE / MANUAL_OVERRIDE resolutions may feed factors
        # (DATABASE_SCHEMA 17A). A PROVIDER_CONFLICT split must never move prices.
        valid = [
            (r, r.ex_date)
            for r in resolutions
            if r.ex_date is not None
            and self._is_price_affecting(r.action_type)
            and status_allows_adjustment(r.status)
            and not superseded_by_unmodelled(r, resolutions)  # Fix C11: re-parsed record
        ]
        # Several actions can share one ex-date (BAJFINANCE 2025-06-16: 1:2 split and 4:1
        # bonus). They are combined into ONE factor for that date: storing two rows for the
        # same (instrument, effective_date) made the repository close the first one, so the
        # combined factor 0.1 became 0.5 (found in audit Fix 5b on real data).
        by_date: dict[date, list[CorporateActionResolution]] = {}
        for r, ex_date in valid:
            by_date.setdefault(ex_date, []).append(r)

        adjustments: list[CorporateActionAdjustment] = []
        cum_pf = 1.0
        cum_vf = 1.0

        # Walk backwards from the most recent ex-date to the oldest.
        for ex_date in sorted(by_date, reverse=True):
            group = sorted(by_date[ex_date], key=lambda r: (r.action_type.value, r.resolution_id))
            pf, vf = 1.0, 1.0
            # Ratio actions first: a rights issue or demerger on the same ex-date must be
            # derived from the prior close on the post-split/bonus scale, or its factor would
            # count the split/bonus again (AHLEAST 2022-10-06: demerger + bonus 1:2; the
            # adjusted series kept a +50 % jump).
            for r in group:
                if r.action_type not in PRICE_DERIVED_ACTIONS:
                    single_pf, single_vf = self._compute_single_factor(r)
                    pf *= single_pf
                    vf *= single_vf
            # Fix C11: a split/bonus factor large enough to show in the raw prices but not seen
            # there is withheld (a blocking event names it, data.quality.events), unless a
            # person entered it by hand (MANUAL_OVERRIDE).
            manual = any(r.status is CorporateActionStatus.MANUAL_OVERRIDE for r in group)
            if not manual and ratio_unconfirmed(pf, (ex_prices or {}).get(ex_date)):
                logger.warning(
                    "%s %s: split/bonus factor %.4f not seen in the raw prices; withheld",
                    group[0].instrument_id, ex_date, pf,
                )  # fmt: skip
                pf, vf = 1.0, 1.0
            for r in group:
                if r.action_type in PRICE_DERIVED_ACTIONS:
                    prices = (ex_prices or {}).get(ex_date)
                    derived = (
                        derived_factor(r, rescale_prior_close(prices, pf))
                        if prices and prices.raw
                        else None
                    )
                    single_pf, single_vf = derived or (1.0, 1.0)
                    pf *= single_pf
                    vf *= single_vf

            # If the actions have no mathematical effect, no record is made
            if pf == 1.0 and vf == 1.0:
                continue

            cum_pf *= pf
            cum_vf *= vf

            adjustments.append(
                CorporateActionAdjustment(
                    resolution_id="+".join(r.resolution_id for r in group),
                    instrument_id=group[0].instrument_id,
                    effective_date=ex_date,
                    price_factor=pf,
                    volume_factor=vf,
                    cumulative_price_factor=cum_pf,
                    cumulative_volume_factor=cum_vf,
                    source="INTERNAL",
                    calculation_version=CALCULATION_VERSION,
                )
            )

        # Return them sorted ascending by effective date for storage
        adjustments.reverse()
        return adjustments

    def apply_factors(
        self,
        candles: list[Candle],
        adjustments: list[CorporateActionAdjustment],
    ) -> list[Candle]:
        """Apply cumulative adjustment factors to raw candles.

        Args:
            candles: Raw daily candles sorted ascending by date.
            adjustments: Cumulative adjustments sorted ascending by effective_date.

        Returns:
            New list of adjusted Candle objects.
        """
        if not candles:
            return []
        if not adjustments:
            return candles

        # We need to map each candle date to the correct cumulative factor.
        # A candle at time T is adjusted by the cumulative product of all actions
        # with ex_date > T.
        # Because `adjustments` stores `cumulative_price_factor` calculated by walking
        # backwards, the adjustment record for ex_date E holds the multiplier to apply
        # to all candles prior to E.

        lookup = self._factor_lookup(adjustments)
        adjusted_candles = []
        for candle in candles:
            pf, vf = lookup(candle)
            if pf == 1.0 and vf == 1.0:
                adjusted_candles.append(candle)
            else:
                ac = Candle(
                    instrument_id=candle.instrument_id,
                    timestamp=candle.timestamp,
                    open=candle.open * pf,
                    high=candle.high * pf,
                    low=candle.low * pf,
                    close=candle.close * pf,
                    volume=int(candle.volume * vf) if candle.volume is not None else None,
                    timeframe=candle.timeframe,
                    provider=candle.provider,
                    ingested_at=candle.ingested_at,
                )
                adjusted_candles.append(ac)

        return adjusted_candles

    def adjustment_version(self, adjustments: list[CorporateActionAdjustment]) -> str:
        """Deterministic version label for a set of cumulative adjustment factors.

        The label changes exactly when the factors change (a new corporate action, a
        corrected ratio), so earlier adjusted history stays reproducible under its own
        version while the new history is written under a new one
        (DATABASE_SCHEMA section 14). Identical factors always give the same label, which
        makes rebuilding idempotent.
        """
        if not adjustments:
            return f"adj-{CALCULATION_VERSION}-none"
        payload = "|".join(
            f"{a.effective_date.isoformat()}:{a.cumulative_price_factor!r}"
            f":{a.cumulative_volume_factor!r}"
            for a in sorted(adjustments, key=lambda a: a.effective_date)
        )
        digest = hashlib.sha256(payload.encode()).hexdigest()[:8]
        return f"adj-{CALCULATION_VERSION}-{digest}"

    def build_adjusted_rows(
        self,
        candles: list[Candle],
        adjustments: list[CorporateActionAdjustment],
        *,
        computed_at: datetime,
        data_snapshot_id: str | None = None,
    ) -> list[DailyPriceAdjustedRow]:
        """Turn raw candles into persistable ``daily_prices_adjusted`` rows.

        Same factor rule as ``apply_factors`` (a bar is adjusted by every action whose
        ex-date is strictly after it), but each row also records the factors applied and
        the ``adjustment_version``, and volume stays a float rather than being truncated.
        A missing volume stays NULL, never 0 (AGENTS.md rule 4). ``computed_at`` is
        injected by the caller; this method never reads the clock (rule 1).

        ``data_snapshot_id`` is recorded on every row as its lineage; ``None`` means the
        rows are unfrozen (``LIVE``) working data.
        """
        version = self.adjustment_version(adjustments)
        lookup = self._factor_lookup(adjustments)

        rows: list[DailyPriceAdjustedRow] = []
        for candle in candles:
            trade_date = candle.timestamp.date()
            pf, vf = lookup(candle)
            rows.append(
                DailyPriceAdjustedRow(
                    instrument_id=candle.instrument_id,
                    trade_date=trade_date,
                    open_adj=candle.open * pf,
                    high_adj=candle.high * pf,
                    low_adj=candle.low * pf,
                    close_adj=candle.close * pf,
                    volume_adj=None if candle.volume is None else candle.volume * vf,
                    adjustment_version=version,
                    price_factor_applied=Decimal(str(pf)),
                    volume_factor_applied=Decimal(str(vf)),
                    computed_at=computed_at,
                    computed_from_snapshot_id=data_snapshot_id,
                )
            )
        return rows

    @staticmethod
    def _factor_lookup(
        adjustments: list[CorporateActionAdjustment],
    ) -> Callable[[Candle], tuple[float, float]]:
        """Return ``candle -> (price_factor, volume_factor)``; the single factor rule.

        The adjustment for ex-date E holds the cumulative factor of E and every later
        action, so a bar takes the factor of the first adjustment whose ex-date is strictly
        after its threshold: the trade date for raw sources, and ``max(trade date, fetch
        date)`` for provider-adjusted sources (audit P0-1). Bars on or after the last
        ex-date are unadjusted.
        """
        ordered = sorted(adjustments, key=lambda a: a.effective_date)
        dates = [a.effective_date for a in ordered]

        def lookup(candle: Candle) -> tuple[float, float]:
            idx = bisect.bisect_right(dates, _factor_threshold(candle))
            if idx >= len(ordered):
                return 1.0, 1.0
            return ordered[idx].cumulative_price_factor, ordered[idx].cumulative_volume_factor

        return lookup

    def _is_price_affecting(self, action_type: CorporateActionType) -> bool:
        """SPLIT and BONUS (ratio) and RIGHTS and DEMERGER (derived from raw prices) affect
        price/volume. DIVIDEND is excluded: raw prices keep dividends (DATA_SPECIFICATION 21.2).
        """
        return action_type in PRICE_SCALING_ACTIONS or action_type in PRICE_DERIVED_ACTIONS

    def single_factor(self, resolution: CorporateActionResolution) -> tuple[float, float]:
        """Public (price_factor, volume_factor) of one action; (1.0, 1.0) if not applicable."""
        return self._compute_single_factor(resolution)

    def _compute_single_factor(self, resolution: CorporateActionResolution) -> tuple[float, float]:
        """Compute (price_factor, volume_factor) for a single action.

        Returns (1.0, 1.0) if no change.
        """
        if resolution.action_type == CorporateActionType.SPLIT:
            num = resolution.ratio_numerator
            den = resolution.ratio_denominator
            if num and den and num > 0 and den > 0:
                return (den / num, num / den)

        elif resolution.action_type == CorporateActionType.BONUS:
            # N bonus shares for every D shares held -> total shares = N + D
            num = resolution.ratio_numerator
            den = resolution.ratio_denominator
            if num and den and num > 0 and den > 0:
                total = num + den
                return (den / total, total / den)

        # Unrecognized or unhandled action type — no adjustment
        return 1.0, 1.0


def ex_date_prices(candles: Iterable[Candle], ex_dates: Iterable[date]) -> dict[date, ExDatePrices]:
    """Prices around each ex-date from an instrument's current raw bars (audit step 2.4).

    ``prior_close`` is the close of the last bar before the ex-date and ``ex_open`` the open of
    the bar on the ex-date itself (None if the stock did not trade that day). ``raw`` is True
    only when both bars come from ``FINAL_PRICE_SOURCE``. Dates with no earlier bar are omitted.
    """
    ordered = sorted(candles, key=lambda c: c.timestamp)
    days = [c.timestamp.date() for c in ordered]
    out: dict[date, ExDatePrices] = {}
    for ex in set(ex_dates):
        i = bisect.bisect_left(days, ex)
        if i == 0:
            continue
        prior = ordered[i - 1]
        on_ex = ordered[i] if i < len(ordered) and days[i] == ex else None
        raw = prior.provider == FINAL_PRICE_SOURCE and (
            on_ex is None or on_ex.provider == FINAL_PRICE_SOURCE
        )
        out[ex] = ExDatePrices(
            prior_close=prior.close,
            ex_open=None if on_ex is None else on_ex.open,
            raw=raw,
        )
    return out
