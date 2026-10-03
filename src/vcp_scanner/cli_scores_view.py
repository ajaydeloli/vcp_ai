"""``vcp scores list`` and ``vcp scores explain`` (Phase 7 step 4): read stored setup scores.

Both read the score scan of a date (``score-<date>-<hash12>`` of the current config; the latest
date with one when ``--as-of`` is omitted). ``list`` shows the ranked setups (``--all`` adds the
unranked passers); ``explain`` shows how one stock's score was built, sub-component by
sub-component, so every number can be checked by hand (PROJECT_DESIGN Phase 7: "scores are
reproducible and explainable").
"""

from __future__ import annotations

import argparse
from datetime import date
from typing import Any

from vcp_scanner.cli_pipeline import _err, _open_store, _parse_date
from vcp_scanner.config import load_scanner_config
from vcp_scanner.config.loader import scan_config_hash

_PLAIN = {
    "high_proximity": "% below 52-week high",
    "sma200_slope": "SMA200 change over 21 sessions %",
    "ma_stack_margin": "SMA50 above SMA200 %",
    "contraction_sequence": "contractions",
    "tightening": "largest next/previous depth ratio",
    "final_contraction": "final contraction depth %",
    "volatility": "volatility contraction ratio",
    "pivot": "right-side range %",
    "base_structure": "base depth %",
    "dryup_quality": "final volume ratio",
    "up_down_volume": "up/down volume (50 sessions)",
    "distribution": "high-volume down days (25 sessions)",
    "rs_rank": "RS rank",
}
_ORDER = {"TREND": 0, "VCP": 1, "VOLUME": 2, "RS": 3, "FUNDAMENTAL": 4}


def _fmt(x: float | None, digits: int = 1) -> str:
    return "-" if x is None else f"{x:.{digits}f}"


def _scan(store: Any, as_of: date | None, config_hash: str) -> tuple[str, date] | None:
    """(scan id, date) of the requested or latest score scan of this config."""
    like = f"score-%-{config_hash[:12]}"
    if as_of is None:
        row = store.conn.execute(
            "SELECT scan_id, as_of_date FROM setup_scores WHERE scan_id LIKE ?"
            " ORDER BY as_of_date DESC LIMIT 1", [like],
        ).fetchone()  # fmt: skip
    else:
        row = store.conn.execute(
            "SELECT scan_id, as_of_date FROM setup_scores WHERE scan_id = ? LIMIT 1",
            [f"score-{as_of.isoformat()}-{config_hash[:12]}"],
        ).fetchone()
    return (str(row[0]), row[1]) if row else None


def _open(args: argparse.Namespace) -> tuple[Any, str] | None:
    as_of = None
    if args.as_of:
        as_of = _parse_date(args.as_of)
        if as_of is None:
            return None
    try:
        cfg = load_scanner_config(args.config_dir)
    except Exception as e:
        _err(f"Configuration error: {e}")
        return None
    return cfg, scan_config_hash(cfg)


def run_scores_list(args: argparse.Namespace) -> int:
    opened = _open(args)
    if opened is None:
        return 1
    _, config_hash = opened
    with _open_store(args.db) as store:
        found = _scan(store, _parse_date(args.as_of) if args.as_of else None, config_hash)
        if found is None:
            _err("No setup scores for that date and config. Run `vcp compute scores` first.")
            return 1
        scan_id, as_of = found
        rows = store.conn.execute(
            f"""
            SELECT coalesce(i.symbol, s.instrument_id), s.final_setup_score, s.ranking_percentile,
                   s.confirmation_state, s.classification, s.vcp_status, s.trend_score,
                   s.vcp_score, s.volume_score, s.rs_score, s.eligible
            FROM setup_scores s LEFT JOIN instruments i USING (instrument_id)
            WHERE s.scan_id = ? {"" if args.all else "AND s.eligible"}
            ORDER BY s.eligible DESC, s.final_setup_score DESC NULLS LAST, 1
            LIMIT ?
            """,
            [scan_id, args.limit],
        ).fetchall()  # fmt: skip
    print(f"Setup scores {as_of} ({scan_id}); {'all passers' if args.all else 'ranked setups'}")
    print(f"  {'#':>3} {'symbol':14} {'score':>5} {'pctl':>5} {'state':11} {'class':10} "
          f"{'status':11} {'trend':>5} {'vcp':>5} {'vol':>5} {'rs':>5}")  # fmt: skip
    for k, r in enumerate(rows, 1):
        mark = "" if r[10] else "  (not ranked)"
        print(f"  {k:3d} {r[0]:14.14} {_fmt(r[1]):>5} {_fmt(r[2], 0):>5} {r[3] or '-':11} "
              f"{r[4] or '-':10} {r[5] or '-':11} {_fmt(r[6]):>5} {_fmt(r[7]):>5} "
              f"{_fmt(r[8]):>5} {_fmt(r[9]):>5}{mark}")  # fmt: skip
    if not rows:
        print("  (none)")
    print("  Scores rank setups; they are not probabilities. Fundamentals are not scored yet.")
    return 0


def run_scores_explain(args: argparse.Namespace) -> int:
    opened = _open(args)
    if opened is None:
        return 1
    cfg, config_hash = opened
    with _open_store(args.db) as store:
        found = _scan(store, _parse_date(args.as_of) if args.as_of else None, config_hash)
        if found is None:
            _err("No setup scores for that date and config. Run `vcp compute scores` first.")
            return 1
        scan_id, as_of = found
        ids = [r[0] for r in store.conn.execute(
            "SELECT instrument_id FROM instruments WHERE upper(symbol) = upper(?)"
            " OR instrument_id = ?", [args.symbol, args.symbol]).fetchall()]  # fmt: skip
        head = store.conn.execute(
            "SELECT instrument_id, final_setup_score, ranking_percentile, eligible,"
            " classification, vcp_status, confirmation_state, flags, trend_score, vcp_score,"
            " volume_score, rs_score, fundamental_score, trend_weight, vcp_weight,"
            " volume_weight, rs_weight, fundamental_weight, scoring_version"
            " FROM setup_scores WHERE scan_id = ? AND instrument_id IN (SELECT unnest(?))",
            [scan_id, ids],
        ).fetchone()
        if head is None:
            _err(f"{args.symbol} has no setup score on {as_of} (not a Trend Template passer, "
                 "or unknown symbol).")  # fmt: skip
            return 1
        subs = store.conn.execute(
            "SELECT component, sub_component, raw_measurement, normalized_0_100,"
            " weight_within_component, points, max_points FROM score_components"
            " WHERE scan_id = ? AND instrument_id = ?",
            [scan_id, head[0]],
        ).fetchall()
    bounds = _bounds(cfg)
    print(f"{args.symbol.upper()} on {as_of}: final score {_fmt(head[1])} "
          f"({head[18]}; ranks setups, not a probability)")  # fmt: skip
    rank = (f"ranking percentile {_fmt(head[2], 0)} among {head[6] or '-'} setups"
            if head[3] else "not ranked (needs VCP_LIKE or better, forming / pivot-ready / "
            "breakout)")  # fmt: skip
    print(f"  {head[4] or 'no pattern'} / {head[5] or '-'}; {rank}")
    if head[7]:
        print(f"  flags: {head[7]}")
    comp = {"TREND": (head[8], head[13]), "VCP": (head[9], head[14]),
            "VOLUME": (head[10], head[15]), "RS": (head[11], head[16]),
            "FUNDAMENTAL": (head[12], head[17])}  # fmt: skip
    print("  final = sum(weight x component) / 100 over available components:")
    for name in sorted(comp, key=_ORDER.__getitem__):
        score, weight = comp[name]
        contrib = "-" if score is None else f"{score * weight / 100:.1f}"
        print(f"    {name:11} {_fmt(score):>5} x {weight:5.1f}% = {contrib}")
    for name in sorted({s[0] for s in subs}, key=_ORDER.__getitem__):
        print(f"  {name} ({_fmt(comp[name][0])}):")
        for _, sub, raw, norm, _w, pts, mx in sorted(
            (s for s in subs if s[0] == name), key=lambda s: -s[4]
        ):
            b = bounds.get((name, sub))
            rng = f"  [{b[0]:g} -> {b[1]:g}]" if b else ""
            label = _PLAIN.get(sub, sub)
            print(f"    {sub:20} {label:36} {_fmt(raw, 2):>7} -> {_fmt(norm, 0):>3}/100"
                  f"  {pts:5.1f} of {mx:4.1f} pts{rng}")  # fmt: skip
    return 0


def _bounds(cfg: Any) -> dict[tuple[str, str], tuple[float, float]]:
    c = cfg.strategy.scoring.components
    out = {}
    for name, comp in (("TREND", c.trend), ("VCP", c.vcp), ("VOLUME", c.volume)):
        for sub, b in comp.bounds.items():
            out[(name, sub)] = (b.worst, b.best)
    out[("RS", "rs_rank")] = (float(cfg.strategy.trend_template.min_rs_rank), 99.0)
    return out
