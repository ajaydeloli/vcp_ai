import type { ReactNode } from "react";
import { Nav } from "./Nav";
import { StatusBar } from "./StatusBar";

type Active = Parameters<typeof Nav>[0]["active"];

/** The frame every page shares: top bar, a main area and the status bar. */
export function Page({ active, title, children }: { active: Active; title: string; children: ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col">
      <Nav active={active} />
      <main className="min-w-0 flex-1 space-y-4 p-4 pb-16">
        <h1 className="sr-only">{title}</h1>
        {children}
      </main>
      <StatusBar />
    </div>
  );
}
