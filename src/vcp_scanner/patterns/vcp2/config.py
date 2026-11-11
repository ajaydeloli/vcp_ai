"""VCP detector v2 settings (claude/vcp_detector_v2_spec_draft.md, 3.1-3.5; owner 2026-11-09/10).

Kept in their own file, ``config/vcp2.yaml``, with their own hash: they are deliberately NOT part
of ``ScannerConfig``/``StrategyConfig``. Adding them there would change ``scan_config_hash`` and
fork every live v1 scan and the frozen paper ledger. v2 is research only; nothing here feeds v1.

Every value is validated when loaded; an invalid value stops the scan with a clear message. A
changed value is a new v2 configuration (``vcp2_config_hash``), so results made with different
settings are never mixed.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from vcp_scanner.domain.errors import ConfigError

# 2.1.0 (2026-11-11): owner's above-pivot rule. 2.2.0 (2026-11-11): forming contraction, earliest
# equal high, rally pullbacks merged (each a setting), shorter bases. 2.3.0 (2026-11-11): one
# contraction is enough to be a candidate.
VCP2_VERSION = "vcp-2.3.0"
CONFIG_FILE = "vcp2.yaml"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BaseStartConfig(_Strict):
    """3.1: a 26-week window, extended in steps only when its highest high is at the left edge."""

    window_bars: int = Field(130, gt=0)
    left_edge_share: float = Field(0.25, gt=0, lt=1)
    extend_bars: int = Field(30, gt=0)
    max_window_bars: int = Field(325, gt=0)
    # owner 2026-11-11: the base starts at the earliest swing high within the contraction
    # tolerance of the highest one (False: at the highest itself, as in v2.0)
    earliest_equal_high: bool = True

    @model_validator(mode="after")
    def _cap_not_below_window(self) -> BaseStartConfig:
        if self.max_window_bars < self.window_bars:
            raise ValueError(
                f"base_start.max_window_bars ({self.max_window_bars}) must be at least "
                f"window_bars ({self.window_bars})"
            )
        return self


class ContractionConfig(_Strict):
    """3.2: falling legs; levels compared with a tolerance; undercut merge; shakeout (off)."""

    level_tolerance_pct: float = Field(1.5, ge=0, lt=20)
    break_on: Literal["close", "high"] = "close"
    shakeout_tolerance_pct: float = Field(0.0, ge=0, lt=50)
    shakeout_recovery_bars: int = Field(5, gt=0)
    # owner 2026-11-11: a pullback whose peak a later contraction's peak exceeds (by more than
    # the tolerance) was a pause in the rally, not a contraction (False: counted, as in v2.0)
    merge_rally_pullbacks: bool = True


class SwingConfig(_Strict):
    """Swing highs by the n-bar rule; the minimum swing is only a safety net for tiny wiggles."""

    left_bars: int = Field(5, gt=0)
    right_bars: int = Field(5, gt=0)
    min_depth_pct: float = Field(2.0, ge=0)
    min_duration_bars: int = Field(3, ge=0)
    short_swing_max_depth_pct: float = Field(4.0, ge=0)
    # owner 2026-11-11: a forming contraction counts. At the right edge a high followed by at
    # least this many bars (all lower) also splits contractions; it is never a base start.
    # Equal to right_bars: off, as in v2.0.
    edge_right_bars: int = Field(2, gt=0)

    @model_validator(mode="after")
    def _edge_not_above_right(self) -> SwingConfig:
        if self.edge_right_bars > self.right_bars:
            raise ValueError("swing.edge_right_bars must not exceed right_bars")
        return self


class PriorAdvanceConfig(_Strict):
    lookback_bars: int = Field(120, gt=0)
    long_lookback_bars: int = Field(252, gt=0)
    min_pct: float = Field(20.0, ge=0)


class CandidateConfig(_Strict):
    """3.1: the deliberately loose minimum to be a candidate."""

    min_base_bars: int = Field(10, gt=0)  # owner 2026-11-11: shorter bases allowed (v2.0: 15)
    min_depth_pct: float = Field(3.0, ge=0)
    max_depth_pct: float = Field(50.0, gt=0, le=100)
    # owner 2026-11-11 (labelling round 2): one contraction counts (a VCP complete or forming);
    # "at least 2 or 3" is research rule A1 (v2.0-2.2: 2)
    min_contractions: int = Field(1, ge=1)
    # owner 2026-11-11: once a close has been more than 5-7 % above the pivot, it is not a VCP
    max_above_pivot_pct: float = Field(7.0, gt=0)

    @model_validator(mode="after")
    def _depth_range(self) -> CandidateConfig:
        if self.min_depth_pct >= self.max_depth_pct:
            raise ValueError("candidate.min_depth_pct must be below max_depth_pct")
        return self


class PivotConfig(_Strict):
    right_side_window: int = Field(10, gt=0)


class VCP2Config(_Strict):
    base_start: BaseStartConfig = BaseStartConfig()
    contraction: ContractionConfig = ContractionConfig()
    swing: SwingConfig = SwingConfig()
    prior_advance: PriorAdvanceConfig = PriorAdvanceConfig()
    candidate: CandidateConfig = CandidateConfig()
    pivot: PivotConfig = PivotConfig()


def load_vcp2_config(config_dir: str | Path = "config") -> VCP2Config:
    """``<config_dir>/vcp2.yaml``; the defaults when the file is absent."""
    path = Path(config_dir) / CONFIG_FILE
    raw: dict[str, Any] = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    data = raw.get("vcp2", raw)
    try:
        return VCP2Config(**data)
    except ValidationError as exc:
        raise ConfigError(f"invalid {path}: {exc}") from exc


def vcp2_config_hash(cfg: VCP2Config) -> str:
    """Hash of every v2 setting plus the detector version (a code change also forks it)."""
    payload = {"version": VCP2_VERSION, "settings": cfg.model_dump()}
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
