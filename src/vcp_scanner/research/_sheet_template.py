# ruff: noqa: E501
"""HTML template of the blind labelling sheet (``labelling.write_outputs``).

Self-contained (no network): canvas charts drawn lazily from embedded JSON, labels kept in the
browser's localStorage, "Download CSV" exports ``id,window,label,notes``. ``__TITLE__`` and
``__DATA__`` are replaced when the sheet is written.
"""

SHEET = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root { --bg:#f7f7f5; --card:#fff; --ink:#1d1d1f; --muted:#6b6b70; --line:#e2e2e0;
        --up:#2f7d4f; --price:#1d1d1f; --ma50:#2563eb; --ma150:#d97706; --vol:#9ca3af; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#141416; --card:#1d1d20; --ink:#ececee; --muted:#9a9aa1; --line:#2e2e33;
          --price:#ececee; --vol:#5b5b63; }
}
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
       font:15px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif; }
header { position:sticky; top:0; z-index:5; background:var(--bg); border-bottom:1px solid var(--line);
         padding:12px 16px; display:flex; flex-wrap:wrap; gap:10px 18px; align-items:center; }
header h1 { font-size:17px; margin:0 12px 0 0; }
header .counts { color:var(--muted); font-size:13px; }
button { font:inherit; padding:6px 12px; border-radius:6px; border:1px solid var(--line);
         background:var(--card); color:var(--ink); cursor:pointer; }
button.primary { background:var(--ink); color:var(--bg); border-color:var(--ink); }
main { max-width:1000px; margin:0 auto; padding:16px; }
.help { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:12px 16px;
        margin-bottom:16px; font-size:14px; }
.help dt { font-weight:600; } .help dd { margin:0 0 6px 0; color:var(--muted); }
.card { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:12px;
        margin-bottom:16px; }
.card.done { border-left:4px solid var(--up); }
.card h2 { font-size:15px; margin:0 0 8px; display:flex; gap:12px; align-items:baseline; }
.card h2 .meta { color:var(--muted); font-weight:400; font-size:13px; }
canvas { width:100%; height:auto; display:block; }
.row { display:flex; flex-wrap:wrap; gap:8px 12px; margin-top:8px; align-items:center; }
select, input[type=text] { font:inherit; padding:6px 8px; border-radius:6px;
        border:1px solid var(--line); background:var(--bg); color:var(--ink); }
input[type=text] { flex:1 1 260px; }
.legend { color:var(--muted); font-size:12px; }
.legend b.m50 { color:var(--ma50); } .legend b.m150 { color:var(--ma150); }
.hidden { display:none; }
</style></head><body>
<header>
  <h1>__TITLE__</h1>
  <span class="counts" id="counts"></span>
  <label><input type="checkbox" id="reveal"> show symbol and date</label>
  <label><input type="checkbox" id="todo"> unlabelled only</label>
  <button class="primary" id="csv">Download CSV</button>
</header>
<main>
__HELP__
<div id="list"></div>
</main>
<script>
const DATA = __DATA__;
const LABELS = __OPTIONS__;
const KEY = "vcp-labels:" + document.title;
let store = {};
try { store = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) { store = {}; }
function save() { try { localStorage.setItem(KEY, JSON.stringify(store)); } catch (e) {} counts(); }
function counts() {
  const c = {}; let n = 0;
  for (const w of DATA) { const l = (store[w.id] || {}).label; if (l) { c[l] = (c[l] || 0) + 1; n++; } }
  document.getElementById("counts").textContent = n + " of " + DATA.length + " labelled · " +
    LABELS.slice(1).map(([k, t]) => t + " " + (c[k] || 0)).join(" · ");
  for (const w of DATA) {
    const el = document.getElementById("card-" + w.id);
    const has = !!(store[w.id] || {}).label;
    el.classList.toggle("done", has);
    el.classList.toggle("hidden", document.getElementById("todo").checked && has);
  }
}
function sma(a, n) { const o = []; let s = 0;
  for (let i = 0; i < a.length; i++) { s += a[i]; if (i >= n) s -= a[i - n]; o.push(i >= n - 1 ? s / n : null); } return o; }
function css(v) { return getComputedStyle(document.documentElement).getPropertyValue(v).trim(); }
function draw(cv, w) {
  const dpr = window.devicePixelRatio || 1, W = 960, H = 380;
  cv.width = W * dpr; cv.height = H * dpr; const g = cv.getContext("2d"); g.scale(dpr, dpr);
  const n = w.c.length, padR = 60, padB = 22, volH = 80, top = 8;
  const priceH = H - padB - volH - top - 6, x = i => (i + 0.5) * (W - padR) / n;
  const lo = Math.min(...w.l), hi = Math.max(...w.h);
  const y = p => top + (hi - p) / (hi - lo || 1) * priceH;
  g.strokeStyle = css("--line"); g.fillStyle = css("--muted"); g.font = "11px system-ui"; g.lineWidth = 1;
  for (let k = 0; k <= 4; k++) { const p = lo + (hi - lo) * k / 4, yy = y(p);
    g.beginPath(); g.moveTo(0, yy); g.lineTo(W - padR, yy); g.stroke(); g.fillText(p.toFixed(p < 100 ? 2 : 0), W - padR + 6, yy + 4); }
  let lastM = "";
  for (let i = 0; i < n; i++) { const m = w.d[i].slice(0, 7);
    if (m !== lastM) { lastM = m; g.fillText(m.slice(2), x(i) - 10, H - 6); } }
  g.strokeStyle = css("--price");
  for (let i = 0; i < n; i++) { g.beginPath(); g.moveTo(x(i), y(w.h[i])); g.lineTo(x(i), y(w.l[i]));
    g.moveTo(x(i), y(w.c[i])); g.lineTo(x(i) + 2, y(w.c[i])); g.stroke(); }
  for (const [len, col] of [[50, "--ma50"], [150, "--ma150"]]) { const s = sma(w.c, len);
    g.strokeStyle = css(col); g.lineWidth = 1.4; g.beginPath(); let on = false;
    s.forEach((v, i) => { if (v == null) return; if (!on) { g.moveTo(x(i), y(v)); on = true; } else g.lineTo(x(i), y(v)); });
    g.stroke(); g.lineWidth = 1; }
  if (w.m) {
    const m = w.m; g.font = "11px system-ui";
    if (m.bs >= 0) { g.strokeStyle = css("--muted"); g.setLineDash([4, 4]); g.beginPath();
      g.moveTo(x(m.bs), top); g.lineTo(x(m.bs), top + priceH); g.stroke(); g.setLineDash([]);
      g.fillStyle = css("--muted"); g.fillText("base start", x(m.bs) + 4, top + 12); }
    m.t.forEach((c, k) => {
      g.fillStyle = "#c2410c"; g.strokeStyle = "#c2410c";
      if (c.p >= 0) { g.beginPath(); g.arc(x(c.p), y(c.ph), 4, 0, 7); g.fill();
        g.fillText("T" + (k + 1), x(c.p) - 6, y(c.ph) - 8); }
      if (c.q >= 0) { g.beginPath(); g.arc(x(c.q), y(c.ql), 4, 0, 7); g.stroke();
        g.fillText(c.d.toFixed(1) + "%", x(c.q) - 12, y(c.ql) + 16); }
      if (c.p >= 0 && c.q >= 0) { g.setLineDash([2, 3]); g.beginPath(); g.moveTo(x(c.p), y(c.ph));
        g.lineTo(x(c.q), y(c.ql)); g.stroke(); g.setLineDash([]); }
    });
    if (m.pv) { g.strokeStyle = "#7c3aed"; g.setLineDash([6, 4]); g.beginPath();
      g.moveTo(0, y(m.pv)); g.lineTo(W - padR, y(m.pv)); g.stroke(); g.setLineDash([]);
      g.fillStyle = "#7c3aed"; g.fillText("pivot " + m.pv.toFixed(2), 6, y(m.pv) - 4); }
  }
  const vols = w.v.map(v => v || 0), vmax = Math.max(...vols) || 1, vb = H - padB, bw = Math.max(1, (W - padR) / n - 1);
  g.fillStyle = css("--vol");
  vols.forEach((v, i) => { const hh = v / vmax * volH; g.fillRect(x(i) - bw / 2, vb - hh, bw, hh); });
}
const list = document.getElementById("list");
for (const w of DATA) {
  const card = document.createElement("section"); card.className = "card"; card.id = "card-" + w.id;
  const opts = LABELS.map(([k, t]) => `<option value="${k}">${t}</option>`).join("");
  card.innerHTML = `<h2>Window ${w.n}<span class="meta reveal${w.m ? "" : " hidden"}">${w.sym} · ${w.asof}</span></h2>
    ${w.m ? `<div class="legend">${w.m.txt}</div>` : ""}
    <canvas width="960" height="380"></canvas>
    <div class="legend">close tick · <b class="m50">50-day</b> · <b class="m150">150-day</b> · volume</div>
    <div class="row"><select>${opts}</select><input type="text" placeholder="notes (optional)"></div>`;
  list.appendChild(card);
  const sel = card.querySelector("select"), note = card.querySelector("input");
  const s = store[w.id] || {}; sel.value = s.label || ""; note.value = s.notes || "";
  sel.addEventListener("change", () => { store[w.id] = { ...(store[w.id] || {}), label: sel.value }; save(); });
  note.addEventListener("input", () => { store[w.id] = { ...(store[w.id] || {}), notes: note.value }; save(); });
}
const io = new IntersectionObserver(es => es.forEach(e => {
  if (!e.isIntersecting) return; const card = e.target; io.unobserve(card);
  draw(card.querySelector("canvas"), DATA.find(w => "card-" + w.id === card.id)); }), { rootMargin: "400px" });
document.querySelectorAll(".card").forEach(c => io.observe(c));
document.getElementById("reveal").addEventListener("change", e =>
  document.querySelectorAll(".reveal").forEach(x => x.classList.toggle("hidden", !e.target.checked)));
document.getElementById("todo").addEventListener("change", counts);
document.getElementById("csv").addEventListener("click", () => {
  const q = s => '"' + String(s || "").replace(/"/g, '""') + '"';
  const rows = [["id", "window", "label", "notes"].join(",")].concat(DATA.map(w => {
    const s = store[w.id] || {}; return [w.id, w.n, s.label || "", q(s.notes)].join(","); }));
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([rows.join("\n") + "\n"], { type: "text/csv" }));
  a.download = "vcp_labels.csv"; a.click();
});
counts();
</script></body></html>
"""


#: The labelling sheet's help block and options (``labelling.write_outputs``).
LABEL_HELP = r"""<dl class="help">
  <dt>How to label</dt>
  <dd>Each chart ends at the window's date (right edge); judge the setup as it looked that day.
      Price bars show each day's high-low range with a tick at the close; lines are the 50-day
      (blue) and 150-day (amber) averages; grey bars are volume. Your labels are kept in this
      browser; use Download CSV when done (or at any time) and send the file back.</dd>
  <dt>A+ VCP</dt><dd>Textbook: three or more contractions, each clearly tighter, volume drying up, a tight right side just under the pivot.</dd>
  <dt>VCP</dt><dd>A proper VCP that is not textbook (two contractions, or one criterion weaker).</dd>
  <dt>VCP-like</dt><dd>Resembles a VCP but too loose, too deep or not tightening enough to act on.</dd>
  <dt>Non-VCP</dt><dd>No volatility contraction pattern (extended run, V-shaped recovery, flat drift, wide and loose).</dd>
  <dt>Failed VCP</dt><dd>A VCP that has already broken down or failed its breakout by the window's date.</dd>
  <dt>Ambiguous</dt><dd>You cannot decide; excluded from the accuracy numbers.</dd>
</dl>"""
LABEL_OPTIONS = (
    '[["", "-- label --"], ["confirmed_a_plus", "A+ VCP"], ["confirmed_vcp", "VCP"], '
    '["vcp_like", "VCP-like"], ["non_vcp", "Non-VCP"], ["failed_vcp", "Failed VCP"], '
    '["ambiguous", "Ambiguous"]]'
)
