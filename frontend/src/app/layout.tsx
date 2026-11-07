import type { Metadata } from "next";
import type { ReactNode } from "react";
import { THEME_BOOT } from "@/lib/themeBoot";
import { Providers } from "./providers";
// Inter, bundled with the dashboard (no font is fetched from the web; owner, 2026-11-07)
import "@fontsource-variable/inter";
import "./globals.css";

export const metadata: Metadata = {
  title: "ShreeVault",
  description: "Read-only research dashboard for the NSE VCP scanner. Not financial advice.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    // data-theme is set by THEME_BOOT before React loads, hence suppressHydrationWarning
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT }} />
      </head>
      <body className="bg-bg font-sans text-ink antialiased lg:pl-14">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
