"""Sync a provider's instrument dump into ``provider_instruments`` (audit P1-1).

The dump speaks the provider's language (Kite token + tradingsymbol). Each row is resolved to
the permanent ``instrument_id`` through the shared ``InstrumentResolver`` (ISIN first, then
exchange + symbol), so a renamed symbol keeps its identity. A row that resolves to no known
instrument is *counted and skipped*, never mapped to a freshly minted id: a mapping to an
instrument the database has never heard of would be an orphan.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from vcp_scanner.domain.errors import ProviderError
from vcp_scanner.domain.market import ProviderInstrumentMapping, ProviderMappingSyncResult

if TYPE_CHECKING:
    from vcp_scanner.data.identity import InstrumentResolver
    from vcp_scanner.data.providers.base import ProviderInstrumentSource
    from vcp_scanner.data.repositories.base import ProviderInstrumentRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ResolvedDump:
    """A provider dump after identity resolution."""

    mappings: list[ProviderInstrumentMapping]
    skipped_unresolved: int
    unresolved_sample: list[str]


def resolve_dump(source: ProviderInstrumentSource, resolver: InstrumentResolver) -> ResolvedDump:
    """Resolve every row of ``source``'s dump to a permanent instrument id.

    Raises ``ProviderError`` if the dump is empty or nothing in it is a known instrument:
    syncing that would close every open mapping.
    """
    dump = source.get_provider_instruments()
    if not dump:
        raise ProviderError("Provider returned an empty instrument dump; mappings not synced.")
    providers = {row.provider for row in dump}
    if len(providers) != 1:
        raise ProviderError(f"Instrument dump mixes providers {sorted(providers)}")

    mappings: list[ProviderInstrumentMapping] = []
    unresolved: list[str] = []
    for row in dump:
        instrument_id = resolver.resolve(
            isin=row.isin, symbol=row.provider_symbol, exchange=row.exchange
        )
        if instrument_id is None:
            unresolved.append(row.provider_symbol)
            continue
        mappings.append(
            ProviderInstrumentMapping(
                provider=row.provider,
                provider_instrument_id=row.provider_instrument_id,
                instrument_id=instrument_id,
                provider_symbol=row.provider_symbol,
                exchange=row.exchange,
                metadata={"name": row.name} if row.name else None,
            )
        )

    if not mappings:
        raise ProviderError(
            f"None of the {len(dump)} instruments in the {next(iter(providers))} dump is known "
            "locally; run `vcp ingest security-master` first."
        )
    return ResolvedDump(mappings, len(unresolved), unresolved[:10])


def sync_provider_mappings(
    source: ProviderInstrumentSource,
    repository: ProviderInstrumentRepository,
    resolver: InstrumentResolver,
    *,
    as_of: date,
) -> ProviderMappingSyncResult:
    """Fetch the provider's dump, resolve it, and record it as observed on ``as_of``."""
    resolved = resolve_dump(source, resolver)
    if resolved.skipped_unresolved:
        logger.warning(
            "%d provider instrument(s) match no known instrument and were not mapped "
            "(for example %s).",
            resolved.skipped_unresolved,
            ", ".join(resolved.unresolved_sample),
        )
    provider = resolved.mappings[0].provider
    return repository.sync_full_dump(
        provider,
        resolved.mappings,
        as_of,
        skipped_unresolved=resolved.skipped_unresolved,
    )
