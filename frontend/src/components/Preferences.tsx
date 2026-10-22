"use client";

// The ⚙ button in the top bar and its panel (owner, 2026-10-22). Preferences change only how the
// pages look and start, and are kept in this browser; the strategy rules, scoring and paper
// ledger are frozen and are not settings.
import { useEffect, useRef, useState } from "react";
import { useStrategies } from "@/lib/api";
import { strategyLabel } from "@/lib/fmt";
import { PAGE_SIZES, START_PAGES, type Numbers, type Theme, usePrefs } from "@/lib/prefs";

/** Screener columns that can be hidden (rank, star and symbol always show). Keys of COLS in Screener.tsx. */
export const HIDEABLE_COLS: [string, string][] = [
  ["sector", "Sector"],
  ["stage", "Stage"],
  ["conditions_passed", "Conditions"],
  ["near_high", "Near high"],
  ["rs_rank", "RS"],
  ["close", "Close"],
  ["live", "Live"],
  ["change_pct", "Change"],
  ["grade", "Setup"],
  ["score", "Score"],
  ["pivot", "Pivot"],
  ["status", "Status"],
  ["pivot_distance_pct", "To pivot"],
];

const field = "rounded border border-line bg-panel2 px-2 py-1 text-xs text-ink focus:border-accent focus:outline-none";

function Choice<T extends string>({ name, label, value, options, onChange }: { name: string; label: string; value: T; options: [T, string][]; onChange: (v: T) => void }) {
  return (
    <fieldset>
      <legend className="mb-1 text-xs font-semibold text-ink">{label}</legend>
      <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label={label}>
        {options.map(([v, text]) => (
          <label
            key={v}
            className={`cursor-pointer rounded border px-2 py-1 text-xs ${value === v ? "border-accent bg-accent/10 text-ink" : "border-line text-mute hover:text-ink"}`}
          >
            <input type="radio" name={name} value={v} checked={value === v} onChange={() => onChange(v)} className="sr-only" />
            {text}
          </label>
        ))}
      </div>
    </fieldset>
  );
}

function Panel({ onClose }: { onClose: () => void }) {
  const { prefs, set, reset } = usePrefs();
  const strategies = useStrategies();
  const ids = strategies.data?.strategies.map((s) => s.strategy_id) ?? [prefs.strategy];
  const hidden = new Set(prefs.hiddenCols);
  const toggleCol = (key: string) => set({ hiddenCols: hidden.has(key) ? prefs.hiddenCols.filter((k) => k !== key) : [...prefs.hiddenCols, key] });
  return (
    <div
      role="dialog"
      aria-label="Preferences"
      className="absolute right-0 top-full z-50 mt-2 w-[22rem] max-w-[92vw] space-y-4 rounded-lg border border-line bg-panel p-4 shadow-xl"
    >
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-ink">Preferences</h2>
        <button type="button" onClick={onClose} className="text-xs text-mute hover:text-ink" aria-label="Close preferences">
          ✕
        </button>
      </div>
      <Choice<Theme>
        name="pref-theme"
        label="Theme"
        value={prefs.theme}
        options={[
          ["dark", "Dark"],
          ["light", "Light"],
          ["system", "Same as computer"],
        ]}
        onChange={(theme) => set({ theme })}
      />
      <Choice<Numbers>
        name="pref-numbers"
        label="Numbers"
        value={prefs.numbers}
        options={[
          ["in", "Indian 1,23,456.78"],
          ["intl", "International 123,456.78"],
        ]}
        onChange={(numbers) => set({ numbers })}
      />
      <label className="flex items-center justify-between gap-3 text-xs">
        <span className="font-semibold text-ink">Start page</span>
        <select className={field} value={prefs.startPage} onChange={(e) => set({ startPage: e.target.value })}>
          {START_PAGES.map(([path, name]) => (
            <option key={path} value={path}>
              {name}
            </option>
          ))}
        </select>
      </label>
      <fieldset className="space-y-2 border-t border-line pt-3">
        <legend className="text-xs font-semibold text-ink">Screener</legend>
        <label className="flex items-center justify-between gap-3 text-xs text-mute">
          Opens with strategy
          <select className={field} value={prefs.strategy} onChange={(e) => set({ strategy: e.target.value })}>
            {ids.map((id) => (
              <option key={id} value={id}>
                {strategyLabel(id)}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center justify-between gap-3 text-xs text-mute">
          Rows per page
          <select className={field} value={prefs.pageSize} onChange={(e) => set({ pageSize: Number(e.target.value) })}>
            {PAGE_SIZES.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2 text-xs text-mute">
          <input type="checkbox" checked={prefs.showFundamentals} onChange={(e) => set({ showFundamentals: e.target.checked })} />
          Show EPS and sales growth columns from the start
        </label>
        <div>
          <p className="mb-1 text-xs text-mute">Columns shown</p>
          <div className="grid grid-cols-3 gap-x-2 gap-y-1">
            {HIDEABLE_COLS.map(([key, label]) => (
              <label key={key} className="flex items-center gap-1 text-xs text-ink">
                <input type="checkbox" checked={!hidden.has(key)} onChange={() => toggleCol(key)} />
                {label}
              </label>
            ))}
          </div>
        </div>
      </fieldset>
      <div className="flex items-center justify-between border-t border-line pt-3">
        <p className="text-[11px] text-mute">Saved in this browser only.</p>
        <button type="button" onClick={reset} className="rounded border border-line px-2 py-0.5 text-xs text-mute hover:border-accent hover:text-ink">
          Reset to defaults
        </button>
      </div>
    </div>
  );
}

/** The ⚙ button: opens the panel; Escape or a click outside closes it. */
export function PreferencesButton() {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const key = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    const click = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("keydown", key);
    document.addEventListener("mousedown", click);
    return () => {
      document.removeEventListener("keydown", key);
      document.removeEventListener("mousedown", click);
    };
  }, [open]);
  return (
    <div ref={box} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-label="Preferences"
        title="Preferences"
        className="inline-flex h-8 w-8 items-center justify-center rounded border border-line text-mute hover:border-accent hover:text-ink"
      >
        <svg aria-hidden="true" viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="3" />
          <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.6 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.6a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
        </svg>
      </button>
      {open ? <Panel onClose={() => setOpen(false)} /> : null}
    </div>
  );
}
