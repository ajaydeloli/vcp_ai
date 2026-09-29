"""Canonical instrument identity (DATA_SPECIFICATION section 6, 'Identity').

``instrument_id`` is the permanent internal key. ISIN is a mapped attribute, not the
identity, because symbols and ISINs change. Two rules keep every provider and table
joinable:

1. **One format.** A new instrument is minted exactly once, as ``<EXCHANGE>_EQ|<SYMBOL>``
   (for example ``NSE_EQ|RELIANCE``), by ``mint_instrument_id``. Providers must use this
   function and never build their own ID strings.
2. **Resolve before you mint.** At the ingestion boundary, a record's ID is passed through
   an ``InstrumentResolver`` (ISIN first, then exchange + symbol). If the instrument is
   already known, its existing permanent ID wins over the freshly minted one, so a symbol
   rename does not fork the history.
"""

from __future__ import annotations

import uuid
from typing import Protocol, runtime_checkable

_SEPARATOR = "|"


def mint_instrument_id(exchange: str, symbol: str) -> str:
    """Mint the canonical ID for an instrument seen for the first time."""
    return f"{exchange.strip().upper()}_EQ{_SEPARATOR}{symbol.strip().upper()}"


def symbol_from_instrument_id(instrument_id: str) -> str | None:
    """Inverse of ``mint_instrument_id``. ``None`` if the ID is not in minted form."""
    if _SEPARATOR not in instrument_id:
        return None
    return instrument_id.split(_SEPARATOR, 1)[1] or None


_ACTION_ID_NAMESPACE = uuid.UUID("6f0c1f4e-3b0a-4d6e-9a53-0d1f6b7f3c11")


def deterministic_action_id(source: str, *parts: object) -> str:
    """Stable ID for a provider's corporate-action observation.

    Providers used to mint a random uuid4 per fetch, so re-running an ingest duplicated
    every row. The same observation (same source and same identifying fields) now always
    maps to the same ID, which lets the repository skip what it already holds.
    """
    key = "|".join([source.upper(), *("" if p is None else str(p) for p in parts)])
    return str(uuid.uuid5(_ACTION_ID_NAMESPACE, key))


@runtime_checkable
class InstrumentResolver(Protocol):
    """Maps provider-side identifiers to the permanent internal ``instrument_id``."""

    def resolve(
        self,
        *,
        isin: str | None,
        symbol: str | None,
        exchange: str = "NSE",
    ) -> str | None:
        """Return the known permanent ID, or ``None`` if the instrument is unknown."""
        ...


def canonical_instrument_id(
    resolver: InstrumentResolver | None,
    instrument_id: str,
    *,
    isin: str | None = None,
    symbol: str | None = None,
    exchange: str = "NSE",
) -> str:
    """Return the permanent ID for a provider-reported ``instrument_id``.

    ``symbol`` is optional: when omitted it is recovered from a minted-form ID.
    With no resolver, or when the instrument is unknown, the reported ID is kept.
    """
    if resolver is None:
        return instrument_id
    resolved = resolver.resolve(
        isin=isin,
        symbol=symbol or symbol_from_instrument_id(instrument_id),
        exchange=exchange,
    )
    return resolved or instrument_id
