"""Walk-forward periods (PROJECT_DESIGN 41; Phase 9 step 4).

``config/backtest.yaml`` names the periods. It is read on its own and is *not* part of the scan
config hash: periods decide how results are looked at, not what a scan computes.

Owner decision (2026-10-03): development 2022-02 .. 2024-06, validation 2024-07 .. 2026-09,
and the true out-of-sample test is live paper tracking from 2026-10 (2025-10 .. 2026-09 was
already looked at in outcome checks). Guards:

* ``development`` is the only period shown by default; thresholds and rules are compared there;
* ``validation`` needs ``--validation`` and every look is counted in the run log (it is meant to
  be looked at rarely, for a choice already made on development);
* ``test`` (live paper) needs ``--test`` and at least ``min_test_months`` of data.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

Role = Literal["development", "validation", "test"]


class Period(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    role: Role
    start: date
    end: date | None = None  # None = up to the latest data


class BacktestConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    periods: list[Period] = Field(default_factory=lambda: [
        Period(name="development", role="development", start=date(2022, 2, 1),
               end=date(2024, 6, 30)),
        Period(name="validation", role="validation", start=date(2024, 7, 1),
               end=date(2026, 9, 30)),
        Period(name="live_paper", role="test", start=date(2026, 10, 1)),
    ])  # fmt: skip
    min_test_months: Annotated[int, Field(ge=1)] = 6

    @model_validator(mode="after")
    def validate_periods(self) -> BacktestConfig:
        names = [p.name for p in self.periods]
        if len(set(names)) != len(names):
            raise ValueError("period names must be unique")
        ordered = sorted(self.periods, key=lambda p: p.start)
        for a, b in zip(ordered, ordered[1:], strict=False):
            if a.end is None or a.end >= b.start:
                raise ValueError(f"periods {a.name} and {b.name} overlap")
        for p in self.periods:
            if p.end is not None and p.end < p.start:
                raise ValueError(f"period {p.name} ends before it starts")
        roles = [p.role for p in ordered]
        order = {"development": 0, "validation": 1, "test": 2}
        if roles != sorted(roles, key=order.__getitem__):
            raise ValueError("periods must run development, then validation, then test")
        return self


def load_backtest_config(config_dir: str | Path = "config") -> BacktestConfig:
    path = Path(config_dir) / "backtest.yaml"
    if not path.exists():
        return BacktestConfig()
    raw = yaml.safe_load(path.read_text()) or {}
    return BacktestConfig(**raw)


def months_between(a: date, b: date) -> int:
    return (b.year - a.year) * 12 + b.month - a.month
