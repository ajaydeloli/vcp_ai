"""Recent listings (FRONTEND_SPECIFICATION 67.23): shown, but never part of the scan universe."""

from __future__ import annotations

import re
from pathlib import Path

from tests.api.conftest import Env
from tests.api.test_api import get


def test_a_stock_with_short_history_is_listed_with_only_the_measures_it_can_have(
    ro_env: Env,
) -> None:
    body = get(ro_env, "/api/v1/ipos")
    assert body["full_history_bars"] == 253
    rows = {r["symbol"]: r for r in body["rows"]}
    assert set(rows) == {"NEWCO"}  # every other fixture stock has a full year of bars
    r = rows["NEWCO"]
    assert r["bars"] == 60
    assert r["sma20"] is not None and r["sma50"] is not None
    assert r["range_pct"] is not None and r["avg_traded_value"] is not None
    assert r["since_listing_pct"] is not None and r["from_high_pct"] is not None


def test_a_young_listing_has_nulls_not_zeros(ro_env: Env) -> None:
    # the API sample is built from NEWCO (60 bars); fewer bars are covered by the pure function
    from vcp_scanner.api.ipos import _pct

    assert _pct(None, 10.0) is None and _pct(10.0, None) is None and _pct(10.0, 0.0) is None
    assert round(_pct(110.0, 100.0) or 0.0, 6) == 10.0


def test_recent_listings_are_not_in_the_universe_or_the_scan(ro_env: Env) -> None:
    ipos = {r["symbol"] for r in get(ro_env, "/api/v1/ipos")["rows"]}
    scanned = {r["symbol"] for r in get(ro_env, "/api/v1/screener?page_size=100")["rows"]}
    assert "NEWCO" in ipos and "NEWCO" not in scanned


def test_the_ipo_module_only_reads_and_touches_no_scan_code() -> None:
    src = (
        Path(__file__).resolve().parents[2] / "src" / "vcp_scanner" / "api" / "ipos.py"
    ).read_text()
    assert not re.search(r"\b(INSERT|UPDATE|DELETE|CREATE|DROP|ALTER)\b", src)
    imports = re.findall(r"^(?:from|import) (vcp_scanner[\w.]*)", src, re.M)
    assert set(imports) <= {
        "vcp_scanner.api",
        "vcp_scanner.api.queries",
        "vcp_scanner.domain.snapshot",
    }
