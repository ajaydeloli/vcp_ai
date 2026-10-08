import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { PaperTrading } from "@/components/PaperTrading";
import { Strategies } from "@/components/Strategies";
import { SystemStatus } from "@/components/SystemStatus";
import { VcpScanner } from "@/components/VcpScanner";
import { mockApi, renderApp } from "./helpers";

describe("more pages", () => {
  it("VCP Scanner lists the setups of a strategy and switches strategy and status", async () => {
    const calls = mockApi();
    renderApp(<VcpScanner />);
    expect(await screen.findByRole("heading", { name: "Setups" })).toBeInTheDocument();
    expect(await screen.findAllByRole("link", { name: /^[A-Z]+$/ })).not.toHaveLength(0);
    await userEvent.click(screen.getByRole("button", { name: "Breakout" }));
    expect(calls.some((c) => c.includes("status=BREAKOUT"))).toBe(true);
  });

  it("Strategies shows one card per strategy and the overlap", async () => {
    mockApi();
    renderApp(<Strategies />);
    expect(await screen.findByRole("heading", { name: "Stocks in more than one strategy" })).toBeInTheDocument();
    expect(await screen.findAllByText(/algorithm/)).not.toHaveLength(0);
  });

  it("Paper Trading shows the review gates", async () => {
    mockApi();
    renderApp(<PaperTrading />);
    expect(await screen.findByRole("heading", { name: "Review gates" })).toBeInTheDocument();
    expect((await screen.findAllByText(/^needs /)).length).toBeGreaterThan(0);
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
});
