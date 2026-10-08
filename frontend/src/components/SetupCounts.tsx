"use client";

import { useSetups } from "@/lib/api";
import { DASH, fmtInt } from "@/lib/fmt";
import { countList } from "@/lib/lists";
import { Card } from "./ui";

const TILES = [
  { id: "aplus", title: "A+ VCP setups", sub: "Grade 3 in the VCP list", box: "border-up/40 from-up/20", text: "text-up" },
  { id: "vcp", title: "VCP setups", sub: "", box: "border-accent/40 from-accent/20", text: "text-accent" },
  { id: "forming", title: "Forming bases", sub: "Pivot not reached yet", box: "border-violet/40 from-violet/20", text: "text-violet" },
  { id: "watch", title: "Breakout watch", sub: "Pivot ready or broken out", box: "border-warn/40 from-warn/20", text: "text-warn" },
];

/** How many stocks are in each list of the VCP ranking. */
export function SetupCounts() {
  const vcp = useSetups("vcp");
  const rows = vcp.data?.rows ?? null;
  return (
    <Card title="Setups today" subtitle="The lists of the VCP ranking" className="h-full min-w-0">
      <div className="grid flex-1 grid-cols-2 gap-3">
        {TILES.map((t) => (
          <div key={t.id} className={`rounded-lg border bg-gradient-to-br to-panel p-3 ${t.box}`}>
            <p className={`text-[11px] font-medium uppercase tracking-wide ${t.text}`}>{t.title}</p>
            <p className="mt-1 text-3xl font-semibold text-ink" data-testid={`kpi-${t.title}`}>
              {rows ? fmtInt(countList(rows, t.id)) : DASH}
            </p>
            <p className="mt-0.5 min-h-4 text-xs text-mute">
              {t.id === "vcp" ? (rows ? `${countList(rows, "vcp_like")} more are VCP like` : "") : t.sub}
            </p>
          </div>
        ))}
      </div>
    </Card>
  );
}
