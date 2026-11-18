import {
  sound, soundButton, glossary, complete, isDone, markTasks, rail, onProgress, toast, resetProgress, store,
  plot, svg, sci, doseText, bqText, wattText, durationText, meter, reducedMotion,
} from "../learn.js";

const DAY = 86400, YEAR = 365.25 * DAY;
const LIMIT = 3e-3; // the chapter's illustrative access limit, Sv/h (air kerma Gy/h)
const $ = (s) => document.querySelector(s);

/* ---------- Glossary ---------- */
glossary({
  activity: ["Activity", "How many atoms decay each second. Measured in becquerels: 1 Bq is one decay per second. A banana has about 15 Bq, from its potassium."],
  dt: ["Deuterium and tritium", "Two heavy forms of hydrogen. When they fuse they make helium and a neutron with a lot of energy, 14.1 MeV. That's the fuel of most fusion machines being built."],
  neutron: ["Neutron", "One of the two particles in an atom's nucleus. It has no electric charge, so it isn't pushed away by atoms and goes deep into materials."],
  activation: ["Activation", "Making a material radioactive by hitting it with neutrons (or other particles). ACTINV's name comes from ACTivation and INVentory."],
  nuclide: ["Nuclide", "A specific kind of atomic nucleus, named by element and mass number: iron-56 (Fe56) or cobalt-60 (Co60). Isotopes are nuclides of the same element."],
  decay: ["Radioactive decay", "An unstable nucleus turning into another one by itself, giving off radiation: an electron (beta), a helium nucleus (alpha), or gamma rays, which are energetic light."],
  halflife: ["Half-life", "The time for half of a radioactive nuclide's atoms to decay. After two half-lives a quarter is left, after ten about a thousandth."],
  ss316: ["SS316", "A common stainless steel: mostly iron, with 17% chromium, 12% nickel, 2.5% molybdenum and small amounts of other elements. Used all over fusion and fission plants."],
  flux: ["Neutron flux", "How many neutrons cross a square centimetre each second, in n/cm²/s. Near a fusion plasma it can reach 10¹⁴. Behind shielding it is much lower."],
  json: ["JSON", "A plain-text format for structured information: labels and values, in curly braces. Many programs read and write it, and it's readable in any text editor."],
  dose: ["Dose rate", "How fast radiation deposits energy in a person, in sieverts per hour (Sv/h). Most of us get about 2 to 3 thousandths of a sievert a year from natural sources."],
  msvh: ["Millisievert per hour", "One thousandth of a sievert per hour (mSv/h). At 4 mSv/h, a worker would reach a typical annual limit of 20 mSv in five hours."],
  heat: ["Decay heat", "The energy the radioactive decays deposit in the material, in watts. In this bracket it's small, but in a reactor core or a fusion blanket it can need active cooling."],
  terminal: ["Terminal", "A window where you type commands and the computer prints its answers. Also called the command line, shell, console or PowerShell."],
  python: ["Python", "A free programming language that's widely used in science. ACTINV installs into it like any other package: python -m pip install actinv."],
  impurity: ["Impurity", "An element that's in a material but not part of its recipe, usually in tiny amounts. Steel often contains a little cobalt, niobium or silver that nobody added on purpose."],
});

soundButton($("#sound"));
markTasks();
rail({
  welcome: ["welcome"], physics: ["beam", "halflife"], problem: ["file"], guess: ["guess"],
  run: ["ran"], read: ["access", "handoff"], cobalt: ["cobalt-guess", "cobalt"], wrap: ["quiz"],
});
document.querySelectorAll(".ver").forEach((el) => (el.textContent = "1.7.2"));
$("#reset").addEventListener("click", resetProgress);
$("#start").addEventListener("click", () => {
  complete("welcome");
  $("#physics").scrollIntoView({ behavior: reducedMotion ? "auto" : "smooth" });
});

const [problem, data] = await Promise.all([
  fetch("files/bracket.json").then((r) => r.json()),
  fetch("data/bracket-data.json").then((r) => r.json()),
]);
document.querySelectorAll(".ver").forEach((el) => (el.textContent = data.actinv_version.replace("actinv ", "")));

const COLORS = {
  Co60: "#5f3dc4", Co58: "#ae3ec9", Co57: "#1c7ed6", Mn54: "#e03131", Mn56: "#0ca678", Cr51: "#74b816",
  Fe55: "#868e96", Ni63: "#495057", Nb94: "#d6336c", V52: "#0b7285", Fe59: "#f08c00",
};
const timeNarrow = [[60, "1 min"], [DAY, "1 d"], [30.44 * DAY, "1 mo"], [YEAR, "1 y"], [100 * YEAR, "100 y"]];
const doseNarrow = [[1e-7, "0.1 µSv/h"], [1e-4, "0.1 mSv/h"], [1e-1, "0.1 Sv/h"]];
const timeTicks = [[60, "1 min"], [3600, "1 h"], [DAY, "1 d"], [7 * DAY, "1 wk"], [30.44 * DAY, "1 mo"], [YEAR, "1 y"], [10 * YEAR, "10 y"], [100 * YEAR, "100 y"]];
const nice = (n) => n.replace(/^([A-Z][a-z]?)(\d+)(m1)?$/, (_, el, a, m) => `${el}-${a}${m ? "m" : ""}`);

/* ---------- Step 2: atoms ---------- */
(() => {
  const cv = $("#atoms"), ctx = cv.getContext("2d");
  let W, H, atoms = [], neutrons = [], flashes = [], on = false, everOn = false, offAt = 0, last = performance.now(), clicks = 0;
  const KINDS = [
    { name: "Mn-56", color: "#0ca678", life: 4 }, { name: "Cr-51", color: "#74b816", life: 14 },
    { name: "Mn-54", color: "#e03131", life: 30 }, { name: "Co-60", color: "#5f3dc4", life: 80 },
  ];
  function resize() {
    const r = cv.getBoundingClientRect(), dpr = devicePixelRatio || 1;
    W = r.width; H = r.height; cv.width = W * dpr; cv.height = H * dpr; ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    atoms = [];
    const gap = Math.max(22, W / 34);
    for (let x = gap * 1.2; x < W - gap / 2; x += gap) for (let y = gap * 0.8; y < H - gap / 2; y += gap)
      atoms.push({ x: x + (Math.random() - .5) * 4, y: y + (Math.random() - .5) * 4, kind: null, age: 0 });
  }
  resize(); addEventListener("resize", resize);
  const status = $("#atoms-status");
  $("#beam").addEventListener("click", () => {
    on = !on; sound.tick();
    $("#beam").textContent = on ? "Turn the machine off" : "Turn the machine on";
    status.textContent = on ? "Machine on: neutrons streaming in" : "Machine off: cooling";
    if (on) everOn = true; else offAt = performance.now();
  });
  const style = getComputedStyle(document.documentElement);
  function frame(now) {
    const dt = Math.min(0.05, (now - last) / 1000); last = now;
    ctx.clearRect(0, 0, W, H);
    if (on && Math.random() < dt * 40) neutrons.push({ x: -6, y: Math.random() * H, vx: 260 + Math.random() * 160, vy: (Math.random() - .5) * 60 });
    neutrons = neutrons.filter((n) => {
      n.x += n.vx * dt; n.y += n.vy * dt;
      for (const a of atoms) {
        if (!a.kind && Math.abs(a.x - n.x) < 5 && Math.abs(a.y - n.y) < 5 && Math.random() < 0.18) {
          a.kind = KINDS[Math.random() < 0.45 ? 0 : Math.random() < 0.5 ? 1 : Math.random() < 0.6 ? 2 : 3];
          a.age = 0; return false;
        }
      }
      return n.x < W + 10;
    });
    clicks = Math.max(0, clicks - dt * 30);
    for (const a of atoms) {
      if (a.kind) {
        a.age += dt;
        if (Math.random() < dt * Math.LN2 / a.kind.life * 3) {
          flashes.push({ x: a.x, y: a.y, r: 3, c: a.kind.color });
          a.kind = null;
          if (clicks < 12) { sound.click(0.3); clicks++; }
        }
      }
      ctx.beginPath(); ctx.arc(a.x, a.y, a.kind ? 5.2 : 3.6, 0, Math.PI * 2);
      ctx.fillStyle = a.kind ? a.kind.color : style.getPropertyValue("--grid-strong");
      ctx.fill();
    }
    ctx.fillStyle = style.getPropertyValue("--mark");
    for (const n of neutrons) { ctx.beginPath(); ctx.arc(n.x, n.y, 2.2, 0, Math.PI * 2); ctx.fill(); }
    flashes = flashes.filter((f) => {
      f.r += dt * 60;
      ctx.beginPath(); ctx.arc(f.x, f.y, f.r, 0, Math.PI * 2);
      ctx.strokeStyle = f.c; ctx.globalAlpha = Math.max(0, 1 - f.r / 22); ctx.lineWidth = 2; ctx.stroke(); ctx.globalAlpha = 1;
      return f.r < 22;
    });
    if (!on && everOn && offAt && now - offAt > 3500 && !isDone("beam")) complete("beam");
    const live = atoms.filter((a) => a.kind).length;
    if (!on && everOn) status.textContent = live ? `Machine off: ${live} radioactive atoms left` : "Machine off: all decayed";
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
})();

/* ---------- Step 2: build-up of one nuclide ---------- */
(() => {
  const HL = { Cr51: 27.7025 * DAY, Mn54: 312.19 * DAY, Co60: 5.2711 * YEAR };
  const NAMES = { Cr51: "chromium-51", Mn54: "manganese-54", Co60: "cobalt-60" };
  let nuc = "Cr51";
  const seen = new Set(["Cr51"]);
  const host = $("#buildup");
  const tmax = 12 * YEAR;
  const yearTicks = [0, 2, 4, 6, 8, 10, 12].map((y) => [y * YEAR, y + " y"]);
  const p = plot(host, {
    height: 300, label: "Activity of one nuclide during and after irradiation",
    x: { domain: [0, tmax], ticks: yearTicks, title: "time since the machine started" },
    y: { domain: [0, 1.05], ticks: [0, 0.25, 0.5, 0.75, 1], fmt: (v) => Math.round(v * 100) + "%", title: "activity, % of ceiling" },
  });
  const phase = svg("rect", { class: "phase", y: p.m.t, height: p.H - p.m.t - p.m.b }, p.layer);
  const phaseLabel = svg("text", { class: "phase-label", y: p.m.t + 16 }, p.layer);
  svg("line", { x1: p.x(0), x2: p.x(tmax), y1: p.y(1), y2: p.y(1), class: "landline" }, p.layer);
  const curve = p.line([0], [0], { class: "ink" });
  const ts = Array.from({ length: 400 }, (_, i) => (i / 399) * tmax);
  const irr = $("#irr"), out = $("#irr-out"), cap = $("#buildup-cap");
  function draw() {
    const tIrr = (irr.value / 100) * 10 * YEAR, lam = Math.LN2 / HL[nuc];
    const ys = ts.map((t) => t <= tIrr ? 1 - Math.exp(-lam * t) : (1 - Math.exp(-lam * tIrr)) * Math.exp(-lam * (t - tIrr)));
    p.update(curve, ts, ys);
    phase.setAttribute("x", p.x(0)); phase.setAttribute("width", Math.max(0, p.x(tIrr) - p.x(0)));
    phaseLabel.setAttribute("x", p.x(0) + 8); phaseLabel.textContent = tIrr > 0.6 * YEAR ? "machine on" : "";
    out.textContent = durationText(Math.max(tIrr, 60));
    const frac = 1 - Math.exp(-lam * tIrr), halves = tIrr / HL[nuc];
    cap.textContent = tIrr <= 0 ? "Move the slider to start irradiating."
      : `After ${durationText(tIrr)}, ${NAMES[nuc]} has reached ${Math.round(frac * 100)}% of its ceiling: that's ${halves < 10 ? halves.toFixed(1) : Math.round(halves)} half-lives. After shutdown it halves every ${durationText(HL[nuc])}.`;
  }
  irr.addEventListener("input", () => { sound.tick(); draw(); });
  document.querySelectorAll("[data-nuc]").forEach((b) => b.addEventListener("click", () => {
    nuc = b.dataset.nuc; seen.add(nuc); sound.tick();
    document.querySelectorAll("[data-nuc]").forEach((x) => x.classList.toggle("on", x === b));
    draw();
    if (seen.size === 3) complete("halflife");
  }));
  draw();
})();

/* ---------- Step 3: the problem file ---------- */
(() => {
  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;");
  const val = (v) => typeof v === "string" ? `<span class="s">${esc(JSON.stringify(v))}</span>`
    : typeof v === "number" ? `<span class="n">${v}</span>` : `<span class="n">${JSON.stringify(v)}</span>`;
  function render(v, ind, key) {
    const pad = "  ".repeat(ind);
    if (key === "flux_per_group") {
      const nz = v.filter((x) => x > 0).length;
      return `[${v.slice(0, 3).map(val).join(", ")}, <span class="c">… ${v.length - 3} more numbers, ${nz} not zero …</span>]`;
    }
    if (key === "schedule") {
      const lines = v.slice(0, 2).map((s) => pad + "  " + render(s, ind + 1));
      lines.push(pad + `  <span class="c">… ${v.length - 4} more steps …</span>`);
      lines.push(...v.slice(7, 9).map((s) => pad + "  " + render(s, ind + 1)));
      return "[\n" + lines.join(",\n") + "\n" + pad + "]";
    }
    if (Array.isArray(v)) return v.length === 0 ? "[]" : "[" + v.map((x) => render(x, ind + 1)).join(", ") + "]";
    if (v && typeof v === "object") {
      const ks = Object.keys(v);
      if (ks.length <= 2 && ks.every((k) => typeof v[k] !== "object" || v[k] === null))
        return "{" + ks.map((k) => `<span class="k">"${k}"</span>: ${val(v[k])}`).join(", ") + "}";
      return "{\n" + ks.map((k) => `${pad}  <span class="k">"${k}"</span>: ${render(v[k], ind + 1, k)}`).join(",\n") + "\n" + pad + "}";
    }
    return val(v);
  }
  const PARTS = [
    { id: "about", keys: ["spec", "title", "projectile"], name: "What this is",
      text: `<p><code>spec</code> says this is an ACTINV problem, in version 1 of the format. <code>title</code> is a free-text label for people, and it's copied into the result. <code>projectile</code> says what hits the material: neutrons.</p>` },
    { id: "data", keys: ["library", "decay", "fission_yields"], name: "Nuclear data",
      text: `<p>These lines say which <strong>nuclear data</strong> to use. That's the physics measured and evaluated over decades: how likely each neutron reaction is at each energy, and how each nuclide decays. ACTINV calculates. The data says what can happen.</p><p><code>catalog:tendl-2025-neutron-709g</code> names a standard library that <code>actinv data fetch</code> installs, and the long <code>sha256</code> code is its fingerprint. If the file on disk isn't exactly that one, ACTINV refuses to run rather than quietly using the wrong data.</p>` },
    { id: "material", keys: ["material"], name: "Material",
      text: `<p>20,000 grams of steel, with each element's share by weight (<code>wt_percent</code>): 66.9% iron, 17% chromium, 12% nickel, and so on.</p><p>Notice what's <em>not</em> on the list: no cobalt. Hold that thought until step 7.</p>` },
    { id: "spectrum", keys: ["spectrum"], name: "Neutron spectrum",
      text: `<p>Not every neutron has the same energy. Neutrons are born at 14.1 MeV in the plasma, then slow down as they bounce off everything in the machine. The <strong>spectrum</strong> says how many neutrons there are in each of 709 energy bands (<code>fispact-709</code>). <code>total</code> is the flux: 10¹⁰ neutrons per cm² per second at the bracket.</p><p>This one is made up for teaching: a mix of ACTINV's built-in fast, slowing-down and slow shapes. In a real design you'd take the spectrum from a neutron transport code, and ACTINV can read those outputs directly.</p>` },
    { id: "schedule", keys: ["schedule"], name: "Irradiation & cooling",
      text: `<p>A list of time steps. <code>"flux": 1.0</code> means the machine is on at full flux, and <code>0.0</code> means it's off and the bracket is cooling. Durations are in seconds here, but you can also write <code>"2 y"</code> or <code>"30 d"</code>.</p><p>This schedule has 7 steps of irradiation adding up to two years, then 34 cooling steps from one minute to 100 years. ACTINV reports the bracket's state at the end of each step.</p>` },
    { id: "outputs", keys: ["options", "photon", "summary", "uncertainty", "radiological", "self_shielding"], name: "What to compute",
      text: `<p>Activity, decay heat and every nuclide's amount are always computed. These blocks ask for extras.</p><p><code>photon</code> asks for the gamma rays and the dose rate 1 m from the bracket (<code>point_source</code>), using a table of how photons deposit energy in air (<code>photon-response.json</code>, the second file you download). <code>summary</code> adds totals in Bq and W and the five nuclides that dominate each step. <code>null</code> means "not asked for".</p>` },
  ];
  const pre = $("#file"), chips = $("#chips"), explain = $("#explain");
  const seen = new Set(store.get("file-seen", []));
  pre.innerHTML = "{\n" + PARTS.map((part) =>
    `<span class="blk" data-part="${part.id}">` + part.keys.filter((k) => k in problem)
      .map((k) => `  <span class="k">"${k}"</span>: ${render(problem[k], 1, k)}`).join(",\n") + "</span>").join(",\n") + "\n}";
  PARTS.forEach((part) => {
    const c = document.createElement("button");
    c.type = "button"; c.className = "chip" + (seen.has(part.id) ? " seen" : ""); c.textContent = part.name;
    c.setAttribute("role", "tab"); c.dataset.part = part.id;
    c.addEventListener("click", () => select(part.id));
    chips.append(c);
  });
  pre.querySelectorAll(".blk").forEach((b) => b.addEventListener("click", () => select(b.dataset.part)));
  function select(id) {
    const part = PARTS.find((p) => p.id === id);
    seen.add(id); store.set("file-seen", [...seen]); sound.tick();
    chips.querySelectorAll(".chip").forEach((c) => { c.classList.toggle("on", c.dataset.part === id); c.classList.toggle("seen", seen.has(c.dataset.part)); c.setAttribute("aria-selected", c.dataset.part === id); });
    pre.querySelectorAll(".blk").forEach((b) => b.classList.toggle("on", b.dataset.part === id));
    const blk = pre.querySelector(`.blk[data-part="${id}"]`);
    pre.scrollTo({ top: blk.offsetTop - 20, behavior: reducedMotion ? "auto" : "smooth" });
    explain.innerHTML = `<h4>${part.name}</h4>${part.text}`;
    if (seen.size === PARTS.length) complete("file");
  }
  explain.innerHTML = `<h4>Click a part</h4><p>Use the buttons above or click inside the file. Each part answers one question ACTINV needs answered.</p>`;
})();

/* ---------- Step 4: the guess ---------- */
const truth1d = data.runs[0].dose_Gy_h[13];
const guess = (() => {
  const host = $("#guessplot");
  const p = plot(host, {
    height: 170, margin: { l: 24, r: 24, t: 54, b: 40 }, label: "Drag to guess the dose rate",
    x: { domain: [1e-8, 10], log: true, narrow: doseNarrow, ticks: [[1e-7, "0.1 µSv/h"], [1e-5, "10 µSv/h"], [1e-3, "1 mSv/h"], [1e-1, "100 mSv/h"], [10, "10 Sv/h"]] },
    y: { domain: [0, 1], ticks: [] },
  });
  for (let e = -8; e <= 1; e++) for (let k = 1; k < 10; k++) {
    const v = k * 10 ** e; if (v > 10) break;
    svg("line", { x1: p.x(v), x2: p.x(v), y1: p.m.t, y2: p.H - p.m.b, class: k === 1 ? "gmajor" : "gminor" }, p.layer);
  }
  const LANDS = [[2e-7, "natural background"], [3e-6, "flying at cruising altitude"], [LIMIT, "this lesson's access limit"], [1, "an hour here can make you ill"]];
  const narrow = p.W < 600;
  LANDS.forEach(([v, label], i) => {
    svg("line", { x1: p.x(v), x2: p.x(v), y1: p.m.t - 4, y2: p.H - p.m.b, class: v === LIMIT ? "limit" : "landline" }, p.layer);
    svg("text", { x: p.x(v), y: p.m.t - 10 - (narrow ? 0 : (i % 2) * 16), "text-anchor": "middle", class: v === LIMIT ? "limit-label" : "landmark" }, p.layer).textContent = narrow ? "abcd"[i] : label;
  });
  if (narrow) {
    const cap = document.createElement("p"); cap.className = "cap";
    cap.innerHTML = LANDS.map(([v, label], i) => `<b>${"abcd"[i]}</b> ${label}, ${doseText(v)}`).join(" · ");
    host.append(cap);
  }
  let g = store.get("guess", 1e-5), locked = store.get("guess-locked", false);
  const cy = (p.m.t + p.H - p.m.b) / 2;
  const marker = svg("circle", { cx: p.x(g), cy, r: 12, class: "guess", tabindex: 0, role: "slider", "aria-label": "Your guess", "aria-valuetext": doseText(g) }, p.top);
  const out = $("#guess-out");
  function set(v) {
    g = Math.min(10, Math.max(1e-8, v));
    marker.setAttribute("cx", p.x(g)); marker.setAttribute("aria-valuetext", doseText(g));
    out.textContent = doseText(g); store.set("guess", g);
  }
  set(g);
  let drag = false, lastTick = 0;
  const toVal = (e) => { const r = p.root.getBoundingClientRect(); return p.x.invert((e.clientX - r.left) * p.W / r.width); };
  p.root.addEventListener("pointerdown", (e) => { if (locked) return; drag = true; p.root.setPointerCapture(e.pointerId); set(toVal(e)); });
  p.root.addEventListener("pointermove", (e) => {
    if (!drag) return; set(toVal(e));
    if (performance.now() - lastTick > 60) { sound.tick(); lastTick = performance.now(); }
  });
  p.root.addEventListener("pointerup", () => (drag = false));
  marker.addEventListener("keydown", (e) => {
    if (locked) return;
    if (e.key === "ArrowRight" || e.key === "ArrowUp") { set(g * 1.26); e.preventDefault(); sound.tick(); }
    if (e.key === "ArrowLeft" || e.key === "ArrowDown") { set(g / 1.26); e.preventDefault(); sound.tick(); }
  });
  const lock = $("#lock");
  function setLocked() {
    locked = true; marker.classList.add("locked"); lock.disabled = true; lock.textContent = "Guess locked in";
  }
  if (locked) setLocked();
  lock.addEventListener("click", () => { setLocked(); store.set("guess-locked", true); complete("guess"); toast("Guess saved. Now run the bracket."); });
  return { get value() { return g; } };
})();

/* ---------- Step 5: three doors ---------- */
(() => {
  const tabs = [...document.querySelectorAll('.tabs [role="tab"]')];
  tabs.forEach((t) => t.addEventListener("click", () => {
    sound.tick();
    tabs.forEach((x) => { x.setAttribute("aria-selected", x === t); document.getElementById(x.getAttribute("aria-controls")).hidden = x !== t; });
  }));
})();

function ran(how) {
  if (complete("ran", { quiet: true })) { sound.reveal(); toast(`Run complete (${how}). Step 6 is unlocked.`); }
  unlockRead();
}

/* ---- the practice terminal ---- */
(() => {
  const screen = $("#screen");
  const HOME = "/home/you";
  const fs = { "bracket-lesson": { "bracket.json": "file", "photon-response.json": "file" } };
  const state = store.get("term", { cwd: "~", fetched: false, ranOut: false });
  if (state.fetched) fs["bracket-lesson"]["actinv-data"] = "dir";
  if (state.ranOut) fs["bracket-lesson"]["result.json"] = "file";
  let lastExit = 0, busy = false;
  const hist = []; let hi = 0;
  const print = (html, cls = "") => { const d = document.createElement("div"); if (cls) d.className = cls; d.innerHTML = html; screen.insertBefore(d, inputLine); screen.scrollTop = screen.scrollHeight; return d; };
  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;");
  const inputLine = document.createElement("div");
  inputLine.className = "term-input";
  inputLine.innerHTML = `<span class="prompt"></span><input type="text" aria-label="Type a command" autocomplete="off" autocapitalize="off" spellcheck="false">`;
  screen.append(inputLine);
  const input = inputLine.querySelector("input"), promptEl = inputLine.querySelector(".prompt");
  const promptText = () => `you@laptop:${state.cwd}$`;
  promptEl.textContent = promptText();
  screen.addEventListener("click", () => input.focus({ preventScroll: true }));
  print(`<span class="dim">This is a practice terminal. Type a command and press Enter, or click one of the suggestions below.
Type <b>help</b> to see what it understands.</span>`);
  const here = () => state.cwd === "~" ? null : fs["bracket-lesson"];
  const save = () => store.set("term", state);

  const OUT = {
    bare: `ACTINV — activation and inventory

Start here
  data fetch               install the standard nuclear data (about 186 MiB)
  new problem.json         write a problem to edit (iron, or a library material)
  run problem.json [OUT]   solve it; the result goes to OUT or stdout
  report result.json       read a result as tables or a report page

Ask a design question
  impurities   which impurities matter, and how much of each is allowed
  budget       element limits that keep the clearance index under 1
  optimize     search a composition range for the best material
  decide       certify a design against its constraints
  study        build and run a set of cases

Waste
  waste        classify a component, then budget, bounds, package, uncertainty

Move between codes
  import-fispact  import-flux  export-openmc  export-mcnp  export-source
  export-openmc-mesh  export-r2s  export-r2s-joint  export-r2s-cov

Check and inspect
  validate  doctor  inspect  data list|verify|manifest  clearance

Build nuclear data
  build-library  build-damage  build-shielding  build-covariance

Advanced
  mesh  reverse  reverse-qualified  unfold  assimilate  twin  maintenance
  surrogate  eval-spread

'actinv help COMMAND' for details and an example; 'actinv help exit-codes'.`,
    helpRun: `actinv run: solve a problem

usage: actinv run PROBLEM.json [OUT.json]

Solves the irradiation and cooling history in PROBLEM.json. The result holds
inventory, activity and decay heat at every step, plus the optional outputs
the problem requests: photons and dose, pathways, tables, uncertainty.

actinv run problem.json result.json
actinv run problem.json | jq '.steps[-1].heat_W_per_g.total'

Writes the result to OUT.json, or to stdout without it or for \`-\`; progress
goes to stderr. PROBLEM.json may be \`-\` to read stdin. Relative paths in the
problem are read from the current directory.
The first run with a data release prepares a private cache of a few hundred
MiB under ~/.cache/actinv, or $ACTINV_CACHE_DIR.

Exit: 0 done, 1 the run failed, 2 bad usage or an invalid problem.
Next: actinv report result.json`,
    dominant: `Dominant nuclides
step  time_s     quantity    rank  nuclide  value       unit      percent    cumulative_percent  list_total  n_for_90_percent  n_for_99_percent
----  ---------  ----------  ----  -------  ----------  --------  ---------  ------------------  ----------  ----------------  ----------------
14    6.32016e7  activity    1     Cr51     4.12801e6   Bq_per_g  4.18364e1  4.18364e1           9.86703e6   4                 11
14    6.32016e7  activity    2     Fe55     2.99347e6   Bq_per_g  3.03381e1  7.21744e1           9.86703e6   4                 11
14    6.32016e7  activity    3     Co57     1.03893e6   Bq_per_g  1.05294e1  8.27038e1           9.86703e6   4                 11
14    6.32016e7  activity    4     Co58     7.31712e5   Bq_per_g  7.41573e0  9.01195e1           9.86703e6   4                 11
14    6.32016e7  activity    5     Mn54     4.48637e5   Bq_per_g  4.54683e0  9.46663e1           9.86703e6   4                 11
14    6.32016e7  heat        1     Co58     1.18262e-7  W_per_g   4.18193e1  4.18193e1           2.82793e-7  7                 11
14    6.32016e7  heat        2     Mn54     6.03912e-8  W_per_g   2.13553e1  6.31746e1           2.82793e-7  7                 11
14    6.32016e7  heat        3     Cr51     2.42592e-8  W_per_g   8.57841e0  7.17530e1           2.82793e-7  7                 11
14    6.32016e7  heat        4     Co57     2.39370e-8  W_per_g   8.46449e0  8.02174e1           2.82793e-7  7                 11
14    6.32016e7  heat        5     Fe59     1.21950e-8  W_per_g   4.31233e0  8.45298e1           2.82793e-7  7                 11
14    6.32016e7  beta_heat   1     Mo99     7.65164e-9  W_per_g   3.16190e1  3.16190e1           2.41995e-8  8                 14
14    6.32016e7  beta_heat   2     Co58     3.97955e-9  W_per_g   1.64448e1  4.80638e1           2.41995e-8  8                 14
14    6.32016e7  beta_heat   3     Co57     3.10632e-9  W_per_g   1.28363e1  6.09001e1           2.41995e-8  8                 14
14    6.32016e7  beta_heat   4     Cr51     2.54853e-9  W_per_g   1.05313e1  7.14315e1           2.41995e-8  8                 14
14    6.32016e7  beta_heat   5     Fe55     2.01461e-9  W_per_g   8.32502e0  7.97565e1           2.41995e-8  8                 14
14    6.32016e7  gamma_heat  1     Co58     1.14283e-7  W_per_g   4.41939e1  4.41939e1           2.58594e-7  6                 10
14    6.32016e7  gamma_heat  2     Mn54     6.00900e-8  W_per_g   2.32372e1  6.74311e1           2.58594e-7  6                 10
14    6.32016e7  gamma_heat  3     Cr51     2.17106e-8  W_per_g   8.39565e0  7.58267e1           2.58594e-7  6                 10
14    6.32016e7  gamma_heat  4     Co57     2.08307e-8  W_per_g   8.05537e0  8.38821e1           2.58594e-7  6                 10
14    6.32016e7  gamma_heat  5     Co60     1.11157e-8  W_per_g   4.29853e0  8.81806e1           2.58594e-7  6                 10`,
  };
  const FETCH = [
    "Installing tendl-2025-neutron: 185.5 MiB to download, 276.2 MiB on disk, into actinv-data/v1.2.0",
    "fetching tendl-2025-neutron-709g",
    ...[13.5, 27.1, 40.6, 54.2, 67.7, 81.3, 94.8].map((p) => `  download tendl-2025-neutron-709g: ${p}% (${Math.round(123897743 * p / 100)}/123897743 bytes)`),
    "  download tendl-2025-neutron-709g: verified ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44",
    "fetching tendl-2025-neutron-709g-index",
    "  download tendl-2025-neutron-709g-index: verified 8bd19b4001c246758e739cd0067a0087e1ce5c2157438dae97bd52e1d3beb3fb",
    "fetching endfb-viii-0-decay",
    "  endfb-viii-0-decay: download request failed: http status: 403; trying ACTINV's copy https://github.com/AvilaLabs/ACTINV/releases/download/decay-sources-v1/endf-b-viii-0_decay.sublib.zip",
    "  download endfb-viii-0-decay: verified 9f0254ea1314a233d957e507779eb53e6c7fafd17100fd5da23ce80ee80dd8ae",
    "  extract endfb-viii-0-decay: verified 6f04cf009086c179021f243a58dadc2d5bb078de5ba39c4fe46ccad77d228ddb",
    "fetching jeff-3-3-decay",
    "  jeff-3-3-decay: download request failed: http status: 403; trying ACTINV's copy https://github.com/AvilaLabs/ACTINV/releases/download/decay-sources-v1/jeff-3-3_decay.sublib.zip",
    "  download jeff-3-3-decay: verified 73d52f142a284e2f463a8afcac5a9422c00de7bbbdee7e33196d825bea5edd6d",
    "  extract jeff-3-3-decay: verified 850b8b7f85f8d88b6ad826c4cd341aaaffabd525c8ecf3c588a0ad437bf5d123",
    "fetching tendl-2025-neutron-damage",
    "  download tendl-2025-neutron-damage: verified c9f1b2f2d60511f9da1b6c961b4a2bc4920d11604d4db32325565a265eed1069",
    "fetching actinv-data-notice-v1-2",
    "  download actinv-data-notice-v1-2: verified 417e15cf9043ad9ecbdc56980042ab539afe04a017ffc6965919ee24213ac169",
    "Installed and verified 6 files in actinv-data/v1.2.0.",
    "Next: actinv new problem.json",
  ];
  const SUBS = ["data", "new", "run", "report", "impurities", "budget", "optimize", "decide", "study", "waste", "import-fispact", "import-flux",
    "export-openmc", "export-mcnp", "export-source", "export-openmc-mesh", "export-r2s", "validate", "doctor", "inspect", "clearance",
    "mesh", "reverse", "unfold", "assimilate", "twin", "maintenance", "surrogate", "eval-spread", "help"];
  const lev = (a, b) => { const d = Array.from({ length: a.length + 1 }, (_, i) => [i, ...Array(b.length).fill(0)]); for (let j = 1; j <= b.length; j++) d[0][j] = j; for (let i = 1; i <= a.length; i++) for (let j = 1; j <= b.length; j++) d[i][j] = Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1)); return d[a.length][b.length]; };
  const wait = (ms) => new Promise((r) => setTimeout(r, reducedMotion ? 0 : ms));

  async function exec(line) {
    const words = line.trim().split(/\s+/).filter(Boolean);
    if (!words.length) return 0;
    const [cmd, ...args] = words;
    const dir = here();
    if (cmd === "help") { print(`<span class="hint">This practice terminal understands:
  ls, cd FOLDER, cd .., pwd, cat FILE, clear, echo $?
  actinv, actinv --version, actinv help run
  actinv data fetch, actinv validate FILE, actinv run FILE OUT, actinv report ...
On your own computer, every command ACTINV has works the same way.</span>`); return 0; }
    if (cmd === "clear") { [...screen.children].forEach((c) => c !== inputLine && c.remove()); return 0; }
    if (cmd === "pwd") { print(state.cwd === "~" ? HOME : HOME + "/bracket-lesson"); return 0; }
    if (cmd === "echo") { print(args.join(" ") === "$?" ? String(lastExit) : esc(args.join(" "))); return 0; }
    if (cmd === "ls" || cmd === "dir") {
      const names = dir ? Object.keys(dir).sort() : Object.keys(fs);
      print(names.map((n) => (dir ? dir[n] : "dir") === "dir" ? `<span style="color:#9bb7ff">${n}/</span>` : n).join("   "));
      if (!dir) print(`<span class="hint">↳ The lesson's files are in the folder bracket-lesson. Go into it with: cd bracket-lesson</span>`);
      complete("t-ls"); return 0;
    }
    if (cmd === "cd") {
      const to = (args[0] || "~").replace(/\/$/, "");
      if (to === "~" || to === ".." || to === HOME) { state.cwd = "~"; save(); return 0; }
      if (to === "bracket-lesson" && state.cwd === "~") { state.cwd = "~/bracket-lesson"; save(); return 0; }
      if (to === "." ) return 0;
      print(`cd: ${esc(to)}: No such file or directory`, "err"); return 1;
    }
    if (cmd === "cat" || cmd === "type" || cmd === "less") {
      const f = args[0];
      if (!dir || !(f in dir)) { print(`${cmd}: ${esc(f || "")}: No such file or directory`, "err"); return 1; }
      if (f === "bracket.json") { print(esc(JSON.stringify(problem, null, 1).split("\n").slice(0, 22).join("\n")) + `\n<span class="dim">… (the practice terminal stops here: the spectrum alone is 709 numbers)</span>`); return 0; }
      print(`<span class="dim">(${f} is long; the practice terminal doesn't print it)</span>`); return 0;
    }
    if (cmd === "python" || cmd === "python3" || cmd === "pip") { print(`<span class="hint">↳ The practice terminal doesn't run Python. The Python tab above shows ACTINV in Python.</span>`); return 0; }
    if (cmd !== "actinv") {
      print(`${esc(cmd)}: command not found`, "err");
      if (lev(cmd, "actinv") <= 2) print(`<span class="hint">↳ Did you mean actinv?</span>`);
      return 127;
    }
    // actinv
    const sub = args[0];
    if (!sub || sub === "--help" || sub === "-h" || (sub === "help" && !args[1])) { print(esc(OUT.bare)); return 0; }
    if (sub === "--version" || sub === "-V") { print(data.actinv_version); complete("t-version"); return 0; }
    if (sub === "help" && args[1] === "run") { print(esc(OUT.helpRun)); return 0; }
    if (sub === "help") { print(`<span class="hint">↳ The practice terminal has the help for run. On your computer, every command has its help: actinv help ${esc(args[1])}</span>`); return 0; }
    if (!SUBS.includes(sub)) {
      const best = SUBS.map((s) => [lev(sub, s), s]).sort((a, b) => a[0] - b[0])[0];
      print(`actinv: unknown command '${esc(sub)}'.${best[0] <= 2 ? ` Did you mean '${best[1]}'?` : ""} See 'actinv help'.`, "err"); return 2;
    }
    const needLesson = () => { if (!dir) { print(`<span class="hint">↳ You're in your home folder. The lesson's files are in bracket-lesson: cd bracket-lesson</span>`); return true; } return false; };
    if (sub === "data" && args[1] === "fetch") {
      if (needLesson()) return 1;
      busy = true;
      for (const [i, l] of FETCH.entries()) {
        const cls = l.includes("403") ? "dim" : "";
        print(esc(l), cls);
        if (l.includes("403")) print(`<span class="hint">↳ That's fine: one data site blocks automatic downloads, so ACTINV fetches its own verified copy instead.</span>`);
        if (i % 3 === 0) sound.key();
        await wait(l.includes("%") ? 220 : 120);
      }
      print(`<span class="dim">(It also prints a short JSON record of what it installed, for scripts. The practice terminal skips it.)</span>`);
      dir["actinv-data"] = "dir"; state.fetched = true; save(); busy = false;
      complete("t-fetch"); return 0;
    }
    if (sub === "validate") {
      const f = args[1];
      if (!f) { print("usage: actinv validate PROBLEM.json [--hashes]", "err"); return 2; }
      if (needLesson()) return 2;
      if (!(f in dir) || dir[f] !== "file") { print(`cannot read ${esc(f)}: No such file or directory (os error 2)`, "err"); return 2; }
      if (f !== "bracket.json") { print(`<span class="hint">↳ Try it on the problem: actinv validate bracket.json</span>`); return 2; }
      print("ok (--schema): actinv-spec-1 — 709 groups, 41 steps"); complete("t-validate"); return 0;
    }
    if (sub === "run") {
      const [f, out] = args.slice(1);
      if (!f) { print("usage: actinv run PROBLEM.json [OUT.json]", "err"); return 2; }
      if (needLesson()) return 2;
      if (!(f in dir) || dir[f] !== "file") { print(`cannot read ${esc(f)}: No such file or directory (os error 2)`, "err"); return 2; }
      if (f !== "bracket.json") { print(`<span class="hint">↳ Run the problem: actinv run bracket.json result.json</span>`); return 2; }
      if (!state.fetched) {
        print(`catalog artifact 'endfb-viii-0-decay' is not installed at ${HOME}/bracket-lesson/actinv-data/v1.2.0/decay/endf-b-viii-0_decay.dat — run \`actinv data fetch\` or set ACTINV_DATA_DIR`, "err");
        print(`<span class="hint">↳ ACTINV needs its nuclear data first. Run: actinv data fetch</span>`); return 2;
      }
      busy = true;
      const spin = print(`<span class="dim">solving 41 steps…</span>`);
      for (let i = 0; i < 12; i++) { sound.key(); await wait(110); }
      spin.remove();
      if (!out || out === "-") {
        print(esc(`{\n  "spec_title": "${problem.title}",\n  "entry_point": "cli",\n  "mode": "coupled",\n  "pruned_states": 318,\n  "total_states": 3873,\n  "steps": [\n    {\n      "step": 1,\n      "t_s": 86400.0,`) + `\n<span class="dim">… (the practice terminal stops here: the full result is about 80 MB of JSON)</span>`);
        print(`<span class="hint">↳ With no output file, the result goes to the screen, so another program can read it. Usually you want a file: actinv run bracket.json result.json</span>`);
        busy = false; return 0;
      }
      print(`41 steps, 318 of 3873 states, 1006.1 ms -> ${esc(out)}`);
      print(`<span class="hint">↳ Done. (On your computer it takes a second or two, longer the very first time while ACTINV prepares its cache.) Step 6 is unlocked. Try reading it here too: actinv report ${esc(out)} --tables dominant --format text --step 14</span>`);
      dir[out] = "file"; if (out === "result.json") state.ranOut = true; save(); busy = false;
      complete("t-run"); ran("command line"); return 0;
    }
    if (sub === "report") {
      const f = args[1];
      if (needLesson()) return 2;
      if (!f || !(f in dir)) { print(`cannot read ${esc(f || "")}: No such file or directory (os error 2)`, "err"); return 2; }
      if (line.includes("dominant") && line.includes("--step 14")) { print(esc(OUT.dominant)); print(`<span class="hint">↳ Step 14 is one day after shutdown. Notice that the nuclide with the most activity (Cr-51) isn't the one making the most heat (Co-58).</span>`); return 0; }
      print(`<span class="hint">↳ The practice terminal has one report recorded: actinv report result.json --tables dominant --format text --step 14</span>`); return 0;
    }
    print(`<span class="hint">↳ That command is real, and its help explains it (actinv help ${esc(sub)}), but this chapter doesn't use it.</span>`); return 0;
  }

  async function submit(line) {
    if (busy) return;
    print(`<span class="prompt">${esc(promptText())}</span> <span class="cmd">${esc(line)}</span>`);
    if (line.trim()) { hist.push(line); hi = hist.length; }
    lastExit = await exec(line);
    promptEl.textContent = promptText();
    updateCue();
    screen.scrollTop = screen.scrollHeight;
  }
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { const v = input.value; input.value = ""; submit(v); }
    else if (e.key === "ArrowUp") { if (hi > 0) input.value = hist[--hi]; e.preventDefault(); }
    else if (e.key === "ArrowDown") { input.value = hi < hist.length - 1 ? hist[++hi] : (hi = hist.length, ""); e.preventDefault(); }
    else if (e.key.length === 1) sound.key();
  });
  const cue = $("#cue");
  function next() {
    if (!isDone("t-version")) return "actinv --version";
    if (state.cwd === "~") return "cd bracket-lesson";
    if (!isDone("t-ls")) return "ls";
    if (!state.fetched) return "actinv data fetch";
    if (!isDone("t-validate")) return "actinv validate bracket.json";
    if (!isDone("t-run")) return "actinv run bracket.json result.json";
    return "actinv report result.json --tables dominant --format text --step 14";
  }
  function updateCue() {
    const n = next();
    cue.innerHTML = `Next, try: <code tabindex="0" role="button">${esc(n)}</code> <span>· click it to type it for you</span>`;
    const c = cue.querySelector("code");
    const go = async () => {
      if (busy) return;
      input.value = "";
      for (const ch of n) { input.value += ch; sound.key(); await wait(22); }
      await wait(120); input.value = ""; submit(n);
    };
    c.addEventListener("click", go);
    c.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  }
  updateCue();
})();

$("#app-replay").addEventListener("click", () => { complete("app"); ran("desktop app replay"); $("#read").scrollIntoView({ behavior: reducedMotion ? "auto" : "smooth" }); });

/* ---- Python ---- */
(() => {
  const hl = (code) => code.replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/(#.*)$/gm, '<span class="c">$1</span>');
  const PY1 = `from actinv import Problem, solve

problem = Problem.from_file("bracket.json")   # read the problem file
result = solve(problem)                       # run it

one_day = result.steps[13]                    # step 14: one day after shutdown
dose = one_day["photon_source"]["point_source_dose"]["gamma_air_dose_Sv_h"]
print(f"Dose rate 1 m away, one day after shutdown: {dose * 1000:.2f} mSv/h")`;
  const PY2 = `from actinv import Problem, solve

problem = Problem.from_file("bracket.json")
steel = problem["material"]["composition"]

for cobalt in [0.0, 0.05, 0.1, 0.2]:          # weight percent
    steel["Co"], steel["Fe"] = cobalt, 66.9 - cobalt
    result = solve(problem)
    ten_years = result.steps[34]["photon_source"]["point_source_dose"]
    print(f"Co {cobalt:4.2f}%  ->  {ten_years['gamma_air_dose_Sv_h'] * 1e6:6.2f} µSv/h after 10 years")`;
  // Printed by these two scripts with the actinv 1.7.2 Python package (build/README.md).
  const OUT1 = "Dose rate 1 m away, one day after shutdown: 3.97 mSv/h";
  const OUT2 = ["Co 0.00%  ->   45.83 µSv/h after 10 years", "Co 0.05%  ->  229.07 µSv/h after 10 years",
    "Co 0.10%  ->  412.31 µSv/h after 10 years", "Co 0.20%  ->  778.79 µSv/h after 10 years"].join("\n");
  $("#py1").innerHTML = hl(PY1);
  $("#py2").innerHTML = hl(PY2);
  async function play(btn, status, outEl, text, then) {
    btn.disabled = true; status.textContent = "running…";
    for (let i = 0; i < 8; i++) { sound.key(); await new Promise((res) => setTimeout(res, reducedMotion ? 0 : 120)); }
    outEl.hidden = false; outEl.textContent = "";
    for (const l of text.split("\n")) { outEl.textContent += (outEl.textContent ? "\n" : "") + l; sound.tick(); await new Promise((res) => setTimeout(res, reducedMotion ? 0 : 260)); }
    status.textContent = "done (replayed)"; btn.disabled = false; then && then();
  }
  $("#py-run").addEventListener("click", () => play($("#py-run"), $("#py-status"), $("#py1-out"), OUT1, () => { complete("python"); ran("Python"); }));
  $("#py2-run").addEventListener("click", () => play($("#py2-run"), $("#py2-status"), $("#py2-out"), OUT2));
})();

/* ---------- Step 6: read the answer ---------- */
const T = data.t_s, SH = data.shutdown_s;
const COOL0 = data.irradiation_steps - 1; // index of the shutdown step (end of irradiation)
const after = (i) => T[i] - SH;
function accessTime(run) {
  const d = run.dose_Gy_h;
  for (let i = COOL0 + 1; i < T.length; i++) if (d[i] < LIMIT) {
    if (d[i - 1] < LIMIT || i - 1 <= COOL0) return after(i);
    const t0 = Math.log(after(i - 1)), t1 = Math.log(after(i)), y0 = Math.log(d[i - 1]), y1 = Math.log(d[i]), yl = Math.log(LIMIT);
    return Math.exp(t0 + (yl - y0) / (y1 - y0) * (t1 - t0));
  }
  return Infinity;
}
const roughly = (s) => {
  if (s < 2 * DAY) return durationText(s);
  if (s < 60 * DAY) return `about ${Math.round(s / (7 * DAY))} weeks`;
  if (s < 2 * YEAR) return `about ${Math.round(s / (30.44 * DAY))} months`;
  return `about ${+(s / YEAR).toPrecision(2)} years`;
};
let readBuilt = false;
function unlockRead() {
  if (readBuilt) return; readBuilt = true;
  $("#locked-read").hidden = true; $("#read-body").hidden = false;
  meter.show();
  buildReveal(); buildCooling(); buildCobalt();
}
function buildReveal() {
  const g = guess.value;
  const host = $("#revealplot");
  const p = plot(host, {
    height: 150, margin: { l: 24, r: 24, t: 40, b: 40 }, label: "Your guess against ACTINV",
    x: { domain: [1e-8, 10], log: true, narrow: doseNarrow, ticks: [[1e-7, "0.1 µSv/h"], [1e-5, "10 µSv/h"], [1e-3, "1 mSv/h"], [1e-1, "100 mSv/h"], [10, "10 Sv/h"]] },
    y: { domain: [0, 1], ticks: [] },
  });
  const cy = (p.m.t + p.H - p.m.b) / 2;
  svg("line", { x1: p.x(LIMIT), x2: p.x(LIMIT), y1: p.m.t, y2: p.H - p.m.b, class: "limit" }, p.layer);
  svg("circle", { cx: p.x(g), cy, r: 10, class: "guess locked" }, p.top);
  const close = Math.abs(p.x(g) - p.x(truth1d)) < 110;
  svg("text", { x: p.x(g), y: p.m.t - (close ? 26 : 10), "text-anchor": "middle", class: "landmark", style: "fill:var(--mark)" }, p.top).textContent = "your guess";
  const dot = svg("circle", { cx: p.x(1e-8), cy, r: 10, fill: "var(--ink)" }, p.top);
  const lab = svg("text", { x: p.x(truth1d), y: p.m.t - 10, "text-anchor": "middle", class: "landmark", style: "fill:var(--ink);font-weight:700" }, p.top);
  lab.textContent = `ACTINV: ${doseText(truth1d)}`;
  if (!reducedMotion) {
    dot.animate([{ cx: p.x(1e-8) }, { cx: p.x(truth1d) }], { duration: 1400, easing: "cubic-bezier(.2,.9,.3,1.1)", fill: "forwards" });
  }
  dot.setAttribute("cx", p.x(truth1d));
  const factor = Math.max(g / truth1d, truth1d / g);
  const v = $("#verdict");
  v.innerHTML = factor < 3 ? `<b>Within a factor of ${factor.toFixed(1)}. Excellent.</b> That's a better guess than most experts make without a calculation.`
    : factor < 30 ? `<b>Off by a factor of ${Math.round(factor)}.</b> Respectable: activation numbers span so many powers of ten that being within a couple is a good instinct.`
    : `<b>Off by a factor of ${sci(factor, 1)}.</b> Completely normal for a first guess, which is exactly why we calculate. You were ${g > truth1d ? "too high" : "too low"}.`;
  v.innerHTML += ` ACTINV says <b>${doseText(truth1d)}</b> one metre away, one day after shutdown.`;
}

function shares(container, byMap, total, i, kind) {
  const rows = Object.entries(byMap).map(([n, v]) => [n, v[i] / total]).filter(([, s]) => s > 0.03).sort((a, b) => b[1] - a[1]).slice(0, 4);
  container.innerHTML = rows.map(([n, s]) => `<div class="share"><span>${nice(n)}</span><span class="bar"><i style="width:${Math.min(100, s * 100)}%;background:${COLORS[n] || "#868e96"}"></i></span><small>${Math.round(s * 100)}%</small></div>`).join("")
    || `<div class="share"><small>—</small></div>`;
}

function liveWhenVisible(el) {
  new IntersectionObserver((es) => es.forEach((e) => { el._vis = e.isIntersecting; meter.live([...document.querySelectorAll("[data-meter]")].some((x) => x._vis)); }), { threshold: 0.15 }).observe(el);
  el.dataset.meter = "";
}

function buildCooling() {
  const run = data.runs[0];
  const host = $("#coolplot");
  const idx = Array.from({ length: T.length - COOL0 - 1 }, (_, k) => COOL0 + 1 + k);
  const p = plot(host, {
    height: 340, margin: { l: 100 }, label: "Dose rate after shutdown",
    x: { domain: [60, 100 * YEAR], log: true, ticks: timeTicks, narrow: timeNarrow, title: "time after shutdown" },
    y: { domain: [1e-11, 1e-1], log: true, every: 2, fmt: doseText, title: "dose rate at 1 m" },
  });
  svg("line", { x1: p.x(60), x2: p.x(100 * YEAR), y1: p.y(LIMIT), y2: p.y(LIMIT), class: "limit" }, p.layer);
  svg("text", { x: p.x(100 * YEAR) - 4, y: p.y(LIMIT) - 6, "text-anchor": "end", class: "limit-label" }, p.layer).textContent = "access limit 3 mSv/h";
  p.line(idx.map(after), idx.map((i) => run.dose_Gy_h[i]), { class: "ink" }, { animate: true });
  const cursor = svg("line", { class: "cursor", y1: p.m.t, y2: p.H - p.m.b }, p.top);
  const dot = svg("circle", { r: 6, fill: "var(--ink)", stroke: "var(--sheet)", "stroke-width": 2 }, p.top);
  const slider = $("#cool");
  slider.max = idx.length - 1;
  slider.value = 6; // one day
  const acc = accessTime(run);
  function draw() {
    const i = idx[+slider.value], t = after(i), d = run.dose_Gy_h[i];
    cursor.setAttribute("x1", p.x(t)); cursor.setAttribute("x2", p.x(t));
    dot.setAttribute("cx", p.x(t)); dot.setAttribute("cy", p.y(d));
    $("#cool-out").textContent = durationText(t);
    $("#s-act").textContent = bqText(run.activity_Bq[i]);
    $("#s-heat").textContent = wattText(run.heat_W[i]);
    $("#s-dose").textContent = doseText(d);
    $("#s-dose").style.color = d >= LIMIT ? "#c92a2a" : "";
    shares($("#shares"), run.dose_by, d, i);
    meter.set(d, `${durationText(t)} after shutdown`);
    if (d < LIMIT && run.dose_Gy_h[idx[+slider.value - 1] ?? i] >= LIMIT && complete("access"))
      toast(`Under the limit from ${roughly(acc)} after shutdown.`);
    if (t >= 10 * YEAR * 0.99 && complete("handoff"))
      toast("Cobalt-60, though the steel has no cobalt: it's made from the nickel. Step 7 shows why that matters.");
  }
  slider.addEventListener("input", () => { sound.tick(); draw(); });
  draw();
  liveWhenVisible($("#read"));
}

function buildCobalt() {
  const opts = document.querySelectorAll("#cobalt-guess .opt");
  const why = $("#cobalt-why");
  const reveal = (a) => {
    opts.forEach((o) => { o.disabled = true; o.classList.toggle("right", o.dataset.a === "years"); if (o.dataset.a === a && a !== "years") o.classList.add("wrong"); });
    why.hidden = false;
    why.innerHTML = (a === "years" ? "<b>Right.</b> " : "<b>Not quite.</b> ") +
      "Right after shutdown the bracket is dominated by short-lived nuclides made from its main ingredients, and a trace of cobalt barely shows. Weeks and years later, cobalt-60 is what's left. Slide the cobalt and see.";
    $("#cobalt-body").hidden = false;
    complete("cobalt-guess", { quiet: a !== "years" });
    if (a !== "years") sound.wrong();
    drawCo();
  };
  opts.forEach((o) => o.addEventListener("click", () => { store.set("cobalt-guess", o.dataset.a); reveal(o.dataset.a); }));
  const host = $("#coplot");
  const idx = Array.from({ length: T.length - COOL0 - 1 }, (_, k) => COOL0 + 1 + k);
  const p = plot(host, {
    height: 340, margin: { l: 100 }, label: "Dose rate after shutdown, with and without cobalt",
    x: { domain: [60, 100 * YEAR], log: true, ticks: timeTicks, narrow: timeNarrow, title: "time after shutdown" },
    y: { domain: [1e-11, 1e-1], log: true, every: 2, fmt: doseText, title: "dose rate at 1 m" },
  });
  svg("line", { x1: p.x(60), x2: p.x(100 * YEAR), y1: p.y(LIMIT), y2: p.y(LIMIT), class: "limit" }, p.layer);
  p.line(idx.map(after), idx.map((i) => data.runs[0].dose_Gy_h[i]), { class: "ink thin" });
  const cur = p.line(idx.map(after), idx.map((i) => data.runs[0].dose_Gy_h[i]), { class: "ink", style: "stroke:var(--mark)" });
  const accLine = svg("line", { class: "cursor", y1: p.m.t, y2: p.H - p.m.b, style: "stroke:var(--mark);stroke-dasharray:3 3;opacity:.9" }, p.top);
  const legend = document.createElement("div"); legend.className = "legend";
  legend.innerHTML = `<span><i style="background:var(--ink);opacity:.55"></i>no cobalt</span><span><i style="background:var(--mark)"></i>with the cobalt you choose</span><span><i style="background:#c92a2a"></i>access limit</span>`;
  host.append(legend);
  const slider = $("#co");
  slider.max = data.runs.length - 1;
  const base10 = data.runs[0].dose_Gy_h[34];
  function drawCo() {
    const run = data.runs[+slider.value];
    p.update(cur, idx.map(after), idx.map((i) => run.dose_Gy_h[i]));
    const acc = accessTime(run);
    accLine.setAttribute("x1", p.x(acc)); accLine.setAttribute("x2", p.x(acc));
    $("#co-out").textContent = run.cobalt_wt_percent ? `${run.cobalt_wt_percent}%` : "none";
    $("#c-access").textContent = roughly(acc);
    $("#c-10y").textContent = doseText(run.dose_Gy_h[34]);
    const x = run.dose_Gy_h[34] / base10;
    $("#c-10y-x").textContent = x > 1.05 ? ` ${x.toFixed(x < 10 ? 1 : 0)}× the cobalt-free bracket` : "";
    $("#c-10a").textContent = bqText(run.activity_Bq[34]);
    meter.set(run.dose_Gy_h[34], `10 years after shutdown · Co ${run.cobalt_wt_percent}%`);
    if (run.cobalt_wt_percent === 0.1 && complete("cobalt"))
      toast(`0.1% cobalt: the crew waits ${roughly(acc)} instead of ${roughly(accessTime(data.runs[0]))}.`);
  }
  slider.addEventListener("input", () => { sound.tick(); drawCo(); });
  const prior = store.get("cobalt-guess", null);
  if (prior) reveal(prior);
  liveWhenVisible($("#cobalt"));
}

/* ---------- Step 8: quiz and finish ---------- */
(() => {
  const r0 = data.runs[0];
  const QS = [
    { q: "One day after shutdown, chromium-51 has the most activity in the bracket, yet it gives only a small part of the dose. How can that be?",
      a: [["Some decays give off energetic gamma rays and some give off almost none: activity counts decays, dose counts what reaches you", true],
          ["Chromium-51's activity is wrong in the calculation", false], ["Dose only counts nuclides with long half-lives", false]],
      why: "Activity is decays per second, whatever they emit. Cr-51 emits a weak gamma ray in only about one decay in ten, while Co-58 and Mn-54 emit energetic gamma rays almost every time." },
    { q: "Why does a part's dose rate drop quickly in the first days after shutdown, and then much more slowly?",
      a: [["Short-lived nuclides decay away first, leaving the long-lived ones", true], ["The neutrons take days to leave the steel", false], ["The steel cools down, and cold steel is less radioactive", false]],
      why: "Each nuclide fades at its own half-life. Manganese-56 (2.6 hours) is gone within a day. Cobalt-60 (5.3 years) is still there decades later. Temperature has no effect on radioactive decay." },
    { q: "You need a bracket that can be handled soon after shutdown and disposed of easily. What does this chapter suggest?",
      a: [["Limit trace impurities like cobalt in the steel you buy", true], ["Use a thicker bracket", false], ["Nothing can be done: steel is steel", false]],
      why: `Going from no cobalt to 0.1% stretched the wait from ${roughly(accessTime(r0))} to ${roughly(accessTime(data.runs[4]))} and raised the 10-year dose about ${Math.round(data.runs[4].dose_Gy_h[34] / r0.dose_Gy_h[34])} times. Low-activation materials are specified by their impurities, and actinv impurities tells you which ones to limit.` },
  ];
  const host = $("#quiz");
  const answered = new Set(store.get("quiz", []));
  QS.forEach((q, qi) => {
    const box = document.createElement("div"); box.className = "q";
    box.innerHTML = `<p>${qi + 1}. ${q.q}</p><div class="opts"></div><p class="why" hidden></p>`;
    const opts = box.querySelector(".opts"), why = box.querySelector(".why");
    const order = q.a.map((x, i) => [x, i]).sort((a, b) => ((a[1] * 7 + qi * 3) % 5) - ((b[1] * 7 + qi * 3) % 5));
    order.forEach(([[text, right]]) => {
      const b = document.createElement("button"); b.type = "button"; b.className = "opt"; b.textContent = text;
      b.addEventListener("click", () => {
        if (right) {
          b.classList.add("right"); why.hidden = false; why.textContent = q.why;
          opts.querySelectorAll(".opt").forEach((o) => (o.disabled = true));
          answered.add(qi); store.set("quiz", [...answered]); sound.done();
          if (answered.size === QS.length) complete("quiz");
        } else { b.classList.add("wrong"); sound.wrong(); setTimeout(() => b.classList.remove("wrong"), 400); }
      });
      if (answered.has(qi) && right) { b.classList.add("right"); why.hidden = false; why.textContent = q.why; }
      if (answered.has(qi)) b.disabled = true;
      opts.append(b);
    });
    host.append(box);
  });
})();

const ALL = ["welcome", "beam", "halflife", "file", "guess", "ran", "access", "handoff", "cobalt-guess", "cobalt", "quiz"];
const wasComplete = ALL.every(isDone);
onProgress((set) => {
  const n = ALL.filter((t) => set.has(t)).length;
  $("#ring").style.strokeDashoffset = 339 * (1 - n / ALL.length);
  if (n === ALL.length) {
    $("#finish").classList.add("earned");
    $("#finish-title").textContent = "Chapter 1 complete";
    $("#finish-text").textContent = "You made a steel bracket radioactive, predicted its dose, ran ACTINV and found the impurity that decides when people can work near it. Take the problem home and run it yourself.";
    if (!wasComplete) sound.fanfare();
  }
});
if (isDone("ran")) unlockRead();
