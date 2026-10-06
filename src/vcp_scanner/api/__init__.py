"""Read-only dashboard API (FRONTEND_SPECIFICATION 67; step D1).

``create_app`` serves the endpoints of section 67.3 from the serving copy of the database
(``vcp_scanner.serving``), opened read-only. Nothing here writes, and nothing changes a strategy,
rule, scan, score, label or the paper ledger (STRATEGY_SPECIFICATION 21.1).
"""
