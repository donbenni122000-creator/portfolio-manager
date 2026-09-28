/* Efficient Frontier Lab (modern portfolio theory) — lives in the Model marketplace at #/models/frontier.
   Add stocks/ETFs, optionally your own weights, and see the efficient frontier, the Capital Market Line,
   min-variance and max-Sharpe portfolios, where each holding and your mix sit, and save any point as a model. */
const FL = { items: [], lookback: "3y", method: "blend", maxw: 0.4, shrink: true, res: null, sel: null, chart: null };
const FL_COLORS = ["#DD2F20", "#111111", "#2F6FB5", "#E39B2D", "#2E9E6B", "#7A4FB3", "#8f8f8f", "#C2185B", "#00838F",
  "#6D4C41", "#F2685A", "#455A64", "#9E9D24", "#5C6BC0", "#D81B60", "#26A69A", "#8D6E63", "#FFA726", "#78909C", "#AB47BC",
  "#43A047", "#EF5350", "#1E88E5", "#FDD835", "#3a3a3a"];
const FL_SETS = {
  "US mega-cap tech": ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META"],
  "Diversified ETFs": ["VTI", "VXUS", "BND", "TLT", "GLD", "VNQ"],
  "India leaders": ["RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS", "BHARTIARTL.NS"],
  "Stocks + bonds + gold": ["AAPL", "JPM", "XOM", "JNJ", "BND", "GLD"],
};
function flLoad() {
  try { Object.assign(FL, JSON.parse(localStorage.getItem("pm.frontier") || "{}"), { res: null, sel: null, chart: null }); } catch (e) { /* ignore */ }
}
function flSave() {
  try { localStorage.setItem("pm.frontier", JSON.stringify({ items: FL.items, lookback: FL.lookback, method: FL.method, maxw: FL.maxw, shrink: FL.shrink })); } catch (e) { /* ignore */ }
}

let flPrefetchT = null;
function flPrefetch() {
  clearTimeout(flPrefetchT);
  flPrefetchT = setTimeout(() => {
    if (FL.items.length) api("/frontier/prefetch", { method: "POST", body: { symbols: FL.items.map(it => it.s), lookback: FL.lookback } }).catch(() => {});
  }, 300);
}
let flAutoT = null;
function flAuto(delay = 250) { if (FL.items.length < 2) return; clearTimeout(flAutoT); flAutoT = setTimeout(runFrontier, delay); }

async function frontierView() {
  flLoad();
  const q = new URLSearchParams((location.hash.split("?")[1]) || "");
  const models = await api("/models").catch(() => []);
  if (q.get("model")) {
    const m = models.find(x => String(x.id) === q.get("model"));
    if (m) FL.items = Object.entries(m.holdings).map(([s, w]) => ({ s, w: Math.round(w * 1000) / 10 }));
  }
  if (!FL.items.length) FL.items = FL_SETS["Stocks + bonds + gold"].map(s => ({ s, w: "" }));
  view.innerHTML = `<a href="#/models" class="small">← Model marketplace</a>
    <section class="hero-dark fl-hero">
      <div class="hero-copy"><div class="eyebrow light">Modern portfolio theory · Markowitz</div>
        <h1 class="display">Efficient<br>Frontier Lab.</h1>
        <p>Add any stocks or ETFs and see every efficient mix: the frontier curve, the Capital Market Line, the minimum-variance and maximum-Sharpe portfolios, and exactly how far your own mix sits from efficient. Save any point as a model.</p></div>
      <div class="fl-hero-art" aria-hidden="true"><svg viewBox="0 0 300 200"><defs><linearGradient id="flg" x1="0" x2="1"><stop offset="0" stop-color="#DD2F20" stop-opacity=".2"/><stop offset="1" stop-color="#DD2F20"/></linearGradient></defs>
        ${Array.from({ length: 70 }, (_, i) => { const x = 40 + ((i * 53) % 230), y = 170 - ((i * 37) % 110) * (x / 270); return `<circle cx="${x}" cy="${Math.max(30, y)}" r="2" fill="#fff" opacity=".18"/>`; }).join("")}
        <path d="M60 170 C 70 110, 120 60, 280 38" fill="none" stroke="url(#flg)" stroke-width="4" stroke-linecap="round"/>
        <path d="M10 150 L 290 40" stroke="#fff" stroke-dasharray="5 6" opacity=".45"/><circle cx="160" cy="72" r="7" fill="#DD2F20" stroke="#fff" stroke-width="2"/></svg></div>
    </section>
    <div class="fl-grid">
      <div class="card fl-controls">
        <h3>1 · Pick your universe</h3>
        <div class="fl-add r-field"><input id="fl-in" placeholder="Add a ticker or company, e.g. NVDA, Infosys" autocomplete="off" spellcheck="false" data-gramm="false" data-enable-grammarly="false"><button class="btn primary sm" id="fl-add">Add</button><div class="r-suggest" id="fl-sug"></div></div>
        <div class="fl-presets"><select id="fl-model"><option value="">Load a model…</option>${models.map(m => `<option value="${m.id}">${esc(m.name)}</option>`).join("")}</select>
          ${Object.keys(FL_SETS).map(k => `<button class="chipbtn" data-set="${esc(k)}">${esc(k)}</button>`).join("")}</div>
        <table class="fl-items"><thead><tr><th>Holding</th><th class="num">My weight %</th><th></th></tr></thead><tbody id="fl-rows"></tbody>
          <tfoot><tr><td class="muted small">Optional: enter your current weights to see how efficient your mix is.</td><td class="num mono" id="fl-wsum"></td><td></td></tr></tfoot></table>
        <h3 style="margin-top:18px">2 · Assumptions</h3>
        <div class="fl-opts">
          <label>History<select id="fl-lb">${[["1y", "1 year"], ["3y", "3 years"], ["5y", "5 years"]].map(([v, l]) => `<option value="${v}" ${FL.lookback === v ? "selected" : ""}>${l}</option>`).join("")}</select></label>
          <label>Expected returns<select id="fl-meth">${[["blend", "Blend (history + CAPM)"], ["historical", "Historical average"], ["capm", "CAPM equilibrium"]].map(([v, l]) => `<option value="${v}" ${FL.method === v ? "selected" : ""}>${l}</option>`).join("")}</select></label>
          <label>Max per holding<select id="fl-mw">${[0.15, 0.2, 0.25, 0.3, 0.4, 0.5, 0.6, 1].map(v => `<option value="${v}" ${Math.abs(FL.maxw - v) < 1e-6 ? "selected" : ""}>${v === 1 ? "No limit" : Math.round(v * 100) + "%"}</option>`).join("")}</select></label>
          <label class="fl-check"><input type="checkbox" id="fl-shr" ${FL.shrink ? "checked" : ""}> Shrink covariance (Ledoit-Wolf)</label>
        </div>
        <button class="btn red" id="fl-run" style="margin-top:16px;width:100%">Build efficient frontier →</button>
        <div class="muted small" id="fl-status" style="margin-top:8px"></div>
      </div>
      <div class="card fl-chartcard">
        <div class="flex-between"><h3>The efficient frontier</h3><div class="fl-legend" id="fl-legend"></div></div>
        <div class="fl-chartwrap"><div id="fl-chart" class="fl-svg"></div><div class="fl-tip" id="fl-tip"></div><div class="fl-empty" id="fl-empty">Pick at least two holdings and press <b>Build efficient frontier</b>.</div></div>
        <div class="fl-slider" id="fl-slider" hidden>
          <div class="flex-between small muted"><span>◀ Lower risk</span><span>Slide along the curve — or click it</span><span>Higher return ▶</span></div>
          <input type="range" id="fl-range" min="0" max="10" value="0">
          <div class="fl-jumps"><button class="chipbtn" data-jump="min">Min variance</button><button class="chipbtn" data-jump="tan">Max Sharpe</button><button class="chipbtn" data-jump="same_risk" hidden>Same risk as my mix</button><button class="chipbtn" data-jump="same_return" hidden>Same return as my mix</button></div>
        </div>
        <div id="fl-selected"></div>
      </div>
    </div>
    <div id="fl-more"></div>`;

  const rows = () => {
    $("#fl-rows").innerHTML = FL.items.map((it, i) => `<tr><td><b>${esc(it.s)}</b></td>
      <td class="num"><input class="fl-w mono" data-i="${i}" value="${it.w ?? ""}" placeholder="—" inputmode="decimal"></td>
      <td><button class="linkbtn" data-rm="${i}" title="Remove">✕</button></td></tr>`).join("") || `<tr><td colspan="3" class="muted small">No holdings yet.</td></tr>`;
    const sum = FL.items.reduce((a, it) => a + (parseFloat(it.w) || 0), 0);
    $("#fl-wsum").textContent = sum ? `${sum.toFixed(1)}%` : "";
    $("#fl-wsum").className = "num mono " + (sum && Math.abs(sum - 100) > 0.5 ? "neg" : "");
    $$(".fl-w").forEach(inp => inp.oninput = () => { FL.items[+inp.dataset.i].w = inp.value; flSave(); flAuto(900); const s = FL.items.reduce((a, it) => a + (parseFloat(it.w) || 0), 0); $("#fl-wsum").textContent = s ? `${s.toFixed(1)}%` : ""; });
    $$("[data-rm]").forEach(b => b.onclick = () => { FL.items.splice(+b.dataset.rm, 1); flSave(); rows(); flAuto(); });
  };
  const add = s => {
    s = (s || "").trim().toUpperCase();
    if (!s) return;
    if (FL.items.some(it => it.s === s)) { toast(`${s} is already in the list`); return; }
    if (FL.items.length >= 25) { toast("Use at most 25 holdings", true); return; }
    FL.items.push({ s, w: "" }); flSave(); rows(); flAuto(0); $("#fl-in").value = ""; $("#fl-sug").classList.remove("on");
  };
  rows();
  // search-as-you-type
  const inp = $("#fl-in"), sug = $("#fl-sug");
  let timer = null, results = [], pick = -1;
  const draw = () => {
    sug.innerHTML = results.map((r, i) => `<button type="button" class="r-sug ${i === pick ? "on" : ""}" data-s="${esc(r.symbol)}"><b>${esc(r.symbol)}</b><span>${esc(r.name || "")}</span><em>${esc(r.exchange || "")}</em></button>`).join("");
    sug.classList.toggle("on", results.length > 0);
    sug.querySelectorAll(".r-sug").forEach(b => b.onmousedown = e => { e.preventDefault(); add(b.dataset.s); results = []; });
  };
  inp.addEventListener("input", () => {
    clearTimeout(timer); const q = inp.value.trim();
    if (q.length < 2) { results = []; draw(); return; }
    timer = setTimeout(async () => { try { const r = await api(`/search?q=${encodeURIComponent(q)}`); if (inp.value.trim() !== q) return; results = (r || []).slice(0, 7); pick = -1; draw(); } catch (e) { /* ignore */ } }, 250);
  });
  inp.addEventListener("keydown", e => {
    if (e.key === "ArrowDown" && results.length) { e.preventDefault(); pick = Math.min(pick + 1, results.length - 1); draw(); }
    else if (e.key === "ArrowUp" && results.length) { e.preventDefault(); pick = Math.max(pick - 1, -1); draw(); }
    else if (e.key === "Enter") { e.preventDefault(); add(pick >= 0 && results[pick] ? results[pick].symbol : inp.value); results = []; draw(); }
  });
  inp.addEventListener("blur", () => setTimeout(() => sug.classList.remove("on"), 150));
  $("#fl-add").onclick = () => add(pick >= 0 && results[pick] ? results[pick].symbol : inp.value);
  $$("[data-set]").forEach(b => b.onclick = () => { FL.items = FL_SETS[b.dataset.set].map(s => ({ s, w: "" })); flSave(); rows(); flPrefetch(); runFrontier(); });
  $("#fl-model").onchange = e => {
    const m = models.find(x => String(x.id) === e.target.value); if (!m) return;
    FL.items = Object.entries(m.holdings).map(([s, w]) => ({ s, w: Math.round(w * 1000) / 10 })); flSave(); rows(); runFrontier();
    toast(`Loaded ${m.name} — its weights are filled in as "my weight"`);
  };
  $("#fl-lb").onchange = e => { FL.lookback = e.target.value; flSave(); flPrefetch(); flAuto(); };
  $("#fl-meth").onchange = e => { FL.method = e.target.value; flSave(); flAuto(); };
  $("#fl-mw").onchange = e => { FL.maxw = +e.target.value; flSave(); flAuto(); };
  $("#fl-shr").onchange = e => { FL.shrink = e.target.checked; flSave(); flAuto(); };
  $("#fl-run").onclick = runFrontier;
  $("#fl-range").oninput = e => flSelect(+e.target.value);
  $$("[data-jump]").forEach(b => b.onclick = () => flJump(b.dataset.jump));
  flPrefetch();
  if (FL.items.length >= 2) runFrontier();
}

async function runFrontier() {
  if (FL.items.length < 2) { toast("Add at least two holdings", true); return; }
  const my = {}; let any = false;
  FL.items.forEach(it => { const w = parseFloat(it.w); if (w > 0) { my[it.s] = w / 100; any = true; } });
  const t0 = performance.now(), token = (FL.token = (FL.token || 0) + 1);
  $("#fl-run").disabled = true; $("#fl-run").textContent = "Building…";
  document.querySelector(".fl-chartwrap").classList.add("busy");
  const tick = setInterval(() => { $("#fl-status").textContent = `Optimising ${FL.items.length} holdings… ${((performance.now() - t0) / 1000).toFixed(1)}s`; }, 100);
  let res;
  try {
    res = await api("/frontier", { method: "POST", body: { symbols: FL.items.map(it => it.s), lookback: FL.lookback, method: FL.method, max_weight: FL.maxw, shrink: FL.shrink, my_weights: any ? my : null } });
  } catch (e) {
    clearInterval(tick); document.querySelector(".fl-chartwrap").classList.remove("busy");
    $("#fl-status").innerHTML = `<span class="neg">${esc(e.message)}</span>`; $("#fl-run").disabled = false; $("#fl-run").textContent = "Build efficient frontier →"; return;
  }
  clearInterval(tick);
  if (token !== FL.token) return;                      // a newer run started - ignore this one
  document.querySelector(".fl-chartwrap").classList.remove("busy");
  $("#fl-run").disabled = false; $("#fl-run").textContent = "Build efficient frontier →";
  FL.res = res;
  const r = FL.res;
  $("#fl-status").textContent = `Built in ${((performance.now() - t0) / 1000).toFixed(1)}s · ${r.weeks} weekly returns · ${r.start} → ${r.end} · risk-free ${pct(r.risk_free, 2)} (${r.risk_free_source})`;
  drawFrontierChart();
  $("#fl-slider").hidden = false;
  $("#fl-range").max = r.frontier.length - 1;
  $$("[data-jump^=same]").forEach(b => b.hidden = !(r.mine && r.mine[b.dataset.jump]));
  flJump("tan");
  drawFrontierMore();
}

function flNearestIndex(p) {
  const f = FL.res.frontier; let best = 0, d = 1e9;
  f.forEach((x, i) => { const dd = Math.abs(x.vol - p.vol) + Math.abs(x.ret - p.ret); if (dd < d) { d = dd; best = i; } });
  return best;
}
function flJump(k) {
  const r = FL.res; if (!r) return;
  const p = k === "min" ? r.min_variance : k === "tan" ? r.max_sharpe : r.mine && r.mine[k];
  if (!p) return;
  const i = flNearestIndex(p);
  $("#fl-range").value = i;
  flSelect(i, p);
}
function flSelect(i, exact) {
  const r = FL.res; const f = r.frontier[i];
  const p = exact || { label: "Frontier point", ret: f.ret, vol: f.vol, sharpe: f.sharpe, weights: f.weights };
  FL.sel = p;
  flMoveSelected(p);
  const w = Object.entries(p.weights).sort((a, b) => b[1] - a[1]);
  const color = s => FL_COLORS[r.symbols.indexOf(s) % FL_COLORS.length];
  $("#fl-selected").innerHTML = `<div class="fl-sel">
    <div class="fl-sel-head"><div><div class="eyebrow">${esc(p.label)}</div>
      <div class="fl-kpis"><div><span>Expected return</span><b>${pct(p.ret, 1)}</b></div><div><span>Volatility</span><b>${pct(p.vol, 1)}</b></div><div><span>Sharpe</span><b>${p.sharpe == null ? "—" : p.sharpe.toFixed(2)}</b></div></div></div>
      <div class="fl-save"><input id="fl-name" value="${esc(defaultModelName(p))}" maxlength="60"><button class="btn primary" id="fl-savebtn">Save as model</button></div></div>
    <div class="fl-stack">${w.map(([s, v]) => `<span style="flex:${v};background:${color(s)}" title="${esc(s)} ${pct(v, 1)}"></span>`).join("")}</div>
    <div class="fl-wlist">${w.map(([s, v]) => `<div><i style="background:${color(s)}"></i><b>${esc(s)}</b><span class="mono">${pct(v, 1)}</span></div>`).join("")}</div></div>`;
  $("#fl-savebtn").onclick = async () => {
    const name = $("#fl-name").value.trim() || defaultModelName(p);
    try {
      const m = await api("/frontier/save", { method: "POST", body: { name, weights: p.weights, description: `${p.label}: exp. return ${pct(p.ret, 1)}, volatility ${pct(p.vol, 1)}, Sharpe ${p.sharpe == null ? "—" : p.sharpe.toFixed(2)} (${r.method}, ${r.lookback}, max ${Math.round(r.max_weight * 100)}% per holding).` } });
      toast(`Saved "${m.name}" to the Model marketplace`);
      setTimeout(() => { location.hash = `#/models/${m.id}`; }, 600);
    } catch (e) { toast(e.message, true); }
  };
}
function defaultModelName(p) {
  const tag = /Sharpe/.test(p.label) ? "Max Sharpe" : /variance/i.test(p.label) ? "Min Variance" : /Your/.test(p.label) ? "My mix" : `Efficient ${Math.round(p.vol * 100)}% vol`;
  return `${tag} · ${FL.res.symbols.slice(0, 3).join(" ")}${FL.res.symbols.length > 3 ? " +" + (FL.res.symbols.length - 3) : ""}`;
}

// ---- the frontier chart, drawn as SVG (no canvas: it always renders, is crisp at any size and never goes blank)
const SVGNS = "http://www.w3.org/2000/svg";
function niceTicks(lo, hi, n = 6) {
  const span = hi - lo, raw = span / n, mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(st => span / st <= n) || 10 * mag;
  const out = []; for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-12; v += step) out.push(+v.toFixed(10));
  return out;
}
function drawFrontierChart() {
  const r = FL.res, host = $("#fl-chart");
  if (!r || !host) return;
  $("#fl-empty").style.display = "none";
  const W = Math.max(320, host.clientWidth || host.parentElement.clientWidth || 700), H = Math.max(300, host.clientHeight || 470);
  const M = { l: 58, r: 18, t: 16, b: 46 };
  const pts = [...r.cloud.map(([v, x]) => [v, x]), ...r.frontier.map(p => [p.vol, p.ret]), ...r.assets.map(a => [a.vol, a.ret]),
    [r.min_variance.vol, r.min_variance.ret], [r.max_sharpe.vol, r.max_sharpe.ret], ...(r.mine ? [[r.mine.vol, r.mine.ret]] : []), [0, r.risk_free]];
  let x1 = Math.max(...pts.map(p => p[0])) * 1.08, y0 = Math.min(...pts.map(p => p[1])), y1 = Math.max(...pts.map(p => p[1]));
  const yp = (y1 - y0) * 0.08 || 0.01; y0 -= yp; y1 += yp;
  const X = v => M.l + (v / x1) * (W - M.l - M.r), Y = v => H - M.b - ((v - y0) / (y1 - y0)) * (H - M.t - M.b);
  FL.scale = { X, Y, x1, y0, y1, W, H, M };
  const el = (tag, attrs, parent) => { const e = document.createElementNS(SVGNS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); if (parent) parent.appendChild(e); return e; };
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, class: "fl-plot", role: "img", "aria-label": "Efficient frontier chart" });
  const defs = el("defs", {}, svg);
  const clip = el("clipPath", { id: "fl-clip" }, defs); el("rect", { x: M.l, y: M.t, width: W - M.l - M.r, height: H - M.t - M.b }, clip);
  const grad = el("linearGradient", { id: "fl-grad", x1: "0", x2: "1" }, defs);
  el("stop", { offset: "0", "stop-color": "#DD2F20", "stop-opacity": ".55" }, grad); el("stop", { offset: "1", "stop-color": "#DD2F20" }, grad);
  // grid + axes
  const g = el("g", { class: "fl-grid-lines" }, svg);
  niceTicks(0, x1).forEach(v => { el("line", { x1: X(v), x2: X(v), y1: M.t, y2: H - M.b }, g); const t = el("text", { x: X(v), y: H - M.b + 18, "text-anchor": "middle", class: "fl-tick" }, g); t.textContent = (v * 100).toFixed(0) + "%"; });
  niceTicks(y0, y1).forEach(v => { el("line", { x1: M.l, x2: W - M.r, y1: Y(v), y2: Y(v), class: Math.abs(v) < 1e-9 ? "zero" : "" }, g); const t = el("text", { x: M.l - 8, y: Y(v) + 4, "text-anchor": "end", class: "fl-tick" }, g); t.textContent = (v * 100).toFixed(0) + "%"; });
  const xl = el("text", { x: (M.l + W - M.r) / 2, y: H - 8, "text-anchor": "middle", class: "fl-axis" }, svg); xl.textContent = "Risk — annual volatility";
  const yl = el("text", { x: 14, y: (M.t + H - M.b) / 2, "text-anchor": "middle", class: "fl-axis", transform: `rotate(-90 14 ${(M.t + H - M.b) / 2})` }, svg); yl.textContent = "Expected annual return";
  const plot = el("g", { "clip-path": "url(#fl-clip)" }, svg);
  // random portfolios
  const cloud = el("g", { class: "fl-cloud" }, plot);
  r.cloud.forEach(([v, x]) => el("circle", { cx: X(v).toFixed(1), cy: Y(x).toFixed(1), r: 1.8 }, cloud));
  // capital market line
  if (r.cml.points.length) {
    const [[a0, b0]] = r.cml.points, slope = r.cml.slope, xe = x1;
    el("line", { x1: X(a0), y1: Y(b0), x2: X(xe), y2: Y(b0 + slope * xe), class: "fl-cml" }, plot);
  }
  // your mix -> efficient (same risk)
  if (r.mine && r.mine.same_risk) el("line", { x1: X(r.mine.vol), y1: Y(r.mine.ret), x2: X(r.mine.same_risk.vol), y2: Y(r.mine.same_risk.ret), class: "fl-gap" }, plot);
  // frontier
  const d = r.frontier.map((p, i) => `${i ? "L" : "M"}${X(p.vol).toFixed(1)},${Y(p.ret).toFixed(1)}`).join("");
  el("path", { d, class: "fl-curve-glow" }, plot);
  const curve = el("path", { d, class: "fl-curve" }, plot);
  try { const len = curve.getTotalLength(); curve.style.strokeDasharray = len; curve.style.strokeDashoffset = len; requestAnimationFrame(() => { curve.style.transition = "stroke-dashoffset .9s ease"; curve.style.strokeDashoffset = 0; }); setTimeout(() => { curve.style.strokeDasharray = "none"; }, 1200); } catch (e) { /* static */ }
  // holdings
  const hg = el("g", { class: "fl-assets" }, svg);
  r.assets.forEach((a, i) => {
    el("circle", { cx: X(a.vol), cy: Y(a.ret), r: 5.5, fill: FL_COLORS[i % FL_COLORS.length], class: "fl-asset" }, hg);
    const t = el("text", { x: X(a.vol) + 8, y: Y(a.ret) - 7, class: "fl-label" }, hg); t.textContent = a.symbol;
  });
  // special portfolios
  const sp = el("g", {}, svg);
  const mv = r.min_variance, ms = r.max_sharpe;
  el("rect", { x: X(mv.vol) - 6, y: Y(mv.ret) - 6, width: 12, height: 12, transform: `rotate(45 ${X(mv.vol)} ${Y(mv.ret)})`, class: "fl-minvar" }, sp);
  const star = (cx, cy, R, rr) => Array.from({ length: 10 }, (_, k) => { const ang = -Math.PI / 2 + k * Math.PI / 5, rad = k % 2 ? rr : R; return `${(cx + rad * Math.cos(ang)).toFixed(1)},${(cy + rad * Math.sin(ang)).toFixed(1)}`; }).join(" ");
  el("polygon", { points: star(X(ms.vol), Y(ms.ret), 11, 4.6), class: "fl-star" }, sp);
  if (r.mine) el("circle", { cx: X(r.mine.vol), cy: Y(r.mine.ret), r: 7.5, class: "fl-mine" }, sp);
  el("circle", { cx: X(r.risk_free > 0 ? 0 : 0), cy: Y(r.risk_free), r: 4, class: "fl-rf" }, sp);
  FL.selNode = el("circle", { cx: -50, cy: -50, r: 12, class: "fl-sel-ring" }, svg);
  host.innerHTML = ""; host.appendChild(svg);
  // legend
  $("#fl-legend").innerHTML = [["fl-lg-curve", "Efficient frontier"], ["fl-lg-cml", "Capital Market Line"], ["fl-lg-star", "Max Sharpe"], ["fl-lg-min", "Min variance"], ...(r.mine ? [["fl-lg-mine", "Your mix"]] : []), ["fl-lg-cloud", "Random portfolios"]]
    .map(([c, l]) => `<span><i class="${c}"></i>${l}</span>`).join("");
  // interaction: tooltip + click to select
  const tip = $("#fl-tip");
  const targets = [
    ...r.assets.map(a => ({ v: a.vol, x: a.ret, label: a.symbol, sub: `β ${a.beta.toFixed(2)} · Sharpe ${a.sharpe == null ? "—" : a.sharpe.toFixed(2)}` })),
    { v: mv.vol, x: mv.ret, label: "Minimum variance", sub: `Sharpe ${mv.sharpe == null ? "—" : mv.sharpe.toFixed(2)}` },
    { v: ms.vol, x: ms.ret, label: "Maximum Sharpe", sub: `Sharpe ${ms.sharpe == null ? "—" : ms.sharpe.toFixed(2)}` },
    ...(r.mine ? [{ v: r.mine.vol, x: r.mine.ret, label: "Your mix", sub: `Sharpe ${r.mine.sharpe == null ? "—" : r.mine.sharpe.toFixed(2)}` }] : []),
    ...r.frontier.map(p => ({ v: p.vol, x: p.ret, label: "Efficient portfolio", sub: `Sharpe ${p.sharpe == null ? "—" : p.sharpe.toFixed(2)} · click to select`, frontier: true })),
  ];
  const toData = e => { const b = svg.getBoundingClientRect(); return [(e.clientX - b.left) * (W / b.width), (e.clientY - b.top) * (H / b.height), b]; };
  svg.addEventListener("mousemove", e => {
    const [px, py, b] = toData(e); let best = null, bd = 16;
    targets.forEach(t => { const dd = Math.hypot(X(t.v) - px, Y(t.x) - py) - (t.frontier ? 3 : 0); if (dd < bd) { bd = dd; best = t; } });
    if (!best) { tip.style.opacity = 0; svg.style.cursor = "crosshair"; return; }
    svg.style.cursor = "pointer";
    tip.innerHTML = `<b>${esc(best.label)}</b><span>${pct(best.x, 1)} return · ${pct(best.v, 1)} risk</span><em>${esc(best.sub)}</em>`;
    const hb = host.getBoundingClientRect();
    tip.style.left = Math.min(hb.width - 190, X(best.v) * (b.width / W) + 14) + "px"; tip.style.top = Math.max(0, Y(best.x) * (b.height / H) - 54) + "px"; tip.style.opacity = 1;
  });
  svg.addEventListener("mouseleave", () => { tip.style.opacity = 0; });
  svg.addEventListener("click", e => {
    const [px, py] = toData(e); const v = (px - M.l) / (W - M.l - M.r) * x1, x = y0 + (H - M.b - py) / (H - M.t - M.b) * (y1 - y0);
    const i = flNearestIndex({ vol: v, ret: x }); $("#fl-range").value = i; flSelect(i);
  });
  if (FL.sel) flMoveSelected(FL.sel);
}
function flMoveSelected(p) {
  if (!FL.selNode || !FL.scale || !p) return;
  FL.selNode.setAttribute("cx", FL.scale.X(p.vol)); FL.selNode.setAttribute("cy", FL.scale.Y(p.ret));
}
let flResizeT = null;
window.addEventListener("resize", () => { clearTimeout(flResizeT); flResizeT = setTimeout(() => { if (FL.res && $("#fl-chart")) drawFrontierChart(); }, 200); });

function drawFrontierMore() {
  const r = FL.res;
  const row = (p, key) => p ? `<tr><td><b>${esc(p.label)}</b></td><td class="num">${pct(p.ret, 1)}</td><td class="num">${pct(p.vol, 1)}</td><td class="num">${p.sharpe == null ? "—" : p.sharpe.toFixed(2)}</td>
    <td class="small">${Object.entries(p.weights).sort((a, b) => b[1] - a[1]).slice(0, 5).map(([s, w]) => `${esc(s)} ${pct(w, 0)}`).join(" · ")}${Object.keys(p.weights).length > 5 ? " …" : ""}</td>
    <td><button class="btn sm" data-pick="${key}">View</button></td></tr>` : "";
  const heat = v => { const a = Math.abs(v); return v >= 0 ? `rgba(221,47,32,${(a * 0.85).toFixed(2)})` : `rgba(47,111,181,${(a * 0.85).toFixed(2)})`; };
  const mine = r.mine;
  $("#fl-more").innerHTML = `
    ${mine ? `<div class="card fl-verdict ${mine.efficiency_gap > 0.005 ? "warn" : "ok"}"><div class="eyebrow">Your mix vs the frontier</div>
      <h2>${mine.efficiency_gap > 0.005 ? `Your mix is leaving about <span class="neg">${pct(mine.efficiency_gap, 1)}</span> a year on the table.` : "Your mix is on (or very close to) the efficient frontier."}</h2>
      <p>Your mix: ${pct(mine.ret, 1)} expected return at ${pct(mine.vol, 1)} volatility (Sharpe ${mine.sharpe == null ? "—" : mine.sharpe.toFixed(2)}).
      ${mine.same_risk ? ` At the <b>same risk</b>, the efficient portfolio earns <b>${pct(mine.same_risk.ret, 1)}</b>.` : ""}
      ${mine.same_return ? ` For the <b>same return</b>, it needs only <b>${pct(mine.same_return.vol, 1)}</b> volatility.` : ""}</p></div>` : ""}
    <div class="grid g2">
      <div class="card"><h3>Key portfolios</h3><table><thead><tr><th>Portfolio</th><th class="num">Return</th><th class="num">Vol</th><th class="num">Sharpe</th><th>Top weights</th><th></th></tr></thead><tbody>
        ${row(r.max_sharpe, "max_sharpe")}${row(r.min_variance, "min_variance")}${row(r.equal_weight, "equal_weight")}${mine ? row(mine, "mine") + row(mine.same_risk, "same_risk") + row(mine.same_return, "same_return") : ""}</tbody></table>
        <div class="muted small" style="margin-top:8px">Max Sharpe is where the Capital Market Line touches the curve: the best return per unit of risk. Mix it with T-bills to move along the line.</div></div>
      <div class="card"><h3>How the allocation shifts along the curve</h3><div style="height:260px"><canvas id="fl-area"></canvas></div>
        <div class="muted small">Each column is one efficient portfolio, from lowest risk (left) to highest return (right).</div></div>
    </div>
    <div class="grid g2">
      <div class="card"><h3>Each holding on its own</h3><table><thead><tr><th>Holding</th><th class="num">Exp. return</th><th class="num">Historical</th><th class="num">CAPM</th><th class="num">Vol</th><th class="num">Beta</th><th class="num">Sharpe</th></tr></thead><tbody>
        ${r.assets.map((a, i) => `<tr><td><i class="fl-dot" style="background:${FL_COLORS[i % FL_COLORS.length]}"></i><a href="#" data-chart="${esc(a.symbol)}"><b>${esc(a.symbol)}</b></a></td><td class="num">${pct(a.ret, 1)}</td><td class="num">${pct(a.hist_return, 1)}</td><td class="num">${pct(a.capm_return, 1)}</td><td class="num">${pct(a.vol, 1)}</td><td class="num">${a.beta.toFixed(2)}</td><td class="num">${a.sharpe == null ? "—" : a.sharpe.toFixed(2)}</td></tr>`).join("")}</tbody></table></div>
      <div class="card"><h3>Correlation matrix</h3><div class="fl-corr" style="grid-template-columns:70px repeat(${r.symbols.length},1fr)">
        <span></span>${r.symbols.map(s => `<span class="fl-ch">${esc(s)}</span>`).join("")}
        ${r.corr.map((rowv, i) => `<span class="fl-ch l">${esc(r.symbols[i])}</span>${rowv.map((v, j) => `<span class="fl-cell" style="background:${i === j ? "#111" : heat(v)};color:${i === j || Math.abs(v) > 0.55 ? "#fff" : "inherit"}" title="${esc(r.symbols[i])} / ${esc(r.symbols[j])}: ${v.toFixed(2)}">${i === j ? "" : v.toFixed(2)}</span>`).join("")}`).join("")}</div>
        <div class="muted small" style="margin-top:8px">Low (or negative, blue) correlations are what bend the curve up and to the left — that's the diversification benefit.</div></div>
    </div>
    <div class="card"><h3>Growth of 10,000 (in-sample)</h3><div style="height:280px"><canvas id="fl-bt"></canvas></div>
      <div class="muted small">Uses the same history the weights were estimated from, so it flatters the optimised portfolios. Treat it as a sanity check, not a forecast.</div></div>
    ${flExplain(r)}
    <div class="card fl-notes"><h3>Method notes</h3><ul>${r.notes.map(n => `<li>${esc(n)}</li>`).join("")}
      <li>Expected returns: ${r.method === "blend" ? "50% historical average + 50% CAPM (risk-free + beta × " + pct(r.erp, 1) + " equity risk premium)" : r.method === "capm" ? "CAPM: risk-free + beta × " + pct(r.erp, 1) + " equity risk premium (beta vs S&P 500)" : "historical average weekly return × 52"}. Long-only, max ${Math.round(r.max_weight * 100)}% per holding.</li></ul></div>`;
  $$("[data-pick]").forEach(b => b.onclick = () => {
    const k = b.dataset.pick; const p = k === "mine" ? r.mine : k.startsWith("same") ? r.mine[k] : r[k];
    const i = flNearestIndex(p); $("#fl-range").value = i; flSelect(i, p); $("#fl-chart").scrollIntoView({ behavior: "smooth", block: "center" });
  });
  $$("[data-chart]").forEach(a => a.onclick = e => { e.preventDefault(); if (window.openDetail) openDetail(a.dataset.chart); });
  // stacked weights along the frontier
  chart($("#fl-area"), {
    type: "bar",
    data: { labels: r.frontier.map(p => (p.vol * 100).toFixed(1) + "%"),
      datasets: r.symbols.map((s, i) => ({ label: s, data: r.frontier.map(p => (p.weights[s] || 0) * 100), backgroundColor: FL_COLORS[i % FL_COLORS.length], borderWidth: 0, barPercentage: 1, categoryPercentage: 1 })) },
    options: { maintainAspectRatio: false, animation: { duration: 500 }, scales: { x: { stacked: true, title: { display: true, text: "Volatility" }, ticks: { maxTicksLimit: 8 } }, y: { stacked: true, max: 100, ticks: { callback: v => v + "%" } } },
      plugins: { legend: { position: "bottom", labels: { boxWidth: 10 } }, tooltip: { callbacks: { label: c => `${c.dataset.label}: ${c.parsed.y.toFixed(1)}%` } } },
      onClick: (e, els) => { if (els.length) { const i = els[0].index; $("#fl-range").value = i; flSelect(i); } } },
  });
  const bt = r.backtest, sc = { "Max Sharpe": "#DD2F20", "Min variance": "#111", "Equal weight": "#8f8f8f", "Your mix": "#E39B2D", "S&P 500": "#2F6FB5" };
  chart($("#fl-bt"), {
    type: "line",
    data: { labels: bt.dates, datasets: Object.entries(bt.series).map(([k, v]) => ({ label: k, data: v, borderColor: sc[k] || "#555", borderWidth: k === "Max Sharpe" ? 2.5 : 1.6, pointRadius: 0, tension: 0.15, borderDash: k === "S&P 500" ? [5, 4] : [] })) },
    options: { maintainAspectRatio: false, interaction: { mode: "index", intersect: false }, scales: { x: { ticks: { maxTicksLimit: 8 } }, y: { ticks: { callback: v => Math.round(v).toLocaleString() } } },
      plugins: { legend: { position: "bottom", labels: { boxWidth: 10 } }, tooltip: { callbacks: { label: c => `${c.dataset.label}: ${Math.round(c.parsed.y).toLocaleString()}` } } } },
  });
}
// "How is this calculated?" - the formulas, with a worked example using the first holding's real numbers
function flExplain(r) {
  const a = r.assets[0], rf = r.risk_free, erp = r.erp;
  const ms = r.max_sharpe;
  const blendNote = r.method === "blend" ? `Blend = ½ × ${pct(a.hist_return, 1)} + ½ × ${pct(a.capm_return, 1)} = <b>${pct(a.ret, 1)}</b>` :
    r.method === "capm" ? `Using CAPM → <b>${pct(a.ret, 1)}</b>` : `Using the historical average → <b>${pct(a.ret, 1)}</b>`;
  return `<div class="card fl-explain"><details open><summary><h3>How the risk and return numbers are calculated</h3></summary>
    <div class="fl-ex-grid">
      <div class="fl-ex"><div class="fl-ex-n">1</div><h4>Prices → weekly returns</h4>
        <p>We download ${r.lookback === "1y" ? "1 year" : r.lookback === "5y" ? "5 years" : "3 years"} of daily closing prices (adjusted for dividends and splits) from Yahoo Finance, take each Friday's close, and compute the weekly return: <code>r<sub>t</sub> = P<sub>t</sub> / P<sub>t−1</sub> − 1</code>. That gave <b>${r.weeks}</b> weekly returns (${esc(r.start)} → ${esc(r.end)}).</p></div>
      <div class="fl-ex"><div class="fl-ex-n">2</div><h4>Expected return (the "return %")</h4>
        <p><b>Historical</b> = average weekly return × 52.<br><b>CAPM</b> = risk-free + β × equity risk premium, where β = cov(stock, S&P 500) ÷ var(S&P 500) on the same weekly data, risk-free = ${pct(rf, 2)} (${esc(r.risk_free_source)}), ERP = ${pct(erp, 1)}.<br><b>Blend</b> (default) = 50/50 of the two.</p>
        <p class="fl-ex-eg">Example, <b>${esc(a.symbol)}</b>: historical ${pct(a.hist_return, 1)}; β ${a.beta.toFixed(2)} → CAPM ${pct(rf, 2)} + ${a.beta.toFixed(2)} × ${pct(erp, 1)} = ${pct(a.capm_return, 1)}. ${blendNote}.</p></div>
      <div class="fl-ex"><div class="fl-ex-n">3</div><h4>Risk (the "volatility %")</h4>
        <p>Standard deviation of weekly returns × √52 (annualised). For the relationships between holdings we use the covariance matrix Σ of weekly returns × 52${r.shrinkage ? `, shrunk ${Math.round(r.shrinkage * 100)}% toward an average-correlation target (Ledoit-Wolf) because raw sample correlations are noisy` : ""}.</p>
        <p class="fl-ex-eg">Example, <b>${esc(a.symbol)}</b>: volatility ${pct(a.vol, 1)} a year.</p></div>
      <div class="fl-ex"><div class="fl-ex-n">4</div><h4>A portfolio's return and risk</h4>
        <p>Return = Σ wᵢ × E[rᵢ] (the weighted average).<br>Risk = √(wᵀ Σ w) — it is <i>not</i> the weighted average of the volatilities: low correlations cancel part of the swings. That is why the curve bends to the left.</p>
        <p class="fl-ex-eg">Sharpe ratio = (return − risk-free) ÷ risk. Max-Sharpe here: (${pct(ms.ret, 1)} − ${pct(rf, 2)}) ÷ ${pct(ms.vol, 1)} = <b>${ms.sharpe == null ? "—" : ms.sharpe.toFixed(2)}</b>.</p></div>
      <div class="fl-ex"><div class="fl-ex-n">5</div><h4>The efficient frontier</h4>
        <p>For each level of risk appetite λ we solve <code>minimise wᵀΣw − λ·μᵀw</code> subject to weights ≥ 0, each ≤ ${Math.round(r.max_weight * 100)}%, and summing to 100%. λ = 0 gives the minimum-variance portfolio; raising λ walks up the curve. The max-Sharpe (tangency) portfolio is where the Capital Market Line from the risk-free rate just touches the curve.</p></div>
    </div>
    <div class="muted small">All estimates come from past prices, so they describe how these assets <i>behaved</i>, not a guarantee of how they will. Short histories and single great years move the historical number a lot — the reason Blend/CAPM and shrinkage are there.</div>
  </details></div>`;
}
window.frontierView = frontierView;
