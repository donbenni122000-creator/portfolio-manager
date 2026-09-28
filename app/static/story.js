/* Scroll storytelling: a client's story in four chapters, and the DCF valuation told step by step. */

// ---------------------------------------------------------------- helpers
const bigMoney = v => v == null ? "—" : Math.abs(v) >= 1e12 ? MONEY_SYM + (v / 1e12).toFixed(2) + "T" : Math.abs(v) >= 1e9 ? MONEY_SYM + (v / 1e9).toFixed(1) + "B" : Math.abs(v) >= 1e6 ? MONEY_SYM + (v / 1e6).toFixed(1) + "M" : money(v, 0);
const colBars = (items, opts = {}) => {
  const max = Math.max(...items.map(i => Math.abs(i.v)), 1e-9);
  return `<div class="colbars ${opts.cls || ""}">${items.map((it, i) => `
    <div class="cb" style="--h:${Math.max(3, Math.abs(it.v) / max * 100)}%;--i:${i}">
      <em>${esc(it.top || "")}</em><span class="${it.c || ""}"></span><b>${esc(it.lbl)}</b></div>`).join("")}</div>`;
};

// ---------------------------------------------------------------- client story
async function storyTab(el, id, arg, client) {
  const d = await api(`/clients/${id}/dashboard`);
  const c = d.client, v = d.valuation, m = d.metrics, p = d.profile;
  const first = esc(c.name.split(" ")[0]);
  const held = v.holdings.filter(h => h.qty > 0).sort((a, b) => b.value - a.value);
  const classes = {};
  held.forEach(h => { classes[h.asset_class || "Other"] = (classes[h.asset_class || "Other"] || 0) + h.weight; });
  if (v.cash_weight > 0.001) classes["Cash"] = v.cash_weight;
  const cls2 = Object.entries(classes).sort((a, b) => b[1] - a[1]);
  const g10 = m.available && m.growth_of_10k ? m.growth_of_10k : [];
  const recs = [];
  if (d.drift.needs_rebalance) recs.push({ t: "Rebalance back to target", d: `${d.drift.breaches} holding(s) have drifted outside their bands.`, href: `#/client/${id}/rebalance`, cta: "Review trades" });
  d.alerts.filter(a => !a.title.startsWith("Rebalancing")).slice(0, 3).forEach(a => recs.push({ t: a.title, d: a.detail, href: `#/client/${id}/overview`, cta: "See details" }));
  held.filter(h => h.rating === "SELL").slice(0, 2).forEach(h => recs.push({ t: `Review ${h.symbol}`, d: "Our research model rates it SELL.", href: `#/client/${id}/research/${h.symbol}`, cta: "Open research" }));
  if (!recs.length) recs.push({ t: "Stay the course", d: "Everything is inside its limits. Next check: the quarterly review.", href: `#/client/${id}/reports`, cta: "Prepare the report" });
  const volOk = m.available && m.annual_vol >= p.vol_band[0] && m.annual_vol <= p.vol_band[1];

  el.innerHTML = `
    <section class="chapter ch-white">
      <div class="ch-num">01</div>
      <div class="ch-body">
        <div class="eyebrow">Who they are</div>
        <h1 class="display">Meet ${first}.</h1>
        <p class="ch-lead">${esc(c.account_type)} account · ${esc(c.risk_profile)} investor · with us since ${c.created_at.slice(0, 10)}.
          ${c.objective ? `Goal: <b>${esc(c.objective)}</b>.` : ""}</p>
        <div class="ch-stats">
          <div><div class="stat-big dark">${num(c.risk_score, 0)}</div><div class="stat-lbl dark">risk score out of 100</div></div>
          <div><div class="stat-big dark">${pct(p.vol_band[0], 0)}–${pct(p.vol_band[1], 0)}</div><div class="stat-lbl dark">volatility band</div></div>
          <div><div class="stat-big dark">${pct(p.max_dd, 0)}</div><div class="stat-lbl dark">drawdown tolerance</div></div>
        </div>
      </div>
      <div class="ch-visual dark-bg"><div class="scene-host" data-scene="risk" data-score="${c.risk_score}" aria-hidden="true"></div></div>
    </section>

    <section class="chapter ch-dark">
      <div class="ch-num">02</div>
      <div class="ch-body">
        <div class="eyebrow light">What they own</div>
        <h1 class="display">${money(v.equity, 0)}<br>across ${held.length} holdings.</h1>
        <div class="alloc-rows">${cls2.map(([k, w], i) => `<div class="ar" style="--i:${i}"><span>${esc(k)}</span><div class="ar-bar"><i style="--w:${(w * 100).toFixed(1)}%"></i></div><b>${pct(w, 1)}</b></div>`).join("")}</div>
      </div>
      <div class="ch-visual">${colBars(held.slice(0, 8).map((h, i) => ({ v: h.value, lbl: h.symbol, top: pct(h.weight, 0), c: i === 0 ? "hot" : "" })), { cls: "on-dark" })}</div>
    </section>

    <section class="chapter ch-white">
      <div class="ch-num">03</div>
      <div class="ch-body">
        <div class="eyebrow">How it's going</div>
        <h1 class="display">${v.total_return >= 0 ? "Up" : "Down"} ${pct(Math.abs(v.total_return), 2)}<br>since day one.</h1>
        <p class="ch-lead">${money(v.total_pnl)} on ${money(v.net_deposits, 0)} invested. ${m.available ? `Over three years this mix returned ${pct(m.annual_return, 1)} a year with ${pct(m.annual_vol, 1)} volatility — ${volOk ? "right inside" : "outside"} the ${esc(p.name)} band.` : ""}</p>
        <div class="ch-stats">
          <div><div class="stat-big dark">${m.available ? num(m.sharpe, 2) : "—"}</div><div class="stat-lbl dark">Sharpe ratio</div></div>
          <div><div class="stat-big dark">${m.available ? pct(m.max_drawdown, 1) : "—"}</div><div class="stat-lbl dark">worst drawdown (3y)</div></div>
          <div><div class="stat-big dark">${m.available ? num(m.beta, 2) : "—"}</div><div class="stat-lbl dark">beta vs S&P 500</div></div>
        </div>
      </div>
      <div class="ch-visual"><div class="chart-box tall"><canvas id="st-growth"></canvas></div><div class="ch-cap">Growth of $10,000 in today's mix, three-year look-through</div></div>
    </section>

    <section class="chapter ch-red">
      <div class="ch-num">04</div>
      <div class="ch-body">
        <div class="eyebrow light">What we recommend</div>
        <h1 class="display">${recs.length === 1 && recs[0].t === "Stay the course" ? "Stay<br>the course." : `${recs.length} thing${recs.length === 1 ? "" : "s"}<br>to do next.`}</h1>
      </div>
      <div class="ch-visual recs">${recs.map((r, i) => `<a class="rec" href="${r.href}" style="--i:${i}"><div class="rec-n">${String(i + 1).padStart(2, "0")}</div><div><b>${esc(r.t)}</b><p>${esc(r.d)}</p><span>${esc(r.cta)} →</span></div></a>`).join("")}</div>
    </section>`;

  if (g10.length) {
    const io = new IntersectionObserver(([e]) => {
      if (!e.isIntersecting) return;
      io.disconnect();
      chart($("#st-growth"), { type: "line", data: { labels: g10.map(x => x.date), datasets: [{ data: g10.map(x => x.value), borderColor: "#DD2F20", borderWidth: 2.5, pointRadius: 0, fill: true, backgroundColor: "rgba(221,47,32,.08)", tension: .2 }] },
        options: { maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: { ticks: { maxTicksLimit: 5 }, grid: { display: false } }, y: { ticks: { callback: x => "$" + Math.round(x).toLocaleString() } } } } });
    }, { threshold: 0.25 });
    io.observe($("#st-growth"));
  }
}

// ---------------------------------------------------------------- DCF told step by step
function dcfShow(r) {
  if (!r || !r.dcf || !r.dcf.projection || !r.dcf.projection.length) return "";
  const P = r.dcf.projection, d = r.dcf, a = r.assumptions || {};
  const y0 = new Date().getFullYear();
  const rev = colBars(P.map((x, i) => ({ v: x.revenue, lbl: `'${String(y0 + i + 1).slice(2)}`, top: i % 3 === 0 ? bigMoney(x.revenue) : "", c: x.source && /consensus/i.test(x.source) ? "hot" : "" })));
  const fcf = colBars(P.map((x, i) => ({ v: x.fcf, lbl: `'${String(y0 + i + 1).slice(2)}`, top: i % 3 === 0 ? pct(x.fcf_margin, 0) : "", c: "hot" })));
  const pv = colBars(P.map((x, i) => ({ v: x.pv_fcf, lbl: `'${String(y0 + i + 1).slice(2)}`, top: i === 0 || i === P.length - 1 ? bigMoney(x.pv_fcf) : "", c: "" })));
  const netDebt = a.net_debt || 0;
  const wf = [
    { lbl: "PV of 10y cash flows", v: d.pv_fcf_sum, c: "" },
    { lbl: "PV of terminal value", v: d.pv_terminal, c: "" },
    { lbl: "Enterprise value", v: d.enterprise_value, c: "hot" },
    { lbl: netDebt >= 0 ? "Less net debt" : "Plus net cash", v: Math.abs(netDebt), c: "" },
    { lbl: "Equity value", v: d.equity_value, c: "hot" },
  ];
  const wmax = Math.max(...wf.map(x => x.v), 1);
  const st = (r.street && r.street.price_target) ? r.street.price_target.consensus : null;
  const marks = [["Bear", r.bear_value], ["Price", r.price], ["Fair value", r.fair_value], ["Bull", r.bull_value], ["Street", st]].filter(x => x[1]);
  const lo = Math.min(...marks.map(x => x[1])) * 0.9, hi = Math.max(...marks.map(x => x[1])) * 1.05;
  const at = x => ((x - lo) / (hi - lo) * 100).toFixed(1);
  return `
  <section class="dcf-show">
    <div class="dcf-intro"><div class="eyebrow light">The valuation, step by step</div>
      <h1 class="display">What is ${esc(r.symbol)}<br>really worth?</h1>
      <p>We forecast ten years of cash, discount it at ${pct(a.wacc, 1)} and compare it with today's price of ${money(r.price)}.</p></div>

    <div class="dcf-step"><div class="ds-n">01</div><div class="ds-copy"><h2>Revenue.</h2><p>Starts at ${pct(a.revenue_growth_start, 1)} growth (${esc(a.growth_source || "history")}), fading to ${pct(a.terminal_growth, 1)}. Red bars use Street consensus.</p></div>${rev}</div>
    <div class="dcf-step"><div class="ds-n">02</div><div class="ds-copy"><h2>Free cash flow.</h2><p>Cash left after running and reinvesting in the business — a ${pct(a.fcf_margin, 1)} margin today, ${pct(a.fcf_margin_terminal, 1)} at maturity.</p></div>${fcf}</div>
    <div class="dcf-step"><div class="ds-n">03</div><div class="ds-copy"><h2>Discounted to today.</h2><p>A dollar in 2036 is worth less than a dollar now. At a ${pct(a.wacc, 1)} WACC the far bars shrink — terminal value is ${pct(d.terminal_share_of_ev, 0)} of the total.</p></div>${pv}</div>
    <div class="dcf-step"><div class="ds-n">04</div><div class="ds-copy"><h2>From business to share.</h2><p>Add it up, adjust for debt and cash, divide by ${(a.shares / 1e9).toFixed(2)}B shares: <b>${money(d.per_share)}</b> per share from the DCF, blended 60/40 with peer multiples to <b>${money(r.fair_value)}</b>.</p></div>
      <div class="waterfall">${wf.map((x, i) => `<div class="wf" style="--i:${i}"><span>${esc(x.lbl)}</span><div class="wf-bar"><i class="${x.c}" style="--w:${(x.v / wmax * 100).toFixed(1)}%"></i></div><b>${bigMoney(x.v)}</b></div>`).join("")}</div></div>
    <div class="dcf-step"><div class="ds-n">05</div><div class="ds-copy"><h2>Price vs value.</h2><p>${r.upside >= 0 ? "Upside" : "Downside"} of <b>${pct(Math.abs(r.upside), 1)}</b> to our fair value.</p></div>
      <div class="pv-scale"><div class="pv-line"></div>${marks.map(([k, x], i) => `<div class="pv-mark ${k === "Fair value" ? "fair" : k === "Price" ? "px" : ""}" style="--x:${at(x)}%;--i:${i}"><i></i><span>${esc(k)}<b>${money(x)}</b></span></div>`).join("")}</div></div>
    <div class="dcf-verdict"><div class="stamp r-${r.rating}">${esc(r.rating)}</div><div><h2>${r.rating === "BUY" ? "Worth more than it costs." : r.rating === "SELL" ? "Priced above its value." : "Fairly priced, for now."}</h2>
      <p>Composite score ${num(r.scores.composite, 0)} / 100 · valuation ${num(r.scores.valuation, 0)}, quality ${num(r.scores.quality, 0)}, momentum ${num(r.scores.momentum, 0)} · ${esc(r.conviction)} conviction.</p></div></div>
  </section>`;
}
window.dcfShow = dcfShow;
