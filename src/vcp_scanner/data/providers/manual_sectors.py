"""Sectors entered by hand (``config/manual_sectors.yaml``) for stocks NSE's index lists do not
cover (DATA_SPECIFICATION §48.1). Validated strictly: a typo would otherwise become a sector of
its own on every card and filter."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import yaml

#: NSE's sector level (the "Industry" column of its index lists, the "Sector" line of a quote).
NSE_SECTORS = frozenset({
    "Automobile and Auto Components", "Capital Goods", "Chemicals", "Construction",
    "Construction Materials", "Consumer Durables", "Consumer Services", "Diversified",
    "Fast Moving Consumer Goods", "Financial Services", "Forest Materials", "Healthcare",
    "Information Technology", "Media Entertainment & Publication", "Metals & Mining",
    "Oil Gas & Consumable Fuels", "Power", "Realty", "Services", "Telecommunication", "Textiles",
    "Utilities",
})  # fmt: skip
STALE_AFTER = timedelta(days=365)
FILE_NAME = "manual_sectors.yaml"


@dataclass(frozen=True)
class ManualSector:
    symbol: str
    sector: str
    industry: str | None
    as_of: date


def load_manual_sectors(path: Path | str) -> tuple[list[ManualSector], list[str]]:
    """(valid entries, problems). A missing file is no entries and no problem; an entry with a
    problem is left out and named, the rest still load."""
    p = Path(path)
    if not p.exists():
        return [], []
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        return [], [f"{p.name}: not valid YAML ({exc})"]
    raw = data.get("sectors") or []
    if not isinstance(raw, list):
        return [], [f"{p.name}: 'sectors' must be a list"]
    out: list[ManualSector] = []
    problems: list[str] = []
    seen: set[str] = set()
    for n, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            problems.append(f"entry {n}: not a mapping")
            continue
        symbol = str(item.get("symbol") or "").strip().upper()
        sector = str(item.get("sector") or "").strip()
        industry = str(item.get("industry") or "").strip() or None
        as_of = item.get("as_of")
        if not symbol:
            problems.append(f"entry {n}: no symbol")
        elif symbol in seen:
            problems.append(f"{symbol}: listed twice")
        elif sector not in NSE_SECTORS:
            problems.append(f"{symbol}: '{sector}' is not one of NSE's sectors")
        elif not isinstance(as_of, date):
            problems.append(f"{symbol}: as_of must be a date (YYYY-MM-DD)")
        else:
            seen.add(symbol)
            out.append(ManualSector(symbol, sector, industry, as_of))
    return out, problems


def stale_entries(entries: list[ManualSector], today: date) -> list[ManualSector]:
    """Entries last checked more than STALE_AFTER ago."""
    return [e for e in entries if today - e.as_of > STALE_AFTER]
