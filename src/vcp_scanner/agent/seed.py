"""The decisions the notebook starts with (AGENT_SPECIFICATION §8, A1), taken from the specs.
Idempotent: an entry already seeded is not added again. Later decisions are added as notes."""

from __future__ import annotations

from vcp_scanner.agent.store import AgentStore

SEED: tuple[tuple[str, str, str], ...] = (
    (
        "decision",
        "2026-10-06: strategy research stopped. The five strategies are frozen until the "
        "review in April 2027: vcp-1.1.0, flat_base-1.0.0, three_weeks_tight-1.1.0, "
        "cup_handle-1.1.0, double_bottom-1.0.0, with entry cross_5, regime breadth50, exit "
        "hold_low8, 10 positions, 15 bps a side; scoring-1.0.0, labels-1.0.0. Validation "
        "(§20.9) showed no edge outside the strong 2022-2024 advance; live paper trading from"
        " 2026-10-01 is the only fair test. Any change is a new version and restarts that "
        "strategy's paper clock. STRATEGY_SPECIFICATION §21.",
        "freeze,strategy,review",
    ),
    (
        "decision",
        "Data windows: development 2022-02 to 2024-06 and validation 2024-07 to 2026-09 have "
        "both been used to choose the frozen strategies and count as seen. New ideas may be "
        "generated on development data; only a candidate's shadow paper ledger from its start"
        " date can promote it. AGENT_SPECIFICATION §4.",
        "research,overfitting,validation",
    ),
    (
        "decision",
        "Fundamentals, sectors, shareholding and the Market Health Score are display and "
        "research only until the review: nothing in the scan, score, backtest or paper ledger"
        " reads them. FUNDAMENTALS_SPECIFICATION §2.",
        "freeze,fundamentals,shareholding",
    ),
    (
        "finding",
        "2026-10-19 F7 (development period, 45,161 setups, 84 % scored): the SCORING §7 "
        "fundamental shadow score does not order outcomes at 60 sessions (top minus bottom "
        "fifth within date -0.1 pp, all strategies; VCP +0.7 pp, t 0.9). VCP IC +0.03 to "
        "+0.04 at 5-20 sessions only. The hard gate makes no difference within dates. EPS QoQ"
        " steadiest part; margin change and debt/equity have the wrong sign. Shareholding: "
        "FII/promoter nothing, DII small short-term. Recommendation: keep fundamentals "
        "display only; validation not opened. FUNDAMENTALS_SPECIFICATION §12.",
        "f7,fundamentals,research",
    ),
    (
        "decision",
        "2026-10-19 agent rules: candidates run in a shadow paper ledger, nothing replaces a "
        "frozen strategy before the review unless the owner ends the freeze; at most 3 pre-"
        "registered hypotheses per strategy per review cycle; promotion needs at least 30 "
        "shadow trades and 3 months; pre-approved kinds are notes and watchlist only; the AI "
        "never approves its own proposals. AGENT_SPECIFICATION §9.",
        "agent,governance",
    ),
    (
        "decision",
        "Data sources: NSE only for prices, results and shareholding; never scrape "
        "screener.in (terms); never bypass an NSE block (no VPN, proxy, IP change, fake "
        "browser signals, or the owner's browser); keep requests gentle (2 s apart). NSE "
        "refused us after about 9 hours at 2 s (2026-10-18): split long backfills across "
        "days.",
        "nse,data,rules",
    ),
    (
        "decision",
        "Live prices: Upstox is the provider in use, Kite is for testing. Switch by editing "
        "the provider line of config/live.yaml only; the running API picks it up. Never "
        "commit the owner's live.yaml switch.",
        "live,provider",
    ),
    (
        "decision",
        "2026-10-15: shareholding is shown on the Fundamentals page (Results / Shareholding "
        "switch) and the stock page, not on the Screener, which stays technical plus EPS and "
        "sales growth.",
        "dashboard,shareholding",
    ),
    (
        "note",
        "Evening runs: Windows scheduled tasks 'VCP daily run' 19:15 and 'VCP daily run late'"
        " 22:00 IST, Monday to Friday, headless; the status bar shows a running or stopped "
        "run. Manual steps and commands: USER_GUIDE.md.",
        "operations,runs",
    ),
    (
        "note",
        "Manual sectors: the owner fills sectors from NSE quote pages in the spreadsheet; "
        "entries live in config/manual_sectors.yaml (NSE's own lists win). 991 of 1,274 "
        "universe stocks covered on 2026-10-19.",
        "sectors,data",
    ),
)


def seed_notebook(store: AgentStore) -> int:
    """Add the seed entries not yet present; returns how many were added."""
    added = 0
    for kind, text, tags in SEED:
        if not store.has_seed(text):
            store.add_note(kind, text, tags, source="seed")
            added += 1
    return added
