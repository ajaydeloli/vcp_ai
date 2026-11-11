"""Re-run VCP v2 on the owner's 150 labelled charts under several settings, one change at a time.

Read-only. Bars come from the research copy exactly as the daily scan loads them. The owner's
verdicts are adjusted by the owner's above-pivot rule (2026-11-11): a Yes whose highest close
after the owner's own pivot is more than 7 % above it counts as No.

These are the same 150 charts the changes were drawn from, so this checks that each change does
what was meant; it does not show that it generalises (a second labelling round does that).
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import duckdb

from vcp_scanner.domain.snapshot import LIVE_SNAPSHOT_ID
from vcp_scanner.patterns.vcp2.config import (
    BaseStartConfig,
    CandidateConfig,
    ContractionConfig,
    SwingConfig,
    VCP2Config,
)
from vcp_scanner.patterns.vcp2.detector import Bars, Candidate, detect

sys.path.insert(0, str(Path(__file__).resolve().parent))
from label_report import RESEARCH, bar_gap, load  # noqa: E402

RULE_PCT = 7.0
MATCH = 5
NEW = VCP2Config()
NO_RULE = CandidateConfig(min_base_bars=15, max_above_pivot_pct=1e9)
V20 = VCP2Config(
    base_start=BaseStartConfig(earliest_equal_high=False),
    contraction=ContractionConfig(merge_rally_pullbacks=False),
    swing=SwingConfig(edge_right_bars=5),
    candidate=NO_RULE,
)
V21 = V20.model_copy(update={"candidate": CandidateConfig(min_base_bars=15)})
VARIANTS: dict[str, VCP2Config] = {
    "v2.0 (stored scan)": V20,
    "v2.1 above-pivot rule": V21,
    "  + forming contraction": V21.model_copy(update={"swing": SwingConfig()}),
    "  + earliest equal high": V21.model_copy(update={"base_start": BaseStartConfig()}),
    "  + shorter base (10)": V21.model_copy(update={"candidate": CandidateConfig()}),
    "  + merge rally pullbacks": V21.model_copy(update={"contraction": ContractionConfig()}),
    "v2.2 all four": NEW,
}


def bars_for(con: duckdb.DuckDBPyConnection, iid: str, day: date) -> Bars:
    rows = con.execute("""
        SELECT trade_date, high_adj, low_adj, close_adj, volume_adj
        FROM daily_prices_adjusted_current
        WHERE instrument_id = ? AND computed_from_snapshot_id = ?
          AND trade_date BETWEEN ? AND ? ORDER BY trade_date""",
        [iid, LIVE_SNAPSHOT_ID, day - timedelta(days=900), day]).fetchall()  # fmt: skip
    return Bars([r[0] for r in rows], [r[1] for r in rows], [r[2] for r in rows],
                [r[3] for r in rows], [r[4] for r in rows])  # fmt: skip


def adjusted(r: dict[str, Any], b: Bars) -> str:
    """The owner's verdict under the owner's above-pivot rule (against the owner's pivot)."""
    if r["verdict"] != "yes" or not r["pivot_price"]:
        return str(r["verdict"])
    after = [c for d_, c in zip(b.dates, b.close, strict=True) if d_ > r["pivot_date"]]
    return "no" if after and max(after) > r["pivot_price"] * (1 + RULE_PCT / 100) else "yes"


def pct_of(part: int, whole: int) -> str:
    return f"{part}/{whole} ({100 * part / whole:.0f}%)" if whole else "—"


def score(rows: list[dict[str, Any]], found: list[Candidate | None]) -> list[str]:
    yes = [k for k, r in enumerate(rows) if r["adj"] == "yes"]
    trending = [k for k, r in enumerate(rows) if r["tt"] == "PASS"]
    picks = [k for k, c in enumerate(found) if c is not None and c.is_candidate]
    tpicks = [k for k in picks if k in trending]
    t1 = cnt = cnt1 = piv = lows_f = lows_o = lows_m = lows_d = 0
    n_t1 = n_cnt = n_piv = 0
    for r, c in zip(rows, found, strict=True):
        if c is None or r["adj"] != "yes":
            continue
        dates = r["dates"]
        if r["t1_date"]:
            n_t1 += 1
            g = bar_gap(dates, r["t1_date"], c.base_start_date)
            t1 += g is not None and abs(g) <= MATCH
        if r["lows"]:
            n_cnt += 1
            cnt += c.contraction_count == len(r["lows"])
            cnt1 += abs(c.contraction_count - len(r["lows"])) <= 1
            own = [date.fromisoformat(x["date"][:10]) for x in r["lows"]]
            det = [x.low_date for x in c.contractions]

            def near(a: date, bs: list[date], dates: list[date] = dates) -> bool:
                return any(abs(bar_gap(dates, a, x) or 0) <= MATCH for x in bs)

            lows_f += sum(near(a, det) for a in own)
            lows_o += len(own)
            lows_m += sum(near(a, own) for a in det)
            lows_d += len(det)
        if r["pivot_price"]:
            n_piv += 1
            piv += abs(c.pivot / r["pivot_price"] - 1) <= 0.02
    return [
        str(len(picks)),
        pct_of(sum(k in yes for k in picks), len(picks)),
        pct_of(sum(k in picks for k in yes), len(yes)),
        f"{sum(k in yes for k in tpicks)}/{len(tpicks)}",
        pct_of(sum(k in picks for k in yes if k in trending), sum(k in trending for k in yes)),
        pct_of(t1, n_t1),
        pct_of(cnt, n_cnt) + " / " + pct_of(cnt1, n_cnt),
        pct_of(lows_f, lows_o),
        pct_of(lows_m, lows_d),
        pct_of(piv, n_piv),
    ]


def main() -> None:
    rows = load()
    con = duckdb.connect(str(RESEARCH), read_only=True)
    series = []
    for r in rows:
        b = bars_for(con, r["iid"], r["scan_date"])
        r["dates"], r["adj"] = list(b.dates), adjusted(r, b)
        series.append(b)
    con.close()
    print(f"owner Yes {sum(r['verdict'] == 'yes' for r in rows)}, "
          f"after the above-pivot rule {sum(r['adj'] == 'yes' for r in rows)}; "
          f"Trend Template pass {sum(r['tt'] == 'PASS' for r in rows)}")  # fmt: skip
    # the v2.0 settings must reproduce the stored scan exactly
    old = [detect(b, V20).candidate for b in series]
    same = sum((c is not None and c.is_candidate) == r["v2_pick"]
               and (c.base_start_date.isoformat() if c else None)
               == (r["v2"]["base_start_date"] if r["v2"] else None)
               for r, c in zip(rows, old, strict=True))  # fmt: skip
    print(f"v2.0 settings reproduce the stored scan on {same} of {len(rows)} charts\n")
    head = ["picks", "Yes among picks", "Yes caught", "trending picks Yes",
            "trending Yes caught", "T1 ±5 bars", "count = / ±1", "owner lows found",
            "v2 lows owner marked", "pivot ±2%"]  # fmt: skip
    table = {name: score(rows, [detect(b, cfg).candidate for b in series])
             for name, cfg in VARIANTS.items()}  # fmt: skip
    for k, h in enumerate(head):
        print(f"{h:22s}" + "".join(f"{v[k]:>24s}" for v in table.values()))
    print(f"{'':22s}" + "".join(f"{n.strip():>24s}" for n in table))


if __name__ == "__main__":
    main()
