import type { Metadata } from "next";
import type { ReactNode } from "react";
import { THEME_BOOT } from "@/lib/themeBoot";
import { Providers } from "./providers";
import "./globals.css";

export const metadata: Metadata = {
  title: "ShreeVault - NSE VCP scanner",
  description: "Read-only research dashboard for the NSE VCP scanner. Not financial advice.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    // data-theme is set by THEME_BOOT before React loads, hence suppressHydrationWarning
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT }} />
      </head>
      <body className="bg-bg font-sans text-ink antialiased">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
