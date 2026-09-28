"""Unit tests for version manifest and version identifiers (PROJECT_DESIGN section 60)."""

from vcp_scanner.versioning import (
    DATA_SCHEMA_VERSION,
    FUNDAMENTAL_ALGORITHM_VERSION,
    PACKAGE_VERSION,
    RS_ALGORITHM_VERSION,
    SCORING_VERSION,
    STAGE_ALGORITHM_VERSION,
    STRATEGY_VERSION,
    TREND_ALGORITHM_VERSION,
    VCP_ALGORITHM_VERSION,
    version_manifest,
)


def test_version_manifest_keys_and_values() -> None:
    manifest = version_manifest()
    expected_keys = {
        "package_version",
        "strategy_version",
        "trend_algorithm_version",
        "rs_algorithm_version",
        "stage_algorithm_version",
        "vcp_algorithm_version",
        "fundamental_algorithm_version",
        "scoring_version",
        "data_schema_version",
    }
    assert set(manifest.keys()) == expected_keys
    assert manifest["package_version"] == PACKAGE_VERSION
    assert manifest["strategy_version"] == STRATEGY_VERSION
    assert manifest["trend_algorithm_version"] == TREND_ALGORITHM_VERSION
    assert manifest["rs_algorithm_version"] == RS_ALGORITHM_VERSION
    assert manifest["stage_algorithm_version"] == STAGE_ALGORITHM_VERSION
    assert manifest["vcp_algorithm_version"] == VCP_ALGORITHM_VERSION
    assert manifest["fundamental_algorithm_version"] == FUNDAMENTAL_ALGORITHM_VERSION
    assert manifest["scoring_version"] == SCORING_VERSION
    assert manifest["data_schema_version"] == DATA_SCHEMA_VERSION
    assert isinstance(DATA_SCHEMA_VERSION, int)
