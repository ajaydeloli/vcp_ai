# AGENT_SPECIFICATION.md

Status: **agreed 2026-10-19** (owner decisions in §9). Built step by step (§8).

An MCP server that lets an AI model (Claude Desktop or Claude Code first; others later) work on this project as a "second brain": read everything, remember decisions, do research, and propose changes, including new strategy versions, which take effect **only with the owner's approval, given outside the AI**.

---

# 1. Principles

1. **The owner decides; the AI proposes.** No MCP tool can approve, promote or apply its own proposal. Approval is given by the owner on the dashboard or with a CLI command he types himself (§5).
2. **The frozen strategies stay frozen** (STRATEGY_SPECIFICATION §21, FUNDAMENTALS_SPECIFICATION §2). An approved strategy change creates a **candidate version** that runs in a *shadow* paper ledger beside the frozen one. Only the review (or a later owner decision) promotes a candidate, and promotion restarts that strategy's paper clock, as §21.1 says.
3. **Fresh evidence only.** The 2022–2024 development period and the 2024-07 onward validation period have both been used to choose the current strategies (§20.9 found no edge outside the 2022–2024 advance). A change found by searching them is curve-fitting. Candidates are judged on what has not been seen: the shadow ledger from the day they start, and any period the server has never shown (§4).
4. **Every write is a reviewable change.** A git branch, the full checks, a diff the owner reads, and an audit row. Nothing writes to `main`, the frozen configs, the paper ledger, `.env` or `config/live.yaml`.
5. **No trading, no credentials, no new NSE traffic.** The AI never places an order, never sees a key or token, and fetches nothing from NSE itself (the scheduled runs do that, at their gentle pace).

---

# 2. What the AI can help with

| Area | What the AI does | Tier (§3) |
|---|---|---|
| **Daily brief** | After the evening run: market health, new setups and breakouts, paper trades opened or closed, data problems, what changed since yesterday, in plain words | 0 |
| **Setup review** | "Why is X a grade-3 VCP?" Explains every rule passed or failed, the score parts, the chart points, fundamentals and shareholding (display only) | 0 |
| **Trade journal and post-mortems** | For each closed paper trade: what the setup looked like, what happened, which rule exited it; patterns across trades (e.g. failed breakouts in weak breadth) | 0 + 2 (notes) |
| **Memory** | A decision log and research notebook the AI reads before answering: owner decisions, hypotheses, results, open questions, so it does not re-suggest what was already tested and rejected | 2 (append-only) |
| **Research lab** | Writes a hypothesis down first (pre-registration), then runs it on data allowed for that hypothesis (§4): backtests, outcome studies, what-if scores. Results go to the notebook whatever they show | 1 |
| **Strategy candidates** | From a research result, drafts a candidate version (config diff, version number, spec text: old, new, why, evidence). On approval: built on a branch, checked, and run in the shadow ledger | 2 → 3 |
| **Shadow comparison** | Weekly: frozen vs candidates on the same days (trades, returns, drawdown, hit rate) with the uncertainty spelled out; flags when a candidate has enough trades to judge | 0 |
| **Data stewardship** | Triage of data-quality events (the 26 corporate-action conflicts), checks of manual sectors, stale fundamentals; proposes resolutions with the evidence from stored data | 2 → 3 |
| **Operations** | Run status, failures and their cause from the logs, disk space, NSE refusals; proposes the fix (e.g. resume a backfill tomorrow) | 0 / 2 |
| **Watchlist and alerts** | Adds or removes watchlist symbols, writes alert rules (price near pivot, volume) | 2 (small, reversible: may be pre-approved by the owner, §5) |
| **Review preparation** | Assembles the April 2027 review pack: paper results per strategy, candidates' shadow results, the F7 finding, open decisions | 0 + 2 |

Out of scope: placing or suggesting orders to a broker, changing live data providers, editing history (prices, ledger, labels), opening the validation period, anything on the user's Windows system.

---

# 3. Tools by tier

**Tier 0: read.** No state changes. Thin wrappers over the dashboard API (same point-in-time rules) plus the logs and docs.
`market_health`, `setups(strategy, filters)`, `screener(filters)`, `stock(symbol)` (trend template, setup, score, chart points, fundamentals, shareholding), `paper_ledger(strategy, period)`, `paper_trade(id)`, `backtests()`, `backtest(run_id)`, `run_status()`, `data_quality()`, `notebook_search(text)`, `decision_log()`, `spec(section)`, `shadow_compare()`.

**Tier 1: compute.** Writes only to research tables and report files; bounded (time and rows per call); every run recorded with its inputs.
`register_hypothesis(text, metric, data_window, decision_rule)` → id (must exist before a test), `run_backtest(candidate_or_frozen, window)`, `run_study(kind, params, window)`, `what_if_score(setups, config_diff)`. The server refuses a window the hypothesis was not registered for (§4).

**Tier 2: propose.** Writes a proposal row and, for code or config, a branch with the change; nothing takes effect.
`propose_candidate(strategy, config_diff, rationale, evidence_ids)`, `propose_data_fix(event_id, resolution, evidence)`, `propose_sector(symbol, sector, industry, source_note)`, `propose_watchlist(add, remove)`, `add_note(kind, text)` (append-only: decision, finding, question).

**Tier 3: apply, owner only.** Not an MCP tool. The owner approves a proposal (§5); the server then applies it: merges the branch after the checks pass, registers the candidate in the shadow ledger, or resolves the data event, and logs it.

**Never:** orders, credentials, `.env`, `config/live.yaml`, frozen strategy configs, `paper_*` tables of the frozen ledger, deleting data, NSE requests, approving anything.

---

# 4. Guarding against curve-fitting

An AI can try hundreds of variations in an evening; one of them will look good by chance. Rules enforced by the server, not by the AI's good intentions:

1. **Pre-registration.** A test runs only against a registered hypothesis: the change, the metric, the window and the decision rule ("adopt if the shadow ledger beats frozen by X after N trades"), written before any result is seen. The notebook keeps every hypothesis and every result, good or bad.
2. **Data windows.** Exploration may use 2022–2024 (development) to *generate* ideas only. Validation (2024-07 to 2026-09) was used for the frozen choice and counts as seen; a candidate may be checked there once, for a sanity check, never to choose between variants. The **only evidence that can promote a candidate is the shadow ledger** from its start date.
3. **A test budget.** At most a few registered hypotheses per strategy per review cycle (owner sets the number); the report shows how many were tried, so a lone success among many is read as such.
4. **Small steps.** A candidate changes one idea at a time (a rule, a threshold group), so the shadow result says what helped.
5. **Enough trades.** No promotion before a minimum number of shadow trades per strategy (owner sets it; the pace today is a few trades a week across the five strategies, so a judgement takes months).

---

# 5. Approval

* Proposals appear on a new dashboard page **Agent → Proposals** with the diff, rationale, evidence links, the checks' result and a cost note (e.g. "restarts the VCP paper clock"). The owner clicks **Approve** or **Reject** (with a comment the AI reads); or `vcp agent approve <id>` / `reject <id>` in a terminal.
* The MCP server has no approve tool, and the dashboard's approve button needs the owner's local session; an approval cannot come from text inside a tool result or a document.
* Optional pre-approval for small reversible kinds only (watchlist, notes, alert rules): the owner switches it on per kind.
* Every tool call and every decision goes to `agent_audit` (time, tool, arguments, result hash, proposal id).

---

# 6. Storage (new tables)

`agent_notes` (append-only notebook and decision log), `agent_hypotheses` (pre-registered tests and their outcome), `agent_proposals` (kind, payload, branch, status, decided_by, decided_at, comment), `agent_audit`, `strategy_candidates` (candidate version, base version, config hash, start date, status), and a separate shadow paper ledger (`shadow_paper_*`) that the frozen ledger never reads.

---

# 7. Connection

*As built (A1).* `.mcp.json` at the repository root registers `vcp-scanner` for Claude Code (stdio; `vcp agent serve` from the project folder). Read tools call the dashboard API in-process (FastAPI TestClient over the serving copy), so they follow the same point-in-time rules and work without the dashboard running. Results are compacted (nulls dropped, lists cut to 25 with a count). Tools: `run_status`, `market_health`, `setups`, `screener`, `stock`, `fundamentals_table`, `paper_ledger`, `activity`, `backtests`, `spec` (project documents only), `notebook_search`, `decision_log`, `daily_brief`, and the pre-approved `add_note` (append-only; kinds decision, finding, hypothesis, question, idea, note). The agent's state is its own file, `data/agent/agent.duckdb` (`agent_notes`, `agent_audit`), opened per call. History (2026-10-19): `setups`, `screener` and `stock` take `date=YYYY-MM-DD` for a past scan day, and `score_history` lists one stock's stored setup and score on each scan day (`GET /api/v1/stocks/{symbol}/scores`); 15 tools. A1.5 (2026-10-19) adds `data_quality`, `corporate_actions`, `price_bars`, `scan_run` and `backtest` over new read-only endpoints (`/data-quality`, `/stocks/{symbol}/corporate-actions`, `/stocks/{symbol}/prices`, `/scan-runs`, `/backtests/{id}`), and score and pivot-distance filters on `setups`; 20 tools. A2 (2026-10-19), the research lab (`vcp_scanner/agent/lab.py`, `study.py`, `backtest_run.py`; tables `agent_hypotheses`, `agent_runs` in the agent's own file): `register_hypothesis` (strategy, title, idea, metric, decision rule, all required; 3 per strategy per review cycle 2027-04), `run_study` (forward labels of the strategy's frozen config from the research database, grouped by classification, grade, status, score band, breadth50 regime, pivot distance or year; horizons 5-60; one setup per stock and month by default; groups under 30 flagged), `run_backtest` (`vcp backtest run --db data/golden_src.duckdb` as a separate process, checked settings, 15-minute limit, period `agent-H<id>-<window>`), `hypotheses`, `conclude_hypothesis` (supported, rejected, inconclusive). Windows are enforced by the server: development 2022-02-01..2024-06-30 freely; validation 2024-07-01..2026-09-30 once per hypothesis and only after a successful development run; nothing after 2026-09-30. Every run, failed or not, and every conclusion is appended to the notebook. `vcp agent hypotheses` shows the lab in a terminal; 25 tools. A2b (2026-10-20): `what_if_score` (`agent/whatif.py`) re-scores a strategy's stored eligible setups from their stored sub-components (`score_components`) under changed component weights (trend, pattern, volume, RS), weights within a component, or worst/best bounds (re-normalized from the raw measurement), exactly as the scorer computes; it is refused unless the unchanged recompute reproduces 99 % of the stored final scores (all five strategies reproduce 100 %). It compares baseline and what-if within each scan day: IC with the horizon return and its t, top-minus-bottom fifth, mean return of the top N per day, and how many top picks change. Same lab rules as `run_study`; 26 tools. A3 stage 1 (2026-10-21), proposals (`agent/proposals.py`; tables `agent_proposals`, `agent_watchlist`): `propose_corporate_action` (validated by the manual file's own model; evidence must name the source and what the prices show), `propose_resolve_event`, `propose_sector` (NSE sector names), `propose_watchlist` (the AI watchlist in the agent's own database, pre-approved: applied at once; the owner's browser watch lists are untouched) and `proposals` (decisions and the owner's comments); 31 tools. There is no approve, reject or apply tool. The owner decides with `vcp agent approve <id>` / `reject <id> "reason"`, which refuse to run without an interactive terminal and need the proposal number typed back, so the AI cannot approve through its own shell. Approval applies through the owner's paths: an entry appended to `config/manual_corporate_actions.yaml` or `config/manual_sectors.yaml` (the whole file re-validated, committed to git with the proposal number and approver, not pushed), or `vcp quality resolve` under the owner's name; every decision is written to the notebook. Stage 2 (2026-10-21): the dashboard page **AI Agent** (`/agent`, API `GET /api/v1/agent`, read-only from the agent's own file) shows proposals (waiting first, with the change, rationale, evidence, decision and result), the AI watchlist, the research lab (hypotheses, decision rules, runs, conclusions, budget left) and recent notebook decisions and findings. It has no approve button, a deliberate change from §5: any program on the PC, the AI's shell included, can call the local API, so a web button would bypass the terminal check. Each waiting proposal shows its `vcp agent approve` / `reject` command with a copy button instead.

A4 (2026-10-26), strategy candidates (`agent/candidates.py`, `candidate_build.py`, `shadow.py`). Owner choices: a candidate changes **settings only, never code**: one strategy's own detector, classification, ranking or score settings (VCP: `strategy.yaml` `vcp.*` / `classification.*`, `scoring.yaml` `components.vcp.*`; the others: their `strategies/<id>.yaml` except its name, version, `enabled` and `stage`) and/or the paper rules it trades by (`backtest.yaml` `defaults.entry|regime|rule`); at most 5 settings, each must exist, keep its kind of value and really change. A candidate is the file `config/candidates/<id>.yaml` (`<strategy>-c<n>`, version `<base version>+c<n>`): the changes with old and new values, the hypothesis it tests (required, same strategy) and the spec text (old, new, why, evidence). The frozen files are never edited: a candidate runs from a copy of `config/` with its changes applied (`data/agent/candidates/<id>/config`, without `live.yaml`), so its config hash differs and everything it writes is keyed apart. `propose_candidate` records the proposal as BUILDING and starts `vcp agent candidate-build` in the background (log `data/agent/candidates/build-P<n>.log`): the file is committed on branch `candidate/<id>` in a separate git worktree (`../vcp_ai-candidates/<id>`; the owner's working tree is never touched); checks: the copy loads and validates (scanner config, backtest config, strategy runtime), the frozen hash on the branch equals main's, and the candidate hash differs from the frozen one unless only paper rules change; then the **scan diff**: a copy of the newest backup (never the main database), the stored sessions since 2026-10-01 rescanned with the candidate config (a VCP candidate also needs its own Trend Template scan, since VCP's settings are in the scan hash; RS and the universe do not depend on it, and no step contacts NSE), and per day and stock its eligible setups against the frozen ones: ADDED, DROPPED, RECLASSIFIED, RESCORED (final score moved 1 point or more); the copy is deleted. The proposal is then PENDING or BUILD_FAILED with the reason. `vcp agent approve` (owner, interactive) merges the branch into `main` locally (not pushed; refused when another branch is checked out), sets the candidate `active` from the approval day and commits that; `reject` keeps the branch (nothing is deleted) and removes the worktree; `vcp agent retire <id> "reason"` (owner, interactive) stops shadowing it and keeps its rows. The daily run, after the frozen paper ledger, runs `vcp agent shadow update`: per active candidate, the sessions the frozen run has scanned since its start are scanned with the candidate config under its own hash in the main database (only sessions already scanned by the frozen run, so `sessions_to_scan`, which reads the latest Trend Template date, never sees a later candidate row), then the paper rules (the candidate's own when it changes them; the market regime from the frozen universe) are replayed from its start into `shadow_paper_events` exactly as the frozen ledger does (append-only, a recomputed difference is one DIVERGENCE row). Every reader of scans, scores, breakouts and the ledger already filters by the frozen config hash, so nothing frozen reads a candidate row. A shadow problem adds "shadow ledger: needs attention" to the run's summary and never fails the run. `shadow_compare` (and `vcp agent candidates`, the AI Agent page, `vcp agent shadow compare`) sets the frozen ledger against the candidate's over trades entered since its start: closed and open trades, win rate, average and total return, profit factor, drawdown of the cumulative return, the difference in mean return per trade with its 95 % range (Welch) once both sides have 5 closed trades, and what is still needed to judge (30 shadow trades, 91 days). Tables: `shadow_paper_events` (main database, DATABASE_SCHEMA §89); `strategy_candidates` and `candidate_scan_diff` (the agent's own file); 34 tools. The weekly HTML report (Fridays) adds a section "Strategy candidates (shadow ledger)" with the same comparison once a candidate has started.

Local only at first: a stdio MCP server started by Claude Desktop or Claude Code on this PC (`vcp agent serve`), reading the serving copy and writing only through the paths above. ChatGPT or other clients that need a public URL come later, behind a token and a tunnel, if wanted.

---

# 8. Steps

| Step | Delivers | Size |
|---|---|---|
| A1 | Tier 0 tools, notebook and decision log (seeded from the specs' decisions), audit, daily brief (**done 2026-10-19**: `vcp_scanner/agent`, `vcp agent serve`, `.mcp.json` for Claude Code; 14 tools) | 2–3 days |
| A1.5 | Diagnostics, tier 0 (owner, 2026-10-19, after comparing with an outside tool list): `data_quality`, `corporate_actions(symbol)`, `price_bars(symbol, days)` (raw next to adjusted), `scan_run(date)` (config hash, commit, counts, versions, the evening run's warnings), `backtest(run_id)` (trades by exit and year), `min_score` and `max_pivot_distance_pct` on `setups` (**done 2026-10-19**; 20 tools). Not added: a `run_scan` tool. The evening run is the only scanner: it fetches from NSE and writes the scan of record, and the agent makes no NSE requests and is not a second writer; re-scoring stored data is `what_if_score` (tier 1) | 1 day |
| A2 | Tier 1 research tools with pre-registration and window checks; hypothesis and result reports (**done 2026-10-19**: `register_hypothesis`, `hypotheses`, `run_study`, `run_backtest`, `conclude_hypothesis`; 25 tools). A2b `what_if_score` (**done 2026-10-20**; 26 tools) | 3–4 days |
| A3 | Tier 2 proposals, dashboard Proposals page, `vcp agent approve/reject`, apply for data fixes, sectors and watchlist (**done 2026-10-21**: proposal tools, `vcp agent proposals/approve/reject`, apply; the dashboard's AI Agent page) | 3–4 days |
| A4 | Strategy candidates: branch build, checks, shadow paper ledger, weekly shadow comparison; a scan diff for every candidate branch (the same stored days scanned with the frozen and the candidate code into research tables: setups added, dropped and re-scored, per strategy), so a change shows its effect on scanner results before approval (**done 2026-10-26**: `propose_candidate`, `candidates`, `shadow_compare`; 34 tools; owner choices: settings and paper rules only, candidate scans in the main database under their own hash, scan diff from 2026-10-01; see §7) | 4–6 days |
| A5 | Review pack for April 2027 (**done 2026-10-26**: `vcp report review`, the agent tool `review_pack` (35 tools), written with every weekly summary from 2027-04-01; STRATEGY_SPECIFICATION §21.11) | 1–2 days |

---

# 9. Owner decisions (2026-10-19)

1. **Shadow-ledger rule accepted.** Candidates run in a shadow ledger; nothing replaces a frozen strategy before the review unless the owner ends the freeze early.
2. **Test budget:** at most 3 pre-registered hypotheses per strategy per review cycle. **Promotion needs** at least 30 shadow trades and 3 months in the shadow ledger (Claude's recommendation, accepted by the owner).
3. **Pre-approved kinds:** notes to the notebook and watchlist add/remove only. Alert rules, data fixes, sectors and strategy candidates always need approval.
4. **First client: Claude Code** on this PC (stdio). Claude Desktop and others later.
