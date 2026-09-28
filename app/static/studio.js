/* Studio layer: brand mark, welcome briefing, dark mode, market pulse line, trade ticket and rebalance flow. */
const LOGO_SVG = `<svg class="logo-mark" viewBox="0 0 40 40" aria-hidden="true">
  <rect width="40" height="40" rx="11" fill="#DD2F20"/>
  <rect class="lb lb1" x="9" y="22" width="5.2" height="9" rx="1.6" fill="#fff"/>
  <rect class="lb lb2" x="17.4" y="16" width="5.2" height="15" rx="1.6" fill="#fff"/>
  <rect class="lb lb3" x="25.8" y="9" width="5.2" height="22" rx="1.6" fill="#fff"/>
  <path class="lline" d="M7.5 19.5 L19.5 11.5 L32.5 6.5" stroke="#0a0a0a" stroke-width="2.4" fill="none" stroke-linecap="round"/></svg>`;
const storeGet = k => { try { return localStorage.getItem(k); } catch (e) { return null; } };
const storeSet = (k, v) => { try { localStorage.setItem(k, v); } catch (e) { /* ignore */ } };
const sessGet = k => { try { return sessionStorage.getItem(k); } catch (e) { return null; } };
const sessSet = (k, v) => { try { sessionStorage.setItem(k, v); } catch (e) { /* ignore */ } };
const sleep = ms => new Promise(r => setTimeout(r, ms));

// ---------------------------------------------------------------- brand + sidebar tools
function setupBrand() {
  const brand = document.querySelector(".brand");
  if (!brand) return;
  const name = (CONFIG && CONFIG.brand) || "Redline";
  brand.innerHTML = `${LOGO_SVG}<div class="brand-text"><div class="brand-name">${esc(name)}</div><div class="brand-sub">Portfolio Manager</div></div>`;
  brand.style.cursor = "pointer";
  brand.onclick = () => { location.hash = "#/"; };
  document.title = `${name} · Portfolio Manager`;
  const foot = document.querySelector(".sidebar-foot");
  if (foot && !document.getElementById("side-tools")) {
    const tools = document.createElement("div");
    tools.id = "side-tools";
    tools.className = "side-tools";
    tools.innerHTML = `<button id="brief-btn" title="Morning briefing">☀ Briefing</button><button id="theme-btn" title="Light / dark"></button>`;
    foot.prepend(tools);
    document.getElementById("brief-btn").onclick = () => showWelcome(true);
    document.getElementById("theme-btn").onclick = () => setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark", true);
  }
}

// ---------------------------------------------------------------- dark mode
function setTheme(t, rerender) {
  document.documentElement.dataset.theme = t;
  storeSet("pm.theme", t);
  const b = document.getElementById("theme-btn");
  if (b) b.textContent = t === "dark" ? "☀ Light" : "☾ Dark";
  Chart.defaults.color = t === "dark" ? "#a3a3a3" : "#737373";
  Chart.defaults.borderColor = t === "dark" ? "#262626" : "#efefef";
  if (rerender) route();
}

// ---------------------------------------------------------------- market pulse line (speed follows the VIX)
function setPulse(b) {
  let el = document.getElementById("pulse");
  if (!el) { el = document.createElement("div"); el.id = "pulse"; el.innerHTML = "<span></span>"; document.body.appendChild(el); }
  const vix = b && b.mood ? b.mood.vix : null;
  const dur = vix ? Math.max(0.9, Math.min(4.5, 4.5 - (vix - 12) * 0.2)) : 3;
  el.style.setProperty("--pulse-dur", dur.toFixed(2) + "s");
  el.dataset.mood = b && b.mood ? b.mood.label : "";
  el.title = vix ? `Market pulse · VIX ${vix.toFixed(1)} · ${b.mood.label}` : "Market pulse";
}
async function refreshPulse() {
  try { const b = await api("/briefing"); setPulse(b); return b; } catch (e) { return null; }
}

// ---------------------------------------------------------------- welcome briefing
async function showWelcome(force) {
  if (!force && sessGet("pm.welcomed")) return;
  sessSet("pm.welcomed", "1");
  const w = document.createElement("div");
  w.className = "welcome";
  w.innerHTML = `<div class="welcome-inner"><div class="welcome-logo">${LOGO_SVG}</div><div class="welcome-load">Preparing your briefing…</div></div>`;
  document.body.appendChild(w);
  requestAnimationFrame(() => w.classList.add("on"));
  let b = null;
  try { b = await api("/briefing"); setPulse(b); } catch (e) { /* show a minimal welcome */ }
  await sleep(700);
  const m = b ? b.mood : null;
  const moodPct = m ? Math.round((m.score + 1) * 50) : 50;
  w.querySelector(".welcome-inner").innerHTML = !b ? `<div class="welcome-logo">${LOGO_SVG}</div><h1 class="w-h1">Welcome back.</h1><button class="btn red w-enter">Enter the desk →</button>` : `
    <div class="w-top"><div class="welcome-logo sm">${LOGO_SVG}</div><div class="eyebrow light">${esc(b.date)} · ${esc(b.brand)} briefing</div></div>
    <h1 class="w-h1">${esc(b.greeting)},<br>${esc(b.owner)}.</h1>
    <p class="w-sum">${esc(b.summary)}</p>
    <div class="w-grid">
      <div class="w-card"><div class="w-lbl">Markets · ${esc(m.label)}</div>
        <div class="w-mood"><div class="w-mood-track"><span style="left:${moodPct}%"></span></div><div class="w-mood-l"><span>Risk-off</span><span>Risk-on</span></div></div>
        ${b.headline.map(h => `<div class="w-row"><span>${esc(h.name)}</span><b>${h.price != null ? Number(h.price).toLocaleString(undefined, { maximumFractionDigits: 2 }) : "—"}</b><em class="${cls(h.change_pct)}">${pct(h.change_pct, 2, true)}</em></div>`).join("")}
        ${m.vix ? `<div class="w-row muted-l"><span>VIX</span><b>${m.vix.toFixed(1)}</b><em></em></div>` : ""}</div>
      <div class="w-card"><div class="w-lbl">Your book</div>
        <div class="w-big">${money(b.book.aum, 0)}</div><div class="w-cap">assets across ${b.book.clients} client${b.book.clients === 1 ? "" : "s"}</div>
        <div class="w-big sm ${cls(b.book.day_pnl)}">${b.book.day_pnl >= 0 ? "+" : ""}${money(b.book.day_pnl, 0)}</div><div class="w-cap">today (${pct(b.book.day_pct, 2, true)})</div></div>
      <div class="w-card"><div class="w-lbl">To do today</div>
        ${b.actions.length ? b.actions.slice(0, 5).map(a => `<a class="w-todo" href="#/client/${a.client_id}/${a.title.startsWith("Rebalancing") ? "rebalance" : "overview"}"><i class="${a.level}"></i><span><b>${esc(a.client)}</b> — ${esc(a.title)}</span></a>`).join("") : `<div class="w-cap">Nothing urgent. Every portfolio is inside its limits.</div>`}
        ${b.open_orders ? `<div class="w-cap" style="margin-top:8px">${b.open_orders} open order${b.open_orders === 1 ? "" : "s"} working</div>` : ""}</div>
    </div>
    <div class="w-actions"><button class="btn red w-enter">Enter the desk →</button>${b.data_mode === "sim" ? `<span class="w-cap">Simulated prices</span>` : ""}</div>`;
  w.classList.add("ready");
  const close = () => { w.classList.add("off"); setTimeout(() => w.remove(), 900); document.removeEventListener("keydown", onKey); };
  const onKey = e => { if (e.key === "Escape" || e.key === "Enter") close(); };
  document.addEventListener("keydown", onKey);
  w.querySelectorAll(".w-enter, .w-todo").forEach(x => x.addEventListener("click", close));
}

// ---------------------------------------------------------------- trade confirmation ticket
async function showTicket(o) {
  const t = document.createElement("div");
  const filled = o.status === "filled";
  t.className = `ticket ${o.side === "buy" ? "buy" : "sell"}`;
  t.innerHTML = `<div class="tk-head"><span>${filled ? "FILLED" : esc(String(o.status || "").toUpperCase())}</span><span>#${o.id || ""}</span></div>
    <div class="tk-side">${o.side === "buy" ? "BUY" : "SELL"} <b>${esc(o.symbol)}</b></div>
    <div class="tk-grid"><div><i>Qty</i>${num(o.qty, 4)}</div><div><i>Price</i>${filled ? money(o.fill_price) : "—"}</div>
      <div><i>Value</i>${filled ? money(o.qty * o.fill_price) : "—"}</div><div><i>Type</i>${esc(o.order_type || "market")}</div></div>
    <div class="tk-stamp">${filled ? "EXECUTED" : "WORKING"}</div>`;
  document.body.appendChild(t);
  requestAnimationFrame(() => t.classList.add("in"));
  await sleep(1500);
  t.classList.add("out");
  setTimeout(() => t.remove(), 700);
}

// ---------------------------------------------------------------- rebalance money-flow animation
async function showRebalanceFlow(preview, result) {
  const trades = (preview && preview.trades) || [];
  if (!trades.length) return;
  const sells = trades.filter(t => t.side === "sell"), buys = trades.filter(t => t.side === "buy");
  const maxV = Math.max(...trades.map(t => t.value), 1);
  const row = (t, i) => `<div class="rf-row" style="--i:${i}"><b>${esc(t.symbol)}</b><div class="rf-bar"><span style="--w:${Math.max(6, t.value / maxV * 100)}%"></span></div><em>${money(t.value, 0)}</em>
    <small>${pct(t.from_weight)} → ${pct(t.to_weight)}</small></div>`;
  const o = document.createElement("div");
  o.className = "rflow";
  o.innerHTML = `<div class="rf-card">
    <div class="eyebrow light">Rebalancing · ${trades.length} trades</div>
    <h2 class="rf-h">Moving money back to target.</h2>
    <div class="rf-cols">
      <div class="rf-col sells"><div class="rf-lbl">Trim overweight</div>${sells.map(row).join("") || `<div class="rf-empty">Nothing to sell</div>`}</div>
      <div class="rf-mid"><div class="rf-pipe"><i></i><i></i><i></i><i></i><i></i></div><div class="rf-cash">${money(preview.summary.sell_value, 0)}<small>via cash</small></div></div>
      <div class="rf-col buys"><div class="rf-lbl">Top up underweight</div>${buys.map(row).join("") || `<div class="rf-empty">Nothing to buy</div>`}</div>
    </div>
    <div class="rf-done">${result.executed.filter(e => e.status === "filled").length} of ${result.executed.length} filled · portfolio back inside its bands</div></div>`;
  document.body.appendChild(o);
  requestAnimationFrame(() => o.classList.add("in"));
  await sleep(2600 + Math.min(trades.length, 10) * 120);
  o.classList.add("done");
  await sleep(1100);
  o.classList.add("out");
  setTimeout(() => o.remove(), 600);
}
window.showTicket = showTicket;
window.showRebalanceFlow = showRebalanceFlow;

// ---------------------------------------------------------------- boot (after app.js loaded CONFIG)
setTheme(storeGet("pm.theme") || "light", false);
(async function bootStudio() {
  for (let i = 0; i < 80 && !CONFIG; i++) await sleep(60);
  setupBrand();
  setTheme(storeGet("pm.theme") || "light", false);
  showWelcome(false);
  refreshPulse();
  setInterval(refreshPulse, 5 * 60 * 1000);
})();
