"""Compare v1 and v2 VCP detection with the owner's blind chart labels.

Reads data/research/vcp2_labels.duckdb (read-only) and prints:
  1. the owner's verdicts by sample group and by each detector's reading;
  2. precision / recall of each detector against the owner's "yes";
  3. placement error of T1, the contraction lows and the pivot, in bars and %;
  4. contraction count, owner vs detector;
  5. v2 measurements on Yes vs No charts (descriptive only).
Writes one row per chart to data/research/vcp2_label_report.csv.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections.abc import Callable
from datetime import date
from pathlib import Path
from statistics import median
from typing import Any

import duckdb

ROOT = Path(__file__).resolve().parents[2]
LABELS = ROOT / "data" / "research" / "vcp2_labels.duckdb"
RESEARCH = ROOT / "data" / "research" / "database_research.duckdb"
OUT = ROOT / "data" / "research" / "vcp2_label_report.csv"
VCP_CLASSES = {"VCP", "VCP_LIKE", "A_PLUS_VCP"}
MATCH_BARS = 5  # a detector low within this many bars of an owner low counts as the same low


def d(x: Any) -> date | None:
    return None if x is None else x if isinstance(x, date) else date.fromisoformat(str(x)[:10])


def tt_status(rows: list[tuple[Any, ...]]) -> dict[int, str]:
    """v1's stored Trend Template status for each chart's stock on its scan day (the samples
    are drawn on v1 weekly scan days, so every chart has one)."""
    c = duckdb.connect(str(RESEARCH), read_only=True)
    c.execute("CREATE TEMP TABLE s (sid INT, iid VARCHAR, d DATE)")
    c.executemany("INSERT INTO s VALUES (?,?,?)", rows)
    out = dict(c.execute("""
        SELECT s.sid, coalesce(t.status, 'NONE') FROM s LEFT JOIN trend_template_results t
          ON t.instrument_id = s.iid AND t.as_of_date = s.d""").fetchall())  # fmt: skip
    c.close()
    return out


def load() -> list[dict[str, Any]]:
    c = duckdb.connect(str(LABELS), read_only=True)
    tts = tt_status(c.execute("SELECT sample_id, instrument_id, scan_date FROM samples").fetchall())
    rows = c.execute("""
        SELECT s.sample_id, s.symbol, s.scan_date, t.grp, t.v1_class, t.v2_candidate,
               t.v2_json, t.v1_json, l.verdict, l.t1_date, l.t1_price, l.lows_json,
               l.pivot_date, l.pivot_price, s.instrument_id
        FROM samples s JOIN sample_truth t USING (sample_id)
        LEFT JOIN labels l USING (sample_id) ORDER BY s.sample_id""").fetchall()  # fmt: skip
    bars: dict[int, list[date]] = {}
    for sid, td in c.execute(
        "SELECT sample_id, trade_date FROM sample_bars ORDER BY sample_id, trade_date"
    ).fetchall():
        bars.setdefault(sid, []).append(td)
    c.close()
    out = []
    for r in rows:
        v2 = json.loads(r[6] or "[]")
        v1 = json.loads(r[7] or "[]")
        v2r = v2[0] if v2 else None
        out.append({
            "id": r[0], "symbol": r[1], "scan_date": r[2], "grp": r[3], "iid": r[14],
            "v1_class": r[4], "v1_pick": r[4] in VCP_CLASSES, "v2_pick": bool(r[5]),
            "v2": v2r, "v2_legs": json.loads(v2r["contractions_json"]) if v2r else [],
            "v1": v1[0] if v1 else None, "verdict": r[8], "t1_date": r[9], "t1_price": r[10],
            "lows": json.loads(r[11] or "[]"), "pivot_date": r[12], "pivot_price": r[13],
            "bars": bars.get(r[0], []), "tt": tts.get(r[0], "NONE"),
        })  # fmt: skip
    return out


def bar_gap(bars: list[date], a: date | None, b: date | None) -> int | None:
    """Signed distance in trading bars from a to b (positive: b is later)."""
    if a is None or b is None or not bars:
        return None

    def idx(x: date) -> int:
        return min(range(len(bars)), key=lambda i: abs((bars[i] - x).days))

    return idx(b) - idx(a)


def pct(a: float | None, b: float | None) -> float | None:
    return None if a in (None, 0) or b is None else (b / a - 1) * 100


def share(xs: list[bool]) -> str:
    return f"{sum(xs)}/{len(xs)} ({100 * sum(xs) / len(xs):.0f}%)" if xs else "—"


def med(xs: list[float | None]) -> str:
    v = [x for x in xs if x is not None]
    return f"{median(v):.1f}" if v else "—"


def verdict_table(rows: list[dict[str, Any]], key: str, title: str) -> None:
    print(f"\n{title}")
    print(f"  {'':28s}{'yes':>6s}{'unsure':>8s}{'no':>6s}{'n':>5s}  yes%  yes+unsure%")
    groups: dict[str, list[str]] = {}
    for r in rows:
        groups.setdefault(r[key], []).append(r["verdict"])
    for g in sorted(groups):
        v = groups[g]
        y, u, n = v.count("yes"), v.count("unsure"), v.count("no")
        print(f"  {g:28s}{y:6d}{u:8d}{n:6d}{len(v):5d}  {100 * y / len(v):4.0f}  "
              f"{100 * (y + u) / len(v):6.0f}")  # fmt: skip


def detector_scores(rows: list[dict[str, Any]]) -> None:
    print(f"\n2. Each detector against the owner (all {len(rows)} charts)")
    yes = [r for r in rows if r["verdict"] == "yes"]
    yu = [r for r in rows if r["verdict"] in ("yes", "unsure")]

    def tt(r: dict[str, Any]) -> bool:
        return bool(r["tt"] == "PASS")

    dets: dict[str, Callable[[dict[str, Any]], bool]] = {
        "v1 (VCP/VCP_LIKE/A+)": lambda r: r["v1_pick"],
        "v2 candidate": lambda r: r["v2_pick"],
        "v2 candidate + TT pass": lambda r: r["v2_pick"] and tt(r),
        "v1 pick + TT pass": lambda r: r["v1_pick"] and tt(r),
        "Trend Template pass only": tt,
        "both v1 and v2": lambda r: r["v1_pick"] and r["v2_pick"],
    }
    print(f"  {'':26s}{'picks':>6s}  {'owner yes among picks':>22s}  {'yes+unsure':>12s}"
          f"  {'owner yes caught':>17s}")  # fmt: skip
    for name, f in dets.items():
        picks = [r for r in rows if f(r)]
        py = sum(r["verdict"] == "yes" for r in picks)
        pu = sum(r["verdict"] in ("yes", "unsure") for r in picks)
        cy = sum(f(r) for r in yes)
        print(f"  {name:26s}{len(picks):6d}  {py:>9d} ({100 * py / max(1, len(picks)):3.0f}%)"
              f"         {100 * pu / max(1, len(picks)):5.0f}%"
              f"  {cy:>7d} of {len(yes)} ({100 * cy / len(yes):.0f}%)")  # fmt: skip
    print(f"  (owner yes+unsure: {len(yu)} of {len(rows)})")


def placement(rows: list[dict[str, Any]], verdicts: tuple[str, ...]) -> list[dict[str, Any]]:
    """Per-chart placement errors where the owner marked the structure."""
    out = []
    for r in rows:
        if r["verdict"] not in verdicts:
            continue
        v2, v1, b = r["v2"], r["v1"], r["bars"]
        e: dict[str, Any] = {"id": r["id"], "symbol": r["symbol"], "verdict": r["verdict"]}
        if r["t1_date"]:
            e["v2_t1_bars"] = bar_gap(b, r["t1_date"], d(v2["base_start_date"])) if v2 else None
            e["v2_t1_pct"] = pct(r["t1_price"], v2["base_high"]) if v2 else None
            e["v1_t1_bars"] = (bar_gap(b, r["t1_date"], d(v1["base_start_date"]))
                               if v1 and v1["base_start_date"] else None)  # fmt: skip
        if r["pivot_price"]:
            e["v2_pivot_pct"] = pct(r["pivot_price"], v2["pivot_price"]) if v2 else None
            e["v1_pivot_pct"] = pct(r["pivot_price"], v1["pivot_price"]) if v1 else None
        if r["lows"]:
            e["owner_n"] = len(r["lows"])
            e["v2_n"] = v2["contraction_count"] if v2 else None
            e["v1_n"] = v1["contraction_count"] if v1 else None
            if v2:
                own = [d(x["date"]) for x in r["lows"]]
                det = [d(x["low_date"]) for x in r["v2_legs"]]

                def hit(a: date | None, bs: list[date | None], b: list[date] = b) -> bool:
                    gaps = [bar_gap(b, a, x) for x in bs]
                    return any(g is not None and abs(g) <= MATCH_BARS for g in gaps)

                e["owner_lows_found"] = sum(hit(a, det) for a in own)
                e["v2_lows_matched"] = sum(hit(a, own) for a in det)
                e["v2_lows"] = len(det)
        out.append(e)
    return out


def placement_summary(es: list[dict[str, Any]], title: str) -> None:
    print(f"\n{title}")

    def line(name: str, key: str, unit: str, near: float) -> None:
        v = [e[key] for e in es if e.get(key) is not None]
        if not v:
            print(f"  {name:34s} —")
            return
        a = [abs(x) for x in v]
        print(f"  {name:34s} n={len(v):3d}  median |error| {median(a):6.1f}{unit}  "
              f"within {near:g}{unit}: {share([x <= near for x in a])}  "
              f"median signed {median(v):+.1f}{unit}")  # fmt: skip

    line("T1 date, v2 (bars)", "v2_t1_bars", " bars", 5)
    line("T1 date, v1 (bars)", "v1_t1_bars", " bars", 5)
    line("T1 price, v2 (%)", "v2_t1_pct", "%", 2)
    line("Pivot price, v2 (%)", "v2_pivot_pct", "%", 2)
    line("Pivot price, v1 (%)", "v1_pivot_pct", "%", 2)
    cnt = [e for e in es if e.get("owner_n") is not None]
    for k in ("v2_n", "v1_n"):
        c = [e for e in cnt if e.get(k) is not None]
        if c:
            same = [e[k] == e["owner_n"] for e in c]
            diff = [e[k] - e["owner_n"] for e in c]
            print(f"  Contraction count, {k[:2]} = owner's:   {share(same)}; "
                  f"within one: {share([abs(x) <= 1 for x in diff])}; "
                  f"median detector − owner {median(diff):+.0f}")  # fmt: skip
    lw = [e for e in es if e.get("v2_lows") is not None]
    if lw:
        f = sum(e["owner_lows_found"] for e in lw)
        o = sum(e["owner_n"] for e in lw)
        m = sum(e["v2_lows_matched"] for e in lw)
        t = sum(e["v2_lows"] for e in lw)
        print(f"  Lows (v2 within {MATCH_BARS} bars): owner lows found by v2 {f}/{o} "
              f"({100 * f / max(1, o):.0f}%); v2 lows the owner also marked {m}/{t} "
              f"({100 * m / max(1, t):.0f}%)")  # fmt: skip


FEATURES = [
    "trend_template_pass", "rs_rank", "above_sma50_pct", "above_sma200_pct",
    "sma200_slope_pct", "prior_advance_pct", "base_bars", "base_depth_pct",
    "close_in_base_pct", "contraction_count", "tightening_share", "final_depth_pct",
    "final_bars", "distance_to_pivot_pct", "right_side_range_pct", "atr_ratio",
    "final_volume_ratio", "last5_volume_ratio", "undercuts", "window_extensions",
]  # fmt: skip


def features(rows: list[dict[str, Any]]) -> None:
    print("\n5. v2 measurements on the scan day, by owner verdict (medians; descriptive only)")
    by = {v: [r for r in rows if r["verdict"] == v and r["v2"]] for v in ("yes", "unsure", "no")}
    print(f"  {'':24s}" + "".join(f"{v + ' (' + str(len(by[v])) + ')':>14s}" for v in by))
    for f in FEATURES:
        cells = []
        for v in by:
            xs = [r["v2"][f] for r in by[v]]
            if f == "trend_template_pass":
                cells.append(f"{100 * sum(bool(x) for x in xs) / max(1, len(xs)):.0f}%")
            else:
                cells.append(med([float(x) if x is not None else None for x in xs]))
        print(f"  {f:24s}" + "".join(f"{c:>14s}" for c in cells))
    no_base = {v: sum(1 for r in rows if r["verdict"] == v and not r["v2"]) for v in by}
    print(f"  no v2 base at all:      {no_base}")


GROUPS = {
    1: {"A": "A: drawn as v2 candidate", "B": "B: drawn as v1 VCP", "C": "C: TT pass, neither"},
    2: {"A": "A: TT pass, v2 candidate", "B": "B: TT pass, not v2"},
}


def main() -> None:
    global LABELS, OUT
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", type=int, choices=(1, 2), default=1)
    a = ap.parse_args()
    if a.round == 2:
        LABELS = ROOT / "data" / "research" / "vcp2_labels_r2.duckdb"
        OUT = ROOT / "data" / "research" / "vcp2_label_report_r2.csv"
    rows = load()
    todo = sum(r["verdict"] is None for r in rows)
    print(f"{len(rows)} charts, {len(rows) - todo} labelled")
    rows = [r for r in rows if r["verdict"]]
    for r in rows:
        r["quad"] = {(True, True): "both v1 and v2", (True, False): "v1 only",
                     (False, True): "v2 only", (False, False): "neither"}[
                         (r["v1_pick"], r["v2_pick"])]  # fmt: skip
        r["grp_name"] = GROUPS[a.round][r["grp"]]
    verdict_table(rows, "grp_name", "1a. Owner verdict by sample group")
    verdict_table(rows, "quad", "1b. Owner verdict by what each detector said")
    for r in rows:
        r["tt_quad"] = f"TT {r['tt']:4s} · {r['quad']}"
    verdict_table(rows, "tt_quad", "1c. Same, split by Trend Template (v1's stored result)")
    detector_scores(rows)
    es_y = placement(rows, ("yes",))
    es_a = placement(rows, ("yes", "unsure", "no"))
    placement_summary(es_y, "3a. Placement vs the owner's marks — owner said yes")
    placement_summary(es_a, "3b. Placement vs the owner's marks — every chart the owner marked")
    features(rows)
    with OUT.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["sample_id", "symbol", "scan_date", "group", "verdict", "v1_class",
                    "v2_candidate", "tt_pass", "owner_lows", "v2_contractions",
                    "v1_contractions", "v2_t1_bars", "v1_t1_bars", "v2_pivot_pct",
                    "v1_pivot_pct"])  # fmt: skip
        pe = {e["id"]: e for e in es_a}
        for r in rows:
            e = pe.get(r["id"], {})
            w.writerow([r["id"], r["symbol"], r["scan_date"], r["grp"], r["verdict"],
                        r["v1_class"], r["v2_pick"],
                        r["tt"] == "PASS",
                        e.get("owner_n"), e.get("v2_n"), e.get("v1_n"),
                        e.get("v2_t1_bars"), e.get("v1_t1_bars"),
                        e.get("v2_pivot_pct"), e.get("v1_pivot_pct")])  # fmt: skip
    print(f"\nPer-chart rows: {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
