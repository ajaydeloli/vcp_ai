import { redirect } from "next/navigation";

/** The Stock Analysis page was removed (owner, 2026-11-07): its address opens the Chart page. */
export default function StocksPage() {
  redirect("/chart");
}
