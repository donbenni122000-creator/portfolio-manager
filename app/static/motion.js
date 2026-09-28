/* Motion layer: scroll-triggered "pop up from the bottom" reveals, masked headline reveals, count-up numbers,
   sparkline draw-in and live price flashes. Skipped for reduced-motion users and during quiet auto-refreshes. */
window.QUIET = false;
const REDUCED = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const REVEAL_SEL = ".eyebrow, h1, h2.display-3, #view > .flex-between .sub, .hero-copy p, .hero-actions, .tabs, .tape, .card, .alert, .wl-item, .mover, .scene-host, .kpi-strip > *, .ch-lead, .ch-stats, .ch-visual, .alloc-rows, .dcf-intro p, .dcf-step, .dcf-verdict, .rec, .sky-copy p";
if (!REDUCED) Chart.defaults.animation = { duration: 900, easing: "easeOutQuart" };

// ---------------------------------------------------------------- count-up numbers
function countUp(el) {
  if (el.dataset.counted) return;
  el.dataset.counted = "1";
  const txt = el.textContent.trim();
  const m = txt.match(/^([^\d-]*)(-?[\d,]+(?:\.\d+)?)(.*)$/);
  if (!m) return;
  const [, pre, numStr, post] = m;
  const target = parseFloat(numStr.replace(/,/g, ""));
  if (!isFinite(target) || target === 0) return;
  const dec = (numStr.split(".")[1] || "").length;
  const from = target * 0.82, t0 = performance.now(), dur = 1000;
  const fmt = v => pre + v.toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec }) + post;
  const step = now => {
    const p = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - p, 3);
    el.textContent = fmt(from + (target - from) * e);
    if (p < 1) requestAnimationFrame(step); else el.textContent = txt;
  };
  requestAnimationFrame(step);
}

// ---------------------------------------------------------------- sparkline draw-in
function drawSpark(svg) {
  const line = svg.querySelector("polyline");
  if (!line || svg.dataset.drawn) return;
  svg.dataset.drawn = "1";
  let len = 300;
  try { len = Math.ceil(line.getTotalLength()) || 300; } catch (e) { /* not laid out */ }
  line.style.strokeDasharray = len;
  line.style.strokeDashoffset = len;
  line.getBoundingClientRect();
  line.style.transition = "stroke-dashoffset 1.2s cubic-bezier(.3,.7,.2,1)";
  requestAnimationFrame(() => { line.style.strokeDashoffset = 0; });
}

function play(el) {
  el.classList.add("in");
  el.querySelectorAll(".hero-px, .kpi .value, .wl-over .big, .gauge b, .stat-big").forEach(countUp);
  if (el.matches(".hero-px, .stat-big")) countUp(el);
  el.querySelectorAll("svg.spark").forEach(drawSpark);
}

// ---------------------------------------------------------------- reveal as elements scroll into view
let batch = 0, batchTimer = null;
const io = "IntersectionObserver" in window ? new IntersectionObserver(entries => {
  entries.forEach(e => {
    if (!e.isIntersecting) return;
    io.unobserve(e.target);
    e.target.style.setProperty("--d", Math.min(batch++, 10) * 70 + "ms");
    clearTimeout(batchTimer);
    batchTimer = setTimeout(() => { batch = 0; }, 160);
    play(e.target);
  });
}, { threshold: 0.08, rootMargin: "0px 0px -6% 0px" }) : null;

function prepare(el) {
  if (el.dataset.rev) return;
  el.dataset.rev = "1";
  el.classList.add("rv");
  if (el.tagName === "H1") {                       // clip-path hides it from IntersectionObserver - headlines sit at the top anyway
    el.classList.add("rv-mask");
    el.style.setProperty("--d", "80ms");
    requestAnimationFrame(() => requestAnimationFrame(() => play(el)));
    return;
  }
  io ? io.observe(el) : play(el);
}
function animateNode(root) {
  if (!(root instanceof Element)) return;
  if (REDUCED || window.QUIET) {
    root.querySelectorAll("svg.spark").forEach(s => { s.dataset.drawn = "1"; });
    return;
  }
  const els = root.matches(REVEAL_SEL) ? [root] : [];
  els.push(...root.querySelectorAll(REVEAL_SEL));
  els.filter(el => !el.closest(".modal")).forEach(prepare);
  // things outside revealable blocks still get their small animations
  root.querySelectorAll("svg.spark").forEach(s => { if (!s.closest(".rv")) drawSpark(s); });
}
new MutationObserver(muts => { for (const m of muts) m.addedNodes.forEach(animateNode); })
  .observe(document.getElementById("view"), { childList: true, subtree: true });

// ---------------------------------------------------------------- live price flashes on refresh
function snapshotPrices() {
  const snap = {};
  document.querySelectorAll("[data-px]").forEach(el => { snap[el.dataset.px + "|" + el.dataset.pxk] = parseFloat(el.dataset.v); });
  return snap;
}
function flashChanges(snap) {
  if (REDUCED) return;
  document.querySelectorAll("[data-px]").forEach(el => {
    const old = snap[el.dataset.px + "|" + el.dataset.pxk], now = parseFloat(el.dataset.v);
    if (old == null || isNaN(now) || old === now) return;
    el.classList.remove("flash-up", "flash-down");
    void el.offsetWidth;
    el.classList.add(now > old ? "flash-up" : "flash-down");
  });
}
async function quietRefresh(fn) {
  const snap = snapshotPrices();
  const y = window.scrollY;
  // keep running 3D scenes alive across the refresh instead of restarting them
  const scenes = {};
  document.querySelectorAll("[data-scene][data-mounted]").forEach(el => { scenes[el.dataset.scene] = el; });
  window.QUIET = true;
  try { await fn(); } finally { window.QUIET = false; }
  document.querySelectorAll("[data-scene]:not([data-mounted])").forEach(el => {
    const old = scenes[el.dataset.scene];
    if (old) { old.dataset.values = el.dataset.values || old.dataset.values || ""; el.replaceWith(old); }
  });
  window.mountScenes && window.mountScenes(document.getElementById("view"));
  document.querySelectorAll("#view [data-rev]").forEach(el => el.classList.add("in"));
  document.querySelectorAll("#view .rv:not(.in)").forEach(el => el.classList.add("in"));
  window.scrollTo(0, y);
  flashChanges(snap);
}
