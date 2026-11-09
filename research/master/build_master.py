"""Build the merged master database: live data (2021 ->) plus the NSE history of 2011-2020.

Owner request 2026-11-09 ("start steps 1 to 3"): one database from June 2011 onwards, built on
a COPY. The live database is never opened for writing; the source is the verified pre-merge
backup. Nothing replaces the live file without the owner's go.

Stages (``--stages``, in this order):

  copy      fresh copy of the pre-merge backup to data/master_build/vcp_master.duckdb
  merge     raw history rows dated before 2021-01-01, every instrument mapped to its live id:
            instruments (stocks gone before 2021, inactive), identifier history, daily
            prices, daily series, bhavcopy and index files, index closes, corporate actions
            and their resolutions (ex-date before 2021)
  factors   adjustment factors recomputed from each touched stock's full 2011-2026 action set
            (the corporate-action worker's own step: compute_factors + replace_adjustments)
  derive    the project's own commands: quality scan, adjusted prices, features, market health

Identity: a history stock that traded in Jan-Jun 2021 is matched to the live stock with the
same raw closes and volumes on the same days (all 1,889 match one live id; 307 under another
name, e.g. INFOSYSTCH -> INFY). A stock gone before 2021 keeps its id when no live stock
holds it, or maps to the live id of the same issuer (same ISIN prefix).

    .venv/bin/python research/master/build_master.py --stages copy,merge,factors,derive
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import time
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[2]
BACKUP = ROOT / "data" / "backups_manual" / "vcp_scanner_premerge_20261109.duckdb"
HIST = ROOT / "data" / "history_2011.duckdb"
OUT = ROOT / "data" / "master_build" / "vcp_master.duckdb"
LOG = ROOT / "data" / "logs" / "master_build.log"
CUT = date(2021, 1, 1)
TABLES = ("instruments", "instrument_identifier_history", "daily_prices", "daily_series",
          "bhavcopy_files", "index_files", "index_daily", "corporate_actions",
          "corporate_action_resolution")  # fmt: skip


def log(text: str) -> None:
    line = f"{datetime.now(UTC):%Y-%m-%dT%H:%M:%SZ} {text}"
    print(line, flush=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


def one(c: duckdb.DuckDBPyConnection, sql: str, params: list[object] | None = None) -> int:
    row = c.execute(sql, params or []).fetchone()
    return int(row[0]) if row else 0


def stage_copy() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    for p in (OUT, OUT.with_suffix(".duckdb.wal")):
        p.unlink(missing_ok=True)
    shutil.copy2(BACKUP, OUT)
    log(f"copy: {BACKUP.name} -> {OUT} ({OUT.stat().st_size / 1e9:.2f} GB)")


def build_map(c: duckdb.DuckDBPyConnection) -> None:
    """idmap(hid, lid, how): every history instrument to its id in the master."""
    c.execute("""
        CREATE OR REPLACE TEMP TABLE ov AS
        SELECT h.instrument_id hid, l.instrument_id lid, count(*) n
        FROM H.daily_prices h JOIN main.daily_prices l
          ON h.trade_date = l.trade_date AND h.close_raw = l.close_raw
         AND h.volume_raw = l.volume_raw
        WHERE h.trade_date BETWEEN '2021-01-01' AND '2021-06-30'
        GROUP BY 1, 2""")
    c.execute("""
        CREATE OR REPLACE TEMP TABLE hb AS
        SELECT instrument_id hid, count(*) nb FROM H.daily_prices
        WHERE trade_date BETWEEN '2021-01-01' AND '2021-06-30' GROUP BY 1""")
    c.execute("""
        CREATE OR REPLACE TEMP TABLE idmap AS
        WITH best AS (
            SELECT ov.*, nb, row_number() OVER (PARTITION BY ov.hid ORDER BY n DESC) rk
            FROM ov JOIN hb USING (hid))
        SELECT hid, lid, 'overlap' how FROM best WHERE rk = 1 AND n >= 0.9 * nb""")
    if one(c, "SELECT count(*) FROM hb WHERE hid NOT IN (SELECT hid FROM idmap)"):
        raise SystemExit("a history stock with 2021 bars has no live match")
    # stocks gone before 2021: same id if free, or the live id of the same issuer
    c.execute("""
        INSERT INTO idmap
        SELECT h.instrument_id, h.instrument_id,
               CASE WHEN l.instrument_id IS NULL THEN 'new' ELSE 'same id, same issuer' END
        FROM H.instruments h LEFT JOIN main.instruments l USING (instrument_id)
        WHERE h.instrument_id NOT IN (SELECT hid FROM idmap)
          AND (l.instrument_id IS NULL OR substr(h.isin, 1, 9) = substr(l.isin, 1, 9))""")
    left = one(c, "SELECT count(*) FROM H.instruments WHERE instrument_id NOT IN "
                  "(SELECT hid FROM idmap)")  # fmt: skip
    dup = one(c, "SELECT count(*) FROM (SELECT lid FROM idmap GROUP BY lid HAVING count(*) > 1)")
    if left or dup:
        raise SystemExit(f"identity map incomplete: {left} unmapped, {dup} live ids taken twice")
    hows = c.execute("SELECT how, count(*) FROM idmap GROUP BY 1 ORDER BY 1").fetchall()
    renamed = one(c, "SELECT count(*) FROM idmap WHERE hid <> lid")
    log("map: " + ", ".join(f"{h} {n}" for h, n in hows) + f"; renamed {renamed}")


def stage_merge() -> None:
    c = duckdb.connect(str(OUT))
    c.execute(f"ATTACH '{HIST}' AS H (READ_ONLY)")
    before = {t: one(c, f"SELECT count(*) FROM main.{t}") for t in TABLES}
    if one(c, "SELECT count(*) FROM main.daily_prices WHERE trade_date < ?", [CUT]):
        raise SystemExit("the copy already holds pre-2021 prices: start again from `copy`")
    build_map(c)
    c.execute("BEGIN")
    # instruments of stocks gone before 2021 (no live row): inactive, as live keeps them
    c.execute("""
        INSERT INTO main.instruments
        SELECT h.* REPLACE (FALSE AS is_active)
        FROM H.instruments h JOIN idmap m ON m.hid = h.instrument_id AND m.how = 'new'""")
    # identifier periods before 2021, ended where the live history begins
    c.execute("""
        INSERT INTO main.instrument_identifier_history
        WITH first_live AS (SELECT instrument_id, min(valid_from) f
                            FROM main.instrument_identifier_history GROUP BY 1)
        SELECT m.lid, h.symbol, h.isin, h.valid_from,
               CASE WHEN fl.f IS NULL THEN h.valid_to
                    ELSE least(coalesce(h.valid_to, fl.f), fl.f) END,
               h.change_reason, h.source, h.recorded_at
        FROM H.instrument_identifier_history h
        JOIN idmap m ON m.hid = h.instrument_id
        LEFT JOIN first_live fl ON fl.instrument_id = m.lid
        WHERE h.valid_from < coalesce(fl.f, DATE '9999-12-31') AND h.valid_from < ?""", [CUT])
    for t in ("daily_prices", "daily_series"):
        c.execute(f"""
            INSERT INTO main.{t}
            SELECT h.* REPLACE (m.lid AS instrument_id)
            FROM H.{t} h JOIN idmap m ON m.hid = h.instrument_id
            WHERE h.trade_date < ?""", [CUT])  # noqa: S608
    for t in ("bhavcopy_files", "index_files", "index_daily"):
        c.execute(f"INSERT INTO main.{t} SELECT * FROM H.{t} WHERE trade_date < ?", [CUT])  # noqa: S608
    # an action is filed under its instrument's master id: the mapped history id, or, for an
    # action the history import filed under today's name (its prices sat under the old one,
    # e.g. VEEDOL, HEGAM), that name when the master holds it
    c.execute("""
        CREATE OR REPLACE TEMP TABLE ca_id AS
        SELECT DISTINCT h.instrument_id hid,
               coalesce(m.lid, CASE WHEN l.instrument_id IS NOT NULL THEN h.instrument_id END) lid
        FROM (SELECT instrument_id FROM H.corporate_actions
              UNION SELECT instrument_id FROM H.corporate_action_resolution) h
        LEFT JOIN idmap m ON m.hid = h.instrument_id
        LEFT JOIN main.instruments l ON l.instrument_id = h.instrument_id""")
    c.execute("""
        INSERT INTO main.corporate_actions
        SELECT h.* REPLACE (k.lid AS instrument_id)
        FROM H.corporate_actions h JOIN ca_id k ON k.hid = h.instrument_id AND k.lid IS NOT NULL
        WHERE h.ex_date < ? AND h.corporate_action_id NOT IN
              (SELECT corporate_action_id FROM main.corporate_actions)""", [CUT])
    c.execute("""
        INSERT INTO main.corporate_action_resolution
        SELECT h.* REPLACE (k.lid AS instrument_id)
        FROM H.corporate_action_resolution h
        JOIN ca_id k ON k.hid = h.instrument_id AND k.lid IS NOT NULL
        WHERE h.ex_date < ?""", [CUT])
    dropped = one(c, """SELECT count(*) FROM H.corporate_actions h JOIN ca_id k
                        ON k.hid = h.instrument_id WHERE k.lid IS NULL AND h.ex_date < ?""", [CUT])
    log(f"merge: {dropped} pre-2021 actions dropped (no such stock in the master)")
    c.execute("CREATE TABLE main.master_idmap AS SELECT * FROM idmap")
    c.execute("COMMIT")
    for t, n in before.items():
        after = one(c, f"SELECT count(*) FROM main.{t}")
        log(f"merge: {t:30} {n:>9} -> {after:>9} (+{after - n})")
    c.execute("CHECKPOINT")
    c.close()


def stage_factors() -> None:
    from vcp_scanner.data.adjustment.engine import AdjustmentEngine, ex_date_prices
    from vcp_scanner.data.ingestion.ca_worker import _factor_key
    from vcp_scanner.data.repositories.duckdb_corporate_action_repository import (
        DuckDBCorporateActionRepository,
    )
    from vcp_scanner.data.repositories.duckdb_market_repository import (
        DuckDBMarketDataRepository,
    )
    from vcp_scanner.data.storage.duckdb_store import DuckDBStore
    from vcp_scanner.domain.corporate_actions import PRICE_DERIVED_ACTIONS, PRICE_SCALING_ACTIONS

    known_at = datetime.now(UTC)
    with DuckDBStore(str(OUT)) as store:
        ids = [r[0] for r in store.conn.execute(
            "SELECT DISTINCT instrument_id FROM corporate_action_resolution WHERE ex_date < ?",
            [CUT]).fetchall()]  # fmt: skip
        repo = DuckDBCorporateActionRepository(store)
        market = DuckDBMarketDataRepository(store)
        engine = AdjustmentEngine()
        changed = factors = 0
        for iid in ids:
            current = repo.load_resolutions(iid)
            kinds = PRICE_DERIVED_ACTIONS | PRICE_SCALING_ACTIONS
            ex_dates = [r.ex_date for r in current
                        if r.ex_date is not None and r.action_type in kinds]  # fmt: skip
            candles = market.load_daily(iid, date(1900, 1, 1), date(2999, 12, 31))
            prices = ex_date_prices(candles, ex_dates) if ex_dates else {}
            new = engine.compute_factors(current, prices)
            if _factor_key(new) != _factor_key(repo.load_adjustments(iid)):
                repo.replace_adjustments(iid, new, known_from=known_at)
                changed += 1
                factors += len(new)
        log(f"factors: {len(ids)} stocks with pre-2021 actions; {changed} factor sets replaced "
            f"({factors} factors)")  # fmt: skip


def stage_derive() -> None:
    db, cfg = str(OUT), str(ROOT / "config")
    for name, argv in (
        ("quality scan", ["quality", "scan", "--db", db, "--config-dir", cfg]),
        ("adjusted prices", ["ingest", "adjusted-prices", "--db", db]),
        ("features", ["compute", "features", "--db", db]),
        ("market health", ["health", "build", "--db", db, "--config-dir", cfg]),
    ):
        t = time.monotonic()
        out = ROOT / "data" / "logs" / f"master_{name.replace(' ', '_')}.out"
        with out.open("w") as f:
            code = subprocess.call([str(ROOT / ".venv" / "bin" / "vcp"), *argv], stdout=f,
                                   stderr=subprocess.STDOUT, cwd=ROOT)  # fmt: skip
        log(f"derive: {name} exit {code} in {time.monotonic() - t:.0f}s (output {out.name})")
        if code:
            raise SystemExit(f"{name} failed")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stages", default="copy,merge,factors,derive")
    stages = ap.parse_args().stages.split(",")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    for name, fn in (("copy", stage_copy), ("merge", stage_merge), ("factors", stage_factors),
                     ("derive", stage_derive)):  # fmt: skip
        if name in stages:
            t = time.monotonic()
            fn()
            log(f"stage {name} done in {time.monotonic() - t:.0f}s")


if __name__ == "__main__":
    main()
