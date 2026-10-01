"""Audit P1-8a (D3): scan ids depend only on the config sections that can change a result."""

from __future__ import annotations

from vcp_scanner.config.loader import scan_config_hash, section_config_hashes
from vcp_scanner.config.models import (
    DataConfig,
    LoggingConfig,
    MonitoringConfig,
    QualityGateConfig,
    ScannerConfig,
    StrategyConfig,
    TrendTemplateConfig,
    UniverseConfig,
)
from vcp_scanner.data.universe.builder import UniverseBuilder

BASE = ScannerConfig()


def test_sections_are_strategy_universe_and_gate() -> None:
    assert set(section_config_hashes(BASE)) == {"strategy", "universe", "gate"}


def test_logging_monitoring_and_paths_do_not_change_the_scan_hash() -> None:
    for other in (
        ScannerConfig(logging=LoggingConfig(level="DEBUG")),
        ScannerConfig(monitoring=MonitoringConfig(poll_interval_seconds=5)),
        ScannerConfig(data=DataConfig(duckdb_path="elsewhere.duckdb")),
    ):
        assert scan_config_hash(other) == scan_config_hash(BASE)


def test_strategy_universe_and_gate_changes_do() -> None:
    changed = [
        ScannerConfig(strategy=StrategyConfig(trend_template=TrendTemplateConfig(min_rs_rank=75))),
        ScannerConfig(universe=UniverseConfig(min_close_price=50.0)),
        ScannerConfig(data=DataConfig(quality=QualityGateConfig(absence_min_missed_sessions=30))),
    ]
    hashes = {scan_config_hash(c) for c in changed}
    assert scan_config_hash(BASE) not in hashes and len(hashes) == 3


class _NoInputs:
    def load_universe_candidates(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        return []


def test_universe_snapshot_hash_includes_gate_settings_when_gated() -> None:
    plain = UniverseBuilder(_NoInputs(), UniverseConfig())._hash_config()  # type: ignore[arg-type]
    gated = [
        UniverseBuilder(
            _NoInputs(),
            UniverseConfig(),
            gate_settings=QualityGateConfig(block_lifetime_bars=n),  # type: ignore[arg-type]
        )._hash_config()
        for n in (253, 300)
    ]
    assert plain not in gated and gated[0] != gated[1]
