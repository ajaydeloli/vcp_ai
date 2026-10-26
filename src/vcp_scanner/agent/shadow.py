"""Shadow paper ledger of active strategy candidates (AGENT_SPECIFICATION §1.2, §6; step A4).

Run by the daily run after the frozen paper ledger (``vcp agent shadow update``). For each
active candidate: scan the sessions the frozen run has scanned since the candidate started, with
the candidate config, under its own hash (stored data only); then replay the paper rules (the
candidate's, when it changes them) from its start date and append to ``shadow_paper_events``,
exactly as the frozen ledger does (``paper.ledger``: append-only, a recomputed difference is one
DIVERGENCE row). The frozen ledger, its config and its views never read these rows. A candidate
failing never stops the others, and the daily run reports a shadow failure without failing.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from vcp_scanner.agent.candidates import (
    Candidate,
    LedgerStats,
    compare,
    ledger_stats,
    load_candidates,
    materialize,
    run_scans,
    scanned_days,
    strategy_hash,
)
from vcp_scanner.paper.ledger import RULE_SET, PaperEvent

SHADOW_RULE_SET = "shadow-v1"  # the paper-v1 rules, or the candidate's own paper rules
WORK_DIR = Path("data/agent/candidates")
_COLS = ("event_id", "candidate_id", "rule_set", "strategy_id", "config_hash", "instrument_id",
         "event_date", "event_type", "price", "scan_date", "metadata_json", "code_commit",
         "recorded_at")  # fmt: skip


class ShadowRepository:
    """``shadow_paper_events``: insert-only, like the frozen ``paper_events``."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def events(self, candidate_id: str) -> list[PaperEvent]:
        rows = self._conn.execute(
            "SELECT strategy_id, instrument_id, event_date, event_type, price, scan_date,"
            " metadata_json FROM shadow_paper_events WHERE candidate_id = ?"
            " ORDER BY event_date, recorded_at, event_id",
            [candidate_id],
        ).fetchall()
        return [PaperEvent(r[0], r[1], r[2], r[3], r[4], r[5], json.loads(r[6] or "{}"))
                for r in rows]  # fmt: skip

    def append(self, candidate_id: str, config_hash: str, events: Sequence[PaperEvent],
               code_commit: str | None, now: datetime | None = None) -> int:  # fmt: skip
        if not events:
            return 0
        at = now or datetime.now(UTC)
        key = f"{config_hash}:{candidate_id}"
        rows = [(e.event_id(SHADOW_RULE_SET, key), candidate_id, SHADOW_RULE_SET, e.strategy_id,
                 config_hash, e.instrument_id, e.event_date, e.event_type, e.price, e.scan_date,
                 json.dumps(e.meta, sort_keys=True, default=str) if e.meta else None,
                 code_commit, at) for e in events]  # fmt: skip
        before = self._conn.execute("SELECT count(*) FROM shadow_paper_events").fetchone()[0]
        marks = ", ".join("?" * len(_COLS))
        self._conn.execute("BEGIN TRANSACTION")
        try:
            self._conn.executemany(f"INSERT OR IGNORE INTO shadow_paper_events ({', '.join(_COLS)})"
                                   f" VALUES ({marks})", rows)  # fmt: skip
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        after = self._conn.execute("SELECT count(*) FROM shadow_paper_events").fetchone()[0]
        return int(after - before)


@dataclass(frozen=True, slots=True)
class ShadowResult:
    candidate_id: str
    ok: bool
    text: str


def _ledger(store: Any, frozen_dir: str, cand: Candidate, cand_dir: Path, cand_hash: str,
            through: date, code: str | None) -> str:  # fmt: skip
    """Replay the candidate's paper rules from its start through ``through`` and append."""
    from vcp_scanner.backtest.engine import EngineConfig
    from vcp_scanner.backtest.periods import load_backtest_config
    from vcp_scanner.backtest.regime import regime_by_day
    from vcp_scanner.config import load_scanner_config
    from vcp_scanner.config.loader import scan_config_hash
    from vcp_scanner.data.repositories.duckdb_backtest_repository import (
        DuckDBBacktestRepository,
    )
    from vcp_scanner.domain.features import FEATURES_CALCULATION_VERSION
    from vcp_scanner.domain.snapshot import LIVE_SNAPSHOT_ID
    from vcp_scanner.paper.ledger import derive_events, plan_update
    from vcp_scanner.patterns.registry import load_runtime
    from vcp_scanner.research.outcomes import ENGINE_RULES

    assert cand.start_date is not None
    start = cand.start_date
    cfg = load_scanner_config(str(cand_dir))
    frozen_cfg = load_scanner_config(frozen_dir)
    d = load_backtest_config(str(cand_dir)).defaults
    repo = DuckDBBacktestRepository(store, LIVE_SNAPSHOT_ID)
    # The market regime is not a strategy setting: breadth from the frozen universe scans.
    rows = repo.breadth(start, through, scan_config_hash(frozen_cfg), FEATURES_CALCULATION_VERSION)
    days = [r.day for r in rows if start <= r.day <= through]
    rule = next(r for r in ENGINE_RULES if r.name == d.rule)
    ecfg = EngineConfig(rule, min_volume_ratio=cfg.strategy.vcp.breakout.min_volume_ratio,
                        entry=d.entry, regime=regime_by_day(d.regime, rows))  # fmt: skip
    rt = load_runtime(str(cand_dir), cand.strategy_id)
    classes = [t for t in rt.tiers if rt.grade(t) >= rt.min_grade]
    signals = repo.signals(start - timedelta(days=7), through, cand_hash, classes, None,
                           eligible_only=True, strategy_id=cand.strategy_id)  # fmt: skip
    bars = repo.bars(sorted({s.instrument_id for s in signals}), start, through, 0)
    derived = derive_events(cand.strategy_id, signals, bars, ecfg, start, through, days)
    shadow = ShadowRepository(store.conn)
    plan = plan_update(cand.strategy_id, derived, shadow.events(cand.candidate_id), through)
    extra = [plan.divergence] if plan.divergence else []
    n = shadow.append(cand.candidate_id, cand_hash, [*plan.new, *extra], code)
    kinds: dict[str, int] = {}
    for ev in plan.new:
        kinds[ev.event_type] = kinds.get(ev.event_type, 0) + 1
    trades = ", ".join(f"{k} {v}" for k, v in sorted(kinds.items()) if k != "DAY_CLOSED")
    note = "; DIVERGENCE recorded" if plan.divergence else ""
    return f"{n} rows ({trades or 'no trades'}){note}"


def update(db: str, config_dir: str, cli_main: Callable[[list[str]], int],
           work_dir: Path = WORK_DIR) -> list[ShadowResult]:  # fmt: skip
    """Scan and ledger every active candidate (module docstring)."""
    from vcp_scanner.cli_pipeline import _open_store
    from vcp_scanner.versioning import code_state

    commit, dirty = code_state()
    code = f"{commit}{' (dirty)' if dirty else ''}" if commit else None
    out: list[ShadowResult] = []
    for cand in load_candidates(config_dir, "active"):
        cid = cand.candidate_id
        try:
            if cand.start_date is None:
                raise ValueError("active without a start date")
            cand_dir = materialize(config_dir, cand.overrides, work_dir / cid / "config")
            frozen_hash = strategy_hash(config_dir, cand.strategy_id)
            cand_hash = strategy_hash(cand_dir, cand.strategy_id)
            with _open_store(db) as store:
                frozen_days = scanned_days(store.conn, cand.strategy_id, frozen_hash,
                                           cand.start_date)  # fmt: skip
                done = set(scanned_days(store.conn, cand.strategy_id, cand_hash, cand.start_date))
            if not frozen_days:
                out.append(ShadowResult(cid, True, "no frozen scan since the start yet"))
                continue
            through = max(frozen_days)
            # Only sessions the frozen run has already scanned, so no candidate row is ever
            # dated after the frozen scans (``daily.sessions_to_scan`` reads the latest date).
            todo = [d for d in frozen_days if d not in done] if cand_hash != frozen_hash else []
            failed = run_scans(cli_main, cand.strategy_id, todo, db, str(cand_dir))
            if failed:
                raise RuntimeError(f"scan failed: {failed[0]}")
            with _open_store(db) as store:
                text = _ledger(store, config_dir, cand, cand_dir, cand_hash, through, code)
            out.append(ShadowResult(cid, True, f"scanned {len(todo)} session(s); {text}"))
        except Exception as exc:  # one candidate failing never stops the others
            out.append(ShadowResult(cid, False, f"{type(exc).__name__}: {exc}"))
    return out


def _events(conn: Any, sql: str, args: list[Any]) -> list[tuple[str, date, dict[str, Any]]]:
    return [(str(r[0]), r[1], json.loads(r[2] or "{}")) for r in conn.execute(sql, args).fetchall()]


def comparison(conn: Any, config_dir: str, cand: Candidate, today: date) -> dict[str, Any]:
    """Frozen paper ledger against the candidate's shadow ledger over the candidate's days:
    trades entered on or after its start (``candidates.compare``)."""
    if cand.start_date is None:
        return {"candidate_id": cand.candidate_id, "started": False}
    frozen_hash = strategy_hash(config_dir, cand.strategy_id)
    frozen = _events(conn, "SELECT event_type, event_date, metadata_json FROM paper_events"
                     " WHERE rule_set = ? AND strategy_id = ? AND config_hash = ?"
                     " AND event_type IN ('ENTRY', 'EXIT')",
                     [RULE_SET, cand.strategy_id, frozen_hash])  # fmt: skip
    start = cand.start_date

    def since_start(rows: list[tuple[str, date, dict[str, Any]]]) -> list[Any]:
        keep = []
        for t, d, m in rows:
            entered = date.fromisoformat(str(m["entry_day"])) if t == "EXIT" and m.get(
                "entry_day") else d  # fmt: skip
            if entered >= start:
                keep.append((t, d, m))
        return keep

    shadow = _events(conn, "SELECT event_type, event_date, metadata_json FROM shadow_paper_events"
                     " WHERE candidate_id = ? AND event_type IN ('ENTRY', 'EXIT')",
                     [cand.candidate_id])  # fmt: skip
    f: LedgerStats = ledger_stats(since_start(frozen))
    s: LedgerStats = ledger_stats(since_start(shadow))
    return {"candidate_id": cand.candidate_id, "strategy_id": cand.strategy_id,
            "started": True, "start_date": start.isoformat(),
            **compare(f, s, start, today)}  # fmt: skip


def comparisons(conn: Any, config_dir: str, today: date) -> list[dict[str, Any]]:
    out = []
    for cand in load_candidates(config_dir):
        if cand.status == "proposed":
            continue
        try:
            out.append({**comparison(conn, config_dir, cand, today), "status": cand.status})
        except Exception as exc:  # a candidate whose files no longer load is shown, not hidden
            out.append({"candidate_id": cand.candidate_id, "status": cand.status,
                        "error": f"{type(exc).__name__}: {exc}"})  # fmt: skip
    return out
