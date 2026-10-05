"""Strategy dimension: ids, grades, scan ids (STRATEGY_SPECIFICATION 2, 4.3, 6.2-6.3).

A strategy is one base-pattern detector with its config, pattern score and ranking rule. Every
stored result names its strategy. VCP is strategy ``vcp`` and keeps its original scan ids.
"""

from __future__ import annotations

import re
from typing import Final

from vcp_scanner.domain.snapshot import LIVE_SNAPSHOT_ID

VCP_STRATEGY_ID: Final[str] = "vcp"

#: Lowercase, starting with a letter: scan ids put a date right after it (section 2).
STRATEGY_ID_PATTERN: Final[str] = r"^[a-z][a-z0-9_]*$"
_ID_RE = re.compile(STRATEGY_ID_PATTERN)

#: Statuses an eligible setup may have (section 6.3; SCORING_SPECIFICATION 11).
RANKED_STATUSES: Final[frozenset[str]] = frozenset({"FORMING", "PIVOT_READY", "BREAKOUT"})

#: Grades on the common 0-3 scale (section 6.3).
GRADE_NONE, GRADE_LIKE, GRADE_STANDARD, GRADE_A_PLUS = 0, 1, 2, 3

#: VCP tiers on the common scale (section 11.1).
VCP_GRADES: Final[dict[str, int]] = {
    "NONE": GRADE_NONE, "VCP_LIKE": GRADE_LIKE, "VCP": GRADE_STANDARD, "A_PLUS_VCP": GRADE_A_PLUS,
}  # fmt: skip


def is_valid_strategy_id(strategy_id: str) -> bool:
    return bool(_ID_RE.match(strategy_id))


def _with_snapshot(scan_id: str, data_snapshot_id: str) -> str:
    return scan_id if data_snapshot_id == LIVE_SNAPSHOT_ID else f"{scan_id}-{data_snapshot_id}"


def detector_scan_id(
    strategy_id: str, as_of_iso: str, config_hash: str, data_snapshot_id: str
) -> str:
    """``vcp-<date>-<hash12>`` for VCP (unchanged); ``setup-<id>-<date>-<hash12>`` otherwise."""
    if strategy_id == VCP_STRATEGY_ID:
        return _with_snapshot(f"vcp-{as_of_iso}-{config_hash[:12]}", data_snapshot_id)
    return _with_snapshot(f"setup-{strategy_id}-{as_of_iso}-{config_hash[:12]}", data_snapshot_id)


def score_scan_id(strategy_id: str, as_of_iso: str, config_hash: str, data_snapshot_id: str) -> str:
    """``score-<date>-<hash12>`` for VCP (unchanged); ``score-<id>-<date>-<hash12>`` otherwise."""
    if strategy_id == VCP_STRATEGY_ID:
        return _with_snapshot(f"score-{as_of_iso}-{config_hash[:12]}", data_snapshot_id)
    return _with_snapshot(f"score-{strategy_id}-{as_of_iso}-{config_hash[:12]}", data_snapshot_id)
