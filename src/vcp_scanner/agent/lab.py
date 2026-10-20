"""The research lab's rules (AGENT_SPECIFICATION §4, step A2): pre-registered hypotheses, data
windows and the test budget, enforced here and not by the AI's good intentions.

* A test runs only against a registered, open hypothesis.
* Development (2022-02-01 .. 2024-06-30): as many runs as wanted, to generate and shape ideas.
* Validation (2024-07-01 .. 2026-09-30) was used for the frozen choice and counts as seen: one
  run per hypothesis, as a sanity check, and only after a development run.
* Nothing after 2026-09-30: that is the paper and shadow ledgers' own evidence.
* At most ``MAX_PER_STRATEGY`` hypotheses per strategy per review cycle (owner decision
  2026-10-19, AGENT_SPECIFICATION §9).
* Every run and every conclusion is written to the notebook, whatever it shows.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import duckdb

from vcp_scanner.agent.store import AgentStore

STRATEGIES = ("vcp", "flat_base", "three_weeks_tight", "cup_handle", "double_bottom")
WINDOWS = {
    "development": (date(2022, 2, 1), date(2024, 6, 30)),
    "validation": (date(2024, 7, 1), date(2026, 9, 30)),
}
#: The review cycle hypotheses count against (the April 2027 review).
CYCLE = "2027-04"
MAX_PER_STRATEGY = 3
OUTCOMES = ("supported", "rejected", "inconclusive")
MAX_TEXT = 1500

_DDL = (
    "CREATE SEQUENCE IF NOT EXISTS agent_hypotheses_seq",
    """CREATE TABLE IF NOT EXISTS agent_hypotheses (
        hypothesis_id BIGINT PRIMARY KEY DEFAULT nextval('agent_hypotheses_seq'),
        created_at    TIMESTAMPTZ NOT NULL,
        cycle         VARCHAR NOT NULL,
        strategy      VARCHAR NOT NULL,
        title         VARCHAR NOT NULL,
        idea          VARCHAR NOT NULL,   -- the change or effect being tested
        metric        VARCHAR NOT NULL,   -- what is measured
        decision_rule VARCHAR NOT NULL,   -- what result would count, written before any result
        status        VARCHAR NOT NULL,   -- OPEN | CONCLUDED
        outcome       VARCHAR,            -- supported | rejected | inconclusive
        conclusion    VARCHAR,
        concluded_at  TIMESTAMPTZ
    )""",
    "CREATE SEQUENCE IF NOT EXISTS agent_runs_seq",
    """CREATE TABLE IF NOT EXISTS agent_runs (
        run_id        BIGINT PRIMARY KEY DEFAULT nextval('agent_runs_seq'),
        hypothesis_id BIGINT NOT NULL,
        kind          VARCHAR NOT NULL,   -- study | backtest | what_if
        window_name   VARCHAR NOT NULL,   -- development | validation
        start_date    DATE NOT NULL,
        end_date      DATE NOT NULL,
        params_json   VARCHAR NOT NULL,
        started_at    TIMESTAMPTZ NOT NULL,
        finished_at   TIMESTAMPTZ,
        ok            BOOLEAN,
        summary       VARCHAR,            -- one line, also written to the notebook
        result_json   VARCHAR,
        error         VARCHAR
    )""",
)


class LabRuleError(ValueError):
    """A request the lab's rules refuse (said plainly so the AI can tell the owner)."""


@dataclass(frozen=True, slots=True)
class Hypothesis:
    hypothesis_id: int
    created_at: datetime
    cycle: str
    strategy: str
    title: str
    idea: str
    metric: str
    decision_rule: str
    status: str
    outcome: str | None
    conclusion: str | None


def _text(name: str, value: str) -> str:
    v = (value or "").strip()
    if not v:
        raise LabRuleError(f"{name} is empty: a hypothesis must say it before any result")
    if len(v) > MAX_TEXT:
        raise LabRuleError(f"{name} is longer than {MAX_TEXT} characters")
    return v


class Lab:
    def __init__(self, store: AgentStore) -> None:
        self.store = store

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

    # --- hypotheses ---------------------------------------------------------------------------

    def register(self, strategy: str, title: str, idea: str, metric: str,
                 decision_rule: str) -> int:  # fmt: skip
        strategy = strategy.strip().lower()
        if strategy not in STRATEGIES:
            raise LabRuleError(f"strategy must be one of {', '.join(STRATEGIES)}")
        fields = {k: _text(k, v) for k, v in (("title", title), ("idea", idea),
                                              ("metric", metric),
                                              ("decision_rule", decision_rule))}  # fmt: skip
        with self._conn() as c:
            used = c.execute(
                "SELECT count(*) FROM agent_hypotheses WHERE cycle = ? AND strategy = ?",
                [CYCLE, strategy],
            ).fetchone()
            if used and used[0] >= MAX_PER_STRATEGY:
                raise LabRuleError(
                    f"the test budget for {strategy} is used: {used[0]} of {MAX_PER_STRATEGY} "
                    f"hypotheses this review cycle ({CYCLE}). The owner sets the budget "
                    "(AGENT_SPECIFICATION §9); conclude or reuse an existing hypothesis."
                )
            row = c.execute(
                "INSERT INTO agent_hypotheses (created_at, cycle, strategy, title, idea, metric,"
                " decision_rule, status) VALUES (?, ?, ?, ?, ?, ?, ?, 'OPEN')"
                " RETURNING hypothesis_id",
                [datetime.now(UTC), CYCLE, strategy, fields["title"], fields["idea"],
                 fields["metric"], fields["decision_rule"]],
            ).fetchone()  # fmt: skip
        assert row is not None
        hid = int(row[0])
        self.store.add_note(
            "hypothesis",
            f"H{hid} registered ({strategy}): {fields['title']}. Idea: {fields['idea']} "
            f"Metric: {fields['metric']} Decision rule: {fields['decision_rule']}",
            tags=f"h{hid},{strategy},lab",
        )
        return hid

    def get(self, hypothesis_id: int) -> Hypothesis:
        found = self.find(hypothesis_id=hypothesis_id)
        if not found:
            raise LabRuleError(f"no hypothesis H{hypothesis_id}; register it first")
        return found[0]

    def find(self, status: str | None = None, strategy: str | None = None,
             hypothesis_id: int | None = None) -> list[Hypothesis]:  # fmt: skip
        where: list[str] = ["1 = 1"]
        args: list[Any] = []
        if status:
            where.append("status = upper(?)")
            args.append(status)
        if strategy:
            where.append("strategy = lower(?)")
            args.append(strategy)
        if hypothesis_id is not None:
            where.append("hypothesis_id = ?")
            args.append(hypothesis_id)
        with self._conn(read_only=True) as c:
            rows = c.execute(
                "SELECT hypothesis_id, created_at, cycle, strategy, title, idea, metric,"
                " decision_rule, status, outcome, conclusion FROM agent_hypotheses"
                f" WHERE {' AND '.join(where)} ORDER BY hypothesis_id DESC",
                args,
            ).fetchall()
        return [Hypothesis(int(r[0]), r[1], *[x for x in r[2:]]) for r in rows]

    def budget(self) -> dict[str, int]:
        with self._conn(read_only=True) as c:
            rows = c.execute(
                "SELECT strategy, count(*) FROM agent_hypotheses WHERE cycle = ? GROUP BY 1",
                [CYCLE],
            ).fetchall()
        used = {str(s): int(n) for s, n in rows}
        return {s: MAX_PER_STRATEGY - used.get(s, 0) for s in STRATEGIES}

    def conclude(self, hypothesis_id: int, outcome: str, conclusion: str) -> None:
        h = self.get(hypothesis_id)
        if h.status != "OPEN":
            raise LabRuleError(f"H{hypothesis_id} is already concluded ({h.outcome})")
        outcome = outcome.strip().lower()
        if outcome not in OUTCOMES:
            raise LabRuleError(f"outcome must be one of {', '.join(OUTCOMES)}")
        text = _text("conclusion", conclusion)
        with self._conn() as c:
            c.execute(
                "UPDATE agent_hypotheses SET status = 'CONCLUDED', outcome = ?, conclusion = ?,"
                " concluded_at = ? WHERE hypothesis_id = ?",
                [outcome, text, datetime.now(UTC), hypothesis_id],
            )
        self.store.add_note(
            "finding",
            f"H{hypothesis_id} concluded: {outcome}. {text} (A conclusion is research only: "
            "a change to a frozen strategy needs a proposal the owner approves, and only the "
            "shadow ledger can promote it.)",
            tags=f"h{hypothesis_id},{h.strategy},lab",
        )

    # --- runs ---------------------------------------------------------------------------------

    def runs(self, hypothesis_id: int | None = None) -> list[dict[str, Any]]:
        with self._conn(read_only=True) as c:
            rows = c.execute(
                "SELECT run_id, hypothesis_id, kind, window_name, start_date, end_date, ok,"
                " summary, error, started_at FROM agent_runs"
                + (" WHERE hypothesis_id = ?" if hypothesis_id is not None else "")
                + " ORDER BY run_id DESC",
                [hypothesis_id] if hypothesis_id is not None else [],
            ).fetchall()
        keys = ("run_id", "hypothesis_id", "kind", "window", "start", "end", "ok", "summary",
                "error", "started_at")  # fmt: skip
        return [dict(zip(keys, r, strict=True)) for r in rows]

    def check(self, hypothesis_id: int, window: str, start: date | None,
              end: date | None) -> tuple[Hypothesis, date, date]:  # fmt: skip
        """The hypothesis and the run's dates, or LabRuleError saying which rule refuses it."""
        h = self.get(hypothesis_id)
        if h.status != "OPEN":
            raise LabRuleError(f"H{hypothesis_id} is concluded; register a new hypothesis")
        window = window.strip().lower()
        if window not in WINDOWS:
            raise LabRuleError(
                "window must be 'development' (2022-02-01..2024-06-30) or 'validation' "
                "(2024-07-01..2026-09-30, once per hypothesis). Data after 2026-09-30 is the "
                "paper and shadow ledgers' evidence and is not open to studies."
            )
        lo, hi = WINDOWS[window]
        s, e = start or lo, end or hi
        if s < lo or e > hi or s > e:
            raise LabRuleError(f"dates must lie within the {window} window {lo}..{hi}")
        if window == "validation":
            done = self.runs(hypothesis_id)
            if not any(r["window"] == "development" and r["ok"] for r in done):
                raise LabRuleError(
                    "validation comes after development: run the hypothesis on the "
                    "development window first (validation is a one-time sanity check)"
                )
            if any(r["window"] == "validation" for r in done):
                raise LabRuleError(
                    f"H{hypothesis_id} has already used its one validation run; validation "
                    "is never used to choose between variants"
                )
        return h, s, e

    def start_run(self, hypothesis_id: int, kind: str, window: str, start: date, end: date,
                  params: dict[str, Any]) -> int:  # fmt: skip
        with self._conn() as c:
            row = c.execute(
                "INSERT INTO agent_runs (hypothesis_id, kind, window_name, start_date, end_date,"
                " params_json, started_at) VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING run_id",
                [hypothesis_id, kind, window, start, end,
                 json.dumps(params, sort_keys=True, default=str), datetime.now(UTC)],
            ).fetchone()  # fmt: skip
        assert row is not None
        return int(row[0])

    def finish_run(self, run_id: int, hypothesis: Hypothesis, window: str, ok: bool,
                   summary: str, result: Any = None, error: str | None = None) -> None:  # fmt: skip
        with self._conn() as c:
            c.execute(
                "UPDATE agent_runs SET finished_at = ?, ok = ?, summary = ?, result_json = ?,"
                " error = ? WHERE run_id = ?",
                [datetime.now(UTC), ok, summary[:MAX_TEXT],
                 None if result is None else json.dumps(result, default=str), error, run_id],
            )  # fmt: skip
        seen = (" Validation data was used for the frozen choice: a sanity check only."
                if window == "validation" else "")  # fmt: skip
        self.store.add_note(
            "finding",
            f"H{hypothesis.hypothesis_id} run {run_id} ({window}): "
            + (summary if ok else f"failed: {error}")
            + seen,
            tags=f"h{hypothesis.hypothesis_id},{hypothesis.strategy},lab,run",
        )
