// The theme script for <head> (server-safe: no "use client"), so a light theme does not flash
// dark before React loads. The preferences themselves are in prefs.ts.
export const PREFS_KEY = "vcp-prefs";

/** Runs in <head> before the page paints, so a light theme does not flash dark first. */
export const THEME_BOOT = `try{var p=JSON.parse(localStorage.getItem("${PREFS_KEY}")||"{}");var t=p.theme;if(t==="system")t=window.matchMedia&&window.matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light";if(t==="light")document.documentElement.dataset.theme="light"}catch(e){}`;
