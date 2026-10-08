"""Builds the real provider feed and the NSE holiday loader (everything lazy: no network and no
credential read until the first poll)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path

from vcp_scanner.domain.errors import ProviderAuthError, ProviderError
from vcp_scanner.live.config import LiveConfig
from vcp_scanner.live.models import LiveFeed


def make_feed_factory(cfg: LiveConfig, env_file: str = ".env") -> Callable[[], LiveFeed]:
    """A function that reads the credentials from ``env_file`` each time it is called, so a
    refreshed token is picked up without a restart. A missing credential raises
    ``ProviderAuthError`` naming the variable, never its value."""

    def build() -> LiveFeed:
        from dotenv import load_dotenv

        from vcp_scanner.cli_pipeline import env_secret

        load_dotenv(env_file, override=True)
        if cfg.provider == "upstox":
            from vcp_scanner.data.providers.upstox import UpstoxProvider
            from vcp_scanner.live.upstox_feed import UpstoxFeed

            token = env_secret("UPSTOX_ACCESS_TOKEN")
            if token is None:
                raise ProviderAuthError("UPSTOX_ACCESS_TOKEN is not set")
            return UpstoxFeed(UpstoxProvider(env_secret("UPSTOX_API_KEY") or "", token))
        from vcp_scanner.live.kite_feed import KiteFeed

        key, token = env_secret("KITE_API_KEY"), env_secret("KITE_ACCESS_TOKEN")
        if key is None or token is None:
            raise ProviderAuthError("KITE_API_KEY or KITE_ACCESS_TOKEN is not set")
        try:
            from vcp_scanner.data.providers.kite import KiteProvider

            return KiteFeed(KiteProvider(key, token), cfg.kite_batch)
        except ImportError as exc:
            raise ProviderError("the kiteconnect package is not installed") from exc

    return build


def nse_holiday_loader(data_dir: Path) -> Callable[[], set[date]]:
    """NSE's trading-holiday list through the existing NSE provider (one small request;
    nothing is cached or written)."""

    def load() -> set[date]:
        from vcp_scanner.data.providers.nse_bhavcopy import NseBhavcopyProvider

        return NseBhavcopyProvider(data_dir / "raw" / "bhavcopy").get_trading_holidays()

    return load
