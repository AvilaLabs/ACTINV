// Shared pieces of the ACTINV course: sound, glossary, progress, tasks, log plots, the survey meter.

const store = {
  get(key, fallback) {
    try { const v = localStorage.getItem("actinv-learn:" + key); return v === null ? fallback : JSON.parse(v); }
    catch { return fallback; }
  },
  set(key, value) { try { localStorage.setItem("actinv-learn:" + key, JSON.stringify(value)); } catch { /* private mode */ } },
};
export { store };

export const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;

/* ---------- Sound: synthesized, quiet, off with one click ---------- */
export const sound = (() => {
  let ctx = null, master = null, noise = null;
  let on = store.get("sound", true);
  const listeners = new Set();
  function ensure() {
    if (!on) return null;
    if (!ctx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return null;
      ctx = new AC();
      master = ctx.createGain(); master.gain.value = 0.5; master.connect(ctx.destination);
      noise = ctx.createBuffer(1, ctx.sampleRate * 0.03, ctx.sampleRate);
      const d = noise.getChannelData(0);
      for (let i = 0; i < d.length; i++) d[i] = (Math.random() * 2 - 1) * Math.exp(-i / (ctx.sampleRate * 0.0012));
    }
    if (ctx.state === "suspended") ctx.resume();
    return ctx;
  }
  function tone(freq, start, dur, gain = 0.12, type = "sine") {
    const c = ensure(); if (!c) return;
    const t = c.currentTime + start;
    const o = c.createOscillator(), g = c.createGain();
    o.type = type; o.frequency.setValueAtTime(freq, t);
    g.gain.setValueAtTime(0, t); g.gain.linearRampToValueAtTime(gain, t + 0.01);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    o.connect(g); g.connect(master); o.start(t); o.stop(t + dur + 0.05);
  }
  return {
    get on() { return on; },
    set(v) { on = v; store.set("sound", v); if (v) ensure(); listeners.forEach((f) => f(v)); },
    onChange(f) { listeners.add(f); },
    unlock() { if (on) ensure(); },
    // A Geiger-counter tick: a filtered noise burst.
    click(gain = 0.5) {
      const c = ensure(); if (!c) return;
      const s = c.createBufferSource(); s.buffer = noise;
      const f = c.createBiquadFilter(); f.type = "highpass"; f.frequency.value = 1800;
      const g = c.createGain(); g.gain.value = gain;
      s.connect(f); f.connect(g); g.connect(master); s.start();
    },
    done() { tone(659.3, 0, 0.35, 0.09); tone(987.8, 0.09, 0.5, 0.08); },
    reveal() {
      const c = ensure(); if (!c) return;
      const t = c.currentTime, o = c.createOscillator(), g = c.createGain();
      o.type = "triangle"; o.frequency.setValueAtTime(330, t); o.frequency.exponentialRampToValueAtTime(880, t + 0.6);
      g.gain.setValueAtTime(0, t); g.gain.linearRampToValueAtTime(0.06, t + 0.08); g.gain.exponentialRampToValueAtTime(0.0001, t + 0.8);
      o.connect(g); g.connect(master); o.start(t); o.stop(t + 0.9);
    },
    tick() { tone(1400, 0, 0.03, 0.025, "square"); },
    key() { tone(240 + Math.random() * 60, 0, 0.025, 0.02, "square"); },
    wrong() { tone(220, 0, 0.18, 0.07, "triangle"); tone(185, 0.1, 0.25, 0.06, "triangle"); },
    fanfare() { [523.3, 659.3, 784, 1046.5].forEach((f, i) => tone(f, i * 0.11, 0.6, 0.07)); },
  };
})();
addEventListener("pointerdown", () => sound.unlock(), { once: true });
addEventListener("keydown", () => sound.unlock(), { once: true });

export function soundButton(btn) {
  const draw = () => {
    btn.setAttribute("aria-pressed", String(sound.on));
    btn.textContent = sound.on ? "Sound off" : "Sound on";
    btn.title = sound.on ? "Sound is on" : "Sound is off";
  };
  btn.addEventListener("click", () => { sound.set(!sound.on); draw(); if (sound.on) sound.tick(); });
  sound.onChange(draw);
  draw();
}

/* Light and dark, as the workbench's "Dark theme" button. */
export function themeButton(btn) {
  const draw = () => { btn.textContent = document.documentElement.dataset.theme === "dark" ? "Light theme" : "Dark theme"; };
  btn.addEventListener("click", () => {
    const dark = document.documentElement.dataset.theme !== "dark";
    if (dark) document.documentElement.dataset.theme = "dark"; else delete document.documentElement.dataset.theme;
    store.set("theme", dark ? "Dark" : "Light"); sound.tick(); draw();
  });
  draw();
}

/* ---------- Glossary: plain definitions one tap away ---------- */
let openPop = null;
export function glossary(terms) {
  document.querySelectorAll("[data-term]").forEach((el) => {
    const key = el.dataset.term;
    const entry = terms[key];
    if (!entry) { console.warn("[learn] no glossary entry", key); return; }
    const btn = document.createElement("button");
    btn.type = "button"; btn.className = "term"; btn.innerHTML = el.innerHTML;
    btn.setAttribute("aria-expanded", "false");
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const wasMine = openPop && openPop.owner === btn;
      closePop();
      if (wasMine) return;
      const pop = document.createElement("div");
      pop.className = "pop"; pop.setAttribute("role", "dialog");
      pop.innerHTML = `<b>${entry[0]}</b>${entry[1]}`;
      document.body.append(pop);
      const r = btn.getBoundingClientRect();
      const w = Math.min(340, innerWidth - 24);
      pop.style.maxWidth = w + "px";
      pop.style.left = Math.max(12, Math.min(innerWidth - w - 12, r.left + scrollX - 20)) + "px";
      pop.style.top = r.bottom + scrollY + 8 + "px";
      pop.owner = btn; openPop = pop; btn.setAttribute("aria-expanded", "true");
      sound.tick();
    });
    el.replaceWith(btn);
  });
  addEventListener("click", (e) => { if (openPop && !openPop.contains(e.target)) closePop(); });
  addEventListener("keydown", (e) => { if (e.key === "Escape") closePop(); });
}
function closePop() {
  if (!openPop) return;
  openPop.owner.setAttribute("aria-expanded", "false");
  openPop.remove(); openPop = null;
}

/* ---------- Tasks and progress ---------- */
const done = new Set(store.get("done", []));
const taskHooks = [];
export function onProgress(f) { taskHooks.push(f); f(done); }
export function isDone(id) { return done.has(id); }
export function complete(id, { quiet = false } = {}) {
  if (done.has(id)) return false;
  done.add(id); store.set("done", [...done]);
  document.querySelectorAll(`.task[data-task="${id}"]`).forEach((t) => t.classList.add("done"));
  if (!quiet) sound.done();
  taskHooks.forEach((f) => f(done));
  return true;
}
export function resetProgress() { done.clear(); store.set("done", []); location.reload(); }
export function markTasks() {
  document.querySelectorAll(".task[data-task]").forEach((t) => {
    if (!t.querySelector(".box")) t.insertAdjacentHTML("afterbegin", '<span class="box" aria-hidden="true"><svg viewBox="0 0 12 12"><path d="M2 6.5l2.6 2.6L10 3"/></svg></span>');
    if (done.has(t.dataset.task)) t.classList.add("done");
  });
}

/* Messages go to the status bar at the bottom, where the workbench puts them. */
export function toast(text) {
  const el = document.querySelector("#status");
  if (!el) return;
  el.textContent = text; el.classList.add("fresh");
  clearTimeout(el._t); el._t = setTimeout(() => el.classList.remove("fresh"), 2600);
}

/* ---------- Numbers people can read ---------- */
const SUP = { "-": "⁻", 0: "⁰", 1: "¹", 2: "²", 3: "³", 4: "⁴", 5: "⁵", 6: "⁶", 7: "⁷", 8: "⁸", 9: "⁹" };
export function sci(x, digits = 2) {
  if (!isFinite(x) || x === 0) return "0";
  const e = Math.floor(Math.log10(Math.abs(x)));
  if (e >= -2 && e < 4) return (+x.toPrecision(digits)).toLocaleString("en-US");
  const m = x / 10 ** e;
  return `${(+m.toPrecision(digits))}×10${String(e).replace(/./g, (c) => SUP[c])}`;
}
// Dose rate with a prefix: 4.8 mSv/h, 12 µSv/h …
export function doseText(gyh) {
  const sv = gyh; // air kerma rate in Gy/h, read as Sv/h for these photons (see the chapter's note)
  const units = [[1, "Sv/h"], [1e-3, "mSv/h"], [1e-6, "µSv/h"], [1e-9, "nSv/h"]];
  for (const [f, u] of units) if (sv >= f) return `${+(sv / f).toPrecision(2)} ${u}`;
  return `${+(sv / 1e-12).toPrecision(2)} pSv/h`;
}
export function bqText(bq) {
  const units = [[1e12, "TBq"], [1e9, "GBq"], [1e6, "MBq"], [1e3, "kBq"], [1, "Bq"]];
  for (const [f, u] of units) if (bq >= f) return `${+(bq / f).toPrecision(2)} ${u}`;
  return `${+bq.toPrecision(2)} Bq`;
}
export function wattText(w) {
  const units = [[1, "W"], [1e-3, "mW"], [1e-6, "µW"], [1e-9, "nW"]];
  for (const [f, u] of units) if (w >= f) return `${+(w / f).toPrecision(2)} ${u}`;
  return `${sci(w)} W`;
}
const DAY = 86400, YEAR = 365.25 * DAY;
export function durationText(s) {
  if (s < 3600) return `${Math.round(s / 60)} min`;
  if (s < DAY) return `${+(s / 3600).toPrecision(2)} h`;
  if (s < 300 * DAY) return `${+(s / DAY).toPrecision(3)} days`.replace(/^1 days/, "1 day");
  return `${+(s / YEAR).toPrecision(2)} years`.replace(/^1 years/, "1 year");
}

/* ---------- Log-paper plots ---------- */
const NS = "http://www.w3.org/2000/svg";
export function svg(tag, attrs = {}, parent) {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  if (parent) parent.append(el);
  return el;
}
export function scale(domain, range, log) {
  const [d0, d1] = log ? domain.map(Math.log10) : domain;
  const f = (v) => range[0] + ((log ? Math.log10(v) : v) - d0) / (d1 - d0) * (range[1] - range[0]);
  f.invert = (p) => { const v = d0 + (p - range[0]) / (range[1] - range[0]) * (d1 - d0); return log ? 10 ** v : v; };
  return f;
}
/* A plot frame on log or linear axes. Returns {root, x, y, line(), clear} */
export function plot(container, opts) {
  const avail = container.clientWidth || container.closest(".lesson, main")?.clientWidth || 860;
  const W = opts.width || Math.max(320, Math.min(900, avail)), H = opts.height || 380;
  const m = Object.assign({ l: 70, r: 20, t: 16, b: 46 }, opts.margin || {});
  const root = svg("svg", { class: "plot", viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": opts.label || "" });
  container.append(root);
  const x = scale(opts.x.domain, [m.l, W - m.r], opts.x.log);
  const y = scale(opts.y.domain, [H - m.b, m.t], opts.y.log);
  const grid = svg("g", {}, root);
  const decade = (d) => { const out = []; for (let e = Math.floor(Math.log10(d[0])); e <= Math.ceil(Math.log10(d[1])); e++) out.push(e); return out; };
  if (opts.y.log) {
    for (const e of decade(opts.y.domain)) {
      for (let k = 1; k < 10; k++) {
        const v = k * 10 ** e; if (v < opts.y.domain[0] || v > opts.y.domain[1]) continue;
        svg("line", { x1: m.l, x2: W - m.r, y1: y(v), y2: y(v), class: k === 1 ? "gmajor" : "gminor" }, grid);
        if (k === 1 && (opts.y.every ? e % opts.y.every === 0 : true)) svg("text", { x: m.l - 8, y: y(v) + 4, "text-anchor": "end", class: "tick" }, grid).textContent = (opts.y.fmt || sci)(v);
      }
    }
  } else {
    for (const v of opts.y.ticks) {
      svg("line", { x1: m.l, x2: W - m.r, y1: y(v), y2: y(v), class: "gmajor" }, grid);
      svg("text", { x: m.l - 8, y: y(v) + 4, "text-anchor": "end", class: "tick" }, grid).textContent = (opts.y.fmt || String)(v);
    }
  }
  const xticks = (W < 600 && opts.x.narrow) || opts.x.ticks;
  for (const [v, label] of xticks) {
    svg("line", { x1: x(v), x2: x(v), y1: m.t, y2: H - m.b, class: "gmajor" }, grid);
    svg("text", { x: x(v), y: H - m.b + 18, "text-anchor": "middle", class: "tick" }, grid).textContent = label;
  }
  if (opts.x.minor) for (const v of opts.x.minor) svg("line", { x1: x(v), x2: x(v), y1: m.t, y2: H - m.b, class: "gminor" }, grid);
  if (opts.x.title) svg("text", { x: (m.l + W - m.r) / 2, y: H - 6, "text-anchor": "middle", class: "axis-title" }, root).textContent = opts.x.title;
  if (opts.y.title) svg("text", { x: 14, y: (m.t + H - m.b) / 2, transform: `rotate(-90 14 ${(m.t + H - m.b) / 2})`, "text-anchor": "middle", class: "axis-title" }, root).textContent = opts.y.title;
  svg("rect", { x: m.l, y: m.t, width: W - m.l - m.r, height: H - m.t - m.b, class: "pframe" }, grid);
  const layer = svg("g", {}, root);
  const top = svg("g", {}, root);
  const clampY = (v) => Math.min(Math.max(v, opts.y.domain[0]), opts.y.domain[1]);
  function path(xs, ys) {
    let d = "";
    xs.forEach((xv, i) => {
      const yv = ys[i];
      if (!(yv > 0) && opts.y.log) return;
      d += (d ? "L" : "M") + x(xv).toFixed(1) + "," + y(clampY(yv)).toFixed(1);
    });
    return d;
  }
  return {
    root, x, y, layer, top, W, H, m,
    line(xs, ys, attrs = {}, { animate = false } = {}) {
      const p = svg("path", { d: path(xs, ys), ...attrs }, attrs.top ? top : layer);
      if (animate && !reducedMotion) {
        const len = p.getTotalLength();
        p.style.setProperty("--len", len);
        p.classList.add("draw");
      }
      return p;
    },
    update(p, xs, ys) { p.setAttribute("d", path(xs, ys)); },
  };
}

/* ---------- The survey meter ---------- */
/* Clicks follow the dose rate on a log scale: background (about 0.1 µSv/h) ticks now and then,
   millisieverts per hour roar. It plays only while a section that drives it is on screen. */
export const meter = (() => {
  let el, needle, valEl, subEl, led, timer = null, rate = 0, live = false, muted = store.get("meter-muted", false);
  let hidden = store.get("meter-hidden", false);
  // A hidden meter comes back from the toolbar's "Survey meter" button.
  function reopen(showButton) {
    const b = document.querySelector("#meter-btn");
    if (!b) return;
    b.hidden = !showButton;
    b.onclick = () => { hidden = false; store.set("meter-hidden", false); el.hidden = false; el.classList.add("shown"); b.hidden = true; schedule(); };
  }
  function build() {
    el = document.createElement("aside");
    el.className = "meter"; el.setAttribute("aria-label", "Survey meter");
    el.innerHTML = `
      <div class="title"><span><span class="led"></span>Survey meter</span><span><button type="button" class="btn mute">${muted ? "Unmute" : "Mute"}</button> <button type="button" class="btn close" title="Hide the survey meter" aria-label="Hide the survey meter">✕</button></span></div>
      <div class="body">
      <svg class="dial" viewBox="0 0 200 74" aria-hidden="true">
        <path class="arc" d="M18 68 A82 82 0 0 1 182 68" fill="none" stroke-width="6"/>
        <path class="hot" d="M164.2 17 A82 82 0 0 1 182 68" fill="none" stroke-width="6"/>
        <g class="ticks"></g>
        <line class="needle" x1="100" y1="68" x2="100" y2="12" stroke-width="2"/>
        <circle cx="100" cy="68" r="4" fill="currentColor" class="hub"/>
      </svg>
      <div class="val" aria-live="polite">—</div><div class="sub">waiting for a result</div></div>`;
    document.body.append(el);
    needle = el.querySelector(".needle"); valEl = el.querySelector(".val"); subEl = el.querySelector(".sub"); led = el.querySelector(".led");
    const ticks = el.querySelector(".ticks");
    for (let i = 0; i <= 8; i++) {
      const a = Math.PI * (1 - i / 8);
      svg("line", { x1: 100 + 70 * Math.cos(a), y1: 68 - 70 * Math.sin(a), x2: 100 + 62 * Math.cos(a), y2: 68 - 62 * Math.sin(a), class: "arc", "stroke-width": 1 }, ticks);
    }
    el.querySelector(".close").addEventListener("click", () => {
      hidden = true; store.set("meter-hidden", true); el.hidden = true; schedule(); reopen(true);
    });
    el.querySelector(".mute").addEventListener("click", () => {
      muted = !muted; store.set("meter-muted", muted);
      el.querySelector(".mute").textContent = muted ? "Unmute" : "Mute";
    });
  }
  function schedule() {
    clearTimeout(timer);
    if (!live || rate <= 0 || hidden) return;
    const wait = -Math.log(1 - Math.random()) / rate * 1000; // Poisson: random, like the real thing
    timer = setTimeout(() => {
      if (!muted && document.visibilityState === "visible") sound.click(0.35 + Math.random() * 0.2);
      led.classList.add("blink"); setTimeout(() => led.classList.remove("blink"), 40);
      schedule();
    }, Math.min(wait, 8000));
  }
  return {
    show() { if (!el) build(); if (hidden) { reopen(true); return; } requestAnimationFrame(() => el.classList.add("shown")); },
    set(gyh, label) {
      if (!el) build();
      // 1e-8 Gy/h (background-ish) at the left, 1e-1 at the right.
      const f = Math.min(1, Math.max(0, (Math.log10(Math.max(gyh, 1e-12)) + 8) / 7));
      needle.style.transform = `rotate(${(f - 0.5) * 180}deg)`;
      valEl.textContent = doseText(gyh);
      subEl.textContent = "1 m from the bracket, " + label;
      rate = Math.min(45, Math.max(0.25, 0.6 * (gyh / 1e-7) ** 0.42));
      schedule();
    },
    live(v) { live = v; schedule(); if (el) el.style.opacity = v ? "1" : ".72"; },
  };
})();

/* ---------- Rail: current step and finished steps ---------- */
export function rail(stepTasks) {
  const links = [...document.querySelectorAll(".side a")];
  links.forEach((a) => a.insertAdjacentHTML("beforeend", '<svg class="tick" viewBox="0 0 14 14" aria-hidden="true"><path d="M2 7.5l3 3L12 3.5"/></svg>'));
  const side = document.querySelector(".side"), menu = document.querySelector("#menu");
  menu?.addEventListener("click", (e) => { e.stopPropagation(); side.classList.toggle("open"); });
  links.forEach((a) => a.addEventListener("click", () => side.classList.remove("open")));
  addEventListener("click", (e) => { if (side.classList.contains("open") && !side.contains(e.target)) side.classList.remove("open"); });
  const sections = links.map((a) => document.querySelector(a.getAttribute("href")));
  const io = new IntersectionObserver((entries) => {
    entries.forEach((e) => {
      if (e.isIntersecting) links.forEach((a, i) => a.classList.toggle("current", sections[i] === e.target));
    });
  }, { rootMargin: "-40% 0px -55% 0px" });
  sections.forEach((s) => s && io.observe(s));
  const meta = document.querySelector("#progress-text");
  onProgress((set) => {
    links.forEach((a) => {
      const id = a.getAttribute("href").slice(1);
      const need = stepTasks[id] || [];
      a.classList.toggle("done", need.length > 0 && need.every((t) => set.has(t)));
    });
    const n = links.filter((a) => a.classList.contains("done")).length;
    if (meta) meta.textContent = `${n} of ${links.length} steps done`;
  });
}
