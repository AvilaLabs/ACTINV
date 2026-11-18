import { boot, replay, python, esc, $, sound, complete, isDone, store, key } from "../kit.js";
import { onProgress, bqText, doseText } from "../learn.js";

const STEPS = { evidence: ["c2-evidence"], moves: ["c2-moves"], cases: ["c2-cases"], elsewhere: ["c2-field"], verdict: ["c2-verdict"], own: ["c2-python"] };
const data = await fetch("data/routes-data.json").then((r) => r.json());
const problem = await fetch("files/routes.json").then((r) => r.json());

boot({
  steps: STEPS, version: data.actinv_version,
  terms: {
    route: ["Route", "One chain from an atom you started with to a nuclide you care about: Co-59 captures a neutron and becomes Co-60, for example. A nuclide is usually made by several routes at once."],
    chart: ["Chart of nuclides", "A map of every known nucleus: one row per element (number of protons), one column per number of neutrons. Reactions move an atom around it in fixed steps."],
    isomer: ["Isomer", "A nucleus in a long-lived excited state. It has the same protons and neutrons as the ordinary one, but extra energy, which it gives off later. Written with an m: Co-60m."],
  },
});

/* ---------- The chart: names, positions, reactions ---------- */
const ZOF = { Cr: 24, Mn: 25, Fe: 26, Co: 27, Ni: 28 };
const ELOF = Object.fromEntries(Object.entries(ZOF).map(([e, z]) => [z, e]));
const NAME = { Cr: "chromium", Mn: "manganese", Fe: "iron", Co: "cobalt", Ni: "nickel", Mo: "molybdenum" };
const ABUND = { Cr50: 4.35, Cr52: 83.79, Cr53: 9.5, Cr54: 2.37, Mn55: 100, Fe54: 5.85, Fe56: 91.75, Fe57: 2.12, Fe58: 0.28, Co59: 100, Ni58: 68.08, Ni60: 26.22, Ni61: 1.14, Ni62: 3.64 };
const HL = {
  Cr49: "42 minutes", Cr51: "27.7 days", Mn52: "5.6 days", Mn53: "3.7 million years", Mn54: "312 days", Mn56: "2.6 hours", Mn57: "1.4 minutes",
  Fe53: "8.5 minutes", Fe55: "2.7 years", Fe59: "44.5 days", Co56: "77 days", Co57: "272 days", Co58: "70.9 days", Co60: "5.27 years",
  Co61: "1.6 hours", Co62: "1.5 minutes", Ni57: "36 hours", Ni59: "76,000 years", Ni63: "101 years", Mn58: "3 seconds", Cr55: "3.5 minutes", Fe61: "6 minutes",
};
const MOVES = [
  { id: "ng", label: "(n,γ)", say: "capture", dz: 0, dn: 1, kind: "slow", text: "the neutron stays: one column right" },
  { id: "np", label: "(n,p)", say: "(n,p)", dz: -1, dn: 1, kind: "fast", text: "a proton leaves: one row down, one right" },
  { id: "n2n", label: "(n,2n)", say: "(n,2n)", dz: 0, dn: -1, kind: "fast", text: "a second neutron leaves: one column left" },
  { id: "na", label: "(n,α)", say: "(n,α)", dz: -2, dn: -1, kind: "fast", text: "an alpha leaves: two rows down, one left" },
];
const EDGE = {
  "(n,g)": ["(n,γ)", "slow"], "(n,p)": ["(n,p)", "fast"], "(n,2n)": ["(n,2n)", "fast"], "(n,a)/(n,nh)/(n,pt)": ["(n,α)", "fast"],
  "(n,d)/(n,np)": ["(n,np)", "fast"], "(n,p)/b+/EC": ["decay", "decay"], IT: ["settles", "decay"],
};
const ground = (n) => n.replace(/m1$/, "");
const parse = (n) => { const m = ground(n).match(/^([A-Z][a-z]?)(\d+)$/); const z = ZOF[m[1]], a = +m[2]; return { el: m[1], z, a, n: a - z }; };
const NICE = (n) => n.replace(/^([A-Z][a-z]?)(\d+)(m1)?$/, (_, e, a, m) => `${e}-${a}${m ? "m" : ""}`);
const nameAt = (z, n) => `${ELOF[z]}${z + n}`;
const comp = data.composition_wt_percent;
const N0 = 26, N1 = 34, CW = 50, CH = 40, SX = 84, SY = 64, LX = 62, TY = 4;
const cx = (n) => LX + (n - N0) * SX + CW / 2, cy = (z) => TY + (28 - z) * SY + CH / 2;
const NS = "http://www.w3.org/2000/svg";
const el = (tag, attrs, parent) => { const e = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); parent && parent.append(e); return e; };

/* A route's segments on the board: isomers fold into their ground state, so Co59 -> Co60m -> Co60 is one arrow. */
function segments(target) {
  const seg = new Map();
  for (const r of target.routes) {
    for (let i = 0; i < r.edges.length; i++) {
      const a = ground(r.nuclides[i]), b = ground(r.nuclides[i + 1]);
      if (a === b) continue;
      const [label, kind] = EDGE[r.edges[i]] || [r.edges[i], "fast"];
      const k = `${a}>${b}>${label}`;
      const s = seg.get(k) || { from: a, to: b, label, kind, share: 0 };
      s.share += r.fraction; seg.set(k, s);
    }
  }
  return [...seg.values()];
}
const parents = (target) => {
  const p = {};
  for (const r of target.routes) { const a = r.nuclides[0]; p[a] = (p[a] || 0) + r.fraction; }
  return Object.entries(p).sort((x, y) => y[1] - x[1]);
};

/* One board: stable squares you can pick, marked squares, and arrows that keep their identity so widths animate. */
function board(svgEl, onPick) {
  svgEl.setAttribute("viewBox", `0 0 ${LX + (N1 - N0 + 1) * SX} ${TY + 5 * SY + 16}`);
  const slots = el("g", {}, svgEl), arrowLayer = el("g", {}, svgEl), cellLayer = el("g", {}, svgEl), labelLayer = el("g", {}, svgEl);
  const cells = {};
  for (let z = 28; z >= 24; z--) {
    const lab = el("text", { x: 4, y: cy(z) + 2, class: "zl" }, svgEl);
    lab.textContent = ELOF[z];
    const t = el("tspan", { x: 4, dy: 14 }, lab); t.textContent = `${comp[ELOF[z]]}% `;
    for (let n = N0; n <= N1; n++) {
      const name = nameAt(z, n), x = cx(n) - CW / 2, y = cy(z) - CH / 2;
      el("rect", { x, y, width: CW, height: CH, rx: 2, class: "slot" }, slots);
      if (name in ABUND) {
        const g = el("g", { class: "stable", tabindex: 0, role: "button", "aria-label": `${NICE(name)}, stable, ${ABUND[name]}% of ${NAME[ELOF[z]]}` }, cellLayer);
        el("rect", { x, y, width: CW, height: CH, rx: 2 }, g);
        el("text", { x: x + 5, y: y + 17 }, g).textContent = NICE(name);
        el("text", { x: x + 5, y: y + 32, class: "ab" }, g).textContent = `${ABUND[name]}%`;
        const go = () => onPick && onPick(name, g);
        g.addEventListener("click", go);
        g.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } });
        cells[name] = g;
      }
    }
  }
  for (let n = N0; n <= N1; n++) el("text", { x: cx(n) - 6, y: TY + 5 * SY + 10, class: "nl" }, svgEl).textContent = n;
  const marks = new Map(), arrows = new Map();
  return {
    cells,
    pick(name) { Object.entries(cells).forEach(([k, g]) => g.classList.toggle("on", k === name)); },
    flag(name, cls) { const g = cells[name]; if (!g) return; g.classList.remove(cls); void g.getBBox(); g.classList.add(cls); },
    unflag(cls) { Object.values(cells).forEach((g) => g.classList.remove(cls)); },
    clearMarks() { marks.forEach((g) => g.remove()); marks.clear(); },
    mark(name, cls = "target", sub = "") {
      const { z, n } = parse(name);
      if (n < N0 || n > N1 || z < 24 || z > 28 || cells[name]) return;
      marks.get(name)?.remove();
      const x = cx(n) - CW / 2, y = cy(z) - CH / 2, g = el("g", { class: cls }, cellLayer);
      el("rect", { x, y, width: CW, height: CH, rx: 2 }, g);
      el("text", { x: x + 5, y: y + 17 }, g).textContent = NICE(name);
      if (sub) el("text", { x: x + 5, y: y + 32, class: "ab", style: "font-size:9px;fill:var(--weak)" }, g).textContent = sub;
      marks.set(name, g);
    },
    // segs: [{from,to,label,kind,share}]; keyed, so a new set changes the old arrows' widths instead of redrawing them.
    arrows(segs, { draw = false, labels = false } = {}) {
      const keep = new Set();
      segs.forEach((s) => {
        const k = `${s.from}>${s.to}>${s.label}`; keep.add(k);
        const a = parse(s.from), b = parse(s.to);
        let x1 = cx(a.n), y1 = cy(a.z), x2 = cx(b.n), y2 = cy(b.z);
        const dx = x2 - x1, dy = y2 - y1, len = Math.hypot(dx, dy), ux = dx / len, uy = dy / len;
        const edge = (u, v) => Math.min(u ? (CW / 2) / Math.abs(u) : 1e9, v ? (CH / 2) / Math.abs(v) : 1e9);
        const e = edge(ux, uy);
        x1 += ux * (e + 3); y1 += uy * (e + 3); x2 -= ux * (e + 3); y2 -= uy * (e + 3);
        const w = s.kind === "decay" ? 2 : 2 + 9 * s.share;
        const hx = x2 - ux * (7 + w * 0.6), hy = y2 - uy * (7 + w * 0.6), px = -uy * (4 + w * 0.6), py = ux * (4 + w * 0.6);
        let item = arrows.get(k);
        if (!item) {
          const g = el("g", {}, arrowLayer);
          item = { g, line: el("path", { class: `arrow ${s.kind}${draw ? " draw" : ""}` }, g), head: el("path", { class: `arrow ${s.kind}`, style: "stroke-width:1.5" }, g),
            text: el("text", { class: "pct" }, labelLayer) };
          arrows.set(k, item);
        }
        item.line.setAttribute("d", `M${x1},${y1} L${hx},${hy}`);
        item.line.style.strokeWidth = w; item.line.style.opacity = s.share > 0 ? 1 : 0;
        item.head.setAttribute("d", `M${x2},${y2} L${hx + px},${hy + py} L${hx - px},${hy - py} Z`);
        item.head.style.fill = getComputedStyle(item.line).stroke; item.head.style.opacity = s.share > 0 ? 1 : 0;
        const mx = (x1 + x2) / 2 + (Math.abs(uy) > 0.3 ? 10 : 0), my = (y1 + y2) / 2 + (Math.abs(uy) > 0.3 ? 0 : -8);
        item.text.setAttribute("x", mx); item.text.setAttribute("y", my);
        item.text.textContent = labels && s.share >= 0.005 && s.kind !== "decay" ? `${s.label} ${Math.round(s.share * 100)}%` : "";
        item.text.style.opacity = s.share > 0 ? 1 : 0;
        item.g.querySelector("title")?.remove(); el("title", {}, item.g).textContent = `${NICE(s.from)} → ${NICE(s.to)}, ${s.label}${s.kind === "decay" ? "" : `: ${Math.round(s.share * 100)}%`}`;
      });
      arrows.forEach((item, k) => {
        if (keep.has(k)) return;
        item.line.style.strokeWidth = 0; item.line.style.opacity = 0; item.head.style.opacity = 0; item.text.style.opacity = 0;
      });
    },
    clearArrows() { arrows.forEach((i) => { i.g.remove(); i.text.remove(); }); arrows.clear(); },
  };
}
const status = (t) => { $("#status").textContent = t; $("#status").classList.add("fresh"); };

/* ---------- The evidence ---------- */
const CASES = ["Co58", "Co60", "Mn54", "Co57", "Cr51", "Fe59", "Mn56", "Fe55"];
const base = data.cases.cobalt, total = base.total_dose_Gy_h;
const doseShare = (t, c = base) => c.targets[t].dose_Gy_h / c.total_dose_Gy_h;
$("#total-dose").textContent = doseText(total);
$("#strip-end").textContent = doseText(total);
const traced = CASES.reduce((s, t) => s + doseShare(t), 0);
const strip = $("#strip");
CASES.filter((t) => doseShare(t) >= 0.005).forEach((t) => {
  const b = document.createElement("button");
  b.type = "button"; b.dataset.t = t; b.style.flex = `${doseShare(t)} 1 0`;
  b.innerHTML = `${NICE(t)}<small>${Math.round(doseShare(t) * 100)}%</small>`;
  b.title = `${NICE(t)}: ${Math.round(doseShare(t) * 100)}% of the dose rate`;
  b.addEventListener("click", () => { open(t); $("#cases").scrollIntoView({ behavior: "smooth" }); });
  strip.append(b);
});
const rest = document.createElement("button");
rest.type = "button"; rest.disabled = true; rest.style.flex = `${1 - traced} 1 0`; rest.title = "Nuclides not traced in this chapter";
strip.append(rest);
$("#strip-cap").textContent += ` Iron-55 isn't on the bar: it's the bracket's second-most active nuclide, but it gives off almost no gamma rays. The last ${Math.round((1 - traced) * 100)}% is from nuclides this chapter doesn't trace.`;

/* ---------- Moves ---------- */
(() => {
  let from = null;
  const tried = new Set(store.get(key("moves"), []));
  const out = $("#move-out");
  const b = board($("#move-board"), (name) => { from = name; sound.tick(); b.pick(name); b.clearMarks(); b.clearArrows(); out.textContent = `${NICE(name)} picked. Now choose a move.`; });
  const host = $("#move-buttons");
  MOVES.forEach((m) => {
    const btn = document.createElement("button");
    btn.type = "button"; btn.dataset.m = m.id; btn.classList.toggle("tried", tried.has(m.id));
    const ox = m.dn > 0 ? 8 : m.dn < 0 ? 24 : 16, oy = m.dz < 0 ? 4 : 16;
    btn.innerHTML = `<svg viewBox="0 0 44 44" aria-hidden="true">${[0, 1, 2].map((r) => [0, 1, 2].map((c) => `<rect x="${2 + c * 14}" y="${2 + r * 14}" width="12" height="12"/>`).join("")).join("")}
      <rect class="from" x="${2 + (ox - 2) / 14 * 14}" y="${2 + (oy - 4) / 14 * 14}" width="12" height="12"/>
      <path class="${m.kind}" style="stroke:var(${m.kind === "slow" ? "--accent" : "--warn"})" d="M${ox + 6},${oy + 6 - 2} L${ox + 6 + m.dn * 14},${oy + 4 - m.dz * 14}"/></svg>
      <span><b>${m.label}</b>${m.text}</span>`;
    btn.addEventListener("click", () => {
      if (!from) { out.textContent = "Pick a grey square first."; sound.wrong(); return; }
      const a = parse(from), z = a.z + m.dz, n = a.n + m.dn, name = nameAt(z, n) || `${z}`;
      b.clearMarks();
      const stable = name in ABUND;
      const onBoard = z >= 24 && z <= 28 && n >= N0 && n <= N1;
      if (onBoard && !stable) b.mark(name, "made", HL[name] || "");
      if (onBoard) b.arrows([{ from, to: name, label: m.label, kind: m.kind, share: 0.4 }], { draw: true, labels: false });
      const what = !onBoard ? `${z < 24 ? "titanium or vanadium" : "an element"}, off this piece of the chart.`
        : stable ? `${NICE(name)}: stable, so nothing more happens.` : `${NICE(name)}: radioactive, half-life ${HL[name] || "under a second"}.`;
      out.innerHTML = `${NICE(from)} ${m.label} → <strong>${what}</strong>`;
      stable || !onBoard ? sound.tick() : sound.click(0.5);
      tried.add(m.id); btn.classList.add("tried"); store.set(key("moves"), [...tried]);
      if (tried.size === MOVES.length) complete("c2-moves");
    });
    host.append(btn);
  });
})();

/* ---------- Cases ---------- */
const closed = new Set(store.get(key("closed"), []));
const found = store.get(key("found"), {});
const caseBoard = board($("#case-board"), (name, g) => guess(name, g));
let current = null;
const list = $("#case-list");
CASES.forEach((t) => {
  const b = document.createElement("button");
  b.type = "button"; b.dataset.t = t;
  b.innerHTML = `<span>${NICE(t)}</span><small>${t === "Fe55" ? "no γ" : Math.round(doseShare(t) * 100) + "%"}</small><svg viewBox="0 0 14 14" aria-hidden="true"><path d="M2 7.5l3 3L12 3.5"/></svg>`;
  b.addEventListener("click", () => { open(t); $("#cases").scrollIntoView({ behavior: "smooth" }); document.querySelector(".side").classList.remove("open"); });
  list.append(b);
});
function paintLists() {
  list.querySelectorAll("button").forEach((b) => { b.classList.toggle("closed", closed.has(b.dataset.t)); b.classList.toggle("on", b.dataset.t === current); });
  strip.querySelectorAll("button[data-t]").forEach((b) => { b.classList.toggle("closed", closed.has(b.dataset.t)); b.classList.toggle("on", b.dataset.t === current); });
  $("#case-count").textContent = `${Math.min(closed.size, 5)} of 5 so far.`;
}
function why(t) {
  const sh = Math.round(doseShare(t) * 100);
  if (t === "Fe55") return "Iron-55 gives off almost no gamma rays, so it adds nothing to the dose rate, but it's the second-most active nuclide in the bracket and it matters for waste (chapter 5).";
  return `${sh}% of the dose rate one day after shutdown.`;
}
function open(t, auto = false) {
  current = t; if (!auto) complete("c2-evidence", { quiet: true });
  const tg = base.targets[t];
  $("#case-name").textContent = NICE(t);
  $("#case-facts").textContent = `half-life ${HL[t]} · ${bqText(tg.activity_Bq)}`;
  caseBoard.clearMarks(); caseBoard.clearArrows(); caseBoard.unflag("could"); caseBoard.unflag("found"); caseBoard.pick(null);
  caseBoard.mark(t, "target");
  $("#case-ask").innerHTML = `${why(t)} <strong>Which stable atom in the steel made it?</strong> Click it on the chart.`;
  $("#case-log").innerHTML = ""; $("#case-routes").innerHTML = "";
  (found[t] || []).forEach((p) => logParent(t, p, false));
  if (closed.has(t)) solve(t, true);
  else { $("#case-stamp").textContent = "open"; $("#case-stamp").className = "stamp"; $("#next-case").hidden = true; }
  paintLists();
  if (!auto) status(`Case ${NICE(t)} opened.`);
}
function logParent(t, p, fresh) {
  const share = Object.fromEntries(parents(base.targets[t]))[p];
  const segs = segments(base.targets[t]).filter((s) => s.from === p || (p === "Ni58" && s.from === "Ni57" && t === "Co57"));
  caseBoard.arrows(segments(base.targets[t]).filter((s) => (found[t] || []).includes(s.from) || (s.from === "Ni57" && (found[t] || []).includes("Ni58"))), { draw: fresh });
  caseBoard.flag(p, "found");
  if (segs.some((s) => s.to !== t)) segs.filter((s) => s.to !== t).forEach((s) => caseBoard.mark(s.to, "made calm", HL[s.to] || ""));
  const how = [...new Set(segs.filter((s) => s.kind !== "decay").map((s) => s.label))].join(" and ");
  const li = document.createElement("li"); li.className = "yes";
  li.textContent = `${NICE(p)}, by ${how}: ${Math.round(share * 100)}% of the ${NICE(t)}.`;
  $("#case-log").append(li);
}
function guess(p, g) {
  if (!current) { open(CASES[0]); return; }
  const t = current, tg = base.targets[t];
  if (closed.has(t) && (found[t] || []).includes(p)) return;
  caseBoard.pick(null);
  const share = Object.fromEntries(parents(tg))[p];
  if (share) {
    found[t] = [...new Set([...(found[t] || []), p])]; store.set(key("found"), found);
    logParent(t, p, true);
    const top = parents(tg)[0][0];
    if (p === top || found[t].reduce((s, x) => s + Object.fromEntries(parents(tg))[x], 0) > 0.8) { sound.done(); solve(t, false); }
    else {
      sound.click(0.6);
      const li = document.createElement("li");
      li.textContent = `A real route, but not the main one. Something else makes most of the ${NICE(t)}.`;
      $("#case-log").append(li);
    }
    return;
  }
  sound.wrong(); caseBoard.flag(p, "nope"); setTimeout(() => caseBoard.unflag("nope"), 400);
  const a = parse(p), b = parse(t), dz = b.z - a.z, dn = b.n - a.n;
  const move = MOVES.find((m) => m.dz === dz && m.dn === dn) || (dz === -1 && dn === 0 ? { label: "(n,np)" } : null);
  const li = document.createElement("li"); li.className = "no";
  li.textContent = move ? `${NICE(p)}: ${move.label} would get there, but ACTINV finds it makes less than 0.5% of the ${NICE(t)}${ABUND[p] < 3 ? `; ${NICE(p)} is only ${ABUND[p]}% of its element` : ""}.`
    : dz > 0 ? `${NICE(p)}: ${NICE(t)} has more protons than ${NICE(p)}. A neutron can knock a proton out, but never add one.`
    : `${NICE(p)}: no single move goes ${Math.abs(dz) ? `${Math.abs(dz)} row${Math.abs(dz) > 1 ? "s" : ""} and ` : ""}${Math.abs(dn)} column${Math.abs(dn) === 1 ? "" : "s"}. Try the moves backwards from ${NICE(t)}.`;
  $("#case-log").append(li);
}
function solve(t, quiet) {
  const tg = base.targets[t];
  closed.add(t); store.set(key("closed"), [...closed]);
  caseBoard.arrows(segments(tg), { draw: !quiet });
  segments(tg).filter((s) => s.to !== t).forEach((s) => caseBoard.mark(s.to, "made calm", HL[s.to] || ""));
  const stamp = $("#case-stamp"); stamp.textContent = "solved"; stamp.className = "stamp shut";
  const RX = Object.fromEntries(Object.values(EDGE).map((x) => [x[0], x[1]]));
  $("#case-routes").innerHTML = `<span class="caption">Every route ACTINV found (explains ${(100 * tg.explained).toFixed(1)}% of the ${NICE(t)})</span>` + tg.routes.map((r) => {
    const chain = r.nuclides.map((n, i) => (i ? ` <i>→ ${esc((EDGE[r.edges[i - 1]] || [r.edges[i - 1]])[0])} →</i> ` : "") + NICE(n)).join("");
    const kind = (EDGE[r.edges[0]] || ["", "fast"])[1];
    return `<div class="route"><span class="chain">${chain}</span><span class="bar"><i class="${kind}" style="width:${(100 * r.fraction).toFixed(1)}%"></i></span><span class="pc">${(100 * r.fraction).toFixed(r.fraction < 0.1 ? 1 : 0)}%</span></div>`;
  }).join("");
  void RX;
  const next = CASES.find((x) => !closed.has(x));
  $("#next-case").hidden = !next;
  if (next) $("#next-case").textContent = `Next case: ${NICE(next)}`;
  if (!quiet) status(`${NICE(t)} solved.${next ? ` ${CASES.length - closed.size} cases left.` : " Every case is solved."}`);
  if (closed.size >= 5) complete("c2-cases");
  paintLists();
}
$("#next-case").addEventListener("click", () => { const n = CASES.find((x) => !closed.has(x)); if (n) open(n); });
$("#hint").addEventListener("click", () => {
  if (!current) return;
  const b = parse(current); caseBoard.unflag("could");
  const could = [...MOVES, { label: "(n,np)", dz: -1, dn: 0 }].map((m) => nameAt(b.z - m.dz, b.n - m.dn)).filter((n) => n in ABUND);
  could.forEach((n) => caseBoard.flag(n, "could")); sound.tick();
  status(could.length ? `Dashed: atoms one move away from ${NICE(current)}. ${could.map(NICE).join(", ")}.` : `No stable atom is one move away; look for a radioactive step in between.`);
});
open(CASES.find((t) => !closed.has(t)) || CASES[0], true);

/* ---------- Same steel, other places ---------- */
(() => {
  let target = "Co60", kase = "cobalt";
  const b = board($("#else-board"));
  const host = $("#else-targets");
  CASES.forEach((t) => host.insertAdjacentHTML("beforeend", `<button type="button" class="sel" data-t="${t}">${NICE(t)}</button>`));
  host.querySelectorAll("[data-t]").forEach((x) => x.addEventListener("click", () => { target = x.dataset.t; sound.tick(); b.clearArrows(); draw(); }));
  document.querySelectorAll("[data-case]").forEach((x) => x.addEventListener("click", () => {
    kase = x.dataset.case; sound.tick();
    document.querySelectorAll("[data-case]").forEach((y) => y.classList.toggle("on", y === x));
    draw();
  }));
  const NOTE = {
    cobalt: "",
    fast_only: "With only 14 MeV neutrons, capture (blue) all but stops: it needs slow neutrons. Fast routes (orange) take over.",
    no_cobalt: "With no cobalt in the steel, every route that started at Co-59 is gone.",
  };
  function draw() {
    host.querySelectorAll("[data-t]").forEach((x) => x.classList.toggle("on", x.dataset.t === target));
    const c = data.cases[kase], tg = c.targets[target];
    b.clearMarks(); b.mark(target, "target");
    const segs = segments(tg);
    segs.filter((s) => s.to !== target).forEach((s) => b.mark(s.to, "made calm", HL[s.to] || ""));
    b.arrows(segs);
    const was = base.targets[target].activity_Bq, ratio = tg.activity_Bq / was;
    const change = kase === "cobalt" ? "" : ` That's ${ratio >= 1 ? `${ratio.toFixed(ratio > 3 ? 0 : 1)} times` : `${Math.round(100 * ratio)}% of`} what the port-cell bracket with cobalt has.`;
    const top = parents(tg)[0];
    $("#else-cap").textContent = `${NICE(target)}: ${bqText(tg.activity_Bq)}.${change} Biggest source: ${NICE(top[0])}, ${Math.round(top[1] * 100)}%. ${NOTE[kase]}`;
    if (kase === "fast_only" && target === "Co60") complete("c2-field");
  }
  draw();
})();

/* ---------- What you can change ---------- */
(() => {
  const blame = {};
  for (const t of CASES) for (const [p, f] of parents(base.targets[t])) {
    const e = parse(p).el; blame[e] = (blame[e] || 0) + base.targets[t].dose_Gy_h * f;
  }
  const rows = Object.entries(blame).sort((a, b) => b[1] - a[1]);
  $("#blame").innerHTML = rows.map(([e, d]) => `<div class="row${e === "Co" ? " imp" : ""}"><span class="el">${NAME[e]}<small>${comp[e]}% of the steel</small></span><span class="bar"><i style="width:${(100 * d / total).toFixed(1)}%"></i></span><span class="pc">${Math.round(100 * d / total)}%</span></div>`).join("")
    + `<div class="row"><span class="el">other nuclides<small>not traced here</small></span><span class="bar"><i style="width:${(100 * (1 - traced)).toFixed(1)}%;opacity:.4"></i></span><span class="pc">${Math.round(100 * (1 - traced))}%</span></div>`;
  const share = (e) => Math.round(100 * blame[e] / total);
  const noCo = data.cases.no_cobalt.total_dose_Gy_h;
  const ANSWER = {
    Co: [true, `Right. Cobalt is ${comp.Co}% of the steel and makes ${share("Co")}% of this dose rate, almost all of it as cobalt-60. ACTINV's run without cobalt gives ${doseText(noCo)} instead of ${doseText(total)}, ${Math.round(100 * (1 - noCo / total))}% lower already, and the gap grows: years from now, chapter 1 showed, cobalt-60 is nearly all that's left. Specify a cobalt limit and you get most of that back.`],
    Ni: [false, `Nickel makes the most, ${share("Ni")}%. But it's ${comp.Ni}% of the steel and part of what makes SS316 stainless. Limiting it means a different alloy: a materials decision, not a purchase specification.`],
    Fe: [false, `Iron is the steel: ${comp.Fe}% of it. Its ${share("Fe")}% comes with any steel you can buy.`],
    Cr: [false, `Chromium, ${comp.Cr}%, is what makes steel stainless, so it stays. Its chromium-51 lasts weeks, so waiting removes it.`],
    Mn: [false, `Manganese is ${comp.Mn}% and SS316 allows up to 2%. You could ask for less, but all of its routes together make only ${share("Mn")}% of this dose rate.`],
  };
  const host = $("#pick"), out = $("#pick-out");
  ["Fe", "Cr", "Ni", "Mn", "Co"].forEach((e) => host.insertAdjacentHTML("beforeend", `<button type="button" class="btn" data-e="${e}">${NAME[e]} (${comp[e]}%)</button>`));
  const show = (e, quiet) => {
    const [right, text] = ANSWER[e];
    out.hidden = false; out.innerHTML = `<p>${text}</p>`;
    host.querySelectorAll("[data-e]").forEach((b) => b.classList.toggle("primary", b.dataset.e === e && right));
    if (!quiet) right ? sound.done() : sound.wrong();
    if (right) complete("c2-verdict");
  };
  host.querySelectorAll("[data-e]").forEach((b) => b.addEventListener("click", () => { store.set(key("pick"), b.dataset.e); show(b.dataset.e); }));
  const saved = store.get(key("pick"), null);
  if (saved) show(saved, true);
})();

/* ---------- Your own part ---------- */
$("#block").textContent = `"pathways": ${JSON.stringify(problem.pathways, null, 2)}`;
$("#py1").innerHTML = python(`from actinv import Problem, solve

result = solve(Problem.from_file("routes.json"))

for target in result["pathway_routes"]["targets"]:
    top = target["paths"][0]                      # the biggest route
    chain = " -> ".join(top["nuclides"])
    print(f'{target["target"]:5}  {top["fraction"]:4.0%} from {chain}  {", ".join(top["edges"])}')`);
$("#py-run").addEventListener("click", () => replay($("#py-run"), $("#py-status"), $("#py1-out"),
  ["Mn56    63% from Mn55 -> Mn56  (n,g)", "Cr51    67% from Cr50 -> Cr51  (n,g)", "Fe55    70% from Fe56 -> Fe55  (n,2n)",
    "Co58    50% from Ni58 -> Co58  (n,p)", "Mn54    63% from Fe54 -> Mn54  (n,p)", "Co60    66% from Co59 -> Co60  (n,g)",
    "Fe59    95% from Fe58 -> Fe59  (n,g)", "Co57    96% from Ni58 -> Co57  (n,d)/(n,np)"].join("\n"),
  () => complete("c2-python")));

const ALL = Object.values(STEPS).flat();
const was = ALL.every(isDone);
onProgress((set) => {
  if (ALL.every((t) => set.has(t))) {
    $("#finish-title").innerHTML = '<svg class="done-mark" viewBox="0 0 16 16" aria-hidden="true"><path d="M2.5 8.5l3.5 3.5L13.5 4"/></svg>Chapter 2 complete';
    $("#finish-text").textContent = "You traced the bracket's radioactive nuclides to the atoms that made them, and found the one ingredient a purchase specification can change.";
    if (!was) sound.fanfare();
  }
});
