"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { usePrefs } from "@/lib/prefs";

export function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(() => new QueryClient({ defaultOptions: { queries: { refetchOnWindowFocus: false, retry: 1 } } }));
  const { prefs } = usePrefs();
  // a new number style redraws every page (the formatters are not React state)
  return (
    <QueryClientProvider client={client}>
      <div key={prefs.numbers} className="contents">
        {children}
      </div>
    </QueryClientProvider>
  );
}
