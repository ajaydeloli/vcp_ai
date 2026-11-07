import { ChartPage } from "@/components/ChartPage";

export default async function ChartSymbolPage({ params }: { params: Promise<{ symbol: string }> }) {
  const { symbol } = await params;
  return <ChartPage symbol={decodeURIComponent(symbol).toUpperCase()} />;
}
