import type { Config } from "tailwindcss";

// Dark theme after the owner's mockup (FRONTEND_SPECIFICATION 67, decision W4).
const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "#070b14",
        panel: "#0d1422",
        panel2: "#111a2c",
        line: "#1b2740",
        ink: "#e6ecf8",
        mute: "#8a97b3",
        accent: "#2f6df6",
        up: "#26c281",
        down: "#ef5350",
        warn: "#f5a524",
      },
      fontFamily: { sans: ["ui-sans-serif", "system-ui", "Segoe UI", "Roboto", "sans-serif"] },
    },
  },
  plugins: [],
};

export default config;
