#!/usr/bin/env python3
"""Project bootstrap script (PROJECT_DESIGN section 49).

Ensures required directories exist, verifies configuration integrity,
computes configuration hash, and outputs environment status.
"""

from __future__ import annotations

import sys
from pathlib import Path

from vcp_scanner.config.loader import compute_config_hash, load_scanner_config
from vcp_scanner.domain.errors import ConfigError
from vcp_scanner.versioning import version_manifest

REQUIRED_DIRS = [
    Path("data"),
    Path("data/raw"),
    Path("data/canonical"),
    Path("reports"),
    Path("notebooks"),
    Path("config"),
]


def bootstrap() -> int:
    print("=" * 60)
    print("NSE VCP Scanner - Project Bootstrap")
    print("=" * 60)

    # 1. Ensure required runtime directories exist
    for directory in REQUIRED_DIRS:
        directory.mkdir(parents=True, exist_ok=True)
        print(f"[OK] Directory ready: {directory}")

    # 2. Check and validate configuration
    try:
        config = load_scanner_config("config")
        cfg_hash = compute_config_hash(config)
        print("[OK] Configuration validated successfully.")
        print(f"[OK] Deterministic configuration hash: {cfg_hash}")
    except ConfigError as err:
        print(f"[ERROR] Configuration validation failed: {err}", file=sys.stderr)
        return 1

    # 3. Print version manifest
    print("\nVersion Manifest:")
    for key, value in version_manifest().items():
        print(f"  {key:<32}: {value}")

    print("\n[OK] Phase 0 Bootstrap Complete. Project is ready.")
    return 0


if __name__ == "__main__":
    sys.exit(bootstrap())
