"""Strategy candidates (AGENT_SPECIFICATION §1-§5, step A4; owner choices 2026-10-26).

A candidate is one idea applied to one frozen strategy: a few settings changed (its detector,
classification, ranking or score numbers, or the paper rules it trades by), never code. It is a
small file ``config/candidates/<id>.yaml`` holding the changes ("overrides"), the version label,
the hypothesis it tests and the spec text (old, new, why, evidence). The frozen files are never
edited: a candidate runs from a copy of ``config/`` with its overrides applied
(``materialize``), so its config hash differs and everything it writes is keyed apart.

Life cycle: the AI proposes (``propose_candidate``) -> a background build writes the file on a
branch ``candidate/<id>`` in a separate git worktree, checks it and runs the scan diff (frozen
and candidate on the same stored days since the paper start, on a copy of the newest backup) ->
the owner approves in a terminal (``vcp agent approve``), which merges the branch locally and
starts the candidate -> the daily run scans active candidates under their own hash in the main
database and keeps their shadow paper ledger (``shadow_paper_events``), which the frozen ledger
and the dashboard's frozen views never read -> ``shadow_compare`` sets frozen against candidate
on the same days. Promotion needs 30 shadow trades and 3 months (§9) and is the owner's, at the
review.
"""

from __future__ import annotations

import copy
import math
import re
import shutil
import statistics
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

CANDIDATES_DIR = "candidates"  # under the config directory
PAPER_START = date(2026, 10, 1)  # the scan diff covers stored days from here (owner, 2026-10-26)
MAX_OVERRIDES = 5
MIN_SHADOW_TRADES = 30  # AGENT_SPECIFICATION §9.2
MIN_SHADOW_DAYS = 91  # three months
STATUSES = ("proposed", "active", "retired")
_ID = re.compile(r"^[a-z0-9_]+-c[0-9]+$")
#: Keys of a strategy file that name or switch the strategy, never a setting to test.
_FIXED_KEYS = ("strategy_id", "algorithm_version", "enabled", "stage", "config_source")
#: Paper rules a candidate may change (backtest.yaml ``defaults``).
_PAPER_KEYS = ("defaults.entry", "defaults.regime", "defaults.rule")
#: VCP keeps its settings in the shared files (decision O3); these parts belong to it alone.
_VCP_PATHS = (("strategy.yaml", "vcp."), ("strategy.yaml", "classification."),
              ("scoring.yaml", "components.vcp."))  # fmt: skip


class CandidateError(ValueError):
    """A candidate refused, said plainly."""


@dataclass(frozen=True, slots=True)
class Override:
    file: str  # relative to the config directory, e.g. "strategies/flat_base.yaml"
    path: str  # dotted keys, e.g. "classification.flat_base.max_depth_pct"
    old: Any
    new: Any

    def as_dict(self) -> dict[str, Any]:
        return {"file": self.file, "path": self.path, "old": self.old, "new": self.new}


@dataclass(slots=True)
class Candidate:
    candidate_id: str
    strategy_id: str
    base_version: str
    candidate_version: str
    hypothesis_id: int
    proposal_id: int | None
    status: str
    start_date: date | None
    overrides: list[Override]
    spec: dict[str, str] = field(default_factory=dict)  # old, new, why, evidence

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id, "strategy_id": self.strategy_id,
            "base_version": self.base_version, "candidate_version": self.candidate_version,
            "hypothesis_id": self.hypothesis_id, "proposal_id": self.proposal_id,
            "status": self.status,
            "start_date": None if self.start_date is None else self.start_date.isoformat(),
            "overrides": [o.as_dict() for o in self.overrides], "spec": dict(self.spec),
        }  # fmt: skip

    @property
    def paper_rules_only(self) -> bool:
        return all(o.file == "backtest.yaml" for o in self.overrides)


# ------------------------------------------------------------------------------ YAML paths


def _get(doc: Any, path: str) -> Any:
    cur = doc
    for key in path.split("."):
        if not isinstance(cur, dict) or key not in cur:
            raise KeyError(path)
        cur = cur[key]
    return cur


def _set(doc: dict[str, Any], path: str, value: Any) -> None:
    keys = path.split(".")
    cur: Any = doc
    for key in keys[:-1]:
        cur = cur[key]
    cur[keys[-1]] = value


def _read(config_dir: Path, file: str) -> dict[str, Any]:
    p = config_dir / file
    if not p.exists():
        raise CandidateError(f"{file} does not exist in {config_dir}")
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    return doc if isinstance(doc, dict) else {}


def _same_kind(old: Any, new: Any) -> bool:
    if isinstance(old, bool) or isinstance(new, bool):
        return isinstance(old, bool) and isinstance(new, bool)
    if isinstance(old, (int, float)) and isinstance(new, (int, float)):
        return not (isinstance(new, float) and math.isnan(new))
    if isinstance(old, str):
        return isinstance(new, str)
    if isinstance(old, list):
        return isinstance(new, list)
    return False  # dicts and nulls: change the keys inside, one by one


def _allowed(strategy_id: str, file: str, path: str) -> bool:
    if file == "backtest.yaml":
        return path in _PAPER_KEYS
    if strategy_id == "vcp":
        return any(file == f and path.startswith(p) for f, p in _VCP_PATHS)
    if file == f"strategies/{strategy_id}.yaml":
        return path.split(".")[0] not in _FIXED_KEYS
    return False


def validate_overrides(
    config_dir: str | Path, strategy_id: str, overrides: Sequence[Mapping[str, Any]]
) -> list[Override]:
    """Check each change against the frozen files: an allowed place for this strategy, a key
    that exists, a value of the same kind, really different. Returns them with the old value."""
    root = Path(config_dir)
    if not overrides:
        raise CandidateError("a candidate changes at least one setting")
    if len(overrides) > MAX_OVERRIDES:
        raise CandidateError(f"one idea at a time: at most {MAX_OVERRIDES} settings per candidate")
    out: list[Override] = []
    seen: set[tuple[str, str]] = set()
    for raw in overrides:
        file, path = str(raw.get("file", "")).strip(), str(raw.get("path", "")).strip()
        if "new" not in raw:
            raise CandidateError(f"{file} {path}: the new value is missing")
        new = raw["new"]
        if (file, path) in seen:
            raise CandidateError(f"{file} {path} is changed twice")
        seen.add((file, path))
        if not _allowed(strategy_id, file, path):
            raise CandidateError(
                f"{file} {path} is not a {strategy_id} setting a candidate may change (its own "
                "detector, classification, ranking or score settings, or backtest.yaml "
                "defaults.entry / regime / rule)"
            )
        try:
            old = _get(_read(root, file), path)
        except KeyError:
            raise CandidateError(f"{file} has no setting {path}") from None
        if not _same_kind(old, new):
            raise CandidateError(f"{file} {path}: {new!r} is not the same kind of value as "
                                 f"the current {old!r}")  # fmt: skip
        if old == new:
            raise CandidateError(f"{file} {path} is already {old!r}")
        out.append(Override(file, path, old, new))
    return out


# ------------------------------------------------------------------------------ the config


def materialize(config_dir: str | Path, overrides: Iterable[Override], target: str | Path) -> Path:
    """A full config directory for the candidate: a copy of ``config_dir`` (without the
    provider switch ``live.yaml`` and the candidates themselves) with the overrides applied."""
    src, dst = Path(config_dir), Path(target)
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("live.yaml", CANDIDATES_DIR,
                                                             "__pycache__"))  # fmt: skip
    by_file: dict[str, list[Override]] = {}
    for o in overrides:
        by_file.setdefault(o.file, []).append(o)
    for file, items in by_file.items():
        doc = _read(dst, file)
        for o in items:
            _set(doc, o.path, copy.deepcopy(o.new))
        header = f"# Candidate copy: {', '.join(o.path for o in items)} changed\n"
        (dst / file).write_text(header + yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return dst


def strategy_hash(config_dir: str | Path, strategy_id: str) -> str:
    """The hash setups and the paper ledger are keyed by (``cli_backtest._strategy_hash``)."""
    from vcp_scanner.cli_backtest import _strategy_hash
    from vcp_scanner.config import load_scanner_config

    cfg = load_scanner_config(str(config_dir))
    return _strategy_hash(cfg, strategy_id, str(config_dir))


def check(config_dir: str | Path, cand_dir: str | Path, strategy_id: str,
          paper_only: bool) -> dict[str, Any]:  # fmt: skip
    """The build checks: the candidate config loads and validates, the strategy runtime loads,
    the frozen hash is what the frozen files give, and the candidate hash differs from it unless
    only paper rules change. Raises CandidateError on the first failure."""
    from vcp_scanner.backtest.periods import load_backtest_config
    from vcp_scanner.config import load_scanner_config
    from vcp_scanner.patterns.registry import load_runtime

    try:
        load_scanner_config(str(cand_dir))
        load_backtest_config(str(cand_dir))
        load_runtime(str(cand_dir), strategy_id)
    except Exception as exc:
        raise CandidateError(f"the candidate config does not validate: {exc}") from exc
    frozen = strategy_hash(config_dir, strategy_id)
    cand = strategy_hash(cand_dir, strategy_id)
    if paper_only and cand != frozen:
        raise CandidateError("paper-rule changes must leave the scan unchanged")
    if not paper_only and cand == frozen:
        raise CandidateError("the change does not alter the strategy's config hash")
    return {"frozen_hash": frozen, "candidate_hash": cand, "validated": True}


# ------------------------------------------------------------------------------ the file


def candidate_path(config_dir: str | Path, candidate_id: str) -> Path:
    return Path(config_dir) / CANDIDATES_DIR / f"{candidate_id}.yaml"


def write_file(path: Path, cand: Candidate) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    head = (f"# Strategy candidate {cand.candidate_id} (AGENT_SPECIFICATION step A4).\n"
            "# Not read by the frozen strategies; the daily run shadows it when status is "
            "active.\n")  # fmt: skip
    path.write_text(head + yaml.safe_dump(cand.as_dict(), sort_keys=False, allow_unicode=True),
                    encoding="utf-8")  # fmt: skip


def read_file(path: Path) -> Candidate:
    d = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    start = d.get("start_date")
    return Candidate(
        candidate_id=str(d["candidate_id"]), strategy_id=str(d["strategy_id"]),
        base_version=str(d["base_version"]), candidate_version=str(d["candidate_version"]),
        hypothesis_id=int(d["hypothesis_id"]),
        proposal_id=None if d.get("proposal_id") is None else int(d["proposal_id"]),
        status=str(d["status"]),
        start_date=None if start is None else (start if isinstance(start, date)
                                               else date.fromisoformat(str(start))),
        overrides=[Override(o["file"], o["path"], o.get("old"), o["new"])
                   for o in d.get("overrides", [])],
        spec={k: str(v) for k, v in (d.get("spec") or {}).items()},
    )  # fmt: skip


def load_candidates(config_dir: str | Path, status: str | None = None) -> list[Candidate]:
    folder = Path(config_dir) / CANDIDATES_DIR
    out = [read_file(p) for p in sorted(folder.glob("*.yaml"))] if folder.exists() else []
    return [c for c in out if status is None or c.status == status]


def next_candidate_id(config_dir: str | Path, strategy_id: str, taken: Iterable[str] = ()) -> str:
    used = {c.candidate_id for c in load_candidates(config_dir)} | set(taken)
    n = 1
    while f"{strategy_id}-c{n}" in used:
        n += 1
    return f"{strategy_id}-c{n}"


def valid_id(candidate_id: str) -> bool:
    return bool(_ID.match(candidate_id))


# ------------------------------------------------------------------------------ scanning


def scan_steps(strategy_id: str, day: date, db: str, cand_dir: str) -> list[list[str]]:
    """The CLI steps that scan one stored session with the candidate config, as the daily run
    does for the frozen one. Reads stored data only: no NSE request."""
    iso, common = day.isoformat(), ["--db", db, "--config-dir", cand_dir]
    if strategy_id == "vcp":  # VCP's settings change the scan hash: its own Trend Template too
        return [["compute", "trend-template", "--as-of", iso, *common],
                ["compute", "vcp", "--as-of", iso, *common],
                ["compute", "scores", "--as-of", iso, *common]]  # fmt: skip
    return [["compute", "setups", "--strategy", strategy_id, "--as-of", iso, *common],
            ["compute", "scores", "--strategy", strategy_id, "--as-of", iso, *common]]  # fmt: skip


def scanned_days(conn: Any, strategy_id: str, config_hash: str, start: date,
                 through: date | None = None) -> list[date]:  # fmt: skip
    rows = conn.execute(
        "SELECT DISTINCT as_of_date FROM setup_scores WHERE strategy_id = ? AND config_hash = ?"
        " AND data_snapshot_id = 'LIVE' AND as_of_date >= ? AND as_of_date <= coalesce(?, DATE"
        " '9999-12-31') ORDER BY 1",
        [strategy_id, config_hash, start, through],
    ).fetchall()
    return [r[0] for r in rows]


def run_scans(cli_main: Callable[[list[str]], int], strategy_id: str, days: Sequence[date],
              db: str, cand_dir: str) -> list[str]:  # fmt: skip
    """Scan ``days`` with the candidate config; returns the steps that failed."""
    failed = []
    for d in days:
        for argv in scan_steps(strategy_id, d, db, cand_dir):
            if cli_main(argv) != 0:
                failed.append(f"{' '.join(argv[:2])} {d}")
                break
    return failed


# ------------------------------------------------------------------------------ scan diff

DIFF_KINDS = ("ADDED", "DROPPED", "RECLASSIFIED", "RESCORED")
RESCORE_MIN = 1.0  # points of final score that count as re-scored


def scan_diff(conn: Any, strategy_id: str, frozen_hash: str, cand_hash: str,
              days: Sequence[date]) -> list[dict[str, Any]]:  # fmt: skip
    """Per day and stock, how the candidate's eligible setups differ from the frozen ones:
    ADDED (eligible only for the candidate), DROPPED (only frozen), RECLASSIFIED (both, other
    class) and RESCORED (both, final score moved by at least ``RESCORE_MIN``)."""

    def load(h: str, d: date) -> dict[str, tuple[Any, ...]]:
        rows = conn.execute(
            "SELECT s.instrument_id, coalesce(i.symbol, s.instrument_id), s.classification,"
            " s.final_setup_score FROM setup_scores s LEFT JOIN (SELECT instrument_id,"
            " arg_max(symbol, coalesce(valid_from, DATE '1900-01-01')) AS symbol FROM instruments"
            " GROUP BY 1) i USING (instrument_id) WHERE s.strategy_id = ? AND s.config_hash = ?"
            " AND s.data_snapshot_id = 'LIVE' AND s.as_of_date = ? AND s.eligible",
            [strategy_id, h, d],
        ).fetchall()
        return {str(r[0]): r for r in rows}

    out: list[dict[str, Any]] = []
    for d in days:
        f, c = load(frozen_hash, d), load(cand_hash, d)
        for iid in sorted(set(f) | set(c)):
            a, b = f.get(iid), c.get(iid)
            kind = None
            if a is None:
                kind = "ADDED"
            elif b is None:
                kind = "DROPPED"
            elif a[2] != b[2]:
                kind = "RECLASSIFIED"
            elif a[3] is not None and b[3] is not None and abs(b[3] - a[3]) >= RESCORE_MIN:
                kind = "RESCORED"
            if kind is None:
                continue
            row = b or a
            assert row is not None
            out.append({
                "as_of_date": d, "instrument_id": iid, "symbol": str(row[1]), "change": kind,
                "frozen_class": None if a is None else a[2],
                "candidate_class": None if b is None else b[2],
                "frozen_score": None if a is None else a[3],
                "candidate_score": None if b is None else b[3],
            })  # fmt: skip
    return out


def diff_summary(rows: Sequence[Mapping[str, Any]], days: Sequence[date],
                 frozen_counts: Mapping[date, int]) -> dict[str, Any]:  # fmt: skip
    counts = {k: sum(1 for r in rows if r["change"] == k) for k in DIFF_KINDS}
    return {"days": len(days), "first": None if not days else min(days).isoformat(),
            "last": None if not days else max(days).isoformat(),
            "frozen_eligible": int(sum(frozen_counts.values())),
            "changes": counts, "stocks": len({r["instrument_id"] for r in rows})}  # fmt: skip


# ------------------------------------------------------------------------------ comparison


@dataclass(frozen=True, slots=True)
class LedgerStats:
    closed: int
    open: int
    win_rate: float | None
    avg_ret: float | None
    profit_factor: float | None
    total_ret: float
    max_drawdown: float  # deepest fall of the cumulative closed-trade return, in % points
    returns: tuple[float, ...]


def ledger_stats(events: Sequence[tuple[str, date, Mapping[str, Any]]]) -> LedgerStats:
    """Stats from (event_type, event_date, meta) rows of one ledger."""
    exits = sorted((d, float(m.get("ret_pct", 0.0))) for t, d, m in events if t == "EXIT")
    entries = sum(1 for t, _d, _m in events if t == "ENTRY")
    rets = [r for _d, r in exits]
    wins = [r for r in rets if r > 0]
    losses = [-r for r in rets if r < 0]
    cum, peak, mdd = 0.0, 0.0, 0.0
    for r in rets:
        cum += r
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
    return LedgerStats(
        closed=len(rets), open=entries - len(rets),
        win_rate=None if not rets else len(wins) / len(rets) * 100,
        avg_ret=None if not rets else statistics.fmean(rets),
        profit_factor=None if not losses else sum(wins) / sum(losses),
        total_ret=sum(rets), max_drawdown=mdd, returns=tuple(rets),
    )  # fmt: skip


def compare(frozen: LedgerStats, shadow: LedgerStats, start: date | None,
            today: date) -> dict[str, Any]:  # fmt: skip
    """Frozen against candidate over the same days, with the uncertainty: the difference in
    mean return per trade and its 95 % range (Welch), once each side has 5 closed trades."""
    diff = lo = hi = None
    if frozen.closed >= 5 and shadow.closed >= 5:
        a, b = frozen.returns, shadow.returns
        diff = statistics.fmean(b) - statistics.fmean(a)
        se = math.sqrt(statistics.variance(a) / len(a) + statistics.variance(b) / len(b))
        lo, hi = diff - 1.96 * se, diff + 1.96 * se
    days = 0 if start is None else (today - start).days
    ready = shadow.closed >= MIN_SHADOW_TRADES and days >= MIN_SHADOW_DAYS
    need = []
    if shadow.closed < MIN_SHADOW_TRADES:
        need.append(f"{MIN_SHADOW_TRADES - shadow.closed} more closed shadow trades")
    if days < MIN_SHADOW_DAYS:
        need.append(f"{MIN_SHADOW_DAYS - days} more days")

    def side(s: LedgerStats) -> dict[str, Any]:
        return {"closed": s.closed, "open": s.open, "win_rate": s.win_rate, "avg_ret": s.avg_ret,
                "profit_factor": s.profit_factor, "total_ret": s.total_ret,
                "max_drawdown": s.max_drawdown}  # fmt: skip

    return {"frozen": side(frozen), "candidate": side(shadow), "days": days,
            "avg_ret_diff": diff, "avg_ret_diff_lo": lo, "avg_ret_diff_hi": hi,
            "ready_to_judge": ready, "needs": need}  # fmt: skip


def shadow_start(approved_on: date) -> date:
    """The shadow ledger starts on the approval day: the evening run scans it."""
    return approved_on


def days_between(a: date, b: date) -> list[date]:
    return [a + timedelta(days=i) for i in range((b - a).days + 1)]
