// Fundamentals (F5): the stock page's quarterly results card and the Fundamentals page. Display
// only; the samples come from the real API (tests/api/test_contract_samples.py).
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { QuarterlyResultsCard } from "@/components/Fundamentals";
import { FundamentalsPage } from "@/components/FundamentalsPage";
import { Screener } from "@/components/Screener";
import { fmtCrore, gateText, quarterLabel } from "@/lib/fundamentals";
import { mockApi, renderApp } from "./helpers";

describe("fundamentals helpers", () => {
  it("formats crores with Indian grouping and never shows a missing value as 0", () => {
    expect(fmtCrore(3118500000000)).toBe("₹3,11,850 cr");
    expect(fmtCrore(125e7)).toBe("₹125 cr");
    expect(fmtCrore(5.5e7)).toBe("₹5.50 cr");
    expect(fmtCrore(null)).toBe("—");
    expect(quarterLabel("2026-06-30")).toBe("Jun 26");
    expect(gateText("INSUFFICIENT_DATA;NEGATIVE_TTM_EPS")).toBe(
      "fewer than half of the metrics available, negative trailing-twelve-month EPS",
    );
  });
});

describe("quarterly results card", () => {
  it("lists the quarters newest first with publication dates, estimated ones marked", async () => {
    mockApi();
    renderApp(<QuarterlyResultsCard symbol="ALPHA" />);
    const card = (await screen.findByText("Quarterly results")).closest("section")!;
    const table = await within(card).findByRole("table");
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((r) => within(r).getAllByRole("rowheader")[0]?.textContent)).toEqual([
      "Jun 26", "Mar 26est.", "Dec 25", "Sep 25", "Jun 25",
    ]); // fmt: skip
    expect(rows[0]).toHaveTextContent("Jun 2620 Jul 2026₹125 cr+25.0%3.00+50.0%16.0%");
    // the first quarter has no year-ago quarter: dashes, not 0
    expect(rows[4]).toHaveTextContent("Jun 2520 Jul 2025₹100 cr—2.00—");
    expect(within(card).getByRole("img", { name: /Quarterly EPS, 5 quarters, latest 3.00/ })).toBeInTheDocument();
    expect(card).toHaveTextContent("do not change the setup score or the paper ledger");
  });

  it("a stock without results shows the reason", async () => {
    mockApi();
    renderApp(<QuarterlyResultsCard symbol="GAMMA" />);
    const card = (await screen.findByText("Quarterly results")).closest("section")!;
    await waitFor(() => expect(card).toHaveTextContent("No results filed yet"));
    expect(within(card).queryByRole("table")).toBeNull();
  });
});

describe("fundamentals page", () => {
  it("lists the universe's stocks with period end and publication date as separate columns", async () => {
    mockApi();
    renderApp(<FundamentalsPage />);
    const link = await screen.findByRole("link", { name: "ALPHA" });
    expect(link).toHaveAttribute("href", "/stocks/ALPHA");
    expect(screen.getByText(/2 of 4 universe stocks have results filed/)).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).getByRole("link", { name: "Fundamentals" })).toHaveAttribute("aria-current", "page");
    for (const name of ["Quarter", "Published", "EPS YoY", "Sales YoY", "ROE", "D/E"]) {
      expect(screen.getByRole("columnheader", { name: new RegExp(`^${name}`) })).toBeInTheDocument();
    }
    const alpha = link.closest("tr")!;
    expect(alpha).toHaveTextContent("Jun 26");
    expect(alpha).toHaveTextContent("20 Jul 2026");
    expect(alpha).toHaveTextContent("+50.0%");
    expect(alpha).toHaveTextContent("6/7");
    // BETA has a single quarter: no metric can be computed, every figure is a dash
    const beta = screen.getByRole("link", { name: "BETA" }).closest("tr")!;
    expect(beta).toHaveTextContent("0/7");
    expect(beta).not.toHaveTextContent("0.0%");
  });

  it("sorting and the filters reach the API", async () => {
    const calls = mockApi();
    renderApp(<FundamentalsPage />);
    await screen.findByRole("link", { name: "ALPHA" });
    expect(calls.some((c) => c.startsWith("fundamentals?") && c.includes("sort=eps_yoy") && c.includes("direction=desc"))).toBe(true);
    await userEvent.click(screen.getByRole("button", { name: /^Sales YoY/ }));
    await waitFor(() => expect(calls.some((c) => c.includes("sort=sales_yoy"))).toBe(true));
    await userEvent.type(screen.getByLabelText("Min EPS growth YoY %"), "25");
    await userEvent.selectOptions(screen.getByLabelText("Sector"), "Capital Goods");
    await userEvent.click(screen.getByRole("checkbox", { name: "EPS accelerating" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Current data only" }));
    await waitFor(() =>
      expect(
        calls.some(
          (c) =>
            c.includes("min_eps_yoy=25") &&
            c.includes("sector=Capital%20Goods") &&
            c.includes("accelerating=true") &&
            c.includes("current_only=true"),
        ),
      ).toBe(true),
    );
    await userEvent.click(screen.getByRole("button", { name: "Reset filters" }));
    expect(screen.getByLabelText("Min EPS growth YoY %")).toHaveValue(null);
  });
});

describe("screener fundamentals", () => {
  it("the Fundamentals box shows EPS and sales growth columns; the minimums reach the API", async () => {
    const calls = mockApi();
    renderApp(<Screener />);
    await screen.findByRole("link", { name: "ALPHA" });
    expect(screen.queryByRole("columnheader", { name: /^EPS YoY/ })).toBeNull();
    await userEvent.click(screen.getByRole("checkbox", { name: "Fundamentals" }));
    expect(screen.getByRole("columnheader", { name: /^EPS YoY/ })).toBeInTheDocument();
    const alpha = screen.getByRole("link", { name: "ALPHA" }).closest("tr")!;
    expect(alpha).toHaveTextContent("+50.0%");
    expect(alpha).toHaveTextContent("+25.0%");
    await userEvent.click(screen.getByRole("button", { name: "More" }));
    await userEvent.type(screen.getByLabelText("Min EPS growth YoY %"), "20");
    await userEvent.type(screen.getByLabelText("Min sales growth YoY %"), "10");
    await waitFor(() => expect(calls.some((c) => c.includes("min_eps_yoy=20") && c.includes("min_sales_yoy=10"))).toBe(true));
  });
});
