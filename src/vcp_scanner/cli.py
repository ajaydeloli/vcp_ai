"""CLI entry point for vcp-scanner (PROJECT_DESIGN section 67)."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from vcp_scanner.config.loader import compute_config_hash, load_scanner_config
from vcp_scanner.domain.errors import ConfigError
from vcp_scanner.versioning import PACKAGE_VERSION, version_manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vcp",
        description="Institutional-grade NSE VCP Scanner CLI",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {PACKAGE_VERSION}",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # version subcommand
    subparsers.add_parser("version", help="Show full version manifest")

    # config subcommands
    config_parser = subparsers.add_parser("config", help="Configuration utilities")
    config_subparsers = config_parser.add_subparsers(
        dest="config_command", help="Config operations"
    )

    validate_parser = config_subparsers.add_parser(
        "validate", help="Validate YAML configuration files"
    )
    validate_parser.add_argument(
        "--config-dir",
        default="config",
        help="Path to directory containing configuration YAML files (default: config)",
    )

    hash_parser = config_subparsers.add_parser(
        "hash", help="Compute deterministic configuration SHA-256 hash"
    )
    hash_parser.add_argument(
        "--config-dir",
        default="config",
        help="Path to directory containing configuration YAML files (default: config)",
    )

    # auth subcommands
    auth_parser = subparsers.add_parser("auth", help="Authentication utilities")
    auth_subparsers = auth_parser.add_subparsers(dest="auth_command", help="Auth operations")

    kite_auth_parser = auth_subparsers.add_parser(
        "kite", help="Interactive Kite Connect authentication"
    )
    kite_auth_parser.add_argument(
        "--api-key",
        help="Kite Connect API Key (defaults to KITE_API_KEY env var)",
    )
    kite_auth_parser.add_argument(
        "--api-secret",
        help="Kite Connect API Secret (defaults to KITE_API_SECRET env var)",
    )
    kite_auth_parser.add_argument(
        "--env-file",
        default=".env",
        help="Path to .env file to store the access token (default: .env)",
    )

    # ingest subcommands
    ingest_parser = subparsers.add_parser("ingest", help="Data ingestion commands")
    ingest_subparsers = ingest_parser.add_subparsers(
        dest="ingest_command", help="Ingest operations"
    )

    universe_parser = ingest_subparsers.add_parser(
        "universe", help="Build a point-in-time universe snapshot"
    )
    universe_parser.add_argument(
        "--as-of",
        required=True,
        metavar="YYYY-MM-DD",
        help="Date for which to build the universe snapshot",
    )
    universe_parser.add_argument(
        "--db",
        default="data/minervini.duckdb",
        help="Path to DuckDB database file (default: data/minervini.duckdb)",
    )
    universe_parser.add_argument(
        "--config-dir",
        default="config",
        help="Path to directory containing configuration YAML files (default: config)",
    )

    sm_parser = ingest_subparsers.add_parser(
        "security-master", help="Ingest NSE security master and surveillance flags"
    )
    sm_parser.add_argument(
        "--start",
        metavar="YYYY-MM-DD",
        default="2000-01-01",
        help="Start date for ingestion range (default: 2000-01-01)",
    )
    sm_parser.add_argument(
        "--end",
        metavar="YYYY-MM-DD",
        help="End date for ingestion range (default: today)",
    )
    sm_parser.add_argument(
        "--db",
        default="data/minervini.duckdb",
        help="Path to DuckDB database file (default: data/minervini.duckdb)",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    if args.command == "version":
        manifest = version_manifest()
        for k, v in manifest.items():
            print(f"{k}: {v}")
        return 0

    if args.command == "config":
        if args.config_command == "validate":
            try:
                cfg = load_scanner_config(args.config_dir)
                cfg_hash = compute_config_hash(cfg)
                print("Configuration is valid.")
                print(f"Strategy version: {cfg.strategy.scoring.version}")
                print(f"Configuration hash: {cfg_hash}")
                return 0
            except ConfigError as err:
                print(f"Configuration error: {err}", file=sys.stderr)
                return 1

        elif args.config_command == "hash":
            try:
                cfg = load_scanner_config(args.config_dir)
                print(compute_config_hash(cfg))
                return 0
            except ConfigError as err:
                print(f"Configuration error: {err}", file=sys.stderr)
                return 1
        else:
            parser.parse_args(["config", "--help"])
            return 0

    if args.command == "auth":
        if args.auth_command == "kite":
            import os

            from vcp_scanner.auth.kite_auth import KiteAuthenticator

            api_key = args.api_key or os.getenv("KITE_API_KEY")
            api_secret = args.api_secret or os.getenv("KITE_API_SECRET")

            if not api_key or not api_secret:
                print(
                    "Error: Kite API Key and Secret are required "
                    "(via arguments or KITE_API_KEY/KITE_API_SECRET env vars).",
                    file=sys.stderr,
                )
                return 1

            try:
                authenticator = KiteAuthenticator(
                    api_key=api_key, api_secret=api_secret, env_file=args.env_file
                )
                login_url = authenticator.get_login_url()
                print("=" * 80)
                print(f"1. Open this URL in your browser to log in to Kite:\n\n{login_url}\n")
                print("2. After logging in, you will be redirected to a URL like:")
                print("   https://your-redirect-url/?request_token=YOUR_REQUEST_TOKEN_HERE")
                print("=" * 80)

                request_token = input("Enter the request_token: ").strip()
                if not request_token:
                    print("Error: No request token provided.", file=sys.stderr)
                    return 1

                access_token = authenticator.generate_and_store_token(request_token)
                if access_token:
                    print(f"Success! Access token saved to {args.env_file}")
                    return 0

            except Exception as e:
                print(f"Authentication failed: {e}", file=sys.stderr)
                return 1
        else:
            parser.parse_args(["auth", "--help"])
            return 0

    if args.command == "ingest":
        if args.ingest_command == "universe":
            import os
            from datetime import date

            from vcp_scanner.data.repositories.duckdb_universe_repository import (
                DuckDBUniverseRepository,
            )
            from vcp_scanner.data.storage.duckdb_store import DuckDBStore
            from vcp_scanner.data.universe.builder import UniverseBuilder

            try:
                as_of = date.fromisoformat(args.as_of)
            except ValueError:
                print(
                    f"Error: Invalid date '{args.as_of}'. Use YYYY-MM-DD format.",
                    file=sys.stderr,
                )
                return 1

            try:
                cfg = load_scanner_config(args.config_dir)
            except Exception as e:
                print(f"Configuration error: {e}", file=sys.stderr)
                return 1

            db_path = args.db
            os.makedirs(
                os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True
            )

            with DuckDBStore(db_path) as store:
                store.migrate()
                builder = UniverseBuilder(store, cfg.universe)
                repo = DuckDBUniverseRepository(store)

                snapshot, memberships = builder.build_snapshot(as_of_date=as_of)
                repo.save_snapshot(snapshot, memberships)

                eligible = sum(m.eligible for m in memberships)
                excluded = len(memberships) - eligible
                print(f"Universe snapshot built for {as_of}")
                print(f"  Snapshot ID    : {snapshot.universe_snapshot_id}")
                print(f"  Total screened : {len(memberships)}")
                print(f"  Eligible       : {eligible}")
                print(f"  Excluded       : {excluded}")
                print(f"  Survivorship   : {snapshot.survivorship_status.value}")
                return 0

        elif args.ingest_command == "security-master":
            import os
            from datetime import UTC, date, datetime

            from vcp_scanner.data.ingestion.sm_worker import SecurityMasterIngestionWorker
            from vcp_scanner.data.providers.nse_security_master import (
                NSESecurityMasterProvider,
            )
            from vcp_scanner.data.providers.nse_surveillance import NSESurveillanceProvider
            from vcp_scanner.data.storage.duckdb_store import DuckDBStore

            try:
                start_date = date.fromisoformat(args.start)
                end_date = date.fromisoformat(args.end) if args.end else datetime.now(UTC).date()
            except ValueError as err:
                print(f"Error parsing dates: {err}", file=sys.stderr)
                return 1

            db_path = args.db
            os.makedirs(
                os.path.dirname(db_path) if os.path.dirname(db_path) else ".",
                exist_ok=True,
            )

            with DuckDBStore(db_path) as store:
                store.migrate()
                worker = SecurityMasterIngestionWorker(
                    store=store,
                    security_master_provider=NSESecurityMasterProvider(),
                    surveillance_provider=NSESurveillanceProvider(),
                )
                print(
                    f"Ingesting security master and surveillance flags "
                    f"from {start_date} to {end_date}..."
                )
                stats = worker.run(start=start_date, end=end_date)
                print("Ingestion complete:")
                for k, v in stats.items():
                    print(f"  {k}: {v}")
                return 0
        else:
            parser.parse_args(["ingest", "--help"])
            return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
