"""The agent MCP server, step A1 (AGENT_SPECIFICATION): tier-0 tools over the fixture API, the
notebook (append-only, validated), the seed, the audit row of every call, and that no write tool
beyond the notebook exists."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from tests.api.conftest import Env
from vcp_scanner.agent.seed import SEED, seed_notebook
from vcp_scanner.agent.server import _compact, build_server
from vcp_scanner.agent.store import AgentStore

REPO = Path(__file__).resolve().parents[2]
READ_TOOLS = {
    "run_status", "market_health", "setups", "screener", "stock", "fundamentals_table",
    "paper_ledger", "activity", "backtests", "spec", "notebook_search", "decision_log",
    "daily_brief", "score_history", "data_quality", "corporate_actions", "price_bars",
    "scan_run", "backtest",
}  # fmt: skip


@pytest.fixture
def server(ro_env: Env, tmp_path: Path) -> Any:
    return build_server(data_dir=str(ro_env.data_dir), agent_db=tmp_path / "agent.duckdb",
                        repo_root=REPO, client=ro_env.client)  # fmt: skip


def call(server: Any, tool: str, **args: Any) -> Any:
    res = asyncio.run(server.call_tool(tool, args))
    content = res[0] if isinstance(res, tuple) else res
    return json.loads(content[0].text) if content[0].text[:1] in "[{" else content[0].text


def test_the_tool_list_is_read_tools_plus_the_notebook(server: Any) -> None:
    names = {t.name for t in asyncio.run(server.list_tools())}
    assert names == READ_TOOLS | {"add_note"}  # no approve, apply, order or config tool


def test_read_tools_return_compact_api_data(server: Any) -> None:
    status = call(server, "run_status")
    assert status["prices_date"] == "2026-10-05"
    setups = call(server, "setups", strategy="vcp", limit=2)
    assert setups["strategy_id"] == "vcp" and len(setups["rows"]) <= 2
    stock = call(server, "stock", symbol="alpha")
    assert stock["setups"]["symbol"] == "ALPHA"
    assert stock["fundamentals"]["shareholding"]["promoter_pct"] == 48.5
    brief = call(server, "daily_brief")
    assert brief["as_of"] == "2026-10-05" and set(brief["setups"]) >= {"vcp", "flat_base"}
    assert {p["strategy_id"] for p in brief["paper"]} >= {"vcp"}


def test_tools_read_a_past_scan_day_and_a_score_history(server: Any) -> None:
    prev = call(server, "setups", strategy="vcp", date="2026-10-02")
    assert prev["as_of"] == "2026-10-02" and "rows" not in prev  # no scan that day: no rows
    assert call(server, "screener", date="2026-10-05")["as_of"] == "2026-10-05"
    assert (
        call(server, "stock", symbol="ALPHA", date="2026-10-05")["setups"]["as_of"] == "2026-10-05"
    )
    hist = call(server, "score_history", symbol="alpha", strategy="flat_base")
    assert [(r["day"], r["strategy_id"]) for r in hist["rows"]] == [("2026-10-05", "flat_base")]
    with pytest.raises(Exception, match="YYYY-MM-DD"):
        asyncio.run(server.call_tool("setups", {"date": "16 Oct"}))


def test_diagnostic_tools_and_setup_filters(server: Any) -> None:
    dq = call(server, "data_quality", status="all")
    assert dq["total"] == 0  # the fixture has no data-quality events
    assert call(server, "corporate_actions", symbol="alpha")["symbol"] == "ALPHA"
    assert call(server, "price_bars", symbol="ALPHA", days=5)["symbol"] == "ALPHA"
    assert call(server, "scan_run")["as_of"] == "2026-10-05"
    bt = call(server, "backtest", backtest_id="bt-dev", trades=5)
    assert bt["run"]["backtest_id"] == "bt-dev" and bt["run"]["period"] == "development"
    every = call(server, "setups", strategy="flat_base")["rows"]
    high = call(server, "setups", strategy="flat_base", min_score=75)["rows"]
    assert len(high) < len(every) and all(r["score"] >= 75 for r in high)
    near = call(server, "setups", strategy="vcp", max_pivot_distance_pct=100)
    assert all(0 <= r["pivot_distance_pct"] <= 100 for r in near.get("rows", []))


def test_unknown_stock_is_an_error_and_is_audited(server: Any, tmp_path: Path) -> None:
    with pytest.raises(Exception, match="404"):
        asyncio.run(server.call_tool("stock", {"symbol": "NOPE"}))
    rows = AgentStore(tmp_path / "agent.duckdb").audit_rows()
    assert rows[0][1] == "stock" and rows[0][3] is False


def test_spec_headings_and_sections(server: Any) -> None:
    heads = call(server, "spec", name="AGENT_SPECIFICATION")
    assert "# 1. Principles" in heads
    sec = call(server, "spec", name="agent_specification", section="9")
    assert sec.startswith("# 9. Owner decisions") and "Claude Code" in sec
    with pytest.raises(Exception, match="unknown document"):
        asyncio.run(server.call_tool("spec", {"name": "../.env"}))


def test_notebook_is_validated_searchable_and_append_only(server: Any, tmp_path: Path) -> None:
    text = "Failed breakouts cluster in weak breadth"
    added = call(server, "add_note", kind="finding", text=text, tags="Breadth, Breakouts")
    assert added["added"] == 1
    found = call(server, "notebook_search", text="weak breadth")
    assert found[0]["kind"] == "finding" and found[0]["tags"] == "breadth,breakouts"
    assert found[0]["source"] == "agent"
    with pytest.raises(Exception, match="kind must be one of"):
        asyncio.run(server.call_tool("add_note", {"kind": "order", "text": "buy"}))
    with pytest.raises(Exception, match="empty"):
        asyncio.run(server.call_tool("add_note", {"kind": "note", "text": "  "}))
    audit = AgentStore(tmp_path / "agent.duckdb").audit_rows()
    assert {r[1] for r in audit} >= {"add_note", "notebook_search"}


def test_seed_is_idempotent_and_read_by_the_decision_log(server: Any, tmp_path: Path) -> None:
    store = AgentStore(tmp_path / "agent.duckdb")
    assert seed_notebook(store) == len(SEED) and seed_notebook(store) == 0
    decisions = call(server, "decision_log", limit=50)
    assert all(d["kind"] == "decision" for d in decisions)
    assert any("frozen until the review" in d["text"] for d in decisions)


def test_compact_cuts_lists_and_drops_nulls() -> None:
    out = _compact({"a": None, "b": [], "c": 1.23456, "d": list(range(30))}, max_items=5)
    assert out == {"c": 1.235, "d": [0, 1, 2, 3, 4, "... 25 more"]}
