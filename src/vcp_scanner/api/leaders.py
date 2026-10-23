"""Past Leaders (owner, 2026-10-23): the runs of the stored superperformance study.

Read only, from the tables ``vcp research superperformance`` writes (nothing is recomputed per
request). Research and display: nothing here is read by a scan, a score, a strategy or the paper
ledger. A serving copy made before the study existed has no tables yet; the page then says that no
study is stored.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

import duckdb

from vcp_scanner.api import models as m
from vcp_scanner.api.queries import Cur, bars
from vcp_scanner.domain.snapshot import LIVE_SNAPSHOT_ID as LIVE

UNCLASSIFIED = "Unclassified"
BANDS = ("50-100", "doubled", "tripled")
BARS_BEFORE, BARS_AFTER = 40, 60  # sessions shown around a run

_COLS = (
    "run_id", "symbol", "sector", "launch_date", "launch_close", "peak_date", "peak_close",
    "gain_pct", "gain_band", "sessions_to_peak", "complete", "max_pullback_pct", "to50",
    "launch_stage", "launch_rs", "launch_tt", "launch_dist_high_pct", "breakout_date",
    "base_length", "base_depth_pct", "vcp_before_breakout", "bases_count", "giveback_pct",
    "giveback_of_gain_pct", "giveback_final",
)  # fmt: skip


def _study(cur: Cur) -> tuple[m.LeadersStudy, str] | None:
    """The newest stored study and its id, or None (none stored, or a serving copy from before
    the tables existed)."""
    try:
        row = cur.execute(
            "SELECT s.study_id, s.created_at, s.study_version, s.data_end, s.survivorship_status,"
            " s.survivorship_detail, s.vcp_source, s.summary_json,"
            " (SELECT count(*) FROM superperformance_runs r WHERE r.study_id = s.study_id)"
            " FROM superperformance_studies s ORDER BY s.created_at DESC LIMIT 1"
        ).fetchone()
    except duckdb.CatalogException:
        return None
    if row is None:
        return None
    try:
        lift_end = json.loads(row[7]).get("lift_end")
    except (ValueError, AttributeError):
        lift_end = None
    study = m.LeadersStudy(
        study_id=row[0], created_at=row[1], study_version=row[2], data_end=row[3],
        lift_end=date.fromisoformat(lift_end) if isinstance(lift_end, str) else None,
        survivorship_status=row[4], survivorship_detail=row[5], vcp_source=row[6],
        runs_total=int(row[8]),
    )  # fmt: skip
    return study, str(row[0])


def _row(r: tuple[Any, ...]) -> m.LeaderRow:
    return m.LeaderRow(**dict(zip(_COLS, r, strict=True)))


def leaders(
    cur: Cur, data_time: datetime, *, year: int | None = None, sector: str | None = None,
    band: str | None = None, vcp: bool | None = None,
) -> m.LeadersResponse:  # fmt: skip
    """Every run of the stored study, newest launch first, optionally filtered by launch year,
    sector (``Unclassified`` = none), gain band and whether a VCP was flagged before the first
    breakout."""
    found = _study(cur)
    if found is None:
        return m.LeadersResponse(as_of=None, data_time=data_time, study=None, years=[],
                                 sectors=[], runs=[])  # fmt: skip
    study, sid = found
    years = [int(r[0]) for r in cur.execute(
        "SELECT DISTINCT year(launch_date) FROM superperformance_runs WHERE study_id = ?"
        " ORDER BY 1", [sid]).fetchall()]  # fmt: skip
    sectors = [str(r[0]) for r in cur.execute(
        "SELECT DISTINCT coalesce(sector, ?) FROM superperformance_runs WHERE study_id = ?"
        " ORDER BY 1", [UNCLASSIFIED, sid]).fetchall()]  # fmt: skip
    where: list[str] = ["study_id = ?"]
    args: list[Any] = [sid]
    if year is not None:
        where.append("year(launch_date) = ?")
        args.append(year)
    if sector is not None:
        where.append("coalesce(sector, ?) = ?")
        args += [UNCLASSIFIED, sector]
    if band is not None:
        where.append("gain_band = ?")
        args.append(band)
    if vcp is not None:
        where.append("(coalesce(vcp_before_breakout, '') = 'VCP') = ?")
        args.append(vcp)
    rows = cur.execute(
        f"SELECT {', '.join(_COLS)} FROM superperformance_runs WHERE {' AND '.join(where)}"
        " ORDER BY launch_date DESC, symbol",
        args,
    ).fetchall()
    return m.LeadersResponse(
        as_of=study.data_end, data_time=data_time, study=study, years=years, sectors=sectors,
        runs=[_row(r) for r in rows],
    )  # fmt: skip


def leader_run(cur: Cur, run_id: int, data_time: datetime) -> m.LeaderRunResponse | None:
    """One run with its bases and the adjusted bars from ``BARS_BEFORE`` sessions before the
    launch to ``BARS_AFTER`` after the peak; None when the stored study has no such run."""
    found = _study(cur)
    if found is None:
        return None
    study, sid = found
    row = cur.execute(
        f"SELECT {', '.join(_COLS)}, instrument_id FROM superperformance_runs"
        " WHERE study_id = ? AND run_id = ?",
        [sid, run_id],
    ).fetchone()
    if row is None:
        return None
    run, iid = _row(row[:-1]), str(row[-1])
    bases = [
        m.LeaderBase(seq=r[0], base_start=r[1], breakout_date=r[2], base_high=r[3],
                     depth_pct=r[4], length=r[5])
        for r in cur.execute(
            "SELECT seq, base_start, breakout_date, base_high, depth_pct, length"
            " FROM superperformance_bases WHERE study_id = ? AND run_id = ? ORDER BY seq",
            [sid, run_id],
        ).fetchall()
    ]  # fmt: skip
    days = [r[0] for r in cur.execute(
        "SELECT trade_date FROM daily_prices_adjusted_current WHERE computed_from_snapshot_id = ?"
        " AND instrument_id = ? ORDER BY trade_date", [LIVE, iid]).fetchall()]  # fmt: skip
    window: list[m.Bar] = []
    if days:
        lo = next((k for k, d in enumerate(days) if d >= run.launch_date), 0)
        hi = next((k for k, d in enumerate(days) if d >= run.peak_date), len(days) - 1)
        a, b = max(0, lo - BARS_BEFORE), min(len(days) - 1, hi + BARS_AFTER)
        window = bars(cur, iid, b - a + 1, days[b])
    return m.LeaderRunResponse(
        as_of=study.data_end, data_time=data_time, study_id=sid, run=run, bases=bases,
        bars=window,
    )  # fmt: skip
