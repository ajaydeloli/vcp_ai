# Changelog

All notable changes to the Institutional-Grade NSE VCP Scanner will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-09-28

### Added
- **Phase 0 — Architecture baseline delivery**:
  - Repository skeleton conforming to `PROJECT_DESIGN.md` §49.
  - Core domain models in `vcp_scanner.domain` (`market`, `trend`, `vcp`, `scoring`, `fundamentals`, `enums`, `errors`).
  - Strict StrEnum enumerations for classifications, lifecycle statuses, confirmation states, and error categories.
  - Provider protocols in `vcp_scanner.data.providers.base` (`MarketDataProvider`, `SecurityMasterProvider`, `CorporateActionProvider`, `SurveillanceProvider`, `FundamentalProvider`).
  - Repository protocols in `vcp_scanner.data.repositories.base` (`MarketDataRepository`, `UniverseRepository`, `ScanRepository`, `FundamentalRepository`).
  - Pattern and alert protocols in `vcp_scanner.patterns.base` (`PatternDetector`, `PatternConfirmer`) and `vcp_scanner.alerts.base` (`AlertChannel`).
  - Structured logging with JSON and text formatters in `vcp_scanner.infrastructure.logging`, supporting contextual field enrichment (`scan_id`, `run_id`, `symbol`, `component`, `duration_ms`).
  - Centralized version manifest in `vcp_scanner.versioning` covering package, strategy, algorithm, and data schema versions.
  - Configuration models and strict Pydantic validation in `vcp_scanner.config.models` implementing rules from `TREND_TEMPLATE_SPECIFICATION.md` and `SCORING_SPECIFICATION.md`.
  - Canonical deterministic configuration SHA-256 hash calculator and YAML loader in `vcp_scanner.config.loader`.
  - Production-ready YAML configurations under `config/`: `strategy.yaml`, `scoring.yaml`, `universe.yaml`, `data.yaml`, `monitoring.yaml`, `logging.yaml`.
  - Environment variables template in `.env.example`.
  - Command-line interface `vcp` in `vcp_scanner.cli` supporting `vcp version`, `vcp config validate`, and `vcp config hash`.
  - Complete Phase 0 test suite under `tests/unit/` covering domain models, config validation, protocol compliance, structured logging, CLI commands, and architectural boundary static analysis.
