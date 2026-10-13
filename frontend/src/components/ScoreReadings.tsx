"use client";

// Shared by the cards that show the Market Health Score's readings (FRONTEND_SPECIFICATION §67.35):
// a reading is coloured by its points in the score, the same as the pillar bars.
import type { MarketHealth as Health } from "@/lib/schemas";

export type Pillar = NonNullable<Health["score"]>["pillars"][number];

/** A reading's colour by its points in the score: green 65+, amber 50–64, red below 50. */
export function pointsClass(points: number | null): string {
  return points === null ? "text-mute" : points >= 65 ? "text-up" : points >= 50 ? "text-warn" : "text-down";
}
