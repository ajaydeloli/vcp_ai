"""Architectural boundary enforcement tests (AGENTS.md rule 3, PROJECT_DESIGN section 67).

Acceptance criterion: No strategy, domain, scoring, or pattern code depends
directly on broker SDKs (kiteconnect, dhanhq) or direct storage engines (duckdb, pyarrow).
"""

import ast
from pathlib import Path

FORBIDDEN_STRATEGY_IMPORTS = {
    "kiteconnect",
    "dhanhq",
    "duckdb",
    "pyarrow",
}

STRATEGY_PACKAGES = [
    "src/vcp_scanner/domain",
    "src/vcp_scanner/trend",
    "src/vcp_scanner/patterns",
    "src/vcp_scanner/scoring",
    "src/vcp_scanner/indicators",
    "src/vcp_scanner/fundamentals",
    "src/vcp_scanner/universe",
]


def test_strategy_and_domain_have_no_broker_or_db_dependencies() -> None:
    project_root = Path(__file__).resolve().parent.parent.parent

    for package_dir in STRATEGY_PACKAGES:
        target_dir = project_root / package_dir
        if not target_dir.exists():
            continue

        for py_file in target_dir.rglob("*.py"):
            with py_file.open("r", encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=str(py_file))

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        root_mod = alias.name.split(".")[0]
                        assert (
                            root_mod not in FORBIDDEN_STRATEGY_IMPORTS
                        ), f"Forbidden import '{root_mod}' found in {py_file}"
                elif isinstance(node, ast.ImportFrom) and node.module:
                    root_mod = node.module.split(".")[0]
                    assert (
                        root_mod not in FORBIDDEN_STRATEGY_IMPORTS
                    ), f"Forbidden import '{root_mod}' found in {py_file}"
