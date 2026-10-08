"""``/api/v1/live/*`` against a fake feed: shapes, stamps, "not available", no provider calls."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import pytest

from tests.api.conftest import NOW, Env
from tests.api.fixture import CONFIG_DIR
from tests.live.fakes import SATURDAY, Clock, FakeFeed, make_service
from vcp_scanner.api import queries as q
from vcp_scanner.api.app import create_app
from vcp_scanner.domain.errors import ProviderAuthError, ProviderRateLimited


def quotes(env: Env, symbols: str) -> dict[str, Any]:
    r = env.client.get(f"/api/v1/live/quotes?symbols={symbols}")
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


def test_quotes_are_stamped_live_with_source_and_delay(ro_env: Env) -> None:
    body = quotes(ro_env, "ALPHA,BETA")
    assert body["mode"] == "live" and body["as_of"] == "2026-10-06"
    assert body["data_time"].startswith("2026-10-06T14:00:00")
    assert body["feed"]["state"] == "live" and body["feed"]["provider"] == "UPSTOX"
    assert body["feed"]["market"] == "open"
    a = body["quotes"][0]
    assert a["symbol"] == "ALPHA" and a["available"] is True and a["mode"] == "live"
    assert a["source"] == "UPSTOX" and a["last_price"] == 102.0 and a["delay_seconds"] == 0
    assert a["change"] == pytest.approx(2.0) and a["change_pct"] == pytest.approx(2.0)
    assert a["high"] == 103.0 and a["volume"] == 123456


def test_what_the_feed_does_not_have_is_not_available_never_zero(ro_env: Env) -> None:
    body = quotes(ro_env, "GAMMA,DELTA,ZZZ")
    by = {x["symbol"]: x for x in body["quotes"]}
    for sym, why in (("GAMMA", "no key"), ("DELTA", "no price"), ("ZZZ", "not in the scanned")):
        x = by[sym]
        assert x["available"] is False and why in x["reason"]
        assert x["mode"] is None and x["source"] is None
        assert all(x[k] is None for k in ("last_price", "change", "change_pct", "high", "volume"))
    assert body["mode"] == "unavailable" and body["as_of"] is None


def test_symbols_are_normalised_and_limited(ro_env: Env) -> None:
    body = quotes(ro_env, "alpha, ALPHA ,beta")
    assert [x["symbol"] for x in body["quotes"]] == ["ALPHA", "BETA"]
    assert ro_env.client.get("/api/v1/live/quotes?symbols=").status_code == 422
    assert ro_env.client.get("/api/v1/live/quotes?symbols=,,").status_code == 422
    assert ro_env.client.get("/api/v1/live/quotes").status_code == 422
    many = ",".join(f"S{i}" for i in range(501))
    assert ro_env.client.get(f"/api/v1/live/quotes?symbols={many}").status_code == 422
    ok = ",".join(f"S{i}" for i in range(500))
    assert ro_env.client.get(f"/api/v1/live/quotes?symbols={ok}").status_code == 200


def test_indices_have_a_line_and_missing_ones_say_so(ro_env: Env) -> None:
    r = ro_env.client.get("/api/v1/live/indices")
    assert r.status_code == 200
    body = r.json()
    by = {i["id"]: i for i in body["indices"]}
    assert list(by) == ["NIFTY50", "SENSEX", "NIFTY500"]
    n = by["NIFTY50"]
    assert n["label"] == "NIFTY 50" and n["last_price"] == 25500.0 and n["volume"] is None
    assert n["mode"] == "live" and n["points"] and n["points"][-1]["v"] == 25500.0
    assert by["NIFTY500"]["available"] is False and by["NIFTY500"]["points"] == []
    assert body["mode"] == "live"


def test_status(ro_env: Env) -> None:
    body = ro_env.client.get("/api/v1/live/status").json()
    assert body["feed"]["state"] == "live" and body["mode"] == "unavailable"
    assert body["feed"]["stocks_total"] == 5 and body["feed"]["stocks_covered"] == 3
    assert body["feed"]["holidays_known"] is False


def test_outside_hours_the_last_session_is_served_as_closed(env: Env) -> None:
    env.set_now(SATURDAY)  # the prices were received on Tuesday
    body = quotes(env, "ALPHA")
    a = body["quotes"][0]
    assert body["mode"] == "closed" and a["mode"] == "closed" and a["delay_seconds"] is None
    assert a["session_date"] == "2026-10-06" and body["as_of"] == "2026-10-06"
    assert body["feed"]["market"] == "closed" and body["feed"]["state"] == "closed"
    indices = env.client.get("/api/v1/live/indices").json()
    assert indices["mode"] == "closed"


def test_requests_never_call_the_provider(env: Env) -> None:
    clock = Clock(NOW)
    live, feed = make_service(clock)
    app = create_app(config_dir=CONFIG_DIR, data_dir=env.data_dir, now=lambda: NOW, live=live)
    from fastapi.testclient import TestClient

    client = TestClient(app)
    for url in ("quotes?symbols=ALPHA,BETA", "indices", "status"):
        assert client.get(f"/api/v1/live/{url}").status_code == 200
    assert feed.calls == []  # only a poll (the poller thread) talks to the provider


def _failing_client(env: Env, error: Exception, *, polled: bool = True) -> Any:
    from fastapi.testclient import TestClient

    clock = Clock(NOW)
    feed = FakeFeed({})
    live, _ = make_service(clock, feed, factory=lambda: (_ for _ in ()).throw(error))
    if polled:
        live.poll_once()
    app = create_app(config_dir=CONFIG_DIR, data_dir=env.data_dir, now=lambda: NOW, live=live)
    return TestClient(app)


def test_a_missing_token_is_reported_on_every_live_endpoint(env: Env) -> None:
    client = _failing_client(env, ProviderAuthError("UPSTOX_ACCESS_TOKEN is not set"))
    body = client.get("/api/v1/live/quotes?symbols=ALPHA").json()
    assert body["feed"]["state"] == "token_needed" and "fresh token" in body["feed"]["message"]
    assert body["quotes"][0]["available"] is False and body["mode"] == "unavailable"
    ind = client.get("/api/v1/live/indices").json()
    assert ind["feed"]["state"] == "token_needed"
    assert all(not i["available"] for i in ind["indices"])
    assert "UPSTOX_ACCESS_TOKEN" not in str(body)  # only the generic wording reaches the page


def test_a_rate_limit_is_reported(env: Env) -> None:
    err = ProviderRateLimited("Upstox quotes: HTTP 429", retry_after=45)
    body = _failing_client(env, err).get("/api/v1/live/status").json()
    assert body["feed"]["state"] == "rate_limited" and "rate limit" in body["feed"]["message"]
    assert body["feed"]["retry_at"] is not None


def test_closed_weekend_start_without_a_snapshot_is_not_available(env: Env) -> None:
    client = _failing_client(env, ProviderAuthError("x"), polled=False)
    ind = client.get("/api/v1/live/indices").json()
    assert ind["feed"]["state"] == "starting"
    assert all(i["reason"] == "waiting for the first live price" for i in ind["indices"])


def test_the_universe_is_the_eligible_members_of_the_newest_scan(env: Env) -> None:
    con = duckdb.connect(str(Path(env.data_dir) / "serving" / "vcp_serving.duckdb"), read_only=True)
    try:
        rows = q.live_universe(con.cursor(), env.ctx)
    finally:
        con.close()
    symbols = {s for s, _ in rows}
    assert {"ALPHA", "BETA", "GAMMA"} <= symbols
    assert rows == sorted(rows)  # ordered, no duplicates
    assert len(rows) == len(symbols)
