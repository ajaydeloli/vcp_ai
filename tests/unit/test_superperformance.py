"""Superperformance study (research/superperformance.py): runs, bases, lift and the
point-in-time rules. Synthetic prices only (labelled as such; AGENTS.md hard rule 10)."""

from __future__ import annotations

import random
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from vcp_scanner.config.models import StageConfig
from vcp_scanner.research import superperformance as sp
from vcp_scanner.research import superperformance_data as spd

P = sp.Params(bootstrap=50)
START = date(2022, 1, 3)


def _days(n: int) -> list[date]:
    out: list[date] = []
    d = START
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def make_stock(
    closes: list[float], ok: list[bool] | None = None, iid: str = "NSE_EQ|SYN"
) -> sp.Stock:
    """A synthetic stock: highs and lows 1 % around the close, flat volume."""
    n = len(closes)
    sma50 = [sum(closes[i - 49 : i + 1]) / 50 if i >= 49 else None for i in range(n)]
    return sp.Stock(
        iid=iid, symbol=iid.split("|")[-1], sector="Synthetic", days=_days(n), close=closes,
        high=[c * 1.01 for c in closes], low=[c * 0.99 for c in closes], volume=[1000.0] * n,
        ok=ok or [True] * n, rs=[50] * n, tt=[False] * n, sma50=sma50, sma200=[None] * n,
        high252=[max(closes[max(0, i - 251) : i + 1]) for i in range(n)], vavg50=[1000.0] * n,
        vol50=[0.02] * n,
    )  # fmt: skip


def ctx_for(s: sp.Stock, **kw: object) -> sp.Context:
    base = sp.Context(
        calendar=list(s.days), nifty={d: 100.0 for d in s.days}, market_label={},
        sector_tt_share={}, tt_share={},
    )  # fmt: skip
    return replace(base, **kw)  # type: ignore[arg-type]


def ramp(a: float, b: float, n: int) -> list[float]:
    return [a + (b - a) * k / (n - 1) for k in range(n)]


# ----------------------------------------------------------------------------- helpers


def test_forward_max_index_matches_brute_force() -> None:
    rng = random.Random(1)
    vals = [float(rng.randint(1, 20)) for _ in range(300)]  # many ties
    got = sp.forward_max_index(vals, 17)
    for i in range(len(vals)):
        win = list(range(i + 1, min(len(vals), i + 18)))
        want = None if not win else min(win, key=lambda k: (-vals[k], k))
        assert got[i] == want, i


def test_trailing_max_matches_brute_force_with_gaps() -> None:
    rng = random.Random(2)
    vals: list[float | None] = [None if rng.random() < 0.2 else rng.random() for _ in range(200)]
    got = sp.trailing_max(vals, 9)
    for i in range(len(vals)):
        window = [v for v in vals[max(0, i - 8) : i + 1] if v is not None]
        assert got[i] == (max(window) if window else None)


# ----------------------------------------------------------------------------- runs


def test_run_launches_at_the_low_and_peaks_at_the_high() -> None:
    closes = ramp(100, 80, 40) + ramp(80, 130, 60)[1:] + ramp(130, 100, 40)[1:]
    s = make_stock(closes)
    runs = sp.find_runs(s, P, sp.forward_max_index(closes, P.horizon), ended=True)
    assert len(runs) == 1
    assert closes[runs[0].launch] == 80 and closes[runs[0].peak] == 130


def test_an_ineligible_low_is_not_a_launch() -> None:
    closes = ramp(100, 80, 40) + ramp(80, 130, 60)[1:]
    ok = [True] * len(closes)
    ok[39] = False  # the low itself is outside the universe that day
    s = make_stock(closes, ok)
    (run,) = sp.find_runs(s, P, sp.forward_max_index(closes, P.horizon), ended=True)
    assert run.launch == 38


def test_no_run_below_the_bar_and_runs_do_not_overlap() -> None:
    assert not sp.find_runs(make_stock(ramp(100, 140, 100)), P,
                            sp.forward_max_index(ramp(100, 140, 100), 252), True)  # fmt: skip
    closes = ramp(100, 160, 50) + ramp(160, 120, 30)[1:] + ramp(120, 200, 50)[1:]
    # within 252 sessions the highest close after 100 is 200: one run
    assert len(sp.find_runs(make_stock(closes), P, sp.forward_max_index(closes, 252), True)) == 1
    short = replace(P, horizon=60)  # 160 is the high within 60 sessions: two runs
    runs = sp.find_runs(make_stock(closes), short, sp.forward_max_index(closes, 60), True)
    assert len(runs) == 2
    assert runs[0].peak < runs[1].launch
    assert closes[runs[1].launch] == 120


def test_a_recent_run_is_open_unless_the_stock_stopped_trading() -> None:
    closes = [120.0] * 300 + ramp(120, 100, 30)[1:] + ramp(100, 160, 50)[1:]  # low at 329
    fmax = sp.forward_max_index(closes, 252)
    (open_run,) = sp.find_runs(make_stock(closes), P, fmax, ended=False)
    assert not open_run.complete
    (ended_run,) = sp.find_runs(make_stock(closes), P, fmax, ended=True)
    assert ended_run.complete


# ----------------------------------------------------------------------------- bases


def _base_series() -> list[float]:
    # 70 sessions up to 110, 20 sessions between 100 and 108, then a breakout close of 112
    rng = random.Random(3)
    return ramp(80, 110, 70) + [100 + rng.random() * 8 for _ in range(20)] + [112.0, 113.0]


def test_breakout_from_a_base() -> None:
    closes = _base_series()
    (b,) = sp.find_bases(make_stock(closes), P)
    assert b.day == 90 and b.start == 69 and b.length == 21
    assert b.depth_pct == pytest.approx((1 - min(closes[69:90]) / 110) * 100)
    assert b.new_high


def test_no_breakout_when_the_base_is_too_short() -> None:
    assert sp.find_bases(make_stock(_base_series()), replace(P, min_base=25)) == []


def test_pullbacks_inside_a_run() -> None:
    c = [100.0, 120.0, 108.0, 130.0, 117.0, 140.0]
    assert sp._pullbacks(c, 0, 5) == pytest.approx([10.0, 10.0])


# ----------------------------------------------------------------------------- lift


def test_mantel_haenszel() -> None:
    assert sp.mantel_haenszel([[10, 10, 5, 20]]) == pytest.approx(10 * 20 / (10 * 5))
    two = sp.mantel_haenszel([[2, 1, 10, 20], [3, 2, 30, 40]])
    want = (2 * 20 / 33 + 3 * 40 / 75) / (1 * 10 / 33 + 2 * 30 / 75)
    assert two == pytest.approx(want)
    assert sp.mantel_haenszel([[0, 0, 5, 5]]) is None


def test_lift_table_uses_only_days_with_a_winner_and_skips_missing() -> None:
    lc = sp.LiftCounts()
    d1, d2 = date(2023, 1, 2), date(2023, 2, 1)
    lc.add(d1, True, [("x", True)])
    lc.add(d1, False, [("x", False)])
    lc.add(d1, False, [("x", None)])  # missing: not counted for x
    lc.add(d2, False, [("x", True)])  # no winner that day: not in the shares
    (row,) = sp.lift_table(lc, [("x", "X")], P)
    assert (row["winners"], row["winners_with"], row["rest"], row["rest_with"]) == (1, 1, 1, 0)
    assert row["share_winners"] == 100 and row["share_rest"] == 0


def test_volatility_adjusted_lift_removes_a_volatility_effect() -> None:
    """X goes with volatility and volatility with winning; within each volatility third X makes
    no difference, so the crude lift is about 1.9 and the adjusted one exactly 1."""
    lc = sp.LiftCounts()
    d = date(2023, 5, 2)
    groups = [  # (volatile, winner, with X, without X)
        (True, True, 6, 2), (True, False, 15, 5), (False, True, 1, 1), (False, False, 40, 40),
    ]  # fmt: skip
    for vol, won, with_x, without_x in groups:
        for val, n in ((True, with_x), (False, without_x)):
            for _ in range(n):
                lc.add(d, won, [("x", val)], vol)
    (row,) = sp.lift_table(lc, [("x", "X")], P)
    assert row["lift"] == pytest.approx(7 * 45 / (3 * 55))
    assert row["lift_vol_adj"] == pytest.approx(1.0)


# ----------------------------------------------------------------------------- point in time


def _rich_stock() -> tuple[sp.Stock, sp.Context]:
    rng = random.Random(4)
    closes = [100.0]
    for _ in range(699):
        closes.append(max(5.0, closes[-1] * (1 + rng.gauss(0.001, 0.025))))
    s = make_stock(closes)
    s.rs = [None if i < 252 else rng.randint(1, 99) for i in range(len(s))]
    s.tt = [None if i < 252 else rng.random() < 0.3 for i in range(len(s))]
    s.sma200 = [sum(closes[i - 199 : i + 1]) / 200 if i >= 199 else None for i in range(len(s))]
    d = s.days
    ctx = ctx_for(
        s,
        nifty={x: 100 + k * 0.05 for k, x in enumerate(d)},
        fundamentals={s.iid: [sp.Fundamental(d[300], 30.0, 25.0, 5.0),
                              sp.Fundamental(d[420], 10.0, 5.0, -3.0)]},
        holdings={s.iid: [sp.Holding(d[310], None, None), sp.Holding(d[380], 0.5, -0.2)]},
        vcp={s.iid: [(d[k], "VCP" if k % 15 == 0 else "NONE") for k in range(260, 700, 5)]},
        sector_tt_share={("Synthetic", x): 0.4 for x in d}, tt_share={x: 0.3 for x in d},
        vol_cut={x: 0.015 for x in d},
    )  # fmt: skip
    return s, ctx


def _cut(s: sp.Stock, i: int) -> sp.Stock:
    parts = {k: (v[: i + 1] if isinstance(v, list) else v) for k, v in s.__dict__.items()}
    return sp.Stock(**parts)  # type: ignore[arg-type]


def test_launch_features_use_no_later_data() -> None:
    """Every launch feature on day i is the same whether or not the days after i exist."""
    s, ctx = _rich_stock()
    cfg = StageConfig()
    full = sp.DayView.build(s, ctx, P, cfg)
    for i in range(255, len(s), 37):
        cut = sp.DayView.build(_cut(s, i), ctx, P, cfg)
        for key, _label, f in sp.LAUNCH_FEATURES:
            assert f(full, i) == f(cut, i), (key, i)


def test_fundamentals_and_shareholding_only_once_available() -> None:
    s, ctx = _rich_stock()
    v = sp.DayView.build(s, ctx, P, StageConfig())
    assert v.fundamental(299) is None
    f = v.fundamental(300)
    assert f is not None and f.eps_yoy == 30.0
    assert v.fundamental(300 + 200) is not None and v.fundamental(419) is not None
    assert v.holding(309) is None and v.holding(379) == ctx.holdings[s.iid][0]
    late = sp.DayView.build(s, ctx, replace(P, fundamentals_max_age=30), StageConfig())
    assert late.fundamental(300 + 40) is None  # 56 calendar days old: stale


def test_vcp_scan_of_the_breakout_day_itself_is_not_used() -> None:
    s, ctx = _rich_stock()
    v = sp.DayView.build(s, ctx, P, StageConfig())
    assert v.vcp_class(270, before=False) == "VCP"  # the scan of that very day
    assert v.vcp_class(270, before=True) == "NONE"  # the one before it
    assert v.vcp_class(250, before=True) is None  # no scan yet


def test_holdings_timeline_from_the_database() -> None:
    con = duckdb.connect()
    con.execute(
        "CREATE TABLE shareholding_snapshots (instrument_id VARCHAR, as_on DATE,"
        " is_quarter_end BOOLEAN, revision_number INTEGER, available_at TIMESTAMPTZ,"
        " promoter_pct DOUBLE, fii_pct DOUBLE)"
    )
    rows = [  # a filing at 16:00 IST counts from the next day; a revision replaces the value
        ("A", date(2024, 3, 31), True, 0, datetime(2024, 4, 10, 4, 0, tzinfo=UTC), 50.0, 10.0),
        ("A", date(2024, 6, 30), True, 0, datetime(2024, 7, 10, 10, 30, tzinfo=UTC), 51.0, 9.0),
        ("A", date(2024, 6, 30), True, 1, datetime(2024, 7, 20, 4, 0, tzinfo=UTC), 52.0, 9.0),
    ]
    con.executemany("INSERT INTO shareholding_snapshots VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    (a0, a1, a2) = spd._holdings(con)["A"]
    assert a0 == sp.Holding(date(2024, 4, 10), None, None)
    assert a1.available == date(2024, 7, 11)
    assert a1.promoter_chg == pytest.approx(1.0) and a1.fii_chg == pytest.approx(-1.0)
    assert a2.available == date(2024, 7, 20) and a2.promoter_chg == pytest.approx(2.0)


# ----------------------------------------------------------------------------- whole study


def _study() -> tuple[sp.StudyResult, sp.Context]:
    s, ctx = _rich_stock()
    closes = [100.0] * 300 + ramp(100, 60, 60)[1:] + ramp(60, 150, 120)[1:] + ramp(150, 90, 80)
    t = make_stock(closes[: len(s)], iid="NSE_EQ|RUN")
    t.rs = [None if i < 252 else 60 for i in range(len(t))]
    res = sp.StudyResult()
    ctx = replace(ctx, calendar=list(s.days) + _days(len(s) + 300)[len(s) :])
    for stock in (s, t):
        sp.study_stock(stock, ctx, P, StageConfig(), res)
    sp.add_sector_peers(res.runs)
    return res, ctx


def test_study_finds_the_run_and_counts_its_launch_as_a_winner() -> None:
    res, ctx = _study()
    run = next(r for r in res.runs if r.symbol == "RUN")
    assert run.launch_close == 60 and run.peak_close == 150 and run.gain_pct == pytest.approx(150)
    assert run.to20 is not None and run.to50 is not None and run.to100 is not None
    assert run.to20 < run.to50 < run.to100 <= run.sessions_to_peak
    assert sum(res.launch.winners.values()) >= 1
    summary = sp.summarize(res, ctx, P)
    assert summary["counts"]["runs"] == len(res.runs)
    assert {r["key"] for r in summary["lift_launch"]} <= {k for k, _l, _f in sp.LAUNCH_FEATURES}


def test_store_replaces_the_previous_study(tmp_path: Path) -> None:
    from vcp_scanner.data.storage.duckdb_store import DuckDBStore

    res, ctx = _study()
    summary = sp.summarize(res, ctx, P)
    store = DuckDBStore(tmp_path / "t.duckdb")
    store.migrate()
    first = spd.store(
        store.conn, res.runs, summary, "src", "vcp", datetime(2026, 10, 23, tzinfo=UTC)
    )
    second = spd.store(store.conn, res.runs, summary, "src", "vcp",
                       datetime(2026, 10, 24, tzinfo=UTC))  # fmt: skip
    assert first != second
    studies = store.conn.execute("SELECT study_id FROM superperformance_studies").fetchall()
    assert studies == [(second,)]
    row = store.conn.execute(
        "SELECT count(*), list(DISTINCT gain_band) FROM superperformance_runs"
    ).fetchone()  # fmt: skip
    assert row is not None
    n, bands = row
    assert n == len(res.runs) and set(bands) <= {"50-100", "doubled", "tripled"}
    bases = store.conn.execute("SELECT count(*) FROM superperformance_bases").fetchone()
    assert bases is not None and bases[0] == sum(len(r.bases) for r in res.runs)
    store.close()
