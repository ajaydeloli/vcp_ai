# AGENTS.md — Read This First

Concise entry point for AI coding agents on the NSE VCP scanner. Details live in the specs. This file does not restate them.

## Specs (single source of truth per topic)

| Topic | Document |
|---|---|
| Architecture, phases, scope | `PROJECT_DESIGN.md` |
| Tables, columns, bitemporal rules | `DATABASE_SCHEMA.md` |
| Ingestion, providers, data quality, snapshots | `DATA_SPECIFICATION.md` |
| VCP detection, classification, confirmation | `VCP_SPECIFICATION.md` |
| Trend Template, RS formula, weekly Stage | `TREND_TEMPLATE_SPECIFICATION.md` |
| Scoring and ranking | `SCORING_SPECIFICATION.md` |
| Detailed agent rules (reference) | `AI_AGENT_RULES.md` |

If code and spec disagree: stop, name the conflict, propose the smallest change. Never silently pick one.

## Hard rules

1. **No look-ahead.** Functions take an explicit `as_of_date`. Never use `datetime.now()` in research code.
2. **Raw data is immutable.** Canonical tables are append-only/bitemporal. Adjusted prices are derived from stored adjustment factors, in their own table. Never flush and re-fetch history. Kite candles arrive adjusted as of their fetch time: the engine applies only actions after each bar's fetch date (DATA_SPECIFICATION §21.1); never apply a factor to a bar the provider already rescaled.
3. **Providers stay behind interfaces.** Strategy code never imports Kite/Dhan SDKs or touches DuckDB/Parquet directly. Use repositories. Strategy code = `domain/`, `features/`, `data/universe/`, `patterns/`, `scoring/`, `fundamentals/` (PROJECT_DESIGN §6); indicator SQL lives in `data/features/`. `tests/unit/test_architecture_rules.py` enforces this.
4. **Missing is not zero.** Use NULL plus a status. Keep `NO_VCP` distinct from `INSUFFICIENT_DATA`, `DATA_NOT_READY` and `STALE_DATA`.
5. **Detector decides, scorer ranks.** Never gate on a score. Fundamentals, ML and LLMs cannot override technical gates.
6. **Store measurements, not just verdicts.** Raw values, thresholds, ratios and versions.
7. **Thresholds are hypotheses.** Config-driven; changing one is a strategy change (spec update, version bump, note old/new/why). Weights are validated in code (sum = 100, fundamentals ≤ cap).
8. **Survivorship status is stamped** on every scan/backtest. Only `POINT_IN_TIME_COMPLETE` results may validate thresholds.
9. **No live order execution**, ever, as a side effect.
10. **No fabricated market data.** Fixtures are labeled synthetic.

## Workflow

1. Read the governing spec and the code you will touch (callers, tests, config).
2. For a non-trivial task, write a plan and list the files you will change before editing.
3. Make the smallest coherent change, with tests. Never skip, weaken or delete a test to get green.
4. Run targeted tests, then the full suite. Report exact results.
5. Update the spec if behavior or schema changed (new migration for schema changes).

## Completion report

What changed, files changed, tests run and results, docs updated, known limitations (say "not tested: ..." where true).

## Ask the user when

A strategy decision is needed, a spec is ambiguous, a destructive action is required, or provider behavior can't be verified from official docs.
