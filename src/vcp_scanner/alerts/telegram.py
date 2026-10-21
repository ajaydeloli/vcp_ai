"""Telegram alerts (owner decision 2026-10-21): config, the channel and the sent-alert log.

Secrets live only in the environment (``.env``): ``TELEGRAM_BOT_TOKEN`` and
``TELEGRAM_CHAT_ID``. They are never logged; a failed send records the HTTP status, not the
request. Every alert, sent or not, is recorded in ``data/alerts/alerts.duckdb`` so the same
alert is not sent twice in a day and the owner can see what was sent (``vcp alerts log``).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Any

import duckdb
import yaml
from pydantic import Field

from vcp_scanner.config.models import StrictBaseModel

API = "https://api.telegram.org"
MAX_LEN = 4000  # Telegram's limit is 4096 characters


class EveningConfig(StrictBaseModel):
    enabled: bool = True
    near_pivot_pct: Annotated[float, Field(ge=0, le=20)] = 2.0
    max_setups: Annotated[int, Field(ge=0, le=50)] = 10
    strategies: list[str] = ["vcp", "flat_base", "three_weeks_tight", "cup_handle",
                             "double_bottom"]  # fmt: skip


class IntradayConfig(StrictBaseModel):
    enabled: bool = True
    poll_seconds: Annotated[int, Field(ge=20, le=900)] = 60
    approach_pct: Annotated[float, Field(ge=0, le=10)] = 1.0
    near_pivot_pct: Annotated[float, Field(ge=0, le=20)] = 3.0
    chase_warn_pct: Annotated[float, Field(ge=0, le=50)] = 5.0
    include_ai_watchlist: bool = True
    symbols: list[str] = []


class AlertsConfig(StrictBaseModel):
    enabled: bool = True
    evening: EveningConfig = EveningConfig()
    intraday: IntradayConfig = IntradayConfig()


def load_alerts_config(config_dir: str | Path) -> AlertsConfig:
    path = Path(config_dir) / "alerts.yaml"
    if not path.exists():
        return AlertsConfig(enabled=False)
    return AlertsConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


class TelegramChannel:
    """Sends plain-text messages to one chat. ``post`` is injectable for tests."""

    def __init__(self, token: str, chat_id: str,
                 post: Callable[[str, dict[str, Any]], Any] | None = None) -> None:  # fmt: skip
        self._token, self._chat = token, chat_id
        self._post = post or self._httpx_post

    @staticmethod
    def _httpx_post(url: str, data: dict[str, Any]) -> Any:
        import httpx

        return httpx.post(url, json=data, timeout=15)

    def send(self, text: str) -> tuple[bool, str | None]:
        """(sent, error). Retries once; never raises, never includes the token in the error."""
        body = {"chat_id": self._chat, "text": text[:MAX_LEN], "disable_web_page_preview": True}
        url = f"{API}/bot{self._token}/sendMessage"
        error = None
        for attempt in range(2):
            try:
                r = self._post(url, body)
                if getattr(r, "status_code", 0) == 200:
                    return True, None
                error = f"Telegram HTTP {getattr(r, 'status_code', '?')}"
                if getattr(r, "status_code", 0) in (400, 401, 403, 404):
                    break  # a wrong token or chat: retrying does not help
            except Exception as exc:  # network
                error = f"Telegram not reachable ({type(exc).__name__})"
            if attempt == 0:
                time.sleep(2)
        return False, error


def channel_from_env() -> TelegramChannel | None:
    from vcp_scanner.cli_pipeline import env_secret

    token, chat = env_secret("TELEGRAM_BOT_TOKEN"), env_secret("TELEGRAM_CHAT_ID")
    return TelegramChannel(token, chat) if token and chat else None


_DDL = """CREATE TABLE IF NOT EXISTS alerts_sent (
    sent_at TIMESTAMPTZ NOT NULL,
    day     DATE NOT NULL,      -- the IST day the alert belongs to
    kind    VARCHAR NOT NULL,   -- EVENING | APPROACHING | CROSSED | TEST
    symbol  VARCHAR,
    text    VARCHAR NOT NULL,
    ok      BOOLEAN NOT NULL,
    error   VARCHAR
)"""


class AlertLog:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def _conn(self, read_only: bool = False) -> duckdb.DuckDBPyConnection:
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            con = duckdb.connect(str(self.path))
            con.execute(_DDL)
            con.close()
        return duckdb.connect(str(self.path), read_only=read_only)

    def sent_today(self, day: date, kind: str, symbol: str | None) -> bool:
        con = self._conn(read_only=True)
        try:
            row = con.execute(
                "SELECT count(*) FROM alerts_sent WHERE day = ? AND kind = ? AND ok"
                " AND symbol IS NOT DISTINCT FROM ?",
                [day, kind, symbol],
            ).fetchone()
        finally:
            con.close()
        return bool(row and row[0])

    def record(self, day: date, kind: str, symbol: str | None, text: str, ok: bool,
               error: str | None) -> None:  # fmt: skip
        con = self._conn()
        try:
            con.execute("INSERT INTO alerts_sent VALUES (?, ?, ?, ?, ?, ?, ?)",
                        [datetime.now(UTC), day, kind, symbol, text, ok, error])  # fmt: skip
        finally:
            con.close()

    def recent(self, limit: int = 30) -> list[tuple[Any, ...]]:
        con = self._conn(read_only=True)
        try:
            return con.execute(
                "SELECT sent_at, kind, symbol, ok, error, text FROM alerts_sent"
                " ORDER BY sent_at DESC LIMIT ?", [limit]).fetchall()  # fmt: skip
        finally:
            con.close()


def send_once(channel: TelegramChannel | None, log: AlertLog, day: date, kind: str,
              symbol: str | None, text: str) -> bool:  # fmt: skip
    """Send unless the same alert went out today; record either way. False: not sent."""
    if log.sent_today(day, kind, symbol):
        return False
    if channel is None:
        log.record(day, kind, symbol, text, False, "Telegram not configured (.env)")
        return False
    ok, error = channel.send(text)
    log.record(day, kind, symbol, text, ok, error)
    return ok
