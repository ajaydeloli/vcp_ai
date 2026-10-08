"""Live prices for the read-only dashboard (FRONTEND_SPECIFICATION 67.19).

DISPLAY ONLY. Nothing in this package feeds a scan, score, label, regime, universe, strategy or
the paper ledger, writes to any database or file, or is imported by anything but ``vcp_scanner.api``
(``tests/unit/test_live_isolation.py`` enforces it). The scheduled daily run never starts it.
"""
