/* 3D scenes (three.js, bundled locally in /static/vendor). Any element with data-scene="desk|bars|globe" that is
   added to the page gets a live WebGL scene; scenes pause off-screen and are disposed when the page changes.
   Palette: signal red #DD2F20 on black, matching the app theme. */
import * as THREE from "./vendor/three.min.js";

const RED = 0xdd2f20, DEEP = 0x7a1108, INK = 0x0a0a0a;
const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const live = new Set();

function makeRenderer(host) {
  const canvas = document.createElement("canvas");
  canvas.className = "scene-canvas";
  host.appendChild(canvas);
  const r = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: "high-performance" });
  r.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.75));
  r.outputColorSpace = THREE.SRGBColorSpace;
  r.toneMapping = THREE.ACESFilmicToneMapping;
  r.toneMappingExposure = 1.05;
  return r;
}

function mount(host, build) {
  if (host.dataset.mounted) return;
  host.dataset.mounted = "1";
  let renderer;
  try { renderer = makeRenderer(host); } catch (e) { host.classList.add("scene-fallback"); return; }
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 200);
  const api = build(scene, camera, host) || {};
  const pointer = { x: 0, y: 0 };
  const onMove = e => {
    const b = host.getBoundingClientRect();
    pointer.x = ((e.clientX - b.left) / b.width - 0.5) * 2;
    pointer.y = ((e.clientY - b.top) / b.height - 0.5) * 2;
  };
  host.addEventListener("pointermove", onMove);
  const resize = () => {
    const w = host.clientWidth || 1, h = host.clientHeight || 1;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  };
  const ro = new ResizeObserver(resize);
  ro.observe(host);
  resize();
  let visible = true, raf = 0, t0 = performance.now(), last = t0;
  const io = new IntersectionObserver(([e]) => { visible = e.isIntersecting; if (visible && !raf) loop(); }, { threshold: 0.01 });
  io.observe(host);
  function loop(now = performance.now()) {
    raf = 0;
    if (!host.isConnected) return dispose();
    const t = (now - t0) / 1000, dt = Math.min(0.05, (now - last) / 1000);
    last = now;
    api.update && api.update(t, dt, pointer);
    renderer.render(scene, camera);
    if (visible && !REDUCED) raf = requestAnimationFrame(loop);
  }
  function dispose() {
    cancelAnimationFrame(raf);
    io.disconnect(); ro.disconnect();
    host.removeEventListener("pointermove", onMove);
    scene.traverse(o => {
      if (o.geometry) o.geometry.dispose();
      const m = o.material; (Array.isArray(m) ? m : m ? [m] : []).forEach(mm => { mm.map && mm.map.dispose(); mm.dispose(); });
    });
    renderer.dispose();
    live.delete(dispose);
  }
  live.add(dispose);
  requestAnimationFrame(t => { resize(); loop(t); });
  setTimeout(() => host.classList.add("scene-ready"), 60);
}

function lights(scene, intensity = 1) {
  scene.add(new THREE.AmbientLight(0xffffff, 0.35 * intensity));
  const key = new THREE.DirectionalLight(0xffffff, 1.4 * intensity);
  key.position.set(4, 8, 6);
  scene.add(key);
  const rim = new THREE.PointLight(RED, 60 * intensity, 30, 1.6);
  rim.position.set(-5, 3, -4);
  scene.add(rim);
  const fill = new THREE.PointLight(0xff6a4d, 25 * intensity, 25, 1.8);
  fill.position.set(5, 1.5, 4);
  scene.add(fill);
}

// ------------------------------------------------------------------ screen textures (live-drawn charts)
function screenTexture(kind, real) {
  const c = document.createElement("canvas");
  c.width = 512; c.height = 320;
  const g = c.getContext("2d");
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 4;
  let series = [], p = 100;
  const realCloses = real && real.closes && real.closes.length > 5 ? real.closes : null;
  if (realCloses) {
    for (let i = 1; i < realCloses.length; i++) { const o = realCloses[i - 1], c = realCloses[i]; series.push({ o, c, h: Math.max(o, c) * 1.002, l: Math.min(o, c) * 0.998 }); }
  } else {
    for (let i = 0; i < 48; i++) { const o = p; p *= 1 + (Math.random() - 0.47) * 0.03; series.push({ o, c: p, h: Math.max(o, p) * (1 + Math.random() * 0.01), l: Math.min(o, p) * (1 - Math.random() * 0.01) }); }
  }
  const fmtN = v => v.toLocaleString(undefined, { maximumFractionDigits: v < 100 ? 2 : 0 });
  const book = Array.from({ length: 12 }, () => Math.random());
  function draw(t) {
    g.fillStyle = "#0b0b0b"; g.fillRect(0, 0, 512, 320);
    g.strokeStyle = "rgba(255,255,255,.06)"; g.lineWidth = 1;
    for (let y = 40; y < 320; y += 40) { g.beginPath(); g.moveTo(0, y); g.lineTo(512, y); g.stroke(); }
    g.font = "600 18px Raleway, Arial"; g.fillStyle = "#fff";
    if (kind === "candles") {
      g.fillText(realCloses ? "S&P 500 · 30 DAYS" : "S&P 500", 18, 30);
      if (realCloses) {
        const last = realCloses[realCloses.length - 1], ch = real.chg || 0;
        g.font = "700 26px Raleway, Arial"; g.textAlign = "right";
        g.fillStyle = ch >= 0 ? "#ffffff" : "#DD2F20"; g.fillText(fmtN(last), 494, 32);
        g.font = "600 15px Raleway, Arial"; g.fillText((ch >= 0 ? "▲ " : "▼ ") + (Math.abs(ch) * 100).toFixed(2) + "%", 494, 52);
        g.textAlign = "left";
      }
      const lo = Math.min(...series.map(s => s.l)), hi = Math.max(...series.map(s => s.h));
      const Y = v => 300 - (v - lo) / (hi - lo || 1) * 220;
      const step = Math.min(10, 470 / series.length);
      series.forEach((s, i) => {
        const x = 18 + i * step * (realCloses ? 1.6 : 1), up = s.c >= s.o;
        g.strokeStyle = g.fillStyle = up ? "#ffffff" : "#DD2F20";
        g.beginPath(); g.moveTo(x + 3, Y(s.h)); g.lineTo(x + 3, Y(s.l)); g.stroke();
        g.fillRect(x, Math.min(Y(s.o), Y(s.c)), 7, Math.max(2, Math.abs(Y(s.o) - Y(s.c))));
      });
    } else if (kind === "line") {
      const nc = real && real.nifty && real.nifty.length > 5 ? real.nifty : null;
      g.fillText(nc ? "NIFTY 50 · 30 DAYS" : "PORTFOLIO VALUE", 18, 30);
      g.beginPath();
      if (nc) {
        const lo = Math.min(...nc), hi = Math.max(...nc), n = nc.length;
        const reveal = Math.min(1, t / 2.5), upto = Math.max(2, Math.floor(n * reveal));
        for (let i = 0; i < upto; i++) { const x = 18 + i * (480 / (n - 1)), y = 290 - (nc[i] - lo) / (hi - lo || 1) * 200; i ? g.lineTo(x, y) : g.moveTo(x, y); }
      } else {
        for (let i = 0; i <= 60; i++) {
          const x = 18 + i * 8, y = 220 - Math.sin(i / 7 + t * 0.8) * 30 - i * 2.2 - Math.sin(i * 1.7 + t) * 6;
          i ? g.lineTo(x, y) : g.moveTo(x, y);
        }
      }
      g.strokeStyle = "#DD2F20"; g.lineWidth = 4; g.stroke();
      g.lineTo(498, 300); g.lineTo(18, 300); g.closePath();
      const gr = g.createLinearGradient(0, 60, 0, 300); gr.addColorStop(0, "rgba(221,47,32,.45)"); gr.addColorStop(1, "rgba(221,47,32,0)");
      g.fillStyle = gr; g.fill();
      g.fillStyle = "#fff"; g.font = "700 30px Raleway, Arial"; g.textAlign = "right";
      if (nc) { const ch = real.niftyChg || 0; g.fillText(fmtN(nc[nc.length - 1]), 494, 34); g.font = "600 15px Raleway, Arial"; g.fillStyle = ch >= 0 ? "#fff" : "#DD2F20"; g.fillText((ch >= 0 ? "▲ " : "▼ ") + (Math.abs(ch) * 100).toFixed(2) + "%", 494, 54); }
      else g.fillText("+12.4%", 494, 70);
      g.textAlign = "left";
    } else {
      g.fillText("ORDER BOOK", 18, 30);
      book.forEach((v, i) => {
        const w = 60 + (0.5 + 0.5 * Math.sin(t * 2 + i + v * 6)) * 380;
        g.fillStyle = i < 6 ? "rgba(221,47,32,.85)" : "rgba(255,255,255,.75)";
        g.fillRect(18, 48 + i * 22, w, 14);
      });
    }
    tex.needsUpdate = true;
  }
  let acc = 0;
  return {
    tex,
    tick(t, dt) {
      acc += dt;
      if (acc < 0.12) return;
      acc = 0;
      if (kind === "candles" && !realCloses) {
        const lastC = series[series.length - 1].c, c2 = lastC * (1 + (Math.random() - 0.47) * 0.025);
        series.push({ o: lastC, c: c2, h: Math.max(lastC, c2) * 1.004, l: Math.min(lastC, c2) * 0.996 });
        series.shift();
      }
      draw(t);
    },
    draw,
  };
}

// ------------------------------------------------------------------ scene: trading desk
function desk(scene, camera, host) {
  const J = k => { try { return JSON.parse(host.dataset[k] || "[]"); } catch (e) { return []; } };
  const real = { closes: J("sp"), chg: +host.dataset.spChg || 0, nifty: J("nifty"), niftyChg: +host.dataset.niftyChg || 0 };
  const mood = Math.max(-1, Math.min(1, +host.dataset.mood || 0));        // -1 risk-off .. +1 risk-on
  scene.fog = new THREE.Fog(INK, 9, 26);
  lights(scene, 0.75 + (mood + 1) * 0.2);
  host.dataset.moodLabel = mood > 0.25 ? "up" : mood < -0.25 ? "down" : "flat";
  const floorMat = new THREE.MeshStandardMaterial({ color: 0x0c0303, roughness: 0.5, metalness: 0.4 });
  const floor = new THREE.Mesh(new THREE.CircleGeometry(14, 64), floorMat);
  floor.rotation.x = -Math.PI / 2; floor.position.y = -1.35;
  scene.add(floor);

  const deskGroup = new THREE.Group();
  scene.add(deskGroup);
  const slab = new THREE.Mesh(new THREE.BoxGeometry(7.4, 0.16, 2.4), new THREE.MeshStandardMaterial({ color: 0x141414, roughness: 0.55, metalness: 0.35 }));
  slab.position.y = -0.2;
  deskGroup.add(slab);
  const edge = new THREE.Mesh(new THREE.BoxGeometry(7.42, 0.03, 2.42), new THREE.MeshStandardMaterial({ color: RED, emissive: RED, emissiveIntensity: 1.4 }));
  edge.position.y = -0.11;
  deskGroup.add(edge);
  [-3.2, 3.2].forEach(x => { const leg = new THREE.Mesh(new THREE.BoxGeometry(0.12, 1.1, 2), new THREE.MeshStandardMaterial({ color: 0x151515, metalness: 0.7, roughness: 0.3 })); leg.position.set(x, -0.8, 0); deskGroup.add(leg); });

  const screens = [];
  [["line", -2.45, 0.42], ["candles", 0, 0], ["book", 2.45, -0.42]].forEach(([kind, x, rot]) => {
    const s = screenTexture(kind, real);
    s.draw(0);
    screens.push(s);
    const mon = new THREE.Group();
    const body = new THREE.Mesh(new THREE.BoxGeometry(2.3, 1.45, 0.08), new THREE.MeshStandardMaterial({ color: 0x0d0d0d, metalness: 0.6, roughness: 0.35 }));
    const face = new THREE.Mesh(new THREE.PlaneGeometry(2.18, 1.34), new THREE.MeshBasicMaterial({ map: s.tex, toneMapped: false }));
    face.position.z = 0.045;
    const stand = new THREE.Mesh(new THREE.CylinderGeometry(0.04, 0.04, 0.55), new THREE.MeshStandardMaterial({ color: 0x222222, metalness: 0.8 }));
    stand.position.y = -0.95;
    mon.add(body, face, stand);
    mon.position.set(x, 1.05, -0.35 + Math.abs(x) * 0.12);
    mon.rotation.y = rot;
    deskGroup.add(mon);
  });

  // keyboard + coffee-cup accents
  const kb = new THREE.Mesh(new THREE.BoxGeometry(1.5, 0.05, 0.45), new THREE.MeshStandardMaterial({ color: 0x202020, roughness: 0.6 }));
  kb.position.set(0, -0.09, 0.55); deskGroup.add(kb);
  const cup = new THREE.Mesh(new THREE.CylinderGeometry(0.13, 0.11, 0.3, 24), new THREE.MeshStandardMaterial({ color: RED, roughness: 0.4 }));
  cup.position.set(2.2, 0.03, 0.6); deskGroup.add(cup);

  // rising bar-chart towers behind the desk
  const bars = [];
  const barMat = new THREE.MeshStandardMaterial({ color: RED, emissive: DEEP, emissiveIntensity: 0.6, roughness: 0.3, metalness: 0.2 });
  const barMatDark = new THREE.MeshStandardMaterial({ color: 0x1b1b1b, roughness: 0.35, metalness: 0.6 });
  for (let i = 0; i < 14; i++) {
    const h = (1 + Math.random() * 3) * (0.7 + (mood + 1) * 0.25);
    const m = new THREE.Mesh(new THREE.BoxGeometry(0.42, 1, 0.42), i % 3 === 0 ? barMatDark : barMat);
    m.position.set(-5.2 + i * 0.8, -1.35, -3.2 - (i % 2) * 0.7);
    m.userData = { h, phase: Math.random() * 6 };
    scene.add(m); bars.push(m);
  }

  // floating coins
  const coins = [];
  const coinMat = new THREE.MeshStandardMaterial({ color: 0xffffff, metalness: 0.9, roughness: 0.2, emissive: 0x220000 });
  for (let i = 0; i < 6; i++) {
    const coin = new THREE.Mesh(new THREE.CylinderGeometry(0.22, 0.22, 0.05, 40), i % 2 ? coinMat : barMat);
    coin.position.set(-3.5 + i * 1.4, 2.3 + Math.random() * 0.6, -1.6 + Math.random());
    coin.rotation.x = Math.PI / 2;
    coin.userData.phase = Math.random() * 6;
    scene.add(coin); coins.push(coin);
  }
  camera.position.set(0, 3, 10.5);
  return {
    update(t, dt, p) {
      screens.forEach(s => s.tick(t, dt));
      const intro = Math.min(1, t / 1.6), ease = 1 - Math.pow(1 - intro, 3);
      bars.forEach(b => {
        const h = b.userData.h * ease * (0.8 + 0.2 * Math.sin(t * 0.9 + b.userData.phase));
        b.scale.y = Math.max(0.01, h); b.position.y = -1.35 + h / 2;
      });
      coins.forEach(c => { c.position.y += Math.sin(t * 1.2 + c.userData.phase) * 0.003; c.rotation.z += dt * 1.4; });
      edge.material.emissiveIntensity = (0.7 + (mood + 1) * 0.35) + Math.sin(t * (1.2 + (mood + 1))) * 0.3;
      const a = Math.sin(t * 0.18) * 0.32 + p.x * 0.22;
      camera.position.x = Math.sin(a) * 10.5;
      camera.position.z = Math.cos(a) * 10.5;
      camera.position.y = 3.1 - p.y * 0.4;
      camera.lookAt(0, 0.45, -0.4);
    },
  };
}

// ------------------------------------------------------------------ scene: 3D bar field (values -1..+1 → heights)
function barsField(scene, camera, host) {
  let values = [];
  try { values = JSON.parse(host.dataset.values || "[]"); } catch (e) { /* none */ }
  if (!values.length) values = Array.from({ length: 24 }, () => Math.random() * 2 - 0.6);
  scene.fog = new THREE.Fog(INK, 10, 28);
  lights(scene, 1.1);
  const n = values.length, cols = Math.ceil(Math.sqrt(n * 2)), rows = Math.ceil(n / cols);
  const maxAbs = Math.max(...values.map(Math.abs), 0.01);
  const up = new THREE.MeshStandardMaterial({ color: RED, emissive: DEEP, emissiveIntensity: 0.55, roughness: 0.28, metalness: 0.25 });
  const down = new THREE.MeshStandardMaterial({ color: 0xf2f2f2, roughness: 0.3, metalness: 0.4 });
  const base = new THREE.Mesh(new THREE.BoxGeometry(cols * 0.9 + 1, 0.12, rows * 0.9 + 1), new THREE.MeshStandardMaterial({ color: 0x111111, metalness: 0.6, roughness: 0.3 }));
  base.position.y = -0.06; scene.add(base);
  const bars = values.map((v, i) => {
    const m = new THREE.Mesh(new THREE.BoxGeometry(0.62, 1, 0.62), v >= 0 ? up : down);
    const c = i % cols, r = Math.floor(i / cols);
    m.position.set((c - (cols - 1) / 2) * 0.9, 0, (r - (rows - 1) / 2) * 0.9);
    m.userData = { h: 0.25 + Math.abs(v) / maxAbs * 3.2, delay: (c + r) * 0.06 };
    scene.add(m);
    return m;
  });
  camera.position.set(6, 5.5, 8);
  return {
    update(t, dt, p) {
      bars.forEach(b => {
        const k = Math.min(1, Math.max(0, (t - b.userData.delay) / 1.2)), e = 1 - Math.pow(1 - k, 4);
        const h = Math.max(0.01, b.userData.h * e * (1 + 0.04 * Math.sin(t * 1.5 + b.position.x)));
        b.scale.y = h; b.position.y = h / 2;
      });
      const a = t * 0.12 + p.x * 0.3;
      camera.position.set(Math.sin(a) * 10, 5.8 - p.y * 0.6, Math.cos(a) * 10);
      camera.lookAt(0, 1, 0);
    },
  };
}

// ------------------------------------------------------------------ scene: advisory globe with trade arcs
function globe(scene, camera) {
  lights(scene, 0.9);
  const g = new THREE.Group(); scene.add(g);
  const R = 2.2;
  const pts = [], golden = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < 1800; i++) {
    const y = 1 - (i / 1799) * 2, r = Math.sqrt(1 - y * y), th = golden * i;
    pts.push(Math.cos(th) * r * R, y * R, Math.sin(th) * r * R);
  }
  const geo = new THREE.BufferGeometry(); geo.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
  g.add(new THREE.Points(geo, new THREE.PointsMaterial({ color: 0xffffff, size: 0.034, transparent: true, opacity: 0.85 })));
  g.add(new THREE.Mesh(new THREE.SphereGeometry(R * 0.985, 48, 48), new THREE.MeshStandardMaterial({ color: 0x0d0d0d, roughness: 0.6, metalness: 0.3 })));
  const ll = (lat, lon) => { const phi = (90 - lat) * Math.PI / 180, th = (lon + 180) * Math.PI / 180; return new THREE.Vector3(-R * Math.sin(phi) * Math.cos(th), R * Math.cos(phi), R * Math.sin(phi) * Math.sin(th)); };
  const cities = [[40.7, -74], [51.5, -0.1], [19.1, 72.9], [35.7, 139.7], [22.3, 114.2], [1.35, 103.8], [9.9, 76.3], [37.8, -122.4], [50.1, 8.7], [-33.9, 151.2]];
  const dotMat = new THREE.MeshBasicMaterial({ color: RED });
  cities.forEach(([a, b]) => { const d = new THREE.Mesh(new THREE.SphereGeometry(0.06, 16, 16), dotMat); d.position.copy(ll(a, b)); g.add(d); });
  const arcs = [];
  const pairs = [[0, 1], [1, 2], [2, 6], [0, 7], [3, 4], [4, 5], [1, 8], [5, 9], [7, 3], [8, 2], [0, 2]];
  const packetMat = new THREE.MeshBasicMaterial({ color: 0xffffff });
  pairs.forEach(([i, j], k) => {
    const a = ll(...cities[i]), b = ll(...cities[j]);
    const mid = a.clone().add(b).multiplyScalar(0.5).normalize().multiplyScalar(R + a.distanceTo(b) * 0.2);
    const curve = new THREE.QuadraticBezierCurve3(a, mid, b);
    const tube = new THREE.Mesh(new THREE.TubeGeometry(curve, 64, 0.011, 6), new THREE.MeshBasicMaterial({ color: RED, transparent: true, opacity: 0.75 }));
    const packet = new THREE.Mesh(new THREE.SphereGeometry(0.045, 12, 12), packetMat);
    g.add(tube, packet);
    arcs.push({ curve, packet, phase: k * 0.37, speed: 0.22 + (k % 3) * 0.05 });
  });
  const halo = new THREE.Mesh(new THREE.RingGeometry(R * 1.2, R * 1.215, 128), new THREE.MeshBasicMaterial({ color: RED, transparent: true, opacity: 0.35, side: THREE.DoubleSide }));
  halo.rotation.x = Math.PI / 2.3; scene.add(halo);
  camera.position.set(0, 0.6, 8.6);
  return {
    update(t, dt, p) {
      g.rotation.y += dt * 0.12;
      g.rotation.x = 0.25 + p.y * 0.12;
      g.rotation.z = p.x * 0.08;
      halo.rotation.z += dt * 0.1;
      arcs.forEach(a => { a.packet.position.copy(a.curve.getPoint((t * a.speed + a.phase) % 1)); });
      camera.lookAt(0, 0, 0);
    },
  };
}

// ------------------------------------------------------------------ scene: portfolio skyline
function labelSprite(text, color = "#ffffff") {
  const c = document.createElement("canvas"); c.width = 256; c.height = 64;
  const g = c.getContext("2d");
  g.font = "700 34px Raleway, Arial"; g.textAlign = "center"; g.textBaseline = "middle"; g.fillStyle = color;
  g.fillText(text, 128, 34);
  const tex = new THREE.CanvasTexture(c); tex.colorSpace = THREE.SRGBColorSpace;
  const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false }));
  sp.scale.set(1.3, 0.33, 1);
  return sp;
}
function skyline(scene, camera, host) {
  let rows = [];
  try { rows = JSON.parse(host.dataset.values || "[]"); } catch (e) { /* none */ }
  rows = rows.filter(r => r.w > 0.001 || r.t > 0.001).sort((a, b) => b.w - a.w).slice(0, 16);
  scene.fog = new THREE.Fog(INK, 12, 30);
  lights(scene, 1.05);
  const ground = new THREE.Mesh(new THREE.CircleGeometry(9, 64), new THREE.MeshStandardMaterial({ color: 0x0e0e0e, metalness: 0.5, roughness: 0.45 }));
  ground.rotation.x = -Math.PI / 2; scene.add(ground);
  const grid = new THREE.GridHelper(18, 36, 0x3a0d08, 0x1c1c1c); grid.position.y = 0.002; scene.add(grid);
  const maxW = Math.max(...rows.map(r => r.w), 0.01);
  const okMat = new THREE.MeshStandardMaterial({ color: 0xe9e9e9, roughness: 0.3, metalness: 0.35 });
  const hotMat = new THREE.MeshStandardMaterial({ color: RED, emissive: RED, emissiveIntensity: 0.55, roughness: 0.25, metalness: 0.2 });
  const winHot = new THREE.MeshBasicMaterial({ color: 0xffd9d4 }), winOk = new THREE.MeshBasicMaterial({ color: 0x9a9a9a });
  const n = rows.length, perRow = Math.ceil(Math.sqrt(n * 1.6)) || 1;
  const blds = rows.map((r, i) => {
    const tower = new THREE.Group();                                       // scaled vertically as it rises
    const h = 0.35 + (r.w / maxW) * 4.2, wdt = 0.55 + Math.min(0.45, r.w * 2);
    const body = new THREE.Mesh(new THREE.BoxGeometry(wdt, 1, wdt), r.b ? hotMat : okMat);
    body.position.y = 0.5; tower.add(body);
    for (let k = 0.18; k < 0.95; k += 0.17) {
      const band = new THREE.Mesh(new THREE.BoxGeometry(wdt + 0.012, 0.025, wdt + 0.012), r.b ? winHot : winOk);
      band.position.y = k; tower.add(band);
    }
    const grp = new THREE.Group(); grp.add(tower);
    const lbl = labelSprite(r.s, r.b ? "#ff6a57" : "#ffffff"); grp.add(lbl);
    const col = i % perRow, row = Math.floor(i / perRow);
    grp.position.set((col - (perRow - 1) / 2) * 1.55, 0, (row - (Math.ceil(n / perRow) - 1) / 2) * 1.55);
    grp.userData = { h, lean: Math.max(-0.35, Math.min(0.35, (r.w - r.t) * 4)), delay: i * 0.08, lbl, tower };
    scene.add(grp);
    return grp;
  });
  camera.position.set(7, 6, 9);
  return {
    update(t, dt, p) {
      blds.forEach(b => {
        const k = Math.min(1, Math.max(0, (t - b.userData.delay) / 1.3)), e = 1 - Math.pow(1 - k, 4);
        const h = Math.max(0.01, b.userData.h * e);
        b.userData.tower.scale.y = h;
        b.userData.lbl.position.y = h + 0.35;
        b.rotation.z = -b.userData.lean * e;
      });
      const a = t * 0.1 + p.x * 0.35;
      camera.position.set(Math.sin(a) * 12.5, 7.2 - p.y * 0.8, Math.cos(a) * 12.5);
      camera.lookAt(0, 1.9, 0);
    },
  };
}

// ------------------------------------------------------------------ scene: risk signature (calm sphere -> spiky, fast shape)
function risk(scene, camera, host) {
  const score = Math.max(0, Math.min(100, +host.dataset.score || 50)) / 100;
  lights(scene, 1);
  const geo = new THREE.IcosahedronGeometry(1.5, 16);
  const base = geo.attributes.position.array.slice();
  const col = new THREE.Color().lerpColors(new THREE.Color(0xf2f2f2), new THREE.Color(RED), Math.min(1, score * 1.25));
  const mat = new THREE.MeshStandardMaterial({ color: col, roughness: 0.32 - score * 0.15, metalness: 0.3, emissive: RED, emissiveIntensity: score * 0.35, flatShading: score > 0.6 });
  const mesh = new THREE.Mesh(geo, mat); scene.add(mesh);
  const wire = new THREE.Mesh(new THREE.IcosahedronGeometry(1.95, 2), new THREE.MeshBasicMaterial({ color: RED, wireframe: true, transparent: true, opacity: 0.18 + score * 0.25 }));
  scene.add(wire);
  const amp = 0.04 + score * 0.42, freq = 1.5 + score * 4.5, speed = 0.25 + score * 1.6;
  const pos = geo.attributes.position, v = new THREE.Vector3();
  camera.position.set(0, 0.3, 6.2);
  return {
    update(t, dt, p) {
      for (let i = 0; i < pos.count; i++) {
        v.set(base[i * 3], base[i * 3 + 1], base[i * 3 + 2]).normalize();
        const n = Math.sin(v.x * freq + t * speed * 1.7) * Math.sin(v.y * freq * 1.3 + t * speed) * Math.sin(v.z * freq * 0.9 - t * speed * 1.2);
        const r = 1.5 + amp * (score > 0.6 ? Math.pow(Math.abs(n), 0.6) * Math.sign(n) : n);
        pos.setXYZ(i, v.x * r, v.y * r, v.z * r);
      }
      pos.needsUpdate = true;
      geo.computeVertexNormals();
      mesh.rotation.y += dt * speed * 0.5; mesh.rotation.x = p.y * 0.3;
      wire.rotation.y -= dt * speed * 0.2; wire.rotation.z += dt * 0.05;
      camera.lookAt(0, 0, 0);
    },
  };
}

const BUILDERS = { desk, bars: barsField, globe, skyline, risk };
function mountAll(root = document) {
  (root.querySelectorAll ? root.querySelectorAll("[data-scene]") : []).forEach(el => {
    const b = BUILDERS[el.dataset.scene];
    if (b) mount(el, b);
  });
}
window.disposeScenes = () => [...live].forEach(d => d());
window.mountScenes = mountAll;
new MutationObserver(ms => {
  if (window.QUIET) return;                       // quiet refreshes re-use the running scene (see motion.js)
  ms.forEach(m => m.addedNodes.forEach(n => n instanceof Element && (n.matches("[data-scene]") ? mountAll(n.parentElement) : mountAll(n))));
}).observe(document.getElementById("view"), { childList: true, subtree: true });
mountAll();
