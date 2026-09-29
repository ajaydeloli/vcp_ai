"""Unit tests for the CLI commands (PROJECT_DESIGN section 67)."""

import io
from contextlib import redirect_stderr, redirect_stdout

import pytest

from vcp_scanner.cli import main


def test_cli_version() -> None:
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        exit_code = main(["version"])
    assert exit_code == 0
    output = stdout.getvalue()
    assert "package_version: 0.1.0" in output
    assert "strategy_version: strategy-1.0.0" in output


def test_cli_config_validate() -> None:
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        exit_code = main(["config", "validate", "--config-dir", "config"])
    assert exit_code == 0
    output = stdout.getvalue()
    assert "Configuration is valid." in output
    assert "Configuration hash:" in output


def test_cli_config_validate_failure() -> None:
    stderr = io.StringIO()
    with redirect_stderr(stderr):
        exit_code = main(["config", "validate", "--config-dir", "nonexistent_dir"])
    assert exit_code == 1
    output = stderr.getvalue()
    assert "Configuration error:" in output


def test_cli_config_hash() -> None:
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        exit_code = main(["config", "hash", "--config-dir", "config"])
    assert exit_code == 0
    output = stdout.getvalue().strip()
    assert len(output) == 64


def test_cli_ingest_help() -> None:
    stdout = io.StringIO()
    with redirect_stdout(stdout), pytest.raises(SystemExit) as exc_info:
        main(["ingest", "--help"])
    assert exc_info.value.code == 0


def test_cli_ingest_universe_invalid_date() -> None:
    stderr = io.StringIO()
    with redirect_stderr(stderr):
        exit_code = main(["ingest", "universe", "--as-of", "invalid-date"])
    assert exit_code == 1
    assert "Invalid date" in stderr.getvalue()


def test_cli_ingest_security_master_invalid_date() -> None:
    stderr = io.StringIO()
    with redirect_stderr(stderr):
        exit_code = main(["ingest", "security-master", "--start", "invalid-date"])
    assert exit_code == 1
    assert "Error parsing dates" in stderr.getvalue()
