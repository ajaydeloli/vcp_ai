import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AgentPage } from "@/components/Agent";
import { AgentCandidates } from "@/components/AgentCandidates";
import type { AgentCandidate } from "@/lib/schemas";
import { fx, mockApi, renderApp } from "./helpers";

const side = (closed: number, avg: number | null) => ({
  closed,
  open: 1,
  win_rate: closed ? 50 : null,
  avg_ret: avg,
  profit_factor: closed ? 1.4 : null,
  total_ret: closed * (avg ?? 0),
  max_drawdown: 4.5,
});

const ACTIVE: AgentCandidate = {
  candidate_id: "vcp-c1",
  strategy_id: "vcp",
  base_version: "vcp-1.1.0",
  candidate_version: "vcp-1.1.0+c1",
  status: "active",
  proposal_id: 9,
  hypothesis_id: 4,
  start_date: "2026-10-27",
  changes: [{ file: "strategy.yaml", path: "vcp.progressive_tolerance_pct", old: 10, new: 15 }],
  scan_diff: { days: 14, first: "2026-10-01", last: "2026-10-22", frozen_eligible: 300, stocks: 12, changes: { ADDED: 20, DROPPED: 0, RECLASSIFIED: 3, RESCORED: 5 } },
  diff_rows: [],
  shadow: {
    start_date: "2026-10-27",
    days: 40,
    frozen: side(6, 1.2),
    candidate: side(7, 2.1),
    avg_ret_diff: 0.9,
    avg_ret_diff_lo: -3.1,
    avg_ret_diff_hi: 4.9,
    ready_to_judge: false,
    needs: ["23 more closed shadow trades", "51 more days"],
  },
  shadow_error: null,
};

describe("Strategy candidates (agent step A4)", () => {
  it("shows the API sample's rejected candidate with its changes and scan diff", async () => {
    mockApi();
    renderApp(<AgentPage />);
    const card = await screen.findByRole("listitem", { name: "Candidate flat_base-c1" });
    expect(within(card).getByText("rejected")).toBeInTheDocument();
    expect(within(card).getByText(/classification.flat_base.max_depth_pct: 15 → 12/)).toBeInTheDocument();
    expect(within(card).getByText(/Dropped 1 · Reclassified 1/)).toBeInTheDocument();
    expect(within(card).getByText(/Not started/)).toBeInTheDocument();
    expect(within(card).queryByRole("table", { name: /Scan diff/ })).toBeNull();
    fireEvent.click(within(card).getByRole("button", { name: "Show setups" }));
    const table = within(card).getByRole("table", { name: "Scan diff of flat_base-c1" });
    expect(
      within(table)
        .getAllByRole("row")
        .map((r) => r.textContent),
    ).toEqual([
      "DayStockChangeFrozenCandidate",
      expect.stringMatching(/ALPHA.*Dropped.*flat base 70\.0/),
      expect.stringMatching(/BETA.*Reclassified.*tight flat base 60\.0.*flat base 58\.5/),
    ]);
  });

  it("names the candidate's change in words on its proposal", async () => {
    mockApi();
    renderApp(<AgentPage />);
    fireEvent.click(await screen.findByRole("button", { name: "All" }));
    const p = screen.getByRole("listitem", { name: "Proposal 3" });
    expect(within(p).getByText("flat_base-c1 classification.flat_base.max_depth_pct: 15 → 12")).toBeInTheDocument();
  });

  it("compares an active candidate's shadow ledger with the frozen one and says what is missing", () => {
    renderApp(<AgentCandidates items={[ACTIVE]} minTrades={30} minMonths={3} />);
    const t = screen.getByRole("table", { name: "Shadow ledger of vcp-c1" });
    const rows = within(t)
      .getAllByRole("row")
      .map((r) => r.textContent ?? "");
    expect(rows[1]).toMatch(/^Frozen61/);
    expect(rows[2]).toMatch(/^Candidate71/);
    expect(screen.getByText(/Candidate minus frozen: \+0\.90% per trade \(95% range -3\.10% to \+4\.90%\)/)).toBeInTheDocument();
    expect(screen.getByText("Needs 23 more closed shadow trades and 51 more days.")).toBeInTheDocument();
    expect(screen.getByText(/Shadow from/)).toBeInTheDocument();
  });

  it("says nothing exists yet and how a candidate comes about", () => {
    renderApp(<AgentCandidates items={[]} minTrades={30} minMonths={3} />);
    expect(screen.getByText(/No candidate yet/)).toBeInTheDocument();
    expect(fx.agent.candidates).toHaveLength(1);
  });
});
