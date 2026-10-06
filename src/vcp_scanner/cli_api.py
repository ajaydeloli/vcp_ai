"""``vcp api serve``: the read-only dashboard API on this PC (FRONTEND_SPECIFICATION 67.5)."""

from __future__ import annotations

import argparse

from vcp_scanner.cli_pipeline import _err


def add_api_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = subparsers.add_parser("api", help="Read-only dashboard API")
    sub = p.add_subparsers(dest="api_command")
    s = sub.add_parser("serve", help="Serve the API from the serving copy of the database")
    s.add_argument("--host", default="127.0.0.1", help="Default 127.0.0.1 (this PC only)")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--data-dir", default="data", help="The main database's folder")
    s.add_argument("--serving-db", default=None,
                   help="Default: <data-dir>/serving/vcp_serving.duckdb")  # fmt: skip
    s.add_argument("--config-dir", default="config")


def run_api(args: argparse.Namespace) -> int:
    if getattr(args, "api_command", None) != "serve":
        _err("Usage: vcp api serve [--host H] [--port P]")
        return 1
    try:
        import uvicorn

        from vcp_scanner.api.app import create_app

        app = create_app(args.serving_db, args.config_dir, args.data_dir)
    except Exception as exc:
        _err(f"Cannot start the API: {exc}")
        return 1
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0
