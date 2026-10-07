// What the Screener page asks the API for: the filter state, and the built-in presets (fixed
// combinations of filters, nothing saved or stored). Every filter runs on the server.
export type ScreenerQuery = {
  q: string;
  stages: string[];
  ttPass?: boolean;
  nearHigh?: boolean;
  minRs?: number;
  minConditions?: number;
  hasSetup: boolean;
  minGrade?: number;
  status?: string;
  sort: string;
  direction: "asc" | "desc";
  page: number;
  pageSize: number;
};

export const PAGE_SIZE_SCREENER = 25;

export const DEFAULT_QUERY: ScreenerQuery = {
  q: "",
  stages: [],
  hasSetup: false,
  sort: "rs_rank",
  direction: "desc",
  page: 1,
  pageSize: PAGE_SIZE_SCREENER,
};

export type Preset = { id: string; label: string; hint: string; query: Partial<ScreenerQuery> };

export const PRESETS: Preset[] = [
  { id: "all", label: "All scanned stocks", hint: "No filter", query: {} },
  {
    id: "tt",
    label: "Trend Template passers",
    hint: "Meet all 10 Trend Template conditions",
    query: { ttPass: true },
  },
  {
    id: "stage2-rs",
    label: "Stage 2, RS 80+",
    hint: "Weekly Stage 2 uptrend with relative strength of 80 or more",
    query: { stages: ["STAGE_2"], minRs: 80 },
  },
  {
    id: "near-high",
    label: "Near 52-week high",
    hint: "Trend Template passers close to their 52-week high",
    query: { ttPass: true, nearHigh: true },
  },
  {
    id: "pivot",
    label: "Pivot-ready setups",
    hint: "Ranked VCP setups sitting at their pivot",
    query: { hasSetup: true, status: "PIVOT_READY", sort: "score" },
  },
  {
    id: "breakout",
    label: "Breakouts",
    hint: "Ranked VCP setups that have broken out",
    query: { hasSetup: true, status: "BREAKOUT", sort: "score" },
  },
  {
    id: "top-vcp",
    label: "Top VCP setups",
    hint: "Ranked VCP setups, best score first",
    query: { hasSetup: true, sort: "score" },
  },
];

export const STAGES = ["STAGE_1", "STAGE_2", "STAGE_3", "STAGE_4", "TRANSITION"];

/** The query of a preset: defaults plus the preset's filters (keeps the search text). */
export const presetQuery = (p: Preset, q: string): ScreenerQuery => ({
  ...DEFAULT_QUERY,
  ...p.query,
  q,
});
