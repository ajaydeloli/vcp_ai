"""``vcp health build``: the Market Health Score of every session (MARKET_HEALTH_SPECIFICATION).
Stores the days not stored yet for the current settings version; ``--rebuild`` replaces them.
Display, research and a shadow traffic light only."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path


def add_health_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = subparsers.add_parser("health", help="Market Health Score (display and research only)")
    sub = p.add_subparsers(dest="health_command")
    b = sub.add_parser("build", help="Score and store the sessions not stored yet")
    b.add_argument("--db", default="data/vcp_scanner.duckdb")
    b.add_argument("--config-dir", default="config")
    b.add_argument("--rebuild", action="store_true",
                   help="Replace the stored days of the current settings version")  # fmt: skip
    b.add_argument("--from", dest="start", default=None, metavar="YYYY-MM-DD")


def run_health(args: argparse.Namespace) -> int:
    if getattr(args, "health_command", None) != "build":
        print("Usage: vcp health build [--rebuild] [--from YYYY-MM-DD]", file=sys.stderr)
        return 2
    from vcp_scanner.market_health.build import run

    start = date.fromisoformat(args.start) if args.start else None
    return run(Path(args.db).resolve(), args.config_dir, args.rebuild, start)
