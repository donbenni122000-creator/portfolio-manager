/* Portfolio Manager — single-page UI (vanilla JS + Chart.js). */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const view = $("#view");
let CONFIG = null;
// report browser-side errors to data/server.log (so problems in the app window can be diagnosed)
const clientLog = (kind, message, where, extra) => { try { fetch("/api/clientlog", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ kind, message: String(message || ""), where: where || location.hash, extra: extra || null }), keepalive: true }); } catch (e) { /* ignore */ } };
window.addEventListener("error", e => clientLog("error", `${e.message} @ ${(e.filename || "").split("/").pop()}:${e.lineno}:${e.colno}`, location.hash, e.error && e.error.stack ? { stack: String(e.error.stack).slice(0, 1200) } : null));
window.addEventListener("unhandledrejection", e => clientLog("rejection", e.reason && (e.reason.stack || e.reason.message) || String(e.reason), location.hash));
const charts = [];

// ---------------------------------------------------------------- helpers
async function api(path, opts = {}) {
  const res = await fetch("/api" + path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail ? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail)) : res.statusText);
  return data;
}
function toast(msg, err = false) {
  const d = document.createElement("div");
  d.textContent = msg; if (err) d.className = "err";
  $("#toast").appendChild(d); setTimeout(() => d.remove(), err ? 7000 : 3500);
}
// currency symbol used by money(): "$" everywhere except while a non-USD stock's research page is open
let MONEY_SYM = "$";
const CCY_SYM = { USD: "$", INR: "₹", EUR: "€", GBP: "£", GBp: "GBp ", JPY: "¥", CNY: "CN¥", HKD: "HK$", CAD: "C$", AUD: "A$", KRW: "₩", TWD: "NT$", CHF: "CHF ", SGD: "S$", BRL: "R$", ZAc: "ZAc " };
const setCurrency = c => { MONEY_SYM = !c ? "$" : (CCY_SYM[c] || c + " "); };
const money = (v, d = 2) => v == null || isNaN(v) ? "—" : (v < 0 ? "-" : "") + MONEY_SYM + Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
const pct = (v, d = 1, sign = false) => v == null || isNaN(v) ? "—" : (sign && v > 0 ? "+" : "") + (v * 100).toFixed(d) + "%";
const num = (v, d = 2) => v == null || isNaN(v) ? "—" : Number(v).toLocaleString(undefined, { maximumFractionDigits: d, minimumFractionDigits: 0 });
const big = v => v == null ? "—" : Math.abs(v) >= 1e12 ? (v / 1e12).toFixed(2) + "T" : Math.abs(v) >= 1e9 ? (v / 1e9).toFixed(2) + "B" : Math.abs(v) >= 1e6 ? (v / 1e6).toFixed(1) + "M" : num(v, 0);
const cls = v => v > 0 ? "pos" : v < 0 ? "neg" : "";
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const ratingPill = r => r ? `<span class="pill r-${r}">${r}</span>` : "";
const PALETTE = ["#DD2F20", "#0a0a0a", "#8f8f8f", "#f28b7f", "#5c0f08", "#c9c9c9", "#ff5a45", "#3a3a3a", "#b51f12", "#e6e6e6", "#6b6b6b", "#ffb3a8", "#1f1f1f"];
function destroyCharts() { while (charts.length) charts.pop().destroy(); }
function chart(el, cfg) { const c = new Chart(el, cfg); charts.push(c); return c; }
Chart.defaults.font.family = "Raleway, system-ui, sans-serif"; Chart.defaults.font.size = 11.5; Chart.defaults.font.weight = 500; Chart.defaults.color = "#737373"; Chart.defaults.borderColor = "#efefef";

// ---------------------------------------------------------------- router
let pageTimer = null;
async function route() {
  destroyCharts();
  if (window.disposeScenes) window.disposeScenes();
  setCurrency("USD");
  window.scrollTo(0, 0);
  if (pageTimer) { clearInterval(pageTimer); pageTimer = null; }
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  const navKey = parts[0] === "client" ? "clients" : (parts[0] || "markets");
  $$(".sidebar nav a").forEach(a => a.classList.toggle("active", a.dataset.nav === navKey));
  if (parts[0] !== "client") $("#client-nav").innerHTML = "";
  view.innerHTML = `<div class="loading">Loading…</div>`;
  try {
    if (!parts.length || parts[0] === "markets") return await marketsView();
    if (parts[0] === "watchlist") return await watchlistView(parts[1] ? +parts[1] : null);
    if (parts[0] === "news") return await newsView(parts[1]);
    if (parts[0] === "clients") return await clientsView();
    if (parts[0] === "new") return await newClientView();
    if (parts[0] === "research") return await researchView(parts[1], null);
    if (parts[0] === "models") return await modelsView(parts[1]);
    if (parts[0] === "billing") return await billingView();
    if (parts[0] === "assistant") return await assistantView(parts[1] ? +parts[1] : null);
    if (parts[0] === "client") return await clientView(+parts[1], parts[2] || "overview", parts[3]);
    view.innerHTML = `<div class="empty">Page not found</div>`;
  } catch (e) {
    view.innerHTML = `<div class="card"><h2>Something went wrong</h2><p class="neg">${esc(e.message)}</p></div>`;
  }
}
window.addEventListener("hashchange", route);

// ---------------------------------------------------------------- clients list
async function clientsView() {
  const clients = await api("/clients");
  const aum = clients.reduce((s, c) => s + (c.equity || 0), 0);
  view.innerHTML = `
    <section class="hero-dark">
      <div class="hero-copy">
        <div class="eyebrow light">Advisory book</div>
        <h1 class="display">Every client.<br>One desk.</h1>
        <p>Each client has a risk profile, a model portfolio and a paper-trading account — monitored, rebalanced and reported from here.</p>
        <div class="band-stats">
          <div><div class="stat-big">${clients.length}</div><div class="stat-lbl">clients</div></div>
          <div><div class="stat-big">${money(aum, 0)}</div><div class="stat-lbl">assets under advice</div></div>
        </div>
        <div class="hero-actions"><a class="btn red" href="#/new">+ New client</a></div>
      </div>
      <div class="scene-host" data-scene="globe" aria-hidden="true"></div>
    </section>
    <div class="card table-wrap">${clients.length ? `
      <table><thead><tr><th>Name</th><th>Risk profile</th><th class="num">Score</th><th class="num">Equity</th><th class="num">Today</th><th class="num">Total return</th><th>Since</th></tr></thead>
      <tbody>${clients.map(c => `<tr style="cursor:pointer" onclick="location.hash='#/client/${c.id}'">
        <td><b>${esc(c.name)}</b><div class="muted small">${esc(c.email || "")}</div></td>
        <td>${esc(c.risk_profile)}</td><td class="num">${num(c.risk_score, 0)}</td>
        <td class="num">${money(c.equity)}</td><td class="num ${cls(c.day_change_pct)}">${pct(c.day_change_pct, 2, true)}</td>
        <td class="num ${cls(c.total_return)}">${pct(c.total_return, 2, true)}</td><td>${c.created_at.slice(0, 10)}</td></tr>`).join("")}</tbody></table>`
      : `<div class="empty">No clients yet. <a href="#/new">Create the first one</a> — it takes a minute.</div>`}</div>`;
}

// ---------------------------------------------------------------- new client + questionnaire
async function newClientView() {
  const [qs, models] = await Promise.all([api("/questionnaire"), api("/models")]);
  view.innerHTML = `
    <h1>New client</h1><div class="sub">1. Account details → 2. Risk questionnaire → 3. Choose a model and open the paper account. Capacity (horizon, income, liquidity) caps willingness, so short-horizon money stays conservative.</div>
    <div class="grid g2" style="grid-template-columns: minmax(0,1.6fr) minmax(0,1fr)">
      <div class="card">
        <div class="grid g3">
          <div class="field"><label>Client name</label><input id="c-name" placeholder="e.g. Priya Nair" style="width:100%"></div>
          <div class="field"><label>Email (optional)</label><input id="c-email" style="width:100%"></div>
          <div class="field"><label>Paper starting cash ($)</label><input id="c-cash" type="number" value="100000" min="1000" step="1000" style="width:100%"></div>
          <div class="field"><label>Account type</label><select id="c-type" style="width:100%">${Object.entries(CONFIG.account_types).map(([k, v]) => `<option value="${k}">${k} (${v.tax})</option>`).join("")}</select></div>
          <div class="field"><label>Phone (optional)</label><input id="c-phone" style="width:100%"></div>
          <div class="field"><label>Objective (optional)</label><input id="c-obj" placeholder="e.g. Retire at 60, fund kids' college" style="width:100%"></div>
        </div>
        ${benEditor([])}
        <h3 style="margin-top:14px">Risk questionnaire</h3>
        <div id="qs">${qs.map(q => `<div class="q"><label style="color:var(--ink);font-size:13px">${esc(q.question)} <span class="muted small">(${q.kind})</span></label>
          <div class="opts">${q.options.map((o, i) => `<label><input type="radio" name="${q.id}" value="${i}"> ${esc(o[0])}</label>`).join("")}</div></div>`).join("")}</div>
      </div>
      <div>
        <div class="card" id="score-card" style="position:sticky;top:20px"><h3>Risk result</h3><div class="muted">Answer all questions to see the profile.</div></div>
      </div>
    </div>`;
  const update = async () => {
    const answers = {};
    qs.forEach(q => { const c = $(`input[name=${q.id}]:checked`); if (c) answers[q.id] = +c.value; });
    if (Object.keys(answers).length < qs.length) return;
    const r = await api("/risk/score", { method: "POST", body: answers });
    const p = r.profile_detail;
    $("#score-card").innerHTML = `<h3>Risk result</h3>
      <div style="font-size:26px;font-weight:800">${esc(r.profile)}</div>
      <div class="muted" style="margin-bottom:12px">Score ${r.score} / 100 · capacity ${r.capacity} · willingness ${r.willingness}${r.capped_by_capacity ? " · <b>capped by capacity</b>" : ""}</div>
      <table><tbody>
        <tr><td>Target volatility</td><td class="num">${pct(p.vol_band[0], 0)} – ${pct(p.vol_band[1], 0)}</td></tr>
        <tr><td>Drawdown tolerance</td><td class="num">${pct(p.max_dd, 0)}</td></tr>
        <tr><td>Individual-stock sleeve</td><td class="num">${pct(p.stock_sleeve, 0)}</td></tr>
        <tr><td>Max single stock</td><td class="num">${pct(p.max_position, 0)}</td></tr>
        <tr><td>Long-run expected return (CMA)</td><td class="num">${pct(p.exp_return, 1)}</td></tr></tbody></table>
      <div class="chart-box sm" style="margin-top:12px"><canvas id="model-pie"></canvas></div>
      <div class="field" style="margin-top:12px"><label>Override profile (optional)</label>
        <select id="c-override"><option value="">Use ${esc(r.profile)}</option>${CONFIG.profiles.map(x => `<option>${x.name}</option>`).join("")}</select></div>
      <div class="field"><label>Model portfolio</label><select id="c-model" style="width:100%"><option value="">Core ${esc(r.profile)} (recommended)</option>
        ${models.filter(m => m.fits_profiles.includes(r.profile) && m.name !== "Core " + r.profile).map(m => `<option value="${m.id}">${esc(m.name)} ✓ fits</option>`).join("")}
        ${models.filter(m => !m.fits_profiles.includes(r.profile)).map(m => `<option value="${m.id}">${esc(m.name)} (outside risk band)</option>`).join("")}</select></div>
      <button class="btn primary" id="create" style="width:100%">Create client & paper account</button>`;
    destroyCharts();
    chart($("#model-pie"), { type: "doughnut", data: { labels: Object.keys(r.model), datasets: [{ data: Object.values(r.model).map(v => v * 100), backgroundColor: PALETTE }] },
      options: { maintainAspectRatio: false, plugins: { legend: { position: "right" }, tooltip: { callbacks: { label: c => `${c.label}: ${c.raw.toFixed(1)}%` } } } } });
    $("#create").onclick = async () => {
      const name = $("#c-name").value.trim();
      if (!name) return toast("Enter a client name", true);
      try {
        const c = await api("/clients", { method: "POST", body: { name, email: $("#c-email").value || null, answers, starting_cash: +$("#c-cash").value,
          profile_override: $("#c-override").value || null, account_type: $("#c-type").value, phone: $("#c-phone").value || null,
          objective: $("#c-obj").value || null, beneficiaries: readBens(), model_id: $("#c-model").value ? +$("#c-model").value : null } });
        toast(`Created ${c.name} (${c.risk_profile})`);
        location.hash = `#/client/${c.id}/rebalance`;
      } catch (e) { toast(e.message, true); }
    };
  };
  $("#qs").addEventListener("change", update);
  bindBens();
}
function benEditor(bens) {
  return `<div class="card" style="background:#f7f7f7;margin-top:4px"><div class="flex-between"><h3 style="margin:0">Beneficiaries</h3><button class="btn sm" id="ben-add" type="button">+ Beneficiary</button></div>
    <div id="bens">${(bens.length ? bens : []).map(benRow).join("")}</div><div class="muted small">Primary shares must add up to 100%.</div></div>`;
}
const benRow = b => `<div class="inline-form ben" style="margin-top:8px"><input class="b-name" placeholder="Name" value="${esc(b.name || "")}" style="width:160px">
  <input class="b-rel" placeholder="Relationship" value="${esc(b.relationship || "")}" style="width:120px"><select class="b-type"><option ${b.type !== "contingent" ? "selected" : ""}>primary</option><option ${b.type === "contingent" ? "selected" : ""}>contingent</option></select>
  <input class="b-share" type="number" step="5" value="${b.share ?? 100}" style="width:80px"> % <button class="btn sm danger b-del" type="button">✕</button></div>`;
function bindBens() {
  const bind = () => $$(".b-del").forEach(b => b.onclick = () => b.closest(".ben").remove());
  $("#ben-add").onclick = () => { $("#bens").insertAdjacentHTML("beforeend", benRow({ share: $$(".ben").length ? 0 : 100 })); bind(); };
  bind();
}
const readBens = () => $$(".ben").map(r => ({ name: $(".b-name", r).value.trim(), relationship: $(".b-rel", r).value.trim(), type: $(".b-type", r).value, share: +$(".b-share", r).value })).filter(b => b.name);

// ---------------------------------------------------------------- client shell
const TABS = [["overview", "Overview"], ["story", "Story"], ["holdings", "Holdings"], ["trade", "Trade"], ["options", "Options"], ["rebalance", "Rebalance"], ["research", "Research"], ["risk", "Risk"], ["tax", "Tax"], ["planning", "Planning"], ["index", "Direct index"], ["lending", "Lending"], ["billing", "Billing"], ["reports", "Reports"], ["settings", "Settings"]];
async function clientView(id, tab, arg) {
  const client = await api(`/clients/${id}`);
  $("#client-nav").innerHTML = `<div class="cname">${esc(client.name)}<div class="muted small">${esc(client.risk_profile)}</div></div>` +
    TABS.map(([k, l]) => `<a href="#/client/${id}/${k}" class="${k === tab ? "active" : ""}">${l}</a>`).join("");
  view.innerHTML = `<div class="flex-between"><div><h1>${esc(client.name)}</h1><div class="sub">${esc(client.account_type)} · ${esc(client.risk_profile)} · risk score ${num(client.risk_score, 0)} · client since ${client.created_at.slice(0, 10)}</div></div>
    <a class="btn" href="#/assistant/${id}"><span class="ai-tag">AI</span> Ask about ${esc(client.name.split(" ")[0])}</a></div>
    <div class="tabs">${TABS.map(([k, l]) => `<a href="#/client/${id}/${k}" class="${k === tab ? "active" : ""}">${l}</a>`).join("")}</div><div id="tab"><div class="loading">Loading…</div></div>`;
  const el = $("#tab");
  const fn = { overview: overviewTab, story: (e, i, a, c) => storyTab(e, i, a, c), holdings: holdingsTab, trade: tradeTab, rebalance: rebalanceTab, research: (el, id, a) => researchView(a, id, el), risk: riskTab, tax: taxTab, planning: planningTab, billing: clientBillingTab, settings: settingsTab,
    options: optionsTab, index: indexTab, lending: lendingTab, reports: reportsTab }[tab];
  await fn(el, id, arg, client);
}

// ---------------------------------------------------------------- overview
async function overviewTab(el, id, arg, client) {
  const d = await api(`/clients/${id}/dashboard`);
  const v = d.valuation, m = d.metrics, p = d.profile;
  const sky = d.drift.rows.map(r => ({ s: r.symbol, w: r.weight, t: r.target, b: !!r.breach }));
  const score = client ? client.risk_score : 50;
  const volOk = m.available ? (m.annual_vol >= p.vol_band[0] && m.annual_vol <= p.vol_band[1]) : null;
  el.innerHTML = `
    <div class="grid g4">
      ${kpi("Portfolio value", money(v.equity), `${pct(v.day_change_pct, 2, true)} today (${money(v.day_change)})`, v.day_change)}
      ${kpi("Total P&L", money(v.total_pnl), `${pct(v.total_return, 2, true)} on ${money(v.net_deposits, 0)} deposited`, v.total_pnl)}
      ${kpi("Cash", money(v.cash), `${pct(v.cash_weight)} of portfolio (target ${pct(v.cash_target, 0)})`)}
      ${kpi("Unrealized / realized", money(v.unrealized_pnl), `Realized ${money(v.realized_pnl)}`, v.unrealized_pnl)}
    </div>
    <div class="grid sky-grid" style="margin-top:16px">
      <section class="band-dark sky-card">
        <div class="sky-copy"><div class="eyebrow light">Portfolio skyline</div><h2 class="display-3">Every building<br>is a holding.</h2>
          <p>Height is its value. A building <b>leans</b> when it drifts from target, and glows <span class="red-t">red</span> once it breaks its band.</p>
          <div class="sky-legend">${d.drift.needs_rebalance ? `<a class="btn red sm" href="#/client/${id}/rebalance">${d.drift.breaches} out of band → rebalance</a>` : `<span class="ok-pill">All holdings inside bands</span>`}</div></div>
        <div class="scene-host" data-scene="skyline" data-values='${esc(JSON.stringify(sky))}' aria-hidden="true"></div>
      </section>
      <section class="band-dark risk-card">
        <div class="eyebrow light">Risk signature</div>
        <div class="scene-host" data-scene="risk" data-score="${score}" aria-hidden="true"></div>
        <div class="risk-cap"><div class="stat-big">${num(score, 0)}</div><div class="stat-lbl">${esc(p.name)} · ${score < 35 ? "calm and smooth" : score < 60 ? "steady, with some edge" : score < 80 ? "energetic" : "spiky and fast"}</div></div>
      </section>
    </div>
    <div class="grid g2" style="margin-top:16px">
      <div class="card"><h3>Alerts & monitoring</h3>${d.alerts.length ? d.alerts.map(a => `<div class="alert ${a.level}"><b>${esc(a.title)}</b>${esc(a.detail)}</div>`).join("") : `<div class="muted">All clear — portfolio within limits.</div>`}
        ${d.drift.needs_rebalance ? `<a class="btn primary" href="#/client/${id}/rebalance" style="margin-top:6px">Review rebalance</a>` : ""}</div>
      <div class="card"><h3>Risk vs profile (${esc(p.name)})</h3>${m.available ? `
        <table><tbody>
          <tr><td>Volatility (ann.)</td><td class="num">${pct(m.annual_vol)}</td><td class="muted small">band ${pct(p.vol_band[0], 0)}–${pct(p.vol_band[1], 0)} ${volOk ? '<span class="check">✓</span>' : '<span class="cross">✕</span>'}</td></tr>
          <tr><td>Return (ann., 3y look-through)</td><td class="num ${cls(m.annual_return)}">${pct(m.annual_return)}</td><td></td></tr>
          <tr><td>Sharpe / Sortino</td><td class="num">${num(m.sharpe)} / ${num(m.sortino)}</td><td></td></tr>
          <tr><td>Max drawdown (3y)</td><td class="num neg">${pct(m.max_drawdown)}</td><td class="muted small">tolerance ${pct(p.max_dd, 0)}</td></tr>
          <tr><td>Beta vs ${CONFIG.benchmark}</td><td class="num">${num(m.beta)}</td><td></td></tr>
          <tr><td>1-day VaR / CVaR (95%)</td><td class="num">${pct(m.var95_1d, 2)} / ${pct(m.cvar95_1d, 2)}</td><td class="muted small">${money(m.var95_1d * v.equity, 0)}</td></tr>
        </tbody></table>` : `<div class="muted">${esc(m.reason)}</div>`}</div>
    </div>
    <div class="grid g2" style="margin-top:16px">
      <div class="card"><h3>Allocation: actual vs target</h3><div class="chart-box"><canvas id="alloc"></canvas></div></div>
      <div class="card"><h3>${d.performance.length > 1 ? "Account performance (time-weighted, 100 = start)" : "Growth of $10k — current holdings, 3y look-through"}</h3><div class="chart-box"><canvas id="perf"></canvas></div></div>
    </div>`;
  const hs = v.holdings.filter(h => h.weight > 0 || h.target_weight > 0);
  chart($("#alloc"), { type: "bar", data: { labels: [...hs.map(h => h.symbol), "CASH"],
    datasets: [{ label: "Actual", data: [...hs.map(h => h.weight * 100), v.cash_weight * 100], backgroundColor: "#DD2F20" },
               { label: "Target", data: [...hs.map(h => h.target_weight * 100), v.cash_target * 100], backgroundColor: "#d4d4d4" }] },
    options: { maintainAspectRatio: false, scales: { y: { ticks: { callback: x => x + "%" } } }, plugins: { tooltip: { callbacks: { label: c => `${c.dataset.label}: ${c.raw.toFixed(1)}%` } } } } });
  const series = d.performance.length > 1 ? d.performance.map(x => ({ x: x.date, y: x.index })) : (m.available ? m.growth_of_10k.map(x => ({ x: x.date, y: x.value })) : []);
  chart($("#perf"), { type: "line", data: { labels: series.map(s => s.x), datasets: [{ data: series.map(s => s.y), borderColor: "#DD2F20", pointRadius: 0, borderWidth: 2, fill: true, backgroundColor: "rgba(221,47,32,.08)" }] },
    options: { maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: { ticks: { maxTicksLimit: 6 } } } } });
}
const kpi = (label, value, delta, sign) => `<div class="card kpi"><div class="label">${label}</div><div class="value">${value}</div><div class="delta ${sign != null ? cls(sign) : "muted"}">${delta}</div></div>`;

// ---------------------------------------------------------------- holdings
async function holdingsTab(el, id) {
  const d = await api(`/clients/${id}/dashboard`);
  const hs = d.valuation.holdings;
  el.innerHTML = `<div class="card table-wrap"><table><thead><tr><th>Symbol</th><th>Class</th><th class="num">Qty</th><th class="num">Avg cost</th><th class="num">Price</th><th class="num">Day</th><th class="num">Value</th><th class="num">Weight</th><th class="num">Target</th><th class="num">Drift</th><th class="num">Unrealized</th><th>Model</th><th></th></tr></thead>
    <tbody>${hs.map(h => `<tr><td><b>${h.symbol}</b><div class="muted small">${esc((h.name || "").slice(0, 28))}</div></td><td class="small">${esc(h.asset_class)}</td>
      <td class="num">${num(h.qty, 4)}</td><td class="num">${money(h.avg_cost)}</td><td class="num">${money(h.price)}</td><td class="num ${cls(h.day_change_pct)}">${pct(h.day_change_pct, 2, true)}</td>
      <td class="num">${money(h.value)}</td><td class="num">${pct(h.weight)}</td><td class="num">${pct(h.target_weight)}</td>
      <td class="num ${Math.abs(h.drift) >= d.client.drift_abs_band ? "neg" : ""}">${pct(h.drift, 1, true)}</td>
      <td class="num ${cls(h.unrealized)}">${money(h.unrealized)}<div class="small">${pct(h.unrealized_pct, 1, true)}</div></td>
      <td>${ratingPill(h.rating)}</td>
      <td><a class="btn sm" href="#/client/${id}/trade/${h.symbol}">Trade</a> <a class="btn sm" href="#/client/${id}/research/${h.symbol}">Research</a></td></tr>`).join("")}
      <tr><td><b>CASH</b></td><td class="small">Cash</td><td colspan="4"></td><td class="num">${money(d.valuation.cash)}</td><td class="num">${pct(d.valuation.cash_weight)}</td><td class="num">${pct(d.valuation.cash_target)}</td><td colspan="4"></td></tr>
    </tbody></table></div>`;
}

// ---------------------------------------------------------------- trade
async function tradeTab(el, id, sym) {
  const [client, open, hist] = await Promise.all([api(`/clients/${id}`), api(`/clients/${id}/orders?status=open`), api(`/clients/${id}/orders`)]);
  el.innerHTML = `<div class="grid g2" style="grid-template-columns:minmax(0,1fr) minmax(0,1.4fr)">
    <div class="card"><h2>Order ticket</h2>
      <div class="field"><label>Symbol</label><div class="inline-form"><input id="t-sym" value="${esc(sym || "")}" placeholder="AAPL" style="text-transform:uppercase;width:120px"><button class="btn" id="t-q">Quote</button></div><div id="t-quote" class="muted small" style="margin-top:6px"></div></div>
      <div class="field"><label>Side</label><div class="inline-form"><button class="btn buy" data-side="buy">Buy</button><button class="btn" data-side="sell">Sell</button></div></div>
      <div class="grid g2">
        <div class="field"><label>Order type</label><select id="t-type"><option value="market">Market</option><option value="limit">Limit</option><option value="stop">Stop</option></select></div>
        <div class="field"><label>Size by</label><select id="t-by"><option value="qty">Shares</option><option value="notional">Dollars</option></select></div>
        <div class="field"><label id="t-amt-l">Shares</label><input id="t-amt" type="number" min="0" step="any" style="width:100%"></div>
        <div class="field" id="t-px-f" style="display:none"><label id="t-px-l">Limit price</label><input id="t-px" type="number" step="0.01" style="width:100%"></div>
      </div>
      <div id="t-est" class="muted small" style="margin-bottom:10px"></div>
      <button class="btn primary" id="t-go" style="width:100%">Place paper order</button>
      <div class="muted small" style="margin-top:10px">Buying power: <b>${money(client.cash)}</b> · slippage ${CONFIG.slippage_bps} bps · commission ${money(CONFIG.commission)} · ${CONFIG.fractional ? "fractional shares on" : "whole shares only"}</div>
    </div>
    <div class="card"><h2>Working orders</h2><div class="table-wrap">${open.length ? orderTable(open, id, true) : `<div class="muted">No open limit/stop orders.</div>`}</div>
      <h2 style="margin-top:20px">Order history</h2><div class="table-wrap">${hist.filter(o => o.status !== "open").length ? orderTable(hist.filter(o => o.status !== "open").slice(0, 60), id, false) : `<div class="muted">No orders yet.</div>`}</div></div></div>`;
  let side = "buy", lastQuote = null;
  $$("[data-side]").forEach(b => b.onclick = () => { side = b.dataset.side; $$("[data-side]").forEach(x => x.className = "btn" + (x.dataset.side === side ? " " + side : "")); est(); });
  const getQuote = async () => {
    const s = $("#t-sym").value.trim().toUpperCase(); if (!s) return;
    try { lastQuote = await api(`/quote/${s}`); $("#t-quote").innerHTML = `<b>${esc(lastQuote.name)}</b> · ${money(lastQuote.price)} <span class="${cls(lastQuote.change_pct)}">${pct(lastQuote.change_pct / 100, 2, true)}</span>`; est(); }
    catch (e) { $("#t-quote").innerHTML = `<span class="neg">${esc(e.message)}</span>`; lastQuote = null; }
  };
  const est = () => {
    const t = $("#t-type").value, by = $("#t-by").value, amt = +$("#t-amt").value;
    $("#t-px-f").style.display = t === "market" ? "none" : ""; $("#t-px-l").textContent = t === "limit" ? "Limit price" : "Stop price";
    $("#t-amt-l").textContent = by === "qty" ? "Shares" : "Dollar amount";
    if (!lastQuote || !amt) return $("#t-est").textContent = "";
    const px = t === "market" ? lastQuote.price : (+$("#t-px").value || lastQuote.price);
    $("#t-est").textContent = by === "qty" ? `≈ ${money(amt * px)} ${side === "buy" ? "cost" : "proceeds"}` : `≈ ${num(amt / px, 4)} shares`;
  };
  $("#t-q").onclick = getQuote; $("#t-sym").onchange = getQuote;
  ["#t-type", "#t-by", "#t-amt", "#t-px"].forEach(s => $(s).oninput = est);
  if (sym) getQuote();
  $("#t-go").onclick = async () => {
    const t = $("#t-type").value, by = $("#t-by").value, amt = +$("#t-amt").value;
    const body = { symbol: $("#t-sym").value.trim(), side, order_type: t };
    body[by] = amt;
    if (t === "limit") body.limit_price = +$("#t-px").value; if (t === "stop") body.stop_price = +$("#t-px").value;
    try {
      const o = await api(`/clients/${id}/orders`, { method: "POST", body });
      if (window.showTicket) await window.showTicket(o);
      else toast(o.status === "filled" ? `Filled: ${o.side} ${num(o.qty, 4)} ${o.symbol} @ ${money(o.fill_price)}` : `Order ${o.status}: ${o.side} ${num(o.qty, 4)} ${o.symbol}`);
      route();
    } catch (e) { toast(e.message, true); }
  };
}
function orderTable(orders, id, cancellable) {
  return `<table><thead><tr><th>Time</th><th>Symbol</th><th>Side</th><th>Type</th><th class="num">Qty</th><th class="num">Limit/Stop</th><th class="num">Fill</th><th class="num">Realized</th><th>Status</th><th>Source</th>${cancellable ? "<th></th>" : ""}</tr></thead><tbody>
  ${orders.map(o => `<tr><td class="small">${(o.filled_at || o.created_at).replace("T", " ").slice(0, 16)}</td><td><b>${o.symbol}</b></td><td class="${o.side === "buy" ? "pos" : "neg"}">${o.side.toUpperCase()}</td><td>${o.order_type}</td>
    <td class="num">${num(o.qty, 4)}</td><td class="num">${money(o.limit_price ?? o.stop_price)}</td><td class="num">${money(o.fill_price)}</td><td class="num ${cls(o.realized_pnl)}">${o.realized_pnl == null ? "" : money(o.realized_pnl)}</td>
    <td>${o.status}${o.reason && o.status === "rejected" ? `<div class="small neg">${esc(o.reason)}</div>` : ""}</td><td class="small">${o.source}</td>
    ${cancellable ? `<td><button class="btn sm danger" onclick="cancelOrder(${id},${o.id})">Cancel</button></td>` : ""}</tr>`).join("")}</tbody></table>`;
}
window.cancelOrder = async (cid, oid) => { try { await api(`/clients/${cid}/orders/${oid}`, { method: "DELETE" }); toast("Order cancelled"); route(); } catch (e) { toast(e.message, true); } };

// ---------------------------------------------------------------- rebalance
async function rebalanceTab(el, id, mode = "full") {
  const r = await api(`/clients/${id}/rebalance?mode=${mode}`);
  const s = r.summary;
  el.innerHTML = `<div class="flex-between" style="margin-bottom:14px"><div>
      ${r.needs_rebalance ? `<span class="pill r-SELL">REBALANCE INDICATED</span> <span class="muted">${r.drift.breaches} breach(es); largest drift ${pct(r.drift.max_abs_drift)}</span>` : `<span class="pill r-BUY">WITHIN BANDS</span> <span class="muted">Largest drift ${pct(r.drift.max_abs_drift)}</span>`}
      <div class="muted small" style="margin-top:4px">Bands: ±${pct(r.drift.abs_band, 0)} absolute or ${pct(r.drift.rel_band, 0)} relative to target</div></div>
    <div class="inline-form"><select id="rb-mode"><option value="full" ${mode === "full" ? "selected" : ""}>Full — all holdings to target</option><option value="breached" ${mode === "breached" ? "selected" : ""}>Breached only — lower turnover</option></select>
      <button class="btn primary" id="rb-go" ${s.n_trades ? "" : "disabled"}>Execute ${s.n_trades} paper trade(s)</button></div></div>
  <div class="grid g2">
    <div class="card table-wrap"><h3>Drift</h3><table><thead><tr><th>Holding</th><th class="num">Actual</th><th class="num">Target</th><th class="num">Drift</th><th>Status</th></tr></thead><tbody>
      ${r.drift.rows.map(x => `<tr class="${x.breach ? "breach" : ""}"><td><b>${x.symbol}</b><div class="muted small">${esc(x.asset_class)}</div></td><td class="num">${pct(x.weight)}</td><td class="num">${pct(x.target)}</td><td class="num ${x.breach ? "neg" : ""}">${pct(x.drift, 1, true)}</td><td class="small">${x.breach ? esc(x.why) : "OK"}</td></tr>`).join("")}</tbody></table></div>
    <div class="card table-wrap"><h3>Proposed trades</h3>${r.trades.length ? `<table><thead><tr><th>Side</th><th>Symbol</th><th class="num">Qty</th><th class="num">Price</th><th class="num">Value</th><th class="num">Weight</th><th class="num">Est. gain</th></tr></thead><tbody>
      ${r.trades.map(t => `<tr><td class="${t.side === "buy" ? "pos" : "neg"}">${t.side.toUpperCase()}</td><td><b>${t.symbol}</b></td><td class="num">${num(t.qty, 4)}</td><td class="num">${money(t.price)}</td><td class="num">${money(t.value)}</td><td class="num small">${pct(t.from_weight)} → ${pct(t.to_weight)}</td><td class="num ${cls(t.est_realized_gain)}">${t.side === "sell" ? money(t.est_realized_gain) : ""}</td></tr>`).join("")}</tbody></table>
      <table style="margin-top:12px"><tbody><tr><td>Sells / buys</td><td class="num">${money(s.sell_value)} / ${money(s.buy_value)}</td></tr><tr><td>Turnover</td><td class="num">${pct(s.turnover)}</td></tr>
      <tr><td>Estimated realized gains (tax impact)</td><td class="num ${cls(s.est_realized_gains)}">${money(s.est_realized_gains)}</td></tr><tr><td>Cash after (est.)</td><td class="num">${money(s.cash_after_est)}</td></tr>
      ${s.buy_scale < 0.999 ? `<tr><td colspan="2" class="small muted">Buys scaled to ${pct(s.buy_scale, 0)} to respect the cash buffer.</td></tr>` : ""}</tbody></table>` : `<div class="muted">No trades needed.</div>`}</div></div>`;
  $("#rb-mode").onchange = e => rebalanceTab(el, id, e.target.value);
  $("#rb-go").onclick = async () => {
    if (!confirm(`Execute ${s.n_trades} paper trades?`)) return;
    try { const x = await api(`/clients/${id}/rebalance`, { method: "POST", body: { mode } });
      if (window.showRebalanceFlow) await window.showRebalanceFlow(r, x);
      const bad = x.executed.filter(e => e.status !== "filled");
      toast(`Rebalanced: ${x.executed.length - bad.length} filled${bad.length ? `, ${bad.length} failed` : ""}`, !!bad.length); rebalanceTab(el, id, mode);
    } catch (e) { toast(e.message, true); }
  };
}

// ---------------------------------------------------------------- research
async function researchView(sym, clientId, el) {
  el = el || view;
  el.innerHTML = `${clientId ? "" : `<h1>Stock research</h1><div class="sub">DCF + peer multiples + quality + momentum → Buy / Hold / Sell. Open research from a client to get a client-specific action.</div>`}
    <div class="card" style="margin-bottom:16px"><div class="inline-form"><div class="field r-field"><label>Ticker or company name</label><input id="r-sym" value="${esc(sym || "")}" placeholder="e.g. AAPL, Apple, Reliance" autocomplete="off" spellcheck="false" data-gramm="false" data-gramm_editor="false" data-enable-grammarly="false" style="width:280px"><div class="r-suggest" id="r-suggest"></div></div>
    <button class="btn primary" id="r-go">Analyze</button><span class="muted small" id="r-hint"></span></div>
    <div class="r-quick" id="r-quick"><span class="muted small">Quick pick:</span></div>
    ${CONFIG.data_mode === "live" ? `<div class="muted small" style="margin-top:10px">Financial statements, analyst estimates and price targets: ${CONFIG.fmp_key ? "FMP, with Yahoo Finance as backup" : "Yahoo Finance (free)"}. Works for US and international tickers, e.g. AAPL, RELIANCE.NS, TSM.</div>` : ""}</div><div id="r-out"></div>`;
  const inp = $("#r-sym"), sug = $("#r-suggest"), hint = $("#r-hint");
  const open = s => {
    s = (s || "").trim().toUpperCase();
    if (!s) { hint.textContent = "Type a ticker or company name first."; hint.classList.add("neg"); inp.focus(); return; }
    const h = clientId ? `#/client/${clientId}/research/${encodeURIComponent(s)}` : `#/research/${encodeURIComponent(s)}`;
    sug.innerHTML = ""; sug.classList.remove("on");
    if (location.hash === h) researchView(s, clientId, el); else location.hash = h;
  };
  let pick = -1, results = [], timer = null;
  const drawSug = () => {
    sug.innerHTML = results.map((r, i) => `<button type="button" class="r-sug ${i === pick ? "on" : ""}" data-s="${esc(r.symbol)}"><b>${esc(r.symbol)}</b><span>${esc(r.name || "")}</span><em>${esc(r.exchange || "")}</em></button>`).join("");
    sug.classList.toggle("on", results.length > 0);
    sug.querySelectorAll(".r-sug").forEach(b => b.onmousedown = e => { e.preventDefault(); open(b.dataset.s); });
  };
  inp.addEventListener("input", () => {
    hint.textContent = ""; hint.classList.remove("neg");
    clearTimeout(timer);
    const q = inp.value.trim();
    if (q.length < 2) { results = []; drawSug(); return; }
    timer = setTimeout(async () => {
      try { const r = await api(`/search?q=${encodeURIComponent(q)}`); if (inp.value.trim() !== q) return;
        results = (r || []).filter(x => !/index|currency|future|crypto|commodit/i.test((x.type || "") + " " + (x.exchange || ""))).slice(0, 7); pick = -1; drawSug(); } catch (e) { /* keep typing */ }
    }, 250);
  });
  inp.addEventListener("keydown", e => {
    if (e.key === "ArrowDown" && results.length) { e.preventDefault(); pick = Math.min(pick + 1, results.length - 1); drawSug(); }
    else if (e.key === "ArrowUp" && results.length) { e.preventDefault(); pick = Math.max(pick - 1, -1); drawSug(); }
    else if (e.key === "Enter") { e.preventDefault(); open(pick >= 0 && results[pick] ? results[pick].symbol : inp.value); }
    else if (e.key === "Escape") { results = []; drawSug(); }
  });
  inp.addEventListener("blur", () => setTimeout(() => sug.classList.remove("on"), 150));
  $("#r-go").onclick = () => open(pick >= 0 && results[pick] ? results[pick].symbol : inp.value);
  (async () => {
    let syms = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "JPM", "RELIANCE.NS", "TCS.NS", "INFY.NS"];
    try { const wl = await api("/watchlists"); if (wl && wl[0]) { const w = await api(`/watchlists/${wl[0].id}`);
      const mine = (w.items || w.rows || []).map(x => x.symbol).filter(x => x && !x.startsWith("^") && !/USD$/.test(x));
      syms = [...new Set([...mine, ...syms])].slice(0, 12); } } catch (e) { /* defaults */ }
    const q = $("#r-quick"); if (!q) return;
    q.insertAdjacentHTML("beforeend", syms.map(x => `<button type="button" class="chipbtn" data-q="${esc(x)}">${esc(x)}</button>`).join(""));
    q.querySelectorAll("[data-q]").forEach(b => b.onclick = () => open(b.dataset.q));
  })();
  if (!sym) inp.focus();
  if (!sym) return;
  $("#r-out").innerHTML = `<div class="loading">Building financial model for ${esc(sym)}… (first run pulls statements, prices and peers)</div>`;
  let r;
  try { r = await api(`/research/${sym}${clientId ? `?client_id=${clientId}` : ""}`); }
  catch (e) { $("#r-out").innerHTML = `<div class="card neg">${esc(e.message)}</div>`; return; }
  setCurrency((r.quote && r.quote.currency) || "USD");
  renderResearch($("#r-out"), r, clientId);
  if (window.dcfShow) $("#r-out").insertAdjacentHTML("afterbegin", dcfShow(r));
}

function renderResearch(out, r, clientId) {
  const bg = { BUY: "var(--buy-bg)", SELL: "var(--sell-bg)", HOLD: "var(--hold-bg)" }[r.rating];
  const cv = r.client_view, a = r.assumptions || {};
  const scoreBar = (l, v) => `<div class="scorebar"><span>${l}</span><div class="bar"><span style="width:${v ?? 0}%"></span></div><span class="mono">${v == null ? "—" : Math.round(v)}</span></div>`;
  out.innerHTML = `
  <div class="grid g3">
    <div class="card"><div class="muted small">${esc(r.sector)}${r.industry ? " · " + esc(r.industry) : ""}</div><h2 style="margin:4px 0 10px">${esc(r.name)} (${r.symbol})</h2>
      <div class="big-rating r-${r.rating}" style="background:${bg}">${r.rating}</div> <span class="muted">conviction: ${r.conviction}</span>
      <table style="margin-top:12px"><tbody><tr><td>Price</td><td class="num">${money(r.price)}</td></tr>
        <tr><td>Fair value</td><td class="num"><b>${money(r.fair_value)}</b></td></tr><tr><td>Upside</td><td class="num ${cls(r.upside)}">${pct(r.upside, 1, true)}</td></tr>
        ${r.bull_value ? `<tr><td>Bear / Bull (DCF)</td><td class="num">${money(r.bear_value)} / ${money(r.bull_value)}</td></tr>` : ""}
        ${r.street && r.street.price_target ? `<tr><td>Street target (${money(r.street.price_target.low, 0)}–${money(r.street.price_target.high, 0)})</td><td class="num">${money(r.street.price_target.consensus)} <span class="small ${cls(r.street.price_target.consensus / r.price - 1)}">${pct(r.street.price_target.consensus / r.price - 1, 1, true)}</span></td></tr>` : ""}
        ${r.street && r.street.forward_pe ? `<tr><td>Forward P/E · consensus EPS growth</td><td class="num">${num(r.street.forward_pe, 1)}x · ${pct(r.street.eps_cagr)}</td></tr>` : ""}
        <tr><td class="small muted" colspan="2">${esc(r.valuation_method)}</td></tr></tbody></table></div>
    <div class="card"><h3>Scores</h3>${scoreBar("Valuation", r.scores.valuation)}${scoreBar("Quality", r.scores.quality)}${scoreBar("Momentum", r.scores.momentum)}${scoreBar("Composite", r.scores.composite)}
      <h3 style="margin-top:14px">Why</h3><ul style="margin:0;padding-left:18px">${r.reasons.map(x => `<li>${esc(x)}</li>`).join("")}</ul>
      ${r.notes.length ? `<div class="muted small" style="margin-top:8px">${r.notes.map(esc).join("<br>")}</div>` : ""}</div>
    <div class="card"><h3>${cv ? `Action for ${esc(cv.client)}` : "Client action"}</h3>${cv ? `
      <div style="font-size:18px;font-weight:700;margin-bottom:8px">${esc(cv.action)}</div>
      <table><tbody><tr><td>Profile</td><td class="num">${esc(cv.profile)}</td></tr><tr><td>Current weight</td><td class="num">${pct(cv.current_weight)}</td></tr>
        ${cv.max_position != null ? `<tr><td>Single-stock limit</td><td class="num">${pct(cv.max_position, 0)}</td></tr><tr><td>Stock sleeve used / cap</td><td class="num">${pct(cv.stock_sleeve_used)} / ${pct(cv.stock_sleeve_cap, 0)}</td></tr>` : ""}
        ${cv.suggested_weight ? `<tr><td>Suggested size</td><td class="num">${pct(cv.suggested_weight)} ≈ ${num(cv.suggested_shares, 2)} sh</td></tr>` : ""}</tbody></table>
      ${cv.warnings.map(w => `<div class="alert warning" style="margin-top:8px">${esc(w)}</div>`).join("")}
      <div class="inline-form" style="margin-top:12px">
        ${cv.suggested_weight ? `<button class="btn buy" id="r-buy">Buy ${pct(cv.suggested_weight)} now</button><button class="btn" id="r-model">Add ${pct(cv.suggested_weight)} to model</button>` : ""}
        <a class="btn" href="#/client/${clientId}/trade/${r.symbol}">Open ticket</a></div>`
      : `<div class="muted">Open this from a client's Research tab to see suitability, position limits and a sized recommendation.</div>`}</div>
  </div>
  <div class="grid g2" style="margin-top:16px">
    <div class="card"><h3>Price, 50/200-day averages (2y)</h3><div class="chart-box"><canvas id="r-px"></canvas></div>
      <table style="margin-top:10px"><tbody><tr><td>1M / 3M / 6M / 1Y</td><td class="num">${[r.technicals.ret_1m, r.technicals.ret_3m, r.technicals.ret_6m, r.technicals.ret_1y].map(x => `<span class="${cls(x)}">${pct(x, 1, true)}</span>`).join(" / ")}</td></tr>
      <tr><td>12-1M momentum · RSI(14)</td><td class="num">${pct(r.technicals.mom_12_1, 1, true)} · ${num(r.technicals.rsi14, 0)}</td></tr><tr><td>Volatility (1y) · Max DD (2y)</td><td class="num">${pct(r.technicals.vol_1y)} · ${pct(r.technicals.max_dd_2y)}</td></tr></tbody></table></div>
    ${r.is_etf ? "" : `<div class="card"><h3>Valuation assumptions (WACC ${pct(a.wacc, 2)})</h3><table><tbody>
      <tr><td>Risk-free / ERP / Beta</td><td class="num">${pct(a.risk_free, 2)} / ${pct(a.erp, 1)} / ${num(a.beta)}</td></tr>
      <tr><td>Cost of equity / after-tax debt</td><td class="num">${pct(a.cost_of_equity, 2)} / ${pct(a.cost_of_debt_pre_tax * (1 - a.tax_rate), 2)}</td></tr>
      <tr><td>Debt weight · tax rate</td><td class="num">${pct(a.debt_weight)} · ${pct(a.tax_rate)}</td></tr>
      <tr><td>Revenue growth path</td><td class="small" style="text-align:right">${esc(a.growth_source)}</td></tr>
      <tr><td>Historical revenue CAGR</td><td class="num">${pct(a.historical_cagr)}</td></tr>
      <tr><td>FCF margin today → steady state</td><td class="num">${pct(a.fcf_margin)} → ${pct(a.fcf_margin_terminal)}</td></tr>
      <tr><td>Terminal growth</td><td class="num">${pct(a.terminal_growth, 1)}</td></tr><tr><td>Net debt · diluted shares</td><td class="num">${big(a.net_debt)} · ${big(a.shares)}</td></tr>
      <tr><td>Market cap</td><td class="num">${big(a.market_cap)}</td></tr></tbody></table>
      ${r.sensitivity ? `<h3 style="margin-top:14px">DCF sensitivity ($/share)</h3><table class="heat"><thead><tr><th>WACC \\ g</th>${r.sensitivity.g.map(g => `<th class="num">${pct(g, 1)}</th>`).join("")}</tr></thead><tbody>
        ${r.sensitivity.wacc.map((w, i) => `<tr><td><b>${pct(w, 1)}</b></td>${r.sensitivity.values[i].map(v => `<td style="background:${v > r.price ? "#ecf7ef" : "#fde8e6"}">${money(v, 0)}</td>`).join("")}</tr>`).join("")}</tbody></table>` : ""}</div>`}
  </div>
  ${r.is_etf ? "" : `
  <div class="grid g2" style="margin-top:16px">
    <div class="card table-wrap"><h3>DCF projection</h3>${r.dcf ? `<table><thead><tr><th>Year</th><th>Source</th><th class="num">Growth</th><th class="num">Revenue</th><th class="num">FCF margin</th><th class="num">FCF</th><th class="num">PV</th></tr></thead><tbody>
      ${r.dcf.projection.map(p => `<tr><td>+${p.year}</td><td class="small ${p.source === "consensus" ? "pos" : "muted"}">${p.source}</td><td class="num">${pct(p.growth)}</td><td class="num">${big(p.revenue)}</td><td class="num">${pct(p.fcf_margin)}</td><td class="num">${big(p.fcf)}</td><td class="num">${big(p.pv_fcf)}</td></tr>`).join("")}
      <tr><td colspan="6">PV of terminal value (${pct(r.dcf.terminal_share_of_ev, 0)} of EV)</td><td class="num">${big(r.dcf.pv_terminal)}</td></tr>
      <tr><td colspan="6">Enterprise value − net debt = equity value</td><td class="num">${big(r.dcf.equity_value)}</td></tr>
      <tr><td colspan="6"><b>Intrinsic value per share</b></td><td class="num"><b>${money(r.dcf.per_share)}</b></td></tr></tbody></table>` : `<div class="muted">DCF not applicable.</div>`}</div>
    <div class="card table-wrap"><h3>Peer multiples</h3><table><thead><tr><th>Ticker</th><th class="num">P/E</th><th class="num">EV/EBITDA</th><th class="num">P/S</th><th class="num">P/FCF</th></tr></thead><tbody>
      ${[r.relative.own, ...r.relative.peers].map((p, i) => `<tr ${i === 0 ? 'style="font-weight:700"' : ""}><td>${p.symbol}</td><td class="num">${num(p.pe, 1)}</td><td class="num">${num(p.ev_ebitda, 1)}</td><td class="num">${num(p.ps, 1)}</td><td class="num">${num(p.pfcf, 1)}</td></tr>`).join("")}
      <tr><td class="muted">Peer median</td><td class="num">${num(r.relative.peer_medians.pe, 1)}</td><td class="num">${num(r.relative.peer_medians.ev_ebitda, 1)}</td><td class="num">${num(r.relative.peer_medians.ps, 1)}</td><td class="num">${num(r.relative.peer_medians.pfcf, 1)}</td></tr></tbody></table>
      <div class="small" style="margin-top:8px">Implied prices: ${Object.entries(r.relative.implied_prices).map(([k, v]) => `${k} ${money(v)}`).join(" · ") || "—"}</div>
      <h3 style="margin-top:16px">Piotroski F-score: ${r.piotroski.score ?? "—"}/9</h3>${Object.entries(r.piotroski.checks).map(([k, v]) => `<div class="small">${v ? '<span class="check">✓</span>' : '<span class="cross">✕</span>'} ${esc(k)}</div>`).join("")}</div>
  </div>
  <div class="card table-wrap" style="margin-top:16px"><h3>Historical financials</h3><table><thead><tr><th>FY</th><th class="num">Revenue</th><th class="num">Growth</th><th class="num">Gross m.</th><th class="num">Op. m.</th><th class="num">Net m.</th><th class="num">EPS</th><th class="num">FCF</th><th class="num">FCF m.</th><th class="num">ROE</th><th class="num">ROIC</th><th class="num">ND/EBITDA</th></tr></thead><tbody>
    ${(r.street && r.street.estimates || []).slice().reverse().map(e => `<tr style="background:#f2f7f2"><td>${e.date.slice(0, 4)}E</td><td class="num">${big(e.revenue)}</td><td class="num muted small" colspan="4">consensus · ${e.n_analysts} analysts</td><td class="num">${num(e.eps)}</td><td colspan="5"></td></tr>`).join("")}
    ${r.history.map(h => `<tr><td>${h.year}</td><td class="num">${big(h.revenue)}</td><td class="num">${pct(h.revenue_growth)}</td><td class="num">${pct(h.gross_margin)}</td><td class="num">${pct(h.operating_margin)}</td><td class="num">${pct(h.net_margin)}</td><td class="num">${num(h.eps)}</td><td class="num">${big(h.fcf)}</td><td class="num">${pct(h.fcf_margin)}</td><td class="num">${pct(h.roe)}</td><td class="num">${pct(h.roic)}</td><td class="num">${num(h.net_debt_ebitda, 1)}</td></tr>`).join("")}</tbody></table></div>`}
  ${r.description ? `<div class="card muted small" style="margin-top:16px">${esc(r.description)}</div>` : ""}`;

  const c = r.technicals.chart, closes = c.map(x => x.close);
  const sma = n => closes.map((_, i) => i < n - 1 ? null : closes.slice(i - n + 1, i + 1).reduce((a, b) => a + b, 0) / n);
  chart($("#r-px"), { type: "line", data: { labels: c.map(x => x.date), datasets: [
    { label: "Price", data: closes, borderColor: "#DD2F20", pointRadius: 0, borderWidth: 2 },
    { label: "SMA50", data: sma(50), borderColor: "#0a0a0a", pointRadius: 0, borderWidth: 1 },
    { label: "SMA200", data: sma(200), borderColor: "#737373", pointRadius: 0, borderWidth: 1, borderDash: [4, 3] },
    ...(r.fair_value ? [{ label: "Fair value", data: closes.map(() => r.fair_value), borderColor: "#16a34a", pointRadius: 0, borderWidth: 1, borderDash: [6, 4] }] : [])] },
    options: { maintainAspectRatio: false, interaction: { mode: "index", intersect: false }, scales: { x: { ticks: { maxTicksLimit: 6 } } } } });

  if (cv && cv.suggested_weight) {
    $("#r-buy").onclick = async () => { try { const o = await api(`/clients/${clientId}/orders`, { method: "POST", body: { symbol: r.symbol, side: "buy", qty: cv.suggested_shares } }); if (window.showTicket) await window.showTicket(o); toast(`Filled: bought ${num(o.qty, 4)} ${o.symbol} @ ${money(o.fill_price)}`); route(); } catch (e) { toast(e.message, true); } };
    $("#r-model").onclick = async () => { try { await api(`/clients/${clientId}/targets/add-stock`, { method: "POST", body: { symbol: r.symbol, weight: +cv.suggested_weight.toFixed(4) } }); toast(`${r.symbol} added to model — funded from core equity ETFs. Rebalance to implement.`); location.hash = `#/client/${clientId}/rebalance`; } catch (e) { toast(e.message, true); } };
  }
}

// ---------------------------------------------------------------- risk
async function riskTab(el, id) {
  const r = await api(`/clients/${id}/risk`);
  const c = r.current, t = r.target_model;
  const row = (l, k, f) => `<tr><td>${l}</td><td class="num">${c.available ? f(c[k]) : "—"}</td><td class="num">${t.available ? f(t[k]) : "—"}</td></tr>`;
  el.innerHTML = `<div class="grid g2"><div class="card"><h3>Current holdings vs target model (3y look-through)</h3><table><thead><tr><th></th><th class="num">Current</th><th class="num">Target model</th></tr></thead><tbody>
    ${row("Annual return", "annual_return", x => pct(x))}${row("Annual volatility", "annual_vol", x => pct(x))}${row("Sharpe", "sharpe", x => num(x))}${row("Sortino", "sortino", x => num(x))}
    ${row("Max drawdown", "max_drawdown", x => pct(x))}${row("Beta vs " + CONFIG.benchmark, "beta", x => num(x))}${row("1-day VaR 95%", "var95_1d", x => pct(x, 2))}${row("1-day CVaR 95%", "cvar95_1d", x => pct(x, 2))}</tbody></table>
    ${!c.available ? `<div class="muted small" style="margin-top:8px">Current: ${esc(c.reason)}</div>` : ""}</div>
    <div class="card"><h3>Risk contribution (share of portfolio variance)</h3><div class="chart-box"><canvas id="rc"></canvas></div></div></div>
    <div class="grid g2" style="margin-top:16px"><div class="card"><h3>Growth of $10,000</h3><div class="chart-box"><canvas id="g10"></canvas></div></div>
    <div class="card table-wrap"><h3>Correlation matrix</h3>${c.available ? corrTable(c.correlation) : `<div class="muted">Available once the portfolio holds securities.</div>`}</div></div>`;
  const src = c.available ? c : t;
  if (src.available) {
    const rc = Object.entries(src.risk_contribution);
    chart($("#rc"), { type: "bar", data: { labels: rc.map(x => x[0]), datasets: [{ data: rc.map(x => x[1] * 100), backgroundColor: "#DD2F20" }] },
      options: { indexAxis: "y", maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: { ticks: { callback: v => v + "%" } } } } });
    const ds = [];
    if (c.available) ds.push({ label: "Current", data: c.growth_of_10k.map(x => x.value), borderColor: "#DD2F20", pointRadius: 0, borderWidth: 2 });
    if (t.available) ds.push({ label: "Target model", data: t.growth_of_10k.map(x => x.value), borderColor: "#a3a3a3", pointRadius: 0, borderWidth: 2, borderDash: [5, 4] });
    chart($("#g10"), { type: "line", data: { labels: src.growth_of_10k.map(x => x.date), datasets: ds }, options: { maintainAspectRatio: false, scales: { x: { ticks: { maxTicksLimit: 6 } } } } });
  }
}
function corrTable(cm) {
  const col = v => v >= 0 ? `rgba(221,47,32,${Math.abs(v) * 0.55})` : `rgba(220,38,38,${Math.abs(v) * 0.55})`;
  return `<table class="heat"><thead><tr><th></th>${cm.symbols.map(s => `<th>${s}</th>`).join("")}</tr></thead><tbody>${cm.matrix.map((r, i) => `<tr><td><b>${cm.symbols[i]}</b></td>${r.map(v => `<td style="background:${col(v)}">${v.toFixed(2)}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}

// ---------------------------------------------------------------- settings
async function settingsTab(el, id, _a, client) {
  const [targets, models, scheds] = await Promise.all([api(`/clients/${id}/targets`), api("/models"), api("/billing/schedules")]);
  const cur = models.find(m => m.id === client.model_id);
  el.innerHTML = `<div class="card" style="margin-bottom:16px"><div class="flex-between"><div><h2 style="margin:0">Assigned model: ${cur ? esc(cur.name) : "Custom targets"}</h2>
      <div class="muted small">${cur ? `${esc(cur.category)} · exp. return ${pct(cur.expected_return)} · volatility ${pct(cur.volatility)} · fits ${cur.fits_profiles.join(", ") || "no profile"}` : "Targets were edited by hand"}</div></div>
      <div class="inline-form"><select id="m-sel">${models.map(m => `<option value="${m.id}" ${m.id === client.model_id ? "selected" : ""}>${esc(m.name)}${m.fits_profiles.includes(client.risk_profile) ? " ✓" : ""}</option>`).join("")}</select>
      <button class="btn primary" id="m-assign">Assign model</button><a class="btn" href="#/models">Browse marketplace</a></div></div>
      <div class="muted small" style="margin-top:6px">✓ = fits the client's ${esc(client.risk_profile)} risk band. Assigning replaces the target weights below; then rebalance.</div></div>
    <div class="grid g2">
    <div class="card"><h2>Target weights (invested assets)</h2>
      <table id="tg"><thead><tr><th>Symbol</th><th>Class</th><th class="num">Weight %</th><th></th></tr></thead><tbody>
      ${targets.map(t => `<tr><td><input class="tg-sym" value="${t.symbol}" style="width:90px;text-transform:uppercase"></td><td class="small">${esc(t.asset_class)}</td><td class="num"><input class="tg-w" type="number" step="0.1" value="${(t.weight * 100).toFixed(2)}" style="width:90px;text-align:right"></td><td><button class="btn sm danger tg-del">✕</button></td></tr>`).join("")}</tbody></table>
      <div class="flex-between" style="margin-top:10px"><div>Total: <b id="tg-sum"></b></div><div class="inline-form"><button class="btn sm" id="tg-add">+ Row</button><button class="btn sm" id="tg-reset">Reset to ${esc(client.risk_profile)} model</button><button class="btn sm primary" id="tg-save">Save targets</button></div></div>
      <div class="muted small" style="margin-top:8px">A ${pct(client.cash_target, 0)} cash buffer is held on top of these weights. Use Research → "Add to model" to carve individual stocks out of core equity within profile limits.</div></div>
    <div>
      <div class="card"><h2>Risk & monitoring settings</h2><div class="grid g2">
        <div class="field"><label>Risk profile</label><select id="s-prof">${CONFIG.profiles.map(p => `<option ${p.name === client.risk_profile ? "selected" : ""}>${p.name}</option>`).join("")}</select></div>
        <div class="field"><label>Cash buffer %</label><input id="s-cash" type="number" step="0.5" value="${client.cash_target * 100}"></div>
        <div class="field"><label>Drift band — absolute (pp)</label><input id="s-abs" type="number" step="0.5" value="${client.drift_abs_band * 100}"></div>
        <div class="field"><label>Drift band — relative %</label><input id="s-rel" type="number" step="1" value="${client.drift_rel_band * 100}"></div>
        <div class="field"><label>Stop-loss alert %</label><input id="s-stop" type="number" step="1" value="${client.stop_loss_pct * 100}"></div>
        <div class="field"><label>Max single stock %</label><input id="s-max" type="number" step="0.5" value="${client.max_position * 100}"></div>
        <div class="field"><label>Fee schedule</label><select id="s-fee">${scheds.map(f => `<option value="${f.id}" ${f.id === client.fee_schedule_id ? "selected" : ""}>${esc(f.name)}</option>`).join("")}</select></div>
        <div class="field"><label>Tax-lot method</label><select id="s-lot">${["FIFO", "HIFO", "LIFO"].map(m => `<option ${m === client.lot_method ? "selected" : ""}>${m}</option>`).join("")}</select></div>
        <div class="field"><label>Short-term tax rate %</label><input id="s-st" type="number" step="1" value="${(client.st_tax_rate * 100).toFixed(0)}"></div>
        <div class="field"><label>Long-term tax rate %</label><input id="s-lt" type="number" step="1" value="${(client.lt_tax_rate * 100).toFixed(0)}"></div></div>
        <div class="muted small" style="margin-bottom:10px">Changing the profile resets the target model to that profile's portfolio.</div>
        <button class="btn primary" id="s-save">Save settings</button></div>
      <div class="card" style="margin-top:16px"><h2>Account details</h2><div class="grid g2">
        <div class="field"><label>Account type</label><select id="a-type">${Object.entries(CONFIG.account_types).map(([k, v]) => `<option value="${k}" ${k === client.account_type ? "selected" : ""}>${k} (${v.tax})</option>`).join("")}</select></div>
        <div class="field"><label>Options level</label><select id="a-opt">${Object.entries(CONFIG.options_levels).map(([k, v]) => `<option value="${k}" ${+k === client.options_level ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></div>
        <div class="field"><label>Email</label><input id="a-email" value="${esc(client.email || "")}"></div><div class="field"><label>Phone</label><input id="a-phone" value="${esc(client.phone || "")}"></div></div>
        <div class="field"><label>Objective</label><input id="a-obj" value="${esc(client.objective || "")}" style="width:100%"></div>
        ${benEditor(client.beneficiaries || [])}<button class="btn primary" id="a-save" style="margin-top:10px">Save account details</button></div>
      <div class="card" style="margin-top:16px"><h2>Paper cash</h2><div class="inline-form"><div class="field"><label>Amount ($, negative to withdraw)</label><input id="cf-amt" type="number" step="100"></div><button class="btn" id="cf-go">Apply</button></div></div>
      <div class="card" style="margin-top:16px"><h2>Danger zone</h2><button class="btn danger" id="del">Delete client and all history</button></div>
    </div></div>`;
  const sum = () => { const s = $$(".tg-w").reduce((a, i) => a + (+i.value || 0), 0); $("#tg-sum").textContent = s.toFixed(2) + "%"; $("#tg-sum").className = Math.abs(s - 100) < 0.5 ? "pos" : "neg"; };
  const bindDel = () => $$(".tg-del").forEach(b => b.onclick = () => { b.closest("tr").remove(); sum(); });
  $("#tg").oninput = sum; sum(); bindDel();
  $("#tg-add").onclick = () => { $("#tg tbody").insertAdjacentHTML("beforeend", `<tr><td><input class="tg-sym" style="width:90px;text-transform:uppercase"></td><td></td><td class="num"><input class="tg-w" type="number" step="0.1" value="0" style="width:90px;text-align:right"></td><td><button class="btn sm danger tg-del">✕</button></td></tr>`); bindDel(); };
  $("#tg-save").onclick = async () => {
    const t = {}; $$("#tg tbody tr").forEach(tr => { const s = $(".tg-sym", tr).value.trim().toUpperCase(), w = +$(".tg-w", tr).value; if (s && w > 0) t[s] = (t[s] || 0) + w / 100; });
    try { await api(`/clients/${id}/targets`, { method: "PUT", body: { targets: t } }); toast("Targets saved — check the Rebalance tab"); route(); } catch (e) { toast(e.message, true); }
  };
  $("#tg-reset").onclick = async () => { try { await api(`/clients/${id}`, { method: "PATCH", body: { reset_targets_to_model: true } }); toast("Targets reset to model"); route(); } catch (e) { toast(e.message, true); } };
  $("#s-save").onclick = async () => {
    const body = { cash_target: +$("#s-cash").value / 100, drift_abs_band: +$("#s-abs").value / 100, drift_rel_band: +$("#s-rel").value / 100, stop_loss_pct: +$("#s-stop").value / 100, max_position: +$("#s-max").value / 100,
      fee_schedule_id: +$("#s-fee").value, lot_method: $("#s-lot").value, st_tax_rate: +$("#s-st").value / 100, lt_tax_rate: +$("#s-lt").value / 100 };
    if ($("#s-prof").value !== client.risk_profile) { if (!confirm("Change profile and reset targets to the new model?")) return; body.risk_profile = $("#s-prof").value; }
    try { await api(`/clients/${id}`, { method: "PATCH", body }); toast("Settings saved"); route(); } catch (e) { toast(e.message, true); }
  };
  bindBens();
  $("#a-save").onclick = async () => {
    try { await api(`/clients/${id}`, { method: "PATCH", body: { account_type: $("#a-type").value, options_level: +$("#a-opt").value, email: $("#a-email").value, phone: $("#a-phone").value, objective: $("#a-obj").value, beneficiaries: readBens() } });
      toast("Account details saved"); route(); } catch (e) { toast(e.message, true); }
  };
  $("#m-assign").onclick = async () => {
    try { const r = await api(`/clients/${id}/model/${$("#m-sel").value}`, { method: "POST" });
      if (r.suitability_warning) alert(r.suitability_warning);
      toast(`${r.model} assigned — review the Rebalance tab`); location.hash = `#/client/${id}/rebalance`; } catch (e) { toast(e.message, true); }
  };
  $("#cf-go").onclick = async () => { try { await api(`/clients/${id}/cash`, { method: "POST", body: { amount: +$("#cf-amt").value } }); toast("Cash updated"); route(); } catch (e) { toast(e.message, true); } };
  $("#del").onclick = async () => { if (!confirm(`Delete ${client.name}? This cannot be undone.`)) return; await api(`/clients/${id}`, { method: "DELETE" }); toast("Client deleted"); location.hash = "#/clients"; };
}


// ================================================================ WAVE 1: models, billing, tax, planning
const mixBar = mix => `<div class="mixbar">${Object.entries(mix).map(([k, v], i) => `<span title="${esc(k)} ${pct(v)}" style="width:${v * 100}%;background:${PALETTE[i % PALETTE.length]}"></span>`).join("")}</div>`;
const profPills = list => list.length ? list.map(p => `<span class="pill r-HOLD" style="margin:0 3px 3px 0">${esc(p)}</span>`).join("") : '<span class="muted small">outside all profile bands</span>';

// ---------------------------------------------------------------- model marketplace
async function modelsView(id) {
  id = (id || "").split("?")[0];
  if (id === "new") return modelEditor(null);
  if (id === "frontier") return frontierView();
  if (id) return modelDetail(+id);
  const models = await api("/models");
  const cats = [...new Set(models.map(m => m.category))];
  view.innerHTML = `<div class="flex-between"><div><h1>Model marketplace</h1><div class="sub">Pick a model, assign it to many clients, update it once and rebalance everyone together.</div></div>
    <div class="inline-form"><a class="btn red" href="#/models/frontier">◢ Efficient Frontier Lab</a><a class="btn primary" href="#/models/new">+ Build custom model</a></div></div>
    <a class="card fl-promo" href="#/models/frontier"><div><div class="eyebrow">New · Modern portfolio theory</div><h2>Build allocation-efficient models on the frontier.</h2>
      <p class="muted">Add any stocks or ETFs, see the efficient frontier and Capital Market Line, find the max-Sharpe and min-variance mixes, check how efficient your own weights are — then save the result as a model.</p></div><span class="btn red">Open the lab →</span></a>
    <div class="inline-form" style="margin-bottom:14px"><select id="mf-cat"><option value="">All categories</option>${cats.map(c => `<option>${esc(c)}</option>`).join("")}</select>
      <select id="mf-prof"><option value="">Any risk profile</option>${CONFIG.profiles.map(p => `<option>${p.name}</option>`).join("")}</select></div>
    <div class="grid g3" id="m-grid"></div>`;
  const draw = () => {
    const cat = $("#mf-cat").value, prof = $("#mf-prof").value;
    $("#m-grid").innerHTML = models.filter(m => (!cat || m.category === cat) && (!prof || m.fits_profiles.includes(prof))).map(m => `
      <div class="card model-card" onclick="location.hash='#/models/${m.id}'">
        <div class="flex-between"><span class="muted small">${esc(m.category)}${m.builtin ? "" : " · custom"}</span><span class="small">${m.clients.length} client${m.clients.length === 1 ? "" : "s"}</span></div>
        <h2 style="margin:6px 0 4px">${esc(m.name)}</h2><div class="muted small" style="min-height:34px">${esc(m.description || "")}</div>
        ${mixBar(m.asset_mix)}
        <div class="grid g2" style="margin:10px 0 8px;gap:8px"><div><div class="muted small">Exp. return</div><b class="mono">${pct(m.expected_return)}</b></div><div><div class="muted small">Volatility</div><b class="mono">${pct(m.volatility)}</b></div></div>
        <div>${profPills(m.fits_profiles)}</div></div>`).join("") || '<div class="empty">No models match.</div>';
  };
  $("#mf-cat").onchange = draw; $("#mf-prof").onchange = draw; draw();
}

async function modelDetail(id) {
  const [m, clients] = await Promise.all([api(`/models/${id}`), api("/clients")]);
  const h = m.history;
  const others = clients.filter(c => !m.clients.some(x => x.id === c.id));
  view.innerHTML = `<a href="#/models" class="small">← Model marketplace</a>
    <div class="flex-between"><div><h1>${esc(m.name)}</h1><div class="sub">${esc(m.category)}${m.builtin ? " · built-in" : " · custom"} — ${esc(m.description || "")}</div></div>
    <div class="inline-form">${m.builtin ? "" : `<a class="btn" href="#/models/new?edit=${m.id}" id="md-edit">Edit</a><button class="btn danger" id="md-del">Delete</button>`}<button class="btn" id="md-dup">Duplicate${m.builtin ? " to customize" : ""}</button><a class="btn red" href="#/models/frontier?model=${m.id}">Optimise on the frontier</a></div></div>
    <div class="grid g4">${kpi("Expected return (CMA)", pct(m.expected_return), "long-run assumption")}${kpi("Expected volatility", pct(m.volatility), "fits " + (m.fits_profiles.join(", ") || "none"))}
      ${kpi("3y look-through return", h.available ? pct(h.annual_return) : "—", h.available ? `max drawdown ${pct(h.max_drawdown)}` : "")}${kpi("Clients on model", m.clients.length, m.clients.map(c => c.name).join(", ") || "none yet")}</div>
    <div class="grid g2" style="margin-top:16px">
      <div class="card"><h3>Holdings</h3><table><thead><tr><th>Symbol</th><th>Asset class</th><th class="num">Weight</th></tr></thead><tbody>
        ${Object.entries(m.holdings).sort((a, b) => b[1] - a[1]).map(([s, w]) => `<tr><td><a href="#/research/${s}"><b>${s}</b></a></td><td class="small">${esc(assetClassOf(s))}</td><td class="num">${pct(w)}</td></tr>`).join("")}</tbody></table>
        <h3 style="margin-top:16px">Asset mix</h3>${mixBar(m.asset_mix)}<div class="small" style="margin-top:6px">${Object.entries(m.asset_mix).map(([k, v], i) => `<span style="color:${PALETTE[i % PALETTE.length]}">■</span> ${esc(k)} ${pct(v)}`).join(" &nbsp; ")}</div></div>
      <div class="card"><h3>Clients</h3>${m.clients.length ? `<table><tbody>${m.clients.map(c => `<tr><td><a href="#/client/${c.id}/rebalance">${esc(c.name)}</a></td></tr>`).join("")}</tbody></table>` : '<div class="muted">No clients on this model yet.</div>'}
        <div class="inline-form" style="margin-top:12px"><select id="md-client">${others.map(c => `<option value="${c.id}">${esc(c.name)} (${esc(c.risk_profile)})</option>`).join("")}</select><button class="btn primary" id="md-assign" ${others.length ? "" : "disabled"}>Assign</button></div>
        <h3 style="margin-top:18px">Bulk actions</h3>
        <div class="inline-form"><button class="btn" id="md-push" ${m.clients.length ? "" : "disabled"}>Push weights to all clients</button><button class="btn" id="md-prev" ${m.clients.length ? "" : "disabled"}>Preview rebalance for all</button></div>
        <div id="md-bulk" style="margin-top:10px"></div></div>
    </div>
    <div class="card" style="margin-top:16px"><h3>Growth of $10,000 (3-year look-through)</h3><div class="chart-box"><canvas id="md-chart"></canvas></div></div>`;
  if (h.available) chart($("#md-chart"), { type: "line", data: { labels: h.growth_of_10k.map(x => x.date), datasets: [{ data: h.growth_of_10k.map(x => x.value), borderColor: "#DD2F20", pointRadius: 0, borderWidth: 2 }] }, options: { maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: { ticks: { maxTicksLimit: 6 } } } } });
  $("#md-dup").onclick = async () => { const d = await api(`/models/${id}/duplicate`, { method: "POST" }); toast("Copy created — edit it"); location.hash = `#/models/new?edit=${d.id}`; };
  if ($("#md-del")) $("#md-del").onclick = async () => { if (!confirm("Delete this model? Clients on it keep their current targets.")) return; await api(`/models/${id}`, { method: "DELETE" }); location.hash = "#/models"; };
  $("#md-assign").onclick = async () => { try { const r = await api(`/clients/${$("#md-client").value}/model/${id}`, { method: "POST" }); if (r.suitability_warning) alert(r.suitability_warning); toast(`Assigned to ${m.name}`); route(); } catch (e) { toast(e.message, true); } };
  $("#md-push").onclick = async () => { const r = await api(`/models/${id}/push`, { method: "POST" }); toast(`Weights pushed to ${r.updated_clients.length} client(s)`); };
  $("#md-prev").onclick = async () => {
    const r = await api(`/models/${id}/rebalance`);
    $("#md-bulk").innerHTML = `<table><thead><tr><th>Client</th><th>Status</th><th class="num">Trades</th><th class="num">Turnover</th></tr></thead><tbody>${r.clients.map(c => `<tr><td>${esc(c.name)}</td><td>${c.needs_rebalance ? '<span class="pill r-SELL">drifted</span>' : '<span class="pill r-BUY">in band</span>'}</td><td class="num">${c.trades}</td><td class="num">${pct(c.turnover)}</td></tr>`).join("")}</tbody></table>
      <button class="btn primary" id="md-exec" style="margin-top:10px">Rebalance all ${r.clients.length} client(s) now</button>`;
    $("#md-exec").onclick = async () => { if (!confirm("Execute paper rebalances for every client on this model?")) return; const x = await api(`/models/${id}/rebalance`, { method: "POST", body: { mode: "full" } }); toast(`Done: ${x.clients.map(c => `${c.name} ${c.filled} filled`).join(", ")}`); route(); };
  };
}
function assetClassOf(s) { return (CONFIG.asset_classes && CONFIG.asset_classes[s]) || "Stock / other"; }

async function modelEditor() {
  const editId = +(new URLSearchParams(location.hash.split("?")[1] || "").get("edit") || 0);
  const m = editId ? await api(`/models/${editId}`) : { name: "", category: "Custom", description: "", holdings: { VTI: 0.6, VXUS: 0.2, BND: 0.2 } };
  view.innerHTML = `<a href="#/models" class="small">← Model marketplace</a><h1>${editId ? "Edit model" : "Build a custom model"}</h1><div class="sub">Weights must total 100%. Expected risk/return updates as you type.</div>
    <div class="grid g2"><div class="card">
      <div class="grid g2"><div class="field"><label>Name</label><input id="me-name" value="${esc(m.name)}" style="width:100%"></div><div class="field"><label>Category</label><input id="me-cat" value="${esc(m.category)}" style="width:100%"></div></div>
      <div class="field"><label>Description</label><input id="me-desc" value="${esc(m.description || "")}" style="width:100%"></div>
      <table id="me-tbl"><thead><tr><th>Symbol</th><th class="num">Weight %</th><th></th></tr></thead><tbody>${Object.entries(m.holdings).map(([s, w]) => meRow(s, w * 100)).join("")}</tbody></table>
      <div class="flex-between" style="margin-top:10px"><div>Total: <b id="me-sum"></b></div><div class="inline-form"><button class="btn sm" id="me-add">+ Holding</button><button class="btn primary" id="me-save">Save model</button></div></div></div>
      <div class="card" id="me-stats"><h3>Live preview</h3><div class="muted">Enter holdings…</div></div></div>`;
  const collect = () => { const h = {}; $$("#me-tbl tbody tr").forEach(tr => { const s = $(".me-s", tr).value.trim().toUpperCase(), w = +$(".me-w", tr).value; if (s && w > 0) h[s] = (h[s] || 0) + w / 100; }); return h; };
  const refresh = () => {
    const h = collect(), tot = Object.values(h).reduce((a, b) => a + b, 0);
    $("#me-sum").textContent = pct(tot); $("#me-sum").className = Math.abs(tot - 1) < 0.005 ? "pos" : "neg";
    const mix = {}; Object.entries(h).forEach(([s, w]) => { const k = assetClassOf(s); mix[k] = (mix[k] || 0) + w; });
    $("#me-stats").innerHTML = `<h3>Live preview</h3>${mixBar(mix)}<table style="margin-top:10px"><tbody>${Object.entries(mix).sort((a, b) => b[1] - a[1]).map(([k, v]) => `<tr><td>${esc(k)}</td><td class="num">${pct(v)}</td></tr>`).join("")}</tbody></table>
      <div class="muted small" style="margin-top:8px">Expected return, volatility and profile fit are calculated when you save.</div>`;
  };
  const bind = () => $$(".me-del").forEach(b => b.onclick = () => { b.closest("tr").remove(); refresh(); });
  $("#me-tbl").oninput = refresh; bind(); refresh();
  $("#me-add").onclick = () => { $("#me-tbl tbody").insertAdjacentHTML("beforeend", meRow("", 0)); bind(); };
  $("#me-save").onclick = async () => {
    const body = { name: $("#me-name").value.trim(), category: $("#me-cat").value.trim() || "Custom", description: $("#me-desc").value.trim(), holdings: collect() };
    try { const r = await api(editId ? `/models/${editId}` : "/models", { method: editId ? "PUT" : "POST", body });
      toast("Model saved" + (editId && r.clients.length ? " — use 'Push weights' to update its clients" : "")); location.hash = `#/models/${r.id}`; } catch (e) { toast(e.message, true); }
  };
}
const meRow = (s, w) => `<tr><td><input class="me-s" value="${esc(s)}" style="width:110px;text-transform:uppercase"></td><td class="num"><input class="me-w" type="number" step="0.5" value="${(+w).toFixed(2)}" style="width:100px;text-align:right"></td><td><button class="btn sm danger me-del">✕</button></td></tr>`;

// ---------------------------------------------------------------- firm billing
async function billingView() {
  const [sum, pv, scheds, invs] = await Promise.all([api("/billing/summary"), api("/billing/preview"), api("/billing/schedules"), api("/billing/invoices")]);
  view.innerHTML = `<h1>Billing</h1><div class="sub">Advisory fees are billed in arrears on average daily assets, pro-rated by days, and deducted from each client's cash. Cash earns ${pct(CONFIG.cash_interest_rate, 2)} APY.</div>
    <div class="grid g4">${kpi("Assets under management", money(sum.total_aum, 0), `${sum.clients.length} clients`)}${kpi("Projected annual revenue", money(sum.projected_annual_revenue, 0), `blended ${pct(sum.blended_rate, 2)}`)}
      ${kpi("Fees collected", money(sum.collected), "all time")}${kpi("Outstanding", money(sum.outstanding), "unpaid invoices", sum.outstanding > 0 ? -1 : null)}</div>
    <div class="card" style="margin-top:16px"><div class="flex-between"><h2 style="margin:0">Run billing</h2>
      <div class="inline-form"><div class="field"><label>From</label><input type="date" id="b-s" value="${pv.period_start}"></div><div class="field"><label>To</label><input type="date" id="b-e" value="${pv.period_end}"></div>
      <button class="btn" id="b-prev">Preview</button><button class="btn primary" id="b-run">Bill clients</button></div></div><div id="b-table" class="table-wrap" style="margin-top:12px"></div></div>
    <div class="grid g2" style="margin-top:16px">
      <div class="card"><h2>Fee schedules</h2><table><thead><tr><th>Name</th><th>Tiers (annual)</th><th class="num">Clients</th></tr></thead><tbody>
        ${scheds.map(f => `<tr><td><b>${esc(f.name)}</b><div class="muted small">${esc(f.description || "")}</div></td><td class="small">${tierText(f.tiers)}</td><td class="num">${f.clients}</td></tr>`).join("")}</tbody></table>
        <h3 style="margin-top:16px">New schedule</h3><div class="field"><input id="fs-name" placeholder="Name, e.g. Tiered 0.85%" style="width:100%"></div>
        <div id="fs-tiers">${fsRow(1000000, 1)}${fsRow("", 0.75)}</div>
        <div class="inline-form"><button class="btn sm" id="fs-add">+ Tier</button><button class="btn sm primary" id="fs-save">Save schedule</button></div>
        <div class="muted small" style="margin-top:6px">Marginal tiers: each rate applies to the slice of assets in its band. Leave the last "up to" blank.</div></div>
      <div class="card table-wrap"><h2>Invoices</h2>${invs.length ? `<table><thead><tr><th>Client</th><th>Period</th><th class="num">Avg AUM</th><th class="num">Fee</th><th>Status</th><th></th></tr></thead><tbody>
        ${invs.map(i => `<tr><td>${esc(i.name)}</td><td class="small">${i.period_start} → ${i.period_end}</td><td class="num">${money(i.avg_aum, 0)}</td><td class="num">${money(i.fee)}</td><td>${statusPill(i.status)}</td>
          <td>${i.status === "unpaid" ? `<button class="btn sm" onclick="invAct(${i.id},'pay')">Pay</button> <button class="btn sm" onclick="invAct(${i.id},'waive')">Waive</button>` : ""}</td></tr>`).join("")}</tbody></table>` : '<div class="muted">No invoices yet — run billing above.</div>'}</div>
    </div>`;
  const drawPrev = r => { $("#b-table").innerHTML = `<table><thead><tr><th>Client</th><th>Schedule</th><th class="num">Days</th><th class="num">Avg AUM</th><th class="num">Eff. rate</th><th class="num">Fee</th><th></th></tr></thead><tbody>
      ${r.rows.map(x => `<tr><td>${esc(x.name)}</td><td class="small">${esc(x.schedule)}</td><td class="num">${x.days}</td><td class="num">${money(x.avg_aum, 0)}</td><td class="num">${pct(x.effective_rate, 2)}</td><td class="num"><b>${money(x.fee)}</b></td><td>${x.already_billed ? '<span class="muted small">already billed</span>' : ""}</td></tr>`).join("")}
      <tr><td colspan="5"><b>Total to bill</b></td><td class="num"><b>${money(r.total_fee)}</b></td><td></td></tr></tbody></table>`; };
  drawPrev(pv);
  $("#b-prev").onclick = async () => drawPrev(await api(`/billing/preview?period_start=${$("#b-s").value}&period_end=${$("#b-e").value}`));
  $("#b-run").onclick = async () => { if (!confirm("Create invoices and deduct fees from client cash for this period?")) return;
    const r = await api("/billing/run", { method: "POST", body: { period_start: $("#b-s").value, period_end: $("#b-e").value } });
    const c = s => r.results.filter(x => x.status === s).length; toast(`Billed: ${c("paid")} paid, ${c("unpaid")} unpaid, ${c("skipped")} skipped`, c("unpaid") > 0); route(); };
  $("#fs-add").onclick = () => $("#fs-tiers").insertAdjacentHTML("beforeend", fsRow("", 0.5));
  $("#fs-save").onclick = async () => {
    const tiers = $$(".fs-row").map(r => [$(".fs-up", r).value ? +$(".fs-up", r).value : null, +$(".fs-rate", r).value / 100]);
    try { await api("/billing/schedules", { method: "POST", body: { name: $("#fs-name").value.trim(), tiers } }); toast("Schedule saved"); route(); } catch (e) { toast(e.message, true); }
  };
}
const tierText = t => t.map((x, i) => `${(x[1] * 100).toFixed(2)}% ${x[0] == null ? (i ? "above" : "on all assets") : "up to " + money(x[0], 0)}`).join("<br>");
const fsRow = (up, rate) => `<div class="inline-form fs-row" style="margin-bottom:6px"><div class="field"><label>Up to ($)</label><input class="fs-up" type="number" value="${up}" placeholder="blank = no limit" style="width:150px"></div><div class="field"><label>Annual rate %</label><input class="fs-rate" type="number" step="0.05" value="${rate}" style="width:100px"></div></div>`;
const statusPill = s => `<span class="pill ${s === "paid" ? "r-BUY" : s === "unpaid" ? "r-SELL" : "r-HOLD"}">${s}</span>`;
window.invAct = async (id, act) => { try { await api(`/billing/invoices/${id}/${act}`, { method: "POST" }); toast(`Invoice ${act === "pay" ? "paid" : "waived"}`); route(); } catch (e) { toast(e.message, true); } };

// ---------------------------------------------------------------- client billing tab
async function clientBillingTab(el, id, _a, client) {
  const b = await api(`/clients/${id}/billing`);
  const cp = b.current_period;
  el.innerHTML = `<div class="grid g4">${kpi("Fee schedule", esc(b.schedule.name), tierText(b.schedule.tiers).replace(/<br>/g, " · "))}${kpi("Accrued fee this period", money(cp.fee), `${cp.period_start} → ${cp.period_end} · ${pct(cp.effective_rate, 2)}/yr`)}
      ${kpi("Cash interest YTD", money(b.interest.interest_ytd), `${pct(b.interest.apy, 2)} APY on uninvested cash`, b.interest.interest_ytd)}${kpi("Fees paid YTD", money(b.interest.fees_ytd), "deducted from cash")}</div>
    <div class="grid g2" style="margin-top:16px">
      <div class="card table-wrap"><h3>Invoices</h3>${b.invoices.length ? `<table><thead><tr><th>Period</th><th class="num">Days</th><th class="num">Avg AUM</th><th class="num">Fee</th><th>Status</th><th></th></tr></thead><tbody>${b.invoices.map(i => `<tr><td class="small">${i.period_start} → ${i.period_end}</td><td class="num">${i.days}</td><td class="num">${money(i.avg_aum, 0)}</td><td class="num">${money(i.fee)}</td><td>${statusPill(i.status)}</td><td>${i.status === "unpaid" ? `<button class="btn sm" onclick="invAct(${i.id},'pay')">Pay</button>` : ""}</td></tr>`).join("")}</tbody></table>` : '<div class="muted">No invoices yet. Bill from the Billing page.</div>'}
        <div class="muted small" style="margin-top:10px">Change the schedule in Settings. Billing runs firm-wide from the <a href="#/billing">Billing</a> page.</div></div>
      <div class="card table-wrap"><h3>Cash activity</h3><table><thead><tr><th>Date</th><th>Type</th><th>Description</th><th class="num">Amount</th></tr></thead><tbody>
        ${b.activity.slice(0, 80).map(a => `<tr><td class="small">${(a.date || "").slice(0, 10)}</td><td><span class="pill ${a.amount >= 0 ? "r-BUY" : "r-HOLD"}">${a.type}</span></td><td class="small">${esc(a.description)}</td><td class="num ${cls(a.amount)}">${money(a.amount)}</td></tr>`).join("")}</tbody></table></div></div>`;
}

// ---------------------------------------------------------------- tax tab
async function taxTab(el, id, _a, client) {
  const t = await api(`/clients/${id}/tax`);
  const byTerm = t.lots.reduce((a, l) => (a[l.term] = (a[l.term] || 0) + l.unrealized, a), {});
  el.innerHTML = `<div class="grid g4">${kpi(`Realized ${t.year} — short-term`, money(t.short_term), `taxed at ~${pct(t.st_rate, 0)}`, t.short_term)}${kpi(`Realized ${t.year} — long-term`, money(t.long_term), `taxed at ~${pct(t.lt_rate, 0)}`, t.long_term)}
      ${kpi("Estimated tax due", money(t.estimated_tax), t.wash_sale_disallowed ? `${money(t.wash_sale_disallowed)} disallowed (wash sales)` : "after netting gains and losses")}${kpi("Unrealized ST / LT", `${money(byTerm.ST || 0, 0)} / ${money(byTerm.LT || 0, 0)}`, `lot method ${t.lot_method}`)}</div>
    <div class="card" style="margin-top:16px"><div class="flex-between"><h2 style="margin:0">Tax-loss harvesting</h2><span class="muted small">Sells losing lots and buys a similar ETF so market exposure is unchanged. Losses ≥ ${money(200, 0)} and ≥ 5%.</span></div>
      ${t.harvest.length ? `<table style="margin-top:10px"><thead><tr><th>Security</th><th class="num">Shares</th><th class="num">Harvestable loss</th><th class="num">Est. tax savings</th><th>Replace with</th><th></th></tr></thead><tbody>
        ${t.harvest.map(h => `<tr><td><b>${h.symbol}</b>${h.wash_sale_risk ? `<div class="small neg">${esc(h.note)}</div>` : ""}</td><td class="num">${num(h.qty, 4)}</td><td class="num neg">${money(h.loss)}</td><td class="num pos">${money(h.est_tax_savings)}</td>
          <td><input class="h-rep" data-s="${h.symbol}" value="${h.replacement || ""}" style="width:90px;text-transform:uppercase"></td><td><button class="btn sm buy" onclick="doHarvest(${id},'${h.symbol}')">Harvest</button></td></tr>`).join("")}</tbody></table>`
      : '<div class="muted" style="margin-top:8px">No harvestable losses right now. The monitor will alert you when one appears.</div>'}</div>
    <div class="grid g2" style="margin-top:16px">
      <div class="card table-wrap"><h3>Open tax lots</h3><table><thead><tr><th>Symbol</th><th>Acquired</th><th class="num">Shares</th><th class="num">Cost/sh</th><th class="num">Unrealized</th><th>Term</th></tr></thead><tbody>
        ${t.lots.map(l => `<tr><td><b>${l.symbol}</b></td><td class="small">${l.acquired_at.slice(0, 10)}</td><td class="num">${num(l.qty_open, 4)}</td><td class="num">${money(l.cost_per_share)}</td><td class="num ${cls(l.unrealized)}">${money(l.unrealized)}<div class="small">${pct(l.unrealized_pct, 1, true)}</div></td><td>${l.term}${l.term === "ST" && l.days_to_lt < 60 ? `<div class="small muted">LT in ${l.days_to_lt}d</div>` : ""}</td></tr>`).join("") || '<tr><td colspan="6" class="muted">No open lots</td></tr>'}</tbody></table></div>
      <div class="card table-wrap"><h3>Realized ${t.year}</h3><table><thead><tr><th>Sold</th><th>Symbol</th><th class="num">Shares</th><th class="num">Proceeds</th><th class="num">Gain</th><th>Term</th></tr></thead><tbody>
        ${t.realized.map(r => `<tr><td class="small">${r.sold_at.slice(0, 10)}</td><td><b>${r.symbol}</b>${r.wash_sale ? ' <span class="pill r-SELL">wash</span>' : ""}</td><td class="num">${num(r.qty, 4)}</td><td class="num">${money(r.proceeds)}</td><td class="num ${cls(r.gain)}">${money(r.gain)}</td><td>${r.term}</td></tr>`).join("") || '<tr><td colspan="6" class="muted">No sales this year</td></tr>'}</tbody></table>
        <div class="muted small" style="margin-top:8px">Estimates only, not tax advice. Net capital losses deduct up to $3,000/yr; the rest carries forward.</div></div></div>`;
}
window.doHarvest = async (cid, sym) => {
  const rep = ($(`.h-rep[data-s="${sym}"]`) || {}).value || "";
  if (!confirm(`Harvest ${sym}: sell the losing lots and buy ${rep || "the replacement"} with the proceeds? (paper trades)`)) return;
  try { const r = await api(`/clients/${cid}/tax/harvest`, { method: "POST", body: { symbol: sym, replacement: rep || null } });
    toast(`Harvested ${money(-r.realized_loss)} loss — est. tax savings ${money(r.est_tax_savings)}`); route(); } catch (e) { toast(e.message, true); }
};

// ---------------------------------------------------------------- planning tab
async function planningTab(el, id) {
  const p = await api(`/clients/${id}/plan`);
  const a = p.assumptions;
  el.innerHTML = `<div class="flex-between" style="margin-bottom:12px"><div class="muted">Portfolio ${money(p.portfolio_value, 0)} · expected return ${pct(a.expected_return)} · volatility ${pct(a.volatility)} · inflation ${pct(p.inflation)} · ${p.simulations.toLocaleString()} Monte Carlo simulations</div>
      <button class="btn primary" id="g-new">+ Add goal</button></div><div id="g-form"></div>
    ${p.goals.length ? p.goals.map((r, i) => goalCard(r, i)).join("") : `<div class="card empty">No goals yet. Add one — e.g. "Retirement: $2,000,000 by 2055, saving $1,000/month".</div>`}`;
  p.goals.forEach((r, i) => {
    const lab = r.path.map(x => x.date.slice(0, 7));
    chart($(`#g-chart-${i}`), { type: "line", data: { labels: lab, datasets: [
      { label: "90th pct", data: r.path.map(x => x.p90), borderColor: "transparent", backgroundColor: "rgba(221,47,32,.10)", fill: "+4", pointRadius: 0 },
      { label: "75th", data: r.path.map(x => x.p75), borderColor: "transparent", backgroundColor: "rgba(221,47,32,.16)", fill: "+2", pointRadius: 0 },
      { label: "Median", data: r.path.map(x => x.p50), borderColor: "#DD2F20", borderWidth: 2, pointRadius: 0, fill: false },
      { label: "25th", data: r.path.map(x => x.p25), borderColor: "transparent", pointRadius: 0, fill: false },
      { label: "10th pct", data: r.path.map(x => x.p10), borderColor: "transparent", pointRadius: 0, fill: false },
      { label: "Target", data: r.path.map(() => r.target_nominal), borderColor: "#16a34a", borderDash: [6, 4], borderWidth: 1.5, pointRadius: 0, fill: false }] },
      options: { animation: false, maintainAspectRatio: false, plugins: { legend: { labels: { filter: it => ["Median", "Target", "90th pct", "10th pct"].includes(it.text) } }, tooltip: { callbacks: { label: c => `${c.dataset.label}: ${money(c.raw, 0)}` } } },
        scales: { x: { ticks: { maxTicksLimit: 8 } }, y: { ticks: { callback: v => "$" + (v >= 1e6 ? (v / 1e6).toFixed(1) + "M" : (v / 1e3).toFixed(0) + "k") } } } } });
  });
  $$(".g-del").forEach(b => b.onclick = async () => { if (!confirm("Delete this goal?")) return; await api(`/clients/${id}/goals/${b.dataset.id}`, { method: "DELETE" }); route(); });
  $$(".g-edit").forEach(b => b.onclick = () => goalForm(id, p.goals.find(r => r.goal.id === +b.dataset.id).goal));
  $("#g-new").onclick = () => goalForm(id, null);
}
function goalCard(r, i) {
  const g = r.goal, col = r.probability >= 0.8 ? "var(--pos)" : r.probability >= 0.5 ? "var(--warn)" : "var(--neg)";
  return `<div class="card" style="margin-bottom:16px"><div class="flex-between"><div><h2 style="margin:0">${esc(g.name)}</h2>
      <div class="muted small">${money(g.target_amount, 0)}${g.inflation_adjust ? " in today's dollars" : ""} by ${g.target_date} · ${money(g.monthly_contribution, 0)}/month · ${pct(g.allocation_pct, 0)} of portfolio</div></div>
      <div class="inline-form"><button class="btn sm g-edit" data-id="${g.id}">Edit</button><button class="btn sm danger g-del" data-id="${g.id}">Delete</button></div></div>
    <div class="grid" style="grid-template-columns:240px minmax(0,1fr);margin-top:12px">
      <div><div class="gauge" style="--p:${r.probability * 100};--c:${col}"><div><b>${(r.probability * 100).toFixed(0)}%</b><span>chance of success</span></div></div>
        <div style="text-align:center;margin:8px 0"><span class="pill" style="background:${col};color:#fff">${r.status.toUpperCase()}</span></div>
        <table class="small"><tbody><tr><td>Target (nominal)</td><td class="num">${money(r.target_nominal, 0)}</td></tr><tr><td>Median outcome</td><td class="num">${money(r.median_end, 0)}</td></tr>
          <tr><td>Bad case (10th pct)</td><td class="num">${money(r.p10_end, 0)}</td></tr><tr><td>Saving for 80% odds</td><td class="num"><b>${money(r.required_monthly_for_80pct, 0)}/mo</b></td></tr>
          ${r.extra_monthly_needed > 0 ? `<tr><td colspan="2" class="neg">Save ${money(r.extra_monthly_needed, 0)}/mo more to be on track</td></tr>` : ""}</tbody></table></div>
      <div><div class="chart-box"><canvas id="g-chart-${i}"></canvas></div>
        <table class="small" style="margin-top:8px"><thead><tr><th>If invested as…</th><th class="num">Exp. return</th><th class="num">Volatility</th><th class="num">Success</th></tr></thead><tbody>
          ${r.what_if.map(w => `<tr><td>${esc(w.profile)}</td><td class="num">${pct(w.expected_return)}</td><td class="num">${pct(w.volatility)}</td><td class="num"><b>${pct(w.probability, 0)}</b></td></tr>`).join("")}</tbody></table></div></div></div>`;
}
function goalForm(cid, g) {
  const y = new Date().getFullYear();
  g = g || { name: "Retirement", target_amount: 2000000, target_date: `${y + 25}-12-31`, monthly_contribution: 1000, allocation_pct: 1, inflation_adjust: 1 };
  $("#g-form").innerHTML = `<div class="card" style="margin-bottom:16px"><h3>${g.id ? "Edit goal" : "New goal"}</h3><div class="grid g3">
    <div class="field"><label>Goal</label><input id="gf-name" value="${esc(g.name)}" style="width:100%"></div>
    <div class="field"><label>Target amount ($)</label><input id="gf-amt" type="number" step="1000" value="${g.target_amount}" style="width:100%"></div>
    <div class="field"><label>Target date</label><input id="gf-date" type="date" value="${g.target_date}" style="width:100%"></div>
    <div class="field"><label>Monthly contribution ($)</label><input id="gf-mon" type="number" step="50" value="${g.monthly_contribution}" style="width:100%"></div>
    <div class="field"><label>Share of current portfolio for this goal (%)</label><input id="gf-alloc" type="number" step="5" value="${g.allocation_pct * 100}" style="width:100%"></div>
    <div class="field"><label>Target is in</label><select id="gf-infl"><option value="1" ${g.inflation_adjust ? "selected" : ""}>Today's dollars (inflation-adjusted)</option><option value="0" ${g.inflation_adjust ? "" : "selected"}>Future dollars</option></select></div></div>
    <div class="inline-form"><button class="btn primary" id="gf-save">Save & run simulation</button><button class="btn" id="gf-cancel">Cancel</button></div></div>`;
  $("#gf-cancel").onclick = () => $("#g-form").innerHTML = "";
  $("#gf-save").onclick = async () => {
    const body = { name: $("#gf-name").value.trim(), target_amount: +$("#gf-amt").value, target_date: $("#gf-date").value, monthly_contribution: +$("#gf-mon").value, allocation_pct: +$("#gf-alloc").value / 100, inflation_adjust: $("#gf-infl").value === "1" };
    try { await api(g.id ? `/clients/${cid}/goals/${g.id}` : `/clients/${cid}/goals`, { method: g.id ? "PUT" : "POST", body }); toast("Goal saved"); route(); } catch (e) { toast(e.message, true); }
  };
}

// ================================================================ WAVE 2: options, direct index, lending, reports
// ---------------------------------------------------------------- options
async function optionsTab(el, id, sym) {
  const st = await api(`/clients/${id}/options`);
  const symbol = (sym || "AAPL").toUpperCase();
  el.innerHTML = `<div class="grid g4">${kpi("Options level", "Level " + st.level, esc(st.levels[st.level]))}${kpi("Open contracts", st.positions.reduce((a, p) => a + Math.abs(p.qty), 0), `${st.positions.length} position(s)`)}
      ${kpi("Options value", money(st.positions.reduce((a, p) => a + p.market_value, 0)), `unrealized ${money(st.positions.reduce((a, p) => a + p.unrealized, 0))}`)}${kpi("Cash reserved for puts", money(st.csp_reserve), `buying power ${money(st.buying_power)}`)}</div>
    ${st.level === 0 ? `<div class="alert warning" style="margin-top:12px"><b>Options aren't enabled for this account.</b> Choose a level: <select id="o-lvl">${Object.entries(st.levels).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("")}</select> <button class="btn sm primary" id="o-lvl-go">Approve</button></div>` : ""}
    <div class="card" style="margin-top:16px"><div class="flex-between"><div class="inline-form"><div class="field"><label>Underlying</label><input id="o-sym" value="${symbol}" style="width:100px;text-transform:uppercase"></div>
      <div class="field"><label>Expiry</label><select id="o-exp"></select></div><button class="btn" id="o-load">Load chain</button></div><div class="muted small" id="o-spot"></div></div>
      <div class="muted small" style="margin:6px 0">Click a bid to sell or an ask to buy. Theoretical Black-Scholes prices from historical volatility — not live exchange quotes.</div>
      <div id="o-ticket"></div><div id="o-chain" class="table-wrap"></div></div>
    <div class="grid g2" style="margin-top:16px"><div class="card table-wrap"><h3>Positions</h3>${st.positions.length ? `<table><thead><tr><th>Contract</th><th>Strategy</th><th class="num">Qty</th><th class="num">Avg</th><th class="num">Mark</th><th class="num">P&L</th><th class="num">Δ sh</th><th class="num">Days</th><th></th></tr></thead><tbody>
      ${st.positions.map(p => `<tr><td><b>${esc(p.label)}</b></td><td class="small">${p.strategy}</td><td class="num">${p.qty}</td><td class="num">${money(p.avg_price)}</td><td class="num">${money(p.mark)}</td><td class="num ${cls(p.unrealized)}">${money(p.unrealized)}</td><td class="num">${num(p.delta, 0)}</td><td class="num">${p.days_left}</td>
        <td><button class="btn sm" onclick="optClose(${id},'${p.underlying}','${p.opt_type}',${p.strike},'${p.expiry}',${p.qty})">Close</button></td></tr>`).join("")}</tbody></table>` : '<div class="muted">No open option positions.</div>'}</div>
      <div class="card table-wrap"><h3>Option activity</h3>${st.trades.length ? `<table><thead><tr><th>Date</th><th>Action</th><th>Contract</th><th class="num">Qty</th><th class="num">Price</th><th class="num">Cash</th><th class="num">P&L</th></tr></thead><tbody>
      ${st.trades.slice(0, 40).map(t => `<tr><td class="small">${t.created_at.slice(0, 10)}</td><td title="${esc(t.note)}">${t.action}</td><td class="small">${t.underlying} ${t.expiry} ${t.strike}${t.opt_type}</td><td class="num">${t.qty}</td><td class="num">${money(t.price)}</td><td class="num ${cls(t.cash_effect)}">${money(t.cash_effect)}</td><td class="num ${cls(t.realized_pnl)}">${t.realized_pnl == null ? "" : money(t.realized_pnl)}</td></tr>`).join("")}</tbody></table>` : '<div class="muted">No option trades yet.</div>'}</div></div>`;
  if ($("#o-lvl-go")) $("#o-lvl-go").onclick = async () => { await api(`/clients/${id}/options/level`, { method: "POST", body: { level: +$("#o-lvl").value } }); toast("Options level updated"); route(); };
  const load = async (exp) => {
    const s = $("#o-sym").value.trim().toUpperCase();
    try {
      const ch = await api(`/options/chain/${s}${exp ? `?expiry=${exp}` : ""}`);
      $("#o-exp").innerHTML = ch.expiries.map(e => `<option ${e === ch.expiry ? "selected" : ""}>${e}</option>`).join("");
      $("#o-spot").innerHTML = `<b>${ch.underlying}</b> ${money(ch.spot)} · ${ch.days} days to expiry · hist. vol ${pct(ch.base_vol)}`;
      $("#o-chain").innerHTML = `<table class="chain"><thead><tr><th class="num">Δ</th><th class="num">Call bid</th><th class="num">Call ask</th><th class="num" style="text-align:center">Strike</th><th class="num">Put bid</th><th class="num">Put ask</th><th class="num">Δ</th></tr></thead><tbody>
        ${ch.rows.map(r => `<tr class="${r.itm_call ? "itm-c" : "itm-p"}"><td class="num small">${r.call.delta.toFixed(2)}</td>
          <td class="num"><a class="px" data-a="STO" data-t="C" data-k="${r.strike}" data-p="${r.call.bid}">${r.call.bid.toFixed(2)}</a></td><td class="num"><a class="px" data-a="BTO" data-t="C" data-k="${r.strike}" data-p="${r.call.ask}">${r.call.ask.toFixed(2)}</a></td>
          <td class="num" style="text-align:center"><b>${r.strike}</b></td>
          <td class="num"><a class="px" data-a="STO" data-t="P" data-k="${r.strike}" data-p="${r.put.bid}">${r.put.bid.toFixed(2)}</a></td><td class="num"><a class="px" data-a="BTO" data-t="P" data-k="${r.strike}" data-p="${r.put.ask}">${r.put.ask.toFixed(2)}</a></td>
          <td class="num small">${r.put.delta.toFixed(2)}</td></tr>`).join("")}</tbody></table>`;
      $$(".px").forEach(a => a.onclick = () => optTicket(id, ch.underlying, a.dataset.t, +a.dataset.k, ch.expiry, a.dataset.a, +a.dataset.p));
    } catch (e) { $("#o-chain").innerHTML = `<div class="neg">${esc(e.message)}</div>`; }
  };
  $("#o-load").onclick = () => load(); $("#o-exp").onchange = e => load(e.target.value); $("#o-sym").onkeydown = e => e.key === "Enter" && load();
  load();
}
function optTicket(cid, u, t, k, exp, action, price) {
  const what = action === "STO" ? (t === "C" ? "Covered call (you need 100 shares per contract)" : `Cash-secured put (reserves ${money(k * 100)} per contract)`) : (t === "C" ? "Long call" : "Long put");
  $("#o-ticket").innerHTML = `<div class="alert info"><div class="flex-between"><div><b>${action === "STO" ? "Sell to open" : "Buy to open"} ${u} ${exp} ${k}${t}</b> @ ${money(price)} · ${what}</div>
    <div class="inline-form"><input id="ot-q" type="number" min="1" value="1" style="width:70px"> contracts = <b id="ot-tot">${money(price * 100)}</b>
    <button class="btn ${action === "STO" ? "sell" : "buy"}" id="ot-go">Place paper order</button><button class="btn" id="ot-x">Cancel</button></div></div></div>`;
  $("#ot-q").oninput = () => $("#ot-tot").textContent = money(price * 100 * (+$("#ot-q").value || 0));
  $("#ot-x").onclick = () => $("#o-ticket").innerHTML = "";
  $("#ot-go").onclick = async () => { try { const r = await api(`/clients/${cid}/options/order`, { method: "POST", body: { underlying: u, type: t, strike: k, expiry: exp, action, qty: +$("#ot-q").value } });
    toast(`${r.action} ${r.qty} ${r.contract} @ ${money(r.price)} (cash ${money(r.cash_effect)})`); route(); } catch (e) { toast(e.message, true); } };
}
window.optClose = async (cid, u, t, k, exp, qty) => {
  const action = qty > 0 ? "STC" : "BTC";
  if (!confirm(`${action === "STC" ? "Sell to close" : "Buy to close"} ${Math.abs(qty)} ${u} ${exp} ${k}${t}?`)) return;
  try { const r = await api(`/clients/${cid}/options/order`, { method: "POST", body: { underlying: u, type: t, strike: k, expiry: exp, action, qty: Math.abs(qty) } });
    toast(`Closed ${r.contract}: P&L ${money(r.realized_pnl)}`); route(); } catch (e) { toast(e.message, true); }
};

// ---------------------------------------------------------------- direct index
async function indexTab(el, id) {
  const st = await api(`/clients/${id}/direct-index`);
  const cfg = st.config || { sleeve_weight: 0.3, top_n: 50, excluded_sectors: [], excluded_symbols: [] };
  el.innerHTML = `${st.active ? `<div class="grid g4">${kpi("Direct-index sleeve", pct(cfg.sleeve_weight, 0), `top ${cfg.top_n} S&P 500 names`)}${kpi("Value", money(st.value, 0), `${st.held_names}/${st.names} names held`)}
      ${kpi("Unrealized", money(st.unrealized), "harvest losses in the Tax tab", st.unrealized)}${kpi("Exclusions", (cfg.excluded_sectors.length + cfg.excluded_symbols.length) || "None", esc([...cfg.excluded_sectors, ...cfg.excluded_symbols].join(", ") || "—"))}</div>` : ""}
    <div class="grid g2" style="margin-top:16px;grid-template-columns:minmax(0,1fr) minmax(0,1.5fr)"><div class="card"><h2>${st.active ? "Adjust" : "Set up"} personalized indexing</h2>
      <div class="muted small" style="margin-bottom:10px">Own the S&P 500's largest stocks directly instead of part of your core US equity ETF — with your own exclusions and many more tax-loss harvesting opportunities.</div>
      <div class="field"><label>Sleeve size (% of invested assets): <b id="di-sv">${pct(cfg.sleeve_weight, 0)}</b></label><input id="di-s" type="range" min="5" max="60" step="5" value="${cfg.sleeve_weight * 100}" style="width:100%"></div>
      <div class="field"><label>Number of stocks</label><select id="di-n">${[...new Set([25, 50, 75, 100, 200, 500, cfg.top_n])].sort((a, b) => a - b).map(n => `<option ${n === cfg.top_n ? "selected" : ""}>${n}</option>`).join("")}</select></div>
      <div class="field"><label>Exclude sectors</label><div class="opts">${st.sectors.map(s => `<label class="chk"><input type="checkbox" class="di-sec" value="${esc(s)}" ${cfg.excluded_sectors.includes(s) ? "checked" : ""}> ${esc(s)}</label>`).join("")}</div></div>
      <div class="field"><label>Exclude tickers (comma separated)</label><input id="di-x" value="${esc(cfg.excluded_symbols.join(", "))}" placeholder="e.g. TSLA, XOM" style="width:100%"></div>
      <div class="inline-form"><button class="btn" id="di-prev">Preview</button><button class="btn primary" id="di-apply">${st.active ? "Update" : "Apply"} direct index</button>${st.active ? '<button class="btn danger" id="di-rm">Remove</button>' : ""}</div></div>
      <div class="card" id="di-out"><div class="muted">Press Preview to see the holdings, sector tilts and tracking error.</div></div></div>`;
  const body = () => ({ sleeve_weight: +$("#di-s").value / 100, top_n: +$("#di-n").value, excluded_sectors: $$(".di-sec").filter(c => c.checked).map(c => c.value),
    excluded_symbols: $("#di-x").value.split(",").map(x => x.trim().toUpperCase()).filter(Boolean) });
  $("#di-s").oninput = e => $("#di-sv").textContent = e.target.value + "%";
  $("#di-prev").onclick = async () => {
    $("#di-out").innerHTML = '<div class="loading">Building index and estimating tracking error…</div>';
    try { const p = await api(`/clients/${id}/direct-index/preview`, { method: "POST", body: body() });
      const te = p.tracking;
      $("#di-out").innerHTML = `<div class="grid g3">${kpi("Stocks", p.count, `${pct(p.index_coverage, 0)} of index weight`)}${kpi("Tracking error", te.available ? pct(te.tracking_error, 1) : "—", te.available ? `correlation ${num(te.correlation, 3)}` : "")}${kpi("Core US equity to replace", pct(p.us_core_available, 0), `sleeve ${pct(p.sleeve_weight, 0)}`)}</div>
        <h3 style="margin-top:14px">Sector tilts vs S&P 500</h3><table><thead><tr><th>Sector</th><th class="num">Index</th><th class="num">Yours</th><th class="num">Active</th></tr></thead><tbody>
        ${p.sectors.map(x => `<tr><td>${esc(x.sector)}</td><td class="num">${pct(x.index)}</td><td class="num">${pct(x.portfolio)}</td><td class="num ${Math.abs(x.active) > 0.02 ? (x.active > 0 ? "pos" : "neg") : ""}">${pct(x.active, 1, true)}</td></tr>`).join("")}</tbody></table>
        <h3 style="margin-top:14px">Holdings</h3><div class="table-wrap" style="max-height:300px;overflow-y:auto"><table><thead><tr><th>Symbol</th><th>Sector</th><th class="num">Index wt</th><th class="num">Sleeve wt</th><th class="num">Portfolio wt</th></tr></thead><tbody>
        ${p.holdings_list.map(h => `<tr><td><b>${h.symbol}</b> <span class="muted small">${esc(h.name)}</span></td><td class="small">${esc(h.sector)}</td><td class="num">${pct(h.index_weight, 2)}</td><td class="num">${pct(h.sleeve_weight, 2)}</td><td class="num">${pct(h.portfolio_weight, 2)}</td></tr>`).join("")}</tbody></table></div>`;
    } catch (e) { $("#di-out").innerHTML = `<div class="neg">${esc(e.message)}</div>`; }
  };
  $("#di-apply").onclick = async () => { try { const r = await api(`/clients/${id}/direct-index`, { method: "PUT", body: body() }); toast(`Direct index set: ${r.names} stocks — rebalance to buy them`); location.hash = `#/client/${id}/rebalance`; } catch (e) { toast(e.message, true); } };
  if ($("#di-rm")) $("#di-rm").onclick = async () => { if (!confirm("Remove the direct index? Its weight goes back to the core US equity ETF (rebalance to sell the stocks).")) return; await api(`/clients/${id}/direct-index`, { method: "DELETE" }); toast("Direct index removed"); location.hash = `#/client/${id}/rebalance`; };
  if (st.active) $("#di-prev").click();
}

// ---------------------------------------------------------------- lending
async function lendingTab(el, id, _a, client) {
  const L = await api(`/clients/${id}/lending`);
  const m = L.margin, s = L.sbloc, sl = L.sec_lending;
  el.innerHTML = `<div class="grid g3">
    <div class="card"><div class="flex-between"><h2 style="margin:0">Margin</h2>${m.allowed ? `<label class="chk"><input type="checkbox" id="mg-on" ${m.enabled ? "checked" : ""}> Enabled</label>` : '<span class="muted small">Not available in retirement accounts</span>'}</div>
      <table style="margin-top:10px"><tbody><tr><td>Buying power</td><td class="num"><b>${money(m.buying_power)}</b></td></tr><tr><td>Margin loan</td><td class="num ${m.loan ? "neg" : ""}">${money(m.loan)}</td></tr>
        <tr><td>Rate</td><td class="num">${pct(m.rate, 2)}</td></tr><tr><td>Equity / holdings</td><td class="num">${pct(m.equity_ratio)}</td></tr>
        <tr><td>Maintenance requirement (30%)</td><td class="num">${money(m.maintenance_requirement)}</td></tr><tr><td>Excess equity</td><td class="num ${cls(m.excess_equity)}">${money(m.excess_equity)}</td></tr></tbody></table>
      ${m.margin_call > 0 ? `<div class="alert danger" style="margin-top:8px"><b>Margin call</b>Deposit ${money(m.margin_call)} or sell about ${money(m.sell_to_cover)}.</div>` : ""}
      <div class="muted small" style="margin-top:8px">Reg T: buy up to 2× your cash. Interest accrues daily on any negative cash balance.</div></div>
    <div class="card"><h2 style="margin:0">Line of credit (SBLOC)</h2>${s.allowed ? `
      <table style="margin-top:10px"><tbody><tr><td>Borrowing base</td><td class="num">${money(s.borrowing_base)}</td></tr><tr><td>Balance</td><td class="num ${s.balance ? "neg" : ""}">${money(s.balance)}</td></tr>
        <tr><td>Available</td><td class="num"><b>${money(s.available)}</b></td></tr><tr><td>Rate</td><td class="num">${pct(s.rate, 2)}</td></tr><tr><td>Loan-to-value</td><td class="num">${pct(s.ltv)}</td></tr></tbody></table>
      ${s.collateral_call ? '<div class="alert danger"><b>Collateral call</b>Repay or add assets.</div>' : ""}
      <div class="inline-form" style="margin-top:10px"><input id="sb-amt" type="number" step="1000" placeholder="Amount" style="width:120px"><button class="btn" id="sb-draw">Draw</button><button class="btn" id="sb-repay" ${s.balance ? "" : "disabled"}>Repay from cash</button></div>
      <div class="muted small" style="margin-top:8px">Borrow against the portfolio without selling (no taxable sale). Draws go to the client's bank; interest compounds daily on the balance. Advance rates: bonds 85%, ETFs 70%, stocks 50%.</div>`
      : '<div class="muted" style="margin-top:10px">Retirement accounts can\'t be pledged as collateral.</div>'}</div>
    <div class="card"><div class="flex-between"><h2 style="margin:0">Securities lending</h2>${sl.allowed ? `<label class="chk"><input type="checkbox" id="sl-on" ${sl.enabled ? "checked" : ""}> Enrolled</label>` : ""}</div>
      ${sl.allowed ? `<table style="margin-top:10px"><tbody><tr><td>Income YTD</td><td class="num pos">${money(sl.income_ytd)}</td></tr><tr><td>Projected per year</td><td class="num">${money(sl.projected_annual_income)}</td></tr><tr><td>Your share of lending fees</td><td class="num">${pct(sl.split, 0)}</td></tr></tbody></table>
      <div class="table-wrap" style="max-height:170px;overflow-y:auto;margin-top:8px"><table><thead><tr><th>Holding</th><th class="num">Fee</th><th class="num">$/yr</th></tr></thead><tbody>${sl.holdings.slice(0, 20).map(h => `<tr><td>${h.symbol}</td><td class="num">${pct(h.rate, 2)}</td><td class="num">${money(h.annual_income)}</td></tr>`).join("")}</tbody></table></div>
      <div class="muted small" style="margin-top:8px">Fully paid shares are lent to borrowers; income is paid to cash daily. Hard-to-borrow stocks earn more.</div>` : '<div class="muted" style="margin-top:10px">Not offered for retirement accounts.</div>'}</div></div>
    ${s.events && s.events.length ? `<div class="card table-wrap" style="margin-top:16px"><h3>Line of credit activity</h3><table><thead><tr><th>Date</th><th>Type</th><th class="num">Amount</th></tr></thead><tbody>${s.events.map(e => `<tr><td class="small">${e.created_at.slice(0, 10)}</td><td>${e.kind}</td><td class="num">${money(e.amount)}</td></tr>`).join("")}</tbody></table></div>` : ""}`;
  if ($("#mg-on")) $("#mg-on").onchange = async e => { try { await api(`/clients/${id}/margin`, { method: "POST", body: { enabled: e.target.checked } }); toast(`Margin ${e.target.checked ? "enabled" : "disabled"}`); route(); } catch (x) { toast(x.message, true); route(); } };
  if ($("#sl-on")) $("#sl-on").onchange = async e => { try { await api(`/clients/${id}/sec-lending`, { method: "POST", body: { enabled: e.target.checked } }); toast(`Securities lending ${e.target.checked ? "on" : "off"}`); route(); } catch (x) { toast(x.message, true); route(); } };
  if ($("#sb-draw")) $("#sb-draw").onclick = async () => { try { await api(`/clients/${id}/sbloc/draw`, { method: "POST", body: { amount: +$("#sb-amt").value } }); toast("Funds drawn"); route(); } catch (x) { toast(x.message, true); } };
  if ($("#sb-repay")) $("#sb-repay").onclick = async () => { try { await api(`/clients/${id}/sbloc/repay`, { method: "POST", body: { amount: +$("#sb-amt").value || 1e12, from_cash: true } }); toast("Repayment made"); route(); } catch (x) { toast(x.message, true); } };
}

// ---------------------------------------------------------------- reports
async function reportsTab(el, id, _a, client) {
  const t = new Date(), iso = d => d.toISOString().slice(0, 10);
  const mStart = new Date(t.getFullYear(), t.getMonth(), 1), qStart = new Date(t.getFullYear(), Math.floor(t.getMonth() / 3) * 3, 1), yStart = new Date(t.getFullYear(), 0, 1);
  el.innerHTML = `<div class="grid g2">
    <div class="card"><h2>Investment Policy Statement</h2><div class="muted" style="margin-bottom:12px">Objectives, risk profile, strategic allocation with permitted ranges, rebalancing policy, constraints, tax and fees — with signature lines.</div>
      <button class="btn primary" onclick="window.open('/report/ips/${id}','_blank')">Open IPS</button></div>
    <div class="card"><h2>Client statement</h2><div class="muted" style="margin-bottom:12px">Value, time-weighted return, allocation vs policy, holdings, activity, fees, income, realized gains and goal progress.</div>
      <div class="inline-form"><button class="btn" onclick="window.open('/report/statement/${id}?start=${iso(mStart)}&end=${iso(t)}','_blank')">Month to date</button>
        <button class="btn" onclick="window.open('/report/statement/${id}?start=${iso(qStart)}&end=${iso(t)}','_blank')">Quarter to date</button>
        <button class="btn" onclick="window.open('/report/statement/${id}?start=${iso(yStart)}&end=${iso(t)}','_blank')">Year to date</button></div>
      <div class="inline-form" style="margin-top:10px"><div class="field"><label>From</label><input type="date" id="r-s" value="${iso(qStart)}"></div><div class="field"><label>To</label><input type="date" id="r-e" value="${iso(t)}"></div>
        <button class="btn primary" id="r-go">Open statement</button></div></div></div>
  <div class="muted small" style="margin-top:12px">Reports open in a new window. Use <b>Print / Save as PDF</b> at the top to create a PDF you can email to ${esc(client.name)}.</div>`;
  $("#r-go").onclick = () => window.open(`/report/statement/${id}?start=${$("#r-s").value}&end=${$("#r-e").value}`, "_blank");
}

// ---------------------------------------------------------------- AI assistant
const AI = { conv: null, clientId: null, busy: false, log: [] };
function md(src) {
  // small, safe markdown renderer: escapes HTML first, then applies formatting
  const lines = esc(src).split("\n"); let html = "", list = null, table = [];
  const inline = t => t.replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>").replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<i>$2</i>").replace(/_\(([^)]*)\)_/g, "<i>($1)</i>");
  const flushList = () => { if (list) { html += `</${list}>`; list = null; } };
  const flushTable = () => {
    if (!table.length) return;
    const body = table.length > 1 && /^[\s|:-]+$/.test(table[1]) ? [table[0], ...table.slice(2)] : table;
    const cells = body.map(r => r.replace(/^\s*\||\|\s*$/g, "").split("|").map(c => inline(c.trim())));
    html += `<div class="table-wrap"><table class="md-table"><thead><tr>${cells[0].map(c => `<th>${c}</th>`).join("")}</tr></thead><tbody>${cells.slice(1).map(r => `<tr>${r.map(c => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
    table = [];
  };
  for (const raw of lines) {
    const l = raw.trimEnd();
    if (/^\s*\|.*\|\s*$/.test(l)) { flushList(); table.push(l.trim()); continue; } else flushTable();
    let m;
    if ((m = l.match(/^\s*[-*]\s+(.*)/))) { if (list !== "ul") { flushList(); html += "<ul>"; list = "ul"; } html += `<li>${inline(m[1])}</li>`; continue; }
    if ((m = l.match(/^\s*\d+[.)]\s+(.*)/))) { if (list !== "ol") { flushList(); html += "<ol>"; list = "ol"; } html += `<li>${inline(m[1])}</li>`; continue; }
    flushList();
    if ((m = l.match(/^#{1,4}\s+(.*)/))) { html += `<h4>${inline(m[1])}</h4>`; continue; }
    html += l.trim() ? `<p>${inline(l)}</p>` : "";
  }
  flushList(); flushTable();
  return html;
}
const TOOL_LABEL = { list_clients: "Looked up clients", get_portfolio: "Read portfolio", get_quote: "Got quote", research_stock: "Ran research model",
  preview_rebalance: "Previewed rebalance", list_orders: "Checked orders", get_risk_questionnaire: "Read questionnaire",
  place_paper_order: "Paper order", cancel_order: "Cancel order", execute_rebalance: "Rebalance", create_client: "Create client",
  get_tax_report: "Read tax report", list_models: "Browsed model marketplace", get_financial_plan: "Ran financial plan", get_billing: "Checked billing",
  harvest_tax_loss: "Tax-loss harvest", assign_model: "Assign model", add_goal: "Add goal", run_billing: "Run billing",
  get_option_chain: "Read option chain", get_lending: "Checked lending", preview_direct_index: "Previewed direct index",
  place_option_order: "Option order", apply_direct_index: "Direct index" };

async function assistantView(clientId) {
  const [st, clients] = await Promise.all([api("/assistant/settings"), api("/clients")]);
  if (AI.clientId !== clientId) { AI.conv = null; AI.log = []; AI.clientId = clientId; }
  const hasKey = st.provider === "claude" ? st.has_claude_key : st.has_openai_key;
  const model = st.provider === "claude" ? st.model_claude : st.model_openai;
  view.innerHTML = `
    <div class="flex-between"><div><h1>AI Assistant</h1><div class="sub">Ask about your clients, research stocks, rebalance and trade — in plain English. Trades always ask for your approval.</div></div>
      <div class="inline-form"><select id="ai-client"><option value="">All clients</option>${clients.map(c => `<option value="${c.id}" ${c.id === clientId ? "selected" : ""}>${esc(c.name)}</option>`).join("")}</select>
      <button class="btn" id="ai-new">New chat</button><button class="btn" id="ai-set-btn">Settings</button></div></div>
    <div class="grid ai-grid">
      <div class="card ai-chat">
        <div class="ai-status"><span class="ai-tag">${st.provider === "claude" ? "Claude" : "ChatGPT"}</span> <span class="muted small">${esc(model)}</span>${hasKey ? "" : ' <span class="neg small">· no API key yet</span>'}</div>
        <div id="ai-log" class="ai-log"></div>
        <div class="ai-input"><textarea id="ai-msg" rows="2" placeholder="${hasKey ? `Ask anything… e.g. How is ${esc((clients.find(c => c.id === clientId) || clients[0] || { name: "my client" }).name)}'s portfolio doing?` : "Add an API key in Settings to start"}"></textarea>
          <button class="btn primary" id="ai-send" ${hasKey ? "" : "disabled"}>Send</button></div>
      </div>
      <div class="card" id="ai-side"></div>
    </div>`;
  $("#ai-client").onchange = e => { location.hash = e.target.value ? `#/assistant/${e.target.value}` : "#/assistant"; };
  $("#ai-new").onclick = async () => { if (AI.conv) await api(`/assistant/${AI.conv}`, { method: "DELETE" }).catch(() => {}); AI.conv = null; AI.log = []; renderLog(); };
  $("#ai-set-btn").onclick = () => renderSettings(st, true);
  const send = () => { const m = $("#ai-msg").value.trim(); if (m && !AI.busy) { $("#ai-msg").value = ""; aiSend(m); } };
  $("#ai-send").onclick = send;
  $("#ai-msg").onkeydown = e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } };
  if (hasKey) renderSuggestions(clients, clientId); else renderSettings(st, false);
  renderLog();
}

function renderSuggestions(clients, clientId) {
  const c = clients.find(x => x.id === clientId) || clients[0];
  const who = c ? c.name : "my client";
  const qs = [`How is ${who}'s portfolio doing? Anything I should act on?`, `Does ${who} need rebalancing? Show me the trades first.`,
    "Research MSFT and tell me if it's a buy, with the key numbers.", `Which stock would fit ${who}'s stock sleeve best among AAPL, JPM and KO?`,
    "Compare all my clients' returns and risk in a table.", `Buy $2,000 of VTI for ${who}.`];
  $("#ai-side").innerHTML = `<h3>Try asking</h3>${qs.map(q => `<button class="ai-suggest">${esc(q)}</button>`).join("")}
    <div class="muted small" style="margin-top:14px">The assistant uses the same research model, portfolios and paper account you see in the app. Orders, rebalances and new clients always wait for your <b>Approve</b>.</div>`;
  $$(".ai-suggest").forEach(b => b.onclick = () => { if (!AI.busy) aiSend(b.textContent); });
}

function renderSettings(st, cancellable) {
  $("#ai-side").innerHTML = `<h3>AI settings</h3>
    <div class="field"><label>Provider</label><select id="s-provider"><option value="claude" ${st.provider === "claude" ? "selected" : ""}>Claude (Anthropic)</option><option value="openai" ${st.provider === "openai" ? "selected" : ""}>ChatGPT (OpenAI)</option></select></div>
    <div class="field"><label>Claude API key ${st.has_claude_key ? `<span class="pos">saved ${esc(st.claude_key_hint)}</span>` : ""}</label><input id="s-ck" type="password" placeholder="${st.has_claude_key ? "Leave blank to keep" : "sk-ant-..."}" style="width:100%" autocomplete="off">
      <div class="small muted">Get one at <b>console.anthropic.com</b> → API Keys</div></div>
    <div class="field"><label>Claude model</label><input id="s-cm" list="cm-list" value="${esc(st.model_claude)}" style="width:100%"><datalist id="cm-list">${st.model_suggestions.claude.map(m => `<option value="${m}">`).join("")}</datalist></div>
    <div class="field"><label>OpenAI API key ${st.has_openai_key ? `<span class="pos">saved ${esc(st.openai_key_hint)}</span>` : ""}</label><input id="s-ok" type="password" placeholder="${st.has_openai_key ? "Leave blank to keep" : "sk-..."}" style="width:100%" autocomplete="off">
      <div class="small muted">Get one at <b>platform.openai.com</b> → API keys</div></div>
    <div class="field"><label>OpenAI model</label><input id="s-om" list="om-list" value="${esc(st.model_openai)}" style="width:100%"><datalist id="om-list">${st.model_suggestions.openai.map(m => `<option value="${m}">`).join("")}</datalist></div>
    <div class="inline-form"><button class="btn primary" id="s-save">Save</button>${cancellable ? '<button class="btn" id="s-cancel">Close</button>' : ""}
      ${st.has_claude_key || st.has_openai_key ? '<button class="btn sm danger" id="s-clear">Remove keys</button>' : ""}</div>
    <div class="small muted" style="margin-top:12px">Keys are stored only on this computer (data\\assistant.json) and are never shown again or sent anywhere except the AI provider. API use is pay-as-you-go, typically a few cents per question — set a monthly limit in the provider's billing page.</div>`;
  $("#s-save").onclick = async () => {
    const body = { provider: $("#s-provider").value, model_claude: $("#s-cm").value.trim(), model_openai: $("#s-om").value.trim() };
    if ($("#s-ck").value.trim()) body.claude_key = $("#s-ck").value.trim();
    if ($("#s-ok").value.trim()) body.openai_key = $("#s-ok").value.trim();
    try { await api("/assistant/settings", { method: "PUT", body }); toast("AI settings saved"); AI.conv = null; route(); } catch (e) { toast(e.message, true); }
  };
  if ($("#s-cancel")) $("#s-cancel").onclick = () => route();
  if ($("#s-clear")) $("#s-clear").onclick = async () => { if (!confirm("Remove both saved API keys?")) return; await api("/assistant/settings", { method: "PUT", body: { claude_key: "", openai_key: "" } }); toast("Keys removed"); route(); };
}

function renderLog() {
  const el = $("#ai-log"); if (!el) return;
  el.innerHTML = AI.log.length ? AI.log.map(renderEvent).join("") :
    `<div class="empty">Ask a question to get started.<br><span class="small">I can read portfolios, run the research model, preview rebalances and place paper trades (with your approval).</span></div>`;
  $$(".ai-approve", el).forEach(b => b.onclick = () => aiDecide(b.dataset.ok === "1"));
  el.scrollTop = el.scrollHeight;
}
function renderEvent(e, i) {
  if (e.type === "user") return `<div class="ai-msg user">${esc(e.text)}</div>`;
  if (e.type === "text") return `<div class="ai-msg bot">${md(e.text)}</div>`;
  if (e.type === "error") return `<div class="alert danger">${esc(e.text)}</div>`;
  if (e.type === "thinking") return `<div class="ai-tool"><span class="dot-pulse"></span> Thinking…</div>`;
  if (e.type === "tool") {
    const a = e.args || {}, detail = a.symbol || (a.client_id ? `client #${a.client_id}` : "");
    const st = { ok: "", approved: '<span class="pos">approved ✓</span>', declined: '<span class="neg">declined</span>', error: '<span class="neg">error</span>' }[e.status] || "";
    return `<div class="ai-tool">⚙ ${TOOL_LABEL[e.tool] || e.tool}${detail ? ` · ${esc(String(detail))}` : ""} ${st}</div>`;
  }
  if (e.type === "approval") {
    const live = i === AI.log.length - 1 && !AI.busy;
    return `<div class="ai-approval"><div><b>Approval needed</b> <span class="muted small">(paper trade)</span></div><div style="margin:6px 0 10px">${esc(e.description)}</div>
      ${live ? '<div class="inline-form"><button class="btn buy ai-approve" data-ok="1">Approve</button><button class="btn ai-approve" data-ok="0">Decline</button></div>' : '<div class="muted small">Answered</div>'}</div>`;
  }
  return "";
}
async function aiCall(fn) {
  AI.busy = true; AI.log.push({ type: "thinking" }); renderLog();
  const btn = $("#ai-send"); if (btn) btn.disabled = true;
  try {
    const r = await fn();
    AI.conv = r.conversation_id;
    AI.log.pop(); AI.log.push(...r.events);
  } catch (e) { AI.log.pop(); AI.log.push({ type: "error", text: e.message }); }
  AI.busy = false; if (btn) btn.disabled = false; renderLog();
}
function aiSend(text) {
  AI.log.push({ type: "user", text });
  return aiCall(() => api("/assistant/chat", { method: "POST", body: { message: text, conversation_id: AI.conv, client_id: AI.clientId } }));
}
function aiDecide(ok) {
  return aiCall(() => api("/assistant/decision", { method: "POST", body: { conversation_id: AI.conv, approve: ok } }));
}

// ---------------------------------------------------------------- boot
(async () => {
  CONFIG = await api("/config");
  $("#mode-badge").textContent = CONFIG.data_mode === "fmp" ? "LIVE DATA · FMP" : CONFIG.data_mode === "live" ? "LIVE PRICES" : "SIMULATED DATA";
  $("#mode-badge").title = CONFIG.data_mode === "live" ? "Prices and company financials: Yahoo Finance (free, prices may be ~15 min delayed)" + (CONFIG.fmp_key ? "; FMP used first for financials" : "") : "";
  if (CONFIG.data_mode === "live") $("#mode-badge").classList.add("live");
  $("#version").textContent = "v" + CONFIG.version;
  $("#quit-btn").onclick = async () => {
    if (!confirm("Quit Portfolio Manager? Your clients and trades are saved.")) return;
    try { await api("/shutdown", { method: "POST" }); } catch (e) { /* server already stopping */ }
    document.body.innerHTML = `<div style="margin:auto;text-align:center;font-family:Inter,system-ui,sans-serif;color:#334155">
      <div style="font-size:40px;color:#DD2F20">◆</div><h2>Portfolio Manager has stopped</h2>
      <p>You can close this window. Open it again from the desktop icon.</p></div>`;
    setTimeout(() => window.close(), 800);
  };
  route();
})();
