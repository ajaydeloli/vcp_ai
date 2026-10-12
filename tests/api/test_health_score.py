"""The stored Market Health Score on /market/health (MARKET_HEALTH_SPECIFICATION §7) and the
cards' readings taken from it (FRONTEND_SPECIFICATION §67.34)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import duckdb
import pytest

from tests.api.conftest import Env
from tests.api.fixture import AS_OF, MHS_VERSION, PREV
from tests.unit.test_market_health import CONFIG, healthy
from vcp_scanner.market_health.build import _row
from vcp_scanner.market_health.features import FeatureDay
from vcp_scanner.market_health.scoring import score_day
from vcp_scanner.market_health.settings import load_settings
from vcp_scanner.serving import refresh_serving_copy


def test_the_latest_stored_day_and_its_history(ro_env: Env) -> None:
    body = ro_env.client.get("/api/v1/market/health").json()
    assert "verdict" not in body
    sc = body["score"]
    assert sc["day"] == AS_OF.isoformat() and sc["settings_version"] == MHS_VERSION
    assert [h["day"] for h in sc["history"]] == ["2026-09-30", PREV.isoformat(), AS_OF.isoformat()]
    assert sc["last_thrust"] == PREV.isoformat()
    # the latest day misses its breakout pillar and has five distribution days in ten
    assert sc["missing"] == ["breakouts"] and not sc["complete"]
    assert sc["raw_label"] == "Strong" and sc["label"] == "Mixed" and sc["exposure"] == "half"
    assert {c["id"] for c in sc["caps"]} == {"distribution", "incomplete"}
    pillars = {p["id"]: p for p in sc["pillars"]}
    assert list(pillars) == ["index_trend", "breadth", "leadership", "accumulation", "breakouts"]
    assert pillars["breakouts"]["score"] is None and pillars["breakouts"]["contribution"] is None
    assert sum(p["contribution"] or 0 for p in pillars.values()) == pytest.approx(sc["raw"], 0.01)
    part = pillars["breadth"]["parts"][0]
    assert part["label"] == "Stocks above their 50-day average, %" and part["reading"] == 72


def test_the_newest_settings_version_wins_and_versions_do_not_mix(env: Env) -> None:
    s = load_settings(CONFIG)
    old = replace(healthy(), pct_above_50=10)
    later = datetime(2026, 10, 6, tzinfo=UTC)
    with duckdb.connect(str(env.db)) as con:
        con.execute(
            f"INSERT INTO market_health_daily VALUES ({', '.join('?' * 15)})",
            _row(FeatureDay(AS_OF, old, 1.0, 1.0), score_day(old, s), "v-new", later),
        )
    refresh_serving_copy(env.db)
    sc = env.client.get("/api/v1/market/health").json()["score"]
    assert sc["settings_version"] == "v-new" and len(sc["history"]) == 1
    assert sc["last_thrust"] is None


def test_no_stored_day_gives_no_score(env: Env) -> None:
    with duckdb.connect(str(env.db)) as con:
        con.execute("DELETE FROM market_health_daily")
    refresh_serving_copy(env.db)
    assert env.client.get("/api/v1/market/health").json()["score"] is None


def test_the_cards_read_the_scores_own_points(ro_env: Env) -> None:
    body = ro_env.client.get("/api/v1/market/health").json()
    pts = body["score_points"]
    assert [p["day"] for p in pts] == [h["day"] for h in body["score"]["history"]]
    first, last = pts[0], pts[-1]
    # NIFTY 500 and its averages rebased by the same factor, 100 at the first session
    assert first["nifty500"] == 100.0
    assert last["n500_ma50"] == pytest.approx(100 * 105 / 110)
    assert last["above50"] == 72 and last["tt_share"] == 22 and last["highs_share"] == 90.0
    assert [p["thrust"] for p in pts] == [False, True, False]
    assert body["score"]["judged"] == 3


def test_the_divergence_warning_is_the_scores_cap(env: Env) -> None:
    s = load_settings(CONFIG)
    f = healthy(ad_line_gap=0.4)  # NIFTY 500 near its high, the A/D line well below its own
    later = datetime(2026, 10, 6, tzinfo=UTC)
    with duckdb.connect(str(env.db)) as con:
        con.execute(
            f"INSERT INTO market_health_daily VALUES ({', '.join('?' * 15)})",
            _row(FeatureDay(AS_OF, f, 1.0, 1.0), score_day(f, s), "v-div", later),
        )
    refresh_serving_copy(env.db)
    body = env.client.get("/api/v1/market/health").json()
    cap = next(c for c in body["score"]["caps"] if c["id"] == "divergence")
    assert body["warnings"] == [{"id": "divergence", "text": cap["reason"]}]
