"""The committed API samples used by the frontend's Zod contract tests stay equal to what the
API returns (FRONTEND_SPECIFICATION 67; step D2).

If a response changes, this test fails until the samples are regenerated
(``UPDATE_API_SAMPLES=1 pytest tests/api/test_contract_samples.py``); the frontend's
``tests/contract.test.ts`` then shows whether its Zod schemas still accept the new shape.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from tests.api.conftest import Env

SAMPLES = Path(__file__).resolve().parents[2] / "frontend" / "tests" / "fixtures" / "api"
FIXED_TIME = "2026-10-06T14:00:00+05:30"  # the copy's real write time varies from run to run
CASES = {
    "status": "status",
    "strategies": "strategies",
    "summary": "summary",
    "market": "market?days=60",
    "setups_vcp": "setups?strategy=vcp",
    "setups_flat_base": "setups?strategy=flat_base",
    "overlap": "setups/overlap",
    "bars": "stocks/ALPHA/bars?days=60",
    "stock_setups": "stocks/ALPHA/setups",
    "activity": "activity?days=7",
    "paper": "paper",
    "search": "search?q=alp",
    "market_health": "market/health",
    "stock_history": "stocks/BETA/history",
    "screener": "screener?page_size=10",
    "live_quotes": "live/quotes?symbols=ALPHA,BETA,GAMMA,DELTA,ZZZ",
    "live_indices": "live/indices",
    "live_status": "live/status",
}


def test_samples_equal_the_api_responses(ro_env: Env) -> None:
    update = os.environ.get("UPDATE_API_SAMPLES") == "1"
    SAMPLES.mkdir(parents=True, exist_ok=True)
    for name, url in CASES.items():
        r = ro_env.client.get(f"/api/v1/{url}")
        assert r.status_code == 200, url
        body = r.json()
        body["data_time"] = FIXED_TIME
        text = json.dumps(body, indent=1) + "\n"
        path = SAMPLES / f"{name}.json"
        if update:
            path.write_text(text, encoding="utf-8")
        else:
            assert path.read_text(encoding="utf-8") == text, f"{name}: regenerate the samples"
