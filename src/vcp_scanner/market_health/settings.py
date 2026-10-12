"""``config/market_health.yaml`` as typed settings, and its version (a hash of the file's
meaning, so a stored day says which settings scored it)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

Label = Literal["Strong", "Constructive", "Mixed", "Weak", "Adverse"]
LABEL_ORDER: tuple[Label, ...] = ("Strong", "Constructive", "Mixed", "Weak", "Adverse")
PILLARS = ("index_trend", "breadth", "leadership", "accumulation", "breakouts")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Rung(_Strict):
    """One scored reading: its weight inside the pillar and its ladder."""

    weight: float = Field(ge=0)
    ladder: list[tuple[float, float]]

    @field_validator("ladder")
    @classmethod
    def _sorted(cls, v: list[tuple[float, float]]) -> list[tuple[float, float]]:
        if len(v) < 2 or any(b[0] <= a[0] for a, b in zip(v, v[1:], strict=False)):
            raise ValueError("a ladder needs two or more points with rising readings")
        return v


class IndexTrend(_Strict):
    above_50: float
    above_150: float
    above_200: float
    stacked: float
    rising_200: float
    slope_sessions: int
    confirm_nifty50: float
    confirm_smallcap: float


class Breadth(_Strict):
    above_50: Rung
    above_150: Rung
    above_200: Rung
    ad_trend: Rung
    ad_sessions: int
    highs_lows: Rung
    highs_lows_sessions: int
    change: Rung
    change_sessions: int
    vqi_gap: Rung
    gap_sessions: int


class Leadership(_Strict):
    leader_rs: int
    tt_share: Rung
    leader_change: Rung
    leader_sessions: int
    highs_20: Rung
    highs_52: Rung
    highs_sessions: int
    sectors: Rung
    sector_horizons: list[int]
    leaders_vs_vqi: Rung


class Accumulation(_Strict):
    day_move: float
    window: int
    distribution: Rung
    nifty_share: float = Field(ge=0, le=1)
    accumulation_days: Rung
    up_down_value: Rung
    value_sessions: int
    clustering: Rung
    cluster_sessions: int
    up_on_volume: Rung


class Breakouts(_Strict):
    volume_ratio: float
    base_sessions: int
    judge_sessions: int
    window: int
    min_judged: int
    follow_through: float
    hard_fail: float
    hold_sessions: int
    success: Rung
    held: Rung
    failure: Rung
    median_return: Rung
    near_pivot: Rung
    near_pivot_gap: float


class Cap(_Strict):
    max: Label
    above_50_below: float | None = None
    days: int | None = None
    rate_above: float | None = None
    min_judged: int | None = None
    drop: float | None = None
    sessions: int | None = None
    near_high: float | None = None
    gap: float | None = None


class Caps(_Strict):
    downtrend: Cap
    distribution: Cap
    failures: Cap
    tt_collapse: Cap
    divergence: Cap
    incomplete: Cap


class Thrust(_Strict):
    sessions: int
    low: float
    high: float


class HealthSettings(_Strict):
    version: str
    pillars: dict[str, float]
    labels: list[tuple[float, Label]]
    exposure: dict[Label, Literal["full", "half", "none"]]
    index_trend: IndexTrend
    breadth: Breadth
    leadership: Leadership
    accumulation: Accumulation
    breakouts: Breakouts
    caps: Caps
    thrust: Thrust
    min_universe: int

    @field_validator("pillars")
    @classmethod
    def _five(cls, v: dict[str, float]) -> dict[str, float]:
        if set(v) != set(PILLARS):
            raise ValueError(f"pillars must be exactly {', '.join(PILLARS)}")
        return v

    @property
    def settings_version(self) -> str:
        """``<version>+<first 10 hex of the settings' hash>``: two files that mean the same
        thing share a version, whatever their comments or layout."""
        blob = json.dumps(self.model_dump(mode="json"), sort_keys=True).encode()
        return f"{self.version}+{hashlib.sha256(blob).hexdigest()[:10]}"


DEFAULT_PATH = Path("config/market_health.yaml")


def load_settings(path: Path | str = DEFAULT_PATH) -> HealthSettings:
    with open(path, encoding="utf-8") as fh:
        return HealthSettings.model_validate(yaml.safe_load(fh))
