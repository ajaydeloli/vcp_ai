"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { currentPrefs } from "@/lib/prefs";

/** The address without a path opens the start page from the preferences (Market Overview by default). */
export default function Home() {
  const router = useRouter();
  useEffect(() => {
    router.replace(currentPrefs().startPage);
  }, [router]);
  return null;
}
