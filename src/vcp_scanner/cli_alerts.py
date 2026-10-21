"""``vcp alerts ...``: Telegram alerts (owner decision 2026-10-21).

* ``test``     send a test message (checks TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID in .env);
* ``chat-id``  show the chat ids that messaged the bot (to fill TELEGRAM_CHAT_ID);
* ``evening``  send the evening summary now (the daily run sends it by itself);
* ``watch``    the intraday watcher: during market hours, pivot alerts from the live feed of
               the running dashboard API (scripts/dashboard.sh starts it);
* ``log``      the alerts sent recently.
"""

from __future__ import annotations

import argparse
import contextlib
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from vcp_scanner.cli_pipeline import _err

DEFAULT_LOG = "data/alerts/alerts.duckdb"


def add_alerts_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = subparsers.add_parser("alerts", help="Telegram alerts: evening summary and pivot alerts")
    sub = p.add_subparsers(dest="alerts_command")
    for name, help_ in (("test", "Send a test message"),
                        ("chat-id", "Show the chat ids that messaged the bot"),
                        ("evening", "Send the evening summary now"),
                        ("watch", "Intraday pivot alerts during market hours"),
                        ("log", "Alerts sent recently")):  # fmt: skip
        s = sub.add_parser(name, help=help_)
        s.add_argument("--config-dir", default="config")
        s.add_argument("--data-dir", default="data")
        s.add_argument("--env-file", default=".env")
        if name == "watch":
            s.add_argument("--api", default="http://127.0.0.1:8000")
            s.add_argument("--once", action="store_true", help="One check, then stop (testing)")
        if name == "log":
            s.add_argument("--limit", type=int, default=30)


def _today() -> Any:
    from vcp_scanner.data.providers._time import IST

    return datetime.now(IST).date()


def _now() -> datetime:
    from vcp_scanner.data.providers._time import IST

    return datetime.now(IST)


def send_evening(config_dir: str, data_dir: str) -> str:
    """The evening summary through the in-process API; returns a one-line result. Never raises
    (the daily run calls it last)."""
    try:
        from vcp_scanner.agent.server import Api
        from vcp_scanner.alerts.messages import evening_message
        from vcp_scanner.alerts.telegram import (
            AlertLog,
            channel_from_env,
            load_alerts_config,
            send_once,
        )

        cfg = load_alerts_config(config_dir)
        if not (cfg.enabled and cfg.evening.enabled):
            return "evening alert off (config/alerts.yaml)"
        api = Api(config_dir, data_dir)
        text = evening_message(api.get, cfg.evening)
        channel = channel_from_env()
        sent = send_once(channel, AlertLog(Path(data_dir) / "alerts" / "alerts.duckdb"),
                         _today(), "EVENING", None, text)  # fmt: skip
        if channel is None:
            return "evening alert not sent: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not in .env"
        return (
            "evening alert sent"
            if sent
            else "evening alert not sent (already sent today, or failed: vcp alerts log)"
        )
    except Exception as exc:  # noqa: BLE001
        return f"evening alert failed: {type(exc).__name__}: {exc}"


def write_watch_state(path: Path, market: str | None, watched: list[Any],
                      quotes: dict[str, float], error: str | None) -> None:  # fmt: skip
    """What the watcher sees, for the dashboard's Alerts page (replaced atomically)."""
    import json

    rows = []
    for w in watched:
        last = quotes.get(w.symbol)
        gap = None if last is None else round((last / w.pivot - 1) * 100, 2)
        rows.append({"symbol": w.symbol, "pivot": w.pivot, "strategy": w.strategy, "why": w.why,
                     "last_price": last, "gap_pct": gap})  # fmt: skip
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"updated_at": _now().isoformat(), "market": market,
                               "watching": rows, "error": error}), encoding="utf-8")  # fmt: skip
    tmp.replace(path)


def _market_line(get: Any) -> str | None:
    """ "Market health: Adverse 22.7, exposure none" (display only), or None."""
    try:
        score = get("market/health").get("score") or {}
    except Exception:  # noqa: BLE001 - the alert goes out without it
        return None
    if not score.get("label"):
        return None
    raw, exposure = score.get("raw", ""), score.get("exposure", "-")
    return f"Market health: {score['label']} {raw}, exposure {exposure}"


def _watch(args: argparse.Namespace) -> int:
    import httpx

    from vcp_scanner.agent.proposals import Proposals
    from vcp_scanner.agent.store import AgentStore
    from vcp_scanner.alerts.messages import pivot_alerts, watch_list
    from vcp_scanner.alerts.telegram import (
        AlertLog,
        channel_from_env,
        load_alerts_config,
        send_once,
    )

    cfg = load_alerts_config(args.config_dir)
    if not (cfg.enabled and cfg.intraday.enabled):
        print("Intraday alerts are off (config/alerts.yaml); nothing to watch.")
        return 0
    state_path = Path(args.data_dir) / "alerts" / "watch_state.json"
    channel = channel_from_env()
    # Not set up yet: wait (re-reading .env each minute) instead of stopping, so adding the
    # token later needs no dashboard restart (2026-10-21).
    while channel is None:
        msg = "Telegram not set up: add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to .env"
        print(f"{_now():%H:%M} {msg}; checking again in a minute")
        with contextlib.suppress(OSError):
            write_watch_state(state_path, None, [], {}, msg)
        if args.once:
            return 1
        time.sleep(60)
        from dotenv import load_dotenv

        load_dotenv(args.env_file)
        channel = channel_from_env()
    log = AlertLog(Path(args.data_dir) / "alerts" / "alerts.duckdb")
    agent_db = Path(args.data_dir) / "agent" / "agent.duckdb"

    def get(path: str, **params: Any) -> Any:
        clean = {k: v for k, v in params.items() if v is not None}
        r = httpx.get(f"{args.api}/api/v1/{path}", params=clean, timeout=20)
        r.raise_for_status()
        return r.json()

    watched: list[Any] = []
    quotes: dict[str, float] = {}
    market_line: str | None = None
    state = state_path
    built_at = 0.0
    print(f"Watching for pivot alerts (every {cfg.intraday.poll_seconds} s in market hours)")
    while True:
        market, error = None, None
        try:
            market = (get("live/status").get("feed") or {}).get("market")
            if market == "open":
                if not watched or time.monotonic() - built_at > 3600:
                    ai = ([s for s, *_ in Proposals(AgentStore(agent_db), Path.cwd()).watchlist()]
                          if agent_db.exists() else [])  # fmt: skip
                    watched = watch_list(get, cfg.intraday, ai)
                    built_at = time.monotonic()
                    print(f"{_now():%H:%M} watching {len(watched)} stocks")
                    market_line = _market_line(get)
                if watched:
                    body = get("live/quotes", symbols=",".join(w.symbol for w in watched))
                    live = {
                        q["symbol"]: q
                        for q in body.get("quotes", [])
                        if q.get("available") and q.get("last_price") is not None
                    }
                    quotes = {s: q["last_price"] for s, q in live.items()}
                    alerts = pivot_alerts(watched, quotes, cfg.intraday, live, market_line, _now())
                    for kind, sym, text in alerts:
                        if send_once(channel, log, _today(), kind, sym, text):
                            print(f"{_now():%H:%M} sent {kind} {sym}")
            else:
                watched, quotes = [], {}
        except Exception as exc:  # noqa: BLE001 - keep watching; the API may be restarting
            error = f"{type(exc).__name__}: {exc}"
            print(f"{_now():%H:%M} watch: {error}")
        with contextlib.suppress(OSError):
            write_watch_state(state, market, watched, quotes, error)
        if args.once:
            return 0
        time.sleep(cfg.intraday.poll_seconds if watched else 60)


def run_alerts(args: argparse.Namespace) -> int:
    from dotenv import load_dotenv

    from vcp_scanner.alerts.telegram import AlertLog, channel_from_env

    cmd = getattr(args, "alerts_command", None)
    if cmd is None:
        _err("Usage: vcp alerts {test,chat-id,evening,watch,log} ...")
        return 1
    load_dotenv(args.env_file)
    if cmd == "log":
        log = AlertLog(Path(args.data_dir) / "alerts" / "alerts.duckdb")
        for at, kind, sym, ok, error, text in log.recent(args.limit):
            mark = "sent" if ok else f"NOT SENT ({error})"
            print(f"{at:%Y-%m-%d %H:%M} {kind} {sym or ''} {mark}: {text.splitlines()[0][:100]}")
        return 0
    if cmd == "evening":
        print(send_evening(args.config_dir, args.data_dir))
        return 0
    if cmd == "watch":
        return _watch(args)
    from vcp_scanner.cli_pipeline import env_secret

    token = env_secret("TELEGRAM_BOT_TOKEN")
    if not token:
        _err(f"TELEGRAM_BOT_TOKEN is not set in {args.env_file}: create a bot with @BotFather")
        return 1
    if cmd == "chat-id":
        import httpx

        r = httpx.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=15)
        if r.status_code != 200:
            _err(f"Telegram HTTP {r.status_code}: check the token")
            return 1
        chats = {(u.get("message") or {}).get("chat", {}).get("id"):
                 (u.get("message") or {}).get("chat", {}).get("first_name")
                 for u in r.json().get("result", [])}  # fmt: skip
        chats.pop(None, None)
        if not chats:
            print("No messages yet: open your bot in Telegram, send it any message, run again.")
        for cid, name in chats.items():
            print(f"TELEGRAM_CHAT_ID={cid}   ({name})")
        return 0
    if cmd == "test":
        channel = channel_from_env()
        if channel is None:
            _err(f"TELEGRAM_CHAT_ID is not set in {args.env_file}: run `vcp alerts chat-id`")
            return 1
        ok, error = channel.send("VCP scanner: test alert. Alerts are working.")
        AlertLog(Path(args.data_dir) / "alerts" / "alerts.duckdb").record(
            _today(), "TEST", None, "test", ok, error)  # fmt: skip
        print("Test alert sent: check Telegram." if ok else f"Not sent: {error}")
        return 0 if ok else 1
    _err(f"Unknown alerts command: {cmd}")
    return 1
