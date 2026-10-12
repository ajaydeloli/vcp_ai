"""Compute the features of every session, score them and store the days not stored yet for the
current settings version (``--rebuild`` replaces them all)."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from vcp_scanner.market_health.features import FeatureDay, compute_features
from vcp_scanner.market_health.scoring import DayScore, score_day
from vcp_scanner.market_health.settings import HealthSettings, load_settings


def _row(fd: FeatureDay, sc: DayScore, version: str, now: datetime) -> tuple[Any, ...]:
    def dump(obj: Any) -> str:
        return json.dumps(obj, default=str, separators=(",", ":"))

    return (
        fd.day, version, sc.raw, sc.raw_label, sc.label, sc.complete, sc.exposure, sc.thrust,
        fd.vqi, fd.nifty500,
        dump([asdict(p) for p in sc.pillars]), dump([asdict(c) for c in sc.caps]),
        dump(sc.missing), dump(asdict(fd.features)), now,
    )  # fmt: skip


def first_scorable(days: list[FeatureDay], s: HealthSettings) -> date | None:
    """The first day with a big enough universe: earlier days have no breadth at all."""
    return next((fd.day for fd in days if fd.features.universe >= s.min_universe), None)


def build(
    conn: Any, settings: HealthSettings, config_dir: str | Path = "config",
    *, rebuild: bool = False, start: date | None = None,
) -> tuple[int, int]:  # fmt: skip
    """Returns (days stored, days scored). Days before the first scorable one are not stored."""
    from vcp_scanner.config.loader import load_scanner_config
    from vcp_scanner.domain.features import FEATURES_CALCULATION_VERSION

    cfg = load_scanner_config(config_dir)
    days = compute_features(conn, settings, cfg.strategy.trend_template, cfg.universe,
                            FEATURES_CALCULATION_VERSION)  # fmt: skip
    first = first_scorable(days, settings)
    if first is None:
        return 0, 0
    lo = max(first, start) if start else first
    version = settings.settings_version
    stored = {r[0] for r in conn.execute(
        "SELECT trade_date FROM market_health_daily WHERE settings_version = ?", [version]
    ).fetchall()}  # fmt: skip
    now = datetime.now(UTC)
    rows = [
        _row(fd, score_day(fd.features, settings), version, now)
        for fd in days
        if fd.day >= lo and (rebuild or fd.day not in stored)
    ]
    conn.execute("BEGIN")
    try:
        if rebuild:
            conn.execute(
                "DELETE FROM market_health_daily WHERE settings_version = ? AND trade_date >= ?",
                [version, lo],
            )
        if rows:
            conn.executemany(
                "INSERT INTO market_health_daily VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
                " ?, ?)", rows,
            )  # fmt: skip
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return len(rows), sum(1 for fd in days if fd.day >= lo)


def run(db: Path, config_dir: str, rebuild: bool, start: date | None) -> int:
    from vcp_scanner.data.storage.duckdb_store import DuckDBStore

    settings = load_settings(Path(config_dir) / "market_health.yaml")
    store = DuckDBStore(db)
    store.migrate()
    stored, scored = build(store.conn, settings, config_dir, rebuild=rebuild, start=start)
    print(f"Market health {settings.settings_version}: {stored} days stored "
          f"({scored} scored).", flush=True)  # fmt: skip
    return 0
