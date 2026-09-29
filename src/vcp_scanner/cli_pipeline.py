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
from vcp_scanner.domain.market import Instrument
from vcp_scanner.versioning import PACKAGE_VERSION


class _NoActions:
    """Stand-in secondary provider used when no Upstox credentials are configured."""

    def get_actions(
        self, start: date, end: date, instruments: list[Instrument] | None = None
    ) -> list[Any]:
        return []


def _err(message: str) -> None:
    print(message, file=sys.stderr)


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


def _adjusted_instrument_ids(store: DuckDBStore) -> list[str]:
    rows = store.conn.execute(
        "SELECT DISTINCT instrument_id FROM daily_prices_adjusted_current ORDER BY instrument_id"
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
    from vcp_scanner.data.ingestion.worker import IngestionWorker
    from vcp_scanner.data.repositories.duckdb_instrument_repository import (
        DuckDBInstrumentRepository,
        DuckDBInstrumentResolver,
    )
    from vcp_scanner.data.repositories.duckdb_market_repository import (
        DuckDBMarketDataRepository,
    )

    _load_env(args.env_file)
    start = _parse_date(args.start)
    end = _parse_date(args.end) if args.end else datetime.now(UTC).date()
    if start is None or end is None:
        return 1

    api_key = os.getenv("KITE_API_KEY")
    access_token = os.getenv("KITE_ACCESS_TOKEN")
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

        worker = IngestionWorker(
            provider=_build_market_provider(api_key, access_token),
            repository=DuckDBMarketDataRepository(store),
            code_version=PACKAGE_VERSION,
            resolver=DuckDBInstrumentResolver(store),
        )
        print(f"Ingesting daily bars for {len(instruments)} instruments, {start} to {end}...")
        statuses: Counter[str] = Counter()
        written = 0
        for instrument in instruments:
            run = worker.ingest_instrument(instrument, start, end, force=args.force)
            statuses[run.status] += 1
            written += run.records_written
            if run.status == "FAILED":
                _err(f"  {instrument.instrument_id}: ingestion failed")

    print("Market ingestion complete:")
    print(f"  Rows written : {written}")
    for status, count in sorted(statuses.items()):
        print(f"  {status:<13}: {count}")
    return 1 if statuses.get("FAILED") else 0


# ---------------------------------------------------------------------------
# ingest corporate-actions
# ---------------------------------------------------------------------------


def run_corporate_actions(args: argparse.Namespace) -> int:
    from vcp_scanner.data.adjustment.engine import AdjustmentEngine
    from vcp_scanner.data.ingestion.ca_worker import CorporateActionIngestionWorker
    from vcp_scanner.data.reconciliation.engine import (
        ReconciliationConfig,
        ReconciliationEngine,
    )
    from vcp_scanner.data.repositories.duckdb_corporate_action_repository import (
        DuckDBCorporateActionRepository,
    )
    from vcp_scanner.data.repositories.duckdb_instrument_repository import (
        DuckDBInstrumentRepository,
        DuckDBInstrumentResolver,
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

    upstox_token = os.getenv("UPSTOX_ACCESS_TOKEN")
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
        )
        print(
            f"Ingesting corporate actions for {len(instruments)} instruments, {start} to {end}..."
        )
        worker.run(start, end, instruments)

    print("Corporate action ingestion and reconciliation complete.")
    print("Next: `vcp ingest adjusted-prices` to rebuild adjusted bars.")
    return 0


# ---------------------------------------------------------------------------
# compute features / rs / trend-template
# ---------------------------------------------------------------------------


def run_compute_features(args: argparse.Namespace) -> int:
    from vcp_scanner.features.daily_features import DailyFeatureEngine
    from vcp_scanner.features.weekly_aggregation import WeeklyAggregationEngine

    with _open_store(args.db) as store:
        available = _adjusted_instrument_ids(store)
        ids = [i for i in available if not args.instrument or i in set(args.instrument)]
        if not ids:
            _err("Error: no adjusted prices found. Run `vcp ingest adjusted-prices` first.")
            return 1

        daily = DailyFeatureEngine(store)
        weekly = WeeklyAggregationEngine(store)
        daily_rows = weekly_rows = 0
        for iid in ids:
            daily_rows += daily.compute_for_instrument(iid)
            weekly_rows += weekly.compute_for_instrument(iid)

    print("Features computed:")
    print(f"  Instruments        : {len(ids)}")
    print(f"  Daily feature rows : {daily_rows}")
    print(f"  Weekly price rows  : {weekly_rows}")
    return 0


def run_compute_rs(args: argparse.Namespace) -> int:
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
        snapshot_id = args.universe_snapshot_id or _latest_snapshot_id(store, as_of)
        if not snapshot_id:
            _err(
                f"Error: no universe snapshot for {as_of}. "
                f"Run `vcp ingest universe --as-of {as_of}` first."
            )
            return 1
        engine = RelativeStrengthEngine(store, config=cfg.strategy.rs)
        rows = engine.compute_for_date(as_of, snapshot_id)

    print(f"Relative strength computed for {as_of}")
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
        ids = DuckDBUniverseRepository(store).load_snapshot(as_of)
        if args.instrument:
            ids = [i for i in ids if i in set(args.instrument)]
        if not ids:
            _err(
                f"Error: no eligible universe members for {as_of}. "
                f"Run `vcp ingest universe --as-of {as_of}` first."
            )
            return 1

        features = DuckDBFeatureRepository(store)
        trend_repo = DuckDBTrendRepository(store)

        contexts = WeeklyStageEngine(features, cfg.strategy.stage).classify_many(ids, as_of)
        trend_repo.save_weekly_context(contexts)

        engine = TrendTemplateEngine(
            features,
            trend_repo,
            cfg.strategy.trend_template,
            rs_version=cfg.strategy.rs.version,
        )
        results = engine.evaluate_many(
            ids, as_of, weekly_contexts={c.instrument_id: c for c in contexts}
        )
        # Deterministic: rerunning the same date under the same config overwrites, not forks.
        scan_id = f"trend-{as_of.isoformat()}-{config_hash[:12]}"
        trend_repo.save_trend_template_results(scan_id, config_hash, results)

    counts = Counter(r.status.value for r in results)
    print(f"Trend template evaluated for {as_of}")
    print(f"  Scan ID     : {scan_id}")
    print(f"  Config hash : {config_hash}")
    print(f"  Evaluated   : {len(results)}")
    for status, count in sorted(counts.items()):
        print(f"  {status:<12}: {count}")
    return 0


__all__ = [
    "run_compute_features",
    "run_compute_rs",
    "run_compute_trend_template",
    "run_corporate_actions",
    "run_market_ingest",
]
