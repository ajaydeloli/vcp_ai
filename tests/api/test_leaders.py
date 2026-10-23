"""Past Leaders (owner, 2026-10-23): the stored superperformance study, read only."""

from __future__ import annotations

from tests.api.conftest import Env
from tests.api.test_api import get


def test_all_runs_newest_launch_first_with_the_filter_lists(ro_env: Env) -> None:
    body = get(ro_env, "/api/v1/leaders")
    assert body["study"]["study_id"] == "sp-test" and body["study"]["runs_total"] == 3
    assert body["study"]["lift_end"] == "2025-09-26"
    assert body["study"]["survivorship_status"] == "PARTIAL"
    assert [r["symbol"] for r in body["runs"]] == ["BETA", "GAMMA", "ALPHA"]
    assert body["years"] == [2024, 2026]
    assert body["sectors"] == ["Capital Goods", "Chemicals", "Unclassified"]
    gamma = body["runs"][1]
    assert gamma["sector"] is None and gamma["vcp_before_breakout"] is None  # null, not ""
    beta = body["runs"][0]
    assert beta["complete"] is False and beta["giveback_pct"] is None


def test_filters(ro_env: Env) -> None:
    def symbols(query: str) -> list[str]:
        return [r["symbol"] for r in get(ro_env, f"/api/v1/leaders?{query}")["runs"]]

    assert symbols("year=2024") == ["GAMMA", "ALPHA"]
    assert symbols("sector=Unclassified") == ["GAMMA"]
    assert symbols("band=doubled") == ["ALPHA"]
    assert symbols("vcp=true") == ["ALPHA"]
    assert symbols("vcp=false") == ["BETA", "GAMMA"]
    assert symbols("year=2024&band=tripled") == ["GAMMA"]
    r = ro_env.client.get("/api/v1/leaders?band=quadrupled")
    assert r.status_code == 422


def test_one_run_with_its_bases_and_bars_around_it(ro_env: Env) -> None:
    body = get(ro_env, "/api/v1/leaders/1")
    assert body["run"]["symbol"] == "ALPHA"
    assert [b["seq"] for b in body["bases"]] == [1, 2]
    days = [b["day"] for b in body["bars"]]
    assert days == sorted(days)
    assert days[0] < "2024-03-01" < "2024-12-02" < days[-1]
    launch = days.index("2024-03-01")
    peak = days.index("2024-12-02")
    assert launch == 40 and len(days) - 1 - peak == 60  # 40 sessions before, 60 after


def test_unknown_run_is_404(ro_env: Env) -> None:
    assert ro_env.client.get("/api/v1/leaders/999").status_code == 404


def test_no_study_stored_is_an_empty_page(env: Env) -> None:
    import duckdb

    from vcp_scanner.serving import refresh_serving_copy

    with duckdb.connect(str(env.db)) as con:
        for t in ("superperformance_bases", "superperformance_runs", "superperformance_studies"):
            con.execute(f"DELETE FROM {t}")
    refresh_serving_copy(env.db)
    body = get(env, "/api/v1/leaders")
    assert body["study"] is None and body["runs"] == [] and body["as_of"] is None
    assert env.client.get("/api/v1/leaders/1").status_code == 404


def test_the_leaders_module_only_reads() -> None:
    from pathlib import Path

    import vcp_scanner.api.leaders as mod

    src = Path(mod.__file__).read_text(encoding="utf-8").upper()
    for word in ("INSERT ", "UPDATE ", "DELETE ", "CREATE ", "DROP "):
        assert word not in src
