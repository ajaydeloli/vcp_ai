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

    return 0


if __name__ == "__main__":
    sys.exit(main())
