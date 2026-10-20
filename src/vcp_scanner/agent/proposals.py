"""Proposals (AGENT_SPECIFICATION §3 tier 2, §5, step A3): the AI proposes, the owner decides.

The MCP server can only *create* proposals and read their state. Approving, rejecting and
applying happen in ``vcp agent approve|reject`` (and later the dashboard), which refuse to run
without an interactive terminal: Claude Code's shell is not one, so an AI cannot approve its own
proposal even though it can run commands. Applying uses the same paths the owner uses by hand:

* ``corporate_action``: one entry appended to ``config/manual_corporate_actions.yaml``
  (validated by its own model), committed to git; the next daily run applies it;
* ``resolve_event``: ``vcp quality resolve <event> --by <owner> --note ...``;
* ``sector``: one entry appended to ``config/manual_sectors.yaml``, committed to git;
* ``watchlist``: the AI watchlist in the agent's own database. Pre-approved by the owner
  (2026-10-19): applied when proposed, still recorded and reversible.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb

from vcp_scanner.agent.store import AgentStore

KINDS = ("corporate_action", "resolve_event", "sector", "watchlist")
PRE_APPROVED = ("watchlist",)
STATUSES = ("PENDING", "APPROVED", "REJECTED", "APPLIED", "FAILED")
MAX_TEXT = 2000

_DDL = (
    "CREATE SEQUENCE IF NOT EXISTS agent_proposals_seq",
    """CREATE TABLE IF NOT EXISTS agent_proposals (
        proposal_id  BIGINT PRIMARY KEY DEFAULT nextval('agent_proposals_seq'),
        created_at   TIMESTAMPTZ NOT NULL,
        kind         VARCHAR NOT NULL,
        title        VARCHAR NOT NULL,
        payload_json VARCHAR NOT NULL,
        rationale    VARCHAR NOT NULL,
        evidence     VARCHAR NOT NULL,
        status       VARCHAR NOT NULL,     -- PENDING | APPROVED | REJECTED | APPLIED | FAILED
        decided_by   VARCHAR,
        decided_at   TIMESTAMPTZ,
        comment      VARCHAR,              -- the owner's words; the AI reads them
        applied_at   TIMESTAMPTZ,
        apply_result VARCHAR
    )""",
    """CREATE TABLE IF NOT EXISTS agent_watchlist (
        symbol      VARCHAR PRIMARY KEY,
        added_at    TIMESTAMPTZ NOT NULL,
        proposal_id BIGINT NOT NULL,
        reason      VARCHAR NOT NULL
    )""",
)


def _today() -> date:
    from vcp_scanner.data.providers._time import IST

    return datetime.now(IST).date()


class ProposalError(ValueError):
    """A proposal or decision refused, said plainly."""


@dataclass(frozen=True, slots=True)
class Proposal:
    proposal_id: int
    created_at: datetime
    kind: str
    title: str
    payload: dict[str, Any]
    rationale: str
    evidence: str
    status: str
    decided_by: str | None
    decided_at: datetime | None
    comment: str | None
    applied_at: datetime | None
    apply_result: str | None


def _text(name: str, v: str, minimum: int = 1) -> str:
    v = (v or "").strip()
    if len(v) < minimum:
        raise ProposalError(f"{name} is required (at least {minimum} characters)")
    if len(v) > MAX_TEXT:
        raise ProposalError(f"{name} is longer than {MAX_TEXT} characters")
    return v


def _yaml_scalar(v: Any) -> str:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return repr(v)
    s = str(v)
    return json.dumps(s, ensure_ascii=False)  # a JSON string is a valid YAML scalar


class Proposals:
    def __init__(self, store: AgentStore, repo_root: Path, config_dir: str = "config") -> None:
        self.store = store
        self.root = repo_root
        self.config_dir = Path(config_dir)

    @contextmanager
    def _conn(self, read_only: bool = False) -> Iterator[duckdb.DuckDBPyConnection]:
        self.migrate()
        conn = duckdb.connect(str(self.store.path), read_only=read_only)
        try:
            yield conn
        finally:
            conn.close()

    def migrate(self) -> None:
        if not self.store.path.exists():
            self.store.migrate()
        conn = duckdb.connect(str(self.store.path))
        try:
            for ddl in _DDL:
                conn.execute(ddl)
        finally:
            conn.close()

    # --- validation of each kind (also re-run when applying) ------------------------------

    def _check(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        if kind == "corporate_action":
            from vcp_scanner.data.providers.manual_ca import ManualActionEntry

            entry = {**payload, "approved_by": "OWNER", "entered_on": _today()}
            try:
                ManualActionEntry.model_validate(entry)
            except Exception as exc:
                raise ProposalError(f"corporate action invalid: {exc}") from exc
            if len(str(payload.get("evidence", ""))) < 30:
                raise ProposalError("evidence must say where the action is documented "
                                    "(notice URL or source) and what the prices show")  # fmt: skip
            return payload
        if kind == "resolve_event":
            eid = str(payload.get("event_id", "")).strip()
            if not eid.startswith("dq-"):
                raise ProposalError("event_id must be a data-quality event id (dq-...)")
            _text("note", str(payload.get("note", "")), 20)
            return {"event_id": eid, "note": str(payload["note"]).strip()}
        if kind == "sector":
            from vcp_scanner.data.providers.manual_sectors import NSE_SECTORS

            sym = str(payload.get("symbol", "")).strip().upper()
            sector = str(payload.get("sector", "")).strip()
            if not sym:
                raise ProposalError("symbol is required")
            if sector not in NSE_SECTORS:
                raise ProposalError(
                    f"sector must be one of NSE's: {', '.join(sorted(NSE_SECTORS))}"
                )
            out: dict[str, Any] = {"symbol": sym, "sector": sector}
            if payload.get("industry"):
                out["industry"] = str(payload["industry"]).strip()
            return out
        if kind == "watchlist":
            add = sorted({str(s).strip().upper() for s in payload.get("add", []) if str(s).strip()})
            remove = sorted({str(s).strip().upper() for s in payload.get("remove", [])
                             if str(s).strip()})  # fmt: skip
            if not add and not remove:
                raise ProposalError("watchlist: give symbols to add or remove")
            if len(add) + len(remove) > 30:
                raise ProposalError("watchlist: at most 30 symbols per proposal")
            return {"add": add, "remove": remove}
        raise ProposalError(f"kind must be one of {', '.join(KINDS)}")

    # --- create and read -------------------------------------------------------------------

    def create(self, kind: str, title: str, payload: dict[str, Any], rationale: str,
               evidence: str) -> Proposal:  # fmt: skip
        kind = kind.strip().lower()
        clean = self._check(kind, payload)
        with self._conn() as c:
            row = c.execute(
                "INSERT INTO agent_proposals (created_at, kind, title, payload_json, rationale,"
                " evidence, status) VALUES (?, ?, ?, ?, ?, ?, 'PENDING') RETURNING proposal_id",
                [datetime.now(UTC), kind, _text("title", title), json.dumps(clean, default=str),
                 _text("rationale", rationale, 10), _text("evidence", evidence, 10)],
            ).fetchone()  # fmt: skip
        assert row is not None
        pid = int(row[0])
        self.store.add_note("note", f"Proposal P{pid} ({kind}): {title.strip()}",
                            tags=f"p{pid},proposal,{kind}")  # fmt: skip
        if kind in PRE_APPROVED:
            self._decide(pid, "APPROVED", "pre-approved (owner decision 2026-10-19)", None)
            self.apply(pid)
        return self.get(pid)

    def get(self, proposal_id: int) -> Proposal:
        found = self.find(proposal_id=proposal_id)
        if not found:
            raise ProposalError(f"no proposal P{proposal_id}")
        return found[0]

    def find(self, status: str | None = None, proposal_id: int | None = None,
             limit: int = 50) -> list[Proposal]:  # fmt: skip
        where: list[str] = ["1 = 1"]
        args: list[Any] = []
        if status:
            where.append("status = upper(?)")
            args.append(status)
        if proposal_id is not None:
            where.append("proposal_id = ?")
            args.append(proposal_id)
        args.append(max(1, min(limit, 200)))
        with self._conn(read_only=True) as c:
            rows = c.execute(
                "SELECT proposal_id, created_at, kind, title, payload_json, rationale, evidence,"
                " status, decided_by, decided_at, comment, applied_at, apply_result"
                f" FROM agent_proposals WHERE {' AND '.join(where)}"
                " ORDER BY proposal_id DESC LIMIT ?",
                args,
            ).fetchall()
        return [Proposal(int(r[0]), r[1], str(r[2]), str(r[3]), json.loads(r[4]), *r[5:])
                for r in rows]  # fmt: skip

    def watchlist(self) -> list[tuple[str, datetime, int, str]]:
        with self._conn(read_only=True) as c:
            return [(str(r[0]), r[1], int(r[2]), str(r[3])) for r in c.execute(
                "SELECT symbol, added_at, proposal_id, reason FROM agent_watchlist"
                " ORDER BY symbol").fetchall()]  # fmt: skip

    # --- decisions (owner only: called from the CLI, never from the MCP server) --------------

    def _decide(self, pid: int, status: str, by: str, comment: str | None) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE agent_proposals SET status = ?, decided_by = ?, decided_at = ?,"
                " comment = ? WHERE proposal_id = ?",
                [status, by, datetime.now(UTC), comment, pid],
            )

    def approve(self, proposal_id: int, by: str, comment: str | None = None) -> Proposal:
        p = self.get(proposal_id)
        if p.status != "PENDING":
            raise ProposalError(f"P{proposal_id} is {p.status}, not PENDING")
        by = _text("approver", by)
        self._decide(proposal_id, "APPROVED", by, comment)
        self.store.add_note("decision", f"P{proposal_id} approved by {by}: {p.title}"
                            + (f" ({comment})" if comment else ""),
                            tags=f"p{proposal_id},proposal", source="owner")  # fmt: skip
        return self.apply(proposal_id)

    def reject(self, proposal_id: int, by: str, comment: str) -> Proposal:
        p = self.get(proposal_id)
        if p.status != "PENDING":
            raise ProposalError(f"P{proposal_id} is {p.status}, not PENDING")
        comment = _text("comment (why it is rejected: the AI reads it)", comment, 3)
        self._decide(proposal_id, "REJECTED", _text("approver", by), comment)
        self.store.add_note("decision", f"P{proposal_id} rejected by {by}: {p.title}. "
                            f"Reason: {comment}", tags=f"p{proposal_id},proposal",
                            source="owner")  # fmt: skip
        return self.get(proposal_id)

    # --- applying ----------------------------------------------------------------------------

    def _finish(self, pid: int, ok: bool, result: str) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE agent_proposals SET status = ?, applied_at = ?, apply_result = ?"
                " WHERE proposal_id = ?",
                ["APPLIED" if ok else "FAILED", datetime.now(UTC), result[:MAX_TEXT], pid],
            )

    def apply(self, proposal_id: int) -> Proposal:
        p = self.get(proposal_id)
        if p.status != "APPROVED":
            raise ProposalError(f"P{proposal_id} is {p.status}; only an approved one is applied")
        try:
            payload = self._check(p.kind, p.payload)  # re-checked: files may have changed
            result = getattr(self, f"_apply_{p.kind}")(p, payload)
            self._finish(proposal_id, True, result)
        except Exception as exc:
            self._finish(proposal_id, False, f"{type(exc).__name__}: {exc}")
        return self.get(proposal_id)

    def _git_commit(self, path: Path, message: str) -> str:
        rel = path.resolve().relative_to(self.root.resolve())
        add = subprocess.run(["git", "add", "--", str(rel)], cwd=self.root,
                             capture_output=True, text=True)  # fmt: skip
        done = subprocess.run(["git", "commit", "-m", message, "--", str(rel)], cwd=self.root,
                              capture_output=True, text=True)  # fmt: skip
        if add.returncode or done.returncode:
            return f"written; not committed ({(done.stderr or add.stderr).strip()[:200]})"
        return "committed to git (not pushed)"

    def _apply_corporate_action(self, p: Proposal, payload: dict[str, Any]) -> str:
        path = self.config_dir / "manual_corporate_actions.yaml"
        if not path.is_absolute():
            path = self.root / path
        entry = {**payload, "approved_by": p.decided_by, "entered_on": _today().isoformat()}
        lines = ["", f"  # Proposal P{p.proposal_id}, approved by {p.decided_by}"]
        first = True
        for key in ("symbol", "isin", "action_type", "ex_date", "ratio", "cash_amount",
                    "price_factor", "reduction_kind", "shares_before", "shares_after",
                    "evidence", "approved_by", "entered_on"):  # fmt: skip
            if entry.get(key) is None:
                continue
            v = entry[key]
            text = (f"[{', '.join(_yaml_scalar(x) for x in v)}]" if isinstance(v, (list, tuple))
                    else _yaml_scalar(v) if key == "evidence" else str(v))  # fmt: skip
            lines.append(f"  {'- ' if first else '  '}{key}: {text}")
            first = False
        with path.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        from vcp_scanner.data.providers.manual_ca import load_manual_entries

        load_manual_entries(path)  # the whole file must still load
        git = self._git_commit(path, f"Manual corporate action {payload['symbol']} "
                               f"{payload['action_type']} ex {payload['ex_date']} (proposal "
                               f"P{p.proposal_id}, approved by {p.decided_by})")  # fmt: skip
        return f"entry added to {path.name}; {git}; the next daily run applies it"

    def _apply_resolve_event(self, p: Proposal, payload: dict[str, Any]) -> str:
        exe = Path(sys.executable).parent / "vcp"
        done = subprocess.run(
            [str(exe) if exe.exists() else "vcp", "quality", "resolve", payload["event_id"],
             "--by", str(p.decided_by), "--note", f"{payload['note']} (proposal "
             f"P{p.proposal_id})"],
            cwd=self.root, capture_output=True, text=True, timeout=300,
        )  # fmt: skip
        out = (done.stdout + done.stderr).strip().splitlines()
        last = next((ln for ln in reversed(out) if " INFO " not in ln), "no output")
        if done.returncode != 0:
            raise ProposalError(last)
        return last

    def _apply_sector(self, p: Proposal, payload: dict[str, Any]) -> str:
        path = self.config_dir / "manual_sectors.yaml"
        if not path.is_absolute():
            path = self.root / path
        text = path.read_text(encoding="utf-8")
        if f"symbol: {payload['symbol']}," in text:
            raise ProposalError(f"{payload['symbol']} already has a manual sector; edit it by hand")
        parts = [f"symbol: {payload['symbol']}", f"sector: {payload['sector']}"]
        if payload.get("industry"):
            parts.append(f"industry: {_yaml_scalar(payload['industry'])}")
        parts.append(f"as_of: {_today().isoformat()}")
        with path.open("a", encoding="utf-8") as f:
            f.write("  - {" + ", ".join(parts) + "}\n")
        from vcp_scanner.data.providers.manual_sectors import load_manual_sectors

        _entries, problems = load_manual_sectors(path)
        if problems:
            raise ProposalError(f"manual_sectors.yaml no longer loads cleanly: {problems[:2]}")
        git = self._git_commit(path, f"Manual sector {payload['symbol']} (proposal "
                               f"P{p.proposal_id}, approved by {p.decided_by})")  # fmt: skip
        return f"entry added to {path.name}; {git}; applied by `vcp classify update` (Fridays)"

    def _apply_watchlist(self, p: Proposal, payload: dict[str, Any]) -> str:
        now = datetime.now(UTC)
        with self._conn() as c:
            for sym in payload["remove"]:
                c.execute("DELETE FROM agent_watchlist WHERE symbol = ?", [sym])
            for sym in payload["add"]:
                c.execute(
                    "INSERT OR REPLACE INTO agent_watchlist VALUES (?, ?, ?, ?)",
                    [sym, now, p.proposal_id, p.title],
                )
        return f"AI watchlist: added {len(payload['add'])}, removed {len(payload['remove'])}"
