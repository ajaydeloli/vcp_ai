import type { Config } from "tailwindcss";

// Colours are CSS variables (src/app/globals.css): the dark theme after the owner's mockup
// (FRONTEND_SPECIFICATION 67, decision W4) and a light theme (preferences, 2026-10-22).
const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "rgb(var(--c-bg) / <alpha-value>)",
        panel: "rgb(var(--c-panel) / <alpha-value>)",
        panel2: "rgb(var(--c-panel2) / <alpha-value>)",
        line: "rgb(var(--c-line) / <alpha-value>)",
        ink: "rgb(var(--c-ink) / <alpha-value>)",
        mute: "rgb(var(--c-mute) / <alpha-value>)",
        accent: "rgb(var(--c-accent) / <alpha-value>)",
        up: "rgb(var(--c-up) / <alpha-value>)",
        down: "rgb(var(--c-down) / <alpha-value>)",
        warn: "rgb(var(--c-warn) / <alpha-value>)",
        violet: "rgb(var(--c-violet) / <alpha-value>)",
        live: "rgb(var(--c-live) / <alpha-value>)",
      },
      fontFamily: { sans: ["ui-sans-serif", "system-ui", "Segoe UI", "Roboto", "sans-serif"] },
    },
  },
  plugins: [],
};

export default config;
