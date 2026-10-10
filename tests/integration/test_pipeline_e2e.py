"""Audit P1-1: the whole CLI pipeline runs from an EMPTY database.

Before the fix no command populated ``instruments``, so ``vcp ingest market`` stopped with "no
instruments found" and nothing downstream could run. This test drives every stage through the
real CLI in README order, with only the external providers replaced by synthetic fakes
(AGENTS.md rule 10): security-master -> market -> corporate-actions -> adjusted-prices ->
features -> universe -> rs -> trend-template.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from vcp_scanner import cli_pipeline
from vcp_scanner.cli import main as cli_main
from vcp_scanner.data.providers import nse_security_master, nse_surveillance
from vcp_scanner.data.providers.fake import FakeMarketDataProvider, make_candle
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.market import SecurityRecord

pytestmark = pytest.mark.integration

CONFIG_DIR = str(Path(__file__).resolve().parents[2] / "config")
AS_OF = date(2024, 6, 28)  # a Friday
DAYS = [d for d in (AS_OF - timedelta(days=i) for i in range(480)) if d.weekday() < 5][
    ::-1
]  # ~343 sessions, oldest first
SYMBOLS = ["S0", "S1", "S2", "S3", "S4", "S5"]
# Daily drift per symbol: S0 is a steady strong advance (should pass), others weaker or falling.
DRIFT = {"S0": 0.6, "S1": 0.2, "S2": 0.05, "S3": -0.05, "S4": -0.2, "S5": 0.0}


def _series(symbol: str) -> list[Any]:
    iid = f"NSE_EQ|{symbol}"
    out = []
    for i, d in enumerate(DAYS):
        close = 150.0 + DRIFT[symbol] * i + (i % 3) * 0.1
        out.append(
            make_candle(iid, d, open_=close, high=close + 1, low=close - 1, close=close,
                        volume=1_000_000)
        )  # fmt: skip
    return out


class _FakeSecurityMaster:
    def get_security_history(self, start: date, end: date) -> list[SecurityRecord]:
        return [
            SecurityRecord(
                instrument_id=f"NSE_EQ|{s}", symbol=s, exchange="NSE",
                valid_from=date(2010, 1, 1), isin=f"INE00000{s}", series="EQ",
                listing_date=date(2010, 1, 1), source="NSE",
            )
            for s in SYMBOLS
        ]  # fmt: skip


class _NoFlags:
    returns_active_snapshot = True

    def get_flags(self, start: date, end: date) -> list[Any]:
        return []


class _NoActions:
    unparsed_ratios: list[str] = []
    unhandled_records: list[str] = []

    def get_actions(self, start: date, end: date, instruments: Any = None) -> list[Any]:
        return []


def test_full_pipeline_from_an_empty_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db = str(tmp_path / "e2e.duckdb")
    env = str(tmp_path / "missing.env")
    monkeypatch.setenv("KITE_API_KEY", "k")
    monkeypatch.setenv("KITE_ACCESS_TOKEN", "t")
    monkeypatch.delenv("UPSTOX_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr(
        nse_security_master, "NSESecurityMasterProvider", lambda: _FakeSecurityMaster()
    )
    monkeypatch.setattr(nse_surveillance, "NSESurveillanceProvider", lambda: _NoFlags())
    fake_market = FakeMarketDataProvider({f"NSE_EQ|{s}": _series(s) for s in SYMBOLS})
    monkeypatch.setattr(cli_pipeline, "_build_market_provider", lambda key, tok: fake_market)
    monkeypatch.setattr(
        cli_pipeline, "_build_ca_providers", lambda tok, cfg=None: (_NoActions(), _NoActions())
    )

    start, end, as_of = DAYS[0].isoformat(), AS_OF.isoformat(), AS_OF.isoformat()
    steps = [
        ["ingest", "security-master", "--start", start, "--end", end, "--no-delisted",
         "--db", db],
        ["ingest", "market", "--kite-history", "--start", start, "--end", end, "--db", db,
         "--config-dir", CONFIG_DIR, "--env-file", env],
        ["ingest", "corporate-actions", "--start", start, "--end", end, "--db", db,
         "--config-dir", CONFIG_DIR, "--env-file", env],
        ["ingest", "adjusted-prices", "--db", db],
        ["compute", "features", "--db", db],
        ["ingest", "universe", "--as-of", as_of, "--db", db, "--config-dir", CONFIG_DIR],
        ["compute", "rs", "--as-of", as_of, "--db", db, "--config-dir", CONFIG_DIR],
        ["compute", "trend-template", "--as-of", as_of, "--db", db, "--config-dir", CONFIG_DIR],
    ]  # fmt: skip
    for argv in steps:
        code = cli_main(argv)
        captured = capsys.readouterr()
        assert code == 0, f"{' '.join(argv[:2])} failed:\n{captured.out}\n{captured.err}"

    with DuckDBStore(db) as store:
        q = store.conn.execute
        assert q("SELECT COUNT(*) FROM instruments WHERE is_active").fetchone() == (6,)
        assert q("SELECT COUNT(DISTINCT instrument_id) FROM daily_prices").fetchone() == (6,)
        assert q(
            "SELECT COUNT(*) FROM data_quality_events WHERE blocks_signal AND status = 'OPEN'"
        ).fetchone() == (0,)
        assert q("SELECT survivorship_status FROM universe_snapshots").fetchall() == [("BIASED",)]
        eligible = q("SELECT COUNT(*) FROM universe_memberships WHERE eligible").fetchone()
        assert eligible == (6,)
        ranked = q(
            "SELECT COUNT(*) FROM relative_strength_snapshots WHERE rs_rank IS NOT NULL"
        ).fetchone()
        assert ranked == (6,)
        statuses = dict(q("SELECT instrument_id, status FROM trend_template_results").fetchall())
        conditions = q("SELECT COUNT(*) FROM trend_template_conditions").fetchone()

    assert set(statuses) == {f"NSE_EQ|{s}" for s in SYMBOLS}
    assert set(statuses.values()) <= {"PASS", "FAIL"}  # data was sufficient everywhere
    assert statuses["NSE_EQ|S0"] == "PASS"
    assert statuses["NSE_EQ|S4"] == "FAIL"
    assert conditions == (60,)

    # Audit P1-8c: the scan left an immutable run record whose hash matches its verdicts, and a
    # rerun adds a second record without erasing the first.
    from vcp_scanner.data.repositories.duckdb_scan_run_repository import (
        DuckDBScanRunRepository,
        results_hash,
    )

    tt_argv = steps[-1]
    assert cli_main(tt_argv) == 0
    assert "Scan run    : run-20240628-" in capsys.readouterr().out
    with DuckDBStore(db) as store:
        runs = DuckDBScanRunRepository(store)
        listed = runs.list_runs(AS_OF)
        assert len(listed) == 2
        first, second = (runs.load(r[0]) for r in reversed(listed))
        assert first is not None and second is not None
        assert first["results_hash"] == second["results_hash"]
        assert first["universe_snapshot_id"] == second["universe_snapshot_id"]
        assert first["data_snapshot_id"] == "LIVE" and first["scan_type"] == "TREND_TEMPLATE"
        assert first["counts"]["considered"] == 6 and first["counts"]["PASS"] >= 1
        assert set(first["section_hashes"]) == {"strategy", "universe", "gate"}
        assert first["code_commit"] and first["versions"]["trend_algorithm_version"]
        assert first["data_cutoff"] == first["started_at"]
        assert results_hash(runs.load_results(first["scan_run_id"])) == first["results_hash"]
        current = store.conn.execute(
            "SELECT instrument_id, status, trend_template_pass, weekly_stage, rs_rank, blocked_by"
            " FROM trend_template_results"
        ).fetchall()
        assert results_hash(current) == first["results_hash"]

    # Audit P1-8d: the recorded run can be rebuilt at its cutoff on a copy and matches; a
    # wrong recorded hash is reported as a mismatch; the main database is left alone.
    first_id = first["scan_run_id"]
    work = tmp_path / "work"
    work.mkdir()
    verify = ["verify", "scan", first_id, "--db", db, "--config-dir", CONFIG_DIR,
              "--work-dir", str(work)]  # fmt: skip
    assert cli_main(verify) == 0, capsys.readouterr().out
    out = capsys.readouterr().out
    assert "MATCH: 6 verdicts" in out
    assert list(work.iterdir()) == []  # the copy was removed
    with DuckDBStore(db) as store:
        assert len(DuckDBScanRunRepository(store).list_runs(AS_OF)) == 2  # untouched
        store.conn.execute(
            "UPDATE scan_runs SET results_hash = 'deadbeef' WHERE scan_run_id = ?", [first_id]
        )
    assert cli_main(verify) == 2
    assert "MISMATCH: recorded deadbeef" in capsys.readouterr().out
    assert cli_main(["verify", "scan", "--db", db]) == 0
    assert first_id in capsys.readouterr().out

    # Phase 6 step 7: VCP detection over the Trend Template scan, with its own run record.
    vcp_argv = ["compute", "vcp", "--as-of", as_of, "--db", db, "--config-dir", CONFIG_DIR]
    assert cli_main(vcp_argv) == 0, capsys.readouterr().out
    out = capsys.readouterr().out
    assert "VCP detection for 2024-06-28" in out and "Considered  : 6" in out
    from vcp_scanner.data.repositories.duckdb_vcp_repository import vcp_results_hash

    with DuckDBStore(db) as store:
        q = store.conn.execute
        vcp_run = q(
            "SELECT scan_run_id, scan_id, results_hash, counts FROM scan_runs"
            " WHERE scan_type = 'VCP'"
        ).fetchall()
        assert len(vcp_run) == 1
        run_id, vcp_scan, vhash, counts = vcp_run[0]
        assert vcp_scan.startswith("vcp-2024-06-28-")
        rows = q(
            "SELECT instrument_id, classification, status, confirmation_state, pivot_price,"
            " no_pattern_reason FROM vcp_scan_run_results WHERE scan_run_id = ?",
            [run_id],
        ).fetchall()
        assert len(rows) == 6 and vcp_results_hash(rows) == vhash
        assert json.loads(counts)["considered"] == 6
        patterns = q("SELECT count(*) FROM vcp_patterns WHERE scan_id = ?", [vcp_scan]).fetchone()
    # A rerun replaces the scan's rows and adds a second run record with the same hash.
    assert cli_main(vcp_argv) == 0
    capsys.readouterr()
    with DuckDBStore(db) as store:
        q = store.conn.execute
        assert (
            q("SELECT count(*) FROM vcp_patterns WHERE scan_id = ?", [vcp_scan]).fetchone()
            == patterns
        )
        hashes = q("SELECT results_hash FROM scan_runs WHERE scan_type = 'VCP'").fetchall()
        assert hashes == [(vhash,), (vhash,)]
    # `verify scan` rebuilds Trend Template runs only and says so for a VCP run.
    assert cli_main(["verify", "scan", run_id, "--db", db, "--config-dir", CONFIG_DIR]) == 1
    assert "is a VCP run" in capsys.readouterr().out

    # Phase 7 step 3: setup scores for every Trend Template passer, with a run record.
    score_argv = ["compute", "scores", "--as-of", as_of, "--db", db, "--config-dir", CONFIG_DIR]
    assert cli_main(score_argv) == 0, capsys.readouterr().err
    assert "Setup scores for 2024-06-28" in capsys.readouterr().out
    assert cli_main(score_argv) == 0  # a rerun replaces the scan's rows
    capsys.readouterr()
    with DuckDBStore(db) as store:
        q = store.conn.execute
        passers = q("SELECT count(*) FROM trend_template_results WHERE scan_id LIKE 'trend-%'"
                    " AND status = 'PASS'").fetchone()  # fmt: skip
        assert passers is not None and passers[0] > 0
        score_scan_id, shash = q(
            "SELECT scan_id, results_hash FROM scan_runs WHERE scan_type = 'SCORE'"
            " ORDER BY started_at DESC LIMIT 1"
        ).fetchone()  # type: ignore[misc]
        assert score_scan_id.startswith("score-2024-06-28-")
        assert q("SELECT count(*) FROM setup_scores WHERE scan_id = ?",
                 [score_scan_id]).fetchone() == passers  # fmt: skip
        hashes = q("SELECT DISTINCT results_hash FROM scan_runs WHERE scan_type = 'SCORE'")
        assert hashes.fetchall() == [(shash,)]  # deterministic
        bad = q("SELECT count(*) FROM setup_scores WHERE ranking_percentile IS NOT NULL"
                " AND NOT eligible").fetchone()  # fmt: skip
        assert bad == (0,)
    # Fundamentals freeze rule (FUNDAMENTALS_SPECIFICATION §2): stored fundamentals, even
    # extreme ones, change nothing in the score; the rerun hash equals the hash without them.
    with DuckDBStore(db) as store:
        q = store.conn.execute
        ids = [r[0] for r in q("SELECT DISTINCT instrument_id FROM setup_scores").fetchall()]
        for n, iid in enumerate(ids):
            sid = f"fs_test_{n}"
            q("INSERT INTO fundamental_snapshots (fundamental_snapshot_id, instrument_id,"
              " period_end, period_type, statement_basis, revision_number, available_at,"
              " provider, data_status) VALUES (?, ?, DATE '2024-03-31', 'QUARTER',"
              " 'CONSOLIDATED', 0, TIMESTAMPTZ '2024-04-20 18:00:00+05:30', 'NSE', 'OK')",
              [sid, iid])  # fmt: skip
            q("INSERT INTO fundamental_facts VALUES (?, 'QUARTER', 'eps', -999.0, false,"
              " DATE '2024-01-01', DATE '2024-03-31')", [sid])  # fmt: skip
    assert cli_main(score_argv) == 0
    capsys.readouterr()
    with DuckDBStore(db) as store:
        hashes = store.conn.execute(
            "SELECT DISTINCT results_hash FROM scan_runs WHERE scan_type = 'SCORE'"
        ).fetchall()
        assert hashes == [(shash,)]
    # Phase 9 step 2: forward labels for the scored observations (no later bars: all open).
    assert cli_main(["compute", "labels", "--db", db, "--config-dir", CONFIG_DIR]) == 0
    assert "Forward labels" in capsys.readouterr().out
    with DuckDBStore(db) as store:
        n_lab, n_done = store.conn.execute(
            "SELECT count(*), count(*) FILTER (WHERE complete) FROM forward_labels").fetchone()  # type: ignore[misc]  # fmt: skip
    assert n_lab == passers[0] and n_done == 0

    # Phase 9 step 3: a backtest over the stored scan (no later bars: no trades, but a run).
    bt = ["backtest", "run", "--from", as_of, "--to", as_of, "--period", "e2e",
          "--db", db, "--config-dir", CONFIG_DIR]  # fmt: skip
    code = cli_main(bt)
    out, err = capsys.readouterr()
    if code == 0:  # eligible setups exist: a run with no trades (no later bars)
        assert "Every trade : 0 trades" in out and "Portfolio" in out
    else:
        assert "No eligible scored setups" in err
    with DuckDBStore(db) as store:
        runs = store.conn.execute("SELECT count(*) FROM backtest_runs").fetchone()
    assert runs == ((1,) if code == 0 else (0,))

    base = cli_main([*bt, "--baseline"])  # every passer with a pivot
    out, err = capsys.readouterr()
    assert (base == 0 and "baseline: every passer" in out) or "No eligible scored" in err

    # Multi-Strategy step 7b: entry rule, market regime and a trailing exit (spec 20).
    for regime in ("breadth50", "ew50"):
        new = cli_main([*bt, "--entry", "cross_5", "--regime", regime, "--rule", "trail_e20"])
        out, err = capsys.readouterr()
        assert (new == 0 and f"entry cross_5; regime {regime}" in out) or (
            "No eligible scored" in err)  # fmt: skip
    with DuckDBStore(db) as store:
        from vcp_scanner.backtest.regime import breadth_regime
        from vcp_scanner.config import load_scanner_config
        from vcp_scanner.config.loader import scan_config_hash
        from vcp_scanner.data.repositories.duckdb_backtest_repository import (
            DuckDBBacktestRepository,
        )
        from vcp_scanner.domain.features import FEATURES_CALCULATION_VERSION

        rows = DuckDBBacktestRepository(store).breadth(
            AS_OF, AS_OF, scan_config_hash(load_scanner_config(CONFIG_DIR)),
            FEATURES_CALCULATION_VERSION)  # fmt: skip
    assert rows and rows[-1].day == AS_OF and 0 <= rows[-1].above <= rows[-1].with_average
    assert set(breadth_regime(rows)) == {r.day for r in rows}

    # Phase 9 step 4: walk-forward; validation and test stay hidden without their flags.
    wf = ["backtest", "walk-forward", "--db", db, "--config-dir", CONFIG_DIR]
    assert cli_main(wf) == 0
    out = capsys.readouterr().out
    assert "[validation] hidden" in out and "[live_paper] hidden" in out

    # Phase 9 step 5: the look-ahead check rebuilds the stored scan without later data.
    la = ["backtest", "lookahead-check", "--as-of", as_of, "--work-dir", str(tmp_path / "la"),
          "--db", db, "--config-dir", CONFIG_DIR]  # fmt: skip
    assert cli_main(la) == 0, capsys.readouterr().out
    out = capsys.readouterr().out
    assert "Result      : IDENTICAL" in out
    assert not list((tmp_path / "la").glob("*.duckdb"))  # the copy is removed

    br = ["backtest", "bias-report", "--from", as_of, "--to", as_of, "--db", db,
          "--config-dir", CONFIG_DIR]  # fmt: skip
    assert cli_main(br) == 0
    out = capsys.readouterr().out
    assert "Survivorship : scan dates by universe status: BIASED 1" in out
    assert "Corporate actions: 0 of" in out and "Period looks" in out

    # Phase 7 step 4: the ranked list and a per-stock explanation.
    assert cli_main(["scores", "list", "--all", "--db", db, "--config-dir", CONFIG_DIR]) == 0
    out = capsys.readouterr().out
    assert "Setup scores 2024-06-28" in out and "not probabilities" in out
    with DuckDBStore(db) as store:
        sym = store.conn.execute(
            "SELECT i.symbol FROM setup_scores s JOIN instruments i USING (instrument_id)"
            " WHERE s.scan_id = ? AND s.final_setup_score IS NOT NULL LIMIT 1", [score_scan_id]
        ).fetchone()[0]  # type: ignore[index]  # fmt: skip
    assert cli_main(["scores", "explain", sym.lower(), "--as-of", as_of, "--db", db,
                     "--config-dir", CONFIG_DIR]) == 0  # fmt: skip
    out = capsys.readouterr().out
    assert f"{sym.upper()} on 2024-06-28: final score" in out
    assert "high_proximity" in out and "pts" in out and "FUNDAMENTALS_UNAVAILABLE" in out
    assert cli_main(["scores", "explain", "NOSUCH", "--db", db, "--config-dir",
                     CONFIG_DIR]) == 1  # fmt: skip
    capsys.readouterr()
    missing = ["compute", "scores", "--as-of", "2024-06-27", "--db", db, "--config-dir",
               CONFIG_DIR]  # fmt: skip
    assert cli_main(missing) == 1
    assert "scores need the Trend Template and VCP scans" in capsys.readouterr().err

    # Multi-Strategy step 4: the new strategies run on the same Trend Template scan.
    for sid in ("flat_base", "three_weeks_tight", "cup_handle", "double_bottom"):
        for argv in (["compute", "setups", "--strategy", sid, "--as-of", as_of],
                     ["compute", "scores", "--strategy", sid, "--as-of", as_of],
                     ["compute", "labels", "--strategy", sid]):  # fmt: skip
            assert cli_main([*argv, "--db", db, "--config-dir", CONFIG_DIR]) == 0, (
                capsys.readouterr().err)  # fmt: skip
        out = capsys.readouterr().out
        assert f"{sid} setups for 2024-06-28" in out and "Forward labels" in out
        bt_s = ["backtest", "run", "--from", as_of, "--to", as_of, "--strategy", sid,
                "--baseline", "--db", db, "--config-dir", CONFIG_DIR]  # fmt: skip
        code = cli_main(bt_s)
        out, err = capsys.readouterr()
        assert (code == 0 and f"[{sid}]" in out) or "No eligible" in err
        with DuckDBStore(db) as store:
            q = store.conn.execute
            assert q("SELECT count(*) FROM scan_runs WHERE scan_type = 'SETUP'"
                     " AND strategy_id = ?", [sid]).fetchone() == (1,)  # fmt: skip
            prim = q("SELECT count(*) FROM strategy_setups WHERE strategy_id = ? AND is_primary",
                     [sid]).fetchone()  # fmt: skip
            assert q("SELECT count(*) FROM setup_scores WHERE strategy_id = ?",
                     [sid]).fetchone() == prim  # fmt: skip
            assert q("SELECT count(*) FROM forward_labels WHERE strategy_id = ?",
                     [sid]).fetchone() == prim  # fmt: skip
            # VCP's rows are untouched by the other strategies (multi-label).
            assert q("SELECT count(*) FROM setup_scores WHERE strategy_id = 'vcp'"
                     ).fetchone() == passers  # fmt: skip

    # Monitoring M3: the paper ledger (the fixture's scans end before the paper start, so the
    # update has nothing to write unless pointed at a session; status reads an empty ledger).
    assert cli_main(["paper", "update", "--db", db, "--config-dir", CONFIG_DIR]) == 0
    assert "nothing to do" in capsys.readouterr().out
    assert cli_main(["paper", "status", "--db", db, "--config-dir", CONFIG_DIR]) == 0
    assert "[flat_base] ledger through -" in capsys.readouterr().out

    # Phase 6 step 8: a blind labelling sheet from the scan, and its CSV back into fixtures.
    sheet_dir = tmp_path / "sheet"
    sheet_argv = ["research", "labelling-sheet", "--from", as_of, "--to", as_of, "--db", db,
                  "--config-dir", CONFIG_DIR, "--out", str(sheet_dir)]  # fmt: skip
    assert cli_main(sheet_argv) == 0, capsys.readouterr().err
    assert "Scan dates  : 1" in capsys.readouterr().out
    cands = json.loads((sheet_dir / "candidates.json").read_text())
    assert cands and all(len(c["bars"]["dates"]) >= 249 for c in cands)  # S0 passes
    assert (sheet_dir / "labelling_sheet.html").stat().st_size > 0
    labels = tmp_path / "labels.csv"
    labels.write_text("id,window,label,notes\n" + f"{cands[0]['id']},1,non_vcp,steady trend\n")
    fx_dir = tmp_path / "fixtures"
    assert cli_main(["research", "import-labels", "--candidates",
                     str(sheet_dir / "candidates.json"), "--labels", str(labels),
                     "--fixtures", str(fx_dir)]) == 0  # fmt: skip
    capsys.readouterr()
    assert cli_main(["research", "golden", "--fixtures", str(fx_dir), "--record",
                     "--config-dir", CONFIG_DIR]) == 0  # fmt: skip
    out = capsys.readouterr().out
    assert "Re-recorded detector baselines for 1 fixtures." in out and "fixtures 1" in out

    # Phase 6 validation: the mark-check sheet and the outcome study run end to end.
    rv_dir = tmp_path / "review"
    assert cli_main(["research", "review-sheet", "--from", as_of, "--to", as_of, "--db", db,
                     "--config-dir", CONFIG_DIR, "--out", str(rv_dir)]) == 0  # fmt: skip
    assert "Scan dates : 1" in capsys.readouterr().out
    assert (rv_dir / "review_sheet.html").stat().st_size > 0
    oc_csv = tmp_path / "windows.csv"
    assert cli_main(["research", "outcomes", "--from", as_of, "--to", as_of, "--split", as_of,
                     "--db", db, "--config-dir", CONFIG_DIR, "--csv", str(oc_csv),
                     "--validate-rule", "t20_low8"]) == 0  # fmt: skip
    out = capsys.readouterr().out
    assert "breakout trade by exit rule" in out and "VALIDATION, rule t20_low8" in out
    assert oc_csv.read_text().splitlines()[0].endswith("trade_hold_s7")
