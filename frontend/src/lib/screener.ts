// What the Screener page asks the API for: the filter state, and the built-in presets (fixed
// combinations of filters, nothing saved or stored). Every filter runs on the server.
export type ScreenerQuery = {
  strategy: string;
  q: string;
  symbols?: string[];
  stages: string[];
  ttPass?: boolean;
  nearHigh?: boolean;
  minRs?: number;
  minConditions?: number;
  hasSetup: boolean;
  minGrade?: number;
  status?: string;
  classification?: string;
  minStrategies?: number;
  sort: string;
  direction: "asc" | "desc";
  page: number;
  pageSize: number;
};

export const PAGE_SIZE_SCREENER = 25;

export const DEFAULT_QUERY: ScreenerQuery = {
  strategy: "vcp",
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
    id: "top",
    label: "Top setups",
    hint: "Ranked setups of the chosen strategy, best score first",
    query: { hasSetup: true, sort: "score" },
  },
  {
    id: "aplus",
    label: "A+ VCP",
    hint: "Grade 3 setups",
    query: { hasSetup: true, classification: "A_PLUS_VCP", sort: "score" },
  },
  {
    id: "vcp",
    label: "VCP",
    hint: "Ranked setups classed VCP",
    query: { hasSetup: true, classification: "VCP", sort: "score" },
  },
  {
    id: "vcp_like",
    label: "VCP like",
    hint: "Ranked setups classed VCP like",
    query: { hasSetup: true, classification: "VCP_LIKE", sort: "score" },
  },
  {
    id: "forming",
    label: "Forming",
    hint: "Ranked setups whose pivot is not reached yet",
    query: { hasSetup: true, status: "FORMING", sort: "score" },
  },
  {
    id: "pivot",
    label: "Pivot ready",
    hint: "Ranked setups sitting at their pivot",
    query: { hasSetup: true, status: "PIVOT_READY", sort: "score" },
  },
  {
    id: "breakout",
    label: "Breakouts",
    hint: "Ranked setups that have broken out",
    query: { hasSetup: true, status: "BREAKOUT", sort: "score" },
  },
  {
    id: "several",
    label: "On several strategies",
    hint: "Ranked by two or more strategies",
    query: { minStrategies: 2, sort: "score" },
  },
];

export const STAGES = ["STAGE_1", "STAGE_2", "STAGE_3", "STAGE_4", "TRANSITION"];

/** The query of a preset: defaults plus the preset's filters (keeps the search text). */
export const presetQuery = (p: Preset, q: string, strategy = "vcp"): ScreenerQuery => ({
  ...DEFAULT_QUERY,
  ...p.query,
  q,
  strategy,
});
