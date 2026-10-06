"""The serving copy, opened read-only and re-opened when the daily run replaces the file.

DuckDB keeps one database instance per file path inside a process, so a replaced file is only
seen after the old connection is closed. ``cursor()`` therefore waits until no request is still
using the old copy, closes it, and opens the new one (a request in flight is never cut off).
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb

from vcp_scanner.data.providers._time import IST

REOPEN_WAIT_SECONDS = 30.0


class ServingCopyMissing(RuntimeError):
    """The serving copy does not exist (the daily run has not written it yet)."""


class ServingDb:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._cond = threading.Condition()
        self._con: duckdb.DuckDBPyConnection | None = None
        self._identity: tuple[int, int] | None = None
        self._active = 0  # cursors in use
        self._cache: dict[Any, Any] = {}

    def _stat(self) -> tuple[int, int]:
        try:
            st = self.path.stat()
        except FileNotFoundError as exc:
            raise ServingCopyMissing(
                f"serving copy not found: {self.path} (run `vcp run daily --serving-copy`)"
            ) from exc
        return (st.st_ino, st.st_mtime_ns)

    def _current(self) -> duckdb.DuckDBPyConnection:
        """The connection to the file as it is now (caller holds the lock)."""
        ident = self._stat()
        while self._con is None or ident != self._identity:
            if self._active and self._con is not None:
                # a request still reads the old copy: let it finish, then switch
                self._cond.wait(REOPEN_WAIT_SECONDS)
                ident = self._stat()
                continue
            if self._con is not None:
                self._con.close()
                self._con = None
            self._con = duckdb.connect(str(self.path), read_only=True)
            self._identity = ident
            self._cache = {}
        return self._con

    @contextmanager
    def cursor(self) -> Iterator[duckdb.DuckDBPyConnection]:
        """A cursor on the current copy, valid inside the ``with`` block."""
        with self._cond:
            cur = self._current().cursor()
            self._active += 1
        try:
            yield cur
        finally:
            cur.close()
            with self._cond:
                self._active -= 1
                self._cond.notify_all()

    def data_time(self) -> datetime:
        _, mtime_ns = self._stat()
        return datetime.fromtimestamp(mtime_ns / 1e9, IST)

    def cached(self, key: Any, make: Callable[[], Any]) -> Any:
        """Memoise ``make()`` until the copy is replaced (for the heavier queries)."""
        with self._cond:
            self._current()  # drops the cache when the file was replaced
            if key in self._cache:
                return self._cache[key]
            generation = self._identity
        value = make()
        with self._cond:
            if self._identity == generation:
                self._cache[key] = value
        return value
