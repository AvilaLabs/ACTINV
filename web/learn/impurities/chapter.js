import { boot, $, sound, complete, isDone, store, key } from "../kit.js";
import { onProgress, doseText } from "../learn.js";

const STEPS = { quotes: ["c3-order", "c3-mills"], when: ["c3-map"], spec: ["c3-spec", "c3-lean"], yours: ["c3-app"] };
const data = await fetch("data/impurities-data.json").then((r) => r.json());

boot({
  steps: STEPS, version: data.actinv_version,
  terms: {
    trace: ["Trace element", "An element in the steel that nobody added on purpose, at hundredths or thousandths of a percent. It comes with the scrap and ore the mill melts."],
    cert: ["Mill certificate", "The document a steel mill ships with each batch (each \"heat\"), listing the composition it measured. Buyers check it against the purchase specification."],
  },
});

const EL = ["Co", "Cu", "N", "Nb", "Ta", "B", "Ag"];
const NAME = { Co: "cobalt", Cu: "copper", N: "nitrogen", Nb: "niobium", Ta: "tantalum", B: "boron", Ag: "silver" };
const NUC = (n) => n.replace(/^([A-Z][a-z]?)(\d+)(m1)?$/, (_, e, a, m) => `${e}-${a}${m ? "m" : ""}`);
const T10 = data.times.indexOf("10 years");
const dose = (i) => data.screen[i]["dose.contact_proxy"];
const LIMIT = data.limit_Gy_h, MATRIX = dose(T10).matrix_only;
const G = Object.fromEntries(EL.map((e) => [e, dose(T10).impurities[e].gradient]));
const RANGE = data.impurities;
// wt% -> dose rate at ten years: the steel's own plus each trace element's amount times its effect.
const at10 = (comp) => MATRIX + EL.reduce((s, e) => s + G[e] * (comp[e] || 0), 0);
const fmt = (x) => (x >= 0.01 ? x.toFixed(x >= 0.1 ? 2 : 3) : x.toPrecision(1)).replace(/\.?0+$/, "") + "%";
const status = (t) => { $("#status").textContent = t; $("#status").classList.add("fresh"); };
$("#limit-text").textContent = doseText(LIMIT);

/* ---------- Three quotes ---------- */
const MILLS = [
  { id: "A", name: "Mill A", kind: "Commercial grade", price: 4.10, comp: { Co: 0.15, Cu: 0.30, N: 0.06, Nb: 0.01, Ta: 0.01, B: 0.002, Ag: 0.001 } },
  { id: "B", name: "Mill B", kind: "Low-cobalt scrap", price: 4.60, comp: { Co: 0.03, Cu: 0.25, N: 0.05, Nb: 0.04, Ta: 0.02, B: 0.002, Ag: 0.0015 } },
  { id: "C", name: "Mill C", kind: "Nuclear grade", price: 7.90, comp: { Co: 0.01, Cu: 0.03, N: 0.03, Nb: 0.002, Ta: 0.002, B: 0.0005, Ag: 0.0002 } },
];
const TONNES = 2;
const cost = (m) => `${(m.price * 1000 * TONNES).toLocaleString("en", { maximumFractionDigits: 0 })}`;
const tried = new Set(store.get(key("mills"), []));
let chosen = store.get(key("chosen"), null);
let spec = store.get(key("spec"), {});

const certs = $("#certs");
MILLS.forEach((m) => {
  const card = document.createElement("article");
  card.className = "cert"; card.dataset.m = m.id;
  card.innerHTML = `<h4>${m.name}</h4><div class="kind">Certificate · ${m.kind}</div>
    <table>${EL.map((e) => `<tr><td>${e} <span style="color:#777">${NAME[e]}</span></td><td data-e="${e}">${fmt(m.comp[e])}</td></tr>`).join("")}</table>
    <div class="price"><span>per kg</span><b>${m.price.toFixed(2)}</b></div>
    <button type="button" class="btn">Buy 2 t for ${cost(m)}</button><span class="stamp" hidden></span>`;
  card.querySelector("button").addEventListener("click", () => buy(m));
  certs.append(card);
});

function buy(m, quiet = false) {
  chosen = m.id; store.set(key("chosen"), chosen);
  tried.add(m.id); store.set(key("mills"), [...tried]);
  certs.querySelectorAll(".cert").forEach((c) => c.classList.toggle("chosen", c.dataset.m === m.id));
  const d = at10(m.comp), ok = d <= LIMIT;
  const stamp = certs.querySelector(`[data-m="${m.id}"] .stamp`);
  stamp.hidden = false; stamp.className = `stamp ${ok ? "pass" : "fail"}`; stamp.textContent = ok ? "meets rule" : "fails rule";
  // the gauge: the steel's own dose, then each element's share, against the rule
  const top = Math.max(LIMIT * 1.6, d * 1.1), pct = (x) => `${(100 * x / top).toFixed(2)}%`;
  let left = MATRIX;
  const segs = [`<div class="seg matrix" style="left:0;width:${pct(MATRIX)}" title="the steel itself"></div>`];
  EL.filter((e) => G[e] * m.comp[e] > 0).forEach((e) => {
    const w = G[e] * m.comp[e];
    segs.push(`<div class="seg imp" style="left:${pct(left)};width:${pct(w)};opacity:${e === "Co" ? 1 : 0.6}" title="${NAME[e]}"></div>`); left += w;
  });
  $("#track").innerHTML = segs.join("") + `<div class="limit" style="left:${pct(LIMIT)}"><span>rule ${doseText(LIMIT)}</span></div>`;
  $("#axis-end").textContent = doseText(top);
  $("#out-title").textContent = `${m.name}'s steel, ten years after shutdown: ${doseText(d)} at the surface. ${ok ? "It meets the rule." : `That's ${(d / LIMIT).toFixed(1)} times the rule.`}`;
  const others = EL.filter((e) => e !== "Co").reduce((t, e) => t + Math.max(0, G[e] * m.comp[e]), 0);
  const rows = [["the steel itself", MATRIX], ["cobalt", G.Co * m.comp.Co], ["the other six", others]];
  $("#parts").innerHTML = rows.map(([n, x]) => `<span>${n}</span><span class="bar" style="height:6px;align-self:center;background:var(--btn)"><i style="display:block;height:100%;width:${Math.max(0, 100 * x / d).toFixed(1)}%;background:${n === "the steel itself" ? "var(--weak)" : "var(--warn)"}"></i></span><span>${x <= 0 ? "none" : doseText(x)}</span>`).join("");
  const co = G.Co * m.comp.Co;
  $("#out-cap").textContent = m.id === "C"
    ? `Everything is low, so it passes easily. But you paid ${cost(MILLS[2])} for steel when ${cost(MILLS[1])} would also have passed: only one of these lines mattered.`
    : `Cobalt alone adds ${doseText(co)}, ${Math.round(100 * co / d)}% of the total. Every other trace element together adds ${doseText(Math.max(0, d - MATRIX - co))}.`;
  $("#outcome").hidden = false;
  if (!quiet) { ok ? sound.done() : sound.wrong(); status(`${m.name}: ${ok ? "meets" : "fails"} the rule.`); }
  complete("c3-order", { quiet });
  if (tried.size === MILLS.length) complete("c3-mills");
  po();
}

/* ---------- Which impurity, when ---------- */
(() => {
  const host = $("#heat"), out = $("#heat-out");
  const seen = new Set(store.get(key("cells"), []));
  const add = (e, i) => { const x = dose(i); return x.impurities[e].gradient * RANGE[e].max / x.matrix_only; };
  const shade = (f) => (f <= 0.01 ? 0 : Math.min(1, Math.log10(f * 100) / 3.3));
  const label = (f) => (f <= 0 ? "none" : f < 0.01 ? "<1%" : f < 1 ? `+${Math.round(100 * f)}%` : `×${(1 + f).toFixed(f < 9 ? 1 : 0)}`);
  host.innerHTML = `<thead><tr><th>each at its highest</th>${data.times.map((t, i) => `<th class="${i === T10 ? "rule" : ""}">${t}${i === T10 ? " · your rule" : ""}</th>`).join("")}</tr></thead>
    <tbody>${EL.map((e) => `<tr><td>${NAME[e]} <span class="note" style="font-size:11px">≤ ${fmt(RANGE[e].max)}</span></td>${data.times.map((t, i) => {
      const f = add(e, i);
      return `<td data-e="${e}" data-i="${i}" class="${i === T10 ? "rulecol" : ""}" style="background:rgb(214 84 0 / ${(0.08 + 0.75 * shade(f)).toFixed(2)})" tabindex="0" role="button">${label(f)}</td>`;
    }).join("")}</tr>`).join("")}</tbody>`;
  const HL = { Co60: "5.3 years", Co58: "71 days", Ta182: "115 days", Ag110m1: "250 days", Ag108m1: "438 years", Nb94: "20,000 years", Nb92m1: "10 days", Mn54: "312 days", Fe59: "45 days", Hf181: "42 days" };
  host.querySelectorAll("td[data-e]").forEach((td) => {
    const go = () => {
      const e = td.dataset.e, i = +td.dataset.i, x = dose(i), imp = x.impurities[e], f = add(e, i);
      host.querySelectorAll("td.on").forEach((c) => c.classList.remove("on")); td.classList.add("on");
      const nuc = imp.top[0], via = `${NUC(nuc)}${HL[nuc] ? ` (half-life ${HL[nuc]})` : ""}`;
      out.textContent = imp.gradient <= 0
        ? `${NAME[e]} at ${data.times[i]}: it adds nothing. It barely activates, and since it replaces iron, slightly less iron is there to make ${NUC(imp.top[0])}.`
        : `${NAME[e]} at ${data.times[i]}: ${fmt(RANGE[e].max)} of it ${f < 0.01 ? "adds under 1% to" : f < 1 ? `adds ${Math.round(100 * f)}% to` : `multiplies by ${(1 + f).toFixed(f < 9 ? 1 : 0)}`} the steel's own ${doseText(x.matrix_only)}, mostly through ${via}.`
          + (e === "Ta" && i === 0 ? " Tantalum matters for handling in the first months, not at ten years." : "")
          + (e === "Nb" && i === 3 ? " After a century, niobium-94 is one of the last things left: chapter 5 meets it again." : "")
          + (e === "Co" && i === T10 ? " This is the cell your rule falls in." : "");
      sound.tick();
      seen.add(`${e}${i}`); store.set(key("cells"), [...seen]);
      if (seen.size >= 3 && [...seen].some((s) => !s.endsWith(String(T10)))) complete("c3-map");
    };
    td.addEventListener("click", go);
    td.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); go(); } });
  });
})();

/* ---------- Write the specification ---------- */
const host = $("#lines");
EL.forEach((e) => {
  const line = document.createElement("div"); line.className = "line";
  line.innerHTML = `<label for="max-${e}">${NAME[e]} (${e})</label><span>≤ <input id="max-${e}" inputmode="decimal" placeholder="no limit" value="${spec[e] ?? ""}"> wt%</span><span class="eff" data-eff="${e}"></span>`;
  line.querySelector("input").addEventListener("input", (ev) => {
    const v = ev.target.value.trim();
    if (v === "") delete spec[e]; else spec[e] = v;
    store.set(key("spec"), spec); check(true);
  });
  host.append(line);
});
const limits = () => Object.fromEntries(EL.filter((e) => spec[e] !== undefined && !Number.isNaN(parseFloat(spec[e]))).map((e) => [e, Math.max(0, parseFloat(spec[e]))]));
function check(live) {
  const lim = limits();
  // the worst steel a mill could ship and still meet the spec: each element at its limit, or at the top of its usual range
  const worst = Object.fromEntries(EL.map((e) => [e, G[e] > 0 ? (lim[e] ?? RANGE[e].max) : 0]));
  const d = at10(worst), ok = d <= LIMIT, n = Object.keys(lim).length;
  EL.forEach((e) => {
    const c = G[e] * worst[e];
    document.querySelector(`[data-eff="${e}"]`).textContent = c <= 0 ? "adds nothing" : c < 1e-6 ? "adds almost nothing" : `worst case adds ${doseText(c)}`;
  });
  $("#verdict").className = `verdict ${ok ? "ok" : "no"}`;
  $("#verdict").innerHTML = `Worst steel this spec allows: <b>${doseText(d)}</b> at ten years, ${ok ? "within" : "over"} the ${doseText(LIMIT)} rule. ${n} line${n === 1 ? "" : "s"}.`;
  const bid = MILLS.map((m) => [m, EL.every((e) => lim[e] === undefined || m.comp[e] <= lim[e])]);
  $("#bidders").innerHTML = bid.map(([m, can]) => `<span class="${can ? "in" : "out"}">${m.name} ${m.price.toFixed(2)}/kg</span>`).join("");
  const cheapest = bid.filter(([, can]) => can).map(([m]) => m).sort((a, b) => a.price - b.price)[0];
  $("#sheet-cap").textContent = !ok ? "A mill could meet every line of this spec and still ship steel that breaks the rule. Tighten the line that matters."
    : cheapest ? `Every steel that meets this spec meets the rule. Cheapest mill that can bid: ${cheapest.name}, ${cost(cheapest)} for the order${cheapest.id === "C" ? ", which is more than you need to pay" : ""}.`
    : "Every steel that meets this spec meets the rule, but none of the three mills can supply it. Loosen a line.";
  if (ok && live) sound.done();
  if (ok) complete("c3-spec", { quiet: !live });
  if (ok && n === 1) complete("c3-lean", { quiet: !live });
  po();
}
const coLimit = data.limits.Co.others_at_worst.wt_pct;
$("#answer").addEventListener("click", () => {
  $("#answer-out").hidden = false; sound.reveal();
  $("#answer-out").innerHTML = `<code>actinv impurities</code> solves for it: with every other trace element at the top of its range, cobalt can be at most <strong>${coLimit.toFixed(4)} wt%</strong>. Round down and write <em>Co ≤ 0.033%</em>, one line, and Mill B can bid. No other element needs a line: tantalum, silver, niobium, boron and nitrogen can't reach the rule however much a mill ships, and copper would need 44%.`;
});
check(false);

/* ---------- Purchase order in the side panel ---------- */
function po() {
  const m = MILLS.find((x) => x.id === chosen), lim = limits();
  $("#po").innerHTML = `<div><span>Mill</span><b>${m ? m.name : "—"}</b></div>
    <div><span>Ten years on</span><b>${m ? doseText(at10(m.comp)) : "—"}</b></div>
    <div><span>Spec lines</span><b>${Object.keys(lim).map((e) => `${e} ≤ ${lim[e]}`).join(", ") || "none"}</b></div>`;
}
po();
if (chosen) buy(MILLS.find((m) => m.id === chosen), true);
$("#app-done").addEventListener("click", () => complete("c3-app"));

const ALL = Object.values(STEPS).flat();
const was = ALL.every(isDone);
onProgress((set) => {
  if (ALL.every((t) => set.has(t))) {
    $("#finish-title").innerHTML = '<svg class="done-mark" viewBox="0 0 16 16" aria-hidden="true"><path d="M2.5 8.5l3.5 3.5L13.5 4"/></svg>Chapter 3 complete';
    $("#finish-text").textContent = "You found the one trace element your rule depends on, and bought steel with a one-line specification instead of paying for nuclear grade.";
    if (!was) sound.fanfare();
  }
});
