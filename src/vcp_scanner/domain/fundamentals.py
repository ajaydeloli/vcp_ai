"""Fundamental observation objects (PROJECT_DESIGN sections 31-32).

Every observation carries ``available_at`` so backtests never use a result before it was
public. Missing metrics are None with a status, never zero.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from vcp_scanner.domain.enums import FundamentalDataStatus


@dataclass(frozen=True, slots=True)
class FundamentalSnapshot:
    instrument_id: str
    period_end: date
    publication_date: date
    available_at: datetime
    source: str
    retrieved_at: datetime
    statement_basis: str | None = None  # standalone | consolidated (DATABASE_SCHEMA section 36)
    revision_number: int = 0
    metrics: dict[str, float | None] = field(default_factory=dict)
    status: FundamentalDataStatus = FundamentalDataStatus.AVAILABLE
