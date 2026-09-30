"""Architectural boundary enforcement (AGENTS.md rule 3; PROJECT_DESIGN section 6).

Strategy code (domain objects, universe rules, indicators consumers, RS, Trend Template,
weekly Stage, and later patterns/scoring/fundamentals) must not depend on broker SDKs,
HTTP clients, storage engines, or concrete adapters. It talks to repository and provider
*Protocols* in ``data.repositories.base`` / ``data.providers.base`` only.

Audit 2026-09-30 (Fix 6): the previous version scanned only empty packages, so it passed while
RS and the universe builder imported DuckDB directly. Every scanned package must now exist and
contain code, so the test cannot pass vacuously.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "vcp_scanner"

FORBIDDEN_ROOTS = {"kiteconnect", "dhanhq", "duckdb", "pyarrow", "requests", "urllib3"}
FORBIDDEN_INTERNAL_PREFIXES = (
    "vcp_scanner.data.storage",
    "vcp_scanner.data.ingestion",
    "vcp_scanner.data.features",  # SQL feature builders (storage side)
    "vcp_scanner.auth",
    "vcp_scanner.cli",
)
ALLOWED_INTERNAL = {"vcp_scanner.data.repositories.base", "vcp_scanner.data.providers.base"}

#: Strategy packages that must hold code today (scanned, and must not be empty).
STRATEGY_PACKAGES = ["domain", "features", "data/universe"]
#: Future strategy packages: scanned when they gain code.
FUTURE_STRATEGY_PACKAGES = ["patterns", "scoring", "fundamentals"]


def _imports(py_file: Path) -> list[str]:
    tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.append(node.module)
    return names


def _violations(module: str) -> str | None:
    root = module.split(".")[0]
    if root in FORBIDDEN_ROOTS:
        return f"external '{root}'"
    if module in ALLOWED_INTERNAL:
        return None
    if module.startswith(FORBIDDEN_INTERNAL_PREFIXES):
        return f"internal '{module}'"
    if module.startswith(("vcp_scanner.data.repositories.", "vcp_scanner.data.providers.")):
        return f"concrete adapter '{module}' (use the Protocol in *.base)"
    return None


def _files(package: str) -> list[Path]:
    return [p for p in (SRC / package).rglob("*.py") if "__pycache__" not in p.parts]


@pytest.mark.parametrize("package", STRATEGY_PACKAGES + FUTURE_STRATEGY_PACKAGES)
def test_strategy_code_depends_only_on_domain_config_and_protocols(package: str) -> None:
    files = _files(package)
    if package in STRATEGY_PACKAGES:
        code = [f for f in files if f.name != "__init__.py" and f.read_text().strip()]
        assert code, f"{package} has no code: the boundary test would pass vacuously"
    problems = [
        f"{f.relative_to(ROOT)} imports {why}"
        for f in files
        for module in _imports(f)
        if (why := _violations(module))
    ]
    assert not problems, "\n".join(problems)


def test_removed_duplicate_packages_stay_removed() -> None:
    """PROJECT_DESIGN section 6 layout: these empty duplicates invited parallel implementations."""
    for name in ("indicators", "trend", "universe", "data/normalization"):
        assert not (SRC / name).exists(), f"src/vcp_scanner/{name} was removed on purpose"


def test_the_checker_catches_a_violation() -> None:
    assert _violations("duckdb") is not None
    assert _violations("vcp_scanner.data.storage.duckdb_store") is not None
    assert _violations("vcp_scanner.data.repositories.duckdb_rs_repository") is not None
    assert _violations("vcp_scanner.data.repositories.base") is None
    assert _violations("vcp_scanner.config.models") is None
