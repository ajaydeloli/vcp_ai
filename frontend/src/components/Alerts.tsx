"use client";

// Telegram alerts (2026-10-21): whether they are set up, the intraday watcher and what it watches
// right now, the alerts sent, and the settings. Read-only: settings live in config/alerts.yaml,
// the bot token only in .env (this page only says whether it is there).
import { numberLocale } from "@/lib/numberStyle";
import { useAlerts } from "@/lib/api";
import { fmtStamp } from "@/lib/fmt";
import type { AlertsData } from "@/lib/schemas";
import { Page } from "./Page";
import { Card, Empty, ErrorBox, Loading } from "./ui";

const NAMES: Record<string, string> = {
  vcp: "VCP",
  flat_base: "Flat base",
  three_weeks_tight: "3 weeks tight",
  cup_handle: "Cup & handle",
  double_bottom: "Double bottom",
};
const num = (x: number | null, d = 2) => (x === null ? "–" : x.toLocaleString(numberLocale(), { minimumFractionDigits: d, maximumFractionDigits: d }));

function Dot({ on }: { on: boolean }) {
  return <span aria-hidden="true" className={`inline-block h-2 w-2 rounded-full ${on ? "bg-up" : "bg-down"}`} />;
}

function Row({ label, on, text }: { label: string; on: boolean; text: string }) {
  return (
    <li className="flex items-start gap-2">
      <span className="mt-1.5">
        <Dot on={on} />
      </span>
      <span>
        <span className="text-ink">{label}: </span>
        <span className="text-mute">{text}</span>
      </span>
    </li>
  );
}

function Status({ d }: { d: AlertsData }) {
  const w = d.watcher;
  return (
    <Card title="Status">
      <ul className="space-y-2 text-xs">
        <Row label="Telegram" on={d.telegram_configured} text={d.telegram_configured ? "connected (bot token and chat id are in .env)" : "not set up yet: see the steps below"} />
        <Row label="Evening summary" on={d.evening_enabled} text={d.evening_enabled ? "after every daily run" : "off in config/alerts.yaml"} />
        <Row
          label="Intraday watcher"
          on={d.intraday_enabled && w.running}
          text={
            !d.intraday_enabled
              ? "off in config/alerts.yaml"
              : w.running
                ? `running, last check ${fmtStamp(w.last_check)}, market ${w.market ?? "unknown"}`
                : w.last_check
                  ? `stopped (last check ${fmtStamp(w.last_check)}); it starts with the dashboard`
                  : "not started yet; it starts with the dashboard once Telegram is set up"
          }
        />
        {w.error ? <li className="text-down">{`Watcher problem: ${w.error}`}</li> : null}
      </ul>
      {!d.telegram_configured ? (
        <ol className="mt-3 list-decimal space-y-1 pl-5 text-xs text-mute">
          <li>In Telegram, open @BotFather, send /newbot and pick a name: it gives you a token.</li>
          <li>
            Add <code className="text-ink">TELEGRAM_BOT_TOKEN=...</code> to <code className="text-ink">.env</code>.
          </li>
          <li>Send your new bot any message.</li>
          <li>
            Run <code className="text-ink">vcp alerts chat-id</code> and copy the line it prints into <code className="text-ink">.env</code>.
          </li>
          <li>
            Run <code className="text-ink">vcp alerts test</code>, then restart the dashboard.
          </li>
        </ol>
      ) : null}
    </Card>
  );
}

function Watching({ d }: { d: AlertsData }) {
  const s = d.settings;
  return (
    <Card
      title="Watching now"
      subtitle={`Setups within ${s.intraday_near_pivot_pct}% of the pivot${s.include_ai_watchlist ? ", the AI watchlist" : ""}${s.own_symbols.length ? " and your symbols" : ""}. Alerts: within ${s.approach_pct}% below the pivot, and above it.`}
    >
      {d.watching.length === 0 ? (
        <Empty>{d.watcher.market === "open" ? "Nothing near a pivot right now." : "The watcher lists stocks in market hours."}</Empty>
      ) : (
        <table className="w-full text-xs">
          <thead className="text-left text-[11px] text-mute">
            <tr>
              <th className="py-1 font-normal">Stock</th>
              <th className="py-1 font-normal">Setup</th>
              <th className="py-1 text-right font-normal">Pivot</th>
              <th className="py-1 text-right font-normal">Last</th>
              <th className="py-1 text-right font-normal">To pivot</th>
            </tr>
          </thead>
          <tbody>
            {d.watching.map((w) => {
              const g = w.gap_pct;
              const tone = g === null ? "text-mute" : g >= 0 ? (g > s.chase_warn_pct ? "text-warn" : "text-up") : -g <= s.approach_pct ? "text-accent" : "text-ink";
              return (
                <tr key={w.symbol} className="border-t border-line">
                  <td className="py-1.5">
                    <a href={`/stocks/${encodeURIComponent(w.symbol)}`} className="text-ink hover:underline">
                      {w.symbol}
                    </a>
                  </td>
                  <td className="py-1.5 text-mute">{`${NAMES[w.strategy] ?? w.strategy} · ${w.why}`}</td>
                  <td className="py-1.5 text-right tabular-nums">{num(w.pivot)}</td>
                  <td className="py-1.5 text-right tabular-nums">{num(w.last_price)}</td>
                  <td className={`py-1.5 text-right tabular-nums ${tone}`}>{g === null ? "–" : `${g > 0 ? "+" : ""}${g.toFixed(1)}%`}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </Card>
  );
}

function Sent({ d }: { d: AlertsData }) {
  return (
    <Card title="Recent alerts" subtitle="Newest first; one that could not be sent says why.">
      {d.sent.length === 0 ? (
        <Empty>No alerts yet.</Empty>
      ) : (
        <ul className="space-y-2 text-xs">
          {d.sent.map((a, i) => (
            <li key={`${a.sent_at}-${i}`} className="rounded border border-line p-2" aria-label={`${a.kind} alert`}>
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-semibold text-ink">{a.kind}</span>
                {a.symbol ? <span className="text-ink">{a.symbol}</span> : null}
                <span className={a.ok ? "text-up" : "text-down"}>{a.ok ? "sent" : `not sent: ${a.error ?? ""}`}</span>
                <span className="ml-auto text-[11px] text-mute">{fmtStamp(a.sent_at)}</span>
              </div>
              <p className="mt-1 whitespace-pre-line break-words text-mute">{a.text}</p>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function Settings({ d }: { d: AlertsData }) {
  const s = d.settings;
  const rows: [string, string][] = [
    ["Evening: setups listed within", `${s.evening_near_pivot_pct}% of the pivot`],
    ["Intraday: setups watched within", `${s.intraday_near_pivot_pct}% of the pivot`],
    ["Approaching alert", `within ${s.approach_pct}% below the pivot`],
    ["Entry limit warning", `more than ${s.chase_warn_pct}% above the pivot`],
    ["Checks", `every ${s.poll_seconds} s in market hours`],
    ["AI watchlist", s.include_ai_watchlist ? "watched" : "not watched"],
    ["Your symbols", s.own_symbols.length ? s.own_symbols.join(", ") : "none"],
  ];
  return (
    <Card title="Settings" subtitle="Change them in config/alerts.yaml.">
      <dl className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs">
        {rows.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-mute">{k}</dt>
            <dd className="text-ink">{v}</dd>
          </div>
        ))}
      </dl>
    </Card>
  );
}

export function AlertsPage() {
  const q = useAlerts();
  const d = q.data;
  return (
    <Page active="Alerts" title="Alerts">
      {q.isLoading ? <Loading what="alerts" /> : null}
      {q.error ? <ErrorBox error={q.error} /> : null}
      {d ? (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
          <div className="space-y-4">
            <Watching d={d} />
            <Sent d={d} />
          </div>
          <div className="space-y-4">
            <Status d={d} />
            <Settings d={d} />
            <p className="text-[11px] text-mute">Alerts are monitoring only: they never change a scan or a strategy, and they are not trading advice.</p>
          </div>
        </div>
      ) : null}
    </Page>
  );
}
