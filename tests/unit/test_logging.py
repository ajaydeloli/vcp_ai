"""Unit tests for structured logging and contextvars enrichment (PROJECT_DESIGN section 76)."""

import io
import json
import logging

from vcp_scanner.infrastructure.logging import (
    StructuredJsonFormatter,
    TextStructuredFormatter,
    configure_logging,
    get_logger,
    log_context,
    timed_operation,
)


def test_structured_json_formatter() -> None:
    formatter = StructuredJsonFormatter()
    logger = logging.getLogger("test_json")

    with log_context(scan_id="scan_123", symbol="TCS"):
        record = logger.makeRecord(
            name="test_json",
            level=logging.INFO,
            fn="test_logging.py",
            lno=20,
            msg="Processing pattern",
            args=(),
            exc_info=None,
            extra={"component": "detector", "operation": "evaluate"},
        )
        output = formatter.format(record)
        data = json.loads(output)
        assert data["level"] == "INFO"
        assert data["message"] == "Processing pattern"
        assert data["scan_id"] == "scan_123"
        assert data["symbol"] == "TCS"
        assert data["component"] == "detector"
        assert data["operation"] == "evaluate"
        assert "timestamp" in data


def test_text_structured_formatter() -> None:
    formatter = TextStructuredFormatter()
    logger = logging.getLogger("test_text")

    with log_context(scan_id="scan_456"):
        record = logger.makeRecord(
            name="test_text",
            level=logging.WARNING,
            fn="test_logging.py",
            lno=40,
            msg="Data latency high",
            args=(),
            exc_info=None,
            extra={"component": "provider", "duration_ms": 125.4},
        )
        output = formatter.format(record)
        assert "WARNING" in output
        assert "[provider]" in output
        assert "Data latency high" in output
        assert "scan_id=scan_456" in output
        assert "duration_ms=125.4" in output


def test_timed_operation_records_duration_and_status() -> None:
    stream = io.StringIO()
    configure_logging(level="INFO", json_format=True, stream=stream)
    logger = get_logger("timer_test")

    with timed_operation(logger, operation="fetch_daily", component="data", symbol="INFY"):
        pass

    log_output = stream.getvalue().strip()
    assert log_output
    entry = json.loads(log_output)
    assert entry["operation"] == "fetch_daily"
    assert entry["component"] == "data"
    assert entry["symbol"] == "INFY"
    assert entry["status"] == "SUCCESS"
    assert isinstance(entry["duration_ms"], (int, float))
