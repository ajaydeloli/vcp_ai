// Chart colours from the current theme (CSS variables), so the charts follow dark or light.
import { ColorType, CrosshairMode } from "lightweight-charts";
import { themeColor } from "./prefs";

export const chartTheme = () => ({
  layout: {
    background: { type: ColorType.Solid, color: themeColor("panel") },
    textColor: themeColor("mute"),
  },
  grid: { vertLines: { color: themeColor("line", 0.55) }, horzLines: { color: themeColor("line", 0.55) } },
  rightPriceScale: { borderColor: themeColor("line") },
  timeScale: { borderColor: themeColor("line"), timeVisible: false },
  crosshair: { mode: CrosshairMode.Normal },
});
