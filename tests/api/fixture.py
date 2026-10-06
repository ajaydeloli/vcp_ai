"""A small synthetic database for the dashboard API tests (FRONTEND_SPECIFICATION 67; D1).

Three named stocks (ALPHA, BETA, GAMMA) with setups in several strategies, 60 filler stocks that
make up the universe for the breadth series, NEWCO with only 60 bars (averages must be null, not
0), and a paper ledger with one open and one closed trade. Built with the real schema and the real
paper repository; the serving copy is made with ``refresh_serving_copy``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from vcp_scanner.api.context import Context, build_context
from vcp_scanner.data.repositories.duckdb_paper_repository import DuckDBPaperRepository
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.features import FEATURES_CALCULATION_VERSION
from vcp_scanner.paper.ledger import RULE_SET, PaperEvent
from vcp_scanner.serving import refresh_serving_copy

CONFIG_DIR = str(Path(__file__).resolve().parents[2] / "config")
AS_OF = date(2026, 10, 5)  # Monday
PREV = date(2026, 10, 1)  # the previous session (2 Oct is a holiday)
HOLIDAY = date(2026, 10, 2)
_START = date(2024, 1, 1)
SESSIONS = [
    d for d in (_START + timedelta(days=k) for k in range((AS_OF - _START).days + 1))
    if d.weekday() < 5 and d != HOLIDAY
]  # fmt: skip
NAMED = {"ALPHA": "Alpha Industries Ltd", "BETA": "Beta Ltd", "GAMMA": None}
FILLERS = 60
T0 = datetime(2026, 10, 5, 15, tzinfo=UTC)

_DEFAULTS: dict[str, Any] = {
    "VARCHAR": "", "DOUBLE": 0.0, "INTEGER": 0, "BIGINT": 0, "BOOLEAN": False,
    "DATE": date(2026, 1, 1), "TIMESTAMP WITH TIME ZONE": T0, "DECIMAL(18,8)": 1,
}  # fmt: skip


def put(conn: Any, table: str, **row: Any) -> None:
    """Insert one row; NOT NULL columns without a default that the test does not care about get
    a neutral value of their type."""
    cols = conn.execute(
        "SELECT column_name, data_type, is_nullable, column_default FROM"
        " information_schema.columns WHERE table_name = ? ORDER BY ordinal_position",
        [table],
    ).fetchall()
    values = {}
    for name, typ, nullable, default in cols:
        if name in row:
            values[name] = row[name]
        elif nullable == "NO" and default is None:
            values[name] = _DEFAULTS[typ]
    unknown = set(row) - {c[0] for c in cols}
    assert not unknown, f"{table} has no columns {unknown}"
    conn.execute(
        f"INSERT INTO {table} ({', '.join(values)}) VALUES ({', '.join('?' * len(values))})",
        list(values.values()),
    )


def iid(symbol: str) -> str:
    return f"NSE_EQ|{symbol}"


def build_main_db(db: Path, ctx: Context) -> None:
    spec = {s.strategy_id: s for s in ctx.strategies}
    with DuckDBStore(str(db)) as store:
        store.migrate()
        c = store.conn
        members = [f"F{k:02d}" for k in range(FILLERS)] + list(NAMED)
        for sym in [*members, "NEWCO"]:
            put(c, "instruments", instrument_id=iid(sym), symbol=sym,
                company_name=NAMED.get(sym, f"{sym} Ltd" if sym == "NEWCO" else None))  # fmt: skip
        for d, status in [(x, "OK") for x in SESSIONS[-30:]] + [(HOLIDAY, "NO_SESSION")]:
            put(c, "bhavcopy_files", trade_date=d, sha256=d.isoformat(), status=status,
                url="u", file_format="CM_UDIFF", recorded_at=T0)  # fmt: skip
        # --- prices: named stocks rise 0.1 a session from 100, fillers 0.05 from 50 ---------
        c.execute("CREATE TEMP TABLE sess (d DATE, n INTEGER)")
        c.executemany(
            "INSERT INTO sess VALUES (?, ?)", list(zip(SESSIONS, range(len(SESSIONS)), strict=True))
        )
        c.execute("CREATE TEMP TABLE inst (id VARCHAR, base DOUBLE, slope DOUBLE, first_n INT,"
                  " idx INT)")  # fmt: skip
        rows = [(iid(s), 50.0, 0.05, 0, k) for k, s in enumerate(members[:FILLERS])]
        rows += [(iid(s), 100.0, 0.1, 0, FILLERS + k) for k, s in enumerate(NAMED)]
        rows.append((iid("NEWCO"), 100.0, 0.1, len(SESSIONS) - 60, -1))
        c.executemany("INSERT INTO inst VALUES (?, ?, ?, ?, ?)", rows)
        c.execute(
            """
            INSERT INTO daily_prices_adjusted (instrument_id, trade_date, open_adj, high_adj,
                low_adj, close_adj, volume_adj, adjustment_version, price_factor_applied,
                volume_factor_applied, computed_from_snapshot_id, computed_at)
            SELECT i.id, s.d, i.base + i.slope * s.n, i.base + i.slope * s.n + 1,
                   i.base + i.slope * s.n - 1, i.base + i.slope * s.n, 100000, 'v1', 1, 1,
                   'LIVE', now()
            FROM inst i JOIN sess s ON s.n >= i.first_n
            """
        )
        # --- features for the breadth series: 45 of the 63 members above their 50-day average,
        # on the last two sessions only 20 -------------------------------------------------
        c.execute(
            """
            INSERT INTO technical_features_daily (instrument_id, trade_date, sma_50,
                daily_return, calculation_version, data_snapshot_id)
            SELECT p.instrument_id, p.trade_date,
                   p.close_adj * CASE WHEN i.idx < (CASE WHEN p.trade_date >= DATE '2026-10-01'
                                      THEN 20 ELSE 45 END) THEN 0.9 ELSE 1.1 END,
                   0.001, ?, 'LIVE'
            FROM daily_prices_adjusted p JOIN inst i ON i.id = p.instrument_id
            WHERE i.idx >= 0 AND p.trade_date > ?
            """,
            [FEATURES_CALCULATION_VERSION, SESSIONS[-130]],
        )
        # --- universe and Trend Template scans ---------------------------------------------
        put(c, "universe_snapshots", universe_snapshot_id="U1", universe_name="u",
            as_of_date=AS_OF)  # fmt: skip
        for sym in members:
            put(c, "universe_memberships", universe_snapshot_id="U1", instrument_id=iid(sym),
                eligible=True)  # fmt: skip
        for d in (date(2026, 4, 1), PREV, AS_OF):
            put(c, "scan_runs", scan_run_id=f"tt-{d}", scan_type="TREND_TEMPLATE", as_of_date=d,
                scan_id=f"tt-{d}", data_snapshot_id="LIVE", universe_snapshot_id="U1",
                scan_config_hash=ctx.scan_hash, status="COMPLETED", completed_at=T0)  # fmt: skip
        for sym, rank in (("ALPHA", 91), ("BETA", 85), ("GAMMA", 78)):
            put(c, "trend_template_results", scan_id=f"tt-{AS_OF}", instrument_id=iid(sym),
                as_of_date=AS_OF, status="PASS", trend_template_pass=True,
                weekly_stage="STAGE_2", weekly_stage2_pass=True, rs_rank=rank,
                calculation_version="1", config_hash=ctx.scan_hash,
                data_snapshot_id="LIVE")  # fmt: skip
        for k, (name, ok) in enumerate((("close_above_sma150", True), ("sma200_rising", True),
                                        ("rs_rank_min", True)), 1):  # fmt: skip
            put(c, "trend_template_conditions", instrument_id=iid("ALPHA"), as_of_date=AS_OF,
                condition_id=k, condition_name=name, measurement=91.0 + k, threshold=70.0,
                passed=ok, calculation_version="1", config_hash=ctx.scan_hash,
                data_snapshot_id="LIVE")  # fmt: skip
        # --- VCP setups ---------------------------------------------------------------------
        vh = spec["vcp"].config_hash
        for sym, pid, cls, status, pivot, brk in (
            ("ALPHA", "vp-alpha", "A_PLUS_VCP", "PIVOT_READY", 120.0, None),
            ("BETA", "vp-beta", "VCP", "BREAKOUT", 140.0, "be-beta"),
            ("GAMMA", "vp-gamma", "VCP_LIKE", "FORMING", None, None),
        ):
            put(c, "vcp_patterns", vcp_pattern_id=pid, scan_id=f"vcp-{AS_OF}",
                instrument_id=iid(sym), as_of_date=AS_OF, is_primary=True,
                base_start_date=date(2026, 8, 1), base_depth_pct=18.8, base_duration_days=50,
                pivot_price=pivot, pivot_distance_pct=None if pivot is None else 1.2,
                classification=cls, status=status, confirmation_state="CONFIRMED",
                breakout_event_id=brk, config_hash=vh, data_snapshot_id="LIVE",
                unmet_rules="{}")  # fmt: skip
        for seq, (pk, tr, depth) in enumerate(((130.0, 118.0, 9.2), (128.0, 122.0, 4.7)), 1):
            put(c, "vcp_contractions", vcp_pattern_id="vp-alpha", sequence_number=seq,
                peak_date=date(2026, 8, 10 + seq), peak_price=pk,
                trough_date=date(2026, 8, 20 + seq), trough_price=tr, depth_pct=depth)  # fmt: skip
        put(c, "vcp_breakout_events", breakout_event_id="be-beta", instrument_id=iid("BETA"),
            base_start_date=date(2026, 8, 1), config_hash=vh, breakout_date=AS_OF,
            pivot_price=140.0, volume_ratio=1.8, detected_as_of=AS_OF)  # fmt: skip
        for sym, cls, elig, score in (("ALPHA", "A_PLUS_VCP", True, 91.0),
                                      ("BETA", "VCP", True, 86.0),
                                      ("GAMMA", "VCP_LIKE", False, 40.0)):  # fmt: skip
            put(c, "setup_scores", scan_id=f"score-vcp-{AS_OF}", strategy_id="vcp",
                instrument_id=iid(sym), as_of_date=AS_OF, classification=cls,
                vcp_status="FORMING", eligible=elig, final_setup_score=score,
                trend_score=80.0, vcp_score=70.0, volume_score=60.0, rs_score=90.0,
                ranking_percentile=95.0 if elig else None, scoring_version="s1",
                config_hash=vh, data_snapshot_id="LIVE")  # fmt: skip
        for comp, sub, raw, pts in (("TREND", "high_proximity", 3.2, 33.0),
                                    ("VCP", "tightening", 0.68, 17.0)):  # fmt: skip
            put(c, "score_components", scan_id=f"score-vcp-{AS_OF}", strategy_id="vcp",
                instrument_id=iid("ALPHA"), component=comp, sub_component=sub,
                raw_measurement=raw, normalized_0_100=80.0, weight_within_component=0.5,
                points=pts, max_points=40.0, scoring_version="s1")  # fmt: skip

        # --- file strategies ------------------------------------------------------------------
        def strat(sid: str, sym: str, cls: str, grade: int, elig: bool, score: float,
                  details: str = "{}") -> None:  # fmt: skip
            h = spec[sid].config_hash
            put(c, "strategy_setups", setup_id=f"st-{sid}-{sym}", strategy_id=sid,
                scan_id=f"setup-{sid}-{AS_OF}", instrument_id=iid(sym), as_of_date=AS_OF,
                is_primary=True, base_start_date=date(2026, 8, 3), base_high=125.0,
                base_low=110.0, base_depth_pct=12.0, base_duration_days=40,
                pivot_price=126.0, stop_reference_price=108.0, classification=cls, grade=grade,
                status="FORMING", confirmation_state="CONFIRMED", trend_gate="PASS",
                details_json=details, algorithm_version=spec[sid].algorithm_version,
                config_hash=h, data_snapshot_id="LIVE")  # fmt: skip
            put(c, "setup_scores", scan_id=f"score-{sid}-{AS_OF}", strategy_id=sid,
                instrument_id=iid(sym), as_of_date=AS_OF, classification=cls,
                vcp_status="FORMING", eligible=elig, final_setup_score=score,
                scoring_version="s1", config_hash=h, data_snapshot_id="LIVE")  # fmt: skip

        strat("flat_base", "ALPHA", "FLAT_BASE", 2, True, 70.0)
        strat("flat_base", "GAMMA", "TIGHT_FLAT_BASE", 3, True, 80.0)
        strat("cup_handle", "ALPHA", "CUP_HANDLE", 2, True, 60.0,
              '{"left_lip_date": "2026-08-03", "bottom_date": "2026-08-20"}')  # fmt: skip
        strat("three_weeks_tight", "GAMMA", "NONE", 0, False, 10.0)
        strat("double_bottom", "GAMMA", "NONE", 0, False, 10.0)
        # --- paper ledger -----------------------------------------------------------------------
        repo = DuckDBPaperRepository(store)
        for sid in spec:
            ev = [PaperEvent(sid, None, d, "DAY_CLOSED", None, None, {"regime_on": d == PREV})
                  for d in (PREV, AS_OF)]  # fmt: skip
            if sid == "vcp":
                ev += [
                    PaperEvent(sid, iid("BETA"), PREV, "ENTRY", 100.0, PREV,
                               {"stop": 92.0, "score": 86.0, "classification": "VCP"}),
                    PaperEvent(sid, iid("GAMMA"), PREV, "ENTRY", 50.0, PREV, {"stop": 46.0}),
                    PaperEvent(sid, iid("GAMMA"), AS_OF, "EXIT", 47.5, PREV,
                               {"kind": "STOP", "ret_pct": -5.15, "entry_day": PREV.isoformat()}),
                ]  # fmt: skip
            repo.append(RULE_SET, spec[sid].config_hash, ev, "abc123")
    # --- logs and backups of the main database's folder ----------------------------------------
    logs = db.parent / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "daily_runs.log").write_text(
        "2026-10-01 19:15 IST | prices to 2026-10-01 | scanned 2026-10-01 | all steps OK\n"
        "2026-10-05 19:15 IST | prices to 2026-10-05 | scanned 2026-10-05 | "
        "surveillance lists collected: ASM, GSM, T2T | all steps OK\n",
        encoding="utf-8",
    )
    backups = db.parent / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    (backups / "vcp_scanner_20261005_134501.duckdb").write_bytes(b"")


def build_all(data_dir: Path) -> tuple[Context, Path]:
    """The main database and its serving copy under ``data_dir``; returns the context and the
    main database path."""
    data_dir.mkdir(parents=True, exist_ok=True)
    ctx = build_context(CONFIG_DIR, data_dir)
    db = data_dir / "vcp_scanner.duckdb"
    build_main_db(db, ctx)
    refresh_serving_copy(db)
    return ctx, db
