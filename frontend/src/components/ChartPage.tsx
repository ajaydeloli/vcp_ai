"use client";

// The Chart page (owner, 2026-11-07): one stock's chart, full size, with a toolbar (daily,
// weekly or monthly bars; range; chart type; indicators; log scale; screenshot; full screen),
// the watch list and the top VCP setups on the left, and the VCP setup on the right.
import type { IChartApi } from "lightweight-charts";
import { useEffect, useRef, useState } from "react";
import { useBars, useIndexCloses, useLiveQuotes, useRsRanks, useSetups, useStockSetups } from "@/lib/api";
import {
  BENCHMARKS,
  CHART_TYPES,
  DEFAULT_CHART,
  loadChart,
  lastSymbol,
  rememberSymbol,
  saveChart,
  type Benchmark,
  type ChartSettings,
  type ChartType,
  type SidePanel,
  type SideTab,
} from "@/lib/chartSettings";
import { DASH, fmtDay, fmtNum, fmtPct, fmtPrice, sentence, tone } from "@/lib/fmt";
import { CHART_RANGES, type ChartRange, type Interval } from "@/lib/indicators";
import { useWatchlists } from "@/lib/watchlist";
import type { StockSetup, StockSetups } from "@/lib/schemas";
import { ChartLinks } from "./ChartLinks";
import { FundamentalsCard, QuarterlyResultsCard } from "./Fundamentals";
import { asNumber, ContractionTable, ScoreCard, TrendCard, unmetByTier } from "./SetupOverview";
import { ShareholdingCard } from "./Shareholding";
import { HistoryCard } from "./StockExtras";
import { StockNotes } from "./StockNotes";
import { BTN, ColorMenu, IndicatorMenu, toggleBtn } from "./ChartMenus";
import { LiveLine } from "./Live";
import { Nav } from "./Nav";
import { ProChart } from "./ProChart";
import { StatusBar } from "./StatusBar";
import { Card, Empty, ErrorBox, GradeBadge, Loading, StatusPill } from "./ui";
import { WatchStar } from "./WatchStar";

const SELECT = "h-8 rounded border border-line bg-panel2 px-2 text-xs text-ink";

export function ChartPage({ symbol: given }: { symbol: string | null }) {
  const [symbol, setSymbol] = useState<string | null>(given);
  const [settings, setSettings] = useState<ChartSettings>(DEFAULT_CHART);
  const [loaded, setLoaded] = useState(false);
  const top = useSetups("vcp");

  // saved settings and, without a symbol in the address, the last stock seen here
  useEffect(() => {
    setSettings(loadChart());
    if (!given) setSymbol((s) => s ?? lastSymbol());
    setLoaded(true);
  }, [given]);
  useEffect(() => {
    if (loaded) saveChart(settings);
  }, [settings, loaded]);
  const shown = symbol ?? top.data?.rows[0]?.symbol ?? null;
  useEffect(() => {
    if (shown) rememberSymbol(shown);
  }, [shown]);

  const pick = (s: string) => {
    const sym = s.toUpperCase();
    setSymbol(sym);
    try {
      window.history.pushState(null, "", `/chart/${encodeURIComponent(sym)}`);
    } catch {
      /* no history (tests) */
    }
  };
  const change = (p: Partial<ChartSettings>) => setSettings((s) => ({ ...s, ...p }));

  return (
    <div className="flex min-h-screen flex-col">
      <Nav active="Chart" onPick={pick} />
      <main className="grid min-w-0 flex-1 gap-3 p-3 pb-14 lg:grid-cols-[minmax(0,1fr)_auto]">
        <h1 className="sr-only">Chart{shown ? `: ${shown}` : ""}</h1>
        {shown === null ? (
          <Card title="Chart">
            <Empty>{top.isPending ? "Loading the best VCP setup…" : "Search a symbol or company above to chart it."}</Empty>
          </Card>
        ) : (
          <ChartArea key={shown} symbol={shown} settings={settings} onChange={change} />
        )}
        <SideBar
          symbol={shown}
          mode={settings.sidePanel}
          onMode={(sidePanel) => change({ sidePanel })}
          dock={settings.sideDock}
          onDock={(sideDock) => change({ sideDock })}
          lists={<SideLists current={shown} onPick={pick} top={top.data?.rows ?? []} />}
        />
      </main>
      <StatusBar />
    </div>
  );
}

function ChartArea({
  symbol,
  settings,
  onChange,
}: {
  symbol: string;
  settings: ChartSettings;
  onChange: (p: Partial<ChartSettings>) => void;
}) {
  const bars = useBars(symbol);
  const stock = useStockSetups(symbol);
  const rsOn = settings.toggles.includes("rs");
  const bench = useIndexCloses(settings.benchmark, rsOn);
  const live = useLiveQuotes([symbol]);
  const ranks = useRsRanks(symbol, rsOn);
  const api = useRef<IChartApi | null>(null);
  const wrap = useRef<HTMLElement>(null);
  const setup = vcpSetup(stock.data);
  const list = bars.data?.bars ?? [];
  const last = list[list.length - 1];
  const prev = list[list.length - 2];
  const pct = last?.close != null && prev?.close != null && prev.close !== 0 ? (last.close / prev.close - 1) * 100 : null;
  const company = bars.data?.company ?? stock.data?.company ?? null;

  const screenshot = () => {
    const canvas = api.current?.takeScreenshot();
    if (!canvas) return;
    const a = document.createElement("a");
    a.href = canvas.toDataURL("image/png");
    a.download = `${symbol}_${settings.interval}_${last?.day ?? "chart"}.png`;
    a.click();
  };
  const fullScreen = () => {
    const el = wrap.current;
    if (!el) return;
    if (document.fullscreenElement) void document.exitFullscreen?.();
    else void el.requestFullscreen?.();
  };

  return (
    <section
      ref={wrap}
      aria-label={`Chart of ${symbol}`}
      className="flex min-w-0 flex-col rounded-lg border border-line bg-panel p-3 [&:fullscreen]:rounded-none [&:fullscreen]:p-4"
    >
      <div className="mb-2 flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="text-xl font-semibold text-ink">{symbol}</span>
        <WatchStar symbol={symbol} />
        {company ? <span className="text-sm text-mute">{company}</span> : null}
        <span className="text-base font-semibold tabular-nums text-ink">{fmtPrice(last?.close)}</span>
        <span className={`text-sm tabular-nums ${tone(pct)}`}>{fmtPct(pct, 2, true)}</span>
        {last ? <span className="text-xs text-mute">close {last.day}, adjusted prices</span> : null}
        <LiveLine q={live.bySymbol.get(symbol.toUpperCase())} />
        <span className="ml-auto flex items-center gap-3">
          <ChartLinks symbol={symbol} />
        </span>
      </div>
      <Toolbar settings={settings} onChange={onChange} onScreenshot={screenshot} onFullScreen={fullScreen} />
      <div className="mt-2 h-[calc(100vh-15rem)] min-h-[480px] [:fullscreen_&]:h-[calc(100vh-7rem)]">
        {bars.isError ? (
          <ErrorBox error={bars.error} />
        ) : bars.isPending ? (
          <Loading what="chart" />
        ) : list.length === 0 ? (
          <Empty>No price bars for {symbol}.</Empty>
        ) : (
          <ProChart
            symbol={symbol}
            bars={list}
            setup={setup}
            settings={settings}
            benchmark={rsOn ? (bench.data?.closes ?? null) : null}
            rsRanks={rsOn ? (ranks.data?.ranks ?? null) : null}
            apiRef={api}
          />
        )}
      </div>
    </section>
  );
}

/** The stock's VCP setup (the chart's marks and the right panel). */
const vcpSetup = (d: StockSetups | undefined): StockSetup | null =>
  d?.setups.find((s) => s.strategy_id === "vcp") ?? null;

const INTERVALS: { id: Interval; label: string; title: string }[] = [
  { id: "D", label: "D", title: "Daily" },
  { id: "W", label: "W", title: "Weekly" },
  { id: "M", label: "M", title: "Monthly" },
];

function Toolbar({
  settings,
  onChange,
  onScreenshot,
  onFullScreen,
}: {
  settings: ChartSettings;
  onChange: (p: Partial<ChartSettings>) => void;
  onScreenshot: () => void;
  onFullScreen: () => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-line pb-2" role="toolbar" aria-label="Chart settings">
      <div className="flex gap-1" role="group" aria-label="Interval">
        {INTERVALS.map((v) => (
          <button
            key={v.id}
            type="button"
            title={v.title}
            aria-label={v.title}
            aria-pressed={settings.interval === v.id}
            onClick={() => onChange({ interval: v.id })}
            className={`${toggleBtn(settings.interval === v.id)} w-8 justify-center`}
          >
            {v.label}
          </button>
        ))}
      </div>
      <label className="flex items-center gap-1.5 text-xs text-mute">
        Range
        <select
          aria-label="Range"
          value={settings.range}
          onChange={(e) => onChange({ range: e.target.value as ChartRange })}
          className={SELECT}
        >
          {CHART_RANGES.map((r) => (
            <option key={r.id} value={r.id}>
              {r.label}
            </option>
          ))}
        </select>
      </label>
      <label className="flex items-center gap-1.5 text-xs text-mute">
        Type
        <select
          aria-label="Chart type"
          value={settings.type}
          onChange={(e) => onChange({ type: e.target.value as ChartType })}
          className={SELECT}
        >
          {CHART_TYPES.map((t) => (
            <option key={t.id} value={t.id}>
              {t.label}
            </option>
          ))}
        </select>
      </label>
      <IndicatorMenu settings={settings} onChange={onChange} />
      <ColorMenu settings={settings} onChange={onChange} />
      {settings.toggles.includes("rs") ? (
        <label className="flex items-center gap-1.5 text-xs text-mute" title="The RS line divides the stock's close by this index">
          RS vs
          <select
            aria-label="RS benchmark"
            value={settings.benchmark}
            onChange={(e) => onChange({ benchmark: e.target.value as Benchmark })}
            className={SELECT}
          >
            {BENCHMARKS.map((b) => (
              <option key={b} value={b}>
                {b}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      <button
        type="button"
        aria-pressed={settings.log}
        onClick={() => onChange({ log: !settings.log })}
        title="Logarithmic price scale"
        className={toggleBtn(settings.log)}
      >
        Log
      </button>
      <span className="ml-auto flex gap-1">
        <button type="button" onClick={onScreenshot} className={BTN} title="Save the chart as a PNG image">
          Screenshot
        </button>
        <button type="button" onClick={onFullScreen} className={BTN} title="Full screen (Esc to leave)">
          Full screen
        </button>
      </span>
    </div>
  );
}

type TopRow = { symbol: string; score: number | null; status: string };

const OPEN_KEY = "vcp-chart-lists-open";
const TOP_ID = "top";

function readOpen(): string[] | null {
  try {
    const v = JSON.parse(window.localStorage.getItem(OPEN_KEY) ?? "null") as unknown;
    return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : null;
  } catch {
    return null;
  }
}

/** The right panel: every watch list and the top VCP setups, each a section that opens and
 *  closes (which ones are open is kept in this browser; at first the active list and the setups). */
function SideLists({ current, onPick, top }: { current: string | null; onPick: (s: string) => void; top: TopRow[] }) {
  const { lists, active } = useWatchlists();
  const [open, setOpen] = useState<string[] | null>(null);
  useEffect(() => setOpen(readOpen()), []);
  const shown = open ?? [active.id, TOP_ID];
  const toggle = (id: string) => {
    const next = shown.includes(id) ? shown.filter((x) => x !== id) : [...shown, id];
    setOpen(next);
    try {
      window.localStorage.setItem(OPEN_KEY, JSON.stringify(next));
    } catch {
      /* storage blocked: kept until the page is reloaded */
    }
  };
  const item = (sym: string, right: React.ReactNode) => (
    <li key={sym}>
      <button
        type="button"
        onClick={() => onPick(sym)}
        aria-current={sym === current ? "true" : undefined}
        className={`flex w-full items-center justify-between rounded px-2 py-1 text-left text-xs ${
          sym === current ? "bg-accent/15 font-semibold text-ink" : "text-ink hover:bg-panel2"
        }`}
      >
        <span className="truncate">{sym}</span>
        {right}
      </button>
    </li>
  );
  const section = (id: string, title: string, count: number, body: React.ReactNode) => {
    const isOpen = shown.includes(id);
    return (
      <section key={id} className="rounded-lg border border-line bg-panel">
        <h2>
          <button
            type="button"
            aria-expanded={isOpen}
            onClick={() => toggle(id)}
            className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs font-semibold text-ink hover:text-accent"
          >
            <span aria-hidden="true" className={`inline-block text-mute transition-transform ${isOpen ? "rotate-90" : ""}`}>
              ›
            </span>
            <span className="min-w-0 flex-1 truncate">{title}</span>
            <span className="font-normal tabular-nums text-mute">{count}</span>
          </button>
        </h2>
        {isOpen ? <div className="border-t border-line p-1.5">{body}</div> : null}
      </section>
    );
  };
  return (
    <aside aria-label="Stocks to chart" className="min-w-0 space-y-2">
      {lists.map((l) =>
        section(
          l.id,
          l.name,
          l.symbols.length,
          l.symbols.length === 0 ? (
            <p className="px-2 py-1 text-[11px] text-mute">Star a stock to add it here.</p>
          ) : (
            <ul className="max-h-64 overflow-y-auto">{l.symbols.map((sym) => item(sym, null))}</ul>
          ),
        ),
      )}
      {section(
        TOP_ID,
        "Top VCP setups",
        top.length,
        top.length === 0 ? (
          <p className="px-2 py-1 text-[11px] text-mute">No VCP setups on the latest scan.</p>
        ) : (
          <ul className="max-h-[24rem] overflow-y-auto">
            {top.map((r) => item(r.symbol, <span className="tabular-nums text-mute">{fmtNum(r.score, 0)}</span>))}
          </ul>
        ),
      )}
    </aside>
  );
}

function Row({ label, value, hint }: { label: string; value: React.ReactNode; hint?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1.5" title={hint}>
      <dt className="text-mute">{label}</dt>
      <dd className="text-right tabular-nums text-ink">{value}</dd>
    </div>
  );
}

/** The right sidebar: a strip of tabs (VCP, Watch lists) and one panel at a time. A tab
 *  opens its panel and closes the other; the open tab again folds the panel away. At first
 *  (auto) the VCP analysis is open when the stock has a setup, else the watch lists. */
function SideBar({
  symbol,
  mode,
  onMode,
  dock,
  onDock,
  lists,
}: {
  symbol: string | null;
  mode: SidePanel;
  onMode: (m: SidePanel) => void;
  dock: boolean;
  onDock: (d: boolean) => void;
  lists: React.ReactNode;
}) {
  const stock = useStockSetups(symbol);
  const has = vcpSetup(stock.data) !== null;
  // while the setup is loading, auto keeps the VCP tab (no flash of the watch lists)
  const loading = symbol !== null && stock.isPending;
  const open: SidePanel = mode === "auto" ? (has || loading ? "vcp" : "lists") : mode;
  const tabs: { id: SideTab; label: string; title: string }[] = [
    { id: "vcp", label: has || loading || !symbol ? "VCP" : "VCP (none)", title: has ? "VCP setup and pattern" : `No VCP setup for ${symbol ?? "this stock"} on the latest scan` },
    { id: "trend", label: "Trend", title: "Trend Template conditions and stage" },
    { id: "score", label: "Score", title: "Score breakdown" },
    { id: "fundamentals", label: "Fundamentals", title: "Fundamentals, quarterly results and shareholding" },
    { id: "notes", label: "Notes", title: "My notes on this stock" },
    { id: "history", label: "History", title: "History and paper trades" },
    { id: "lists", label: "Watch lists", title: "Watch lists and top VCP setups" },
  ];
  const floating = !dock && open !== "none";
  // a floating panel closes with Escape or a click anywhere outside the sidebar (a pinned one
  // stays); clicks on the tabs are inside, so they still switch panels
  const wrap = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!floating) return;
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !document.querySelector("[role=dialog]")) onMode("none");
    };
    const away = (e: MouseEvent) => {
      if (wrap.current && !wrap.current.contains(e.target as Node)) onMode("none");
    };
    document.addEventListener("keydown", key);
    document.addEventListener("mousedown", away);
    return () => {
      document.removeEventListener("keydown", key);
      document.removeEventListener("mousedown", away);
    };
  }, [floating, onMode]);
  const body = open === "lists" ? lists : symbol && open !== "none" ? <TabBody tab={open as SideTab} symbol={symbol} bare={floating} /> : null;
  // how wide a floating panel may grow: tables and charts need more room than lists
  // Fundamentals: as wide as its tables need, so the quarterly results and shareholding do not
  // scroll sideways (their own scroll boxes are opened up); never wider than the screen
  const maxWide =
    open === "fundamentals"
      ? "lg:max-w-[calc(100vw-6rem)] [&_.overflow-x-auto]:overflow-x-visible"
      : open === "history"
        ? "lg:max-w-[44rem]"
        : "lg:max-w-[26rem]";
  const width = open === "fundamentals" || open === "history" ? "lg:w-[30rem]" : open === "lists" ? "lg:w-[13rem]" : "lg:w-[18rem]";
  return (
    <div ref={wrap} className="relative flex min-w-0 flex-col-reverse gap-2 lg:flex-row lg:items-start">
      {body ? (
        <div
          id="side-panel"
          data-testid="side-panel"
          data-floating={floating ? "true" : "false"}
          // floating: the full height of the chart card (top to bottom), as wide as its
          // content needs (13 to 26 rem); pinned: a fixed width beside the chart
          className={`min-w-0 ${
            floating
              ? `lg:absolute lg:bottom-0 lg:right-full lg:top-0 lg:z-30 lg:mr-2 lg:flex lg:w-max lg:min-w-[13rem] ${maxWide} lg:flex-col lg:rounded-lg lg:border lg:border-line lg:bg-panel lg:p-2 lg:shadow-2xl lg:shadow-black/60`
              : width
          }`}
        >
          <div className="hidden h-5 justify-end lg:flex">
            <button
              type="button"
              aria-pressed={dock}
              aria-label={dock ? "Unpin the panel" : "Pin the panel"}
              onClick={() => onDock(!dock)}
              title={dock ? "Pinned beside the chart. Click to float it over the chart again" : "Pin beside the chart (the chart gets narrower)"}
              className={`flex h-5 w-5 items-center justify-center rounded ${dock ? "bg-accent text-white" : "text-mute hover:bg-panel2 hover:text-ink"}`}
            >
              <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true" fill={dock ? "currentColor" : "none"} stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round">
                <path d="M10.5 1.5l4 4-2 1-2.5 2.5.5 3-1.5 1.5-2.5-2.5L3 14.5l-1-1 3.5-3.5L3 7.5 4.5 6l3 .5L10 4z" />
              </svg>
            </button>
          </div>
          <div className={floating ? "lg:min-h-0 lg:flex-1 lg:overflow-y-auto" : ""}>{body}</div>
        </div>
      ) : null}
      <div role="group" aria-label="Side panels" className="flex gap-1 lg:sticky lg:top-3 lg:flex-col">
        {tabs.map((t) => {
          const on = open === t.id;
          return (
            <button
              key={t.id}
              type="button"
              aria-pressed={on}
              aria-controls={on ? "side-panel" : undefined}
              title={on ? `Hide: ${t.title}` : t.title}
              onClick={() => onMode(on ? "none" : t.id)}
              className={`rounded-lg border px-2 py-1.5 text-[11px] lg:w-7 lg:px-1 lg:py-3 ${
                on ? "border-accent bg-accent font-semibold text-white" : "border-line bg-panel text-mute hover:border-accent hover:text-ink"
              }`}
            >
              <span className="lg:[writing-mode:vertical-rl]">{t.label}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

/** The cards in the sidebar look plain, like the VCP and watch-list panels: no coloured
 *  border or tinted background. Floating, the panel
 *  has the border, so the cards have none; pinned, each card has the plain line border. */
const PLAIN_FLOATING = "[&_section]:!border-0 [&_section]:!bg-transparent [&_section]:!bg-none";
const PLAIN_PINNED = "[&_section]:!border [&_section]:!border-line [&_section]:!bg-panel [&_section]:!bg-none";

/** One sidebar tab's content for the stock. */
function TabBody({ tab, symbol, bare }: { tab: SideTab; symbol: string; bare: boolean }) {
  return (
    <div data-testid="tab-body" className={bare ? PLAIN_FLOATING : PLAIN_PINNED}>
      <TabCards tab={tab} symbol={symbol} bare={bare} />
    </div>
  );
}

function TabCards({ tab, symbol, bare }: { tab: SideTab; symbol: string; bare: boolean }) {
  const stock = useStockSetups(symbol);
  if (tab === "vcp") return <SetupSide symbol={symbol} bare={bare} />;
  if (tab === "notes") return <StockNotes symbol={symbol} />;
  if (tab === "history") return <HistoryCard symbol={symbol} />;
  if (tab === "fundamentals") {
    return (
      <div className="space-y-3">
        <FundamentalsCard symbol={symbol} />
        {bare ? <hr className="border-line" /> : null}
        <QuarterlyResultsCard symbol={symbol} />
        {bare ? <hr className="border-line" /> : null}
        <ShareholdingCard symbol={symbol} />
      </div>
    );
  }
  if (stock.isError) return <ErrorBox error={stock.error} />;
  if (stock.isPending) return <Loading what="setup" />;
  return tab === "trend" ? <TrendCard stock={stock.data} /> : <ScoreCard setup={vcpSetup(stock.data)} />;
}

/** The VCP setup and its pattern in one panel: grade, status, the key levels, the base, the contractions and the rules a higher
 *  grade still misses. ``bare``: no card border of its own (inside the floating panel). */
function SetupSide({ symbol, bare = false }: { symbol: string; bare?: boolean }) {
  const stock = useStockSetups(symbol);
  const s = vcpSetup(stock.data);
  const d = stock.data;
  const close = d?.close ?? null;
  const toPivot = s?.pivot != null && close != null ? (close / s.pivot - 1) * 100 : null;
  const risk = s?.pivot != null && s.stop != null ? (s.stop / s.pivot - 1) * 100 : null;
  const det = s?.details ?? {};
  const baseEnd = s?.base_end ?? d?.as_of ?? null;
  const baseDays = s?.base_start && baseEnd ? Math.round((Date.parse(baseEnd) - Date.parse(s.base_start)) / 86_400_000) : null;
  const unmet = s ? unmetByTier(s.details) : [];
  return (
    <aside aria-label={`VCP setup of ${symbol}`} className="min-w-0">
      <Card title="VCP setup" subtitle={d ? `Scan of ${d.as_of ?? DASH}` : undefined} box={bare ? "bg-transparent" : undefined}>
        {stock.isError ? (
          <ErrorBox error={stock.error} />
        ) : stock.isPending ? (
          <Loading what="setup" />
        ) : !s ? (
          <Empty>No VCP setup for {symbol} on the latest scan.</Empty>
        ) : (
          <>
            <div className="mb-2 flex flex-wrap items-center gap-2 text-base">
              <GradeBadge classification={s.classification} grade={s.grade} />
              <StatusPill status={s.status} />
            </div>
            <dl className="divide-y divide-line text-xs">
              <Row label="Score" value={fmtNum(s.score, 0)} />
              <Row label="Pivot" value={fmtPrice(s.pivot)} />
              <Row label="Close" value={fmtPrice(close)} />
              <Row label="Close vs pivot" value={<span className={tone(toPivot)}>{fmtPct(toPivot, 1, true)}</span>} />
              <Row label="Stop" value={`${fmtPrice(s.stop)} (${fmtPct(risk, 1, true)})`} hint="Stop level, and how far below the pivot" />
              <Row label="Base depth" value={fmtPct(s.base_depth_pct, 1)} />
              <Row label="Base length" value={baseDays === null ? DASH : `${Math.round(baseDays / 7)} weeks (${baseDays} days)`} />
              <Row label="Base start" value={fmtDay(s.base_start)} />
              <Row label="Base high / low" value={`${fmtPrice(asNumber(det["base_high"]))} / ${fmtPrice(asNumber(det["base_low"]))}`} />
              <Row label="Prior advance" value={fmtPct(asNumber(det["prior_advance_pct"]))} />
              <Row label="Volume dry-up" value={fmtNum(asNumber(det["dryup_volume_ratio"]), 2)} hint="Volume in the last contraction against the 50-day average" />
              <Row label="Breakout" value={s.breakout_date ? fmtDay(s.breakout_date) : DASH} />
              <Row label="Weekly stage" value={d?.weekly_stage ? sentence(d.weekly_stage) : DASH} />
              <Row
                label="Trend Template"
                value={d?.trend_template_pass == null ? DASH : d.trend_template_pass ? <span className="text-up">Pass</span> : <span className="text-down">Fail</span>}
              />
            </dl>
            <h3 className="mb-1 mt-3 text-[11px] uppercase tracking-wide text-mute">Contractions</h3>
            <ContractionTable setup={s} />
            {unmet.map(([tier, rules]) => (
              <p key={tier} className="mt-1 text-xs text-warn">
                Not {sentence(tier)}: {rules.map(sentence).join(", ")}
              </p>
            ))}
          </>
        )}
      </Card>
    </aside>
  );
}
