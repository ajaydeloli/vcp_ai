# REVIEW_FIXES.md

Changes made in response to the design review. Spec versions: PROJECT_DESIGN / VCP / DATABASE / DATA / AI_AGENT_RULES bumped to 1.1. Three new files added.

## Fixes applied

| # | Issue | Fix | Where |
|---|---|---|---|
| 1 | Survivorship bias unsolvable with Kite alone | Separate `SecurityMasterProvider` / `CorporateActionProvider` / `SurveillanceProvider` interfaces; `survivorship_status` on scans and backtests; Kite limitations documented; `security_master_history` and `surveillance_flags_history` tables | PROJECT_DESIGN §10, §14A; DATA_SPEC §4A; DB §24A |
| 2 | Reproducibility not engineered | Bitemporal append-only canonical tables (`known_from`/`known_to`); adjusted prices moved to separate derived table; `snapshot_manifest` with content hashes; removed mutable `last_verified_at` | DB §14, §16, §41; DATA_SPEC §68A |
| 3 | Confirmation lag hits the right side | Provisional contraction rule, `confirmation_state` axis, alerts/ranking use CONFIRMED by default | VCP §9A; DB §31 |
| 4 | Trend Template / Stage 2 / RS unspecified | New spec: 10 conditions, SMA200 slope lookback, RS formula, weekly Stage 1–4 rules | TREND_TEMPLATE_SPECIFICATION.md |
| 5 | Schema stored verdicts, not measurements | `trend_template_conditions` (10 rows with measurement/threshold), `score_components`, raw VCP measurements in `vcp_patterns`, RS components | DB §26, §30, §31, §35 |
| 6 | Tolerance ambiguity | Defined as relative | VCP §13; PROJECT_DESIGN §22 |
| 7 | Depth basis undefined | Adjusted high/low | VCP §10 |
| 8 | `require_*` flags had no thresholds | Operational definitions and config keys | VCP §18A, §60 |
| 9 | Volume double-counted | Volume removed from VCP Score, scored once in Volume Score | VCP §33; SCORING §4–5; PROJECT_DESIGN §28 |
| 10 | Config/naming conflicts | Staleness = 1 trading day; liquidity 20d/50d on raw values; breakout key `min_volume_ratio`; provider method names unified; `INSUFFICIENT_DATA` spelling; table names aligned to DATABASE_SCHEMA; `sme_flag` typo; stray citation markers removed | multiple |
| 11 | `VCP_LIKE` production vs watchlist; mixed state machine | Classification / status / confirmation are three separate axes; `VCP_LIKE` is watchlist only | VCP §5, §28; PROJECT_DESIGN §35 |
| 12 | "First qualifying base" nondeterministic | Persist all candidates; deterministic primary selection | VCP §61, §61A |
| 13 | Fundamentals key collision, no restatements | `statement_basis`, `revision_number`, as-of revision selection, basis policy | DB §36 |
| 14 | Scoring spec missing | New spec with normalization, bounds, NULL handling | SCORING_SPECIFICATION.md |
| 15 | Backtesting too late | Phase 6B minimal event-study harness | PROJECT_DESIGN §67 |
| 16 | Scope too broad | Deferred-scope list (Dhan reconciliation, ML, LLM, PostgreSQL, WebSocket) | PROJECT_DESIGN §83A |
| 17 | Golden dataset subjective | Blind labeling, two reviewers, 30% hold-out, ≥100 per class as a target | PROJECT_DESIGN §51; VCP §57 |
| 18 | No Kite login workflow; late breakout alerts | Auth lifecycle; interim quote polling for PIVOT_READY names | DATA_SPEC §28A; PROJECT_DESIGN Phase 12 |
| 19 | 134-rule agent file duplicated specs | Short `AGENTS.md` entry point; rules file marked as reference | AGENTS.md; AI_AGENT_RULES |

## Open decisions (need your input; I did not invent answers)

1. **Sources.** Corporate actions are decided: NSE primary, Upstox (by ISIN) secondary, gap detector as safety net (DATA_SPEC §18A). Security master, delistings and delisted price history are set to NSE files/bhavcopy as **candidates**, pending a coverage spike. Until verified, results stay `BIASED`/`PARTIAL`.
2. **RS formula.** I reinstated 40/20/20/20 over 63/126/189/252 days from your older design doc. Confirm it.
3. **Stage 1–4 thresholds** (30-week SMA, ±0.5% flat band, 10% prior advance) are my proposals.
4. **Operational thresholds** (dry-up 0.70, ATR ratio 0.80, right-side range 5%, scoring bounds and sub-weights) are placeholders. They are hypotheses to test in Phase 6B, not validated.
5. **Provisional patterns.** I defaulted to "compute from measured values, alert/rank on CONFIRMED only". Alternative: allow provisional A+ into ranking.
6. **NULL fundamentals.** I chose renormalize technical weights plus a flag. Alternative: exclude such setups from the ranked list.
7. **Kite facts** (dump contents, adjusted vs raw candles, token expiry, rate limits) are flagged "verify", and I did not check them against current docs.

## Update: corporate-action sourcing

- NSE feed primary, Upstox by ISIN secondary, reconciliation statuses, `PROVIDER_CONFLICT` signal blocking (DATA_SPEC §18A; DB §17A).
- `UNEXPLAINED_GAP` replaces `DATA_ANOMALY` / `EXTREME_GAP` everywhere. It compares raw open with the raw previous close.
- Provider-adjusted candles are cross-check only. No flush-and-refetch.
- Per-action factor, source and version stored; 5–10 golden split/bonus fixtures required.
- ISIN is a mapped attribute (`isin_history`), not the permanent identity.
- **Additions of mine to review:** a gap that also looks like a split ratio blocks signals, while other gaps only warn; `secondary_grace_days` = 3; ratio detection `p/q ≤ 10`, 3% tolerance.
- Upstox checks (ratio/ex-date usable, history depth, mergers, delisted symbols) and Kite/Upstox candle adjustment remain **unverified**.

## Not done

- No migrations or code (specs only).
- Cross-references were checked by script for structure, not read end-to-end. Skim the diffs.
