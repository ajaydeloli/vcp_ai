"""The agent's own state: notebook (notes, decisions) and the audit log (AGENT_SPECIFICATION §5,
§6). A small DuckDB file of its own (``data/agent/agent.duckdb``), opened per call, so it never
locks the scanner's database or a second process (the CLI) reading it."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

#: The kinds of notebook entries. Append-only: an entry is never edited or deleted; a later
#: entry corrects an earlier one.
NOTE_KINDS = ("decision", "finding", "hypothesis", "question", "idea", "note")
MAX_NOTE_CHARS = 4000

_DDL = (
    """CREATE SEQUENCE IF NOT EXISTS agent_notes_seq""",
    """CREATE TABLE IF NOT EXISTS agent_notes (
        note_id   BIGINT PRIMARY KEY DEFAULT nextval('agent_notes_seq'),
        created_at TIMESTAMPTZ NOT NULL,
        kind      VARCHAR NOT NULL,      -- one of NOTE_KINDS
        text      VARCHAR NOT NULL,
        tags      VARCHAR,               -- comma separated, lower case
        source    VARCHAR NOT NULL       -- seed | agent | owner
    )""",
    """CREATE TABLE IF NOT EXISTS agent_audit (
        called_at TIMESTAMPTZ NOT NULL,
        tool      VARCHAR NOT NULL,
        args_json VARCHAR NOT NULL,
        ok        BOOLEAN NOT NULL,
        result_sha VARCHAR,              -- sha256 of the result text (what the AI was shown)
        error     VARCHAR,
        ms        INTEGER NOT NULL
    )""",
)


@dataclass(frozen=True, slots=True)
class Note:
    note_id: int
    created_at: datetime
    kind: str
    text: str
    tags: str | None
    source: str


class AgentStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @contextmanager
    def _conn(self, read_only: bool = False) -> Iterator[duckdb.DuckDBPyConnection]:
        if not self.path.exists():
            self.migrate()
        conn = duckdb.connect(str(self.path), read_only=read_only)
        try:
            yield conn
        finally:
            conn.close()

    def migrate(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = duckdb.connect(str(self.path))
        try:
            for ddl in _DDL:
                conn.execute(ddl)
        finally:
            conn.close()

    # --- notebook -----------------------------------------------------------------------------

    def add_note(self, kind: str, text: str, tags: str | None = None, source: str = "agent") -> int:
        kind = kind.strip().lower()
        if kind not in NOTE_KINDS:
            raise ValueError(f"kind must be one of {', '.join(NOTE_KINDS)}")
        text = text.strip()
        if not text:
            raise ValueError("text is empty")
        if len(text) > MAX_NOTE_CHARS:
            raise ValueError(f"text is longer than {MAX_NOTE_CHARS} characters; split it")
        clean_tags = ",".join(t.strip().lower() for t in (tags or "").split(",") if t.strip())
        with self._conn() as c:
            row = c.execute(
                "INSERT INTO agent_notes (created_at, kind, text, tags, source)"
                " VALUES (?, ?, ?, ?, ?) RETURNING note_id",
                [datetime.now(UTC), kind, text, clean_tags or None, source],
            ).fetchone()
        assert row is not None
        return int(row[0])

    def notes(
        self, text: str | None = None, kind: str | None = None, tag: str | None = None,
        limit: int = 20,
    ) -> list[Note]:  # fmt: skip
        """Newest first. ``text`` matches words anywhere in the note (all must match)."""
        where: list[str] = ["1 = 1"]
        args: list[object] = []
        for word in (text or "").split():
            where.append("lower(text) LIKE ?")
            args.append(f"%{word.lower()}%")
        if kind:
            where.append("kind = ?")
            args.append(kind.lower())
        if tag:
            where.append("(',' || coalesce(tags, '') || ',') LIKE ?")
            args.append(f"%,{tag.lower()},%")
        args.append(max(1, min(limit, 200)))
        with self._conn(read_only=True) as c:
            rows = c.execute(
                "SELECT note_id, created_at, kind, text, tags, source FROM agent_notes"
                f" WHERE {' AND '.join(where)} ORDER BY note_id DESC LIMIT ?",
                args,
            ).fetchall()
        return [Note(int(r[0]), r[1], str(r[2]), str(r[3]), r[4], str(r[5])) for r in rows]

    def has_seed(self, text: str) -> bool:
        with self._conn(read_only=True) as c:
            row = c.execute(
                "SELECT count(*) FROM agent_notes WHERE source = 'seed' AND text = ?", [text]
            ).fetchone()
        return bool(row and row[0])

    # --- audit --------------------------------------------------------------------------------

    def audit(
        self, tool: str, args: dict[str, Any], ok: bool, result: str | None, error: str | None,
        ms: int,
    ) -> None:  # fmt: skip
        sha = None if result is None else hashlib.sha256(result.encode()).hexdigest()
        with self._conn() as c:
            c.execute(
                "INSERT INTO agent_audit VALUES (?, ?, ?, ?, ?, ?, ?)",
                [datetime.now(UTC), tool, json.dumps(args, default=str, sort_keys=True), ok, sha,
                 error, ms],
            )  # fmt: skip

    def audit_rows(self, limit: int = 50) -> list[tuple[Any, ...]]:
        with self._conn(read_only=True) as c:
            return c.execute(
                "SELECT called_at, tool, args_json, ok, error, ms FROM agent_audit"
                " ORDER BY called_at DESC LIMIT ?",
                [limit],
            ).fetchall()
