"use client";

// The Chart page (owner, 2026-11-07): one stock's chart, full size, with a toolbar (daily,
// weekly or monthly bars; range; chart type; indicators; log scale; screenshot; full screen),
// the watch list and the top VCP setups on the left, and the VCP setup on the right.
import type { IChartApi } from "lightweight-charts";
import { useEffect, useRef, useState } from "react";
import { useBars, useIndexCloses, useRsRanks, useSetups, useStockSetups } from "@/lib/api";
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
  type VcpPanel,
} from "@/lib/chartSettings";
import { DASH, fmtNum, fmtPct, fmtPrice, sentence, tone } from "@/lib/fmt";
import { CHART_RANGES, type ChartRange, type Interval } from "@/lib/indicators";
import { useWatchlists } from "@/lib/watchlist";
import type { StockSetup, StockSetups } from "@/lib/schemas";
import { ChartLinks } from "./ChartLinks";
import { BTN, ColorMenu, IndicatorMenu } from "./ChartMenus";
import { Nav } from "./Nav";
import { ProChart } from "./ProChart";
import { StatusBar } from "./StatusBar";
import { Card, Empty, ErrorBox, GradeBadge, Loading, StatusPill } from "./ui";
import { WatchStar } from "./WatchStar";

const ON = "border-accent bg-accent text-white";
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
      <main className="grid min-w-0 flex-1 gap-3 p-3 pb-14 lg:grid-cols-[auto_minmax(0,1fr)_12rem]">
        <h1 className="sr-only">Chart{shown ? `: ${shown}` : ""}</h1>
        {shown ? (
          <VcpSide symbol={shown} mode={settings.vcpPanel} onMode={(vcpPanel) => change({ vcpPanel })} />
        ) : (
          <span className="hidden lg:block" />
        )}
        {shown === null ? (
          <Card title="Chart">
            <Empty>{top.isPending ? "Loading the best VCP setup…" : "Search a symbol or company above to chart it."}</Empty>
          </Card>
        ) : (
          <ChartArea key={shown} symbol={shown} settings={settings} onChange={change} />
        )}
        <SideLists current={shown} onPick={pick} top={top.data?.rows ?? []} />
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
        <span className="ml-auto flex items-center gap-3">
          <a href={`/stocks/${encodeURIComponent(symbol)}`} className="text-xs text-accent hover:underline">
            Full analysis →
          </a>
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
            className={`${BTN} w-8 justify-center ${settings.interval === v.id ? ON : ""}`}
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
        className={`${BTN} ${settings.log ? ON : ""}`}
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

function SideLists({ current, onPick, top }: { current: string | null; onPick: (s: string) => void; top: TopRow[] }) {
  const { active } = useWatchlists();
  const item = (s: string, right: React.ReactNode) => (
    <li key={s}>
      <button
        type="button"
        onClick={() => onPick(s)}
        aria-current={s === current ? "true" : undefined}
        className={`flex w-full items-center justify-between rounded px-2 py-1 text-left text-xs ${
          s === current ? "bg-accent/15 font-semibold text-ink" : "text-ink hover:bg-panel2"
        }`}
      >
        <span className="truncate">{s}</span>
        {right}
      </button>
    </li>
  );
  return (
    <aside aria-label="Stocks to chart" className="hidden min-w-0 space-y-3 lg:block">
      <section className="rounded-lg border border-line bg-panel p-2">
        <h2 className="mb-1 px-2 text-xs font-semibold text-ink">
          {active.name} ({active.symbols.length})
        </h2>
        {active.symbols.length === 0 ? (
          <p className="px-2 py-1 text-[11px] text-mute">Star a stock to add it here.</p>
        ) : (
          <ul className="max-h-64 overflow-y-auto">{active.symbols.map((s) => item(s, null))}</ul>
        )}
      </section>
      <section className="rounded-lg border border-line bg-panel p-2">
        <h2 className="mb-1 px-2 text-xs font-semibold text-ink">Top VCP setups</h2>
        <ul className="max-h-[22rem] overflow-y-auto">
          {top.slice(0, 15).map((r) => item(r.symbol, <span className="tabular-nums text-mute">{fmtNum(r.score, 0)}</span>))}
        </ul>
      </section>
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

function weeksBetween(from: string | null, to: string | null): number | null {
  if (!from || !to) return null;
  return Math.round((Date.parse(to) - Date.parse(from)) / (7 * 86_400_000));
}

/** The VCP analysis on the left, opened and closed with a slim button; by default open only
 *  when the stock has a VCP setup on the latest scan. */
function VcpSide({ symbol, mode, onMode }: { symbol: string; mode: VcpPanel; onMode: (m: VcpPanel) => void }) {
  const stock = useStockSetups(symbol);
  const has = vcpSetup(stock.data) !== null;
  const open = mode === "open" || (mode === "auto" && has);
  if (!open) {
    return (
      <button
        type="button"
        onClick={() => onMode("open")}
        aria-label="Show VCP analysis"
        title={has ? "Show the VCP analysis" : `No VCP setup for ${symbol} on the latest scan; show the panel`}
        aria-expanded={false}
        className="flex items-center justify-center gap-1 rounded-lg border border-line bg-panel px-1 py-2 text-[11px] text-mute hover:border-accent hover:text-ink lg:w-6 lg:flex-col lg:self-start lg:py-3"
      >
        <span aria-hidden="true">›</span>
        <span className="lg:[writing-mode:vertical-rl]">VCP{has ? "" : " (none)"}</span>
      </button>
    );
  }
  return (
    <div className="min-w-0 lg:w-[18rem]">
      <SetupSide
        symbol={symbol}
        onHide={
          <button
            type="button"
            onClick={() => onMode("closed")}
            aria-label="Hide VCP analysis"
            aria-expanded={true}
            title="Hide the VCP analysis"
            className="rounded border border-line px-1.5 text-xs text-mute hover:border-accent hover:text-ink"
          >
            ‹
          </button>
        }
      />
    </div>
  );
}

function SetupSide({ symbol, onHide }: { symbol: string; onHide?: React.ReactNode }) {
  const stock = useStockSetups(symbol);
  const s = vcpSetup(stock.data);
  const d = stock.data;
  const close = d?.close ?? null;
  const toPivot = s?.pivot != null && close != null ? (close / s.pivot - 1) * 100 : null;
  const risk = s?.pivot != null && s.stop != null ? (s.stop / s.pivot - 1) * 100 : null;
  const depths = (s?.contractions ?? []).map((c) => (c.depth_pct === null ? DASH : `−${Math.abs(c.depth_pct).toFixed(0)}%`));
  return (
    <aside aria-label={`VCP setup of ${symbol}`} className="min-w-0">
      <Card title="VCP analysis" subtitle={d ? `Scan of ${d.as_of ?? DASH}` : undefined} right={onHide}>
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
              <Row label="Close vs pivot" value={<span className={tone(toPivot)}>{fmtPct(toPivot, 1, true)}</span>} />
              <Row label="Stop" value={`${fmtPrice(s.stop)} (${fmtPct(risk, 1, true)})`} hint="Stop level, and how far below the pivot" />
              <Row label="Base depth" value={fmtPct(s.base_depth_pct, 1)} />
              <Row label="Base length" value={`${weeksBetween(s.base_start, s.base_end ?? d?.as_of ?? null) ?? DASH} weeks`} />
              <Row label="Contractions" value={depths.length ? `${depths.length} (${depths.join(", ")})` : DASH} />
              <Row label="Breakout" value={s.breakout_date ?? DASH} />
              <Row label="Weekly stage" value={d?.weekly_stage ? sentence(d.weekly_stage) : DASH} />
              <Row
                label="Trend Template"
                value={d?.trend_template_pass == null ? DASH : d.trend_template_pass ? <span className="text-up">Pass</span> : <span className="text-down">Fail</span>}
              />
            </dl>
          </>
        )}
      </Card>
    </aside>
  );
}
