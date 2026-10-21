"""The dashboard's Alerts page (2026-10-21): whether Telegram is set up (yes/no only, never the
token), the settings of ``config/alerts.yaml``, the intraday watcher's last check and what it
watches, and the alerts sent recently. Read-only: nothing here sends or changes anything.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from vcp_scanner.api import models as m

#: The watcher checks every 20-900 s; after this long without a check it counts as stopped.
STALE_AFTER = timedelta(minutes=20)


def _configured(env_file: str) -> bool:
    """Both Telegram settings present (in the environment or the env file), not placeholders."""
    import os

    from dotenv import dotenv_values

    values = {**dotenv_values(env_file), **os.environ} if Path(env_file).exists() else dict(
        os.environ)  # fmt: skip

    def real(name: str) -> bool:
        v = (values.get(name) or "").strip()
        return bool(v) and not (v.startswith("your_") and v.endswith("_here"))

    return real("TELEGRAM_BOT_TOKEN") and real("TELEGRAM_CHAT_ID")


def alerts_state(config_dir: str | Path, data_dir: Path, env_file: str,
                 now: datetime) -> m.AlertsResponse:  # fmt: skip
    from vcp_scanner.alerts.telegram import AlertLog, load_alerts_config

    cfg = load_alerts_config(config_dir)
    watcher = m.AlertWatcher(running=False, last_check=None, market=None, error=None)
    watching: list[m.AlertWatched] = []
    state = data_dir / "alerts" / "watch_state.json"
    if state.exists():
        try:
            body = json.loads(state.read_text(encoding="utf-8"))
            last = datetime.fromisoformat(body["updated_at"])
            watcher = m.AlertWatcher(
                running=now - last <= STALE_AFTER,
                last_check=last,
                market=body.get("market"),
                error=body.get("error"),
            )
            watching = [m.AlertWatched(**w) for w in body.get("watching", [])]
        except (ValueError, KeyError, TypeError):
            pass
    log_path = data_dir / "alerts" / "alerts.duckdb"
    sent = []
    if log_path.exists():
        try:
            sent = [
                m.AlertSent(
                    sent_at=r[0],
                    kind=str(r[1]),
                    symbol=r[2],
                    ok=bool(r[3]),
                    error=r[4],
                    text=str(r[5]),
                )  # fmt: skip
                for r in AlertLog(log_path).recent(50)
            ]
        except Exception:  # noqa: BLE001 - the log may be busy being written
            sent = []
    return m.AlertsResponse(
        as_of=None, data_time=now, enabled=cfg.enabled, telegram_configured=_configured(env_file),
        evening_enabled=cfg.enabled and cfg.evening.enabled,
        intraday_enabled=cfg.enabled and cfg.intraday.enabled,
        settings=m.AlertSettings(
            evening_near_pivot_pct=cfg.evening.near_pivot_pct,
            intraday_near_pivot_pct=cfg.intraday.near_pivot_pct,
            approach_pct=cfg.intraday.approach_pct, chase_warn_pct=cfg.intraday.chase_warn_pct,
            poll_seconds=cfg.intraday.poll_seconds,
            include_ai_watchlist=cfg.intraday.include_ai_watchlist,
            own_symbols=list(cfg.intraday.symbols),
        ),
        watcher=watcher,
        watching=sorted(watching, key=lambda w: abs(w.gap_pct) if w.gap_pct is not None else 99),
        sent=sent,
    )  # fmt: skip
