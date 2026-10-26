"""A small synthetic database for the dashboard API tests (FRONTEND_SPECIFICATION 67; D1).

Three named stocks (ALPHA, BETA, GAMMA) with setups in several strategies, 60 filler stocks that
make up the universe for the breadth series, NEWCO with only 60 bars (averages must be null, not
0), and a paper ledger with one open and one closed trade. Built with the real schema and the real
paper repository; the serving copy is made with ``refresh_serving_copy``.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from vcp_scanner.api.context import Context, build_context
from vcp_scanner.data.repositories.duckdb_fundamental_repository import (
    DuckDBFundamentalRepository,
)
from vcp_scanner.data.repositories.duckdb_paper_repository import DuckDBPaperRepository
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.features import FEATURES_CALCULATION_VERSION
from vcp_scanner.fundamentals.pipeline import update_views
from vcp_scanner.fundamentals.snapshots import Fact, SnapshotRow
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


def add_fundamentals(store: DuckDBStore) -> None:
    """Fundamentals (F5), through the real repository and view writer. ALPHA: five quarters
    Jun-25..Jun-26 (Mar-26 estimated, balance sheet on Mar-26) and a Sep-26 quarter published
    after AS_OF, which no response may show. BETA: one quarter. GAMMA: none."""
    repo = DuckDBFundamentalRepository(store)
    cr = 1e7  # one crore
    alpha = [  # period end, EPS, revenue (crore), status
        (date(2025, 6, 30), 2.0, 100.0, "OK"),
        (date(2025, 9, 30), 2.2, 110.0, "OK"),
        (date(2025, 12, 31), 2.5, 120.0, "OK"),
        (date(2026, 3, 31), 2.8, 130.0, "ESTIMATED"),
        (date(2026, 6, 30), 3.0, 125.0, "OK"),
        (date(2026, 9, 30), 9.9, 999.0, "OK"),  # published 20 Oct 2026, after AS_OF
    ]
    rows = [(iid("ALPHA"), *x) for x in alpha] + [(iid("BETA"), date(2026, 6, 30), 4.0, 80.0, "OK")]
    for inst, end, eps, revenue, status in rows:
        published = datetime.combine(end + timedelta(days=20), datetime.min.time(), UTC)
        published = published.replace(hour=12, minute=30)  # 18:00 IST, after the close
        sid = f"{inst}-{end}"
        items = {"revenue": revenue * cr, "eps": eps, "pbt": 15 * cr, "finance_costs": 2 * cr,
                 "depreciation": 3 * cr, "other_income": 0.0, "net_profit": eps * cr,
                 "profit_owners": eps * cr}  # fmt: skip
        facts = [Fact("QUARTER", k, v, status == "ESTIMATED", end - timedelta(days=90), end)
                 for k, v in items.items()]  # fmt: skip
        if end == date(2026, 3, 31) and inst == iid("ALPHA"):
            facts += [Fact("INSTANT", "equity", 400 * cr, False, None, end),
                      Fact("INSTANT", "borrowings", 100 * cr, False, None, end)]  # fmt: skip
        repo.add_snapshot(
            SnapshotRow(sid, inst, end, "QUARTER", "CONSOLIDATED", 0, published.date(),
                        published, "INR", status, f"F-{sid}"),
            facts,
        )  # fmt: skip
    update_views(repo, staleness_days=120, min_availability=0.5)


def add_shareholding(store: DuckDBStore) -> None:
    """Shareholding patterns (F8) for ALPHA, % of shares. Mar-25 is in the older SPLIT layout
    (pledge "pledged or encumbered"), the rest NEW. Dec-25 was not filed. Jun-26 was filed on
    15 Jul and revised on 1 Aug. A filing after an event (15 May 2026) is not a quarter end and
    a Sep-26 filing is broadcast after AS_OF; no response may show either. BETA has none."""
    ist_evening = timedelta(hours=12, minutes=30)  # 18:00 IST
    rows = [  # as on, broadcast day, revision, layout, promoter, pledged, FII, DII
        (date(2025, 3, 31), date(2025, 4, 18), 0, "SPLIT", 50.0, 2.0, 20.0, 10.0),
        (date(2025, 6, 30), date(2025, 7, 18), 0, "NEW", 50.0, 1.0, 21.0, 10.0),
        (date(2025, 9, 30), date(2025, 10, 17), 0, "NEW", 50.0, 1.0, 21.0, 11.0),
        (date(2026, 3, 31), date(2026, 4, 17), 0, "NEW", 49.0, 1.0, 22.0, 12.0),
        (date(2026, 5, 15), date(2026, 5, 18), 0, "NEW", 10.0, 0.0, 10.0, 10.0),
        (date(2026, 6, 30), date(2026, 7, 15), 0, "NEW", 48.0, 1.5, 23.0, 12.5),
        (date(2026, 6, 30), date(2026, 8, 1), 1, "NEW", 48.5, 3.0, 23.0, 12.5),
        (date(2026, 9, 30), date(2026, 10, 20), 0, "NEW", 99.0, 0.0, 0.0, 0.0),
    ]
    for as_on, broadcast, rev, layout, promoter, pledged, fii, dii in rows:
        at = datetime.combine(broadcast, datetime.min.time(), UTC) + ist_evening
        public = 100.0 - promoter
        put(store.conn, "shareholding_snapshots", filing_id=f"NSE_SHP:{as_on}-{rev}",
            instrument_id=iid("ALPHA"), as_on=as_on, is_quarter_end=as_on.month % 3 == 0
            and (as_on + timedelta(days=1)).day == 1, revision_number=rev, available_at=at,
            layout=layout, promoter_pct=promoter, promoter_pledged_pct=pledged, fii_pct=fii,
            fdi_pct=0.0, dii_pct=dii, mf_pct=dii / 2, insurance_pct=dii / 4, government_pct=0.0,
            non_institutions_pct=public - fii - dii, public_pct=public, other_pct=0.0,
            shareholders=100_000 + 1_000 * rev)  # fmt: skip


MHS_VERSION = "mhs-2.0.0+fixture"


def add_market_health(store: DuckDBStore) -> None:
    """Three stored Market Health Score days (the fixture's universe is too small to score):
    a healthy day, a day with a breadth thrust, and AS_OF with its breakout pillar missing and
    five distribution days in ten (capped at Mixed)."""
    from tests.unit.test_market_health import CONFIG, healthy
    from vcp_scanner.market_health.build import _row
    from vcp_scanner.market_health.features import FeatureDay
    from vcp_scanner.market_health.scoring import score_day
    from vcp_scanner.market_health.settings import load_settings

    s = load_settings(CONFIG)
    now = datetime(2026, 10, 5, 13, 0, tzinfo=UTC)
    days = [
        (date(2026, 9, 30), healthy()),
        (PREV, healthy(thrust=True)),
        (AS_OF, healthy(judged=3, dist_cluster=5)),
    ]
    store.conn.executemany(
        f"INSERT INTO market_health_daily VALUES ({', '.join('?' * 15)})",
        [_row(FeatureDay(d, f, 100.0 + k, 20000.0 + k), score_day(f, s), MHS_VERSION, now)
         for k, (d, f) in enumerate(days)],
    )  # fmt: skip


def add_superperformance(store: DuckDBStore) -> None:
    """A stored superperformance study with three synthetic runs (Past Leaders page): ALPHA
    doubled with a VCP and two bases, GAMMA tripled with no sector and no scan, BETA +50 to 99 %
    still open."""
    c = store.conn
    put(c, "superperformance_studies", study_id="sp-test", created_at=T0,
        study_version="superperformance-1.0.0", data_end=AS_OF, source="test",
        vcp_source="vcp-1.1.0: test scans", survivorship_status="PARTIAL",
        survivorship_detail="synthetic", params_json="{}",
        summary_json='{"lift_end": "2025-09-26"}')  # fmt: skip
    runs = [
        (1, "ALPHA", "Capital Goods", date(2024, 3, 1), 106.0, date(2024, 12, 2), 233.2, 120.0,
         "doubled", 197, True, "VCP", 2),
        (2, "GAMMA", None, date(2024, 6, 3), 111.0, date(2025, 3, 3), 366.3, 230.0, "tripled",
         190, True, None, 0),
        (3, "BETA", "Chemicals", date(2026, 4, 1), 160.0, date(2026, 9, 1), 264.0, 65.0,
         "50-100", 107, False, "NONE", 0),
    ]  # fmt: skip
    for rid, sym, sec, ld, lc, pd, pc, g, band, n, done, vcp, nb in runs:
        put(c, "superperformance_runs", study_id="sp-test", run_id=rid, instrument_id=iid(sym),
            symbol=sym, sector=sec, launch_date=ld, launch_close=lc, peak_date=pd,
            peak_close=pc, gain_pct=g, gain_band=band, sessions_to_peak=n, complete=done,
            to50=40, max_pullback_pct=18.0, pullbacks_10=2, bases_count=max(0, nb - 1),
            launch_stage="STAGE_4", launch_rs=35, launch_tt=False, launch_dist_high_pct=45.0,
            vcp_before_breakout=vcp, giveback_pct=None if not done else 30.0,
            giveback_of_gain_pct=None if not done else 60.0, giveback_final=done,
            sector_peers=1)  # fmt: skip
    for seq, (start, day, high) in enumerate(
        [(date(2024, 2, 1), date(2024, 4, 1), 115.0), (date(2024, 6, 3), date(2024, 7, 1), 160.0)],
        start=1,
    ):
        put(c, "superperformance_bases", study_id="sp-test", run_id=1, seq=seq, base_start=start,
            breakout_date=day, base_high=high, depth_pct=8.0, length=20)  # fmt: skip


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
        # the day before: GONE was in and is not listed now; the last member was out for its price
        put(c, "universe_snapshots", universe_snapshot_id="U0", universe_name="u",
            as_of_date=PREV)  # fmt: skip
        put(c, "universe_memberships", universe_snapshot_id="U0", instrument_id="GONE",
            eligible=True)  # fmt: skip
        put(c, "universe_memberships", universe_snapshot_id="U0", instrument_id=iid(members[-1]),
            eligible=False, exclusion_reason="Price 19.8 < 20.0")  # fmt: skip
        for sym in members[:-1]:
            put(c, "universe_memberships", universe_snapshot_id="U0", instrument_id=iid(sym),
                eligible=True)  # fmt: skip
        for sym in members:
            put(c, "universe_memberships", universe_snapshot_id="U1", instrument_id=iid(sym),
                eligible=True)  # fmt: skip
        for d in (date(2026, 4, 1), PREV, AS_OF):
            put(c, "scan_runs", scan_run_id=f"tt-{d}", scan_type="TREND_TEMPLATE", as_of_date=d,
                scan_id=f"tt-{d}", data_snapshot_id="LIVE", universe_snapshot_id="U1",
                scan_config_hash=ctx.scan_hash, status="COMPLETED", completed_at=T0)  # fmt: skip
        # Sector classification (S1): ALPHA from NSE, BETA entered by hand, GAMMA not yet.
        put(c, "instrument_classification", instrument_id=iid("ALPHA"),
            macro_sector="Industrials", sector="Capital Goods", industry="Electrical Equipment",
            basic_industry="Heavy Electrical Equipment", classification_source="NSE_QUOTE",
            valid_from=AS_OF, fetched_at=T0)  # fmt: skip
        put(
            c,
            "instrument_classification",
            instrument_id=iid("BETA"),
            sector="Healthcare",
            classification_source="MANUAL",
            valid_from=AS_OF,
            fetched_at=T0,
        )
        for sym, rank in (("ALPHA", 91), ("BETA", 85), ("GAMMA", 78)):
            put(c, "trend_template_results", scan_id=f"tt-{AS_OF}", instrument_id=iid(sym),
                as_of_date=AS_OF, status="PASS", trend_template_pass=True,
                weekly_stage="STAGE_2", weekly_stage2_pass=True, rs_rank=rank,
                calculation_version="1", config_hash=ctx.scan_hash,
                data_snapshot_id="LIVE")  # fmt: skip
        put(c, "trend_template_results", scan_id=f"tt-{AS_OF}", instrument_id=iid("F00"),
            as_of_date=AS_OF, status="PASS", trend_template_pass=False,
            weekly_stage="STAGE_4", weekly_stage2_pass=False, rs_rank=20,
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
        add_fundamentals(store)
        add_shareholding(store)
        add_market_health(store)
        add_superperformance(store)
        # NSE index closes (Market Overview step 3) for the last 30 sessions only: the NIFTY
        # lines start at 100 on their first stored day, later than the chart's first day.
        for k, d in enumerate(SESSIONS[-30:]):
            for name, close in (("Nifty 50", 20000.0 + 10 * k), ("Nifty 500", 18000.0 + 5 * k),
                                ("Nifty Capital Goods", 1000.0 + k)):  # fmt: skip
                put(c, "index_daily", index_name=name, trade_date=d, close=close)
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


def build_research_db(path: Path) -> Path:
    """A research database with three stored backtest runs: the frozen paper rules in the
    development and validation periods, and one experiment (breakout entry, no regime) whose
    metrics are mostly missing (a missing number must come out as null)."""
    import json

    paper = {"entry": "cross_5", "regime": "breadth50", "rule": "hold_low8", "max_positions": 10,
             "cost_bps": 15.0, "baseline": False, "classes": ["VCP", "A_PLUS_VCP"],
             "min_score": None}  # fmt: skip
    metrics = {
        "every_trade": {"trades": 20, "win_rate": 0.45, "avg_ret": 1.5, "median_ret": -2.0,
                        "profit_factor": 1.6, "avg_win": 9.0, "avg_loss": -5.0,
                        "avg_hold_days": 40.0},
        "portfolio": {"trades": 20, "win_rate": 0.45, "avg_ret": 1.5, "median_ret": -2.0,
                      "profit_factor": 1.6, "avg_win": 9.0, "avg_loss": -5.0, "avg_hold_days": 40.0,
                      "total_ret": 30.0, "cagr": 12.0, "max_drawdown": -11.0, "sharpe": 0.8,
                      "avg_exposure": 0.5, "skipped": 2},
    }  # fmt: skip
    runs = [
        ("bt-dev", "vcp", "development", date(2022, 2, 1), date(2024, 6, 30), paper, metrics),
        ("bt-val", "vcp", "validation", date(2024, 7, 1), date(2026, 9, 30), paper, metrics),
        ("bt-exp", "flat_base", "development", date(2022, 2, 1), date(2024, 6, 30),
         {**paper, "entry": "breakout", "regime": "none"}, {"every_trade": {}, "portfolio": {}}),
    ]  # fmt: skip
    with DuckDBStore(str(path)) as store:
        store.migrate()
        for k, (bid, sid, period, start, end, settings, met) in enumerate(runs):
            put(store.conn, "backtest_runs", backtest_id=bid, strategy_id=sid,
                started_at=T0 + timedelta(minutes=k),
                completed_at=T0 + timedelta(minutes=k, seconds=5),
                start_date=start, end_date=end, status="COMPLETED", period_name=period,
                algorithm_version=f"{sid}-1.0.0", survivorship_status="PARTIAL",
                settings_json=json.dumps(settings), metrics_json=json.dumps(met))  # fmt: skip
    return path


def write_report_files(folder: Path) -> None:
    for kind, name in (("daily", "2026-10-05.html"), ("weekly", "2026-W41.html")):
        (folder / kind).mkdir(parents=True, exist_ok=True)
        (folder / kind / name).write_text(f"<html><body><h1>{kind} {name}</h1></body></html>")
    (folder / "daily" / "notes.html").write_text("not a report name")
    for p in folder.rglob("*.html"):
        os.utime(p, (1_790_000_000, 1_790_000_000))  # a fixed time: the API sample must not change


def write_agent_db(data_dir: Path) -> Path:
    """The agent's own file with fixed rows (fixed times: the API sample must not change): one
    pending and one applied proposal, an AI watchlist entry, a concluded hypothesis with a run,
    and two notebook entries."""
    from json import dumps as _json_dumps
    from pathlib import Path as _P

    from vcp_scanner.agent.lab import Lab
    from vcp_scanner.agent.proposals import Proposals
    from vcp_scanner.agent.store import AgentStore

    path = data_dir / "agent" / "agent.duckdb"
    store = AgentStore(path)
    store.migrate()
    Lab(store).migrate()
    Proposals(store, _P("."), "config").migrate()
    import duckdb as _duckdb

    con = _duckdb.connect(str(path))
    try:
        con.execute(
            "INSERT INTO agent_proposals VALUES (1, ?, 'corporate_action', 'ALPHA SPLIT ex"
            " 2026-09-01', ?, 'Shows as a 5x jump', 'Notice https://example.org/a; 500 -> 101',"
            " 'PENDING', NULL, NULL, NULL, NULL, NULL)",
            [T0, '{"symbol": "ALPHA", "action_type": "SPLIT", "ex_date": "2026-09-01",'
                 ' "ratio": [10, 2]}'],
        )  # fmt: skip
        con.execute(
            "INSERT INTO agent_proposals VALUES (2, ?, 'watchlist', 'AI watchlist +BETA',"
            " '{\"add\": [\"BETA\"], \"remove\": []}', 'Near its pivot', 'Near its pivot',"
            " 'APPLIED', 'pre-approved', ?, NULL, ?, 'AI watchlist: added 1, removed 0')",
            [T0, T0, T0],
        )
        con.execute("INSERT INTO agent_watchlist VALUES ('BETA', ?, 2, 'AI watchlist +BETA')", [T0])
        con.execute(
            "INSERT INTO agent_hypotheses VALUES (1, ?, '2027-04', 'vcp', 'Pattern-heavier score',"
            " 'raise the pattern weight', 'IC at 60 sessions', 'IC +0.03 with t >= 2',"
            " 'CONCLUDED', 'rejected', 'IC -0.003 (t -0.2)', ?)",
            [T0, T0],
        )
        con.execute(
            "INSERT INTO agent_runs VALUES (1, 1, 'what_if', 'development', DATE '2022-02-01',"
            " DATE '2024-06-30', '{}', ?, ?, TRUE, 'what-if vcp: IC -0.002 -> -0.003', NULL, NULL)",
            [T0, T0],
        )
        # A rejected strategy candidate (step A4) with two scan-diff rows.
        cand = {"candidate_id": "flat_base-c1", "strategy_id": "flat_base",
                "base_version": "flat_base-1.0.0", "candidate_version": "flat_base-1.0.0+c1",
                "hypothesis_id": 1, "overrides": [{"file": "strategies/flat_base.yaml",
                "path": "classification.flat_base.max_depth_pct", "old": 15, "new": 12}],
                "spec": {"old": "15 %", "new": "12 %", "why": "shallower bases",
                         "evidence": "run 1"}}  # fmt: skip
        diff = {"days": 2, "first": "2026-10-01", "last": "2026-10-05", "frozen_eligible": 5,
                "changes": {"ADDED": 0, "DROPPED": 1, "RECLASSIFIED": 1, "RESCORED": 0},
                "stocks": 2}  # fmt: skip
        con.execute(
            "INSERT INTO agent_proposals VALUES (3, ?, 'candidate', 'Candidate flat_base:"
            " classification.flat_base.max_depth_pct=12', ?, 'Shallower bases held better',"
            " 'run 1 (development)', 'REJECTED', 'owner', ?, 'wait for more paper trades', NULL,"
            " 'built and checked')",
            [T0, _json_dumps({**cand, "scan_diff": diff}), T0],
        )  # fmt: skip
        from vcp_scanner.agent.candidate_build import migrate as _cand_migrate

        con.close()
        _cand_migrate(path)
        con = _duckdb.connect(str(path))
        con.execute(
            "INSERT INTO strategy_candidates VALUES ('flat_base-c1', 3, 'flat_base',"
            " 'flat_base-1.0.0', 'flat_base-1.0.0+c1', 'rejected', 'candidate/flat_base-c1',"
            " 'abc123', 'fh', 'ch', NULL, ?, ?)",
            [_json_dumps(diff), T0],
        )
        con.execute(
            "INSERT INTO candidate_scan_diff VALUES ('flat_base-c1', DATE '2026-10-05', 'X|ALPHA',"
            " 'ALPHA', 'DROPPED', 'FLAT_BASE', NULL, 70.0, NULL), ('flat_base-c1',"
            " DATE '2026-10-01', 'X|BETA', 'BETA', 'RECLASSIFIED', 'TIGHT_FLAT_BASE',"
            " 'FLAT_BASE', 60.0, 58.5)"
        )
        con.execute(
            "INSERT INTO agent_notes VALUES (1, ?, 'decision', 'Strategies frozen until April"
            " 2027.', NULL, 'seed'), (2, ?, 'finding', 'H1 concluded: rejected.', 'h1', 'agent')",
            [T0, T0],
        )
    finally:
        con.close()
    os.utime(path, (1_790_000_000, 1_790_000_000))
    return path


def write_alert_files(data_dir: Path) -> None:
    """The intraday watcher's state (checked a minute before the test clock) and two logged
    alerts, one sent and one not (fixed times: the API sample must not change)."""
    import json as _json

    from vcp_scanner.alerts.telegram import AlertLog

    folder = data_dir / "alerts"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "watch_state.json").write_text(_json.dumps({
        "updated_at": "2026-10-06T13:59:00+05:30", "market": "open", "error": None,
        "watching": [
            {"symbol": "ALPHA", "pivot": 100.0, "strategy": "vcp", "why": "setup",
             "last_price": 99.5, "gap_pct": -0.5},
            {"symbol": "BETA", "pivot": 50.0, "strategy": "flat_base", "why": "AI watchlist",
             "last_price": None, "gap_pct": None}]}))  # fmt: skip
    log = AlertLog(folder / "alerts.duckdb")
    log.record(AS_OF, "EVENING", None, "VCP scanner - evening 2026-10-05", True, None)
    log.record(AS_OF, "APPROACHING", "ALPHA", "ALPHA is 0.5% below its pivot", False,
               "Telegram HTTP 401")  # fmt: skip
    import duckdb as _duckdb

    con = _duckdb.connect(str(folder / "alerts.duckdb"))
    try:  # fixed send times
        con.execute("UPDATE alerts_sent SET sent_at = ? WHERE kind = 'EVENING'", [T0])
        con.execute("UPDATE alerts_sent SET sent_at = ? WHERE kind = 'APPROACHING'",
                    [T0 + timedelta(minutes=5)])  # fmt: skip
    finally:
        con.close()
