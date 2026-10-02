"""VCP results storage (DATABASE_SCHEMA 31-34, VCP_SPECIFICATION 47)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

from vcp_scanner.config.models import ClassificationConfig, VCPThresholdsConfig
from vcp_scanner.data.repositories.duckdb_vcp_repository import (
    DuckDBVCPRepository,
    pattern_id,
    vcp_results_hash,
    vcp_verdict_row,
)
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.features import FEATURES_CALCULATION_VERSION
from vcp_scanner.patterns.vcp.detector import VCPDetection, VCPDetector
from vcp_scanner.patterns.vcp.monitor import track_breakouts

D0 = date(2026, 1, 1)
CLASSIC = [(60, 100), (20, 150), (10, 120), (10, 145), (6, 130.5), (8, 142), (5, 135), (5, 140)]
VCP_CFG = VCPThresholdsConfig(
    swing={"left_bars": 2, "right_bars": 2},  # type: ignore[arg-type]
    base={"max_duration_days": 80},  # type: ignore[arg-type]
    prior_advance={"lookback_days": 20},  # type: ignore[arg-type]
)
NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


def _closes(extra: list[float]) -> list[float]:
    closes = [100.0]
    for bars, target in CLASSIC:
        a = closes[-1]
        closes += [a + (target - a) * (i + 1) / bars for i in range(bars)]
    return closes + extra


def _seed(store: DuckDBStore, iid: str, closes: list[float], volumes: dict[int, float]) -> None:
    rows, feats = [], []
    for i, c in enumerate(closes):
        d = D0 + timedelta(days=i)
        v = None if volumes.get(i) == -1 else volumes.get(i, 1000.0)
        rows.append((iid, d, c, c * 1.002, c * 0.998, c, v, "adj-test", 1, 1, "LIVE", NOW))
        feats.append((iid, d, 2.0, FEATURES_CALCULATION_VERSION, "LIVE"))
    store.conn.executemany(
        "INSERT INTO daily_prices_adjusted VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows
    )
    store.conn.executemany(
        "INSERT INTO technical_features_daily (instrument_id, trade_date, atr_pct_14,"
        " calculation_version, data_snapshot_id) VALUES (?, ?, ?, ?, ?)",
        feats,
    )


def _scan(store: DuckDBStore, as_of: date, scan_id: str) -> list[VCPDetection]:
    repo = DuckDBVCPRepository(store)
    ids = ["A", "B"]
    series = repo.load_series(ids, as_of, VCP_CFG.lookback_bars())
    priors = repo.prior_patterns(ids, as_of, "h")
    events = repo.events(ids, as_of, "h")
    det = VCPDetector(VCP_CFG, ClassificationConfig(), config_hash="h")
    out, new = [], []
    for iid in ids:
        d = det.detect(iid, series[iid], as_of, trend_template_pass=True, weekly_stage2_pass=True)
        ev = events.get((iid, d.pattern.base_start)) if d.pattern else None
        d, e = track_breakouts(d, series[iid], prior=priors.get(iid), event=ev,
                               config_hash="h", vcp=VCP_CFG)  # fmt: skip
        out.append(d)
        if e:
            new.append(e)
    repo.save_scan(scan_id, as_of, "h", "vcp-1.0.0", out, new, NOW)
    return out


def _store() -> DuckDBStore:
    store = DuckDBStore()
    store.migrate()
    _seed(store, "A", _closes([140.0, 143.0]), {126: 2000.0})  # breakout on the 2nd extra bar
    _seed(store, "B", _closes([140.0, 140.5]), {})  # no breakout
    return store


def test_load_series_takes_the_last_bars_with_atr_and_missing_volume() -> None:
    store = DuckDBStore()
    store.migrate()
    closes = _closes([])
    _seed(store, "A", closes, {124: -1})  # -1 = missing volume
    as_of = D0 + timedelta(days=124)
    s = DuckDBVCPRepository(store).load_series(["A", "Z"], as_of - timedelta(days=1), 10)
    assert set(s) == {"A"}
    a = s["A"]
    assert len(a.dates) == 10 and a.dates[-1] == as_of - timedelta(days=1)
    assert a.atr_pct_14 == [2.0] * 10
    full = DuckDBVCPRepository(store).load_series(["A"], as_of, 500)["A"]
    assert len(full.dates) == len(closes) and full.volume[-1] is None


def test_save_scan_rows_and_relations() -> None:
    store = _store()
    day1 = D0 + timedelta(days=125)
    dets = _scan(store, day1, "vcp-d1")
    q = store.conn.execute
    assert q("SELECT count(*) FROM vcp_patterns WHERE scan_id = 'vcp-d1'").fetchone() == (2,)
    pid = pattern_id("vcp-d1", "A", dets[0].pattern.base_start)  # type: ignore[union-attr]
    row = q("SELECT classification, status, contraction_count, pivot_source, unmet_rules,"
            " is_primary FROM vcp_patterns WHERE vcp_pattern_id = ?", [pid]).fetchone()  # fmt: skip
    assert row is not None
    assert row[2] == 3 and row[5] is True
    assert set(json.loads(row[4])) == {"A_PLUS_VCP", "VCP", "VCP_LIKE"}
    contractions = q(
        "SELECT sequence_number, tightening_ratio_to_prior, is_confirmed FROM vcp_contractions"
        " WHERE vcp_pattern_id = ? ORDER BY 1",
        [pid],
    ).fetchall()
    assert [c[0] for c in contractions] == [1, 2, 3] and contractions[0][1] is None
    pivots = q("SELECT count(*), sum(is_primary::INT), sum(is_structural::INT) FROM vcp_pivots"
               " WHERE vcp_pattern_id = ?", [pid]).fetchone()  # fmt: skip
    assert pivots is not None and pivots[0] >= 2 and pivots[1] == 1 and pivots[2] == 1
    # First scan: every pattern appears in the status history with no previous state.
    hist = q("SELECT instrument_id, previous_classification, new_classification FROM"
             " vcp_status_history WHERE as_of_date = ? ORDER BY 1", [day1]).fetchall()  # fmt: skip
    assert [h[0] for h in hist] == ["A", "B"] and all(h[1] is None for h in hist)
    assert q("SELECT count(*) FROM vcp_breakout_events").fetchone() == (0,)


def test_second_day_breakout_event_history_and_rerun() -> None:
    store = _store()
    day1, day2 = D0 + timedelta(days=125), D0 + timedelta(days=126)
    _scan(store, day1, "vcp-d1")
    dets = _scan(store, day2, "vcp-d2")
    a = dets[0].pattern
    assert a is not None and a.status.value == "BREAKOUT"
    q = store.conn.execute
    ev = q("SELECT instrument_id, breakout_date, method FROM vcp_breakout_events").fetchall()
    assert ev == [("A", day2, "STRUCTURAL")]
    link = q("SELECT breakout_event_id IS NOT NULL, base_end_date FROM vcp_patterns"
             " WHERE scan_id = 'vcp-d2' AND instrument_id = 'A'").fetchone()  # fmt: skip
    assert link == (True, day2)
    hist = q("SELECT instrument_id, previous_as_of_date, previous_status, new_status FROM"
             " vcp_status_history WHERE as_of_date = ?", [day2]).fetchall()  # fmt: skip
    assert ("A", day1, "PIVOT_READY", "BREAKOUT") in hist
    assert all(h[0] != "B" for h in hist) or len(hist) == 2  # B changes only if its status did
    # The prior pattern for a later date is day 2's.
    prior = DuckDBVCPRepository(store).prior_patterns(["A"], day2 + timedelta(days=1), "h")
    assert prior["A"].as_of == day2 and prior["A"].status == "BREAKOUT"
    # Rerunning day 2 replaces its rows instead of adding to them.
    _scan(store, day2, "vcp-d2")
    assert q("SELECT count(*) FROM vcp_patterns WHERE scan_id = 'vcp-d2'").fetchone() == (2,)
    assert q("SELECT count(*) FROM vcp_breakout_events").fetchone() == (1,)
    assert q("SELECT count(*) FROM vcp_status_history WHERE as_of_date = ?", [day2]).fetchone()[
        0
    ] == len(hist)  # type: ignore[index]
    # Rerunning day 2 again regenerates its own event (not read back as history): still one.
    again2 = _scan(store, day2, "vcp-d2")
    assert again2[0].pattern.status.value == "BREAKOUT"  # type: ignore[union-attr]
    assert q("SELECT count(*) FROM vcp_breakout_events").fetchone() == (1,)
    # Rerunning day 1 afterwards does not see day 2's event (detected later).
    again = _scan(store, day1, "vcp-d1")
    assert again[0].pattern.status.value == "PIVOT_READY"  # type: ignore[union-attr]


def test_verdict_rows_and_hash() -> None:
    store = _store()
    dets = _scan(store, D0 + timedelta(days=125), "vcp-d1")
    rows = [vcp_verdict_row(d) for d in dets]
    assert rows[0][0] == "A" and rows[0][1] == "VCP"  # flat volume: no dry-up, so not A+
    assert vcp_results_hash(rows) == vcp_results_hash(list(reversed(rows)))
    nopat = VCPDetection("C", D0, None, None, "NO_PRIOR_ADVANCE")
    assert vcp_verdict_row(nopat) == ("C", None, None, None, None, "NO_PRIOR_ADVANCE")
    assert vcp_results_hash([*rows, vcp_verdict_row(nopat)]) != vcp_results_hash(rows)


def test_recomputing_an_earlier_date_removes_later_events() -> None:
    """Found in the 2026-10-02 copy check: computing day 1 after day 2 must not collide with
    day 2's event; later events were built on the old history and are removed."""
    store = _store()
    day1, day2 = D0 + timedelta(days=125), D0 + timedelta(days=126)
    _scan(store, day2, "vcp-d2")  # day 2 first: its breakout event exists
    q = store.conn.execute
    assert q("SELECT count(*) FROM vcp_breakout_events").fetchone() == (1,)
    repo = DuckDBVCPRepository(store)
    assert repo.later_scan_dates(day1, "h") == [day2]
    _scan(store, day1, "vcp-d1")  # no duplicate-key error
    assert q("SELECT count(*) FROM vcp_breakout_events").fetchone() == (0,)
    _scan(store, day2, "vcp-d2")  # rerun in order: the event comes back
    assert q("SELECT detected_as_of FROM vcp_breakout_events").fetchall() == [(day2,)]
