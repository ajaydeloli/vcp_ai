"""Golden regression for relative strength and universe eligibility (audit Fix 6).

Recorded from the SQL implementations *before* they were refactored behind repositories, so
the refactor is proven to leave every output unchanged. Synthetic data only (AGENTS.md rule 10).

Re-record only for an intended strategy change (new rs / universe version):
``VCP_RECORD_GOLDEN=1 pytest tests/regression/test_rs_universe_golden.py``.
"""

from __future__ import annotations

import json
import math
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from vcp_scanner.config.models import RSConfig, UniverseConfig
from vcp_scanner.data.repositories.duckdb_quality_repository import DuckDBDataQualityRepository
from vcp_scanner.data.repositories.duckdb_rs_repository import DuckDBRelativeStrengthRepository
from vcp_scanner.data.repositories.duckdb_universe_repository import DuckDBUniverseRepository
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.enums import DataQualityFlag
from vcp_scanner.domain.events import DataQualityEvent, EventSeverity

pytestmark = pytest.mark.regression

GOLDEN = Path(__file__).resolve().parents[1] / "fixtures" / "rs_universe_golden.json"
AS_OF = date(2024, 6, 28)
KNOWN_AT = datetime(2024, 7, 1, tzinfo=UTC)
FETCHED = datetime(2024, 6, 30, 12, tzinfo=UTC)
SESSIONS = [d for d in (AS_OF - timedelta(days=i) for i in range(500)) if d.weekday() < 5]


# --------------------------------------------------------------------------- adapters
# The only lines that change when constructors change.


def _run_rs(store: DuckDBStore, gate: DuckDBDataQualityRepository) -> None:
    from vcp_scanner.features.relative_strength import RelativeStrengthEngine

    RelativeStrengthEngine(
        DuckDBRelativeStrengthRepository(store), config=RSConfig(), quality_gate=gate
    ).compute_for_date(AS_OF, "u-golden")


def _run_universe(store: DuckDBStore, gate: DuckDBDataQualityRepository) -> tuple[Any, Any]:
    from vcp_scanner.data.universe.builder import UniverseBuilder

    return UniverseBuilder(
        DuckDBUniverseRepository(store), UniverseConfig(), quality_gate=gate
    ).build_snapshot(AS_OF, known_at=KNOWN_AT)


# --------------------------------------------------------------------------- data


def _block(store: DuckDBStore, iid: str) -> DuckDBDataQualityRepository:
    gate = DuckDBDataQualityRepository(store)
    event = DataQualityEvent(
        event_id=f"dq-{iid}",
        instrument_id=iid,
        flag=DataQualityFlag.UNEXPLAINED_GAP,
        severity=EventSeverity.HIGH,
        detected_at=datetime(2024, 6, 1, tzinfo=UTC),
        description="synthetic",
        trade_date=date(2024, 1, 2),
        blocks_signal=True,
    )
    gate.sync_events(iid, DataQualityFlag.UNEXPLAINED_GAP, [event], at=event.detected_at)
    return gate


def _seed_rs(store: DuckDBStore) -> None:
    def series(iid: str, closes: list[float], sessions: list[date]) -> None:
        store.conn.executemany(
            """
            INSERT INTO daily_prices_adjusted (
                instrument_id, trade_date, open_adj, high_adj, low_adj, close_adj, volume_adj,
                adjustment_version, price_factor_applied, volume_factor_applied, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, 1000, 'v1', 1, 1, TIMESTAMPTZ '2024-06-30 00:00:00+00')
            """,
            [(iid, d, c, c, c, c) for d, c in zip(sessions, closes, strict=True)],
        )

    n = 300
    base = SESSIONS[:n]  # newest first
    shapes = {
        "R1": lambda i: 100 + 0.30 * (n - i),
        "R2": lambda i: 100 + 0.10 * (n - i),
        "R3": lambda i: 200 - 0.20 * (n - i) * 0.5,
        "R4": lambda i: 100 + 20 * math.sin(i / 17),
        "R5": lambda i: 50 + 0.05 * (n - i) + (i % 7),
        "R6": lambda i: 300 - 0.4 * (n - i) * 0.5,
        "R_TIE1": lambda i: 100 + 0.2 * (n - i),
        "R_TIE2": lambda i: 100 + 0.2 * (n - i),
        "R_BLOCK": lambda i: 100 + 1.0 * (n - i),
        "R_NOTELIG": lambda i: 100 + 2.0 * (n - i),
    }
    for iid, f in shapes.items():
        series(iid, [float(f(i)) for i in range(n)], base)
    series("R_SHORT", [100.0 + i for i in range(200)], base[:200])  # too little history
    series("R_STALE", [100.0 + 0.1 * i for i in range(n)], SESSIONS[8 : 8 + n])  # 10+ days old
    nan_closes = [100.0 + 0.1 * i for i in range(n)]
    nan_closes[0] = float("nan")
    series("R_NAN", nan_closes, base)
    holes = [d for k, d in enumerate(SESSIONS[: n + 40]) if k % 9 != 4][:n]  # gappy sessions
    series("R_GAPPY", [100.0 + 0.15 * (n - i) for i in range(n)], holes)

    store.conn.execute(
        "INSERT INTO universe_snapshots VALUES ('u-golden', 'golden', ?, ?, 'h', '1.1', 'BIASED')",
        [AS_OF, KNOWN_AT],
    )
    members = [*shapes, "R_SHORT", "R_STALE", "R_NAN", "R_GAPPY", "R_NOPRICES"]
    store.conn.executemany(
        "INSERT INTO universe_memberships (universe_snapshot_id, instrument_id, eligible) "
        "VALUES ('u-golden', ?, ?)",
        [(m, m != "R_NOTELIG") for m in members],
    )


def _seed_universe(store: DuckDBStore) -> None:
    def prices(iid: str, closes: list[float], volumes: list[float], sessions: list[date],
               provider: str = "SYNTHETIC_FAKE") -> None:  # fmt: skip
        store.conn.executemany(
            """
            INSERT INTO daily_prices (
                instrument_id, trade_date, open_raw, high_raw, low_raw, close_raw, volume_raw,
                primary_provider, data_status, source_run_id, source_hash, known_from
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'OK', 'run', 'h', ?)
            """,
            [
                (iid, d, c, c, c, c, int(v), provider, FETCHED)
                for d, c, v in zip(sessions, closes, volumes, strict=True)
            ],
        )

    def sm(iid: str, series: str = "EQ", exchange: str = "NSE") -> None:
        store.conn.execute(
            "INSERT INTO security_master_history (instrument_id, symbol, series, exchange, "
            "valid_from, known_from) VALUES (?, ?, ?, ?, '2000-01-01', ?)",
            [iid, iid, series, exchange, FETCHED],
        )

    def flag(iid: str, kind: str) -> None:
        store.conn.execute(
            "INSERT INTO surveillance_flags_history (instrument_id, flag_type, valid_from, "
            "known_from) VALUES (?, ?, '2024-01-01', ?)",
            [iid, kind, FETCHED],
        )

    n = 300
    base = SESSIONS[:n]
    flat = [100.0] * n
    liquid = [1_000_000.0] * n
    for iid in ("U_OK", "U_BE", "U_ASM", "U_T2T", "U_NOSM", "U_BLOCKED", "U_BSE"):
        prices(iid, flat, liquid, base)
    for iid in ("U_OK", "U_ASM", "U_T2T", "U_BLOCKED"):
        sm(iid)
    sm("U_BE", series="BE")
    sm("U_BSE", exchange="BSE")
    flag("U_ASM", "ASM")
    flag("U_T2T", "T2T")
    prices("U_LOWPRICE", [10.0] * n, [10_000_000.0] * n, base)
    sm("U_LOWPRICE")
    prices("U_ILLIQ20", flat, [1000.0] * n, base)
    sm("U_ILLIQ20")
    prices("U_ILLIQ50", flat, [110_000.0] * 20 + [10.0] * (n - 20), base)
    sm("U_ILLIQ50")
    prices("U_STALE", flat, liquid, SESSIONS[45 : 45 + n])
    sm("U_STALE")
    prices("U_SHORT", flat[:100], liquid[:100], base[:100])
    sm("U_SHORT")
    # Kite bars fetched on 2024-06-30, after a 10:1 split with ex-date 2024-06-29 (after the
    # last bar, on/before the fetch date): Kite had rescaled them, stored 15 = traded 150.
    prices("U_KITE", [15.0] * n, [10_000_000.0] * n, base, provider="KITE")
    sm("U_KITE")
    store.conn.execute(
        """
        INSERT INTO corporate_action_adjustments (
            resolution_id, instrument_id, effective_date, price_factor, volume_factor,
            cumulative_price_factor, cumulative_volume_factor, source, calculation_version,
            known_from
        ) VALUES ('R', 'U_KITE', '2024-06-29', 0.1, 10, 0.1, 10, 'INTERNAL', '1.1', ?)
        """,
        [FETCHED],
    )
    store.conn.execute(
        "INSERT INTO security_master_history (instrument_id, symbol, series, exchange, "
        "valid_from, valid_to, delisting_date, known_from) "
        "VALUES ('U_DEAD', 'U_DEAD', 'EQ', 'NSE', '2000-01-01', '2020-01-01', '2020-01-01', ?)",
        [FETCHED],
    )


def _rs_rows(store: DuckDBStore) -> list[dict[str, Any]]:
    cols = [
        "instrument_id", "ret_63", "ret_126", "ret_189", "ret_252", "rs_raw", "rs_rank",
        "rs_percentile", "population_size", "rs_status",
    ]  # fmt: skip
    rows = store.conn.execute(
        f"SELECT {', '.join(cols)} FROM relative_strength_snapshots ORDER BY instrument_id"  # noqa: S608
    ).fetchall()
    return [dict(zip(cols, r, strict=True)) for r in rows]


def _universe_rows(snapshot: Any, memberships: Any) -> dict[str, Any]:
    return {
        "snapshot": {
            "survivorship_status": snapshot.survivorship_status.value,
            "config_hash": snapshot.config_hash,
            "method_version": snapshot.method_version,
        },
        "members": [
            {
                "instrument_id": m.instrument_id,
                "eligible": m.eligible,
                "exclusion_reason": m.exclusion_reason,
                "avg_traded_value": m.avg_traded_value,
                "price": m.price,
                "series": m.series,
                "asm_flag": m.asm_flag,
                "gsm_flag": m.gsm_flag,
                "t2t_flag": m.t2t_flag,
            }
            for m in sorted(memberships, key=lambda m: m.instrument_id)
        ],
    }


def _normalise(value: Any) -> Any:
    """JSON-safe, NaN-aware representation used for both recording and comparison."""
    if isinstance(value, float):
        return "NaN" if math.isnan(value) else value
    if isinstance(value, dict):
        return {k: _normalise(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalise(v) for v in value]
    return value


def _actual() -> dict[str, Any]:
    store = DuckDBStore(":memory:")
    store.migrate()
    _seed_rs(store)
    _seed_universe(store)
    gate = _block(store, "R_BLOCK")
    _block(store, "U_BLOCKED")
    _run_rs(store, gate)
    snapshot, memberships = _run_universe(store, gate)
    return _normalise({"rs": _rs_rows(store), "universe": _universe_rows(snapshot, memberships)})


def _assert_close(actual: Any, expected: Any, path: str = "") -> None:
    if isinstance(expected, float) and isinstance(actual, float):
        assert actual == pytest.approx(expected, rel=1e-12, abs=1e-15), path
    elif isinstance(expected, dict):
        assert set(actual) == set(expected), path
        for k in expected:
            _assert_close(actual[k], expected[k], f"{path}.{k}")
    elif isinstance(expected, list):
        assert len(actual) == len(expected), path
        for i, (a, e) in enumerate(zip(actual, expected, strict=True)):
            _assert_close(a, e, f"{path}[{i}]")
    else:
        assert actual == expected, path


def test_rs_and_universe_match_the_golden_record() -> None:
    actual = _actual()
    if os.environ.get("VCP_RECORD_GOLDEN") == "1":
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps(actual, indent=1, sort_keys=True) + "\n")
    expected = json.loads(GOLDEN.read_text())
    _assert_close(actual, expected)


def test_golden_record_covers_the_intended_cases() -> None:
    """Guards the fixture itself: every edge case must actually appear in it."""
    golden = json.loads(GOLDEN.read_text())
    rs = {r["instrument_id"]: r for r in golden["rs"]}
    assert rs["R_SHORT"]["rs_status"] == "INSUFFICIENT_DATA"
    assert rs["R_STALE"]["rs_status"] == "STALE_DATA"
    assert rs["R_NAN"]["rs_status"] == "INSUFFICIENT_DATA"
    assert "R_BLOCK" not in rs and "R_NOTELIG" not in rs and "R_NOPRICES" not in rs
    assert rs["R_TIE1"]["rs_rank"] == rs["R_TIE2"]["rs_rank"]
    assert {r["population_size"] for r in rs.values()} == {9}
    members = {m["instrument_id"]: m for m in golden["universe"]["members"]}
    assert [i for i, m in members.items() if m["eligible"]] == ["U_KITE", "U_OK"]
    assert members["U_KITE"]["price"] == pytest.approx(150.0)
    assert golden["universe"]["snapshot"]["survivorship_status"] == "PARTIAL"
