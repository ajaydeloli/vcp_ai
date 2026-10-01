"""Shared test setup."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_upstox_pacing_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    """Upstox requests are paced ~1.9 s apart in production; unit tests use mocked sessions,
    so the pause is skipped (tests of the pacing itself inject their own ``sleep``)."""
    monkeypatch.setattr("vcp_scanner.data.providers.upstox_ca._default_sleep", lambda seconds: None)
