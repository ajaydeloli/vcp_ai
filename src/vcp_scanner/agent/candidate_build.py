"""Building, approving and retiring strategy candidates (AGENT_SPECIFICATION §1.4, §5; step A4).

* ``build`` (a background process started by ``propose_candidate``): writes the candidate file
  on a branch ``candidate/<id>`` in a separate git worktree (``<repo>-candidates/<id>``), so the
  owner's working tree is never touched; runs the checks; runs the scan diff on a copy of the
  newest backup (never the main database); then the proposal is PENDING, or BUILD_FAILED with the
  reason.
* ``apply`` (``vcp agent approve``, owner only): merges the branch into ``main`` locally (not
  pushed), sets the candidate active from the approval day and commits that; the daily run
  shadows it from the same evening.
* ``reject`` / ``retire``: the branch is kept (nothing is deleted); the worktree is removed.
"""

from __future__ import annotations

import contextlib
import json
import shutil
import subprocess
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb

from vcp_scanner.agent import candidates as C

DDL = (
    """CREATE TABLE IF NOT EXISTS strategy_candidates (
        candidate_id      VARCHAR PRIMARY KEY,
        proposal_id       BIGINT NOT NULL,
        strategy_id       VARCHAR NOT NULL,
        base_version      VARCHAR NOT NULL,
        candidate_version VARCHAR NOT NULL,
        status            VARCHAR NOT NULL,   -- proposed | active | rejected | retired
        branch            VARCHAR,
        commit_sha        VARCHAR,
        frozen_hash       VARCHAR,
        candidate_hash    VARCHAR,
        start_date        DATE,
        diff_json         VARCHAR,            -- scan diff summary
        updated_at        TIMESTAMPTZ NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS candidate_scan_diff (
        candidate_id    VARCHAR NOT NULL,
        as_of_date      DATE NOT NULL,
        instrument_id   VARCHAR NOT NULL,
        symbol          VARCHAR NOT NULL,
        change          VARCHAR NOT NULL,     -- ADDED | DROPPED | RECLASSIFIED | RESCORED
        frozen_class    VARCHAR,
        candidate_class VARCHAR,
        frozen_score    DOUBLE,
        candidate_score DOUBLE,
        PRIMARY KEY (candidate_id, as_of_date, instrument_id)
    )""",
)


def migrate(agent_db: Path) -> None:
    conn = duckdb.connect(str(agent_db))
    try:
        for ddl in DDL:
            conn.execute(ddl)
    finally:
        conn.close()


def _git(root: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    if done.returncode:
        raise RuntimeError(
            f"git {' '.join(args[:2])}: {(done.stderr or done.stdout).strip()[:300]}"
        )
    return done.stdout.strip()


def worktree_dir(root: Path, candidate_id: str) -> Path:
    return root.parent / f"{root.name}-candidates" / candidate_id


def branch_name(candidate_id: str) -> str:
    return f"candidate/{candidate_id}"


def newest_backup(backup_dir: Path) -> Path:
    found = sorted(backup_dir.glob("*.duckdb"), key=lambda p: p.stat().st_mtime)
    if not found:
        raise RuntimeError(f"no backup in {backup_dir} to run the scan diff on")
    return found[-1]


def _record(agent_db: Path, cand: C.Candidate, *, branch: str | None, commit: str | None,
            frozen_hash: str | None, cand_hash: str | None, diff: dict[str, Any] | None,
            status: str) -> None:  # fmt: skip
    migrate(agent_db)
    conn = duckdb.connect(str(agent_db))
    try:
        conn.execute(
            "INSERT OR REPLACE INTO strategy_candidates VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
            " ?)",
            [cand.candidate_id, cand.proposal_id, cand.strategy_id, cand.base_version,
             cand.candidate_version, status, branch, commit, frozen_hash, cand_hash,
             cand.start_date, None if diff is None else json.dumps(diff, default=str),
             datetime.now(UTC)],
        )  # fmt: skip
    finally:
        conn.close()


def _save_diff(agent_db: Path, candidate_id: str, rows: list[dict[str, Any]]) -> None:
    migrate(agent_db)
    conn = duckdb.connect(str(agent_db))
    try:
        conn.execute("BEGIN TRANSACTION")
        conn.execute("DELETE FROM candidate_scan_diff WHERE candidate_id = ?", [candidate_id])
        if rows:
            conn.executemany(
                "INSERT INTO candidate_scan_diff VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(candidate_id, r["as_of_date"], r["instrument_id"], r["symbol"], r["change"],
                  r["frozen_class"], r["candidate_class"], r["frozen_score"],
                  r["candidate_score"]) for r in rows],
            )  # fmt: skip
        conn.execute("COMMIT")
    finally:
        conn.close()


def candidate_from_payload(payload: dict[str, Any], proposal_id: int | None,
                           status: str = "proposed") -> C.Candidate:  # fmt: skip
    return C.Candidate(
        candidate_id=payload["candidate_id"], strategy_id=payload["strategy_id"],
        base_version=payload["base_version"], candidate_version=payload["candidate_version"],
        hypothesis_id=int(payload["hypothesis_id"]), proposal_id=proposal_id, status=status,
        start_date=None,
        overrides=[C.Override(o["file"], o["path"], o["old"], o["new"])
                   for o in payload["overrides"]],
        spec=dict(payload.get("spec", {})),
    )  # fmt: skip


def scan_diff_on_copy(cand: C.Candidate, frozen_dir: Path, cand_dir: Path, backup: Path,
                      work: Path, cli_main: Callable[[list[str]], int]) -> tuple[
                          list[dict[str, Any]], dict[str, Any]]:  # fmt: skip
    """Scan the stored sessions since the paper start with the candidate config on a copy of
    the newest backup, and compare with the frozen scans of the same days. The copy is removed
    afterwards; the main database is never opened for writing."""
    work.mkdir(parents=True, exist_ok=True)
    copy = work / "scandiff.duckdb"
    shutil.copyfile(backup, copy)
    try:
        frozen_hash = C.strategy_hash(frozen_dir, cand.strategy_id)
        cand_hash = C.strategy_hash(cand_dir, cand.strategy_id)
        conn = duckdb.connect(str(copy), read_only=True)
        try:
            days = C.scanned_days(conn, cand.strategy_id, frozen_hash, C.PAPER_START)
        finally:
            conn.close()
        failed = C.run_scans(cli_main, cand.strategy_id, days, str(copy), str(cand_dir))
        if failed:
            raise RuntimeError(f"the candidate scan failed: {failed[0]}")
        conn = duckdb.connect(str(copy), read_only=True)
        try:
            rows = C.scan_diff(conn, cand.strategy_id, frozen_hash, cand_hash, days)
            counts = {d: int(conn.execute(
                "SELECT count(*) FROM setup_scores WHERE strategy_id = ? AND config_hash = ?"
                " AND data_snapshot_id = 'LIVE' AND as_of_date = ? AND eligible",
                [cand.strategy_id, frozen_hash, d]).fetchone()[0])  # type: ignore[index]
                for d in days}  # fmt: skip
        finally:
            conn.close()
        return rows, {**C.diff_summary(rows, days, counts), "backup": backup.name}
    finally:
        copy.unlink(missing_ok=True)


def build(proposals: Any, proposal_id: int, root: Path, config_dir: str,
          cli_main: Callable[[list[str]], int], backup_dir: Path,
          work_dir: Path = Path("data/agent/candidates")) -> str:  # fmt: skip
    """The background build of one candidate proposal (module docstring). Returns a line."""
    p = proposals.get(proposal_id)
    if p.kind != "candidate" or p.status != "BUILDING":
        raise ValueError(f"P{proposal_id} is not a candidate being built ({p.kind}, {p.status})")
    payload = dict(p.payload)
    cand = candidate_from_payload(payload, proposal_id)
    cid = cand.candidate_id
    agent_db = proposals.store.path
    try:
        base = _git(root, "rev-parse", "main")
        wt = worktree_dir(root, cid)
        if not wt.exists():
            wt.parent.mkdir(parents=True, exist_ok=True)
            _git(root, "worktree", "add", "-b", branch_name(cid), str(wt), base)
        path = C.candidate_path(wt / config_dir, cid)
        C.write_file(path, cand)
        _git(wt, "add", "--", str(path.relative_to(wt)))
        _git(
            wt,
            "commit",
            "-m",
            f"Strategy candidate {cid} ({cand.candidate_version}; proposal "
            f"P{proposal_id}, hypothesis H{cand.hypothesis_id})\n\n{cand.spec.get('why', '')}",
        )
        commit = _git(wt, "rev-parse", "HEAD")
        cand_dir = C.materialize(wt / config_dir, cand.overrides, work_dir / cid / "config")
        checks = C.check(wt / config_dir, cand_dir, cand.strategy_id, cand.paper_rules_only)
        main_hash = C.strategy_hash(root / config_dir, cand.strategy_id)
        if checks["frozen_hash"] != main_hash:
            raise C.CandidateError("the branch changes the frozen config hash")
        if cand.paper_rules_only:
            rows: list[dict[str, Any]] = []
            diff: dict[str, Any] = {
                "paper_rules_only": True,
                "changes": {},
                "note": "the scan is unchanged; only the paper rules differ",
            }
        else:
            rows, diff = scan_diff_on_copy(cand, wt / config_dir, cand_dir,
                                           newest_backup(backup_dir), work_dir / cid,
                                           cli_main)  # fmt: skip
        _save_diff(agent_db, cid, rows)
        _record(agent_db, cand, branch=branch_name(cid), commit=commit,
                frozen_hash=checks["frozen_hash"], cand_hash=checks["candidate_hash"],
                diff=diff, status="proposed")  # fmt: skip
        payload.update(branch=branch_name(cid), commit=commit, checks=checks, scan_diff=diff)
        proposals.set_build(proposal_id, True, payload, "built and checked")
        return f"P{proposal_id} {cid}: built, checked, scan diff {diff.get('changes')}"
    except Exception as exc:
        proposals.set_build(proposal_id, False, payload, f"{type(exc).__name__}: {exc}")
        return f"P{proposal_id} {cid}: BUILD FAILED {exc}"


def apply(proposals: Any, p: Any, root: Path, config_dir: str, today: date) -> str:
    """Approved: merge the branch into main locally and start the shadow ledger today."""
    cand = candidate_from_payload(p.payload, p.proposal_id)
    cid = cand.candidate_id
    if _git(root, "rev-parse", "--abbrev-ref", "HEAD") != "main":
        raise RuntimeError("check out main first: the candidate is merged into main")
    _git(root, "merge", "--no-ff", "-m", f"Strategy candidate {cid} approved (P{p.proposal_id})",
         branch_name(cid))  # fmt: skip
    path = C.candidate_path(root / config_dir, cid)
    cand = C.read_file(path)
    cand.status, cand.start_date = "active", C.shadow_start(today)
    C.write_file(path, cand)
    rel = str(path.relative_to(root))
    _git(root, "add", "--", rel)
    _git(root, "commit", "-m", f"Strategy candidate {cid} active from {cand.start_date}", "--", rel)
    _record(proposals.store.path, cand, branch=branch_name(cid),
            commit=_git(root, "rev-parse", "HEAD"), frozen_hash=p.payload.get("checks", {}).get(
                "frozen_hash"), cand_hash=p.payload.get("checks", {}).get("candidate_hash"),
            diff=p.payload.get("scan_diff"), status="active")  # fmt: skip
    _remove_worktree(root, cid)
    return (f"merged {branch_name(cid)} into main (not pushed); shadow ledger from "
            f"{cand.start_date}; the daily run scans it from tonight")  # fmt: skip


def _remove_worktree(root: Path, cid: str) -> None:
    wt = worktree_dir(root, cid)
    if wt.exists():
        # Left for the owner if it fails; the branch holds the work either way.
        with contextlib.suppress(RuntimeError):
            _git(root, "worktree", "remove", "--force", str(wt))


def reject(proposals: Any, p: Any, root: Path) -> None:
    cand = candidate_from_payload(p.payload, p.proposal_id)
    _record(proposals.store.path, cand, branch=p.payload.get("branch"),
            commit=p.payload.get("commit"), frozen_hash=None, cand_hash=None,
            diff=p.payload.get("scan_diff"), status="rejected")  # fmt: skip
    _remove_worktree(root, cand.candidate_id)


def retire(root: Path, config_dir: str, candidate_id: str, by: str, reason: str,
           agent_db: Path) -> str:  # fmt: skip
    """Stop shadowing a candidate (owner only). Its shadow rows stay."""
    path = C.candidate_path(root / config_dir, candidate_id)
    if not path.exists():
        raise ValueError(f"no candidate {candidate_id} in {path.parent}")
    cand = C.read_file(path)
    if cand.status != "active":
        raise ValueError(f"{candidate_id} is {cand.status}, not active")
    cand.status = "retired"
    C.write_file(path, cand)
    rel = str(path.relative_to(root))
    _git(root, "add", "--", rel)
    _git(root, "commit", "-m", f"Strategy candidate {candidate_id} retired by {by}: {reason}",
         "--", rel)  # fmt: skip
    _record(agent_db, cand, branch=branch_name(candidate_id), commit=None, frozen_hash=None,
            cand_hash=None, diff=None, status="retired")  # fmt: skip
    return f"{candidate_id} retired; its shadow ledger stays for the record"
