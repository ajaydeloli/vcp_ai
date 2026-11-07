import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { PaperTrading } from "@/components/PaperTrading";
import { Strategies } from "@/components/Strategies";
import { SystemStatus } from "@/components/SystemStatus";
import { Backtest } from "@/components/Backtest";
import { RecentIpos } from "@/components/RecentIpos";
import { Reports } from "@/components/Reports";
import { mockApi, renderApp } from "./helpers";

describe("more pages", () => {
  it("Recent IPOs lists the young listings, no subtitle", async () => {
    mockApi();
    renderApp(<RecentIpos />);
    expect(await screen.findByRole("heading", { name: "Recent IPOs", level: 1 })).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: "NEWCO" })).toHaveAttribute("href", "/chart/NEWCO");
    expect(screen.queryByText(/outside the scan universe/i)).not.toBeInTheDocument();
    // a 60-bar stock has no 200-day average: nothing invented
    await userEvent.type(screen.getByRole("spinbutton", { name: "Min bars" }), "100");
    expect(screen.queryByRole("link", { name: "NEWCO" })).not.toBeInTheDocument();
    expect(screen.getByText(/No listing matches/)).toBeInTheDocument();
  });

  it("Strategies shows one card per strategy", async () => {
    mockApi();
    renderApp(<Strategies />);
    expect(await screen.findAllByText(/algorithm/)).not.toHaveLength(0);
  });

  it("Paper Trading shows the review gates", async () => {
    mockApi();
    renderApp(<PaperTrading />);
    expect(await screen.findByRole("heading", { name: "Review gates" })).toBeInTheDocument();
    expect((await screen.findAllByText(/^needs /)).length).toBeGreaterThan(0);
    // the research ledger has its own card, apart from the review gates
    const research = await screen.findByRole("region", { name: "research-sma50-v1" });
    expect(screen.getByRole("heading", { name: "Research ledgers" })).toBeInTheDocument();
    expect(research).toHaveTextContent("No entry more than 10% above the 50-day average");
    expect(research).toHaveTextContent("rule fixed 7 Nov 2026");
    expect(within(research).getByRole("link", { name: "BETA" })).toHaveAttribute("href", "/chart/BETA");
  });

  it("System Status shows the data dates, backup and live feed", async () => {
    mockApi();
    renderApp(<SystemStatus />);
    expect(await screen.findByRole("heading", { name: "Live prices" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Backup" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Recent activity" })).toBeInTheDocument();
  });

  it("an API failure is said plainly", async () => {
    mockApi({ status: new Error("down") });
    renderApp(<SystemStatus />);
    expect((await screen.findAllByRole("alert")).length).toBeGreaterThan(0);
  });

  it("Backtest shows the frozen-rule runs and a missing number as a dash", async () => {
    mockApi();
    renderApp(<Backtest />);
    expect(await screen.findByRole("heading", { name: "Stored runs" })).toBeInTheDocument();
    expect((await screen.findAllByText("paper rules")).length).toBe(2);
    expect(screen.queryByText("breakout · no regime gate · hold_low8")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("checkbox", { name: "Frozen paper rules only" }));
    expect(screen.getByText("breakout · no regime gate · hold_low8")).toBeInTheDocument();
    // a research filter is named in the rules and is not marked as the paper rules
    expect(screen.getByText("cross_5 · breadth50 regime · hold_low8 · max 10% above 50-day")).toBeInTheDocument();
    expect(screen.getAllByText("paper rules")).toHaveLength(2);
    expect(screen.getByText("4 of 4 runs")).toBeInTheDocument();
  });

  it("Reports lists the files and shows the newest daily report", async () => {
    mockApi();
    renderApp(<Reports />);
    expect(await screen.findByRole("button", { name: /Daily report 2026-10-05/ })).toHaveAttribute("aria-current", "true");
    expect(screen.getByRole("button", { name: /Weekly summary 2026-W41/ })).toBeInTheDocument();
    expect(screen.getByTitle("Daily report 2026-10-05")).toHaveAttribute("src", "/api/v1/reports/daily/2026-10-05.html");
  });

  it("Reports can build a past report and then shows it", async () => {
    mockApi();
    renderApp(<Reports />);
    await screen.findByRole("button", { name: /Daily report 2026-10-05/ });
    await userEvent.click(screen.getByRole("button", { name: "Generate" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Written: Daily report 2026-10-01");
    const post = vi.mocked(globalThis.fetch).mock.calls.find(([, init]) => init?.method === "POST");
    expect(String(post?.[0])).toBe("/api/v1/reports/generate");
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ kind: "daily", date: "2026-10-05" });
  });

  it("Reports says why a report could not be made", async () => {
    mockApi({ "reports/generate": new Error("down") });
    renderApp(<Reports />);
    await screen.findByRole("button", { name: /Daily report 2026-10-05/ });
    await userEvent.click(screen.getByRole("button", { name: "Generate" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/not reachable/);
  });
});
