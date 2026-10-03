"""CLI entry point for vcp-scanner (PROJECT_DESIGN section 67)."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from vcp_scanner.config.loader import (
    compute_config_hash,
    load_logging_config,
    load_scanner_config,
    scan_config_hash,
    section_config_hashes,
)
from vcp_scanner.config.models import LoggingConfig
from vcp_scanner.domain.errors import ConfigError
from vcp_scanner.infrastructure.logging import configure_logging
from vcp_scanner.versioning import PACKAGE_VERSION, version_manifest

_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

DEFAULT_DB_PATH = "data/vcp_scanner.duckdb"  # matches data.duckdb_path in config/data.yaml


def _add_db_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--db",
        default=DEFAULT_DB_PATH,
        help="Path to DuckDB database file (default: data/vcp_scanner.duckdb)",
    )


def _add_config_dir_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config-dir",
        default="config",
        help="Path to directory containing configuration YAML files (default: config)",
    )


def _add_data_snapshot_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--data-snapshot-id",
        default="LIVE",
        metavar="SNAPSHOT_ID",
        help=(
            "Data snapshot to read prices from (created by `ingest adjusted-prices "
            "--known-at`). Default LIVE = unfrozen working data, NOT valid for validating "
            "thresholds or backtests."
        ),
    )


def _add_range_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--start",
        metavar="YYYY-MM-DD",
        default="2000-01-01",
        help="Start date (default: 2000-01-01)",
    )
    parser.add_argument("--end", metavar="YYYY-MM-DD", help="End date (default: today)")


def _add_instrument_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--instrument",
        action="append",
        metavar="ID_OR_SYMBOL",
        help="Instrument id or symbol (repeatable). Default: all instruments",
    )
    parser.add_argument("--limit", type=int, help="Process at most N instruments")


def _resolve_instrument_args(
    wanted: Sequence[str] | None, known_ids: Sequence[str]
) -> list[str] | None:
    """Map ``--instrument`` values (id or symbol, any case) to instrument ids.

    A value matching no known id is kept as given, so the command still reports it (e.g. as
    having no raw prices) instead of silently dropping it.
    """
    if not wanted:
        return None
    from vcp_scanner.cli_pipeline import _matches

    out: list[str] = []
    for value in wanted:
        hits = [iid for iid in known_ids if _matches(iid, [value])]
        out.extend(hits or [value])
    return list(dict.fromkeys(out))


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
    parser.add_argument(
        "--log-level",
        type=str.upper,
        choices=_LOG_LEVELS,
        default=None,
        help="Log level (overrides config/logging.yaml). Give before the command.",
    )
    parser.add_argument(
        "--log-format",
        choices=("text", "json"),
        default=None,
        help="Log format (overrides config/logging.yaml). Give before the command.",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        metavar="PATH",
        help="Also append logs to this file (overrides config/logging.yaml).",
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
    # Removed (audit P3-1): a secret on the command line ends up in shell history.
    kite_auth_parser.add_argument("--api-secret", help=argparse.SUPPRESS)
    kite_auth_parser.add_argument(
        "--env-file",
        default=".env",
        help="Path to .env file to read the API key/secret from and store the access token in "
        "(default: .env)",
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
        "--known-at",
        metavar="ISO_DATETIME",
        help=(
            "Rebuild the snapshot as it was known at this time (e.g. 2024-01-05T18:00:00+00:00 "
            "or 2024-01-05; naive values are UTC). Default: everything known now"
        ),
    )
    universe_parser.add_argument(
        "--db",
        default=DEFAULT_DB_PATH,
        help="Path to DuckDB database file (default: data/vcp_scanner.duckdb)",
    )
    universe_parser.add_argument(
        "--config-dir",
        default="config",
        help="Path to directory containing configuration YAML files (default: config)",
    )
    universe_parser.add_argument(
        "--allow-provisional",
        action="store_true",
        help=(
            "Use today's PROVISIONAL Kite bar (taken before the NSE bhavcopy is published). "
            "Default: provisional bars are ignored"
        ),
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
    sm_delisted = sm_parser.add_mutually_exclusive_group()
    sm_delisted.add_argument(
        "--delisted-file",
        metavar="PATH",
        help=(
            "Local copy of NSE's 'List of Companies Delisted from NSE' (.xlsx) instead of "
            "downloading it from nseindia.com"
        ),
    )
    sm_delisted.add_argument(
        "--no-delisted",
        action="store_true",
        help=(
            "Skip the delisted-companies list. Survivorship stays BIASED: only currently "
            "listed securities are known"
        ),
    )
    sm_parser.add_argument(
        "--db",
        default=DEFAULT_DB_PATH,
        help="Path to DuckDB database file (default: data/vcp_scanner.duckdb)",
    )

    adjusted_parser = ingest_subparsers.add_parser(
        "adjusted-prices",
        help="Build adjusted daily prices from raw prices and stored adjustment factors",
    )
    adjusted_parser.add_argument(
        "--known-at",
        metavar="ISO_DATETIME",
        help=(
            "Freeze a data snapshot at this system time (e.g. 2024-02-01T18:00:00+00:00) and "
            "build under it, reading raw prices and adjustment factors as known then. "
            "Omit for unfrozen LIVE data."
        ),
    )
    adjusted_parser.add_argument(
        "--instrument",
        action="append",
        metavar="INSTRUMENT_ID",
        help="Instrument to build (repeatable). Default: every instrument with raw prices",
    )
    adjusted_parser.add_argument(
        "--allow-provisional",
        action="store_true",
        help=(
            "Use today's PROVISIONAL Kite bar (taken before the NSE bhavcopy is published). "
            "Default: provisional bars are ignored"
        ),
    )
    adjusted_parser.add_argument(
        "--db",
        default=DEFAULT_DB_PATH,
        help="Path to DuckDB database file (default: data/vcp_scanner.duckdb)",
    )

    market_parser = ingest_subparsers.add_parser(
        "market",
        help=(
            "Kite Connect bars: today's PROVISIONAL bar (--today), or Kite history for "
            "comparison (--kite-history). Daily history comes from `ingest bhavcopy`"
        ),
    )
    _add_range_args(market_parser)
    _add_instrument_args(market_parser)
    market_mode = market_parser.add_mutually_exclusive_group()
    market_mode.add_argument(
        "--today",
        action="store_true",
        help=(
            "Fetch today's bar (IST) and store it as PROVISIONAL until the NSE bhavcopy "
            "supersedes it; --start/--end are ignored"
        ),
    )
    market_mode.add_argument(
        "--kite-history",
        action="store_true",
        help=(
            "Ingest Kite's (provider-adjusted) history for --start..--end. Comparison only: "
            "the price source of truth is `vcp ingest bhavcopy`"
        ),
    )
    market_parser.add_argument(
        "--force",
        action="store_true",
        help="Re-fetch even if the range is already ingested",
    )
    _add_db_arg(market_parser)
    _add_config_dir_arg(market_parser)
    market_parser.add_argument(
        "--env-file", default=".env", help="Path to .env file with Kite credentials"
    )

    bhav_parser = ingest_subparsers.add_parser(
        "bhavcopy",
        help="Ingest raw daily bars from NSE bhavcopy files (the price source of truth)",
    )
    bhav_parser.add_argument(
        "--start", metavar="YYYY-MM-DD", required=True, help="First calendar day to ingest"
    )
    bhav_parser.add_argument("--end", metavar="YYYY-MM-DD", help="Last day (default: today, IST)")
    bhav_parser.add_argument(
        "--refresh",
        action="store_true",
        help="Re-download files even if cached / already ingested",
    )
    bhav_parser.add_argument(
        "--cache-dir",
        help="Where raw bhavcopy zips are kept (default: <raw_storage_dir>/bhavcopy)",
    )
    _add_db_arg(bhav_parser)
    _add_config_dir_arg(bhav_parser)

    ca_parser = ingest_subparsers.add_parser(
        "corporate-actions",
        help="Ingest and reconcile corporate actions (NSE primary, Upstox secondary)",
    )
    _add_range_args(ca_parser)
    _add_instrument_args(ca_parser)
    _add_db_arg(ca_parser)
    _add_config_dir_arg(ca_parser)
    ca_parser.add_argument(
        "--env-file", default=".env", help="Path to .env file with Upstox credentials"
    )

    # compute subcommands
    compute_parser = subparsers.add_parser("compute", help="Feature and trend computation")
    compute_subparsers = compute_parser.add_subparsers(
        dest="compute_command", help="Compute operations"
    )

    features_parser = compute_subparsers.add_parser(
        "features", help="Compute daily features and weekly price aggregates"
    )
    features_parser.add_argument(
        "--instrument",
        action="append",
        metavar="INSTRUMENT_ID",
        help="Instrument to compute (repeatable). Default: every instrument with adjusted prices",
    )
    _add_db_arg(features_parser)
    _add_data_snapshot_arg(features_parser)

    rs_parser = compute_subparsers.add_parser(
        "rs", help="Compute relative-strength ranks for a universe snapshot"
    )
    rs_parser.add_argument("--as-of", required=True, metavar="YYYY-MM-DD")
    rs_parser.add_argument(
        "--universe-snapshot-id",
        help="Universe snapshot to rank (default: latest snapshot for --as-of)",
    )
    _add_db_arg(rs_parser)
    _add_config_dir_arg(rs_parser)
    _add_data_snapshot_arg(rs_parser)

    tt_parser = compute_subparsers.add_parser(
        "trend-template", help="Evaluate the trend template for eligible universe members"
    )
    tt_parser.add_argument("--as-of", required=True, metavar="YYYY-MM-DD")
    tt_parser.add_argument(
        "--instrument",
        action="append",
        metavar="INSTRUMENT_ID",
        help="Restrict to an instrument (repeatable)",
    )
    tt_parser.add_argument(
        "--universe-snapshot-id",
        help="Universe snapshot to evaluate (default: latest snapshot for --as-of)",
    )
    _add_db_arg(tt_parser)
    _add_config_dir_arg(tt_parser)
    _add_data_snapshot_arg(tt_parser)

    vcp_parser = compute_subparsers.add_parser(
        "vcp",
        help=(
            "Detect VCPs over the Trend Template scan of a date (same config); compute dates in "
            "order, breakout tracking reads the previous date"
        ),
    )
    vcp_parser.add_argument("--as-of", required=True, metavar="YYYY-MM-DD")
    vcp_parser.add_argument(
        "--instrument",
        action="append",
        metavar="INSTRUMENT_ID",
        help="Restrict to an instrument (repeatable)",
    )
    _add_db_arg(vcp_parser)
    _add_config_dir_arg(vcp_parser)
    _add_data_snapshot_arg(vcp_parser)

    scores_parser = compute_subparsers.add_parser(
        "scores",
        help="Score every Trend Template passer of a date and rank the eligible VCP setups "
        "(needs the Trend Template and VCP scans of the same date and config)",
    )
    scores_parser.add_argument("--as-of", required=True, metavar="YYYY-MM-DD")
    _add_db_arg(scores_parser)
    _add_config_dir_arg(scores_parser)
    _add_data_snapshot_arg(scores_parser)

    labels_parser = compute_subparsers.add_parser(
        "labels",
        help="Fill in forward labels (returns after 5-60 sessions, breakouts) for every scored "
        "observation that is not complete yet",
    )
    _add_db_arg(labels_parser)
    _add_config_dir_arg(labels_parser)
    _add_data_snapshot_arg(labels_parser)

    # verify subcommands (audit 2026-09-30 P0-1)
    verify_parser = subparsers.add_parser(
        "verify", help="Read-only checks of provider behavior against assumptions"
    )
    verify_subparsers = verify_parser.add_subparsers(
        dest="verify_command", help="Verification checks"
    )
    vka = verify_subparsers.add_parser(
        "kite-adjustment",
        help=(
            "Check whether Kite returns split/bonus-adjusted history (fetches a few bars around "
            "known ex-dates; writes nothing)"
        ),
    )
    vka.add_argument(
        "--action",
        action="append",
        metavar="SYMBOL:YYYY-MM-DD:SPLIT|BONUS:NUM:DEN",
        help=(
            "A known action to test (repeatable), e.g. ABC:2024-05-10:SPLIT:10:1 (face value "
            "10 -> 1) or XYZ:2023-09-01:BONUS:1:1. Default: recent applied splits/bonuses "
            "stored in the database."
        ),
    )
    vka.add_argument("--limit", type=int, default=3, help="Actions to take from the database")
    _add_db_arg(vka)
    vka.add_argument("--env-file", default=".env", help="Path to .env file with Kite credentials")

    vkc = verify_subparsers.add_parser(
        "kite-crosscheck",
        help=(
            "Compare our adjusted closes with Kite's history and attribute every difference "
            "to a corporate action (fetches Kite history; writes nothing)"
        ),
    )
    vkc.add_argument(
        "--instrument",
        action="append",
        required=True,
        metavar="ID_OR_SYMBOL",
        help="Instrument to check (repeatable)",
    )
    vkc.add_argument("--start", metavar="YYYY-MM-DD", help="First day (default: 2 years ago)")
    vkc.add_argument("--end", metavar="YYYY-MM-DD", help="Last day (default: today)")
    vkc.add_argument(
        "--tolerance",
        type=float,
        default=0.002,
        help="Relative step in the Kite/ours ratio treated as a difference (default 0.002)",
    )
    _add_db_arg(vkc)
    vkc.add_argument("--env-file", default=".env", help="Path to .env file with Kite credentials")

    vsc = verify_subparsers.add_parser(
        "scan",
        help=(
            "Rebuild a recorded scan run at its cutoff on a copy of the database and compare "
            "its results hash (audit P1-8); without an id, list recent scan runs"
        ),
    )
    vsc.add_argument("scan_run_id", nargs="?", help="Scan run to verify (omit to list runs)")
    vsc.add_argument("--work-dir", help="Where to put the temporary copy (default: next to --db)")
    vsc.add_argument(
        "--keep", action="store_true", help="Keep the rebuilt copy (a frozen scan worth keeping)"
    )
    vsc.add_argument("--limit", type=int, default=20, help="Runs to list (default 20)")
    _add_db_arg(vsc)
    _add_config_dir_arg(vsc)

    # run subcommands: the evening pipeline with catch-up
    run_parser = subparsers.add_parser("run", help="Pipelines that chain several commands")
    run_sub = run_parser.add_subparsers(dest="run_command", help="Pipelines")
    daily_parser = run_sub.add_parser(
        "daily",
        help=(
            "Evening run: security master + ASM/GSM lists, NSE bhavcopy, corporate actions, "
            "adjusted prices, features, then universe/RS/Trend Template for every new session. "
            "Catches up after skipped evenings (except the surveillance lists of those days)."
        ),
    )
    daily_parser.add_argument(
        "--history-start",
        default="2021-01-01",
        metavar="YYYY-MM-DD",
        help="First day of history (used only when the database has no bhavcopy yet)",
    )
    _add_db_arg(daily_parser)
    _add_config_dir_arg(daily_parser)
    daily_parser.add_argument("--env-file", default=".env", help="Path to .env file")
    daily_parser.add_argument(
        "--backup-dir",
        default=None,
        help="Where the pre-run database backups go (default: <db folder>/backups)",
    )
    daily_parser.add_argument(
        "--backup-keep", type=int, default=3, help="Number of backups to keep (default 3)"
    )
    daily_parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip the pre-run health check and backup (not recommended)",
    )

    # quality subcommands (audit P0-2)
    from vcp_scanner.cli_research import add_research_parser

    add_research_parser(subparsers)

    scores_cmd = subparsers.add_parser("scores", help="Read stored setup scores (Phase 7)")
    scores_sub = scores_cmd.add_subparsers(dest="scores_command")
    sl = scores_sub.add_parser("list", help="Ranked setups of a date (default: latest scored)")
    sl.add_argument("--as-of", metavar="YYYY-MM-DD")
    sl.add_argument("--all", action="store_true", help="Also show unranked passers")
    sl.add_argument("--limit", type=int, default=30)
    _add_db_arg(sl)
    _add_config_dir_arg(sl)
    se = scores_sub.add_parser("explain", help="How one stock's score was built")
    se.add_argument("symbol", help="NSE symbol or instrument id")
    se.add_argument("--as-of", metavar="YYYY-MM-DD")
    _add_db_arg(se)
    _add_config_dir_arg(se)

    quality_parser = subparsers.add_parser("quality", help="Data-quality events that block signals")
    quality_subparsers = quality_parser.add_subparsers(
        dest="quality_command", help="Quality operations"
    )

    qscan = quality_subparsers.add_parser(
        "scan",
        help="Run the gap safety net and sync corporate-action conflicts into the signal gate",
    )
    _add_instrument_args(qscan)
    _add_db_arg(qscan)
    _add_config_dir_arg(qscan)

    qlist = quality_subparsers.add_parser("list", help="List data-quality events")
    qlist.add_argument(
        "--instrument",
        action="append",
        metavar="INSTRUMENT_ID",
        help="Restrict to an instrument (repeatable)",
    )
    qlist.add_argument("--all", action="store_true", help="Include resolved events")
    _add_db_arg(qlist)

    qresolve = quality_subparsers.add_parser(
        "resolve",
        help="Record a human decision closing an event (e.g. a gap confirmed genuine)",
    )
    qresolve.add_argument("event_id", metavar="EVENT_ID")
    qresolve.add_argument("--by", required=True, help="Who is making the decision")
    qresolve.add_argument("--note", required=True, help="Why (kept as the audit trail)")
    _add_db_arg(qresolve)

    return parser


def _setup_logging(args: argparse.Namespace) -> None:
    """Configure structured logging: CLI flags > config/logging.yaml > defaults.

    Logs go to stderr so command output on stdout stays clean. Logging problems must never
    stop a command from running, so an unreadable logging config falls back to defaults
    with a warning on stderr.
    """
    try:
        log_cfg = load_logging_config(getattr(args, "config_dir", "config"))
    except ConfigError as err:
        print(f"Warning: ignoring logging configuration: {err}", file=sys.stderr)
        log_cfg = LoggingConfig()

    level = args.log_level or log_cfg.level
    json_format = log_cfg.json_format if args.log_format is None else args.log_format == "json"
    log_file = args.log_file or log_cfg.log_file

    try:
        configure_logging(level=level, json_format=json_format, log_file=log_file)
    except OSError as err:
        print(f"Warning: cannot open log file {log_file!r}: {err}", file=sys.stderr)
        configure_logging(level=level, json_format=json_format)


def _apply_nse_user_agent(args: argparse.Namespace) -> None:
    """Use ``data.nse_user_agent`` from the config folder for NSE requests (audit P3-1).

    Commands without ``--config-dir`` read the default ``config`` folder; a missing or invalid
    config leaves the built-in string (commands that need the config report the error).
    """
    from vcp_scanner.data.providers.nse_http import set_nse_user_agent

    try:
        cfg = load_scanner_config(getattr(args, "config_dir", None) or "config")
    except Exception:
        return
    set_nse_user_agent(cfg.data.nse_user_agent)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _apply_nse_user_agent(args)

    if args.command is not None:
        _setup_logging(args)

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
                print(f"Scan config hash  : {scan_config_hash(cfg)}")
                for section, digest in section_config_hashes(cfg).items():
                    print(f"  {section:<9}: {digest}")
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

            from dotenv import load_dotenv

            from vcp_scanner.auth.kite_auth import KiteAuthenticator

            # Read KITE_API_KEY / KITE_API_SECRET from --env-file like every other command.
            # Variables already set in the environment win (load_dotenv does not override).
            from vcp_scanner.cli_pipeline import env_secret

            load_dotenv(args.env_file)
            api_key = args.api_key or env_secret("KITE_API_KEY")
            if args.api_secret:
                print(
                    "Error: --api-secret was removed because the command line is saved in shell "
                    f"history. Put KITE_API_SECRET in {args.env_file} (or the environment).",
                    file=sys.stderr,
                )
                return 2
            api_secret = env_secret("KITE_API_SECRET")

            if not api_key or not api_secret:
                print(
                    "Error: Kite API Key and Secret are required "
                    f"(set KITE_API_KEY/KITE_API_SECRET in {args.env_file}, the environment, "
                    "or pass --api-key).",
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
            from datetime import UTC, date, datetime

            from vcp_scanner.data.repositories.duckdb_quality_repository import (
                DuckDBDataQualityRepository,
            )
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

            known_at: datetime | None = None
            if args.known_at:
                try:
                    known_at = datetime.fromisoformat(args.known_at)
                except ValueError:
                    print(
                        f"Error: Invalid --known-at '{args.known_at}'. "
                        "Use an ISO date or datetime.",
                        file=sys.stderr,
                    )
                    return 1
                if known_at.tzinfo is None:
                    known_at = known_at.replace(tzinfo=UTC)

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
                repo = DuckDBUniverseRepository(store)
                builder = UniverseBuilder(
                    repo,
                    cfg.universe,
                    quality_gate=DuckDBDataQualityRepository(
                        store, block_lifetime_bars=cfg.data.quality.block_lifetime_bars
                    ),
                    gate_settings=cfg.data.quality,
                    include_provisional=args.allow_provisional,
                )

                snapshot, memberships = builder.build_snapshot(as_of_date=as_of, known_at=known_at)
                repo.save_snapshot(snapshot, memberships)

                eligible = sum(m.eligible for m in memberships)
                excluded = len(memberships) - eligible
                print(f"Universe snapshot built for {as_of}")
                print(f"  Snapshot ID    : {snapshot.universe_snapshot_id}")
                print(f"  Total screened : {len(memberships)}")
                print(f"  Eligible       : {eligible}")
                print(f"  Excluded       : {excluded}")
                print(f"  Survivorship   : {snapshot.survivorship_status.value}")
                if snapshot.survivorship_detail:
                    print(f"    Why          : {snapshot.survivorship_detail}")
                return 0

        elif args.ingest_command == "security-master":
            import os
            from datetime import UTC, date, datetime

            from vcp_scanner.data.ingestion.sm_worker import SecurityMasterIngestionWorker
            from vcp_scanner.data.providers.nse_delisted import NSEDelistedProvider
            from vcp_scanner.data.providers.nse_security_master import (
                NSESecurityMasterProvider,
            )
            from vcp_scanner.data.providers.nse_surveillance import NSESurveillanceProvider
            from vcp_scanner.data.repositories.duckdb_instrument_repository import (
                DuckDBInstrumentResolver,
            )
            from vcp_scanner.data.storage.duckdb_store import DuckDBStore
            from vcp_scanner.domain.errors import ProviderError

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
                    resolver=DuckDBInstrumentResolver(store),
                    delisting_provider=(
                        None
                        if args.no_delisted
                        else NSEDelistedProvider(file_path=args.delisted_file)
                    ),
                )
                print(
                    f"Ingesting security master and surveillance flags "
                    f"from {start_date} to {end_date}..."
                )
                if args.no_delisted:
                    print(
                        "Warning: delisted securities skipped; survivorship stays BIASED.",
                        file=sys.stderr,
                    )
                try:
                    stats = worker.run(start=start_date, end=end_date)
                except ProviderError as err:
                    print(f"Error: security master ingestion failed: {err}", file=sys.stderr)
                    if not args.no_delisted:
                        print(
                            "Hint: use --delisted-file PATH with a downloaded copy of NSE's "
                            "delisted list, or --no-delisted to skip it.",
                            file=sys.stderr,
                        )
                    return 1
                print("Ingestion complete:")
                for k, v in stats.items():
                    print(f"  {k}: {v}")
                return 0

        elif args.ingest_command == "adjusted-prices":
            import os
            from datetime import UTC, datetime

            from vcp_scanner.cli_pipeline import parse_known_at
            from vcp_scanner.data.adjustment.builder import (
                STATUS_BUILT,
                STATUS_FAILED,
                AdjustedPriceBuilder,
            )
            from vcp_scanner.data.repositories.duckdb_corporate_action_repository import (
                DuckDBCorporateActionRepository,
            )
            from vcp_scanner.data.repositories.duckdb_market_repository import (
                DuckDBMarketDataRepository,
            )
            from vcp_scanner.data.repositories.duckdb_snapshot_repository import (
                DuckDBSnapshotRepository,
            )
            from vcp_scanner.data.storage.duckdb_store import DuckDBStore

            known_at = None
            if args.known_at:
                known_at = parse_known_at(args.known_at)
                if known_at is None:
                    return 1

            db_path = args.db
            os.makedirs(
                os.path.dirname(db_path) if os.path.dirname(db_path) else ".",
                exist_ok=True,
            )

            with DuckDBStore(db_path) as store:
                store.migrate()
                data_snapshot = None
                if known_at is not None:
                    data_snapshot = DuckDBSnapshotRepository(store).create(
                        known_at,
                        created_at=datetime.now(UTC),
                        description="adjusted-prices build",
                    )
                market_repo = DuckDBMarketDataRepository(store)
                adjusted_builder = AdjustedPriceBuilder(
                    market_repo,
                    DuckDBCorporateActionRepository(store),
                    include_provisional=args.allow_provisional,
                )
                results = adjusted_builder.build_all(
                    computed_at=datetime.now(UTC),
                    instrument_ids=_resolve_instrument_args(
                        args.instrument, market_repo.load_priced_instrument_ids()
                    ),
                    snapshot=data_snapshot,
                )

            built = [r for r in results if r.status == STATUS_BUILT]
            failed = [r for r in results if r.status == STATUS_FAILED]
            print("Adjusted prices built:")
            if data_snapshot is not None:
                print(f"  Data snapshot     : {data_snapshot.data_snapshot_id}")
                print(f"  Known at          : {data_snapshot.known_at.isoformat()}")
            else:
                print("  Data snapshot     : LIVE (unfrozen; not valid for threshold validation)")
            print(f"  Instruments built : {len(built)}")
            print(f"  Rows written      : {sum(r.rows_written for r in built)}")
            print(f"  Skipped (no raw)  : {len(results) - len(built) - len(failed)}")
            print(f"  Failed            : {len(failed)}")
            for r in failed:
                print(f"    {r.instrument_id}: {r.error}", file=sys.stderr)
            return 1 if failed else 0
        elif args.ingest_command == "market":
            from vcp_scanner.cli_pipeline import run_market_ingest

            return run_market_ingest(args)

        elif args.ingest_command == "bhavcopy":
            from vcp_scanner.cli_pipeline import run_bhavcopy_ingest

            return run_bhavcopy_ingest(args)

        elif args.ingest_command == "corporate-actions":
            from vcp_scanner.cli_pipeline import run_corporate_actions

            return run_corporate_actions(args)
        else:
            parser.parse_args(["ingest", "--help"])
            return 0

    if args.command == "compute":
        from vcp_scanner import cli_labels, cli_pipeline, cli_scores, cli_vcp

        compute_runners = {
            "features": cli_pipeline.run_compute_features,
            "rs": cli_pipeline.run_compute_rs,
            "trend-template": cli_pipeline.run_compute_trend_template,
            "vcp": cli_vcp.run_compute_vcp,
            "scores": cli_scores.run_compute_scores,
            "labels": cli_labels.run_compute_labels,
        }
        runner = compute_runners.get(args.compute_command)
        if runner is None:
            parser.parse_args(["compute", "--help"])
            return 0
        return runner(args)

    if args.command == "scores":
        from vcp_scanner import cli_scores_view

        if args.scores_command == "list":
            return cli_scores_view.run_scores_list(args)
        if args.scores_command == "explain":
            return cli_scores_view.run_scores_explain(args)
        parser.parse_args(["scores", "--help"])
        return 0

    if args.command == "research":
        from vcp_scanner.cli_research import run_research

        return run_research(args)

    if args.command == "verify":
        if args.verify_command == "kite-adjustment":
            from vcp_scanner import cli_pipeline

            return cli_pipeline.run_verify_kite_adjustment(args)
        if args.verify_command == "kite-crosscheck":
            from vcp_scanner import cli_pipeline

            return cli_pipeline.run_verify_kite_crosscheck(args)
        if args.verify_command == "scan":
            from vcp_scanner.verify_scan import run_verify_scan

            return run_verify_scan(args, main)
        parser.parse_args(["verify", "--help"])
        return 0

    if args.command == "run":
        if args.run_command == "daily":
            from vcp_scanner.daily import run_daily

            return run_daily(args, main)
        parser.parse_args(["run", "--help"])
        return 0

    if args.command == "quality":
        from vcp_scanner import cli_pipeline

        quality_runners = {
            "scan": cli_pipeline.run_quality_scan,
            "list": cli_pipeline.run_quality_list,
            "resolve": cli_pipeline.run_quality_resolve,
        }
        quality_runner = quality_runners.get(args.quality_command)
        if quality_runner is None:
            parser.parse_args(["quality", "--help"])
            return 0
        return quality_runner(args)

    return 0


if __name__ == "__main__":
    sys.exit(main())
