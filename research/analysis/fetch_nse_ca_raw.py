"""Save NSE's raw corporate-action records, quarter by quarter (owner, 2026-11-08).

Read-only towards the databases: it only calls NSE's API (through the project's own provider
session, about one request a second) and writes JSON files under
``data/research/nse_ca_raw/``. The files are used to list the wordings the parser misses in
the old years and to compare parser versions on the same records. A quarter already saved is
not fetched again.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from vcp_scanner.data.providers.nse_ca import NSECorporateActionProvider

OUT = Path(__file__).resolve().parents[2] / "data" / "research" / "nse_ca_raw"


def quarters(start: date, end: date):
    """Calendar quarters from ``start`` to ``end`` (the first and last may be partial)."""
    d = start
    while d <= end:
        if d.month >= 10:
            nxt = date(d.year + 1, 1, 1)
        else:
            nxt = date(d.year, (d.month - 1) // 3 * 3 + 4, 1)
        yield d, min(nxt - timedelta(days=1), end)
        d = nxt


def main(start: date, end: date) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    p = NSECorporateActionProvider()
    p._init_session()
    for a, b in quarters(start, end):
        path = OUT / f"{a}_{b}.json"
        if path.exists():
            continue
        records = {}
        for index in p.INDEXES:
            params = {"index": index, "from_date": a.strftime("%d-%m-%Y"),
                      "to_date": b.strftime("%d-%m-%Y")}  # fmt: skip
            for _attempt in range(3):
                r = p._session.get(p.API_URL, params=params, timeout=30)
                time.sleep(1.0)
                if r.status_code == 200:
                    break
                p._init_session()
                time.sleep(5)
            r.raise_for_status()
            records[index] = r.json()
        path.write_text(json.dumps(records))
        n = sum(len(v) for v in records.values())
        print(f"{a} .. {b}: {n} records", flush=True)


if __name__ == "__main__":
    s = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date(2011, 6, 1)
    e = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else date(2026, 11, 7)
    main(s, e)
