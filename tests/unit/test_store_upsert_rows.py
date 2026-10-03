"""Set-based upserts (DuckDBStore.upsert_rows; Phase 9 step 1) and the pandas guard."""

from __future__ import annotations

import sys

import pytest

from vcp_scanner.data.storage.duckdb_store import DuckDBStore

SQL = """
    INSERT INTO t (k, v, note) VALUES (?, ?, ?)
    ON CONFLICT (k) DO UPDATE SET v = EXCLUDED.v, note = EXCLUDED.note
"""


def test_upsert_rows_inserts_and_updates_like_executemany() -> None:
    with DuckDBStore(":memory:") as store:
        c = store.conn
        c.execute("CREATE TABLE t (k INTEGER PRIMARY KEY, v DOUBLE, note VARCHAR)")
        assert store.upsert_rows(SQL, [(1, 1.5, "a"), (2, None, None)]) == 2
        assert store.upsert_rows(SQL, [(2, 2.5, "b"), (3, 3.0, None)]) == 2
        assert c.execute("SELECT * FROM t ORDER BY k").fetchall() == [
            (1, 1.5, "a"), (2, 2.5, "b"), (3, 3.0, None)]  # fmt: skip
        # The same result as row-by-row executemany.
        c.execute("CREATE TABLE u (k INTEGER PRIMARY KEY, v DOUBLE, note VARCHAR)")
        c.executemany(SQL.replace("INTO t", "INTO u"), [(1, 1.5, "a"), (2, None, None)])
        c.executemany(SQL.replace("INTO t", "INTO u"), [(2, 2.5, "b"), (3, 3.0, None)])
        assert c.execute("SELECT * FROM u ORDER BY k").fetchall() == c.execute(
            "SELECT * FROM t ORDER BY k").fetchall()  # fmt: skip
        assert store.upsert_rows(SQL, []) == 0


def test_upsert_rows_refuses_other_statement_shapes() -> None:
    with DuckDBStore(":memory:") as store:
        store.conn.execute("CREATE TABLE t (k INTEGER PRIMARY KEY, v DOUBLE, note VARCHAR)")
        with pytest.raises(ValueError, match="needs INSERT INTO"):
            store.upsert_rows("INSERT INTO t VALUES (?, ?, ?)", [(1, 1.0, "x")])
        with pytest.raises(ValueError, match="no parameters"):
            store.upsert_rows(
                "INSERT INTO t (k, v, note) VALUES (?, ?, ?) ON CONFLICT (k) DO UPDATE SET v = ?",
                [(1, 1.0, "x")],
            )


def test_pandas_is_marked_absent_when_not_installed() -> None:
    import importlib.util

    if "pandas" in sys.modules and sys.modules["pandas"] is not None:
        pytest.skip("pandas is installed here")
    assert sys.modules.get("pandas", "missing") is None
    assert importlib.util.find_spec is not None
    with pytest.raises(ImportError):
        import pandas  # noqa: F401
