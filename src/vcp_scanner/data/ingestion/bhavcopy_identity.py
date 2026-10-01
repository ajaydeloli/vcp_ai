"""Map bhavcopy rows to permanent instrument IDs over time (audit step 2.2).

The 2.0 spike showed that ISIN alone cannot carry identity through history: NSE issues a new
ISIN 0-1 trading day after a face-value split (TATASTEEL INE081A01012 -> INE081A01020 on
2022-07-29), and ``instruments.isin`` holds only the *current* ISIN. Resolution per row:

1. **Known ISIN** (any past period, or ``instruments.isin``) -> that instrument. If its open
   period has another symbol or ISIN, the period is closed and a new one opened.
2. **Unknown ISIN, same issuer** (:func:`same_issuer_equity`) as an instrument that is
   currently trading under the same symbol, or whose symbol stopped trading today while the
   row's ISIN is a later issue (a split and a rename on the same day) -> the same instrument,
   with an ``ISIN_CHANGE`` period. A seeded instrument with the row's symbol and no history
   yet is adopted the same way. A seed with a *later* ISIN of the same issuer, whose own
   symbol is not trading that day, is the row's future identity and is adopted too: a stock
   renamed and split since (KPIGLOBAL INE542W01017 -> KPIGREEN INE542W01025) keeps one
   history under today's id (found in the step 2.6 rebuild: 48 histories were split in two).
3. **Otherwise a new instrument.** Its ID is ``mint_instrument_id(symbol)``; if that ID is
   already held by a different issuer (NSE reused the symbol), the ISIN disambiguates it.

Days must be resolved in date order. A day at or before the last resolved day is looked up
from the stored per-day mapping (``daily_series``) instead, which keeps re-runs idempotent.
This module is pure: state comes in, changes come out, and the repository persists them.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date

from vcp_scanner.data.identity import mint_instrument_id, same_issuer_equity
from vcp_scanner.domain.bhavcopy import BhavcopyRow
from vcp_scanner.domain.errors import DataValidationError
from vcp_scanner.domain.market import IdentifierPeriod

FIRST_SEEN = "FIRST_SEEN"
ISIN_CHANGE = "ISIN_CHANGE"
SYMBOL_CHANGE = "SYMBOL_CHANGE"


class IdentityOrderError(DataValidationError):
    """A day was resolved out of order, or a re-run found rows the first run did not have."""


@dataclass(frozen=True, slots=True)
class SeedInstrument:
    """An instrument that exists (e.g. from EQUITY_L) but has no identifier history yet."""

    instrument_id: str
    symbol: str
    isin: str | None


@dataclass
class IdentityState:
    """Everything resolution needs, loaded once and updated in memory day by day."""

    open_periods: dict[str, IdentifierPeriod] = field(default_factory=dict)  # by instrument
    isin_owner: dict[str, str] = field(default_factory=dict)  # every ISIN ever seen -> id
    seeds: dict[str, SeedInstrument] = field(default_factory=dict)  # by symbol
    taken_ids: set[str] = field(default_factory=set)
    last_resolved: date | None = None


@dataclass(frozen=True, slots=True)
class DailySeriesRow:
    """Which instrument a (symbol, series) row of one day's file belongs to."""

    trade_date: date
    instrument_id: str
    symbol: str
    series: str
    isin: str


@dataclass
class DayIdentity:
    """Result of resolving one day: the mapping plus what must be persisted."""

    trade_date: date
    mapping: dict[tuple[str, str], str] = field(default_factory=dict)  # (symbol, series) -> id
    daily_series: list[DailySeriesRow] = field(default_factory=list)
    closed: list[IdentifierPeriod] = field(default_factory=list)  # with valid_to set
    opened: list[IdentifierPeriod] = field(default_factory=list)
    new_instruments: list[SeedInstrument] = field(default_factory=list)
    replayed: bool = False  # True when looked up from a previous run


def _serial(isin: str) -> str:
    """Issue serial of an Indian ISIN (characters 10-11); a face-value change increments it."""
    return isin[9:11].upper()


class BhavcopyIdentityResolver:
    def __init__(self, state: IdentityState) -> None:
        self._state = state
        self._today_symbols: set[str] = set()

    @property
    def state(self) -> IdentityState:
        return self._state

    def replay_day(
        self,
        trade_date: date,
        rows: Iterable[BhavcopyRow],
        stored: dict[tuple[str, str], str],
    ) -> DayIdentity:
        """Resolve a day already resolved before, from its stored mapping."""
        result = DayIdentity(trade_date, replayed=True)
        for row in rows:
            key = (row.symbol, row.series)
            iid = stored.get(key)
            if iid is None:
                raise IdentityOrderError(
                    f"{trade_date}: {row.symbol}/{row.series} was not in the first run of this "
                    "day; identity must be rebuilt from the start to include it"
                )
            result.mapping[key] = iid
        return result

    def resolve_day(self, trade_date: date, rows: Iterable[BhavcopyRow]) -> DayIdentity:
        """Resolve a day later than every day resolved so far."""
        state = self._state
        if state.last_resolved is not None and trade_date <= state.last_resolved:
            raise IdentityOrderError(
                f"{trade_date} is not after the last resolved day {state.last_resolved}; "
                "use replay_day for re-runs"
            )
        result = DayIdentity(trade_date)
        # Two passes: known ISINs first, so a new ISIN never claims a symbol that its rightful
        # owner (same symbol, known ISIN) also uses on this day.
        ordered = sorted(rows, key=lambda r: (r.isin not in state.isin_owner, r.symbol, r.series))
        self._today_symbols = {r.symbol for r in ordered}
        for row in ordered:
            iid = self._resolve_row(row, result)
            result.mapping[(row.symbol, row.series)] = iid
            result.daily_series.append(
                DailySeriesRow(trade_date, iid, row.symbol, row.series, row.isin)
            )
        state.last_resolved = trade_date
        return result

    # ------------------------------------------------------------------

    def _resolve_row(self, row: BhavcopyRow, result: DayIdentity) -> str:
        state = self._state
        iid = state.isin_owner.get(row.isin)
        if iid is None:
            iid = self._same_company_by_symbol(row)
        if iid is None:
            iid = self._new_instrument(row, result)
        self._track(iid, row, result)
        return iid

    def _same_company_by_symbol(self, row: BhavcopyRow) -> str | None:
        state = self._state
        today = self._today_symbols
        same_symbol: list[str] = []
        continued: list[str] = []
        for iid, period in state.open_periods.items():
            if not same_issuer_equity(period.isin, row.isin):
                continue
            if period.symbol == row.symbol:
                same_symbol.append(iid)
            elif period.symbol not in today and _serial(row.isin) > _serial(period.isin):
                continued.append(iid)
        if len(same_symbol) == 1:
            return same_symbol[0]
        if not same_symbol and len(continued) == 1:
            return continued[0]
        seed = state.seeds.get(row.symbol)
        if seed is not None and (seed.isin is None or same_issuer_equity(seed.isin, row.isin)):
            return seed.instrument_id
        future = [
            s.instrument_id
            for s in state.seeds.values()
            if s.isin is not None
            and same_issuer_equity(s.isin, row.isin)
            and _serial(s.isin) > _serial(row.isin)
            and s.symbol not in today
        ]
        return future[0] if len(future) == 1 else None

    def _new_instrument(self, row: BhavcopyRow, result: DayIdentity) -> str:
        state = self._state
        iid = mint_instrument_id("NSE", row.symbol)
        if iid in state.taken_ids:
            iid = mint_instrument_id("NSE", row.symbol, disambiguator=row.isin)
        if iid in state.taken_ids:  # pragma: no cover - would need a duplicate ISIN
            raise DataValidationError(f"cannot mint a unique id for {row.symbol} {row.isin}")
        state.taken_ids.add(iid)
        result.new_instruments.append(SeedInstrument(iid, row.symbol, row.isin))
        return iid

    def _track(self, iid: str, row: BhavcopyRow, result: DayIdentity) -> None:
        state = self._state
        state.isin_owner.setdefault(row.isin, iid)
        current = state.open_periods.get(iid)
        if current is not None and (current.symbol, current.isin) == (row.symbol, row.isin):
            return
        if current is None:
            reason = FIRST_SEEN
            # The seed is adopted: it may be filed under a newer symbol than this row's.
            for symbol in [s for s, seed in state.seeds.items() if seed.instrument_id == iid]:
                del state.seeds[symbol]
        else:
            if current.valid_from == row.trade_date:
                # Same instrument, two identities on one day (e.g. EQ and BE rows that
                # disagree). Keep the first; the per-day mapping still records both rows.
                return
            reasons = []
            if current.isin != row.isin:
                reasons.append(ISIN_CHANGE)
            if current.symbol != row.symbol:
                reasons.append(SYMBOL_CHANGE)
            reason = "+".join(reasons)
            closed = IdentifierPeriod(
                current.instrument_id,
                current.symbol,
                current.isin,
                current.valid_from,
                row.trade_date,
                current.change_reason,
                current.source,
            )
            result.closed.append(closed)
        opened = IdentifierPeriod(iid, row.symbol, row.isin, row.trade_date, None, reason)
        state.open_periods[iid] = opened
        result.opened.append(opened)
