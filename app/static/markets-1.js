/* Markets home page + watchlists. Uses helpers from app.js ($, api, esc, num, pct, big, cls, chart, toast, ratingPill). */

// ---------------------------------------------------------------- small helpers
const MK = { wl: null, range: "1M", filter: "all", detailRange: "1Y" };
try { MK.wl = +localStorage.getItem("pm.wl") || null; MK.range = localStorage.getItem("pm.wlRange") || "1M"; } catch (e) { /* storage unavailable */ }
const saveMK = () => { try { localStorage.setItem("pm.wl", MK.wl || ""); localStorage.setItem("pm.wlRange", MK.range); } catch (e) { /* ignore */ } };

function px(v, kind, ref) {
  if (v == null || isNaN(v)) return "—";
  const a = Math.abs(ref == null ? v : ref);
  const d = kind === "Rate" ? 3 : a < 10 ? 4 : a < 1000 ? 2 : 2;
  return Number(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
}
const chg = (v, kind, ref) => v == null || isNaN(v) ? "—" : (v > 0 ? "+" : "") + px(v, kind, ref);
const arrow = v => v > 0 ? "▲" : v < 0 ? "▼" : "•";
const unit = r => r.kind === "Rate" ? "%" : "";
const pxAttr = (r, k) => r.available ? ` data-px="${esc(r.symbol)}" data-pxk="${k}" data-v="${r.price}"` : "";

function sparkSvg(vals, w = 120, h = 34) {
  if (!vals || vals.length < 2) return `<svg width="${w}" height="${h}"></svg>`;
  const lo = Math.min(...vals), hi = Math.max(...vals), span = hi - lo || 1;
  const x = i => (i / (vals.length - 1)) * (w - 2) + 1, y = v => h - 2 - ((v - lo) / span) * (h - 4);
  const up = vals[vals.length - 1] >= vals[0];
  const col = up ? "var(--pos)" : "var(--neg)";
  const pts = vals.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const base = y(vals[0]).toFixed(1);
  return `<svg class="spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true">
    <line x1="0" x2="${w}" y1="${base}" y2="${base}" stroke="currentColor" stroke-opacity=".25" stroke-dasharray="2 3"/>
    <polyline points="${pts}" fill="none" stroke="${col}" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"/></svg>`;
}

function rangeBar(lo, hi, v) {
  if (lo == null || hi == null || v == null || hi <= lo) return "—";
  const p = Math.max(0, Math.min(100, (v - lo) / (hi - lo) * 100));
  return `<div class="rangebar" title="${px(lo)} – ${px(hi)}"><span style="left:${p}%"></span></div>
          <div class="rangebar-l"><span>${px(lo)}</span><span>${px(hi)}</span></div>`;
}

async function currentWatchlist() {
  const lists = await api("/watchlists");
  if (!lists.find(l => l.id === MK.wl)) MK.wl = lists[0] && lists[0].id;
  saveMK();
  return lists;
}

async function addToWatchlist(symbol, btn) {
  try {
    await currentWatchlist();
    await api(`/watchlists/${MK.wl}/symbols`, { method: "POST", body: { symbol } });
    toast(`${symbol} added to your watchlist`);
    if (btn) { btn.classList.add("on"); btn.textContent = "★"; }
  } catch (e) { toast(e.message, true); }
}

function searchBox(id, placeholder, onPick) {
  setTimeout(() => {
    const inp = $(`#${id}`), box = $(`#${id}-res`);
    if (!inp) return;
    let t = null;
    const close = () => { box.innerHTML = ""; box.style.display = "none"; };
    inp.addEventListener("input", () => {
      clearTimeout(t);
      const q = inp.value.trim();
      if (!q) return close();
      t = setTimeout(async () => {
        try {
          const res = await api(`/search?q=${encodeURIComponent(q)}`);
          box.innerHTML = res.length ? res.map(r => `<div class="sr" data-s="${esc(r.symbol)}"><b>${esc(r.symbol)}</b><span>${esc(r.name || "")}</span><em>${esc(r.exchange || "")}</em></div>`).join("")
            : `<div class="sr muted">No matches — press Enter to try “${esc(q.toUpperCase())}”</div>`;
          box.style.display = "block";
          $$(".sr[data-s]", box).forEach(el => el.onclick = () => { close(); inp.value = ""; onPick(el.dataset.s); });
        } catch (e) { close(); }
      }, 220);
    });
    inp.addEventListener("keydown", e => { if (e.key === "Enter" && inp.value.trim()) { const s = inp.value.trim().toUpperCase(); close(); inp.value = ""; onPick(s); } if (e.key === "Escape") close(); });
    document.addEventListener("click", e => { if (!box.contains(e.target) && e.target !== inp) close(); });
  });
  return `<div class="searchwrap"><input id="${id}" placeholder="${esc(placeholder)}" autocomplete="off"><div class="search-res" id="${id}-res"></div></div>`;
}

// ---------------------------------------------------------------- Markets (home)
async function marketsView() {
  if (pageTimer) { clearInterval(pageTimer); pageTimer = null; }
  const b = await api("/markets");
  const wl = await currentWatchlist().then(() => api(`/watchlists/${MK.wl}`).catch(() => null)).catch(() => null);
  const onList = new Set(wl ? wl.rows.map(r => r.symbol) : []);
  const tapeItem = r => `<span class="tape-i" data-s="${esc(r.symbol)}"><b>${esc(r.name)}</b> ${r.available ? `${px(r.price, r.kind)}${unit(r)} <em class="${cls(r.change_pct)}">${arrow(r.change_pct)} ${pct(r.change_pct, 2, true)}</em>` : `<em class="muted">n/a</em>`}</span>`;
  const heroSyms = ["^GSPC", "^NSEI", "GCUSD", "BTCUSD"];
  const all = Object.fromEntries(b.groups.flatMap(g => g.rows).map(r => [r.symbol, r]));
  const hero = heroSyms.map(s => all[s]).filter(Boolean);
  const star = s => `<button class="star ${onList.has(s) ? "on" : ""}" data-star="${esc(s)}" title="Add to watchlist">${onList.has(s) ? "★" : "☆"}</button>`;
  const rowHtml = r => `<tr class="mrow" data-s="${esc(r.symbol)}">
      <td><div class="mname">${esc(r.name)}</div><div class="muted small mono">${esc(r.symbol)}</div></td>
      <td class="sparkcell">${r.available ? sparkSvg(r.spark, 88, 30) : ""}</td>
      <td class="num"><b${pxAttr(r, "m")}>${r.available ? px(r.price, r.kind) + unit(r) : "—"}</b></td>
      <td class="num ${cls(r.change_pct)}">${r.available ? chg(r.change, r.kind, r.price) : ""}</td>
      <td class="num ${cls(r.change_pct)}">${r.available ? `<span class="chip ${cls(r.change_pct)}">${arrow(r.change_pct)} ${pct(Math.abs(r.change_pct), 2)}</span>` : `<span class="muted small">not on your data plan</span>`}</td>
      <td class="num ${cls(r.ytd)}">${pct(r.ytd, 1, true)}</td>
      <td class="num">${star(r.symbol)}</td></tr>`;

  view.innerHTML = `
    <div class="flex-between"><div><div class="eyebrow">Markets overview</div><h1>Markets</h1>
      <div class="sub">World indices, commodities, crypto and currencies${b.data_mode === "live" ? " — live prices from Yahoo Finance (some exchanges ~15 min delayed)" : b.data_mode === "fmp" ? " — live from FMP (may be delayed)" : " — SIMULATED prices, not real"}. Refreshes every minute.</div></div>
      <div class="row" style="align-items:center">${searchBox("mk-search", "Search stocks, ETFs, indices…", s => openDetail(s))}
        <a class="btn primary" href="#/watchlist">★ My watchlist</a></div></div>
    <div class="tape"><div class="tape-track">${b.tape.map(tapeItem).join("")}${b.tape.map(tapeItem).join("")}</div></div>
    <div class="grid g4" style="margin-bottom:18px">${hero.map(r => `
      <div class="card hero-card mrow" data-s="${esc(r.symbol)}">
        <div class="flex-between"><div class="kpi"><div class="label">${esc(r.name)}</div></div>${star(r.symbol)}</div>
        <div class="hero-px"${pxAttr(r, "h")}>${r.available ? px(r.price, r.kind) : "—"}</div>
        <div class="mono small ${cls(r.change_pct)}">${r.available ? `${arrow(r.change_pct)} ${chg(r.change, r.kind, r.price)} (${pct(r.change_pct, 2, true)})` : "n/a"}</div>
        <div style="margin-top:10px">${sparkSvg(r.spark, 260, 54)}</div>
        <div class="flex-between small mono muted" style="margin-top:6px"><span>1M ${pct(r.r1m, 1, true)}</span><span>YTD ${pct(r.ytd, 1, true)}</span><span>1Y ${pct(r.r1y, 1, true)}</span></div>
      </div>`).join("")}</div>
    <div class="grid mk-grid">
      ${b.groups.map(g => `<div class="card"><h3>${esc(g.title)}</h3><div class="table-wrap"><table class="mtable">
        <thead><tr><th>Name</th><th>30 days</th><th class="num">Last</th><th class="num">Change</th><th class="num">Chg %</th><th class="num">YTD</th><th></th></tr></thead>
        <tbody>${g.rows.map(rowHtml).join("")}</tbody></table></div></div>`).join("")}
      <div class="card"><h3>Biggest moves today</h3>
        ${b.movers.map(r => `<div class="mover mrow" data-s="${esc(r.symbol)}"><div><div class="mname">${esc(r.name)}</div><div class="muted small">${esc(r.kind || "")}</div></div>
          <div class="num"><span class="chip ${cls(r.change_pct)}">${arrow(r.change_pct)} ${pct(Math.abs(r.change_pct), 2)}</span></div></div>`).join("") || `<div class="muted">No data yet.</div>`}
        ${wl ? `<h3 style="margin-top:22px">Your watchlist · ${esc(wl.name)}</h3>
          ${wl.rows.slice(0, 6).map(r => `<div class="mover mrow" data-s="${esc(r.symbol)}"><div><div class="mname">${esc(r.symbol)}</div><div class="muted small">${esc(r.name)}</div></div>
            <div style="text-align:right"><div class="mono small">${r.available ? px(r.price, r.kind) : "—"}</div><div class="mono small ${cls(r.change_pct)}">${r.available ? pct(r.change_pct, 2, true) : ""}</div></div></div>`).join("") || `<div class="muted small">Empty — tap ☆ on any row to add it.</div>`}
          <a class="btn sm" href="#/watchlist" style="margin-top:10px">Open watchlist →</a>` : ""}
      </div>
    </div>
    <div class="muted small" style="margin-top:14px">${b.available} of ${b.total} markets available · updated ${new Date(b.as_of).toLocaleTimeString()}</div>`;

  $$(".mrow, .tape-i").forEach(el => el.addEventListener("click", e => { if (e.target.closest("[data-star]")) return; openDetail(el.dataset.s); }));
  $$("[data-star]").forEach(el => el.onclick = e => { e.stopPropagation(); if (!el.classList.contains("on")) addToWatchlist(el.dataset.star, el); });
  pageTimer = setInterval(() => { if (!document.querySelector(".modal") && document.activeElement?.id !== "mk-search") quietRefresh(marketsView).catch(() => {}); }, 60000);
}

// ---------------------------------------------------------------- symbol detail (modal)
async function openDetail(symbol, rng) {
  rng = rng || MK.detailRange;
  MK.detailRange = rng;
  let m = $(".modal");
  if (!m) {
    m = document.createElement("div"); m.className = "modal";
    m.innerHTML = `<div class="modal-card"><div class="loading">Loading ${esc(symbol)}…</div></div>`;
    document.body.appendChild(m);
    m.addEventListener("click", e => { if (e.target === m) closeDetail(); });
    document.addEventListener("keydown", escClose);
  }
  let d;
  try { d = await api(`/markets/${encodeURIComponent(symbol)}?range=${rng}`); }
  catch (e) { $(".modal-card", m).innerHTML = `<p class="neg">${esc(e.message)}</p><button class="btn" onclick="closeDetail()">Close</button>`; return; }
  const isStock = !d.kind || d.kind === "Stock" || d.kind === "ETF" || !/^\^|USD$|=|\.SS$/.test(d.symbol);
  $(".modal-card", m).innerHTML = `
    <div class="flex-between"><div><div class="eyebrow">${esc(d.kind || "Security")} · ${esc(d.symbol)}</div><h2 style="font-size:30px;margin:4px 0">${esc(d.name)}</h2></div>
      <button class="btn sm" onclick="closeDetail()">✕ Close</button></div>
    ${d.available ? `<div class="row" style="align-items:baseline;gap:14px"><div class="hero-px">${px(d.price, d.kind)}${unit(d)}</div>
      <div class="mono ${cls(d.change_pct)}">${arrow(d.change_pct)} ${chg(d.change, d.kind, d.price)} (${pct(d.change_pct, 2, true)}) today</div></div>` : `<p class="muted">This symbol isn't available on your data plan.</p>`}
    <div class="pills" style="margin:14px 0 6px">${["1M", "3M", "6M", "YTD", "1Y", "3Y", "5Y"].map(r => `<button class="pillbtn ${r === d.range ? "on" : ""}" data-r="${r}">${r}</button>`).join("")}
      <span class="mono small ${cls(d.range_return)}" style="margin-left:8px">${d.range} ${pct(d.range_return, 2, true)}</span></div>
    <div class="chart-box"><canvas id="md-canvas"></canvas></div>
    <div class="grid g4" style="margin-top:14px">
      <div class="kpi"><div class="label">Day range</div><div class="mono small" style="margin-top:6px">${px(d.day_low, d.kind)} – ${px(d.day_high, d.kind)}</div></div>
      <div class="kpi"><div class="label">52-week range</div><div style="margin-top:6px">${rangeBar(d.year_low, d.year_high, d.price)}</div></div>
      <div class="kpi"><div class="label">Returns</div><div class="mono small" style="margin-top:6px">1M ${pct(d.r1m, 1, true)} · YTD ${pct(d.ytd, 1, true)} · 1Y ${pct(d.r1y, 1, true)}</div></div>
      <div class="kpi"><div class="label">Volume</div><div class="mono small" style="margin-top:6px">${d.volume ? big(d.volume) : "—"}</div></div></div>
    <div class="row" style="margin-top:18px;gap:8px">
      <button class="btn primary" id="md-add">★ Add to watchlist</button>
      ${isStock ? `<a class="btn" href="#/research/${encodeURIComponent(d.symbol)}" onclick="closeDetail()">Research ${esc(d.symbol)} →</a>` : ""}</div>`;
  $$(".pillbtn", m).forEach(b => b.onclick = () => openDetail(symbol, b.dataset.r));
  $("#md-add").onclick = () => addToWatchlist(d.symbol);
  if (MK.detailChart) MK.detailChart.destroy();
  const up = d.series.length > 1 && d.series[d.series.length - 1].y >= d.series[0].y;
  const col = up ? "#4f7a3a" : "#b3362a";
  MK.detailChart = new Chart($("#md-canvas"), { type: "line",
    data: { labels: d.series.map(p => p.x), datasets: [{ data: d.series.map(p => p.y), borderColor: col, borderWidth: 2, pointRadius: 0, fill: true, backgroundColor: up ? "rgba(79,122,58,.08)" : "rgba(179,54,42,.07)", tension: .15 }] },
    options: { animation: window.QUIET || REDUCED ? false : { duration: 800, easing: "easeOutQuart" }, maintainAspectRatio: false, interaction: { mode: "index", intersect: false }, plugins: { legend: { display: false } },
      scales: { x: { ticks: { maxTicksLimit: 6 }, grid: { display: false } }, y: { ticks: { maxTicksLimit: 5 } } } } });
}
function escClose(e) { if (e.key === "Escape") closeDetail(); }
function closeDetail() {
  const m = $(".modal"); if (m) m.remove();
  if (MK.detailChart) { MK.detailChart.destroy(); MK.detailChart = null; }
  document.removeEventListener("keydown", escClose);
}
window.addEventListener("hashchange", closeDetail);

// ---------------------------------------------------------------- Watchlist
async function watchlistView(id) {
  if (pageTimer) { clearInterval(pageTimer); pageTimer = null; }
  const lists = await api("/watchlists");
  if (id && lists.find(l => l.id === id)) MK.wl = id;
  else if (!lists.find(l => l.id === MK.wl)) MK.wl = lists[0].id;
  saveMK();
  const w = await api(`/watchlists/${MK.wl}?range=${MK.range}`);
  const rows = w.rows.filter(r => MK.filter === "all" || (r.available && (MK.filter === "gainers" ? r.change_pct > 0 : r.change_pct < 0)));
  const ratingCell = r => r.rating ? ratingPill(r.rating.rating) : (r.kind === "Stock" ? `<a class="small" href="#/research/${encodeURIComponent(r.symbol)}">Analyze</a>` : `<span class="muted">—</span>`);

  view.innerHTML = `
    <div class="flex-between"><div><div class="eyebrow">Watchlist</div><h1>${esc(w.name)} <span class="count-badge">${w.rows.length} symbols</span></h1>
      <div class="sub">Track anything — stocks, ETFs, indices, commodities, crypto, currencies. Click a row for its chart.</div></div>
      <a class="btn" href="#/">← Markets</a></div>
    <div class="grid wl-grid">
      <div class="card wl-side">
        <div class="flex-between"><select id="wl-pick">${lists.map(l => `<option value="${l.id}" ${l.id === MK.wl ? "selected" : ""}>${esc(l.name)} (${l.count})</option>`).join("")}</select>
          <button class="btn sm" id="wl-manage" title="Rename or delete">Manage</button></div>
        <button class="btn primary" id="wl-new" style="width:100%;margin-top:10px">+ New watchlist</button>
        <div class="wl-over">
          <div><div class="label">1-day return</div><div class="big ${cls(w.day_return)}">${pct(w.day_return, 2, true)}</div></div>
          <div><div class="label">1-year return</div><div class="big ${cls(w.year_return)}">${pct(w.year_return, 2, true)}</div></div>
        </div>
        <div class="wl-items">${w.rows.map(r => `<div class="wl-item mrow" data-s="${esc(r.symbol)}">
          <div class="wl-n"><b>${esc(r.name && r.name.length < 22 ? r.name : r.symbol)}</b><span>${esc(r.symbol)}</span></div>
          ${r.available ? sparkSvg(r.spark, 84, 28) : "<span></span>"}
          <div class="wl-p"><div${pxAttr(r, "s")}>${r.available ? px(r.price, r.kind) : "—"}</div><div class="${cls(r.change_pct)}">${r.available ? pct(r.change_pct, 2, true) : ""}</div></div></div>`).join("") || `<div class="empty">No symbols yet</div>`}</div>
      </div>
      <div style="min-width:0">
        <div class="grid wl-top">
          <div class="card"><div class="flex-between"><div class="pills">${["1M", "3M", "6M", "YTD", "1Y", "3Y", "5Y"].map(r => `<button class="pillbtn ${r === w.range ? "on" : ""}" data-r="${r}">${r}</button>`).join("")}</div>
            <span class="mono small muted">equal-weighted</span></div>
            <div class="chart-box" style="margin-top:10px"><canvas id="wl-chart"></canvas></div></div>
          <div class="grid" style="gap:16px">
            <div class="card"><h3>${esc(w.range)} performance</h3>
              <table><tbody><tr><td>${esc(w.range)} return</td><td class="num ${cls(w.performance.return)}">${pct(w.performance.return, 2, true)}</td></tr>
              ${w.compare.map(c => `<tr><td>vs ${esc(c.name)}</td><td class="num ${cls(c.diff)}">${pct(c.diff, 2, true)}</td></tr>`).join("")}
              <tr><td>Gainers / losers today</td><td class="num">${w.gainers} / ${w.losers}</td></tr></tbody></table></div>
            <div class="card"><h3>What's on the list</h3><div class="flex-between" style="align-items:center">
              <div><div class="mix-big">${w.mix[0] ? esc(w.mix[0].kind) : "—"}</div><div class="mono small muted">${w.mix[0] ? pct(w.mix[0].weight, 1) : ""}</div></div>
              <div style="width:110px;height:110px"><canvas id="wl-mix"></canvas></div></div>
              <div class="small" style="margin-top:8px">${w.mix.map((m, i) => `<span style="color:${PALETTE[i % PALETTE.length]}">■</span> ${esc(m.kind)} ${m.count}`).join(" &nbsp; ")}</div></div>
          </div>
        </div>
        <div class="card" style="margin-top:16px">
          <div class="flex-between"><div class="pills">${[["all", "All"], ["gainers", "Gainers"], ["losers", "Losers"]].map(([k, l]) => `<button class="pillbtn ${MK.filter === k ? "on" : ""}" data-f="${k}">${l}</button>`).join("")}</div>
            <div class="row" style="align-items:center;gap:8px">${searchBox("wl-add", "+ Add symbol (e.g. TSLA, ^RUT, GCUSD)", s => addSym(s))}</div></div>
          <div class="table-wrap" style="margin-top:10px"><table>
            <thead><tr><th>Symbol</th><th class="num">Last</th><th class="num">Change</th><th class="num">Chg %</th><th class="num">Volume</th><th>Day range</th><th>52-week range</th><th class="num">Market cap</th><th class="num">Beta</th><th>Model rating</th><th></th></tr></thead>
            <tbody>${rows.map(r => `<tr class="mrow" data-s="${esc(r.symbol)}">
              <td><b>${esc(r.symbol)}</b><div class="muted small">${esc(r.name || "")}</div></td>
              <td class="num"><b${pxAttr(r, "t")}>${r.available ? px(r.price, r.kind) + unit(r) : "—"}</b></td>
              <td class="num ${cls(r.change_pct)}">${r.available ? chg(r.change, r.kind, r.price) : ""}</td>
              <td class="num"><span class="chip ${cls(r.change_pct)}">${r.available ? `${arrow(r.change_pct)} ${pct(Math.abs(r.change_pct), 2)}` : "n/a"}</span></td>
              <td class="num">${r.volume ? big(r.volume) : "—"}</td>
              <td class="mono small">${r.day_low != null ? `${px(r.day_low, r.kind)} – ${px(r.day_high, r.kind)}` : "—"}</td>
              <td style="min-width:150px">${rangeBar(r.year_low, r.year_high, r.price)}</td>
              <td class="num">${r.market_cap ? "$" + big(r.market_cap) : "—"}</td>
              <td class="num">${r.beta != null ? num(r.beta, 2) : "—"}</td>
              <td>${ratingCell(r)}</td>
              <td><button class="btn sm danger" data-rm="${esc(r.symbol)}" title="Remove">✕</button></td></tr>`).join("") || `<tr><td colspan="11" class="empty">Nothing here${MK.filter !== "all" ? " for this filter" : " — add a symbol above"}.</td></tr>`}</tbody></table></div>
        </div>
      </div>
    </div>`;

  async function addSym(s) {
    try { await api(`/watchlists/${MK.wl}/symbols`, { method: "POST", body: { symbol: s } }); toast(`${s} added`); watchlistView(MK.wl); } catch (e) { toast(e.message, true); }
  }
  $("#wl-pick").onchange = e => { location.hash = `#/watchlist/${e.target.value}`; };
  $("#wl-new").onclick = async () => {
    const name = prompt("Name for the new watchlist:", "Tech ideas"); if (!name) return;
    try { const r = await api("/watchlists", { method: "POST", body: { name } }); location.hash = `#/watchlist/${r.id}`; } catch (e) { toast(e.message, true); }
  };
  $("#wl-manage").onclick = async () => {
    const name = prompt(`Rename “${w.name}” — or type DELETE to remove this watchlist:`, w.name); if (!name || name === w.name) return;
    try {
      if (name.trim().toUpperCase() === "DELETE") { await api(`/watchlists/${w.id}`, { method: "DELETE" }); MK.wl = null; toast("Watchlist deleted"); location.hash = "#/watchlist"; route(); }
      else { await api(`/watchlists/${w.id}`, { method: "PATCH", body: { name } }); toast("Renamed"); watchlistView(w.id); }
    } catch (e) { toast(e.message, true); }
  };
  $$("[data-r]").forEach(b => b.onclick = () => { MK.range = b.dataset.r; saveMK(); watchlistView(MK.wl); });
  $$("[data-f]").forEach(b => b.onclick = () => { MK.filter = b.dataset.f; watchlistView(MK.wl); });
  $$("[data-rm]").forEach(b => b.onclick = async e => { e.stopPropagation(); try { await api(`/watchlists/${MK.wl}/symbols/${encodeURIComponent(b.dataset.rm)}`, { method: "DELETE" }); toast(`${b.dataset.rm} removed`); watchlistView(MK.wl); } catch (x) { toast(x.message, true); } });
  $$(".mrow").forEach(el => el.addEventListener("click", e => { if (e.target.closest("button, a")) return; openDetail(el.dataset.s); }));

  destroyCharts();
  const s = w.performance.series;
  const up = s.length > 1 && s[s.length - 1].y >= 0;
  chart($("#wl-chart"), { type: "line",
    data: { labels: s.map(p => p.x), datasets: [
      { data: s.map(p => p.y * 100), borderColor: up ? "#4f7a3a" : "#b3362a", borderWidth: 2, pointRadius: 0, tension: .15,
        fill: { target: { value: 0 }, above: "rgba(79,122,58,.10)", below: "rgba(179,54,42,.08)" } }] },
    options: { animation: window.QUIET || REDUCED ? false : { duration: 800, easing: "easeOutQuart" }, maintainAspectRatio: false, interaction: { mode: "index", intersect: false }, plugins: { legend: { display: false },
      tooltip: { callbacks: { label: c => ` ${c.parsed.y.toFixed(2)}%` } } },
      scales: { x: { ticks: { maxTicksLimit: 6 }, grid: { display: false } }, y: { ticks: { callback: v => v.toFixed(1) + "%", maxTicksLimit: 6 } } } } });
  if (w.mix.length) chart($("#wl-mix"), { type: "doughnut", data: { labels: w.mix.map(m => m.kind), datasets: [{ data: w.mix.map(m => m.count), backgroundColor: PALETTE, borderWidth: 0 }] },
    options: { animation: window.QUIET || REDUCED ? false : { duration: 800, easing: "easeOutQuart" }, cutout: "62%", plugins: { legend: { display: false } }, maintainAspectRatio: false } });
  pageTimer = setInterval(() => { if (!document.querySelector(".modal") && document.activeElement?.id !== "wl-add") quietRefresh(() => watchlistView(MK.wl)).catch(() => {}); }, 60000);
}
