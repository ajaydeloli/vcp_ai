"""Live prices are display only (FRONTEND_SPECIFICATION 67.19; STRATEGY_SPECIFICATION 21.1).

Nothing outside ``vcp_scanner.api`` may import ``vcp_scanner.live``, and the live package may not
import anything that stores data or decides a strategy, nor write anywhere.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "vcp_scanner"
LIVE = SRC / "live"

#: Packages that decide or store: the live feed must never reach them.
FORBIDDEN_FOR_LIVE = (
    "duckdb", "pyarrow", "vcp_scanner.data.storage", "vcp_scanner.data.repositories",
    "vcp_scanner.data.ingestion", "vcp_scanner.paper", "vcp_scanner.backtest",
    "vcp_scanner.scoring", "vcp_scanner.patterns", "vcp_scanner.features",
    "vcp_scanner.research", "vcp_scanner.daily", "vcp_scanner.serving",
)  # fmt: skip
#: The only modules that may import the live package: the API and the ``vcp api`` command.
ALLOWED_IMPORTERS = ("api/", "cli_api.py")


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.append(node.module)
    return out


def _py(folder: Path) -> list[Path]:
    return [p for p in folder.rglob("*.py") if "__pycache__" not in p.parts]


def test_only_the_api_imports_the_live_package() -> None:
    offenders = []
    for path in _py(SRC):
        rel = path.relative_to(SRC).as_posix()
        if rel.startswith("live/") or rel.startswith(ALLOWED_IMPORTERS):
            continue
        mods = _imports(path)
        if any(m == "vcp_scanner.live" or m.startswith("vcp_scanner.live.") for m in mods):
            offenders.append(rel)
    assert offenders == []


def test_the_live_package_reaches_no_store_and_no_strategy_code() -> None:
    files = _py(LIVE)
    assert len(files) >= 7
    bad = [
        f"{p.name} imports {m}"
        for p in files
        for m in _imports(p)
        if m == "duckdb" or m.startswith(FORBIDDEN_FOR_LIVE)
    ]
    assert bad == []


def test_the_live_package_writes_nothing() -> None:
    writers = re.compile(
        r"\.write_text|\.write_bytes|open\([^)]*['\"][wax]|\.mkdir|\.unlink|shutil"
    )
    hits = [p.name for p in _py(LIVE) if writers.search(p.read_text(encoding="utf-8"))]
    assert hits == []


def test_the_daily_run_and_scans_never_start_it() -> None:
    for name in ("daily.py", "serving.py", "cli_scores.py", "cli_vcp.py", "cli_paper.py"):
        path = SRC / name
        if path.exists():
            assert not any(m.startswith("vcp_scanner.live") for m in _imports(path)), name
