"""Version identifiers (PROJECT_DESIGN section 60).

Rules:
* Changing detection logic requires a new ``VCP_ALGORITHM_VERSION``.
* Changing a scoring weight, bound or formula requires a new ``SCORING_VERSION``.
* Changing the RS formula requires a new ``RS_ALGORITHM_VERSION``.
* Changing DuckDB/Parquet table layout requires a migration and a new ``DATA_SCHEMA_VERSION``.

Algorithm versions for modules that are not built yet are declared here so that
results, configs and specs can reference them from day one. The tags for trend, RS,
stage and scoring come from their specifications; ``vcp-1.0.0`` and ``schema 1`` come
from the DATABASE_SCHEMA versioning example. Nothing here is a claim that the
algorithm is implemented.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Final

PACKAGE_VERSION: Final[str] = "0.1.0"

# Overall strategy definition (gates + detector + scoring taken together).
STRATEGY_VERSION: Final[str] = "strategy-1.0.0"

# Owned by TREND_TEMPLATE_SPECIFICATION.md
TREND_ALGORITHM_VERSION: Final[str] = "trend-1.0.0"
RS_ALGORITHM_VERSION: Final[str] = "rs-1.0.0"
STAGE_ALGORITHM_VERSION: Final[str] = "stage-1.0.0"

# Owned by SCORING_SPECIFICATION.md
SCORING_VERSION: Final[str] = "scoring-1.0.0"

# Owned by VCP_SPECIFICATION.md (detector arrives in Phase 6)
VCP_ALGORITHM_VERSION: Final[str] = "vcp-1.0.0"

# Owned by the fundamentals spec (Phase 8)
FUNDAMENTAL_ALGORITHM_VERSION: Final[str] = "fund-1.0.0"

# Owned by DATABASE_SCHEMA.md (migrations arrive in Phase 1)
DATA_SCHEMA_VERSION: Final[int] = 1


def version_manifest() -> dict[str, str | int]:
    """All version tags, for stamping onto scan runs (PROJECT_DESIGN section 44)."""
    return {
        "package_version": PACKAGE_VERSION,
        "strategy_version": STRATEGY_VERSION,
        "trend_algorithm_version": TREND_ALGORITHM_VERSION,
        "rs_algorithm_version": RS_ALGORITHM_VERSION,
        "stage_algorithm_version": STAGE_ALGORITHM_VERSION,
        "vcp_algorithm_version": VCP_ALGORITHM_VERSION,
        "fundamental_algorithm_version": FUNDAMENTAL_ALGORITHM_VERSION,
        "scoring_version": SCORING_VERSION,
        "data_schema_version": DATA_SCHEMA_VERSION,
    }


def code_state() -> tuple[str, bool | None]:
    """(git commit, dirty) of the code that is running, for scan-run records (audit P1-8).

    ``dirty`` is True when tracked files have uncommitted changes. Outside a git checkout (or
    without git) the answer is ``("unknown", None)``: never a guess.
    """
    root = Path(__file__).resolve().parents[2]
    try:
        commit = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return "unknown", None
    return (commit or "unknown"), bool(status.strip())
