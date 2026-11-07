// Chart colours from the current theme (CSS variables), so the charts follow dark or light.
import { ColorType, CrosshairMode, type IChartApi } from "lightweight-charts";
import { themeColor } from "./prefs";

export const CHART_FONT = "'Inter Variable', Inter, system-ui, 'Segoe UI', Roboto, sans-serif";

export const chartTheme = () => ({
  layout: {
    background: { type: ColorType.Solid, color: themeColor("panel") },
    textColor: themeColor("mute"),
    // the charts draw on a canvas, so they need the page font named here
    fontFamily: CHART_FONT,
  },
  grid: { vertLines: { color: themeColor("line", 0.55) }, horzLines: { color: themeColor("line", 0.55) } },
  rightPriceScale: { borderColor: themeColor("line") },
  timeScale: { borderColor: themeColor("line"), timeVisible: false },
  crosshair: { mode: CrosshairMode.Normal },
});

/** A chart drawn before Inter has loaded keeps the fallback font on its canvas: redraw it once
 *  the page's fonts are ready (a chart already removed by then is left alone). */
export function redrawWhenFontsReady(chart: IChartApi) {
  if (typeof document === "undefined" || !document.fonts) return;
  void document.fonts.ready.then(() => {
    try {
      chart.applyOptions({ layout: { fontFamily: CHART_FONT } });
    } catch {
      /* the chart was removed meanwhile */
    }
  });
}
