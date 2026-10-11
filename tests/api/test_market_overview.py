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


def test_highs_lows_reads_the_ten_day_average_not_one_day() -> None:
    rows = [row(0, 0, 30, 5)] * 9 + [row(0, 0, 0, 40)]  # one bad day after nine good ones
    item = health._highs_lows(rows)
    assert item.value == pytest.approx((9 * 30 - 9 * 5 - 40) / 10)  # net 18.5 a day
    assert item.status == "green"  # 27 highs against 8.5 lows on average
    assert "0 new 52-week highs against 40 new lows today" in item.text


def test_below_own_high() -> None:
    assert health._below_own_high([0.0, 10.0, 9.0]) == (False, pytest.approx(0.1))
    assert health._below_own_high([0.0, 10.0, 5.0]) == (True, pytest.approx(0.5))
    assert health._below_own_high([None, 3.0]) is None  # one value: no range
    assert health._below_own_high([2.0, 2.0]) is None


def test_divergence_warnings_need_the_index_near_its_high() -> None:
    n = 60
    # breadth: the A/D line rises for 40 sessions, then falls; new highs dry up likewise
    rows = [row(30, 10, 20, 0) for _ in range(40)] + [row(10, 30, 0, 20) for _ in range(20)]
    near = [100.0 + k * 0.1 for k in range(n)]  # VQI keeps making new highs
    ids = [w.id for w in health._warnings(rows, near)]
    assert ids == ["ad_divergence", "highs_divergence"]
    far = near[:-1] + [near[-2] * 0.9]  # VQI 10 % off its high: no divergence to speak of
    assert health._warnings(rows, far) == []
    healthy = [row(30, 10, 20, 0) for _ in range(n)]  # breadth confirms the high
    assert health._warnings(healthy, near) == []


# --- step 3: NIFTY lines -----------------------------------------------------------------------


def test_rebased_is_100_at_the_first_day_with_a_close() -> None:
    d = SESSIONS
    closes = {d[1]: 200.0, d[2]: 210.0, d[4]: 190.0}
    assert health._rebased(closes, d[:5]) == [None, 100.0, 105.0, None, 95.0]
    assert health._rebased({}, d[:3]) == [None, None, None]


def test_nse_closes_reads_nifty_50_and_500_by_name(db: Any) -> None:
    store, _ctx = db
    c = store.conn
    for name, close in (("Nifty 50", 22520.45), ("NIFTY 500", 21866.65), ("Nifty IT", 1.0)):
        c.execute(
            "INSERT INTO index_daily (index_name, trade_date, close) VALUES (?, ?, ?)",
            [name, SESSIONS[0], close],
        )
    got = health._nse_closes(c, SESSIONS[0], SESSIONS[-1])
    assert got == {"nifty50": {SESSIONS[0]: 22520.45}, "nifty500": {SESSIONS[0]: 21866.65}}


def test_market_health_points_carry_rebased_nifty_lines(ro_env: Any) -> None:
    pts = ro_env.client.get("/api/v1/market/health").json()["points"]
    with_nifty = [p for p in pts if p["nifty50"] is not None]
    assert with_nifty and with_nifty[0]["nifty50"] == 100.0 and with_nifty[0]["nifty500"] == 100.0
    assert len(with_nifty) == 30  # only the stored days; earlier points have none
    last = with_nifty[-1]
    assert last["nifty50"] == pytest.approx((20000 + 290) / 20000 * 100)
    assert last["day"] == pts[-1]["day"]


# --- step 4: distribution days -----------------------------------------------------------------


def test_day_kinds_one_rule_for_the_count_and_the_markers() -> None:
    rets = [None, -0.003, -0.003, 0.004, 0.001, -0.001]
    vols = [100.0, 120.0, 110.0, 130.0, 140.0, 150.0]
    # day 1: down 0.3 % on more volume; day 2: down on less; day 3: up 0.4 % on more;
    # day 4: up 0.1 % on more (less than 0.2 %: neither); day 5: down 0.1 %: neither
    assert health._day_kinds(rets, vols) == [
        None, "distribution", None, "accumulation", None, None,
    ]  # fmt: skip


def test_markers_match_the_distribution_count(ro_env: Any) -> None:
    body = ro_env.client.get("/api/v1/market/health").json()
    window = body["index_days"]["window"]
    marked = sum(p["distribution"] for p in body["points"][-window:])
    assert marked == body["index_days"]["distribution"]
