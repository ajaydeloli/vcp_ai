"""Multi-Strategy step 2: the strategy dimension in storage (DATABASE_SCHEMA 35A).

* the migration of a database from before the strategy dimension (every row becomes VCP's);
* the ``setups`` view over ``vcp_patterns`` (decision O2);
* labels and backtest signals read through the view return exactly what the old scan-id
  slicing joins returned (STRATEGY_SPECIFICATION 11.3 item 6), and another strategy's rows on
  the same stock, date and even config hash never leak into VCP's (multi-label, section 7).
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from tests.unit._legacy_ddl import (
    OLD_DDL_BACKTEST_RUNS,
    OLD_DDL_FORWARD_LABELS,
    OLD_DDL_SCAN_RUNS,
    OLD_DDL_SCORE_COMPONENTS,
    OLD_DDL_SETUP_SCORES,
)
from vcp_scanner.data.repositories.duckdb_backtest_repository import DuckDBBacktestRepository
from vcp_scanner.data.repositories.duckdb_label_repository import DuckDBLabelRepository
from vcp_scanner.data.storage.duckdb_store import _DDL_VCP_PATTERNS, DuckDBStore

H = "64da9482a76952d8dbacf395573c6e5e657bc4a7bf6729396a2ad37a33da5801"
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
D1, D2 = date(2026, 9, 30), date(2026, 10, 1)


def _pattern(conn: object, pid: str, iid: str, d: date, cls: str, pivot: float | None,
             primary: bool = True, scan_hash: str = H) -> None:  # fmt: skip
    conn.execute(  # type: ignore[attr-defined]
        """INSERT INTO vcp_patterns (vcp_pattern_id, scan_id, instrument_id, as_of_date,
               is_primary, base_start_date, base_high, base_low, base_depth_pct,
               base_duration_days, contraction_count, pivot_price, classification, status,
               confirmation_state, trend_template_pass, algorithm_version, config_hash,
               data_snapshot_id, created_at, final_volume_ratio)
           VALUES (?, ?, ?, ?, ?, DATE '2026-06-01', 110, 90, 18.2, 80, 2, ?, ?, 'FORMING',
                   'CONFIRMED', ?, 'vcp-1.1.0', ?, 'LIVE', ?, 0.55)""",
        [pid, f"vcp-{d}-{scan_hash[:12]}", iid, d, primary, pivot, cls, cls != "NONE",
         scan_hash, NOW],
    )  # fmt: skip


def _contraction(conn: object, pid: str, seq: int, trough: float) -> None:
    conn.execute(  # type: ignore[attr-defined]
        "INSERT INTO vcp_contractions (vcp_pattern_id, sequence_number, peak_date, peak_price,"
        " trough_date, trough_price, depth_pct, duration_days, is_confirmed)"
        " VALUES (?, ?, DATE '2026-07-01', 110, DATE '2026-07-10', ?, 10, 7, true)",
        [pid, seq, trough],
    )


def _score(conn: object, strategy: str, iid: str, d: date, cls: str | None, eligible: bool,
           score: float | None, scan_hash: str = H) -> None:  # fmt: skip
    prefix = "score-" if strategy == "vcp" else f"score-{strategy}-"
    conn.execute(  # type: ignore[attr-defined]
        """INSERT INTO setup_scores (scan_id, strategy_id, instrument_id, as_of_date,
               classification, vcp_status, eligible, final_setup_score, fundamental_available,
               weights_renormalized, trend_weight, vcp_weight, volume_weight, rs_weight,
               fundamental_weight, scoring_version, config_hash, data_snapshot_id, created_at)
           VALUES (?, ?, ?, ?, ?, 'FORMING', ?, ?, false, true, 27.8, 38.9, 16.7, 16.7, 0,
                   'scoring-1.0.0', ?, 'LIVE', ?)""",
        [f"{prefix}{d}-{scan_hash[:12]}", strategy, iid, d, cls, eligible, score, scan_hash,
         NOW],
    )  # fmt: skip


@pytest.fixture
def store() -> DuckDBStore:
    s = DuckDBStore(":memory:")
    s.migrate()
    c = s.conn
    # VCP: A (pivot, two contractions), B (no pivot), C (a non-primary and a primary pattern).
    for d in (D1, D2):
        _pattern(c, f"vpA{d}", "A", d, "VCP", 120.0)
        _contraction(c, f"vpA{d}", 1, 95.0)
        _contraction(c, f"vpA{d}", 2, 101.5)
        _pattern(c, f"vpB{d}", "B", d, "VCP_LIKE", None)
        _pattern(c, f"vpC0{d}", "C", d, "VCP_LIKE", 50.0, primary=False)
        _pattern(c, f"vpC{d}", "C", d, "NONE", 55.0)
        _contraction(c, f"vpC{d}", 1, 48.0)
        _score(c, "vcp", "A", d, "VCP", True, 71.0)
        _score(c, "vcp", "B", d, "VCP_LIKE", True, 60.0)
        _score(c, "vcp", "C", d, "NONE", False, 40.0)
        _score(c, "vcp", "D", d, None, False, 35.0)  # passer without a pattern
    # Another strategy on stock A, same dates, same config hash (worst case for leaks).
    c.execute(
        """INSERT INTO strategy_setups VALUES ('stA', 'flat_base',
               'setup-flat_base-2026-10-01-64da9482a769', 'A', DATE '2026-10-01', true,
               DATE '2026-08-01', NULL, 118, 104, 11.9, 40, NULL, 999.0, DATE '2026-09-20',
               'BASE_HIGH', 2.0, 103.0, 0.6, 'FLAT_BASE', 2, 'PIVOT_READY', 'CONFIRMED', 'PASS',
               true, NULL, NULL, NULL, '{}', 'flat_base-1.0.0', ?, 'LIVE', ?)""",
        [H, NOW],
    )
    _score(c, "flat_base", "A", D2, "FLAT_BASE", True, 99.0)
    return s


# -- migration ---------------------------------------------------------------------------------


def test_migration_gives_every_existing_row_to_vcp() -> None:
    s = DuckDBStore(":memory:")
    c = s.conn
    for ddl in (OLD_DDL_SCAN_RUNS, OLD_DDL_SETUP_SCORES, OLD_DDL_SCORE_COMPONENTS,
                OLD_DDL_FORWARD_LABELS, OLD_DDL_BACKTEST_RUNS, _DDL_VCP_PATTERNS):  # fmt: skip
        c.execute(ddl)
    _pattern(c, "vp1", "A", D1, "VCP", 120.0)
    c.execute("INSERT INTO setup_scores (scan_id, instrument_id, as_of_date, eligible,"
              " fundamental_available, weights_renormalized, trend_weight, vcp_weight,"
              " volume_weight, rs_weight, fundamental_weight, scoring_version, config_hash,"
              " data_snapshot_id, created_at, vcp_score) VALUES ('score-x', 'A', ?, true, false,"
              " true, 25, 35, 15, 15, 10, 'scoring-1.0.0', ?, 'LIVE', ?, 77.5)",
              [D1, H, NOW])  # fmt: skip
    c.execute("INSERT INTO score_components VALUES ('score-x', 'A', 'VCP', 'pivot', 3.0, 83.3,"
              " 10, 8.33, 10, 'scoring-1.0.0')")  # fmt: skip
    c.execute("INSERT INTO forward_labels (instrument_id, as_of_date, config_hash, label_version,"
              " data_snapshot_id, score_scan_id, entry_close, ret_5, bars_after, complete,"
              " computed_at) VALUES ('A', ?, ?, 'labels-1.0.0', 'LIVE', 'score-x', 100, 1.5, 5,"
              " false, ?)", [D1, H, NOW])  # fmt: skip
    c.execute("INSERT INTO backtest_runs (backtest_id, started_at, start_date, end_date,"
              " universe_definition, strategy_version, config_hash, data_snapshot_id,"
              " execution_model, research_mode, status, settings_json) VALUES ('bt-1', ?, ?, ?,"
              " 'u', 'strategy-1.0.0', ?, 'LIVE', 'close', true, 'COMPLETED', '{}')",
              [NOW, D1, D2, H])  # fmt: skip
    for sid, kind in (("r1", "TREND_TEMPLATE"), ("r2", "VCP"), ("r3", "SCORE")):
        c.execute("INSERT INTO scan_runs VALUES (?, ?, ?, 'x', 'LIVE', ?, 'u', NULL, ?, '{}',"
                  " 'abc', false, '{}', NULL, NULL, '{}', 'h', ?, ?, 'COMPLETED')",
                  [sid, kind, D1, NOW, H, NOW, NOW])  # fmt: skip

    s.migrate()
    s.migrate()  # idempotent

    assert c.execute("SELECT strategy_id, vcp_score FROM setup_scores").fetchall() == [
        ("vcp", 77.5)]  # fmt: skip
    assert c.execute("SELECT strategy_id, points FROM score_components").fetchall() == [
        ("vcp", 8.33)]  # fmt: skip
    assert c.execute("SELECT strategy_id, ret_5 FROM forward_labels").fetchall() == [("vcp", 1.5)]
    assert c.execute("SELECT strategy_id, algorithm_version FROM backtest_runs").fetchall() == [
        ("vcp", "vcp-1.1.0")]  # fmt: skip
    assert dict(c.execute("SELECT scan_type, strategy_id FROM scan_runs").fetchall()) == {
        "TREND_TEMPLATE": None, "VCP": "vcp", "SCORE": "vcp"}  # fmt: skip
    # strategy_id is required from now on, and part of the label key (multi-label).
    nullable = dict(c.execute(
        "SELECT table_name, is_nullable FROM information_schema.columns"
        " WHERE column_name = 'strategy_id' AND table_name IN ('setup_scores',"
        " 'score_components', 'forward_labels', 'backtest_runs')").fetchall())  # fmt: skip
    assert set(nullable.values()) == {"NO"} and len(nullable) == 4
    c.execute("INSERT INTO forward_labels (strategy_id, instrument_id, as_of_date, config_hash,"
              " label_version, data_snapshot_id, score_scan_id, entry_close, bars_after, complete,"
              " computed_at) VALUES ('flat_base', 'A', ?, ?, 'labels-1.0.0', 'LIVE', 's', 100, 0,"
              " false, ?)", [D1, H, NOW])  # fmt: skip
    assert c.execute("SELECT count(*) FROM forward_labels").fetchone() == (2,)


def test_fresh_database_has_the_new_tables_and_views() -> None:
    s = DuckDBStore(":memory:")
    s.migrate()
    names = {r[0] for r in s.conn.execute(
        "SELECT table_name FROM information_schema.tables").fetchall()}  # fmt: skip
    assert {"strategy_setups", "strategy_setup_status_history", "strategy_breakout_events",
            "strategy_scan_run_results", "setups", "breakout_events",
            "setup_scores_v"} <= names  # fmt: skip


# -- setups view -------------------------------------------------------------------------------


def test_setups_view_projects_vcp_patterns(store: DuckDBStore) -> None:
    rows = store.conn.execute(
        "SELECT setup_id, strategy_id, grade, trend_gate, stop_reference_price,"
        " dryup_volume_ratio, prior_advance_pct, details_json FROM setups"
        " WHERE as_of_date = ? ORDER BY setup_id", [D2],
    ).fetchall()  # fmt: skip
    assert rows == [
        ("stA", "flat_base", 2, "PASS", 103.0, 0.6, None, "{}"),
        (f"vpA{D2}", "vcp", 2, "PASS", 101.5, 0.55, None, "{}"),  # last contraction's trough
        (f"vpB{D2}", "vcp", 1, "PASS", None, 0.55, None, "{}"),
        (f"vpC0{D2}", "vcp", 1, "PASS", None, 0.55, None, "{}"),
        (f"vpC{D2}", "vcp", 0, "FAIL", 48.0, 0.55, None, "{}"),
    ]
    assert store.conn.execute("SELECT count(*) FROM setups").fetchone() == (9,)


def test_setup_scores_v_aliases_the_pattern_score(store: DuckDBStore) -> None:
    store.conn.execute("UPDATE setup_scores SET vcp_score = 66 WHERE strategy_id = 'flat_base'")
    assert store.conn.execute(
        "SELECT pattern_score, pattern_weight FROM setup_scores_v WHERE strategy_id = 'flat_base'"
    ).fetchall() == [(66.0, 38.9)]


# -- reads through the view equal the old slicing joins ----------------------------------------

OLD_PENDING = """
    SELECT s.instrument_id, s.as_of_date, s.scan_id, p.pivot_price
    FROM setup_scores s
    LEFT JOIN vcp_patterns p
      ON p.scan_id = 'vcp-' || substr(s.scan_id, 7)
     AND p.instrument_id = s.instrument_id AND p.is_primary
    LEFT JOIN forward_labels f
      ON f.instrument_id = s.instrument_id AND f.as_of_date = s.as_of_date
     AND f.config_hash = s.config_hash AND f.label_version = 'labels-1.0.0'
     AND f.data_snapshot_id = s.data_snapshot_id
    WHERE s.config_hash = ? AND s.data_snapshot_id = 'LIVE' AND coalesce(NOT f.complete, true)
      AND s.strategy_id = 'vcp'
    ORDER BY s.as_of_date, s.instrument_id
"""
OLD_SIGNALS = """
    SELECT s.instrument_id, s.as_of_date, p.pivot_price, s.final_setup_score,
           (SELECT c.trough_price FROM vcp_contractions c
             WHERE c.vcp_pattern_id = p.vcp_pattern_id
             ORDER BY c.sequence_number DESC LIMIT 1),
           s.classification
    FROM setup_scores s
    JOIN vcp_patterns p
      ON p.scan_id = 'vcp-' || substr(s.scan_id, 7) AND p.instrument_id = s.instrument_id
     AND p.is_primary
    WHERE s.config_hash = ? AND s.data_snapshot_id = 'LIVE' AND (s.eligible OR NOT ?)
      AND s.as_of_date BETWEEN ? AND ? AND p.pivot_price IS NOT NULL
      AND s.classification IN (SELECT unnest(?))
      AND s.strategy_id = 'vcp'
    ORDER BY s.as_of_date, s.instrument_id
"""


def test_label_observations_equal_the_old_join(store: DuckDBStore) -> None:
    old = store.conn.execute(OLD_PENDING, [H]).fetchall()
    new = DuckDBLabelRepository(store).pending(H)
    assert [(o.instrument_id, o.as_of, o.score_scan_id, o.pivot) for o in new] == old
    assert len(new) == 8 and {o.pivot for o in new if o.instrument_id == "A"} == {120.0}
    flat = DuckDBLabelRepository(store).pending(H, "flat_base")
    assert [(o.instrument_id, o.as_of, o.pivot) for o in flat] == [("A", D2, 999.0)]


@pytest.mark.parametrize("baseline", [False, True])
def test_backtest_signals_equal_the_old_join(store: DuckDBStore, baseline: bool) -> None:
    classes = ["NONE", "VCP_LIKE", "VCP", "A_PLUS_VCP"] if baseline else [
        "VCP_LIKE", "VCP", "A_PLUS_VCP"]  # fmt: skip
    old = store.conn.execute(OLD_SIGNALS, [H, not baseline, D1, D2, classes]).fetchall()
    new = DuckDBBacktestRepository(store).signals(D1, D2, H, classes, eligible_only=not baseline)
    assert [(s.instrument_id, s.scan_date, s.pivot, s.score, s.final_low, s.classification)
            for s in new] == old  # fmt: skip
    assert len(new) == (4 if baseline else 2)
    flat = DuckDBBacktestRepository(store).signals(D1, D2, H, ["FLAT_BASE"],
                                                   strategy_id="flat_base")  # fmt: skip
    assert [(s.instrument_id, s.pivot, s.final_low) for s in flat] == [("A", 999.0, 103.0)]
