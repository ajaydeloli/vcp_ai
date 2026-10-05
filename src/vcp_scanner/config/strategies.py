"""Strategy config files and strategy config hashes (STRATEGY_SPECIFICATION 3.2, 4.2).

``config/strategies/<strategy_id>.yaml`` holds one strategy's settings. The registry of
strategies (``patterns/registry.py``) passes the ids and algorithm versions it knows, so a file
for an unknown strategy, or with a version the code does not implement, is refused.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path

import yaml
from pydantic import ValidationError

from vcp_scanner.config.loader import scan_config_hash
from vcp_scanner.config.models import ScannerConfig, StrategyFileConfig
from vcp_scanner.domain.errors import ConfigError
from vcp_scanner.domain.strategy import VCP_STRATEGY_ID

STRATEGIES_DIR = "strategies"


def default_vcp_file(algorithm_version: str) -> StrategyFileConfig:
    """VCP's pointer file when ``config/strategies/vcp.yaml`` is absent (test configs, older
    checkouts): thresholds in ``strategy.yaml`` (decision O3), live paper since 2026-10-01."""
    return StrategyFileConfig(
        strategy_id=VCP_STRATEGY_ID, algorithm_version=algorithm_version, stage="paper",
        config_source="legacy",
    )  # fmt: skip


def load_strategy_files(
    config_dir: str | Path, registered: Mapping[str, str]
) -> dict[str, StrategyFileConfig]:
    """Every strategy file under ``<config_dir>/strategies``, checked against ``registered``
    (strategy id -> the code's algorithm version). VCP gets its default pointer when its file is
    missing; any other registered strategy without a file is simply not configured."""
    out: dict[str, StrategyFileConfig] = {}
    folder = Path(config_dir) / STRATEGIES_DIR
    for path in sorted(folder.glob("*.yaml")) if folder.is_dir() else []:
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            cfg = StrategyFileConfig(**raw)
        except (OSError, yaml.YAMLError, TypeError, ValidationError) as err:
            raise ConfigError(f"Invalid strategy file {path}:\n{err}") from err
        if cfg.strategy_id != path.stem:
            raise ConfigError(f"{path}: strategy_id {cfg.strategy_id} must equal the file name")
        if cfg.strategy_id not in registered:
            raise ConfigError(f"{path}: unknown strategy {cfg.strategy_id}; "
                              f"registered: {', '.join(sorted(registered))}")  # fmt: skip
        if cfg.algorithm_version != registered[cfg.strategy_id]:
            raise ConfigError(
                f"{path}: algorithm_version {cfg.algorithm_version} but the code implements "
                f"{registered[cfg.strategy_id]}"
            )
        if (cfg.config_source == "legacy") != (cfg.strategy_id == VCP_STRATEGY_ID):
            raise ConfigError(f"{path}: config_source legacy is for vcp only (decision O3)")
        out[cfg.strategy_id] = cfg
    if VCP_STRATEGY_ID in registered and VCP_STRATEGY_ID not in out:
        out[VCP_STRATEGY_ID] = default_vcp_file(registered[VCP_STRATEGY_ID])
    return out


def strategy_config_hash(config: ScannerConfig, strategy: StrategyFileConfig) -> str:
    """The hash that names every stored result of a strategy (section 4.2, decision O1).

    VCP: ``scan_config_hash`` itself (unchanged, so stored VCP ids stay valid). Others: chained
    on it, with the strategy's own result-relevant settings.
    """
    scan = scan_config_hash(config)
    if strategy.strategy_id == VCP_STRATEGY_ID:
        return scan
    canon = json.dumps(
        {"scan": scan, "strategy_id": strategy.strategy_id,
         "algorithm_version": strategy.algorithm_version, "config": strategy.result_relevant()},
        sort_keys=True, separators=(",", ":"), default=str,
    )  # fmt: skip
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()
