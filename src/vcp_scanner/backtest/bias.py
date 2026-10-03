"""Bias report (PROJECT_DESIGN 40; Phase 9 step 5): how much each known bias can affect the
stored scans of a period. Read-only.

* **Survivorship**: the universe snapshots' status over the period. Prices come from NSE
  bhavcopies, which list every traded security of the day (delisted ones included), so the
  remaining gap is the ASM/GSM surveillance history, which only starts when collection began.
* **Corporate-action leakage**: today's adjusted prices include every split / bonus known now.
  An observation is *exposed* when its stock had a price adjustment effective after the
  observation's date (the scan then saw prices rescaled by an event it could not have known).
  Ratios (returns, moving-average comparisons, depths) are unaffected by a constant rescale;
  absolute price and traded-value thresholds can be. The look-ahead check measures the effect.
* **Parameter leakage**: how often each walk-forward period has been looked at.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from vcp_scanner.data.storage.duckdb_store import DuckDBStore


def bias_report(store: DuckDBStore, start: date, end: date, config_hash: str) -> dict[str, Any]:
    q = store.conn.execute
    surv = dict(q(
        "SELECT coalesce(survivorship_status, 'UNKNOWN'), count(DISTINCT as_of_date)"
        " FROM scan_runs WHERE scan_type = 'TREND_TEMPLATE' AND scan_config_hash = ?"
        " AND as_of_date BETWEEN ? AND ? GROUP BY 1",
        [config_hash, start, end]).fetchall())  # fmt: skip
    obs, exposed, exposed_eligible, eligible = q(
        """
        WITH later AS (
            SELECT DISTINCT s.instrument_id, s.as_of_date, s.eligible
            FROM setup_scores s JOIN corporate_action_adjustments a
              ON a.instrument_id = s.instrument_id AND a.effective_date > s.as_of_date
             AND a.known_to IS NULL AND a.price_factor <> 1
            WHERE s.config_hash = ? AND s.as_of_date BETWEEN ? AND ?
        )
        SELECT (SELECT count(*) FROM setup_scores WHERE config_hash = ?
                  AND as_of_date BETWEEN ? AND ?),
               (SELECT count(*) FROM later),
               (SELECT count(*) FROM later WHERE eligible),
               (SELECT count(*) FROM setup_scores WHERE config_hash = ? AND eligible
                  AND as_of_date BETWEEN ? AND ?)
        """,
        [config_hash, start, end, config_hash, start, end, config_hash, start, end],
    ).fetchone()  # type: ignore[misc]
    looks = dict(q("SELECT coalesce(period_name, '(none)'), count(*) FROM backtest_runs"
                   " GROUP BY 1").fetchall())  # fmt: skip
    return {
        "survivorship_dates": surv,
        "observations": obs,
        "ca_exposed": exposed,
        "ca_exposed_share": exposed / obs if obs else None,
        "eligible": eligible,
        "ca_exposed_eligible": exposed_eligible,
        "period_looks": looks,
    }
