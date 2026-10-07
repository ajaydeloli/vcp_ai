import { StockAnalysis } from "@/components/StockAnalysis";

export default async function StockPage({ params }: { params: Promise<{ symbol: string }> }) {
  const { symbol } = await params;
  return <StockAnalysis symbol={decodeURIComponent(symbol)} />;
}
