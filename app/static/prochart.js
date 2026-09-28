/* Pro chart pop-up: opens instantly on click, TradingView Lightweight Charts™ candlesticks, line/area/bar modes,
   1D/5D intraday to 20-year monthly candles, volume, SMA 20/50/200, Bollinger bands, crosshair OHLC legend,
   zoom & pan, full-screen analysis mode. Data per symbol+interval is cached, so switching ranges is instant. */
const PC = {
  cache: {}, inflight: {}, token: 0, chart: null, series: {}, sym: null, data: null,
  range: "1Y", interval: "auto", type: "candles",
  ind: { vol: true, sma20: false, sma50: true, sma200: false, bb: false },
};
try {
  const saved = JSON.parse(localStorage.getItem("pm.chart") || "{}");
  if (saved.type) PC.type = saved.type;
  if (saved.ind) Object.assign(PC.ind, saved.ind);
  if (saved.range) PC.range = saved.range;
} catch (e) { /* storage unavailable */ }
const savePC = () => { try { localStorage.setItem("pm.chart", JSON.stringify({ type: PC.type, ind: PC.ind, range: PC.range })); } catch (e) { /* ignore */ } };

const RANGES = [["1D", "5m"], ["5D", "15m"], ["1M", "60m"], ["3M", "1d"], ["6M", "1d"], ["YTD", "1d"], ["1Y", "1d"], ["3Y", "1wk"], ["5Y", "1wk"], ["MAX", "1mo"]];
const INTERVALS = [["auto", "Auto"], ["5m", "5 min"], ["15m", "15 min"], ["60m", "1 hour"], ["1d", "Daily"], ["1wk", "Weekly"], ["1mo", "Monthly"]];
const IV_LABEL = { "5m": "5-minute", "15m": "15-minute", "60m": "hourly", "1d": "daily", "1wk": "weekly", "1mo": "monthly" };
const UP = "#16a34a", DOWN = "#DD2F20";
const isIntra = iv => ["5m", "15m", "60m"].includes(iv);
const ivFor = rng => PC.interval !== "auto" ? PC.interval : (RANGES.find(r => r[0] === rng) || RANGES[6])[1];

function fetchCandles(sym, iv) {
  const key = sym + "|" + iv;
  if (PC.cache[key] && Date.now() - PC.cache[key].at < (isIntra(iv) ? 60e3 : 15 * 60e3)) return Promise.resolve(PC.cache[key].d);
  if (PC.inflight[key]) return PC.inflight[key];
  PC.inflight[key] = api(`/markets/${encodeURIComponent(sym)}/candles?interval=${iv}`)
    .then(d => { PC.cache[key] = { d, at: Date.now() }; return d; })
    .finally(() => { delete PC.inflight[key]; });
  return PC.inflight[key];
}
// warm the cache when the pointer rests on a row, so the pop-up is instant
let prefetchTimer = null;
document.addEventListener("mouseover", e => {
  const el = e.target.closest && e.target.closest("[data-s]");
  if (!el || el.closest(".pc-modal")) return;
  clearTimeout(prefetchTimer);
  prefetchTimer = setTimeout(() => fetchCandles(el.dataset.s, ivFor(PC.range)).catch(() => {}), 180);
});

// ---------------------------------------------------------------- indicators
function sma(c, n) {
  const out = []; let sum = 0;
  for (let i = 0; i < c.length; i++) {
    sum += c[i].c; if (i >= n) sum -= c[i - n].c;
    if (i >= n - 1) out.push({ time: c[i].t, value: sum / n });
  }
  return out;
}
function bollinger(c, n = 20, k = 2) {
  const up = [], lo = [];
  for (let i = n - 1; i < c.length; i++) {
    const w = c.slice(i - n + 1, i + 1).map(x => x.c), m = w.reduce((a, b) => a + b, 0) / n;
    const sd = Math.sqrt(w.reduce((a, b) => a + (b - m) ** 2, 0) / n);
    up.push({ time: c[i].t, value: m + k * sd }); lo.push({ time: c[i].t, value: m - k * sd });
  }
  return { up, lo };
}
function precisionFor(p) { return p == null ? 2 : p < 2 ? 4 : p < 20 ? 3 : 2; }
function fmtPx(v, p) { return v == null || isNaN(v) ? "—" : Number(v).toLocaleString(undefined, { minimumFractionDigits: precisionFor(p), maximumFractionDigits: precisionFor(p) }); }
function fmtT(t, intra) {
  if (intra) { const d = new Date(t * 1000); return d.toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" }) + " " + d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", timeZone: "UTC" }); }
  return new Date(t + "T00:00:00Z").toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" });
}
function rangeStart(rng, lastT) {
  const end = new Date(lastT + "T00:00:00Z");
  const d = new Date(end);
  if (rng === "YTD") return `${end.getUTCFullYear()}-01-01`;
  const m = { "1M": 1, "3M": 3, "6M": 6, "1Y": 12, "3Y": 36, "5Y": 60 }[rng];
  if (!m) return null;
  d.setUTCMonth(d.getUTCMonth() - m);
  return d.toISOString().slice(0, 10);
}

// ---------------------------------------------------------------- modal
async function openDetail(symbol, rng) {
  symbol = String(symbol).toUpperCase();
  if (rng) PC.range = rng;
  const known = (typeof MK !== "undefined" && MK.rows && MK.rows[symbol]) || {};
  closeDetail(true);
  PC.sym = symbol;
  const m = document.createElement("div");
  m.className = "modal pc-modal";
  m.innerHTML = `<div class="modal-card pc-card">
    <div class="pc-head">
      <div><div class="eyebrow">${esc(known.kind || "Security")} · ${esc(symbol)}</div>
        <h2 class="pc-name">${esc(known.name || symbol)}</h2>
        <div class="pc-quote"><span class="pc-px">${known.price != null ? fmtPx(known.price, known.price) : "…"}</span>
          <span class="pc-chg ${cls(known.change_pct)}">${known.price != null ? `${known.change_pct >= 0 ? "▲" : "▼"} ${pct(Math.abs(known.change_pct || 0), 2)} today` : ""}</span></div></div>
      <div class="pc-actions">
        <button class="btn sm" id="pc-add">★ Watchlist</button>
        ${/^\^|=|USD$|\.SS$/.test(symbol) ? "" : `<a class="btn sm" href="#/research/${encodeURIComponent(symbol)}">Research →</a>`}
        <button class="btn sm" id="pc-full" title="Full-screen analysis">⤢ Expand</button>
        <button class="btn sm" id="pc-close" title="Close (Esc)">✕</button>
      </div>
    </div>
    <div class="pc-toolbar">
      <div class="pc-ranges">${RANGES.map(([r]) => `<button class="pillbtn ${r === PC.range ? "on" : ""}" data-pcr="${r}">${r}</button>`).join("")}</div>
      <div class="pc-tools">
        <select id="pc-iv" title="Candle interval">${INTERVALS.map(([k, l]) => `<option value="${k}" ${k === PC.interval ? "selected" : ""}>${l}</option>`).join("")}</select>
        <div class="seg" id="pc-type">${[["candles", "Candles"], ["bars", "OHLC"], ["line", "Line"], ["area", "Area"]].map(([k, l]) => `<button data-t="${k}" class="${PC.type === k ? "on" : ""}">${l}</button>`).join("")}</div>
      </div>
    </div>
    <div class="pc-ind">${[["vol", "Volume"], ["sma20", "SMA 20"], ["sma50", "SMA 50"], ["sma200", "SMA 200"], ["bb", "Bollinger"]].map(([k, l]) => `<button class="chipbtn ${PC.ind[k] ? "on" : ""} i-${k}" data-ind="${k}">${l}</button>`).join("")}
      <span class="pc-note" id="pc-note"></span></div>
    <div class="pc-chartwrap"><div class="pc-legend" id="pc-legend"></div><div class="pc-chart" id="pc-chart"></div>
      <div class="pc-loading" id="pc-loading"><span></span>Loading ${esc(symbol)}…</div></div>
    <div class="pc-stats" id="pc-stats"></div>
    <div class="pc-news" id="pc-news"></div>
  </div>`;
  document.body.appendChild(m);
  document.body.classList.add("pc-open");
  m.addEventListener("click", e => { if (e.target === m) closeDetail(); });
  document.addEventListener("keydown", pcKeys);
  m.querySelectorAll("[data-pcr]").forEach(b => b.onclick = () => { PC.range = b.dataset.pcr; savePC(); markOn(m, "[data-pcr]", b); loadAndRender(); });
  m.querySelector("#pc-iv").onchange = e => { PC.interval = e.target.value; loadAndRender(); };
  m.querySelectorAll("#pc-type button").forEach(b => b.onclick = () => { PC.type = b.dataset.t; savePC(); markOn(m, "#pc-type button", b); render(false); });
  m.querySelectorAll("[data-ind]").forEach(b => b.onclick = () => { PC.ind[b.dataset.ind] = !PC.ind[b.dataset.ind]; savePC(); b.classList.toggle("on"); render(false); });
  m.querySelector("#pc-close").onclick = () => closeDetail();
  m.querySelector("#pc-full").onclick = () => { m.classList.toggle("full"); m.querySelector("#pc-full").textContent = m.classList.contains("full") ? "⤡ Exit" : "⤢ Expand"; };
  m.querySelector("#pc-add").onclick = () => addToWatchlist(symbol);
  requestAnimationFrame(() => m.classList.add("in"));
  loadAndRender();
  if (window.loadTickerNews) loadTickerNews(symbol, m.querySelector("#pc-news"));
}
function markOn(m, sel, btn) { m.querySelectorAll(sel).forEach(x => x.classList.toggle("on", x === btn)); }
function pcKeys(e) {
  if (e.key === "Escape") closeDetail();
  if (!document.querySelector(".pc-modal") || /INPUT|SELECT|TEXTAREA/.test((document.activeElement || {}).tagName)) return;
  const i = RANGES.findIndex(r => r[0] === PC.range);
  if (e.key === "ArrowRight" && i < RANGES.length - 1) document.querySelector(`[data-pcr="${RANGES[i + 1][0]}"]`).click();
  if (e.key === "ArrowLeft" && i > 0) document.querySelector(`[data-pcr="${RANGES[i - 1][0]}"]`).click();
}
function closeDetail(silent) {
  const m = document.querySelector(".pc-modal");
  if (PC.chart) { try { PC.chart.remove(); } catch (e) { /* already gone */ } PC.chart = null; PC.series = {}; }
  if (m) m.remove();
  document.body.classList.remove("pc-open");
  document.removeEventListener("keydown", pcKeys);
  PC.token++;
}
window.addEventListener("hashchange", () => closeDetail());

async function loadAndRender() {
  const sym = PC.sym, iv = ivFor(PC.range), tok = ++PC.token;
  const loading = document.getElementById("pc-loading");
  const key = sym + "|" + iv;
  const cached = PC.cache[key];
  if (!cached && loading) loading.classList.add("on");
  try {
    const d = await fetchCandles(sym, iv);
    if (tok !== PC.token || sym !== PC.sym) return;                       // a newer click won - ignore stale data
    const changedSeries = !PC.data || PC.data.interval !== d.interval || PC.data.symbol !== d.symbol;
    PC.data = d; PC.view = null;
    header(d);
    render(changedSeries);
  } catch (e) {
    if (tok !== PC.token) return;
    const el = document.getElementById("pc-chart");
    if (el) el.innerHTML = `<div class="pc-err">${esc(e.message)}</div>`;
  } finally {
    if (tok === PC.token && loading) loading.classList.remove("on");
  }
}
function header(d) {
  const m = document.querySelector(".pc-modal"); if (!m) return;
  m.querySelector(".pc-name").textContent = d.name || d.symbol;
  if (d.price != null) {
    m.querySelector(".pc-px").textContent = fmtPx(d.price, d.price) + (d.currency && d.currency !== "USD" ? " " + d.currency : "");
    const c = m.querySelector(".pc-chg");
    c.className = "pc-chg " + cls(d.change_pct);
    c.textContent = `${d.change_pct >= 0 ? "▲" : "▼"} ${d.change != null ? fmtPx(Math.abs(d.change), d.price) : ""} (${pct(Math.abs(d.change_pct), 2)}) today`;
  }
}

// ---------------------------------------------------------------- rendering
function ensureChart() {
  const host = document.getElementById("pc-chart");
  if (!host) return null;
  if (PC.chart) return PC.chart;
  host.innerHTML = "";
  const dark = true;
  PC.chart = LightweightCharts.createChart(host, {
    autoSize: true,
    layout: { background: { type: "solid", color: "#0b0b0b" }, textColor: "#a8a8a8", fontFamily: "Raleway, system-ui, sans-serif", fontSize: 12, attributionLogo: true },
    grid: { vertLines: { color: dark ? "#161616" : "#eee" }, horzLines: { color: dark ? "#161616" : "#eee" } },
    rightPriceScale: { borderColor: "#262626", scaleMargins: { top: 0.08, bottom: 0.22 } },
    timeScale: { borderColor: "#262626", rightOffset: 4, barSpacing: 8, minBarSpacing: 1 },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal, vertLine: { color: "#DD2F20", labelBackgroundColor: "#DD2F20", width: 1, style: 2 }, horzLine: { color: "#555", labelBackgroundColor: "#333" } },
    localization: { locale: "en-US", priceFormatter: p => fmtPx(p, PC.data ? PC.data.price || p : p) },
  });
  PC.chart.subscribeCrosshairMove(param => legend(param && param.time));
  ["wheel", "mousedown", "touchstart"].forEach(ev => host.addEventListener(ev, () => { PC.userMoved = true; }, { passive: true }));
  PC.chart.timeScale().subscribeVisibleLogicalRangeChange(lr => {
    if (!lr || !PC.userMoved) return;                                        // only the user's own zoom/pan changes the stats window
    PC.view = { a: lr.from, b: lr.to }; stats();
  });
  return PC.chart;
}
function render(resetView) {
  const d = PC.data;
  if (!d) return;
  const chart = ensureChart();
  if (!chart) return;
  Object.values(PC.series).forEach(s => { try { chart.removeSeries(s); } catch (e) { /* ignore */ } });
  PC.series = {};
  const C = d.candles, L = LightweightCharts;
  const prec = precisionFor(d.price || (C.length ? C[C.length - 1].c : 1));
  const pf = { type: "price", precision: prec, minMove: Math.pow(10, -prec) };
  chart.applyOptions({ timeScale: { timeVisible: d.intraday, secondsVisible: false } });
  const ohlc = C.map(x => ({ time: x.t, open: x.o, high: x.h, low: x.l, close: x.c }));
  let main;
  if (PC.type === "candles") main = chart.addSeries(L.CandlestickSeries, { upColor: UP, downColor: DOWN, borderUpColor: UP, borderDownColor: DOWN, wickUpColor: UP, wickDownColor: DOWN, priceFormat: pf });
  else if (PC.type === "bars") main = chart.addSeries(L.BarSeries, { upColor: UP, downColor: DOWN, thinBars: false, priceFormat: pf });
  else if (PC.type === "line") main = chart.addSeries(L.LineSeries, { color: "#ffffff", lineWidth: 2, priceFormat: pf });
  else main = chart.addSeries(L.AreaSeries, { lineColor: "#DD2F20", topColor: "rgba(221,47,32,.35)", bottomColor: "rgba(221,47,32,0)", lineWidth: 2, priceFormat: pf });
  main.setData(PC.type === "candles" || PC.type === "bars" ? ohlc : C.map(x => ({ time: x.t, value: x.c })));
  PC.series.main = main;
  if (d.prev_close && d.intraday && PC.range === "1D") main.createPriceLine({ price: d.prev_close, color: "#777", lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: "prev close" });
  if (PC.ind.vol && C.some(x => x.v > 0)) {
    const v = chart.addSeries(L.HistogramSeries, { priceFormat: { type: "volume" }, priceScaleId: "vol", lastValueVisible: false, priceLineVisible: false });
    v.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    v.setData(C.map(x => ({ time: x.t, value: x.v, color: x.c >= x.o ? "rgba(22,163,74,.45)" : "rgba(221,47,32,.45)" })));
    PC.series.vol = v;
  }
  const line = (data, color, w = 1.5, style = 0) => { const s = chart.addSeries(L.LineSeries, { color, lineWidth: w, lineStyle: style, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, priceFormat: pf }); s.setData(data); return s; };
  if (PC.ind.sma20 && C.length > 20) PC.series.sma20 = line(sma(C, 20), "#f5c542");
  if (PC.ind.sma50 && C.length > 50) PC.series.sma50 = line(sma(C, 50), "#4aa3ff");
  if (PC.ind.sma200 && C.length > 200) PC.series.sma200 = line(sma(C, 200), "#c084fc", 2);
  if (PC.ind.bb && C.length > 20) { const b = bollinger(C); PC.series.bbu = line(b.up, "rgba(255,255,255,.45)", 1, 2); PC.series.bbl = line(b.lo, "rgba(255,255,255,.45)", 1, 2); }
  setView();
  stats();
  // the chart sizes itself asynchronously on first paint - re-apply the window once layout has settled
  requestAnimationFrame(() => requestAnimationFrame(() => { if (PC.data === d) { setView(); stats(); } }));
  setTimeout(() => { if (PC.data === d) { setView(); stats(); } }, 180);
  legend(null);
  const note = document.getElementById("pc-note");
  if (note) note.textContent = `${IV_LABEL[d.interval] || d.interval} candles · ${C.length.toLocaleString()} bars · scroll to zoom, drag to pan${d.intraday ? " · times are exchange-local" : ""}`;
}
function setView() {
  const d = PC.data, C = d.candles, ts = PC.chart.timeScale();
  if (!C.length) return;
  const from = d.intraday || PC.range === "MAX" ? null : rangeStart(PC.range, C[C.length - 1].t);
  let i = from ? C.findIndex(x => x.t >= from) : 0;
  if (i < 0) i = 0;
  PC.view = { a: i, b: C.length - 1 };
  PC.userMoved = false;
  if (!from) ts.fitContent();
  else ts.setVisibleLogicalRange({ from: i - 0.5, to: C.length - 1 + 3 });
}

// crosshair legend (OHLC of the bar under the cursor, or the last bar)
function legend(time) {
  const d = PC.data, el = document.getElementById("pc-legend");
  if (!d || !el || !d.candles.length) return;
  const C = d.candles;
  let i = C.length - 1;
  if (time != null) { const j = C.findIndex(x => x.t === time); if (j >= 0) i = j; }
  const x = C[i], prev = i > 0 ? C[i - 1].c : x.o, ch = x.c / prev - 1, p = d.price || x.c;
  el.innerHTML = `<b>${esc(fmtT(x.t, d.intraday))}</b>
    <span>O <i>${fmtPx(x.o, p)}</i></span><span>H <i>${fmtPx(x.h, p)}</i></span><span>L <i>${fmtPx(x.l, p)}</i></span><span>C <i>${fmtPx(x.c, p)}</i></span>
    <span class="${ch >= 0 ? "up" : "dn"}">${ch >= 0 ? "+" : ""}${(ch * 100).toFixed(2)}%</span>${x.v ? `<span>Vol <i>${big(x.v)}</i></span>` : ""}
    ${PC.ind.sma20 ? `<span class="k20">SMA20</span>` : ""}${PC.ind.sma50 ? `<span class="k50">SMA50</span>` : ""}${PC.ind.sma200 ? `<span class="k200">SMA200</span>` : ""}`;
}
// stats for whatever is visible (updates as you zoom and pan)
function stats() {
  const d = PC.data, el = document.getElementById("pc-stats");
  if (!d || !el || !PC.chart) return;
  const C = d.candles, lr = PC.view;
  let a = 0, b = C.length - 1;
  if (lr) { a = Math.max(0, Math.ceil(lr.a)); b = Math.min(C.length - 1, Math.floor(lr.b)); }
  if (b <= a) return;
  const seg = C.slice(a, b + 1), first = seg[0], last = seg[seg.length - 1];
  const ret = last.c / first.o - 1;
  const hi = Math.max(...seg.map(x => x.h)), lo = Math.min(...seg.map(x => x.l));
  let peak = -Infinity, mdd = 0;
  seg.forEach(x => { peak = Math.max(peak, x.c); mdd = Math.min(mdd, x.c / peak - 1); });
  const rets = seg.slice(1).map((x, i) => Math.log(x.c / seg[i].c));
  const mean = rets.reduce((s, r) => s + r, 0) / (rets.length || 1);
  const sd = Math.sqrt(rets.reduce((s, r) => s + (r - mean) ** 2, 0) / Math.max(1, rets.length - 1));
  const perYear = { "5m": 252 * 78, "15m": 252 * 26, "60m": 252 * 7, "1d": 252, "1wk": 52, "1mo": 12 }[d.interval] || 252;
  const vol = sd * Math.sqrt(perYear);
  const avgV = seg.reduce((s, x) => s + (x.v || 0), 0) / seg.length;
  const p = d.price || last.c;
  el.innerHTML = [
    ["Visible period", `${fmtT(first.t, d.intraday)} → ${fmtT(last.t, d.intraday)}`],
    ["Return", `<b class="${cls(ret)}">${ret >= 0 ? "+" : ""}${(ret * 100).toFixed(2)}%</b>`],
    ["High / Low", `${fmtPx(hi, p)} / ${fmtPx(lo, p)}`],
    ["Max drawdown", `<b class="neg">${(mdd * 100).toFixed(1)}%</b>`],
    ["Volatility (ann.)", isFinite(vol) ? pct(vol, 1) : "—"],
    ["Avg volume", avgV ? big(avgV) : "—"],
    ["52-week range", d.year_low != null ? `${fmtPx(d.year_low, p)} – ${fmtPx(d.year_high, p)}` : "—"],
  ].map(([k, v]) => `<div><span>${k}</span><div>${v}</div></div>`).join("");
}
window.openDetail = openDetail;
window.closeDetail = closeDetail;
