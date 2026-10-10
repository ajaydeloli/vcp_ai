from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.api.fixture import CONFIG_DIR, build_all, build_research_db, write_report_files
from tests.live.fakes import UNIVERSE, Clock, make_service
from vcp_scanner.api.app import create_app
from vcp_scanner.api.context import Context
from vcp_scanner.data.providers._time import IST

NOW = datetime(2026, 10, 6, 14, 0, tzinfo=IST)


class Env(NamedTuple):
    app: FastAPI
    client: TestClient
    ctx: Context
    data_dir: Path
    db: Path  # the main database (tests may change it and refresh the serving copy)
    set_now: Callable[[datetime], None]


def _make(data_dir: Path) -> Env:
    ctx, db = build_all(data_dir)
    research = build_research_db(data_dir / "golden_src.duckdb")
    write_report_files(data_dir / "reports")
    clock = {"now": NOW}
    # Every test app gets a fake live feed (synthetic prices, polled once at NOW): no test can
    # reach a real provider.
    live_clock = Clock(NOW)
    live, _ = make_service(live_clock, universe=UNIVERSE)
    live.poll_once()
    app = create_app(
        config_dir=CONFIG_DIR,
        data_dir=data_dir,
        now=lambda: clock["now"],
        live=live,
        research_db=research,
        reports_dir=data_dir / "reports",
    )

    def set_now(t: datetime) -> None:
        clock.update(now=t)
        live_clock.now = t

    return Env(app, TestClient(app), ctx, data_dir, db, set_now)


@pytest.fixture
def env(tmp_path: Path) -> Env:
    """A fresh fixture database per test (for tests that change it or the clock)."""
    return _make(tmp_path / "data")


@pytest.fixture(scope="module")
def ro_env(tmp_path_factory: pytest.TempPathFactory) -> Env:
    """One fixture database for the tests that only read (they must not change anything)."""
    return _make(tmp_path_factory.mktemp("api") / "data")
