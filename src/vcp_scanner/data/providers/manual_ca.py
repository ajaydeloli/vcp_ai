"""Human-verified corporate actions from a version-controlled file (audit 2.7d).

Some price-scaling actions are in no feed: NSE's API omits them (DTIL's 2021 bonus, GICL's and
JSLL's 2025 splits) or lists them without a ratio (capital reductions). They are entered by
hand in ``config/manual_corporate_actions.yaml`` with the evidence they rest on, and
reconciliation gives them status ``MANUAL_OVERRIDE`` (DATA_SPECIFICATION 18A).

File format::

    actions:
      - symbol: JSLL
        isin: INE0J5801029          # optional; resolves the instrument before the symbol
        action_type: SPLIT          # SPLIT, BONUS, RIGHTS, DEMERGER, DIVIDEND or
                                    # CAPITAL_REDUCTION (a reviewed record; never adjusts)
                                    # DEMERGER takes price_factor (0 < f <= 1), not ratio
        ex_date: 2025-06-12
        ratio: [10, 2]              # same conventions as the NSE parser
        cash_amount: null           # RIGHTS: issue price (face value + premium)
        evidence: "NSE record-date notice ... (URL)"
        approved_by: Ajay
        entered_on: 2026-10-01

A missing file means no overrides. A file that does not validate raises ``ConfigError``: a
silently skipped override would leave prices unadjusted.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from vcp_scanner.data.identity import deterministic_action_id, mint_instrument_id
from vcp_scanner.domain.corporate_actions import CorporateAction
from vcp_scanner.domain.enums import CorporateActionType
from vcp_scanner.domain.errors import ConfigError
from vcp_scanner.domain.market import Instrument
from vcp_scanner.infrastructure.clock import Clock, utc_now

MANUAL_SOURCE = "MANUAL"
DEFAULT_FILE = "manual_corporate_actions.yaml"

_NEEDS_RATIO = {CorporateActionType.SPLIT, CorporateActionType.BONUS, CorporateActionType.RIGHTS}


class ManualActionEntry(BaseModel):
    """One hand-entered action. Every field that changes prices is required and checked."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: Annotated[str, Field(min_length=1)]
    isin: Annotated[str | None, Field(pattern=r"^IN[A-Z0-9]{9}[0-9]$")] = None
    action_type: CorporateActionType
    ex_date: date
    ratio: tuple[Annotated[float, Field(gt=0)], Annotated[float, Field(gt=0)]] | None = None
    cash_amount: Annotated[float | None, Field(ge=0)] = None
    #: DEMERGER only: the parent's price factor (0 < f <= 1), e.g. 0.9079 when 9.21 % of the
    #: value left with the demerged business. Stored as the ratio f:1.
    price_factor: Annotated[float | None, Field(gt=0, le=1)] = None
    #: CAPITAL_REDUCTION only: what kind it was and the share counts, for the record and the
    #: warning. ``cash_amount`` is the consideration paid per cancelled share, if any.
    reduction_kind: Literal["VOLUNTARY_TENDER", "IBC_RESOLUTION_PLAN", "OTHER"] | None = None
    shares_before: Annotated[int | None, Field(gt=0)] = None
    shares_after: Annotated[int | None, Field(ge=0)] = None
    evidence: Annotated[str, Field(min_length=10)]
    approved_by: Annotated[str, Field(min_length=1)]
    entered_on: date

    @model_validator(mode="after")
    def _ratio_where_needed(self) -> ManualActionEntry:
        is_demerger = self.action_type is CorporateActionType.DEMERGER
        if is_demerger and (self.price_factor is None or self.ratio is not None):
            raise ValueError("DEMERGER needs price_factor (and no ratio)")
        if not is_demerger and self.price_factor is not None:
            raise ValueError("price_factor is only for DEMERGER")
        if self.action_type in _NEEDS_RATIO and self.ratio is None:
            raise ValueError(f"{self.action_type.value} needs a ratio")
        is_split = self.action_type is CorporateActionType.SPLIT
        if is_split and self.ratio is not None and self.ratio[0] == self.ratio[1]:
            raise ValueError("a split's old and new face values must differ")
        if self.action_type is CorporateActionType.RIGHTS and self.cash_amount is None:
            raise ValueError("RIGHTS needs cash_amount (issue price)")
        is_reduction = self.action_type is CorporateActionType.CAPITAL_REDUCTION
        if is_reduction and self.ratio is not None:
            raise ValueError("CAPITAL_REDUCTION never adjusts prices: no ratio")
        if is_reduction and self.reduction_kind is None:
            raise ValueError("CAPITAL_REDUCTION needs reduction_kind")
        details = (self.reduction_kind, self.shares_before, self.shares_after)
        if not is_reduction and any(v is not None for v in details):
            raise ValueError("reduction_kind and share counts are only for CAPITAL_REDUCTION")
        return self

    def record_text(self) -> str:
        """What is stored as the raw action's ``source_record_id``."""
        head = f"{self.approved_by} {self.entered_on.isoformat()}: "
        if self.action_type is CorporateActionType.CAPITAL_REDUCTION:
            shares = (
                f" shares {self.shares_before:,} -> {self.shares_after:,};"
                if self.shares_before is not None and self.shares_after is not None
                else ""
            )
            paid = f" Rs {self.cash_amount:g} per cancelled share;" if self.cash_amount else ""
            head += f"[{self.reduction_kind}; price adjustment NONE;{shares}{paid}] "
        return head + self.evidence


class _ManualFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actions: list[ManualActionEntry] = Field(default_factory=list)


def load_manual_entries(path: str | Path) -> list[ManualActionEntry]:
    """Read and validate the override file; ``[]`` when it does not exist."""
    file_path = Path(path)
    if not file_path.exists():
        return []
    try:
        raw = yaml.safe_load(file_path.read_text(encoding="utf-8")) or {}
        entries = _ManualFile.model_validate(raw).actions
    except (yaml.YAMLError, ValidationError) as exc:
        raise ConfigError(f"Invalid manual corporate actions in {file_path}: {exc}") from exc
    keys = [(e.symbol.upper(), e.action_type, e.ex_date) for e in entries]
    duplicates = sorted({str(k) for k in keys if keys.count(k) > 1})
    if duplicates:
        raise ConfigError(f"Duplicate manual corporate actions in {file_path}: {duplicates}")
    return entries


class ManualCorporateActionProvider:
    """Serves the override file as raw actions with source ``MANUAL``.

    Every entry is returned on every run, whatever the requested window: an override added
    today for an old ex-date must still reach reconciliation on the next daily run, which only
    asks for recent dates. Saving is idempotent (deterministic IDs).
    """

    PROVIDER_NAME = MANUAL_SOURCE

    def __init__(self, path: str | Path, *, clock: Clock = utc_now) -> None:
        self._path = Path(path)
        self._clock = clock

    def get_actions(
        self, start: date, end: date, instruments: list[Instrument] | None = None
    ) -> list[CorporateAction]:
        symbols = {i.symbol for i in instruments} if instruments else None
        actions = []
        for e in load_manual_entries(self._path):
            symbol = e.symbol.upper()
            if symbols is not None and symbol not in symbols:
                continue
            num, den = e.ratio if e.ratio is not None else (None, None)
            if e.price_factor is not None:
                num, den = e.price_factor, 1.0
            actions.append(
                CorporateAction(
                    corporate_action_id=deterministic_action_id(
                        MANUAL_SOURCE,
                        e.isin or symbol,
                        e.action_type.value,
                        e.ex_date,
                        num,
                        den,
                        e.cash_amount,
                    ),  # fmt: skip
                    instrument_id=mint_instrument_id("NSE", symbol),
                    isin=e.isin,
                    action_type=e.action_type,
                    source=MANUAL_SOURCE,
                    created_at=self._clock(),
                    ex_date=e.ex_date,
                    ratio_numerator=num,
                    ratio_denominator=den,
                    cash_amount=e.cash_amount,
                    source_record_id=e.record_text(),
                )
            )
        return actions
