"""Structured logging framework (PROJECT_DESIGN section 76, DATA_SPECIFICATION section 95).

Produces structured log entries with required contextual fields:
timestamp, level, component, symbol, instrument_id, scan_id, run_id, provider,
operation, duration_ms, status, error_code.
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
import time
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Context variables for request / scan tracing
_LOG_CONTEXT: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "log_context", default=None
)


@contextmanager
def log_context(**kwargs: Any) -> Generator[None, None, None]:
    """Context manager to attach structured attributes (e.g. scan_id, symbol) to log messages."""
    current = dict(_LOG_CONTEXT.get() or {})
    current.update({k: v for k, v in kwargs.items() if v is not None})
    token = _LOG_CONTEXT.set(current)
    try:
        yield
    finally:
        _LOG_CONTEXT.reset(token)


class StructuredJsonFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects with standard fields."""

    def format(self, record: logging.LogRecord) -> str:
        ctx = _LOG_CONTEXT.get() or {}
        entry: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Merge contextvars context
        for key, value in ctx.items():
            if value is not None:
                entry[key] = value

        # Merge record extra attributes
        for key in (
            "component",
            "symbol",
            "instrument_id",
            "scan_id",
            "run_id",
            "provider",
            "operation",
            "duration_ms",
            "status",
            "error_code",
            "record_count",
            "retry_count",
        ):
            if hasattr(record, key):
                val = getattr(record, key)
                if val is not None:
                    entry[key] = val

        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(entry, default=str)


class TextStructuredFormatter(logging.Formatter):
    """Readable key-value formatter for console use in development."""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
        ctx = _LOG_CONTEXT.get() or {}
        fields: list[str] = [f"[{ts}]", f"{record.levelname:<5}", f"[{record.name}]"]

        component = getattr(record, "component", ctx.get("component"))
        if component:
            fields.append(f"[{component}]")

        fields.append(record.getMessage())

        extras: list[str] = []
        combined: dict[str, Any] = {**ctx}
        for k in (
            "symbol",
            "instrument_id",
            "scan_id",
            "run_id",
            "provider",
            "operation",
            "duration_ms",
            "status",
            "error_code",
        ):
            if hasattr(record, k):
                combined[k] = getattr(record, k)

        for k, v in combined.items():
            if k != "component" and v is not None:
                extras.append(f"{k}={v}")

        if extras:
            fields.append(f"({', '.join(extras)})")

        base = " ".join(fields)
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


def get_logger(name: str) -> logging.Logger:
    """Return a logger instance configured for the vcp_scanner hierarchy."""
    return logging.getLogger(f"vcp_scanner.{name}" if not name.startswith("vcp_scanner") else name)


class _StderrHandler(logging.StreamHandler):  # type: ignore[type-arg]
    """Stream handler that resolves ``sys.stderr`` at emit time, not at construction.

    Keeps the handler valid when ``sys.stderr`` is swapped later (pytest capture, redirects).
    """

    def __init__(self) -> None:
        logging.Handler.__init__(self)

    @property
    def stream(self) -> Any:
        return sys.stderr

    @stream.setter
    def stream(self, value: Any) -> None:
        pass  # always follow the live sys.stderr


def configure_logging(
    level: str = "INFO",
    json_format: bool = False,
    stream: Any = None,
    log_file: str | None = None,
) -> None:
    """Configure the ``vcp_scanner`` logger with structured formatting.

    Logs go to ``stream`` if given, otherwise to stderr (never stdout, so command output
    stays clean). If ``log_file`` is set, records are also appended to that file. Calling
    this again replaces the previous handlers, so it is safe to call more than once.
    """
    log_level = getattr(logging, level.upper(), logging.INFO)
    formatter: logging.Formatter = (
        StructuredJsonFormatter() if json_format else TextStructuredFormatter()
    )

    handlers: list[logging.Handler] = []
    console: logging.Handler = (
        logging.StreamHandler(stream) if stream is not None else _StderrHandler()
    )
    handlers.append(console)
    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(path, encoding="utf-8"))
    for handler in handlers:
        handler.setFormatter(formatter)

    root_logger = logging.getLogger("vcp_scanner")
    root_logger.setLevel(log_level)
    for old in list(root_logger.handlers):
        root_logger.removeHandler(old)
        if isinstance(old, logging.FileHandler):
            old.close()
    for handler in handlers:
        root_logger.addHandler(handler)


@contextmanager
def timed_operation(
    logger: logging.Logger,
    operation: str,
    component: str | None = None,
    **extra: Any,
) -> Generator[dict[str, Any], None, None]:
    """Measure duration in milliseconds and log operation start and completion."""
    start_time = time.perf_counter()
    ctx: dict[str, Any] = {"operation": operation, **extra}
    if component:
        ctx["component"] = component

    try:
        yield ctx
        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        ctx["duration_ms"] = duration_ms
        ctx.setdefault("status", "SUCCESS")
        logger.info(
            f"Completed {operation} in {duration_ms}ms",
            extra=ctx,
        )
    except Exception as exc:
        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        ctx["duration_ms"] = duration_ms
        ctx["status"] = "ERROR"
        ctx.setdefault("error_code", type(exc).__name__)
        logger.exception(
            f"Failed {operation} after {duration_ms}ms: {exc}",
            extra=ctx,
        )
        raise
