"""The CLI configures structured logging (audit finding: logging was never wired in).

Precedence is CLI flags > config/logging.yaml > defaults. Logs go to stderr so command
output on stdout stays clean.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from vcp_scanner.cli import main
from vcp_scanner.infrastructure.logging import (
    StructuredJsonFormatter,
    TextStructuredFormatter,
    get_logger,
)

_LOGGER_NAME = "vcp_scanner"


@pytest.fixture(autouse=True)
def _restore_logger() -> Iterator[None]:
    """Keep CLI logging configuration from leaking into other tests."""
    logger = logging.getLogger(_LOGGER_NAME)
    level, handlers = logger.level, list(logger.handlers)
    yield
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    logger.setLevel(level)
    for handler in handlers:
        logger.addHandler(handler)


def _config_dir(tmp_path: Path, logging_yaml: str | None) -> str:
    cfg = tmp_path / "config"
    cfg.mkdir()
    if logging_yaml is not None:
        (cfg / "logging.yaml").write_text(logging_yaml, encoding="utf-8")
    return str(cfg)


def _handlers() -> list[logging.Handler]:
    return list(logging.getLogger(_LOGGER_NAME).handlers)


def test_cli_configures_logging_from_config_file(tmp_path: Path) -> None:
    cfg = _config_dir(tmp_path, "level: DEBUG\njson_format: true\nlog_file: null\n")

    assert main(["config", "hash", "--config-dir", cfg]) == 0

    logger = logging.getLogger(_LOGGER_NAME)
    assert logger.level == logging.DEBUG
    assert len(_handlers()) == 1
    assert isinstance(_handlers()[0].formatter, StructuredJsonFormatter)


def test_defaults_used_when_no_logging_yaml(tmp_path: Path) -> None:
    cfg = _config_dir(tmp_path, None)

    assert main(["config", "hash", "--config-dir", cfg]) == 0

    assert logging.getLogger(_LOGGER_NAME).level == logging.INFO
    assert isinstance(_handlers()[0].formatter, TextStructuredFormatter)


def test_cli_flags_override_config_file(tmp_path: Path) -> None:
    cfg = _config_dir(tmp_path, "level: DEBUG\njson_format: true\n")

    rc = main(
        ["--log-level", "warning", "--log-format", "text", "config", "hash", "--config-dir", cfg]
    )

    assert rc == 0
    assert logging.getLogger(_LOGGER_NAME).level == logging.WARNING
    assert isinstance(_handlers()[0].formatter, TextStructuredFormatter)


def test_logs_go_to_stderr_and_stdout_stays_clean(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _config_dir(tmp_path, "level: INFO\njson_format: true\n")
    main(["config", "hash", "--config-dir", cfg])
    capsys.readouterr()  # discard the command's own output

    get_logger("cli_logging_test").info("hello from a module", extra={"component": "test"})

    captured = capsys.readouterr()
    assert captured.out == ""
    entry = json.loads(captured.err.strip())
    assert entry["message"] == "hello from a module"
    assert entry["component"] == "test"
    assert entry["logger"] == "vcp_scanner.cli_logging_test"


def test_module_loggers_named_by_dunder_name_are_captured(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Runtime modules use ``logging.getLogger(__name__)``; those must reach the handler."""
    cfg = _config_dir(tmp_path, None)
    main(["--log-level", "DEBUG", "config", "hash", "--config-dir", cfg])
    capsys.readouterr()

    logging.getLogger("vcp_scanner.data.ingestion.worker").debug("gap refetch")

    assert "gap refetch" in capsys.readouterr().err


def test_level_filters_lower_severity(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = _config_dir(tmp_path, None)
    main(["--log-level", "ERROR", "config", "hash", "--config-dir", cfg])
    capsys.readouterr()

    logger = get_logger("level_test")
    logger.warning("dropped")
    logger.error("kept")

    err = capsys.readouterr().err
    assert "dropped" not in err
    assert "kept" in err


def test_log_file_flag_writes_records_to_file(tmp_path: Path) -> None:
    cfg = _config_dir(tmp_path, None)
    log_path = tmp_path / "logs" / "vcp.log"  # parent directory is created on demand

    main(
        ["--log-file", str(log_path), "--log-format", "json", "config", "hash", "--config-dir", cfg]
    )
    get_logger("file_test").info("written to file")

    for handler in _handlers():
        handler.flush()
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert json.loads(lines[-1])["message"] == "written to file"


def test_log_file_from_config_yaml(tmp_path: Path) -> None:
    cfg = _config_dir(tmp_path, None)
    log_path = tmp_path / "from_yaml.log"
    (Path(cfg) / "logging.yaml").write_text(f"log_file: {log_path}\n", encoding="utf-8")

    main(["config", "hash", "--config-dir", cfg])
    get_logger("yaml_file_test").warning("to yaml file")

    for handler in _handlers():
        handler.flush()
    assert "to yaml file" in log_path.read_text(encoding="utf-8")


def test_repeated_invocation_does_not_duplicate_handlers(tmp_path: Path) -> None:
    cfg = _config_dir(tmp_path, None)

    main(["config", "hash", "--config-dir", cfg])
    main(["config", "hash", "--config-dir", cfg])

    assert len(_handlers()) == 1


def test_invalid_logging_yaml_warns_and_command_still_runs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _config_dir(tmp_path, "level: INFO\nunknown_key: 1\n")

    rc = main(["config", "hash", "--config-dir", cfg])

    # The command itself reports the config error (it loads the full config); the point is that
    # logging setup warned and fell back to defaults instead of crashing before the command ran.
    assert rc == 1
    err = capsys.readouterr().err
    assert "ignoring logging configuration" in err
    assert "Configuration error" in err
    assert logging.getLogger(_LOGGER_NAME).level == logging.INFO  # fell back to defaults


def test_unwritable_log_file_warns_and_falls_back_to_console(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _config_dir(tmp_path, None)
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x", encoding="utf-8")  # a file where a directory would be needed

    rc = main(["--log-file", str(blocker / "vcp.log"), "config", "hash", "--config-dir", cfg])

    assert rc == 0
    assert "cannot open log file" in capsys.readouterr().err
    assert len(_handlers()) == 1  # console handler only


def test_invalid_log_level_flag_is_rejected() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--log-level", "LOUD", "version"])
    assert exc.value.code == 2
