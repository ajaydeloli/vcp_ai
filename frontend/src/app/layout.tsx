import type { Metadata } from "next";
import type { ReactNode } from "react";
import { Providers } from "./providers";
import "./globals.css";

export const metadata: Metadata = {
  title: "VCP scanner dashboard",
  description: "Read-only research dashboard for the NSE VCP scanner. Not financial advice.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body className="bg-bg font-sans text-ink antialiased">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
