"""``config/live.yaml``: which provider feeds the live display, and how often.

Deliberately not part of ``load_scanner_config``, so it can never change a scan or strategy
config hash (a test pins them).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from vcp_scanner.domain.errors import ConfigError
from vcp_scanner.live.models import IndexSpec

PROVIDERS = ("upstox", "kite")
DEFAULT_INDICES = (
    IndexSpec("NIFTY50", "NIFTY 50", "NSE_INDEX|Nifty 50", "NSE:NIFTY 50"),
    IndexSpec("SENSEX", "SENSEX", "BSE_INDEX|SENSEX", "BSE:SENSEX"),
    IndexSpec("NIFTY500", "NIFTY 500", "NSE_INDEX|Nifty 500", "NSE:NIFTY 500"),
)


@dataclass(frozen=True, slots=True)
class LiveConfig:
    enabled: bool = True
    provider: str = "upstox"
    poll_interval_seconds: float = 15.0
    stale_after_seconds: float = 120.0
    idle_after_seconds: float = 120.0  # stop polling when no page has asked for this long
    auth_retry_seconds: float = 60.0
    max_backoff_seconds: float = 300.0
    kite_batch: int = 200
    indices: tuple[IndexSpec, ...] = field(default=DEFAULT_INDICES)


def load_live_config(config_dir: str | Path = "config") -> LiveConfig:
    path = Path(config_dir) / "live.yaml"
    if not path.exists():
        return LiveConfig()
    try:
        raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: not valid YAML: {exc}") from exc
    known = set(LiveConfig.__dataclass_fields__)
    unknown = sorted(set(raw) - known)
    if unknown:
        raise ConfigError(f"{path}: unknown keys {unknown}")
    if "indices" in raw:
        try:
            raw["indices"] = tuple(
                IndexSpec(str(i["id"]), str(i["label"]), str(i["upstox_key"]), str(i["kite_key"]))
                for i in raw["indices"]
            )
        except (KeyError, TypeError) as exc:
            raise ConfigError(f"{path}: each index needs id, label, upstox_key, kite_key") from exc
    cfg = LiveConfig(**raw)
    if cfg.provider not in PROVIDERS:
        raise ConfigError(f"{path}: provider must be one of {PROVIDERS}, not {cfg.provider!r}")
    if cfg.poll_interval_seconds < 5:
        raise ConfigError(f"{path}: poll_interval_seconds must be at least 5")
    if cfg.kite_batch < 1:
        raise ConfigError(f"{path}: kite_batch must be at least 1")
    return cfg
