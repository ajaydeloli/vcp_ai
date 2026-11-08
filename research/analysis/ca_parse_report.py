"""Parse the saved NSE corporate-action records with the project's parser and report what it
cannot read, by year (owner, 2026-11-08). Read-only: it only reads
``data/research/nse_ca_raw/*.json`` (from ``fetch_nse_ca_raw.py``).

``parsed_actions()`` is also used to compare two parser versions on the same records.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from vcp_scanner.data.providers.nse_ca import NSECorporateActionProvider

RAW = Path(__file__).resolve().parents[2] / "data" / "research" / "nse_ca_raw"


def records() -> list[dict]:
    out = []
    for f in sorted(RAW.glob("*.json")):
        for index, items in json.loads(f.read_text()).items():
            for it in items:
                out.append({**it, "_index": index})
    return out


def parsed_actions(provider: NSECorporateActionProvider | None = None) -> dict[str, tuple]:
    """Every record's parse result, keyed by symbol + ex-date + text: (type, num, den, cash)."""
    p = provider or NSECorporateActionProvider()
    p.unparsed_ratios, p.unhandled_records = [], []
    out = {}
    for it in records():
        a = p._parse_nse_action(it)
        key = f"{it.get('symbol')}|{it.get('exDate')}|{it.get('subject')}|{it.get('purpose')}"
        out[key] = None if a is None else (
            str(a.action_type.value), a.ratio_numerator, a.ratio_denominator, a.cash_amount)
    return out


def year_of(ex: str | None) -> str:
    m = re.search(r"(\d{4})$", ex or "")
    return m.group(1) if m else "?"


def main(first: int, last: int) -> None:
    p = NSECorporateActionProvider()
    p.unparsed_ratios, p.unhandled_records = [], []
    by_year: dict[str, Counter] = defaultdict(Counter)
    unparsed: list[str] = []
    unhandled: list[str] = []
    for it in records():
        y = year_of(it.get("exDate"))
        if not y.isdigit() or not first <= int(y) <= last:
            continue
        n_unp, n_unh = len(p.unparsed_ratios), len(p.unhandled_records)
        a = p._parse_nse_action(it)
        by_year[y][a.action_type.value if a else "ignored"] += 1
        if len(p.unparsed_ratios) > n_unp:
            unparsed.append(f"{y} {p.unparsed_ratios[-1]}")
        if len(p.unhandled_records) > n_unh:
            unhandled.append(f"{y} {p.unhandled_records[-1]}")
    kinds = sorted({k for c in by_year.values() for k in c})
    print("year  " + "  ".join(f"{k[:11]:>11}" for k in kinds))
    for y in sorted(by_year):
        print(f"{y}  " + "  ".join(f"{by_year[y][k]:>11}" for k in kinds))
    print(f"\nRatios not read ({len(unparsed)}): split/bonus/rights kept with factor 1.0")
    for u in unparsed:
        print("  ", u[:160])
    print(f"\nUnhandled (manual review, {len(unhandled)}), first 40:")
    for u in unhandled[:40]:
        print("  ", u[:160])


if __name__ == "__main__":
    first = int(sys.argv[1]) if len(sys.argv) > 1 else 2011
    last = int(sys.argv[2]) if len(sys.argv) > 2 else 2021
    main(first, last)
