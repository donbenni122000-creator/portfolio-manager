/* News desk: WSJ section headlines + watchlist ticker news. Articles open on the publisher's site in the browser,
   where the user's own WSJ subscription applies. */
const NEWS = { section: "top" };
try { NEWS.section = localStorage.getItem("pm.newsSection") || "top"; } catch (e) { /* ignore */ }

function ago(ts) {
  if (!ts) return "";
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 3600) return Math.max(1, Math.round(s / 60)) + "m ago";
  if (s < 86400) return Math.round(s / 3600) + "h ago";
  if (s < 7 * 86400) return Math.round(s / 86400) + "d ago";
  return new Date(ts * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
const img = (u, w = 720) => u ? (u.includes("images.wsj.net") ? `${u}?width=${w}&height=${Math.round(w * 0.6)}` : u) : "";
const ext = url => `href="${esc(url)}" target="_blank" rel="noopener noreferrer"`;

function storyCard(it, i) {
  return `<a class="news-card" ${ext(it.url)} style="--i:${i}">
    ${it.image ? `<div class="nc-img" style="background-image:url('${esc(img(it.image, 520))}')"></div>` : `<div class="nc-img nc-noimg"><span>${esc(it.publisher || it.source)}</span></div>`}
    <div class="nc-body"><div class="nc-meta"><b>${esc(it.section || it.publisher || "")}</b> · ${ago(it.ts)}</div>
      <h3 class="nc-title">${esc(it.title)}</h3>
      ${it.summary ? `<p>${esc(it.summary)}</p>` : ""}
      <div class="nc-foot">${esc(it.publisher || it.source)}${it.authors && it.authors.length ? " · " + esc(it.authors.slice(0, 2).join(", ")) : ""} <span>↗</span></div></div></a>`;
}

async function newsView(section) {
  if (pageTimer) { clearInterval(pageTimer); pageTimer = null; }
  if (section) NEWS.section = section;
  try { localStorage.setItem("pm.newsSection", NEWS.section); } catch (e) { /* ignore */ }
  const [d, w] = await Promise.all([api(`/news?section=${encodeURIComponent(NEWS.section)}`), api("/news/watchlist").catch(() => ({ items: [], symbols: [] }))]);
  const items = d.items || [];
  const lead = items.find(x => x.image) || items[0];
  const rest = items.filter(x => x !== lead);
  const label = (d.sections.find(s => s.key === NEWS.section) || {}).label || "Top stories";
  view.innerHTML = `
    <section class="hero-dark news-hero">
      <div class="hero-copy">
        <div class="eyebrow light">News desk · The Wall Street Journal</div>
        <h1 class="display">What moved,<br>and why.</h1>
        <p>Live WSJ headlines and news on the names you watch. Full articles open on wsj.com — sign in there once with your WSJ subscription and they read in full.</p>
        <div class="hero-actions"><a class="btn red" href="https://www.wsj.com" target="_blank" rel="noopener">Open WSJ ↗</a>
          <a class="btn light" href="https://www.wsj.com/client/login" target="_blank" rel="noopener">Sign in to WSJ ↗</a></div>
      </div>
      <div class="news-hero-side">${items.slice(0, 5).map((it, i) => `<a class="nh-item" ${ext(it.url)}><span>${String(i + 1).padStart(2, "0")}</span><div><b>${esc(it.title)}</b><em>${esc(it.section || "")} · ${ago(it.ts)}</em></div></a>`).join("")}</div>
    </section>
    <div class="pills news-tabs">${d.sections.map(s => `<a class="pillbtn ${s.key === NEWS.section ? "on" : ""}" href="#/news/${s.key}">${esc(s.label)}</a>`).join("")}
      <span class="muted small" style="margin-left:auto">Updated ${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} · refreshes every 5 min</span></div>
    ${d.errors && d.errors.length && !items.length ? `<div class="alert warning"><b>Couldn't load the news feeds</b>${esc(d.errors.join(" · "))}</div>` : ""}
    <div class="news-grid">
      <div class="news-main">
        ${lead ? `<a class="news-lead card" ${ext(lead.url)}>
          ${lead.image ? `<div class="nl-img" style="background-image:url('${esc(img(lead.image, 1100))}')"></div>` : ""}
          <div class="nl-body"><div class="nc-meta"><b>${esc(lead.section || label)}</b> · ${ago(lead.ts)}</div>
            <h2 class="nl-title">${esc(lead.title)}</h2>${lead.summary ? `<p>${esc(lead.summary)}</p>` : ""}
            <div class="nc-foot">${esc(lead.publisher)}${lead.authors && lead.authors.length ? " · " + esc(lead.authors.join(", ")) : ""} <span>Read on WSJ ↗</span></div></div></a>` : `<div class="empty">No headlines right now.</div>`}
        <div class="news-cards">${rest.map(storyCard).join("")}</div>
      </div>
      <aside class="news-side">
        <div class="card"><h3>On your watchlist</h3>
          ${w.items && w.items.length ? w.items.slice(0, 14).map(it => `<a class="wn-item" ${ext(it.url)}><span class="wn-sym" data-s="${esc(it.symbol)}">${esc(it.symbol)}</span><div><b>${esc(it.title)}</b><em>${esc(it.publisher)} · ${ago(it.ts)}</em></div></a>`).join("")
            : `<div class="muted small">Add stocks to your watchlist to see their news here.</div>`}
        </div>
      </aside>
    </div>`;
  $$(".wn-sym").forEach(el => el.addEventListener("click", e => { e.preventDefault(); e.stopPropagation(); openDetail(el.dataset.s); }));
  pageTimer = setInterval(() => { if (!document.querySelector(".modal")) quietRefresh(() => newsView()).catch(() => {}); }, 5 * 60 * 1000);
}

// headlines strip inside the chart pop-up
async function loadTickerNews(symbol, el) {
  if (!el) return;
  el.innerHTML = `<h3>News · ${esc(symbol)}</h3><div class="muted small">Loading headlines…</div>`;
  try {
    const d = await api(`/news/ticker/${encodeURIComponent(symbol)}`);
    if (!document.body.contains(el)) return;
    el.innerHTML = `<h3>News · ${esc(symbol)}</h3>` + ((d.items || []).length
      ? `<div class="pcn-list">${d.items.slice(0, 6).map(it => `<a class="pcn" ${ext(it.url)}><b>${esc(it.title)}</b><em>${esc(it.publisher)} · ${ago(it.ts)}</em></a>`).join("")}</div>`
      : `<div class="muted small">${esc(d.error || "No recent headlines for this symbol.")}</div>`);
  } catch (e) { el.innerHTML = `<h3>News · ${esc(symbol)}</h3><div class="muted small">${esc(e.message)}</div>`; }
}
window.loadTickerNews = loadTickerNews;
