import type { ReactNode } from "react";
import { Nav } from "./Nav";
import { StatusBar } from "./StatusBar";

type Active = Parameters<typeof Nav>[0]["active"];

/** The frame every page shares: top bar, a main area and the status bar. ``fill``: on a wide screen
 *  the page fits the window and its cards scroll inside it instead of the whole page. */
export function Page({ active, title, children, fill = false }: { active: Active; title: string; children: ReactNode; fill?: boolean }) {
  return (
    <div className={`flex min-h-screen flex-col ${fill ? "lg:h-screen lg:overflow-hidden" : ""}`}>
      <Nav active={active} />
      <main className={`min-w-0 flex-1 space-y-4 p-4 pb-16 ${fill ? "lg:flex lg:min-h-0 lg:flex-col lg:space-y-0 lg:gap-4 lg:overflow-hidden lg:pb-14" : ""}`}>
        <h1 className="sr-only">{title}</h1>
        {children}
      </main>
      <StatusBar />
    </div>
  );
}
