import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { LiveNotice } from "@/components/Live";
import { Screener } from "@/components/Screener";
import { StatusBar } from "@/components/StatusBar";
import { StockPanel } from "@/components/StockPanel";
import { liveEvery } from "@/lib/api";
import type { LiveFeed } from "@/lib/schemas";
import { feedNotice, feedState, fmtAge, liveLabel } from "@/lib/live";
import { fx, mockApi, renderApp } from "./helpers";

const tokenDown = () => {
  const s = structuredClone(fx.liveStatus);
  s.feed = { ...s.feed, state: "token_needed", message: "Live feed needs a fresh token" };
  return s;
};

describe("live labels", () => {
  it("name the age of a live value and the session of a close", () => {
    expect(liveLabel({ mode: "live", delay_seconds: 12, session_date: "2026-10-06" })).toBe("Live, delayed 12 s");
    expect(liveLabel({ mode: "live", delay_seconds: 300, session_date: "2026-10-06" })).toBe("Live, delayed 5 min");
    expect(liveLabel({ mode: "stale", delay_seconds: 200, session_date: "2026-10-06" })).toBe("Stale, 3 min old");
    expect(liveLabel({ mode: "closed", delay_seconds: null, session_date: "2026-10-06" })).toMatch(/^Close of .*2026/);
    expect(liveLabel({ mode: null, delay_seconds: null, session_date: null })).toBeNull();
    expect(fmtAge(5)).toBe("5 s");
  });

  it("feed notices say what to do and are silent when all is well", () => {
    expect(feedNotice(fx.liveStatus.feed as LiveFeed)).toBeNull();
    expect(feedNotice(tokenDown().feed as LiveFeed)).toContain("fresh token");
    expect(feedNotice(undefined)).toBeNull();
    expect(feedState(tokenDown().feed as LiveFeed)).toBe("Needs a fresh token");
    expect(feedState(undefined)).toBe("Loading");
  });

  it("polls every 15 s while live and every 30 s when nothing has loaded", () => {
    expect(liveEvery(fx.liveIndices)).toBe(15_000);
    expect(liveEvery(undefined)).toBe(30_000);
  });
});

describe("live prices on the pages", () => {
  it("the screener shows the live price next to the stored close and a dash when it has none", async () => {
    mockApi();
    renderApp(<Screener />);
    await waitFor(() => expect(screen.getByTestId("live-ALPHA")).toHaveTextContent("102"));
    expect(screen.getByTestId("live-ALPHA")).toHaveTextContent("+2.00%");
    expect(screen.getByRole("columnheader", { name: "Close" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Live" })).toBeInTheDocument();
  });

  it("the live column is not sortable and the stored values stay as they were", async () => {
    mockApi();
    renderApp(<Screener />);
    await screen.findByRole("link", { name: "ALPHA" });
    expect(screen.queryByRole("button", { name: /^Live/ })).toBeNull();
  });

  it("the stock page shows the live line beside the stored close", async () => {
    mockApi();
    renderApp(<StockPanel selection={{ symbol: "ALPHA", strategy: "vcp" }} onSelect={() => {}} onStockPage />);
    await waitFor(() => expect(screen.getByTestId("live-line")).toHaveTextContent("102"));
    expect(screen.getByTestId("live-line")).toHaveTextContent("Live, delayed 0 s");
    expect(screen.getByText(/adjusted prices/)).toBeInTheDocument();
  });

  it("a stock without a live price says not available", async () => {
    mockApi();
    renderApp(<StockPanel selection={{ symbol: "GAMMA", strategy: "vcp" }} onSelect={() => {}} onStockPage />);
    await waitFor(() => expect(screen.getByTestId("live-line")).toHaveTextContent(/not available/i));
  });

  it("with no token the banner says so and the status bar shows the feed state", async () => {
    mockApi({ "live/status": tokenDown() });
    renderApp(
      <>
        <LiveNotice />
        <StatusBar />
      </>,
    );
    expect(await screen.findByTestId("live-notice")).toHaveTextContent("fresh token");
    expect(await screen.findByText("Needs a fresh token")).toBeInTheDocument();
  });

  it("the banner is not shown when the feed is live", async () => {
    mockApi();
    renderApp(<LiveNotice />);
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByTestId("live-notice")).toBeNull();
  });
});
