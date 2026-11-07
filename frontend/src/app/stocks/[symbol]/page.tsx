import { redirect } from "next/navigation";

/** The Stock Analysis page was removed (owner, 2026-11-07): a stock's old address opens its chart. */
export default async function StockPage({ params }: { params: Promise<{ symbol: string }> }) {
  const { symbol } = await params;
  redirect(`/chart/${encodeURIComponent(decodeURIComponent(symbol))}`);
}
