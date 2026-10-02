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
