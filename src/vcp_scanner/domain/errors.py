"""Exception hierarchy. Each error carries a standard category (DATA_SPECIFICATION section 96)."""

from __future__ import annotations

from vcp_scanner.domain.enums import ErrorCategory


class VCPScannerError(Exception):
    """Base class for all scanner errors."""

    category: ErrorCategory = ErrorCategory.PROVIDER_ERROR

    def __init__(self, message: str, *, category: ErrorCategory | None = None) -> None:
        super().__init__(message)
        if category is not None:
            self.category = category


class ConfigError(VCPScannerError):
    """Invalid configuration. A scan must not start (PROJECT_DESIGN section 47)."""

    category = ErrorCategory.CONFIG_ERROR


class ProviderError(VCPScannerError):
    category = ErrorCategory.PROVIDER_ERROR


class ProviderAuthError(ProviderError):
    """The provider rejected the credentials (HTTP 401/403): every further request will fail.

    For the secondary corporate-action source this downgrades the run to primary-only
    (owner decision, 2026-10-01) instead of aborting it.
    """


class ProviderRateLimited(ProviderError):
    """The provider answered HTTP 429 (too many requests); ``retry_after`` is the wait in
    seconds when the provider says so. The live feed reports it instead of retrying blindly."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class DataValidationError(VCPScannerError):
    category = ErrorCategory.DATA_VALIDATION_ERROR


class StorageError(VCPScannerError):
    category = ErrorCategory.STORAGE_ERROR
