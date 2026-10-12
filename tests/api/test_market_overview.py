"""Market Overview update (FRONTEND_SPECIFICATION §67.28): the pieces computed from small
hand-built databases, so every number is known. Display only: none of this feeds a strategy."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from tests.api.fixture import CONFIG_DIR, put
from vcp_scanner.api import health
from vcp_scanner.api.context import build_context
from vcp_scanner.data.storage.duckdb_store import DuckDBStore

SESSIONS = [date(2026, 9, 1) + timedelta(days=k) for k in range(14)]
SESSIONS = [d for d in SESSIONS if d.weekday() < 5]  # ten weekdays, 1 to 14 Sep


@pytest.fixture
def db(tmp_path: Path) -> Any:
    store = DuckDBStore(":memory:")
    store.migrate()
    ctx = build_context(CONFIG_DIR, tmp_path)
    return store, ctx


def close(c: Any, sym: str, d: date, price: float) -> None:
    put(c, "daily_prices_adjusted", instrument_id=sym, trade_date=d, close_adj=price,
        computed_from_snapshot_id="LIVE", adjustment_version="v1")  # fmt: skip


def test_judge_uses_only_closes_known_on_each_day() -> None:
    s = SESSIONS
    fails = {s[1]: 101.0, s[2]: 99.0}  # breaks out on s[0], below the pivot on s[2]
    assert health._judge(fails, s, 0, 100.0) == (True, 2)
    holds = {d: 101.0 for d in s}
    assert health._judge(holds, s, 0, 100.0) == (False, health.FAIL_WITHIN)
    assert health._judge(holds, s, len(s) - 3, 100.0) is None  # too recent to judge
    # a close below the pivot after the window does not make it a failure
    late = {**holds, s[health.FAIL_WITHIN + 1]: 90.0}
    assert health._judge(late, s, 0, 100.0) == (False, health.FAIL_WITHIN)


def test_flow_counts_new_setups_breakouts_and_the_failure_rate(db: Any) -> None:
    store, ctx = db
    c = store.conn
    vcp = ctx.strategy("vcp")
    s = SESSIONS
    # scans on s[0] (ALPHA) and s[1] (ALPHA, BETA): one new setup on s[1], none counted on s[0]
    for d, syms in ((s[0], ["ALPHA"]), (s[1], ["ALPHA", "BETA"])):
        for sym in syms:
            put(c, "setup_scores", scan_id=f"x-{d}", strategy_id="vcp", instrument_id=sym,
                as_of_date=d, classification="VCP", eligible=True, final_setup_score=80.0,
                scoring_version="s1", config_hash=vcp.config_hash,
                data_snapshot_id="LIVE")  # fmt: skip
    # five breakouts on s[1], pivot 100: H1, H2 hold; F1, F2, F3 close below on s[2]
    for sym in ("H1", "H2", "F1", "F2", "F3"):
        put(c, "vcp_breakout_events", breakout_event_id=f"b-{sym}",
            instrument_id=sym, config_hash=vcp.config_hash, breakout_date=s[1],
            pivot_price=100.0, detected_as_of=s[1])  # fmt: skip
        for d in s[2:]:
            close(c, sym, d, 99.0 if sym.startswith("F") and d == s[2] else 102.0)
    flow = {p.day: p for p in health._flow(store.conn, ctx, s)}
    assert flow[s[0]].new_setups is None  # the first stored scan has nothing to compare with
    assert flow[s[1]].new_setups == 1 and flow[s[1]].breakouts == 5
    assert flow[s[2]].new_setups is None  # no scan that day
    # on s[2] the three failures are known, the two holds not yet: below MIN_JUDGED, no rate
    assert (flow[s[2]].judged, flow[s[2]].failed_pct) == (3, None)
    held_known = s[1 + health.FAIL_WITHIN]
    assert flow[held_known].judged == 5
    assert flow[held_known].failed_pct == pytest.approx(60.0)
    assert flow[s[-1]].failed_pct == pytest.approx(60.0)


def test_flow_is_empty_without_stored_scans(db: Any) -> None:
    store, ctx = db
    assert health._flow(store.conn, ctx, SESSIONS) == []


# --- step 2: net new highs, divergences ------------------------------------------------------


def row(adv: int, dec: int, highs: int, lows: int) -> tuple[Any, ...]:
    """A row of the daily query: only advancers [7], decliners [8], highs [9], lows [10] used."""
    return (None, 0, 0, 0, 0, 0.0, 0.0, adv, dec, highs, lows)


# --- step 5: sector strength -------------------------------------------------------------------


def test_sector_rows_known_answers(ro_env: Any) -> None:
    """Fixture: ALPHA is Capital Goods (RS 91), BETA Healthcare (RS 85, a VCP breakout on the
    last day), GAMMA and the fillers not classified. The named stocks close below their 50-day
    average on both days compared; Nifty Capital Goods rises 1 a session."""
    sectors = {s["sector"]: s for s in ro_env.client.get("/api/v1/market/health").json()["sectors"]}
    cap = sectors["Capital Goods"]
    assert (cap["stocks"], cap["above50_pct"], cap["above50_change"]) == (1, 0.0, 0.0)
    assert cap["median_rs"] == 91 and cap["breakouts"] == 0
    assert cap["index_name"] == "Nifty Capital Goods"
    assert cap["index_change_pct"] == pytest.approx((1029 / 1008 - 1) * 100)  # 21 sessions
    health_ = sectors["Healthcare"]
    assert health_["breakouts"] == 1 and health_["median_rs"] == 85
    assert health_["index_change_pct"] is None  # no Healthcare index stored in the fixture
    rest = sectors[None]  # not classified yet: one row, listed last
    assert rest["stocks"] == 61  # 60 fillers and GAMMA
    assert list(sectors)[-1] is None
    # the score's sector test per horizon: 1, 3 and 6 months; None without enough history
    assert len(cap["index_uptrend"]) == 3 and all(u in (None, True) for u in cap["index_uptrend"])
    assert health_["index_uptrend"] == []  # no index for the row's sector stored


def test_index_uptrend_is_the_scores_sector_test() -> None:
    days = [date(2026, 1, 1) + timedelta(days=k) for k in range(200)]
    rising = {d: 100.0 + k for k, d in enumerate(days)}
    assert health._index_uptrend(rising, days[-1]) == [True, True, True]
    falling = {d: 300.0 - k for k, d in enumerate(days)}
    assert health._index_uptrend(falling, days[-1]) == [False, False, False]
    short = {d: c for d, c in list(rising.items())[-60:]}
    assert health._index_uptrend(short, days[-1]) == [True, None, None]
    assert health._index_uptrend(rising, date(2030, 1, 1)) == [None, None, None]


def test_sector_horizons_match_the_score_settings() -> None:
    from vcp_scanner.market_health.settings import load_settings

    s = load_settings(Path(CONFIG_DIR) / "market_health.yaml")
    assert list(health.SECTOR_HORIZONS) == s.leadership.sector_horizons


# --- review 2026-10-11: one failure rate on the page, each stock once per breakout day ---------


def test_a_stock_breaking_out_in_two_strategies_counts_once(db: Any) -> None:
    store, ctx = db
    c = store.conn
    s = SESSIONS
    vcp, flat = ctx.strategy("vcp"), ctx.strategy("flat_base")
    put(c, "setup_scores", scan_id="x", strategy_id="vcp", instrument_id="X", as_of_date=s[0],
        classification="VCP", eligible=True, final_setup_score=80.0, scoring_version="s1",
        config_hash=vcp.config_hash, data_snapshot_id="LIVE")  # fmt: skip
    put(c, "vcp_breakout_events", breakout_event_id="v", instrument_id="DUP",
        config_hash=vcp.config_hash, breakout_date=s[1], pivot_price=100.0,
        detected_as_of=s[1])  # fmt: skip
    put(c, "strategy_breakout_events", breakout_event_id="f", strategy_id="flat_base",
        instrument_id="DUP", base_start_date=s[0], config_hash=flat.config_hash,
        breakout_date=s[1], pivot_price=101.0, pivot_date=s[0], pivot_source="x",
        volume_ratio=2.0, detected_as_of=s[1], method="x")  # fmt: skip
    # closes at 100.5: below the flat-base pivot (101) but not below the VCP pivot (100)
    for d in s[2:]:
        close(c, "DUP", d, 100.5)
    flow = {p.day: p for p in health._flow(c, ctx, s)}
    assert flow[s[1]].breakouts == 1  # one stock, not two events
    known = flow[s[1 + health.FAIL_WITHIN]]
    assert (known.judged, known.failed) == (1, 0)  # still above its lowest pivot: held


# --- leaders vs the index: the leaders of 20 sessions ago, followed forward --------------------


def _tt(c: Any, ctx: Any, sym: str, d: date, rs: int) -> None:
    put(c, "trend_template_results", scan_id=f"tt-{d}", instrument_id=sym, as_of_date=d,
        status="PASS", trend_template_pass=True, rs_rank=rs, calculation_version="1",
        config_hash=ctx.scan_hash, data_snapshot_id="LIVE")  # fmt: skip
