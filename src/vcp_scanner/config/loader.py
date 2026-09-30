"""Configuration loader and hash calculator (PROJECT_DESIGN sections 45-47, 60)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from vcp_scanner.config.models import (
    ClassificationConfig,
    DataConfig,
    LoggingConfig,
    MonitoringConfig,
    RSConfig,
    ScannerConfig,
    ScoringConfig,
    StageConfig,
    StrategyConfig,
    TrendTemplateConfig,
    UniverseConfig,
    VCPThresholdsConfig,
)
from vcp_scanner.domain.errors import ConfigError


def compute_config_hash(config_data: Any) -> str:
    """Compute a deterministic SHA-256 hash of a configuration object or dict.

    Sorts all keys and produces a canonical UTF-8 JSON representation.
    """
    if hasattr(config_data, "model_dump"):
        data = config_data.model_dump()
    elif isinstance(config_data, dict):
        data = config_data
    else:
        raise ConfigError(f"Unsupported config type for hashing: {type(config_data)}")

    canonical_json = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def _read_yaml(file_path: Path) -> dict[str, Any]:
    """Read a YAML file safely into a Python dictionary."""
    if not file_path.exists():
        return {}
    try:
        with file_path.open("r", encoding="utf-8") as f:
            content = yaml.safe_load(f)
            return content if isinstance(content, dict) else {}
    except Exception as exc:
        raise ConfigError(f"Failed to read YAML file {file_path}: {exc}") from exc


def load_logging_config(config_dir: str | Path = "config") -> LoggingConfig:
    """Load only ``logging.yaml`` from ``config_dir``.

    Independent of the rest of the configuration so the CLI can set up logging before (and
    even when) the full scanner config fails to load. Missing file or directory yields the
    defaults; an unreadable or invalid file raises ``ConfigError``.
    """
    raw = _read_yaml(Path(config_dir) / "logging.yaml")
    try:
        return LoggingConfig(**raw) if raw else LoggingConfig()
    except ValidationError as err:
        raise ConfigError(f"Invalid logging configuration:\n{err}") from err


def load_scanner_config(config_dir: str | Path = "config") -> ScannerConfig:
    """Load configuration from a directory containing YAML config files.

    Expected files (optional if defaults apply):
      - strategy.yaml (contains trend_template, rs, stage, vcp, classification, or scoring)
      - scoring.yaml (contains scoring specification weights and bounds)
      - universe.yaml (contains universe filters)
      - data.yaml (contains persistence paths and providers)
      - monitoring.yaml (contains event & channel settings)
      - logging.yaml (contains log level and formatting)
    """
    path = Path(config_dir)
    if not path.exists():
        raise ConfigError(f"Configuration directory does not exist: {path}")

    strategy_raw = _read_yaml(path / "strategy.yaml")
    scoring_raw = _read_yaml(path / "scoring.yaml")
    universe_raw = _read_yaml(path / "universe.yaml")
    data_raw = _read_yaml(path / "data.yaml")
    monitoring_raw = _read_yaml(path / "monitoring.yaml")
    logging_raw = _read_yaml(path / "logging.yaml")

    try:
        # Scoring can be in scoring.yaml or under strategy.yaml
        scoring_data = scoring_raw if scoring_raw else strategy_raw.get("scoring", {})
        scoring_config = ScoringConfig(**scoring_data) if scoring_data else ScoringConfig()

        trend_template_data = strategy_raw.get("trend_template", {})
        trend_template = (
            TrendTemplateConfig(**trend_template_data)
            if trend_template_data
            else TrendTemplateConfig()
        )

        rs_data = strategy_raw.get("rs", {})
        rs = RSConfig(**rs_data) if rs_data else RSConfig()

        stage_data = strategy_raw.get("stage", {})
        stage = StageConfig(**stage_data) if stage_data else StageConfig()

        vcp_data = strategy_raw.get("vcp", {})
        vcp = VCPThresholdsConfig(**vcp_data) if vcp_data else VCPThresholdsConfig()

        classification_data = strategy_raw.get("classification", {})
        classification = (
            ClassificationConfig(**classification_data)
            if classification_data
            else ClassificationConfig()
        )

        strategy = StrategyConfig(
            trend_template=trend_template,
            rs=rs,
            stage=stage,
            vcp=vcp,
            classification=classification,
            scoring=scoring_config,
        )

        universe = UniverseConfig(**universe_raw) if universe_raw else UniverseConfig()
        data = DataConfig(**data_raw) if data_raw else DataConfig()
        monitoring = MonitoringConfig(**monitoring_raw) if monitoring_raw else MonitoringConfig()
        logging_cfg = LoggingConfig(**logging_raw) if logging_raw else LoggingConfig()

        return ScannerConfig(
            strategy=strategy,
            universe=universe,
            data=data,
            monitoring=monitoring,
            logging=logging_cfg,
        )

    except ValidationError as err:
        raise ConfigError(f"Configuration validation failed:\n{err}") from err
