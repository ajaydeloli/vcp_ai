const LATER = ["Screener", "Watchlist", "Backtests", "Reports"];

export function Nav() {
  return (
    <nav aria-label="Main" className="hidden w-52 shrink-0 border-r border-line bg-panel p-4 lg:block">
      <div className="mb-6">
        <p className="text-lg font-semibold text-ink">VCP scanner</p>
        <p className="text-[11px] text-mute">NSE research dashboard</p>
      </div>
      <ul className="space-y-1 text-sm">
        <li>
          <a
            href="/dashboard"
            aria-current="page"
            className="block rounded-lg bg-accent px-3 py-2 font-medium text-white"
          >
            Dashboard
          </a>
        </li>
        {LATER.map((label) => (
          <li key={label}>
            <span
              aria-disabled="true"
              className="flex cursor-not-allowed items-center justify-between rounded-lg px-3 py-2 text-mute/60"
            >
              {label}
              <span className="text-[10px] uppercase">later</span>
            </span>
          </li>
        ))}
      </ul>
    </nav>
  );
}
