import { boot, replay, python, $, sound, complete, isDone, store, key } from "../kit.js";
import { onProgress, sci } from "../learn.js";

const STEPS = { sheet: ["c5-nb", "c5-sum", "c5-col", "c5-class"], trace: ["c5-worst", "c5-limit"], later: ["c5-wait"], script: ["c5-python"] };
const data = await fetch("data/waste-data.json").then((r) => r.json());

boot({
  steps: STEPS, version: data.actinv_version,
  terms: {
    part61: ["10 CFR 61.55", "The section of the U.S. Code of Federal Regulations that sorts low-level radioactive waste into classes A, B and C for burial near the surface, by the concentration of certain nuclides."],
    sof: ["Sum of fractions", "Each nuclide's concentration divided by its limit, added up over the nuclides in a table. A sum of 1 means the waste is exactly at the limit, as if one nuclide alone were at its limit."],
  },
});

const NICE = { C14: "carbon-14", Ni59: "nickel-59", Nb94: "niobium-94", Tc99: "technetium-99", I129: "iodine-129", Ni63: "nickel-63", Co60: "cobalt-60", H3: "tritium", short_lived: "all others under 5 years" };
const T1 = ["C14", "Ni59", "Nb94", "Tc99", "I129"], T2 = ["short_lived", "Ni63", "Co60", "H3"];
const LIM = data.limits_ci_m3, IMP = data.impurities;
// Which table 1 row each trace element feeds: niobium-93 becomes niobium-94, nitrogen-14 becomes carbon-14.
const FEEDS = { Nb: "Nb94", N: "C14" };
const STEEL_TEXT = { typical: "typical", worst: "worst allowed", spec: "worst, Nb limited" };
const status = (t) => { $("#status").textContent = t; $("#status").classList.add("fresh"); };
const f3 = (x) => x.toFixed(x < 10 ? 3 : 0);

let ti = store.get(key("time"), 0);
let steel = store.get(key("steel"), "typical");
let nbLimit = store.get(key("nb"), null);

/* ---------- The numbers for one time and one steel ---------- */
function traces(s = steel) {
  if (s === "typical") return { Nb: IMP.Nb.nominal, N: IMP.N.nominal };
  const nb = s === "spec" && nbLimit !== null ? Math.min(nbLimit, IMP.Nb.max) : IMP.Nb.max;
  return { Nb: nb, N: IMP.N.max };
}
function sheet(i = ti, s = steel) {
  const t = data.times[i], x = traces(s);
  const frac1 = {};
  for (const n of T1) frac1[n] = t.shares.table1_C[n];
  for (const [e, n] of Object.entries(FEEDS)) frac1[n] += t.impurities[e].gradient * (x[e] - IMP[e].nominal);
  const conc1 = Object.fromEntries(T1.map((n) => [n, frac1[n] * LIM[n][0]]));
  const sum1 = T1.reduce((a, n) => a + frac1[n], 0);
  const c = t.ci_per_m3;
  const cols = [0, 1, 2].map((k) => T2.reduce((a, n) => a + (n === "short_lived" ? (k ? 0 : c[n] / 700) : LIM[n][k] ? c[n] / LIM[n][k] : 0), 0));
  const col = cols.findIndex((v) => v <= 1); // -1: none
  const r1 = sum1 <= 0.1 ? "A" : sum1 <= 1 ? "C" : "X";
  const cls = r1 === "A" ? (col < 0 ? "X" : "ABC"[col]) : r1 === "C" ? (cols[2] <= 1 ? "C" : "X") : "X";
  return { t, x, frac1, conc1, sum1, cols, col, r1, cls, c };
}
const CLASS_TEXT = { A: "Class A", B: "Class B", C: "Class C", X: "Not for near-surface burial" };

/* ---------- Header fields ---------- */
const vol = data.mass_g / data.density_g_cm3 / 1e6;
$("#f-vol").textContent = `${data.mass_g / 1000} kg · ${vol.toFixed(4)} m³`;
$("#f-irr").textContent = `${data.years} years, ${sci(data.flux)} n/cm²/s`;

/* ---------- Drawing the form ---------- */
function draw() {
  const S = sheet(), base = sheet(ti, "typical");
  $("#f-cool").textContent = S.t.label;
  $("#f-steel").textContent = `Nb ${S.x.Nb.toFixed(3)}%, N ${S.x.N.toFixed(2)}%`;

  // Part A
  const nbDone = isDone("c5-nb"), sumDone = isDone("c5-sumin");
  $("#t1").innerHTML = `<thead><tr><th>Nuclide</th><th>Ci/m³</th><th>class C limit</th><th>fraction</th></tr></thead><tbody>`
    + T1.map((n) => {
      const moved = Math.abs(S.frac1[n] - base.frac1[n]) > 1e-9 ? " moved" : "";
      const cell = n === "Nb94" && !nbDone ? `<input class="pencil" id="in-nb" inputmode="decimal" aria-label="Niobium-94 fraction" placeholder="?">` : f3(S.frac1[n]);
      return `<tr><td>${NICE[n]}</td><td class="${moved.trim()}">${sci(S.conc1[n], 3)}</td><td>${LIM[n][0]}</td><td class="${moved.trim()}">${cell}</td></tr>`;
    }).join("")
    + `<tr class="total"><td>Sum of fractions</td><td></td><td></td><td>${sumDone ? f3(S.sum1) : `<input class="pencil" id="in-sum" inputmode="decimal" aria-label="Table 1 sum of fractions" placeholder="?"${nbDone ? "" : " disabled"}>`}</td></tr></tbody>`;

  // Part B
  const lim = (n, k) => (n === "short_lived" ? (k ? "–" : 700) : LIM[n][k] ?? "–");
  const fr = (n, k) => { const L = n === "short_lived" ? (k ? null : 700) : LIM[n][k]; return L ? f3(S.c[n] / L) : "–"; };
  $("#t2").innerHTML = `<thead><tr><th>Nuclide</th><th>Ci/m³</th><th>col. 1 limit</th><th>fraction</th><th>col. 2</th><th>fraction</th><th>col. 3</th><th>fraction</th></tr></thead><tbody>`
    + T2.map((n) => `<tr><td>${NICE[n]}</td><td>${sci(S.c[n], 3)}</td>${[0, 1, 2].map((k) => `<td>${lim(n, k)}</td><td>${fr(n, k)}</td>`).join("")}</tr>`).join("")
    + `<tr class="total"><td>Sum of fractions</td><td></td>${S.cols.map((v) => `<td></td><td>${f3(v)}</td>`).join("")}</tr></tbody>`;
  $("#t2").closest(".part").querySelector("h4").title = "A dash means 10 CFR 61.55 sets no limit for that nuclide in that column.";

  boxes("#b1", isDone("c5-sum") ? S.r1 : null, sumDone && !isDone("c5-sum"));
  boxes("#b2", isDone("c5-col") ? (S.col < 0 ? "X" : String(S.col + 1)) : null, !isDone("c5-col"));
  boxes("#b3", isDone("c5-class") ? S.cls : null, !isDone("c5-class"));
  $("#w1").innerHTML = isDone("c5-sum") ? `Sum <b>${f3(S.sum1)}</b>: ${S.r1 === "A" ? "under a tenth of the limits" : S.r1 === "C" ? "over a tenth of the limits, under the limits" : "over the limits"}.` : nbDone ? "Add the fractions, then tick the result." : "";
  $("#w2").innerHTML = isDone("c5-col") ? (S.col < 0 ? "No column holds." : `Column ${S.col + 1} is the first whose sum is at most 1.`) + " Cobalt-60 and the short-lived nuclides only count against column 1." : "";
  const stamp = $("#stamp");
  stamp.hidden = !isDone("c5-class");
  stamp.textContent = CLASS_TEXT[S.cls];
  stamp.classList.toggle("gtcc", S.cls === "X");
  $("#w3").innerHTML = isDone("c5-class") ? (S.cls === "X" ? "Table 1 is over its limits: this block can't go to a near-surface site at all." : `Part A puts it in class ${S.r1 === "A" ? "A or higher" : "C"}, and table 2 allows class C. The stricter result is <b>${CLASS_TEXT[S.cls]}</b>.`) : "";

  const nb = $("#in-nb");
  nb?.addEventListener("change", () => check(nb, S.frac1.Nb94, "c5-nb", `Right: ${sci(S.conc1.Nb94, 3)} ÷ ${LIM.Nb94[0]} = ${f3(S.frac1.Nb94)}. Niobium-94 alone uses over half the class C limit.`));
  const sm = $("#in-sum");
  sm?.addEventListener("change", () => check(sm, S.sum1, "c5-sumin", `Right: the table 1 sum is ${f3(S.sum1)}. Now tick what it means.`));
  side(S);
}
function check(input, want, task, msg) {
  const v = parseFloat(input.value.replace(",", "."));
  if (!isFinite(v)) return;
  if (Math.abs(v / want - 1) <= 0.02) { sound.done(); complete(task, { quiet: task === "c5-sumin" }); status(msg); draw(); $(task === "c5-nb" ? "#in-sum" : "#b1 button")?.focus(); }
  else { sound.wrong(); input.classList.add("bad"); status(task === "c5-nb" ? "Not quite: divide the concentration by the class C limit in the same row." : "Not quite: add the four fractions in the column (iodine-129 is zero)."); }
}
function boxes(sel, on, open) {
  document.querySelectorAll(`${sel} button`).forEach((b) => {
    b.classList.toggle("on", on !== null && b.dataset.v === on);
    b.classList.remove("wrong");
    b.disabled = !open;
  });
}

/* The three groups of ticks */
function tick(sel, task, want, explain) {
  document.querySelectorAll(`${sel} button`).forEach((b) => b.addEventListener("click", () => {
    const S = sheet();
    const right = want(S);
    if (b.dataset.v === right) { complete(task); sound.done(); status(explain(S, true)); draw(); }
    else { b.classList.add("wrong"); sound.wrong(); status(explain(S, false, b.dataset.v)); }
  }));
}
tick("#b1", "c5-sum", (S) => S.r1, (S, ok) => ok ? "Table 1 says class C. Now table 2." : `The sum is ${f3(S.sum1)}: compare it with 0.1 and 1.`);
tick("#b2", "c5-col", (S) => (S.col < 0 ? "X" : String(S.col + 1)), (S, ok, v) => ok ? `Column ${S.col + 1}. Nickel-63 alone is ${f3(S.c.Ni63 / 700)} of column 2's limit.` : `Column ${v} adds up to ${f3(S.cols[+v - 1] ?? 0)}: it has to be 1 or less.`);
tick("#b3", "c5-class", (S) => S.cls, (S, ok) => ok ? `Classified: ${CLASS_TEXT[S.cls]}.` : "The block takes the stricter of parts A and B.");

/* ---------- The certificate's worst case ---------- */
document.querySelectorAll("[data-steel]").forEach((b) => b.addEventListener("click", () => pickSteel(b.dataset.steel)));
function pickSteel(s, quiet = false) {
  if (s === "spec" && nbLimit === null) { $("#nb-limit").focus(); status("Write a niobium limit first."); return; }
  steel = s; store.set(key("steel"), s);
  document.querySelectorAll("[data-steel]").forEach((x) => x.classList.toggle("on", x.dataset.steel === s));
  const all = data.times.map((_, i) => sheet(i, s)), worst = all.reduce((a, b) => (b.sum1 > a.sum1 ? b : a));
  if (!quiet) sound.tick();
  if (s === "worst") {
    if (!quiet) { complete("c5-worst"); sound.wrong(); }
    $("#trace-out").innerHTML = `At 0.05% niobium the niobium-94 fraction alone is ${f3(worst.frac1.Nb94)}, and the table 1 sum is <strong>${f3(worst.sum1)}</strong>. Steel the certificate allows would make a block that can't be buried near the surface at all. The typical steel is class C only because the mill happened to ship less.`;
  } else if (s === "spec") {
    const ok = worst.sum1 <= 1;
    if (ok && !quiet) { complete("c5-limit"); sound.done(); }
    if (!ok && !quiet) sound.wrong();
    const most = 1 - (worst.sum1 - worst.frac1.Nb94);
    const edge = (1 - worst.sum1) / worst.t.impurities.Nb.gradient + worst.x.Nb;
    $("#trace-out").innerHTML = ok
      ? `With niobium at most ${nbLimit}% and nitrogen at its top, the table 1 sum is at most <strong>${f3(worst.sum1)}</strong>: class C whatever else the mill ships. The tightest you need is about ${edge.toFixed(4)}%, ${(IMP.Nb.max / edge).toFixed(1)} times tighter than the certificate. Niobium is cheap to limit; it has to be asked for before the steel is made.`
      : `With niobium up to ${nbLimit}% the table 1 sum can reach <strong>${f3(worst.sum1)}</strong>, still over 1. ${most > 0 ? `Niobium-94 can use at most ${f3(most)} of the limit, so the niobium has to come down to about ${edge.toFixed(4)}%.` : ""}`;
  } else {
    $("#trace-out").innerHTML = `Typical steel: table 1 sum ${f3(all[ti].sum1)}, class C.`;
  }
  draw();
}
$("#nb-limit").value = nbLimit ?? "";
$("#nb-limit").addEventListener("change", (e) => {
  const v = parseFloat(e.target.value.replace(",", "."));
  if (!isFinite(v) || v < 0) return;
  nbLimit = v; store.set(key("nb"), v); pickSteel("spec");
});

/* ---------- Cooling time ---------- */
const timesHost = $("#times");
data.times.forEach((t, i) => timesHost.insertAdjacentHTML("beforeend", `<button type="button" class="sel" data-t="${i}">${t.label}</button>`));
timesHost.querySelectorAll("[data-t]").forEach((b) => b.addEventListener("click", () => { pickTime(+b.dataset.t); }));
function pickTime(i, quiet = false) {
  ti = i; store.set(key("time"), i);
  timesHost.querySelectorAll("[data-t]").forEach((x) => x.classList.toggle("on", +x.dataset.t === i));
  const a = sheet(0, "typical"), z = sheet(i, "typical");
  $("#later-out").innerHTML = i === 0 ? "One day after the block comes out. Pick a later time." :
    `After ${z.t.label}: cobalt-60 has gone from ${sci(a.c.Co60, 2)} to ${sci(z.c.Co60, 2)} Ci/m³, and table 2's column 1 sum from ${f3(a.cols[0])} to ${f3(z.cols[0])}. Table 1 went from ${f3(a.sum1)} to ${f3(z.sum1)}. ${i === data.times.length - 1 ? "Its nuclides last thousands of years, so waiting lowers the dose rate, not the class. The class is settled when the steel is bought." : ""}`;
  if (!quiet) { sound.tick(); if (i === data.times.length - 1) complete("c5-wait"); }
  draw();
}

/* ---------- Side panel ---------- */
function side(S) {
  $("#ws").innerHTML = `<div><span>Cooling</span><b>${S.t.label}</b></div><div><span>Steel</span><b>${STEEL_TEXT[steel]}</b></div>
    <div><span>Table 1</span><b>${isDone("c5-sum") ? f3(S.sum1) : "—"}</b></div><div><span>Table 2</span><b>${isDone("c5-col") ? (S.col < 0 ? "none" : `col. ${S.col + 1}`) : "—"}</b></div>
    <div><span>Class</span><b>${isDone("c5-class") ? (S.cls === "X" ? "none" : S.cls) : "—"}</b></div>`;
}

/* ---------- Let ACTINV fill it in ---------- */
$("#py1").innerHTML = python(`import json
import subprocess

subprocess.run(["actinv", "impurities", "waste-screen.json", "waste-screen-result.json"], check=True)
result = json.load(open("waste-screen-result.json"))

for target, label in zip(result["targets"], ["1 day", "1 year", "10 years", "100 years"]):
    c = next(e for e in target["endpoints"] if e["response"] == "waste_class:C")
    table1 = next(t["normalized_sum"] for t in c["nominal"]["target_class_constraints"] if t["table"] == 1)
    print(f'{label:9}  class {c["nominal"]["class"]}  (table 1 sum {table1:.3f})  worst case {c["worst_case"]["class"]}')`);
$("#py-run").addEventListener("click", () => replay($("#py-run"), $("#py-status"), $("#py1-out"),
  ["impurities: 12 element solves, 4 targets, verification passed (max rel dev 1.8042966464498896e-15) -> waste-screen-result.json",
    "1 day      class C  (table 1 sum 0.876)  worst case above_class_c", "1 year     class C  (table 1 sum 0.876)  worst case above_class_c",
    "10 years   class C  (table 1 sum 0.876)  worst case above_class_c", "100 years  class C  (table 1 sum 0.873)  worst case above_class_c"].join("\n"),
  () => complete("c5-python")));

/* ---------- Start ---------- */
pickTime(ti, true);
pickSteel(steel === "spec" && nbLimit === null ? "typical" : steel, true);

const ALL = Object.values(STEPS).flat();
const was = ALL.every(isDone);
onProgress((set) => {
  if (ALL.every((t) => set.has(t))) {
    $("#finish-title").innerHTML = '<svg class="done-mark" viewBox="0 0 16 16" aria-hidden="true"><path d="M2.5 8.5l3.5 3.5L13.5 4"/></svg>Chapter 5 complete';
    $("#finish-text").textContent = "You classified the block from ACTINV's concentrations, found that the certificate's niobium range decides the class, and saw that waiting doesn't change it.";
    if (!was) sound.fanfare();
  }
});
