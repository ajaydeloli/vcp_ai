// Shareholding pattern (F8): the Fundamentals card block, the stock page's card and the
// Screener columns. Display only; the samples come from the real API
// (tests/api/test_contract_samples.py; fixture ``add_shareholding``).
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { Screener } from "@/components/Screener";
import { SetupOverview } from "@/components/SetupOverview";
import { ShareholdingCard } from "@/components/Shareholding";
import { fmtPp } from "@/lib/fundamentals";
import { mockApi, renderApp } from "./helpers";

describe("shareholding", () => {
  it("formats changes in percentage points", () => {
    expect(fmtPp(1, 2)).toBe("+1.00 pp");
    expect(fmtPp(-0.5, 2)).toBe("-0.50 pp");
    expect(fmtPp(null, 2)).toBe("—");
  });

  it("the Fundamentals card shows the latest split with 1Q and 4Q changes and flags a rising pledge", async () => {
    mockApi();
    renderApp(<SetupOverview selection={{ symbol: "ALPHA", strategy: "vcp" }} />);
    const card = (await screen.findByText("Fundamentals")).closest("section")!;
    await waitFor(() => expect(card).toHaveTextContent("Shareholding · Jun 26 (revised)"));
    expect(card).toHaveTextContent("Promoter48.50%-0.50 pp-1.50 pp");
    expect(card).toHaveTextContent("FII23.00%+1.00 pp+2.00 pp");
    expect(card).toHaveTextContent("DII12.50%+0.50 pp+2.50 pp");
    expect(card).toHaveTextContent("Pledged3.00%▲ rising+2.00 pp—");
    // a rising pledge is shown in the down colour
    const pledge = within(card).getByRole("rowheader", { name: "Pledged" }).closest("tr")!;
    expect(within(pledge).getByText("+2.00 pp")).toHaveClass("text-down");
  });

  it("the stock page card lists quarter ends newest first, revisions marked", async () => {
    mockApi();
    renderApp(<ShareholdingCard symbol="ALPHA" />);
    const card = (await screen.findByText("Shareholding pattern")).closest("section")!;
    const table = await within(card).findByRole("table");
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((r) => within(r).getAllByRole("rowheader")[0]?.textContent)).toEqual([
      "Jun 26rev.", "Mar 26", "Sep 25", "Jun 25", "Mar 25",
    ]); // fmt: skip
    expect(rows[0]).toHaveTextContent("1 Aug 202648.50%3.00%23.00%12.50%6.25%16.00%1,01,000");
    expect(within(card).getByRole("img", { name: /Shareholding by quarter, 5 quarters; latest promoter 48.5%/ })).toBeInTheDocument();
    expect(card).toHaveTextContent("▲ rising");
    expect(card).toHaveTextContent("not part of any setup score");
  });

  it("a stock without filings says so", async () => {
    mockApi();
    renderApp(<ShareholdingCard symbol="GAMMA" />);
    const card = (await screen.findByText("Shareholding pattern")).closest("section")!;
    await waitFor(() => expect(card).toHaveTextContent("No shareholding pattern filed yet"));
    expect(within(card).queryByRole("table")).toBeNull();
  });

  it("the Screener's Fundamentals box adds promoter, FII and DII change and pledge; the filters reach the API", async () => {
    const calls = mockApi();
    renderApp(<Screener />);
    await screen.findByRole("link", { name: "ALPHA" });
    expect(screen.queryByRole("columnheader", { name: /^Promoter/ })).toBeNull();
    await userEvent.click(screen.getByRole("checkbox", { name: "Fundamentals" }));
    for (const name of ["Promoter", "FII chg", "DII chg", "Pledge"]) {
      expect(screen.getByRole("columnheader", { name: new RegExp(`^${name}`) })).toBeInTheDocument();
    }
    const alpha = screen.getByRole("link", { name: "ALPHA" }).closest("tr")!;
    expect(alpha).toHaveTextContent("48.5%");
    expect(alpha).toHaveTextContent("+1.00 pp");
    expect(alpha).toHaveTextContent("+0.50 pp");
    expect(alpha).toHaveTextContent("3.0% ▲");
    await userEvent.click(screen.getByRole("button", { name: "More" }));
    await userEvent.type(screen.getByLabelText("Min promoter %"), "40");
    await userEvent.type(screen.getByLabelText("Max pledge %"), "5");
    await waitFor(() => expect(calls.some((c) => c.includes("min_promoter_pct=40") && c.includes("max_pledged_pct=5"))).toBe(true));
  });
});
