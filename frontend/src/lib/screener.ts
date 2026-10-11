// What the Screener page asks the API for: the filter state. Nothing is preset, saved or stored.
// Every filter runs on the server.
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
  /** only the scan universe; unchecked adds the stocks outside it (price only) */
  universeOnly: boolean;
  /** with the universe unchecked: main-board (EQ) stocks only */
  eqOnly: boolean;
  minGrade?: number;
  status?: string;
  classification?: string;
  minStrategies?: number;
  /** NSE sector (S1) */
  sector?: string;
  sort: string;
  direction: "asc" | "desc";
  page: number;
  pageSize: number;
};

export const PAGE_SIZE_SCREENER = 15;

export const DEFAULT_QUERY: ScreenerQuery = {
  strategy: "vcp",
  q: "",
  stages: [],
  hasSetup: false,
  universeOnly: true,
  eqOnly: true,
  sort: "rs_rank",
  direction: "desc",
  page: 1,
  pageSize: PAGE_SIZE_SCREENER,
};

export const STAGES = ["STAGE_1", "STAGE_2", "STAGE_3", "STAGE_4", "TRANSITION"];

/** NSE's sectors (the Industry column of its index lists; DATA_SPECIFICATION §48.1). */
export const SECTORS = [
  "Automobile and Auto Components", "Capital Goods", "Chemicals", "Construction",
  "Construction Materials", "Consumer Durables", "Consumer Services", "Diversified",
  "Fast Moving Consumer Goods", "Financial Services", "Forest Materials", "Healthcare",
  "Information Technology", "Media Entertainment & Publication", "Metals & Mining",
  "Oil Gas & Consumable Fuels", "Power", "Realty", "Services", "Telecommunication",
  "Textiles", "Utilities",
];
