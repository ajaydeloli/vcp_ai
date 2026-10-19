"""Data for the F7 study: labelled setups from the research database, fundamentals and
shareholding from the main database, all point in time (FUNDAMENTALS_SPECIFICATION §12, §15)."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from vcp_scanner.config.models import FundamentalScoreComponents
from vcp_scanner.fundamentals.research import METRIC_COLUMNS, PointInTime, scores_as_of
from vcp_scanner.research.fundamental_study import HORIZONS, StudyRow

SHAREHOLDING_KEYS = ("promoter_change_1q", "fii_change_1q", "dii_change_1q", "pledge_rising")


def frozen_hashes(main: Any) -> dict[str, str]:
    """strategy -> the config hash of its frozen version: the one the live labels carry. The
    research database also holds labels of earlier configs, which must not be mixed in."""
    rows = main.execute(
        "SELECT strategy_id, config_hash, count(*) FROM forward_labels GROUP BY 1, 2"
    ).fetchall()
    best: dict[str, tuple[int, str]] = {}
    for sid, h, n in rows:
        if sid not in best or n > best[sid][0]:
            best[str(sid)] = (int(n), str(h))
    return {sid: h for sid, (_n, h) in best.items()}


def load_setups(research: Any, start: date, end: date, hashes: dict[str, str]) -> list[StudyRow]:
    cols = ", ".join(f"ret_{h}" for h in HORIZONS)
    rows = research.execute(
        f"SELECT strategy_id, instrument_id, as_of_date, {cols}, breakout_within_20,"
        " failed_breakout, config_hash FROM forward_labels"
        " WHERE as_of_date BETWEEN ? AND ? ORDER BY as_of_date, strategy_id, instrument_id",
        [start, end],
    ).fetchall()
    out: list[StudyRow] = []
    n = len(HORIZONS)
    for r in rows:
        if hashes.get(str(r[0])) != r[5 + n]:
            continue
        rets = {h: (None if r[3 + k] is None else float(r[3 + k])) for k, h in enumerate(HORIZONS)}
        out.append(StudyRow(
            strategy=str(r[0]), instrument_id=str(r[1]), day=r[2], returns=rets,
            breakout_within_20=None if r[3 + n] is None else bool(r[3 + n]),
            failed_breakout=None if r[4 + n] is None else bool(r[4 + n]),
        ))  # fmt: skip
    return out


def attach_fundamentals(
    main: Any, rows: list[StudyRow], comps: FundamentalScoreComponents, min_availability: float
) -> list[PointInTime]:
    pairs = sorted({(r.instrument_id, r.day) for r in rows})
    scored = scores_as_of(main, pairs, comps, min_availability)
    by = {(p.instrument_id, p.day): p for p in scored}
    for r in rows:
        p = by.get((r.instrument_id, r.day))
        if p is None:
            continue
        r.keys["shadow_score"] = p.shadow.score
        r.keys["availability"] = p.shadow.availability if p.view_date else None
        r.keys["gate_pass"] = None if p.gate_pass is None else float(p.gate_pass)
        for name in METRIC_COLUMNS:
            r.keys[f"m_{name}"] = p.metrics.get(name)
    return scored


def attach_shareholding(main: Any, rows: list[StudyRow]) -> None:
    from vcp_scanner.api.shareholding import shareholding_as_of

    by_day: dict[date, set[str]] = defaultdict(set)
    for r in rows:
        by_day[r.day].add(r.instrument_id)
    found: dict[tuple[str, date], Any] = {}
    for day, ids in by_day.items():
        for iid, summary in shareholding_as_of(main, sorted(ids), day).items():
            found[(iid, day)] = summary
    for r in rows:
        s = found.get((r.instrument_id, r.day))
        if s is None:
            continue
        r.keys["promoter_change_1q"] = s.promoter_change_1q
        r.keys["fii_change_1q"] = s.fii_change_1q
        r.keys["dii_change_1q"] = s.dii_change_1q
        r.keys["pledge_rising"] = None if s.pledged_change_1q is None else float(s.pledge_rising)
