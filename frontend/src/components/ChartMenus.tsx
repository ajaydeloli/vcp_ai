"use client";

// The Chart page's Indicators and Colours dropdowns: every moving average (add, remove, SMA or
// EMA, period, colour), the other indicators with their parameters, and the chart's colours.
import { type ReactNode, useEffect, useRef, useState } from "react";
import {
  COLOR_KEYS,
  DEFAULT_AVERAGES,
  DEFAULT_CHART,
  DEFAULT_COLORS,
  DEFAULT_PARAMS,
  MAX_AVERAGES,
  PERIOD_MAX,
  TOGGLES,
  maLabel,
  newAverage,
  shownCount,
  type ChartSettings,
  type MaKind,
  type MovingAverage,
  type Params,
  type Toggle,
} from "@/lib/chartSettings";

export const BTN = "inline-flex h-8 items-center gap-1.5 rounded border border-line bg-panel2 px-2.5 text-xs text-ink hover:border-accent";
const SMALL = "h-7 rounded border border-line bg-panel2 px-1.5 text-xs text-ink";
type Change = (p: Partial<ChartSettings>) => void;

/** A button with a panel under it; closes on Escape (focus back on the button) or a click outside. */
function Dropdown({ label, title, children, wide = false }: { label: ReactNode; title: string; children: ReactNode; wide?: boolean }) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false);
    };
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        button.current?.focus();
      }
    };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", key);
    };
  }, [open]);
  return (
    <div ref={wrap} className="relative">
      <button ref={button} type="button" aria-haspopup="true" aria-expanded={open} onClick={() => setOpen((o) => !o)} className={`${BTN} ${open ? "border-accent" : ""}`}>
        {label} <span aria-hidden="true">▾</span>
      </button>
      {open ? (
        <div
          role="dialog"
          aria-label={title}
          className={`absolute left-0 top-9 z-30 max-h-[75vh] overflow-y-auto rounded-lg border border-line bg-panel p-3 text-xs shadow-xl ${wide ? "w-[26rem] max-w-[90vw]" : "w-72"}`}
        >
          {children}
        </div>
      ) : null}
    </div>
  );
}

/** A whole-number field: what is typed shows at once; a valid number is passed on. */
export function NumberField({
  label,
  value,
  min,
  max,
  step = 1,
  onValue,
  width = "w-14",
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  onValue: (n: number) => void;
  width?: string;
}) {
  const [text, setText] = useState(String(value));
  useEffect(() => setText(String(value)), [value]);
  const ok = (n: number) => Number.isFinite(n) && n >= min && n <= max && (step < 1 || Number.isInteger(n));
  return (
    <input
      type="number"
      aria-label={label}
      title={`${label} (${min} to ${max})`}
      value={text}
      min={min}
      max={max}
      step={step}
      onChange={(e) => {
        setText(e.target.value);
        const n = Number(e.target.value);
        if (e.target.value !== "" && ok(n)) onValue(n);
      }}
      onBlur={() => setText(String(value))}
      aria-invalid={text !== "" && !ok(Number(text)) ? true : undefined}
      className={`${SMALL} ${width} tabular-nums aria-[invalid=true]:border-down`}
    />
  );
}

function KindSelect({ label, value, onValue }: { label: string; value: MaKind; onValue: (k: MaKind) => void }) {
  return (
    <select aria-label={label} value={value} onChange={(e) => onValue(e.target.value as MaKind)} className={SMALL}>
      <option value="SMA">SMA</option>
      <option value="EMA">EMA</option>
    </select>
  );
}

function ColorField({ label, value, onValue }: { label: string; value: string; onValue: (c: string) => void }) {
  return (
    <input
      type="color"
      aria-label={label}
      title={label}
      value={value}
      onChange={(e) => onValue(e.target.value)}
      className="h-7 w-8 cursor-pointer rounded border border-line bg-panel2 p-0.5"
    />
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <fieldset className="mb-3 last:mb-0">
      <legend className="mb-1.5 text-[11px] uppercase tracking-wide text-mute">{title}</legend>
      <div className="space-y-1.5">{children}</div>
    </fieldset>
  );
}

function ToggleBox({ id, settings, onChange, children }: { id: Toggle; settings: ChartSettings; onChange: Change; children?: ReactNode }) {
  const t = TOGGLES.find((x) => x.id === id)!;
  const on = settings.toggles.includes(id);
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
      <label className="flex min-w-[9rem] cursor-pointer items-center gap-2 text-ink">
        <input
          type="checkbox"
          checked={on}
          onChange={() => onChange({ toggles: on ? settings.toggles.filter((x) => x !== id) : [...settings.toggles, id] })}
        />
        {t.label}
      </label>
      {children}
    </div>
  );
}

export function IndicatorMenu({ settings, onChange }: { settings: ChartSettings; onChange: Change }) {
  const p = settings.params;
  const setParams = (q: Partial<Params>) => onChange({ params: { ...p, ...q } });
  const setAverage = (id: string, q: Partial<MovingAverage>) =>
    onChange({ averages: settings.averages.map((a) => (a.id === id ? { ...a, ...q } : a)) });
  return (
    <Dropdown label={`Indicators (${shownCount(settings)})`} title="Indicators" wide>
      <Section title="Moving averages (on weekly bars, the period counts weeks)">
        {settings.averages.map((a, k) => (
          <div key={a.id} className="flex items-center gap-1.5" data-testid="average-row">
            <input type="checkbox" aria-label={`Show ${maLabel(a)}`} checked={a.on} onChange={() => setAverage(a.id, { on: !a.on })} />
            <KindSelect label={`Average ${k + 1} type`} value={a.kind} onValue={(kind) => setAverage(a.id, { kind })} />
            <NumberField label={`Average ${k + 1} period`} value={a.period} min={1} max={PERIOD_MAX} onValue={(period) => setAverage(a.id, { period })} />
            <ColorField label={`Average ${k + 1} colour`} value={a.color} onValue={(color) => setAverage(a.id, { color })} />
            <span className="flex-1 text-mute">{maLabel(a)}</span>
            <button
              type="button"
              aria-label={`Remove ${maLabel(a)}`}
              title="Remove"
              onClick={() => onChange({ averages: settings.averages.filter((x) => x.id !== a.id) })}
              className="rounded px-1.5 text-mute hover:text-down"
            >
              ✕
            </button>
          </div>
        ))}
        <button
          type="button"
          disabled={settings.averages.length >= MAX_AVERAGES}
          onClick={() => onChange({ averages: [...settings.averages, newAverage(settings.averages)] })}
          className="text-accent hover:underline disabled:text-mute disabled:no-underline"
        >
          + Add moving average
        </button>
      </Section>
      <Section title="On the price">
        <ToggleBox id="bb" settings={settings} onChange={onChange}>
          <NumberField label="Bollinger period" value={p.bb.period} min={2} max={200} onValue={(period) => setParams({ bb: { ...p.bb, period } })} />
          <span className="text-mute">×</span>
          <NumberField label="Bollinger width" value={p.bb.mult} min={0.5} max={5} step={0.1} onValue={(mult) => setParams({ bb: { ...p.bb, mult } })} />
        </ToggleBox>
      </Section>
      <Section title="Panes">
        <ToggleBox id="rs" settings={settings} onChange={onChange}>
          <span className="text-mute">new high over</span>
          <NumberField label="RS new-high lookback in weeks" value={p.rs.weeks} min={4} max={260} onValue={(weeks) => setParams({ rs: { weeks } })} />
          <span className="text-mute">weeks</span>
        </ToggleBox>
        <ToggleBox id="volume" settings={settings} onChange={onChange} />
        <ToggleBox id="volma" settings={settings} onChange={onChange}>
          <KindSelect label="Volume average type" value={p.volMa.kind} onValue={(kind) => setParams({ volMa: { ...p.volMa, kind } })} />
          <NumberField label="Volume average period" value={p.volMa.period} min={1} max={PERIOD_MAX} onValue={(period) => setParams({ volMa: { ...p.volMa, period } })} />
        </ToggleBox>
        <ToggleBox id="rsi" settings={settings} onChange={onChange}>
          <NumberField label="RSI period" value={p.rsi.period} min={2} max={100} onValue={(period) => setParams({ rsi: { ...p.rsi, period } })} />
          <span className="text-mute">lines</span>
          <NumberField label="RSI upper line" value={p.rsi.upper} min={51} max={99} onValue={(upper) => setParams({ rsi: { ...p.rsi, upper } })} width="w-12" />
          <NumberField label="RSI lower line" value={p.rsi.lower} min={1} max={49} onValue={(lower) => setParams({ rsi: { ...p.rsi, lower } })} width="w-12" />
        </ToggleBox>
        <ToggleBox id="macd" settings={settings} onChange={onChange}>
          <NumberField label="MACD fast" value={p.macd.fast} min={1} max={p.macd.slow - 1} onValue={(fast) => setParams({ macd: { ...p.macd, fast } })} width="w-12" />
          <NumberField label="MACD slow" value={p.macd.slow} min={p.macd.fast + 1} max={300} onValue={(slow) => setParams({ macd: { ...p.macd, slow } })} width="w-12" />
          <NumberField label="MACD signal" value={p.macd.signal} min={1} max={100} onValue={(signal) => setParams({ macd: { ...p.macd, signal } })} width="w-12" />
        </ToggleBox>
      </Section>
      <Section title="Setup">
        <ToggleBox id="vcp" settings={settings} onChange={onChange} />
        <ToggleBox id="levels" settings={settings} onChange={onChange} />
      </Section>
      <div className="mt-2 flex justify-between border-t border-line pt-2">
        <button
          type="button"
          className="text-accent hover:underline"
          onClick={() => onChange({ averages: DEFAULT_AVERAGES, toggles: DEFAULT_CHART.toggles, params: DEFAULT_PARAMS })}
        >
          Reset to defaults
        </button>
        <button type="button" className="text-mute hover:text-ink" onClick={() => onChange({ averages: settings.averages.map((a) => ({ ...a, on: false })), toggles: [] })}>
          Hide all
        </button>
      </div>
    </Dropdown>
  );
}

export function ColorMenu({ settings, onChange }: { settings: ChartSettings; onChange: Change }) {
  const c = settings.colors;
  return (
    <Dropdown label="Colours" title="Colours">
      <div className="grid grid-cols-[1fr_auto] items-center gap-x-3 gap-y-1.5">
        {COLOR_KEYS.map((k) => (
          <label key={k.id} className="contents">
            <span className="text-ink">{k.label}</span>
            <ColorField label={`${k.label} colour`} value={c[k.id]} onValue={(v) => onChange({ colors: { ...c, [k.id]: v } })} />
          </label>
        ))}
      </div>
      <p className="mt-2 text-[11px] text-mute">Moving average colours are set in Indicators.</p>
      <div className="mt-2 border-t border-line pt-2">
        <button type="button" className="text-accent hover:underline" onClick={() => onChange({ colors: DEFAULT_COLORS })}>
          Reset colours
        </button>
      </div>
    </Dropdown>
  );
}
