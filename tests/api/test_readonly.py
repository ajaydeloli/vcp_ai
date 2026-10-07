"""The API never writes (FRONTEND_SPECIFICATION 67.1; STRATEGY_SPECIFICATION 21.1)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi.routing import APIRoute

from tests.api.conftest import Env

PATHS = [
    "status", "strategies", "summary", "market?days=60", "setups", "setups?strategy=flat_base",
    "setups/overlap", "stocks/ALPHA/bars", "stocks/ALPHA/setups", "activity", "paper",
    "search?q=alp", "stocks/ALPHA/history", "market/health", "screener?stage=STAGE_2&min_rs=80",
]  # fmt: skip


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_every_route_is_a_get(ro_env: Env) -> None:
    routes = [r for r in ro_env.app.routes if isinstance(r, APIRoute)]
    assert routes and all(r.methods == {"GET"} for r in routes)
    assert all(r.path.startswith("/api/v1/") for r in routes)


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_writing_verbs_are_refused(ro_env: Env, method: str) -> None:
    for path in ("setups", "paper", "stocks/ALPHA/setups"):
        r = getattr(ro_env.client, method)(f"/api/v1/{path}")
        assert r.status_code == 405


def test_requests_leave_both_databases_untouched(ro_env: Env) -> None:
    serving = ro_env.data_dir / "serving" / "vcp_serving.duckdb"
    before = (_digest(serving), _digest(ro_env.db))
    for p in PATHS:
        assert ro_env.client.get(f"/api/v1/{p}").status_code == 200
    assert (_digest(serving), _digest(ro_env.db)) == before
    assert not list(serving.parent.glob("*.wal"))


def test_cross_origin_reads_from_the_local_page_only(ro_env: Env) -> None:
    ok = ro_env.client.get("/api/v1/paper", headers={"Origin": "http://localhost:3000"})
    assert ok.headers["access-control-allow-origin"] == "http://localhost:3000"
    other = ro_env.client.get("/api/v1/paper", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in other.headers
    pre = ro_env.client.options(
        "/api/v1/paper",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"},
    )
    assert pre.status_code == 400  # only GET is allowed across origins
