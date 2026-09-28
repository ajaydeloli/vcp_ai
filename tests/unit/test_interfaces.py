"""Unit tests for Provider and Repository interfaces (PROJECT_DESIGN sections 8.1, 14A, 48).

Verifies structural typing compliance with defined Protocols.
"""

from datetime import UTC, date, datetime
from typing import Any

from vcp_scanner.alerts.base import AlertChannel, AlertPayload
from vcp_scanner.data.providers.base import (
    CorporateActionProvider,
    FundamentalProvider,
    MarketDataProvider,
    SecurityMasterProvider,
    SurveillanceProvider,
)
from vcp_scanner.data.repositories.base import (
    FundamentalRepository,
    MarketDataRepository,
    ScanRepository,
    UniverseRepository,
)
from vcp_scanner.domain.fundamentals import FundamentalSnapshot
from vcp_scanner.domain.market import (
    Candle,
    CorporateActionRecord,
    Instrument,
    ProviderCapabilities,
    ProviderHealth,
    Quote,
    SecurityRecord,
    SurveillanceRecord,
)
from vcp_scanner.patterns.base import (
    ConfirmationResult,
    PatternConfirmer,
    PatternContext,
    PatternDetector,
)


class DummyMarketDataProvider:
    def get_instruments(self) -> list[Instrument]:
        return []

    def get_historical_daily(self, instrument: Instrument, start: date, end: date) -> list[Candle]:
        return []

    def get_historical_intraday(
        self, instrument: Instrument, interval: str, start: datetime, end: datetime
    ) -> list[Candle]:
        return []

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(provider="dummy", healthy=True, checked_at=datetime.now(UTC))

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            daily_history_max_request_days=365,
            intraday_history_max_request_days={},
            historical_requests_per_second=3.0,
            supports_bulk_historical=False,
            supports_websocket=False,
            supports_quotes=True,
            daily_history=True,
            intraday_history=False,
            corporate_actions=False,
            adjusted_prices=False,
            instrument_master=True,
            delisted_history=False,
        )

    def get_quotes(self, instruments: list[Instrument]) -> list[Quote]:
        return []


class DummySecurityMasterProvider:
    def get_security_history(self, start: date, end: date) -> list[SecurityRecord]:
        return []


class DummyCorporateActionProvider:
    def get_actions(self, start: date, end: date) -> list[CorporateActionRecord]:
        return []


class DummySurveillanceProvider:
    def get_flags(self, start: date, end: date) -> list[SurveillanceRecord]:
        return []


class DummyFundamentalProvider:
    def get_snapshot(self, instrument: Instrument, as_of: date) -> FundamentalSnapshot:
        return FundamentalSnapshot(
            instrument_id=instrument.instrument_id,
            period_end=as_of,
            publication_date=as_of,
            available_at=datetime.now(UTC),
            source="dummy",
            retrieved_at=datetime.now(UTC),
        )


class DummyMarketDataRepository:
    def load_daily(self, instrument_id: str, start: date, end: date) -> list[Candle]:
        return []

    def save_daily(self, candles: list[Candle]) -> int:
        return len(candles)

    def latest_timestamp(self, instrument_id: str) -> datetime | None:
        return None


class DummyUniverseRepository:
    def save_snapshot(
        self,
        snapshot_id: str,
        as_of_date: date,
        instrument_ids: list[str],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        pass

    def load_snapshot(self, as_of_date: date) -> list[str]:
        return []


class DummyScanRepository:
    def save_scan(self, scan_run: dict[str, Any]) -> None:
        pass

    def save_results(self, scan_id: str, results: list[dict[str, Any]]) -> None:
        pass

    def load_scan(self, scan_id: str) -> dict[str, Any] | None:
        return None


class DummyFundamentalRepository:
    def save_snapshot(self, snapshot: FundamentalSnapshot) -> None:
        pass

    def load_snapshot(self, instrument_id: str, as_of: date) -> FundamentalSnapshot | None:
        return None


class DummyPatternDetector:
    def detect(self, context: PatternContext) -> None:
        return None


class DummyPatternConfirmer:
    def confirm(self, context: PatternContext) -> ConfirmationResult:
        return ConfirmationResult(confirmed=True, confidence=0.85)


class DummyAlertChannel:
    def send(self, alert: AlertPayload) -> bool:
        return True


def test_provider_protocols_runtime_checkable() -> None:
    assert isinstance(DummyMarketDataProvider(), MarketDataProvider)
    assert isinstance(DummySecurityMasterProvider(), SecurityMasterProvider)
    assert isinstance(DummyCorporateActionProvider(), CorporateActionProvider)
    assert isinstance(DummySurveillanceProvider(), SurveillanceProvider)
    assert isinstance(DummyFundamentalProvider(), FundamentalProvider)


def test_repository_protocols_runtime_checkable() -> None:
    assert isinstance(DummyMarketDataRepository(), MarketDataRepository)
    assert isinstance(DummyUniverseRepository(), UniverseRepository)
    assert isinstance(DummyScanRepository(), ScanRepository)
    assert isinstance(DummyFundamentalRepository(), FundamentalRepository)


def test_pattern_and_alert_protocols_runtime_checkable() -> None:
    assert isinstance(DummyPatternDetector(), PatternDetector)
    assert isinstance(DummyPatternConfirmer(), PatternConfirmer)
    assert isinstance(DummyAlertChannel(), AlertChannel)
