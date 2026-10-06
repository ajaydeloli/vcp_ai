"""The serving copy accessor: a replaced file is seen, and never while a request still reads."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import duckdb
import pytest

from vcp_scanner.api.db import ServingCopyMissing, ServingDb


def _make(path: Path, n: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".new")
    with duckdb.connect(str(tmp)) as con:
        con.execute("CREATE TABLE t (x INTEGER)")
        con.execute("INSERT INTO t SELECT * FROM range(?)", [n])
    tmp.replace(path)


def _count(cur: duckdb.DuckDBPyConnection) -> int:
    return int(cur.execute("SELECT count(*) FROM t").fetchone()[0])  # type: ignore[index]


def test_missing_copy_is_reported_with_the_remedy(tmp_path: Path) -> None:
    db = ServingDb(tmp_path / "serving" / "x.duckdb")
    with pytest.raises(ServingCopyMissing, match="vcp run daily --serving-copy"), db.cursor():
        pass


def test_a_replaced_file_is_seen_by_the_next_cursor(tmp_path: Path) -> None:
    path = tmp_path / "s.duckdb"
    _make(path, 3)
    db = ServingDb(path)
    with db.cursor() as cur:
        assert _count(cur) == 3
    _make(path, 5)
    with db.cursor() as cur:
        assert _count(cur) == 5


def test_the_connection_is_read_only(tmp_path: Path) -> None:
    path = tmp_path / "s.duckdb"
    _make(path, 1)
    with ServingDb(path).cursor() as cur, pytest.raises(duckdb.Error):
        cur.execute("INSERT INTO t VALUES (1)")


def test_a_request_in_flight_keeps_its_copy_until_it_ends(tmp_path: Path) -> None:
    path = tmp_path / "s.duckdb"
    _make(path, 3)
    db = ServingDb(path)
    seen: list[int] = []
    with db.cursor() as old:
        _make(path, 7)  # the daily run replaces the file while a request reads
        worker = threading.Thread(target=lambda: seen.append(_count_new(db)))
        worker.start()
        time.sleep(0.3)
        assert seen == [] and _count(old) == 3  # the new request waits; the old one is intact
    worker.join(5)
    assert seen == [7]


def _count_new(db: ServingDb) -> int:
    with db.cursor() as cur:
        return _count(cur)


def test_cached_values_are_dropped_when_the_file_is_replaced(tmp_path: Path) -> None:
    path = tmp_path / "s.duckdb"
    _make(path, 3)
    db = ServingDb(path)
    calls: list[int] = []

    def make() -> int:
        calls.append(1)
        with db.cursor() as cur:
            return _count(cur)

    assert db.cached("k", make) == 3 and db.cached("k", make) == 3 and len(calls) == 1
    _make(path, 4)
    assert db.cached("k", make) == 4 and len(calls) == 2
