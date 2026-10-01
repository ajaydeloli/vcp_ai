"""Pipeline CLI commands: market ingest, corporate actions, features, RS, trend template.

Each ``run_*`` function takes the parsed ``argparse.Namespace`` and returns a process exit
code. They are thin wiring over the engines and workers in ``data`` and ``features``; no
strategy logic lives here. Providers are built by small ``_build_*`` factories so tests can
substitute fakes without touching the network.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any

from vcp_scanner.config.loader import compute_config_hash, load_scanner_config
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.errors import ProviderError
from vcp_scanner.domain.market import Instrument
from vcp_scanner.domain.snapshot import LIVE_SNAPSHOT_ID, validate_snapshot_id
from vcp_scanner.versioning import PACKAGE_VERSION


class _NoActions:
    """Stand-in secondary provider used when no Upstox credentials are configured."""

    def get_actions(
        self, start: date, end: date, instruments: list[Instrument] | None = None
    ) -> list[Any]:
        return []


def _err(message: str) -> None:
    print(message, file=sys.stderr)


def env_secret(name: str) -> str | None:
    """A credential from the environment, or None when unset, blank or still the
    ``.env.example`` placeholder (``your_..._here``).

    Copying ``.env.example`` to ``.env`` leaves placeholders such as
    ``UPSTOX_ACCESS_TOKEN=your_upstox_access_token_here``; treating those as real credentials
    made optional providers fail the whole command with HTTP 401 (found in audit Fix 5b).
    """
    value = (os.getenv(name) or "").strip()
    if not value or (value.startswith("your_") and value.endswith("_here")):
        return None
    return value


def _load_env(env_file: str) -> None:
    from dotenv import load_dotenv

    load_dotenv(env_file)


def _open_store(db_path: str) -> DuckDBStore:
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    store = DuckDBStore(db_path)
    store.migrate()
    return store


def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        _err(f"Error: Invalid date '{value}'. Use YYYY-MM-DD format.")
        return None


def parse_known_at(value: str) -> datetime | None:
    """Parse an ISO ``--known-at`` value; a naive time is taken as UTC. None on error."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        _err(
            f"Error: Invalid --known-at '{value}'. Use ISO format, e.g. 2024-02-01T18:00:00+00:00."
        )
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _resolve_data_snapshot(store: DuckDBStore, requested: str | None) -> str | None:
    """Validate ``--data-snapshot-id``; ``LIVE`` (or unset) means unfrozen working data.

    Returns the id to use, or None after printing an error when it is malformed or was
    never created (a typo must not silently compute over nothing).
    """
    snapshot_id = requested or LIVE_SNAPSHOT_ID
    if snapshot_id == LIVE_SNAPSHOT_ID:
        return snapshot_id
    try:
        validate_snapshot_id(snapshot_id)
    except ValueError as e:
        _err(f"Error: {e}")
        return None
    from vcp_scanner.data.repositories.duckdb_snapshot_repository import (
        DuckDBSnapshotRepository,
    )

    if DuckDBSnapshotRepository(store).load(snapshot_id) is None:
        _err(
            f"Error: unknown data snapshot '{snapshot_id}'. "
            "Create it with `vcp ingest adjusted-prices --known-at ...`."
        )
        return None
    return snapshot_id


def _quality_gate(store: DuckDBStore, data_snapshot_id: str) -> Any:
    """The data-quality gate for a run: LIVE sees current events; a frozen data snapshot sees
    only events already detected (and unresolved) at its ``known_at``, so a later detection
    never blocks an earlier, reproducible run."""
    from vcp_scanner.data.repositories.duckdb_quality_repository import (
        DuckDBDataQualityRepository,
    )
    from vcp_scanner.data.repositories.duckdb_snapshot_repository import (
        DuckDBSnapshotRepository,
    )

    known_at = None
    if data_snapshot_id != LIVE_SNAPSHOT_ID:
        snapshot = DuckDBSnapshotRepository(store).load(data_snapshot_id)
        known_at = snapshot.known_at if snapshot else None
    return DuckDBDataQualityRepository(store, known_at=known_at)


def _matches(instrument_id: str, wanted: Sequence[str] | None) -> bool:
    """``--instrument`` filter: accepts an instrument id or a trading symbol, any case.

    Several commands compared ``--instrument RELIANCE`` against ids like ``NSE_EQ|RELIANCE``
    and silently selected nothing (the quality scan then checked 0 instruments; found in
    audit Fix 5b).
    """
    if not wanted:
        return True
    from vcp_scanner.data.identity import symbol_from_instrument_id

    keys = {w.upper() for w in wanted}
    symbol = symbol_from_instrument_id(instrument_id) or ""
    return instrument_id.upper() in keys or symbol.upper() in keys


def _select_instruments(
    instruments: Sequence[Instrument], wanted: Sequence[str] | None, limit: int | None
) -> list[Instrument]:
    """Filter by instrument_id or symbol (case-insensitive), then apply ``limit``."""
    selected = list(instruments)
    if wanted:
        keys = {w.upper() for w in wanted}
        selected = [
            i for i in selected if i.instrument_id.upper() in keys or i.symbol.upper() in keys
        ]
    selected.sort(key=lambda i: i.instrument_id)
    return selected[:limit] if limit else selected


def _adjusted_instrument_ids(
    store: DuckDBStore, data_snapshot_id: str = LIVE_SNAPSHOT_ID
) -> list[str]:
    rows = store.conn.execute(
        "SELECT DISTINCT instrument_id FROM daily_prices_adjusted_current"
        " WHERE computed_from_snapshot_id = ? ORDER BY instrument_id",
        [data_snapshot_id],
    ).fetchall()
    return [r[0] for r in rows]


def _latest_snapshot_id(store: DuckDBStore, as_of: date) -> str | None:
    row = store.conn.execute(
        """
        SELECT universe_snapshot_id FROM universe_snapshots
        WHERE as_of_date = ? ORDER BY created_at DESC LIMIT 1
        """,
        [as_of],
    ).fetchone()
    return str(row[0]) if row else None


# ---------------------------------------------------------------------------
# Provider factories (patched in tests)
# ---------------------------------------------------------------------------


def _build_market_provider(api_key: str, access_token: str) -> Any:
    from vcp_scanner.data.providers.kite import KiteProvider

    return KiteProvider(api_key=api_key, access_token=access_token)


def primary_unparsed(provider: Any) -> list[str]:
    return list(getattr(provider, "unparsed_ratios", []))


def primary_unhandled(provider: Any) -> list[str]:
    return list(getattr(provider, "unhandled_records", []))


def _build_ca_providers(upstox_token: str | None) -> tuple[Any, Any]:
    from vcp_scanner.data.providers.nse_ca import NSECorporateActionProvider
    from vcp_scanner.data.providers.upstox_ca import UpstoxCorporateActionProvider

    secondary: Any = (
        UpstoxCorporateActionProvider(access_token=upstox_token) if upstox_token else _NoActions()
    )
    return NSECorporateActionProvider(), secondary


# ---------------------------------------------------------------------------
# ingest market
# ---------------------------------------------------------------------------


def run_market_ingest(args: argparse.Namespace) -> int:
    from vcp_scanner.config.models import CompletenessConfig
    from vcp_scanner.data.ingestion.provider_mapping import sync_provider_mappings
    from vcp_scanner.data.ingestion.worker import IngestionWorker
    from vcp_scanner.data.providers.base import ProviderInstrumentSource
    from vcp_scanner.data.quality.completeness import CompletenessChecker, CompletenessStatus
    from vcp_scanner.data.repositories.duckdb_instrument_repository import (
        DuckDBInstrumentRepository,
        DuckDBInstrumentResolver,
    )
    from vcp_scanner.data.repositories.duckdb_market_repository import (
        DuckDBMarketDataRepository,
    )
    from vcp_scanner.data.repositories.duckdb_provider_instrument_repository import (
        DuckDBProviderInstrumentRepository,
    )
    from vcp_scanner.data.repositories.duckdb_quality_repository import (
        DuckDBDataQualityRepository,
    )

    provisional = bool(getattr(args, "today", False))
    if not provisional and not getattr(args, "kite_history", False):
        _err(
            "Error: daily history comes from NSE bhavcopy (`vcp ingest bhavcopy`). Use "
            "--today for today's PROVISIONAL Kite bar, or --kite-history to fetch Kite's "
            "provider-adjusted history for comparison."
        )
        return 1
    _load_env(args.env_file)
    if provisional:
        from vcp_scanner.data.providers._time import IST

        start = end = datetime.now(UTC).astimezone(IST).date()
    else:
        parsed_start = _parse_date(args.start)
        parsed_end = _parse_date(args.end) if args.end else datetime.now(UTC).date()
        if parsed_start is None or parsed_end is None:
            return 1
        start, end = parsed_start, parsed_end

    api_key = env_secret("KITE_API_KEY")
    access_token = env_secret("KITE_ACCESS_TOKEN")
    if not api_key or not access_token:
        _err(
            "Error: KITE_API_KEY and KITE_ACCESS_TOKEN are required "
            "(run `vcp auth kite` or set them in the environment / .env)."
        )
        return 1

    with _open_store(args.db) as store:
        instruments = _select_instruments(
            DuckDBInstrumentRepository(store).load_instruments(), args.instrument, args.limit
        )
        if not instruments:
            _err("Error: no instruments found. Run `vcp ingest security-master` first.")
            return 1

        completeness_cfg = CompletenessConfig()
        try:
            completeness_cfg = load_scanner_config(
                getattr(args, "config_dir", "config")
            ).data.completeness
        except Exception as e:
            _err(f"Warning: using default completeness settings ({e})")
        checker = CompletenessChecker(store, completeness_cfg)

        provider = _build_market_provider(api_key, access_token)
        resolver = DuckDBInstrumentResolver(store)

        # Record the provider's token -> instrument mapping before any bar is stored, so every
        # raw row's provider_instrument_id has a mapping row behind it (audit P1-1). A failed
        # sync stops the run: bars would otherwise carry tokens nothing can trace.
        if isinstance(provider, ProviderInstrumentSource):
            try:
                sync = sync_provider_mappings(
                    provider,
                    DuckDBProviderInstrumentRepository(store),
                    resolver,
                    as_of=datetime.now(UTC).date(),
                )
            except ProviderError as e:
                _err(f"Error: could not sync provider instrument mappings: {e}")
                return 1
            print(
                f"Provider mappings: {sync.opened} opened, {sync.changed} re-pointed, "
                f"{sync.closed} closed, {sync.unchanged} unchanged, "
                f"{sync.skipped_unresolved} unresolved (skipped)"
            )

        worker = IngestionWorker(
            provider=provider,
            repository=DuckDBMarketDataRepository(store),
            code_version=PACKAGE_VERSION,
            resolver=resolver,
            completeness=checker,
            quality_repository=DuckDBDataQualityRepository(store),
        )
        if provisional:
            print(f"Fetching today's ({start}) PROVISIONAL Kite bar for {len(instruments)} "
                  "instruments...")  # fmt: skip
            statuses_p: Counter[str] = Counter()
            written_p = 0
            for instrument in instruments:
                run = worker.ingest_instrument(instrument, start, end, provisional=True)
                statuses_p[run.status] += 1
                written_p += run.records_written
            print("Provisional bars (superseded by `vcp ingest bhavcopy` tonight):")
            print(f"  Rows written : {written_p}")
            for status, count in sorted(statuses_p.items()):
                print(f"  {status:<13}: {count}")
            print("  Scans ignore them unless run with --allow-provisional.")
            return 1 if statuses_p.get("FAILED") else 0

        print(f"Ingesting daily bars for {len(instruments)} instruments, {start} to {end}...")
        statuses: Counter[str] = Counter()
        written = 0
        for instrument in instruments:
            run = worker.ingest_instrument(instrument, start, end, force=args.force)
            statuses[run.status] += 1
            written += run.records_written
            if run.status == "FAILED":
                _err(f"  {instrument.instrument_id}: ingestion failed")

        # Market sessions can only be observed once the whole cross-section is stored, so
        # instruments ingested early in a first run were checked against a thin one. Re-check
        # every instrument now (cheap when complete) and re-fetch any interior holes.
        scan = checker.scan(start, end)
        for instrument in instruments:
            worker.verify_completeness(instrument, start, end, scan=scan)
        reports = worker.completeness_reports
        incomplete = [r for r in reports.values() if r.status is CompletenessStatus.INCOMPLETE]
        unverified = [r for r in reports.values() if r.status is CompletenessStatus.UNVERIFIED]

    print("Market ingestion complete:")
    print(f"  Rows written : {written}")
    for status, count in sorted(statuses.items()):
        print(f"  {status:<13}: {count}")
    print(
        f"  Completeness : {len(reports) - len(incomplete) - len(unverified)} complete, "
        f"{len(incomplete)} incomplete, {len(unverified)} unverified"
    )
    for r in incomplete[:10]:
        _err(
            f"  INCOMPLETE {r.instrument_id}: {len(r.missing_sessions)} missing session(s), "
            f"{r.missing_sessions[0]} to {r.missing_sessions[-1]}"
        )
    if len(incomplete) > 10:
        _err(f"  ... and {len(incomplete) - 10} more incomplete instruments")
    if scan.suspect_dates:
        _err(
            f"  Warning: {len(scan.suspect_dates)} low-breadth date(s) (partial outage?): "
            + ", ".join(f"{d} ({n}/{a})" for d, n, a in scan.suspect_dates[:5])
        )
    return 1 if statuses.get("FAILED") else 0


# ---------------------------------------------------------------------------
# ingest bhavcopy
# ---------------------------------------------------------------------------


def run_bhavcopy_ingest(args: argparse.Namespace) -> int:
    from pathlib import Path

    from vcp_scanner.data.ingestion.bhavcopy_worker import BhavcopyIngestionWorker
    from vcp_scanner.data.providers.nse_bhavcopy import NseBhavcopyProvider
    from vcp_scanner.data.repositories.duckdb_bhavcopy_repository import (
        DuckDBBhavcopyRepository,
    )
    from vcp_scanner.data.repositories.duckdb_identity_repository import (
        DuckDBIdentityRepository,
    )
    from vcp_scanner.data.repositories.duckdb_market_repository import (
        DuckDBMarketDataRepository,
    )

    start = _parse_date(args.start)
    end = _parse_date(args.end) if args.end else date(2999, 12, 31)
    if start is None or end is None:
        return 1
    cache_dir = args.cache_dir
    if not cache_dir:
        raw_dir = "data/raw"
        try:
            raw_dir = load_scanner_config(
                getattr(args, "config_dir", "config")
            ).data.raw_storage_dir
        except Exception as e:
            _err(f"Warning: using default raw storage dir ({e})")
        cache_dir = str(Path(raw_dir) / "bhavcopy")

    def clock() -> datetime:
        return datetime.now(UTC)

    with _open_store(args.db) as store:
        worker = BhavcopyIngestionWorker(
            NseBhavcopyProvider(cache_dir),
            DuckDBMarketDataRepository(store),
            DuckDBBhavcopyRepository(store),
            DuckDBIdentityRepository(store),
            clock=clock,
            code_version=PACKAGE_VERSION,
        )
        s = worker.run(start, end, refresh=args.refresh)

    print("Bhavcopy ingestion:")
    print(f"  Run id            : {s.run_id}")
    print(f"  Sessions ingested : {s.days_ok}")
    print(f"  No session        : {s.days_no_session}")
    print(f"  Already ingested  : {s.days_skipped}")
    print(f"  Rows / rejected   : {s.rows_received} / {s.rows_rejected}")
    print(f"  Bars written      : {s.bars_written} (unchanged {s.bars_unchanged})")
    superseded = ", ".join(f"{k} {v}" for k, v in sorted(s.superseded.items())) or "none"
    print(f"  Bars superseded   : {superseded}")
    print(f"  New instruments   : {s.new_instruments} (inactive until listed in EQUITY_L)")
    print(f"  Identifier changes: {s.identifier_changes}")
    if s.stopped_at is not None:
        _err(f"  Stopped at {s.stopped_at}: {s.stop_status} ({s.stop_detail})")
    return 1 if s.status == "FAILED" or s.status == "PARTIAL" else 0


# ---------------------------------------------------------------------------
# ingest corporate-actions
# ---------------------------------------------------------------------------


def run_corporate_actions(args: argparse.Namespace) -> int:
    from vcp_scanner.data.adjustment.engine import AdjustmentEngine
    from vcp_scanner.data.ingestion.ca_worker import CorporateActionIngestionWorker
    from vcp_scanner.data.quality.scanner import QualityScanner
    from vcp_scanner.data.reconciliation.engine import (
        ReconciliationConfig,
        ReconciliationEngine,
    )
    from vcp_scanner.data.reconciliation.gap_detector import GapDetector
    from vcp_scanner.data.repositories.duckdb_corporate_action_repository import (
        DuckDBCorporateActionRepository,
    )
    from vcp_scanner.data.repositories.duckdb_identity_repository import (
        DuckDBIdentityRepository,
    )
    from vcp_scanner.data.repositories.duckdb_instrument_repository import (
        DuckDBInstrumentRepository,
        DuckDBInstrumentResolver,
    )
    from vcp_scanner.data.repositories.duckdb_market_repository import (
        DuckDBMarketDataRepository,
    )
    from vcp_scanner.data.repositories.duckdb_quality_repository import (
        DuckDBDataQualityRepository,
    )

    _load_env(args.env_file)
    start = _parse_date(args.start)
    end = _parse_date(args.end) if args.end else datetime.now(UTC).date()
    if start is None or end is None:
        return 1

    try:
        cfg = load_scanner_config(args.config_dir)
    except Exception as e:
        _err(f"Configuration error: {e}")
        return 1
    ca_cfg = cfg.data.corporate_actions

    upstox_token = env_secret("UPSTOX_ACCESS_TOKEN")
    if not upstox_token:
        _err(
            "Warning: UPSTOX_ACCESS_TOKEN is not set; actions will stay SINGLE_SOURCE "
            "(NSE only) and will not be cross-confirmed."
        )
    primary, secondary = _build_ca_providers(upstox_token)

    with _open_store(args.db) as store:
        instruments = _select_instruments(
            DuckDBInstrumentRepository(store).load_instruments(), args.instrument, args.limit
        )
        if not instruments:
            _err("Error: no instruments found. Run `vcp ingest security-master` first.")
            return 1

        worker = CorporateActionIngestionWorker(
            primary_provider=primary,
            secondary_provider=secondary,
            repository=DuckDBCorporateActionRepository(store),
            reconciliation_engine=ReconciliationEngine(
                ReconciliationConfig(
                    secondary_grace_days=ca_cfg.secondary_grace_days,
                    conflict_blocks_signals=ca_cfg.conflict_blocks_signals,
                )
            ),
            adjustment_engine=AdjustmentEngine(),
            resolver=DuckDBInstrumentResolver(store),
            quality_repository=DuckDBDataQualityRepository(store),
            conflict_blocks_signals=ca_cfg.conflict_blocks_signals,
            market=DuckDBMarketDataRepository(store),
        )
        print(
            f"Ingesting corporate actions for {len(instruments)} instruments, {start} to {end}..."
        )
        try:
            worker.run(start, end, instruments)
        except ProviderError as e:
            # Nothing was saved or adjusted: a failed fetch must not look like "no actions".
            _err(f"Error: corporate action ingestion failed: {e}")
            return 1
        unavailable = worker.secondary_unavailable
        if unavailable:
            _err(
                "Warning: Upstox rejected the credentials, so this run used NSE only (actions stay "
                "SINGLE_SOURCE). Refresh UPSTOX_ACCESS_TOKEN in .env. Detail: "
                f"{unavailable[:160]}"
            )

        # DATA_SPECIFICATION 18A workflow: after reconcile, run the gap safety net over raw
        # prices and publish any conflicts or suspected missed actions to the signal gate.
        market_repo = DuckDBMarketDataRepository(store)
        scan_ids = [
            i for i in market_repo.load_priced_instrument_ids() if _matches(i, args.instrument)
        ]
        summary = QualityScanner(
            market_repo,
            DuckDBCorporateActionRepository(store),
            DuckDBDataQualityRepository(store),
            GapDetector(ca_cfg.unexplained_gap),
            conflict_blocks_signals=ca_cfg.conflict_blocks_signals,
            identity=DuckDBIdentityRepository(store),
        ).scan(scan_ids, detected_at=datetime.now(UTC))
        print(
            f"Data-quality scan: {summary.instruments} instrument(s), "
            f"{summary.gap_events} unexplained gap(s), "
            f"{summary.conflict_events} unresolved corporate-action conflict(s), "
            f"{summary.blocking} blocking signals."
        )
        if summary.blocking:
            print("  Review with `vcp quality list`; resolve with `vcp quality resolve`.")

        for label, records in (
            ("split/bonus record(s) whose ratio could not be read", primary_unparsed(primary)),
            (
                "price-affecting NSE record(s) this scanner does not model",
                primary_unhandled(primary),
            ),
        ):
            if records:
                _err(f"Warning: {len(records)} {label}; their prices stay unadjusted:")
                for line in records[:10]:
                    _err(f"  {line}")

    print("Corporate action ingestion and reconciliation complete.")
    print("Next: `vcp ingest adjusted-prices` to rebuild adjusted bars.")
    return 0


# ---------------------------------------------------------------------------
# compute features / rs / trend-template
# ---------------------------------------------------------------------------


def run_compute_features(args: argparse.Namespace) -> int:
    from vcp_scanner.data.features.daily_features import DailyFeatureEngine
    from vcp_scanner.data.features.weekly_aggregation import WeeklyAggregationEngine

    with _open_store(args.db) as store:
        snapshot_id = _resolve_data_snapshot(store, getattr(args, "data_snapshot_id", None))
        if snapshot_id is None:
            return 1
        available = _adjusted_instrument_ids(store, snapshot_id)
        ids = [i for i in available if _matches(i, args.instrument)]
        if not ids:
            _err(
                f"Error: no adjusted prices found for data snapshot {snapshot_id}. "
                "Run `vcp ingest adjusted-prices` first."
            )
            return 1

        daily = DailyFeatureEngine(store, data_snapshot_id=snapshot_id)
        weekly = WeeklyAggregationEngine(store, data_snapshot_id=snapshot_id)
        daily_rows = weekly_rows = 0
        for iid in ids:
            daily_rows += daily.compute_for_instrument(iid)
            weekly_rows += weekly.compute_for_instrument(iid)

    print("Features computed:")
    print(f"  Data snapshot      : {snapshot_id}")
    print(f"  Instruments        : {len(ids)}")
    print(f"  Daily feature rows : {daily_rows}")
    print(f"  Weekly price rows  : {weekly_rows}")
    return 0


def run_compute_rs(args: argparse.Namespace) -> int:
    from vcp_scanner.data.repositories.duckdb_rs_repository import (
        DuckDBRelativeStrengthRepository,
    )
    from vcp_scanner.features.relative_strength import RelativeStrengthEngine

    as_of = _parse_date(args.as_of)
    if as_of is None:
        return 1
    try:
        cfg = load_scanner_config(args.config_dir)
    except Exception as e:
        _err(f"Configuration error: {e}")
        return 1

    with _open_store(args.db) as store:
        data_snapshot_id = _resolve_data_snapshot(store, getattr(args, "data_snapshot_id", None))
        if data_snapshot_id is None:
            return 1
        snapshot_id = args.universe_snapshot_id or _latest_snapshot_id(store, as_of)
        if not snapshot_id:
            _err(
                f"Error: no universe snapshot for {as_of}. "
                f"Run `vcp ingest universe --as-of {as_of}` first."
            )
            return 1
        engine = RelativeStrengthEngine(
            DuckDBRelativeStrengthRepository(store, data_snapshot_id),
            config=cfg.strategy.rs,
            quality_gate=_quality_gate(store, data_snapshot_id),
        )
        rows = engine.compute_for_date(as_of, snapshot_id)

    print(f"Relative strength computed for {as_of}")
    print(f"  Data snapshot     : {data_snapshot_id}")
    print(f"  Universe snapshot : {snapshot_id}")
    print(f"  Rows written      : {rows}")
    return 0


def run_compute_trend_template(args: argparse.Namespace) -> int:
    from vcp_scanner.data.repositories.duckdb_feature_repository import DuckDBFeatureRepository
    from vcp_scanner.data.repositories.duckdb_trend_repository import DuckDBTrendRepository
    from vcp_scanner.data.repositories.duckdb_universe_repository import (
        DuckDBUniverseRepository,
    )
    from vcp_scanner.features.trend_template import TrendTemplateEngine
    from vcp_scanner.features.weekly_stage import WeeklyStageEngine

    as_of = _parse_date(args.as_of)
    if as_of is None:
        return 1
    try:
        cfg = load_scanner_config(args.config_dir)
    except Exception as e:
        _err(f"Configuration error: {e}")
        return 1
    config_hash = compute_config_hash(cfg)

    with _open_store(args.db) as store:
        data_snapshot_id = _resolve_data_snapshot(store, getattr(args, "data_snapshot_id", None))
        if data_snapshot_id is None:
            return 1
        ids = DuckDBUniverseRepository(store).load_snapshot(as_of)
        if args.instrument:
            ids = [i for i in ids if _matches(i, args.instrument)]
        if not ids:
            _err(
                f"Error: no eligible universe members for {as_of}. "
                f"Run `vcp ingest universe --as-of {as_of}` first."
            )
            return 1

        features = DuckDBFeatureRepository(store, data_snapshot_id)
        trend_repo = DuckDBTrendRepository(store, data_snapshot_id)

        contexts = WeeklyStageEngine(features, cfg.strategy.stage).classify_many(ids, as_of)
        trend_repo.save_weekly_context(contexts)

        engine = TrendTemplateEngine(
            features,
            trend_repo,
            cfg.strategy.trend_template,
            rs_version=cfg.strategy.rs.version,
            quality_gate=_quality_gate(store, data_snapshot_id),
        )
        results = engine.evaluate_many(
            ids, as_of, weekly_contexts={c.instrument_id: c for c in contexts}
        )
        # Deterministic: rerunning the same date, config and data snapshot overwrites, not
        # forks. A frozen snapshot is part of the id so different price knowledge never
        # shares a scan; LIVE keeps the historical id format.
        scan_id = f"trend-{as_of.isoformat()}-{config_hash[:12]}"
        if data_snapshot_id != LIVE_SNAPSHOT_ID:
            scan_id += f"-{data_snapshot_id}"
        trend_repo.save_trend_template_results(scan_id, config_hash, results)

    counts = Counter(r.status.value for r in results)
    print(f"Trend template evaluated for {as_of}")
    print(f"  Scan ID     : {scan_id}")
    print(f"  Data snapshot: {data_snapshot_id}")
    print(f"  Config hash : {config_hash}")
    print(f"  Evaluated   : {len(results)}")
    for status, count in sorted(counts.items()):
        print(f"  {status:<12}: {count}")
    return 0


# ---------------------------------------------------------------------------
# quality scan / list / resolve (audit P0-2)
# ---------------------------------------------------------------------------


def run_quality_scan(args: argparse.Namespace) -> int:
    """Run the gap safety net and sync corporate-action conflicts into the signal gate."""
    from vcp_scanner.data.quality.scanner import QualityScanner
    from vcp_scanner.data.reconciliation.gap_detector import GapDetector
    from vcp_scanner.data.repositories.duckdb_corporate_action_repository import (
        DuckDBCorporateActionRepository,
    )
    from vcp_scanner.data.repositories.duckdb_identity_repository import (
        DuckDBIdentityRepository,
    )
    from vcp_scanner.data.repositories.duckdb_market_repository import (
        DuckDBMarketDataRepository,
    )
    from vcp_scanner.data.repositories.duckdb_quality_repository import (
        DuckDBDataQualityRepository,
    )

    try:
        cfg = load_scanner_config(args.config_dir)
    except Exception as e:
        _err(f"Configuration error: {e}")
        return 1
    ca_cfg = cfg.data.corporate_actions
    with _open_store(args.db) as store:
        market = DuckDBMarketDataRepository(store)
        ids = [i for i in market.load_priced_instrument_ids() if _matches(i, args.instrument)]
        summary = QualityScanner(
            market,
            DuckDBCorporateActionRepository(store),
            DuckDBDataQualityRepository(store),
            GapDetector(ca_cfg.unexplained_gap),
            conflict_blocks_signals=ca_cfg.conflict_blocks_signals,
            identity=DuckDBIdentityRepository(store),
        ).scan(ids, detected_at=datetime.now(UTC))
    print("Data-quality scan complete:")
    print(f"  Instruments scanned     : {summary.instruments}")
    print(f"  Unexplained gaps        : {summary.gap_events}")
    print(f"  Corporate-action conflicts: {summary.conflict_events}")
    print(f"  Unexplained ISIN changes: {summary.identity_events}")
    print(f"  Blocking signals        : {summary.blocking}")
    print(f"  New events              : {summary.opened}")
    print(f"  Cleared automatically   : {summary.resolved}")
    return 0


def run_quality_list(args: argparse.Namespace) -> int:
    from vcp_scanner.data.repositories.duckdb_quality_repository import (
        DuckDBDataQualityRepository,
    )

    with _open_store(args.db) as store:
        events = [
            e
            for e in DuckDBDataQualityRepository(store).load_events(open_only=not args.all)
            if _matches(e.instrument_id, args.instrument)
        ]
    if not events:
        print("No data-quality events." if args.all else "No open data-quality events.")
        return 0
    for e in events:
        blocks = "BLOCKS" if e.blocks_signal else "warns "
        when = e.trade_date.isoformat() if e.trade_date else "all dates"
        print(f"{e.event_id}  {e.status.value:<8} {blocks} {e.flag.value:<28} {e.instrument_id}")
        print(f"    from {when}: {e.description}")
        if e.resolved_by:
            print(f"    resolved by {e.resolved_by}: {e.resolution_note}")
    return 0


def run_quality_resolve(args: argparse.Namespace) -> int:
    """Record a human decision closing an event (audited: name and note are required)."""
    from vcp_scanner.data.repositories.duckdb_quality_repository import (
        DuckDBDataQualityRepository,
    )

    with _open_store(args.db) as store:
        try:
            done = DuckDBDataQualityRepository(store).resolve(
                args.event_id,
                resolved_by=args.by,
                note=args.note,
                resolved_at=datetime.now(UTC),
            )
        except ValueError as e:
            _err(f"Error: {e}")
            return 1
    if not done:
        _err(f"Error: no OPEN event with id {args.event_id}.")
        return 1
    print(f"Resolved {args.event_id} (by {args.by}).")
    return 0


# ---------------------------------------------------------------------------
# verify kite-adjustment (audit 2026-09-30 P0-1)
# ---------------------------------------------------------------------------


def _parse_action_spec(spec: str) -> tuple[str, date, Any, float, float] | None:
    """``SYMBOL:YYYY-MM-DD:SPLIT|BONUS:NUM:DEN`` -> parts, or None after printing an error."""
    from vcp_scanner.domain.enums import CorporateActionType

    parts = spec.split(":")
    try:
        symbol, ex, kind, num, den = parts
        action_type = CorporateActionType(kind.upper())
        if action_type not in (CorporateActionType.SPLIT, CorporateActionType.BONUS):
            raise ValueError(kind)
        return symbol.upper(), date.fromisoformat(ex), action_type, float(num), float(den)
    except ValueError:
        _err(f"Error: bad --action '{spec}'. Use SYMBOL:YYYY-MM-DD:SPLIT|BONUS:NUM:DEN.")
        return None


def run_verify_kite_adjustment(args: argparse.Namespace) -> int:
    """Read-only: classify Kite's history around known splits/bonuses as ADJUSTED or RAW.

    Exit codes: 0 all ADJUSTED (matches ``PROVIDER_ADJUSTED_SOURCES``), 2 at least one RAW
    (the assumption is wrong: stop and fix the adjustment policy), 1 nothing conclusive/error.
    """
    from datetime import timedelta

    from vcp_scanner.data.adjustment.engine import AdjustmentEngine
    from vcp_scanner.data.identity import symbol_from_instrument_id
    from vcp_scanner.data.quality.provider_adjustment import (
        AdjustmentVerdict,
        classify_adjustment,
    )
    from vcp_scanner.data.repositories.duckdb_corporate_action_repository import (
        DuckDBCorporateActionRepository,
    )
    from vcp_scanner.domain.corporate_actions import (
        PRICE_SCALING_ACTIONS,
        CorporateActionResolution,
        CorporateActionStatus,
        explains_price_gap,
    )
    from vcp_scanner.domain.market import Instrument

    _load_env(args.env_file)
    api_key, access_token = env_secret("KITE_API_KEY"), env_secret("KITE_ACCESS_TOKEN")
    if not api_key or not access_token:
        _err("Error: KITE_API_KEY and KITE_ACCESS_TOKEN are required (run `vcp auth kite`).")
        return 1

    today = datetime.now(UTC).date()
    resolutions: list[CorporateActionResolution] = []
    if args.action:
        for spec in args.action:
            parsed = _parse_action_spec(spec)
            if parsed is None:
                return 1
            symbol, ex, action_type, num, den = parsed
            resolutions.append(
                CorporateActionResolution(
                    resolution_id=f"cli-{symbol}-{ex}",
                    instrument_id=f"NSE_EQ|{symbol}",
                    action_type=action_type,
                    status=CorporateActionStatus.MANUAL_OVERRIDE,
                    ex_date=ex,
                    ratio_numerator=num,
                    ratio_denominator=den,
                )
            )
    else:
        with _open_store(args.db) as store:
            ids = [
                r[0]
                for r in store.conn.execute(
                    "SELECT DISTINCT instrument_id FROM corporate_action_resolution"
                    " WHERE known_to IS NULL"
                ).fetchall()
            ]
            repo = DuckDBCorporateActionRepository(store)
            stored = [r for iid in ids for r in repo.load_resolutions(iid)]
        usable = [
            r
            for r in stored
            if r.action_type in PRICE_SCALING_ACTIONS
            and explains_price_gap(r)
            and r.ex_date
            and r.ex_date < today
        ]
        usable.sort(key=lambda r: r.ex_date or today, reverse=True)
        resolutions = usable[: args.limit]
        if not resolutions:
            _err(
                "Error: no applied split/bonus in the database to test. Pass --action "
                "SYMBOL:YYYY-MM-DD:SPLIT|BONUS:NUM:DEN."
            )
            return 1

    engine = AdjustmentEngine()
    provider = _build_market_provider(api_key, access_token)
    verdicts: list[AdjustmentVerdict] = []
    for r in resolutions:
        assert r.ex_date is not None
        symbol = symbol_from_instrument_id(r.instrument_id) or r.instrument_id
        price_factor, _ = engine.single_factor(r)
        try:
            bars = provider.get_historical_daily(
                Instrument(instrument_id=r.instrument_id, symbol=symbol),
                r.ex_date - timedelta(days=14),
                r.ex_date + timedelta(days=7),
            )
        except Exception as e:  # noqa: BLE001 - report per action, keep checking the rest
            _err(f"  {symbol} {r.ex_date}: fetch failed: {e}")
            continue
        check = classify_adjustment(symbol, r.ex_date, price_factor, bars)
        verdicts.append(check.verdict)
        ratio = f"{check.observed_ratio:.3f}" if check.observed_ratio is not None else "n/a"
        print(
            f"{symbol:<14} ex {r.ex_date} {r.action_type.value:<5} factor {price_factor:.4f} "
            f"open/prev_close {ratio}: {check.verdict.value} ({check.detail})"
        )

    print(
        "Note: this cannot show whether Kite adjusts on the ex-date itself; the engine assumes "
        "a bar fetched on the ex-date (IST) is already adjusted."
    )
    if AdjustmentVerdict.RAW in verdicts:
        _err(
            "RESULT: Kite returned UNADJUSTED history for at least one action. The adjustment "
            "policy (PROVIDER_ADJUSTED_SOURCES) is wrong for this data; do not build signals."
        )
        return 2
    if verdicts and all(v is AdjustmentVerdict.ADJUSTED for v in verdicts):
        print("RESULT: Kite history is adjusted, as the adjustment engine assumes.")
        return 0
    _err("RESULT: inconclusive; add more --action cases with large ratios.")
    return 1


__all__ = [
    "run_compute_features",
    "run_compute_rs",
    "run_compute_trend_template",
    "run_corporate_actions",
    "run_market_ingest",
    "run_quality_list",
    "run_quality_resolve",
    "run_quality_scan",
    "run_verify_kite_adjustment",
]


# ---------------------------------------------------------------------------
# verify kite-crosscheck (audit step 2.5)
# ---------------------------------------------------------------------------


def run_verify_kite_crosscheck(args: argparse.Namespace) -> int:
    """Read-only: compare our LIVE adjusted closes with Kite's history, per instrument.

    Exit codes: 0 no warnings (every difference is a known methodology difference), 2 at least
    one WARNING finding, 1 error.
    """
    from datetime import timedelta

    from vcp_scanner.data.quality.kite_crosscheck import crosscheck
    from vcp_scanner.data.repositories.duckdb_corporate_action_repository import (
        DuckDBCorporateActionRepository,
    )
    from vcp_scanner.data.repositories.duckdb_instrument_repository import (
        DuckDBInstrumentRepository,
    )
    from vcp_scanner.data.repositories.duckdb_market_repository import (
        DuckDBMarketDataRepository,
    )

    _load_env(args.env_file)
    api_key, access_token = env_secret("KITE_API_KEY"), env_secret("KITE_ACCESS_TOKEN")
    if not api_key or not access_token:
        _err("Error: KITE_API_KEY and KITE_ACCESS_TOKEN are required (run `vcp auth kite`).")
        return 1
    today = datetime.now(UTC).date()
    end = _parse_date(args.end) if args.end else today
    start = _parse_date(args.start) if args.start else (end - timedelta(days=730) if end else None)
    if start is None or end is None:
        return 1

    provider = _build_market_provider(api_key, access_token)
    chunk = max(1, int(provider.get_capabilities().daily_history_max_request_days))
    worst = 0
    with _open_store(args.db) as store:
        instruments = _select_instruments(
            DuckDBInstrumentRepository(store).load_instruments(), args.instrument, None
        )
        if not instruments:
            _err("Error: no matching active instruments.")
            return 1
        market = DuckDBMarketDataRepository(store)
        ca_repo = DuckDBCorporateActionRepository(store)
        for inst in instruments:
            ours = [
                (r.trade_date, float(r.close_adj))
                for r in market.load_adjusted_daily(inst.instrument_id, start, end)
            ]
            kite: list[tuple[date, float]] = []
            try:
                lo = start
                while lo <= end:
                    hi = min(end, lo + timedelta(days=chunk - 1))
                    kite += [
                        (c.timestamp.date(), c.close)
                        for c in provider.get_historical_daily(inst, lo, hi)
                    ]
                    lo = hi + timedelta(days=1)
            except Exception as e:  # noqa: BLE001
                _err(f"  {inst.instrument_id}: Kite fetch failed: {e}")
                worst = max(worst, 1)
                continue
            report = crosscheck(
                ours, kite, ca_repo.load_resolutions(inst.instrument_id), tolerance=args.tolerance
            )
            print(
                f"{inst.symbol}: {report.common_days} common days "
                f"(only ours {report.only_ours}, only Kite {report.only_kite}), "
                f"{len(report.findings)} difference(s), {len(report.warnings)} warning(s)"
            )
            for f in report.findings:
                window = f"{f.previous_date}..{f.trade_date}" if f.previous_date else f.trade_date
                acts = ",".join(f.actions) or "-"
                print(
                    f"  {f.severity:<7} {f.kind.value:<16} {window}  "
                    f"Kite/ours step {f.step:.5f}  actions: {acts}"
                )
            if report.common_days == 0:
                worst = max(worst, 1)
            elif report.warnings:
                worst = 2
    return worst
