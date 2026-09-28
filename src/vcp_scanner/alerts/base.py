"""Alert dispatch interfaces (PROJECT_DESIGN section 54)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from vcp_scanner.domain.enums import VCPClassification


@dataclass(frozen=True, slots=True)
class AlertPayload:
    """Structured alert notification payload."""

    event: str
    symbol: str
    timestamp: datetime
    pivot: float | None = None
    price: float | None = None
    volume_ratio: float | None = None
    classification: VCPClassification | None = None
    setup_score: float | None = None
    extra: dict[str, Any] | None = None


@runtime_checkable
class AlertChannel(Protocol):
    """Interface for notification dispatch channels (console, email, webhook)."""

    def send(self, alert: AlertPayload) -> bool:
        """Send an alert. Returns True if dispatch succeeded."""
        ...
