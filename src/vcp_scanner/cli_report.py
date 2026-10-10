"""``vcp report daily|weekly``: HTML reports from the serving copy (monitoring step M4)."""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from vcp_scanner.cli_pipeline import _err
from vcp_scanner.serving import default_serving_path


def add_report_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = subparsers.add_parser("report", help="Daily and weekly HTML reports (read-only)")
    sub = p.add_subparsers(dest="report_command")
    for name, text in (("daily", "Write reports/daily/<date>.html"),
                       ("weekly", "Write reports/weekly/<ISO week>.html")):  # fmt: skip
        s = sub.add_parser(name, help=text)
        s.add_argument("--date", default=None, metavar="YYYY-MM-DD",
                       help="Report day (default: latest prices)")  # fmt: skip
        s.add_argument("--db", default="data/vcp_scanner.duckdb")
        s.add_argument("--serving", default=None, help="Serving copy (default: next to --db)")
        s.add_argument("--config-dir", default="config")
        s.add_argument("--out-dir", default=None, help="Default: <project>/reports")


def run_report(args: argparse.Namespace) -> int:
    from vcp_scanner.reporting.build import write_daily, write_weekly

    cmd = getattr(args, "report_command", None)
    if cmd not in ("daily", "weekly"):
        _err("Usage: vcp report {daily,weekly} [--date YYYY-MM-DD]")
        return 1
    db = Path(args.db).resolve()
    serving = Path(args.serving) if args.serving else default_serving_path(str(db))
    out = Path(args.out_dir) if args.out_dir else db.parent.parent / "reports"
    day = date.fromisoformat(args.date) if args.date else None
    try:
        write = write_daily if cmd == "daily" else write_weekly
        path = write(Path(serving), args.config_dir, db.parent, out, day)
    except Exception as exc:
        _err(f"Report not written: {exc}")
        return 1
    print(f"Report written: {path}")
    return 0
