"""Evening-run progress for the dashboard (vcp_scanner.run_state) and what daily_run.sh records.

The script tests run the real scripts/daily_run.sh in a scratch project with a fake ``vcp`` that
prints step headers like the real run, so a normal finish, a Ctrl-C/kill and a closed console
(the whole process group killed) are all exercised for real.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

from vcp_scanner.run_state import read_state, run_progress

REPO = Path(__file__).resolve().parents[2]


def _state(logs: Path, **kw: str) -> None:
    base = {"pid": "4242", "started": "2026-10-13T22:00:00+05:30", "log": "data/logs/run.out",
            "log_offset": "0", "summaries_before": "1", "finished": "", "exit": ""}  # fmt: skip
    base.update(kw)
    (logs / "daily_run.state").write_text("".join(f"{k}={v}\n" for k, v in base.items()))


@pytest.fixture
def logs(tmp_path: Path) -> Path:
    d = tmp_path / "data" / "logs"
    d.mkdir(parents=True)
    (d / "daily_runs.log").write_text("2026-10-13 19:15 IST | all steps OK\n")
    (d / "run.out").write_text(
        "=== start 2026-10-13 22:00 IST\n=== database check and backup (22:00 IST)\n"
        "=== corporate actions (22:01 IST)\nINFO asking Upstox\n"
    )
    return d


def test_no_state_file_means_nothing_to_show(logs: Path) -> None:
    assert run_progress(logs) is None


def test_a_live_pid_without_finish_is_running_at_its_last_step(logs: Path) -> None:
    _state(logs)
    p = run_progress(logs, alive=lambda pid: pid == 4242)
    assert p is not None and p.state == "running"
    assert (p.step, p.step_since, p.exit_code) == ("corporate actions", "22:01", None)


def test_a_dead_pid_without_finish_is_a_stopped_run(logs: Path) -> None:
    """Tonight's case: the console window closed, the whole run killed, no trap ran."""
    _state(logs)
    p = run_progress(logs, alive=lambda _pid: False)
    assert p is not None and (p.state, p.step) == ("stopped", "corporate actions")


def test_finished_with_a_new_summary_line_is_finished(logs: Path) -> None:
    with (logs / "daily_runs.log").open("a") as f:
        f.write("2026-10-13 22:00 IST | scanned nothing new | all steps OK\n")
    _state(logs, finished="2026-10-13T22:35:00+05:30", exit="0")
    p = run_progress(logs, alive=lambda _pid: False)
    assert p is not None and (p.state, p.exit_code) == ("finished", 0)


def test_finished_without_a_summary_line_stopped_half_way(logs: Path) -> None:
    _state(logs, finished="2026-10-13T22:02:00+05:30", exit="130")  # Ctrl-C: the trap ran
    p = run_progress(logs, alive=lambda _pid: False)
    assert p is not None and (p.state, p.exit_code, p.step) == ("stopped", 130, "corporate actions")


def test_only_this_runs_part_of_the_log_is_read(logs: Path) -> None:
    """Steps of the 19:15 run earlier in the same day's file are not this run's."""
    out = logs / "run.out"
    earlier = "=== start 2026-10-13 19:15 IST\n=== serving copy (19:54 IST)\n"
    out.write_text(earlier + "=== start 2026-10-13 22:00 IST\n")
    _state(logs, log_offset=str(len(earlier)))
    p = run_progress(logs, alive=lambda _pid: True)
    assert p is not None and p.step is None


# --- the real script -----------------------------------------------------------------------

FAKE_VCP = """#!/usr/bin/env bash
echo "=== database check and backup ($(date +%H:%M) IST)"
echo "=== corporate actions ($(date +%H:%M) IST)"
if [ -n "${FAKE_SLEEP:-}" ]; then sleep "$FAKE_SLEEP"; fi
echo "$(date '+%Y-%m-%d %H:%M') IST | all steps OK" >> data/logs/daily_runs.log
"""


@pytest.fixture
def project(tmp_path: Path) -> Path:
    if shutil.which("flock") is None:
        pytest.skip("flock not available")
    (tmp_path / "scripts").mkdir()
    shutil.copy2(REPO / "scripts" / "daily_run.sh", tmp_path / "scripts" / "daily_run.sh")
    vcp = tmp_path / ".venv" / "bin" / "vcp"
    vcp.parent.mkdir(parents=True)
    vcp.write_text(FAKE_VCP)
    vcp.chmod(0o755)
    return tmp_path


def _start(project: Path, sleep: str = "") -> subprocess.Popen[bytes]:
    env = {**os.environ, "FAKE_SLEEP": sleep}
    return subprocess.Popen(["bash", str(project / "scripts" / "daily_run.sh")], env=env,
                            start_new_session=True)  # fmt: skip


def _wait_for_step(project: Path) -> None:
    for _ in range(100):
        out = list((project / "data" / "logs").glob("daily_run_*.out"))
        if out and "corporate actions" in out[0].read_text():
            return
        time.sleep(0.05)
    raise AssertionError("the run never reached its step")


def test_script_records_a_normal_finish(project: Path) -> None:
    assert _start(project).wait(20) == 0
    logs = project / "data" / "logs"
    st = read_state(logs)
    assert st is not None and st["finished"] and st["exit"] == "0"
    p = run_progress(logs, project)
    assert p is not None and (p.state, p.step) == ("finished", "corporate actions")


def test_script_records_a_run_that_is_stopped(project: Path) -> None:
    """Ctrl-C or kill: the trap still runs and says how the run ended."""
    proc = _start(project, sleep="30")
    _wait_for_step(project)
    p = run_progress(project / "data" / "logs", project)
    assert p is not None and p.state == "running"
    os.killpg(proc.pid, signal.SIGTERM)
    proc.wait(20)
    p = run_progress(project / "data" / "logs", project)
    assert p is not None and p.state == "stopped" and p.step == "corporate actions"
    assert p.exit_code == 143


def test_script_killed_outright_shows_as_stopped(project: Path) -> None:
    """A closed console: everything killed at once, no trap. The dead pid gives it away."""
    proc = _start(project, sleep="30")
    _wait_for_step(project)
    os.killpg(proc.pid, signal.SIGKILL)
    proc.wait(20)
    st = read_state(project / "data" / "logs")
    assert st is not None and st["finished"] == ""
    p = run_progress(project / "data" / "logs", project)
    assert p is not None and (p.state, p.step) == ("stopped", "corporate actions")


def test_a_second_run_while_one_is_going_skips_and_leaves_the_state(project: Path) -> None:
    first = _start(project, sleep="30")
    _wait_for_step(project)
    before = (project / "data" / "logs" / "daily_run.state").read_text()
    assert _start(project).wait(20) == 0  # skipped: another run is in progress
    assert (project / "data" / "logs" / "daily_run.state").read_text() == before
    os.killpg(first.pid, signal.SIGKILL)
    first.wait(20)
