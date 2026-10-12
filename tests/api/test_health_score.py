"""The stored Market Health Score on /market/health (MARKET_HEALTH_SPECIFICATION §7), and the
straight-line ramp the readings of the cards still use."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import duckdb
import pytest

from tests.api.conftest import Env
from tests.api.fixture import AS_OF, MHS_VERSION, PREV
from tests.unit.test_market_health import CONFIG, healthy
from vcp_scanner.api import health
from vcp_scanner.market_health.build import _row
from vcp_scanner.market_health.features import FeatureDay
from vcp_scanner.market_health.scoring import score_day
from vcp_scanner.market_health.settings import load_settings
from vcp_scanner.serving import refresh_serving_copy


def test_ramp_is_a_straight_line_held_flat_at_both_ends() -> None:
    assert health._ramp(5, 8, 3) == 60.0  # distribution days: 3 or fewer is 100, 8 or more is 0
    assert health._ramp(6, 8, 3) == 40.0 and health._ramp(2, 8, 3) == 100.0
    assert health._ramp(9, 8, 3) == 0.0
    assert health._ramp(40, 20, 60) == 50.0  # breadth: 20% is 0, 60% is 100


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
