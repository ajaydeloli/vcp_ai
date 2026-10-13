"""Is the evening run going, finished, or stopped half-way? (read-only, for the dashboard)

``scripts/daily_run.sh`` writes ``<logs>/daily_run.state`` once it holds the run lock, and
rewrites it when it exits (an EXIT trap, so a Ctrl-C or ``kill`` is recorded too):

    pid=1234
    started=2026-10-13T19:15:00+05:30
    log=data/logs/daily_run_2026-10-13.out
    log_offset=511566          # size of that log before this run wrote to it
    summaries_before=20        # lines of daily_runs.log before this run
    finished=                  # set by the EXIT trap
    exit=

A run whose whole process tree is killed (its console window closed) never reaches the trap:
the file still says nothing finished, but the pid is gone. A run that finished but wrote no new
line to ``daily_runs.log`` stopped before ``vcp run daily`` could write its summary.

Nothing here writes anywhere or touches the run lock (taking it, even for a moment, could make
a scheduled run skip itself).
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

STATE_FILE = "daily_run.state"
SUMMARY_FILE = "daily_runs.log"
_STEP = re.compile(r"^=== (.+) \((\d\d:\d\d) IST\)\s*$")
_TAIL_BYTES = 2_000_000  # a run writes about 0.5 MB


@dataclass(frozen=True, slots=True)
class RunProgress:
    state: str  # running | finished | stopped
    started: datetime
    step: str | None  # the step running (or the last one reached)
    step_since: str | None  # HH:MM IST
    exit_code: int | None


def _pid_is_the_run(pid: int) -> bool:
    """True when ``pid`` is alive and is the daily-run script (not a reused pid)."""
    try:
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return False
    return b"daily_run" in cmd


def read_state(logs_dir: Path) -> dict[str, str] | None:
    try:
        text = (logs_dir / STATE_FILE).read_text(encoding="utf-8")
    except OSError:
        return None
    out: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            out[key.strip()] = value.strip()
    return out


def _summaries(logs_dir: Path) -> int:
    try:
        return sum(1 for x in (logs_dir / SUMMARY_FILE).read_text(encoding="utf-8").splitlines()
                   if x.strip())  # fmt: skip
    except OSError:
        return 0


def _last_step(log: Path, offset: int) -> tuple[str | None, str | None]:
    try:
        with log.open("rb") as f:
            size = f.seek(0, os.SEEK_END)
            f.seek(max(offset, size - _TAIL_BYTES, 0))
            text = f.read().decode("utf-8", "replace")
    except OSError:
        return None, None
    step = since = None
    for line in text.splitlines():
        m = _STEP.match(line)
        if m and not m.group(1).startswith(("start ", "end ", "Daily run summary")):
            step, since = m.group(1), m.group(2)
    return step, since


def run_progress(
    logs_dir: Path, project_dir: Path | None = None, *,
    alive: Callable[[int], bool] = _pid_is_the_run,
) -> RunProgress | None:  # fmt: skip
    """The newest run's state, or None before the first run that wrote a state file."""
    st = read_state(logs_dir)
    if not st or not st.get("started"):
        return None
    try:
        started = datetime.fromisoformat(st["started"])
        pid = int(st.get("pid") or 0)
        offset = int(st.get("log_offset") or 0)
        before = int(st.get("summaries_before") or 0)
    except ValueError:
        return None
    log = Path(st.get("log", ""))
    if not log.is_absolute():
        log = (project_dir or logs_dir.parent.parent) / log
    step, since = _last_step(log, offset)
    code = int(st["exit"]) if st.get("exit", "").lstrip("-").isdigit() else None
    if not st.get("finished"):
        state = "running" if pid and alive(pid) else "stopped"
    else:
        state = "finished" if _summaries(logs_dir) > before else "stopped"
    return RunProgress(state, started, step, since, code)
