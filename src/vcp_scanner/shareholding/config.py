"""``config/shareholding.yaml`` (F8). Outside ``load_scanner_config`` on purpose: it can never
change a scan or strategy config hash."""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from vcp_scanner.domain.errors import ConfigError

FILE_NAME = "shareholding.yaml"


@dataclass(frozen=True)
class ShareholdingConfig:
    scope_from: date = date(2023, 10, 1)
    history_from: date = date(2022, 6, 30)
    site_interval_seconds: float = 2.0
    archive_interval_seconds: float = 1.0
    daily_update: bool = True
    update_days: int = 10
    new_stock_limit: int = 100


def load_shareholding_config(config_dir: str | Path = "config") -> ShareholdingConfig:
    path = Path(config_dir) / FILE_NAME
    if not path.exists():
        return ShareholdingConfig()
    try:
        raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: not valid YAML: {exc}") from exc
    known = {f.name for f in fields(ShareholdingConfig)}
    unknown = sorted(set(raw) - known)
    if unknown:
        raise ConfigError(f"{path}: unknown keys {unknown}")
    for key in ("scope_from", "history_from"):
        if key in raw and not isinstance(raw[key], date):
            try:
                raw[key] = date.fromisoformat(str(raw[key]))
            except ValueError as exc:
                raise ConfigError(f"{path}: {key} must be a date (YYYY-MM-DD)") from exc
    cfg = ShareholdingConfig(**raw)
    if cfg.site_interval_seconds < 1.0 or cfg.archive_interval_seconds < 0.5:
        raise ConfigError(
            f"{path}: request pauses below 1.0 s (site) / 0.5 s (archive) are not allowed"
        )
    return cfg
