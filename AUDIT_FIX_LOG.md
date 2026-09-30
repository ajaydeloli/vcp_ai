# AUDIT_FIX_LOG.md

Record of fixes made in response to the independent Phase 0–5 audit of 2026-09-30
(`phase0-5-audit-2026-09-30.md`). Branch: `audit-fixes`, one commit per fix. Each fix is approved
by the project owner before work starts.

## Planned order

| # | Audit ID | Topic | Status |
|---|---|---|---|
| 1 | P0-3 | Gap safety net ignores non-applied actions; unknown split/bonus ratios block | Done |
| 2 | P0-2 | Reconciliation policy: dividends/rights and primary-only actions | Done |
| 3 | P1-9 | Reject NaN / zero / negative OHLC; NaN inputs are INSUFFICIENT_DATA | Done |
| 4 | P0-1 | Kite candles are provider-adjusted: stop double adjustment | Pending (needs live Kite check + sourcing decision) |
| 5 | P1-1 | Seed `instruments`; first real end-to-end run; real golden fixtures | Pending |
| 6 | P1-3 / P1-4 | Architecture boundary test covers real packages; package layout | Pending |
| 7 | P1-7 / P1-6 | VCP config shape per VCP_SPEC §60; real RS tests | Pending |

---

## Fix 1 — P0-3: gap safety net and unknown split/bonus ratios

**Problem.** `GapDetector` treated a raw gap as explained if *any* corporate-action resolution
shared its ex-date, whatever its type or status. A split whose ratio could not be parsed from NSE
text (resolution with NULL ratio) got factor 1.0 from `AdjustmentEngine`, so its price jump stayed
in the adjusted series, and the same resolution silenced the gap detector. A dividend or a
`PROVIDER_CONFLICT` on the same date had the same effect. Result: an unadjusted split looked like a
genuine −50 %/−90 % crash to SMAs, 52-week lows, RS and (later) VCP depths, with no event raised.

**Change.**
- `domain/corporate_actions.py`: `PRICE_SCALING_ACTIONS`, `has_usable_ratio`, `ratio_unknown`,
  `explains_price_gap` (single definition shared by the detector and the event builder).
- `data/reconciliation/gap_detector.py`: only `explains_price_gap` resolutions explain a gap.
- `data/quality/events.py`: `corporate_action_events` adds a blocking
  `CORPORATE_ACTION_UNRESOLVED` event (`cause = ratio_unknown`) per adjustable split/bonus without
  a usable ratio; always blocks (not governed by `conflict_blocks_signals`); not hand-resolvable
  (existing `_SUPERSEDE_ONLY` rule); clears when a resolution with a ratio supersedes it.
- `DATA_SPECIFICATION.md` §18A: rule wording updated ("applied" split/bonus; unknown-ratio event).
- `CHANGELOG.md`: entry under Unreleased.

**Tests.**
- New: `test_quality_events.py` — non-adjusting actions (no/zero/NaN ratio, conflict, dividend,
  rights) do not explain a gap; applied split/bonus under CONFIRMED/SINGLE_SOURCE/MANUAL_OVERRIDE
  does; unknown-ratio event blocks even with `conflict_blocks_signals=False`, has its own id,
  cannot be hand-resolved; scanner end-to-end: unparsed split → gap + ratio events block, both
  clear once a ratio arrives. `test_gap_detector.py` — dividend does not explain a split-like gap.
- Updated fixtures (not weakened): three tests used ratio-less CONFIRMED/SINGLE_SOURCE splits as
  "explanations" or bystanders; they now carry a ratio, since a ratio-less split is exactly the
  case this fix makes visible.

**Behavior change.** Instruments with an unparsed split/bonus ratio, or a split-like gap on a
dividend/conflict ex-date, now become `DATA_QUALITY_BLOCKED` and leave the universe/RS population.

**Not done here.** Per-record parse failures in the NSE/Upstox parsers are still only logged
(audit P1-10); the 30 % threshold still misses small bonuses (e.g. 1:4); split-like tolerance is
still absolute. Revisit with Fix 2 / Fix 5.

**Verification.** Targeted: 79 passed (quality events, gap detector, CA pipeline, quality gate).
Full suite: 466 passed, 0 failed (was 447). `ruff check` clean, `ruff format --check` clean,
`mypy --strict src` clean (93 files).

---

## Fix 2 — P0-2: reconciliation policy (dividends, rights, NSE-only actions)

**Owner decisions (2026-09-30).**
- A: only splits/bonuses block when sources disagree; dividend/rights conflicts are warnings.
- B: an NSE-only split/bonus past the grace period escalates to `PROVIDER_CONFLICT` only when
  Upstox is configured *and* was actually queried for that instrument over a window containing
  the ex-date. Otherwise it stays `SINGLE_SOURCE`. Dividends/rights never escalate.

**Problem.** (1) NSE's parser never sets `cash_amount`, Upstox always does, and the engine compared
them with `!=`, so every dividend reported by both sources became `PROVIDER_CONFLICT`.
(2) Every NSE-only action became `PROVIDER_CONFLICT` 3 days after first sighting, including when no
Upstox token was configured (contradicting the README). (3) Every conflict, of any type, blocked
signals from its ex-date indefinitely and could not be hand-resolved, and conflicts withdrew split
factors. Net effect: dividend payers were progressively removed from the universe and the RS
ranking population (selection bias), and NSE-only splits lost their adjustment.

**Change.**
- `data/reconciliation/engine.py`: `_cash_equivalent` (compare only when both present,
  `abs_tol=0.005`); `reconcile(..., secondary_window=None)`; `_check_grace_period` escalates only
  price-scaling actions whose ex-date lies in `secondary_window`; `conflict_fields =
  secondary_source_missing` for that case.
- `data/providers/upstox_ca.py`: `queried_instrument_ids` (HTTP 200 or 404 per ISIN; no-ISIN
  instruments are not "asked").
- `data/ingestion/ca_worker.py`: reads the secondary provider's coverage (none for providers that
  don't report it, e.g. the no-token stand-in), canonicalises ids, passes `(start, end)` per
  covered instrument.
- `data/quality/events.py`: conflict events block only for SPLIT/BONUS; DIVIDEND/RIGHTS conflicts
  are WARNING and non-blocking.
- Docs: README (credential table, known limitations), DATA_SPECIFICATION §18A table + policy note,
  DATABASE_SCHEMA §17A, CHANGELOG.

**Tests.**
- New: engine — NSE-only past grace without coverage / with coverage outside the ex-date stays
  SINGLE_SOURCE; dividend/rights never escalate; dividend with cash on one side only is CONFIRMED;
  cash tolerance both ways. Worker — no-coverage split keeps its factor; dividend in both sources
  doesn't block; dividend cash conflict is a non-blocking warning; NSE-only dividend never blocks.
  Provider — `queried_instrument_ids` records 200/404 instruments and excludes no-ISIN ones.
- Updated (policy change, intent kept): three tests asserting "NSE-only past grace → conflict"
  now declare that Upstox was queried for the instrument (the only case where that rule now
  applies).

**Behavior change.** On the next `vcp ingest corporate-actions`, resolutions are re-evaluated:
dividend-driven and unqueried NSE-only conflicts become CONFIRMED / SINGLE_SOURCE, their events
close (SYSTEM), and withheld split factors are restored. Rebuild adjusted prices afterwards.

**Not done here.** No manual-override command yet; blocks are still open-ended in time (audit
P1-2); NSE dividend amounts are still not parsed (not needed for correctness now).

**Verification.** Targeted: 106 passed. Full suite: 477 passed, 0 failed. `ruff check`,
`ruff format --check`, `mypy --strict src` clean.

---

## Fix 3 — P1-9: non-finite and non-positive values

**Problem.** `validate_ohlc` only checked orderings. NaN compares False with everything, so a
NaN bar passed and reached `daily_prices`; zero and negative prices passed too. Downstream, a NaN
close or SMA turned Trend Template conditions into a definite `False` → `FAIL` (violating "missing
is not zero"), and NaN in RS sorted above every value in DuckDB, distorting all percentiles.

**Change.**
- `data/schema.py` `validate_ohlc`: rejects non-finite price/volume and any price <= 0. (Raw
  audit copy in `raw_ohlcv` unchanged; the bar never reaches canonical data.)
- `features/trend_template.py`: `_finite()` maps non-finite inputs to None before evaluation
  (stored as NULL, status `INSUFFICIENT_DATA`); `close` extreme basis returns None if its window
  contains a non-finite close.
- `features/weekly_stage.py`: non-finite weekly close → `INSUFFICIENT_DATA` with NULL measurements.
- `features/relative_strength.py`: non-finite returns become NULL (`isfinite` guard) → instrument
  is `INSUFFICIENT_DATA` and excluded from the population.
- Docs: DATA_SPECIFICATION §23, CHANGELOG.

**Tests.** New `tests/unit/test_non_finite_values.py` (32 cases): bad bars rejected with reasons;
valid/flat/zero-volume bars still pass; NaN bar never lands in `daily_prices`; each of 7 TT inputs ×
{NaN, +inf, −inf} gives `INSUFFICIENT_DATA` with no non-finite value stored; weekly NaN close →
`INSUFFICIENT_DATA`; RS with one NaN as-of close: that instrument NULL/INSUFFICIENT_DATA, the other
four ranked over a population of 4. No existing test changed.

**Not done here.** Existing NaN rows already in a live `daily_prices` are not purged (bitemporal;
they are now neutralised downstream). A later `vcp ingest market --force` supersedes them.

**Verification.** Full suite: 509 passed, 0 failed. `ruff check`, `ruff format --check`,
`mypy --strict src` clean.
