import { boot, tabs, quiz, replay, fileExplorer, terminal, python, $, sound, complete, isDone, store, toast, key, predict } from "../kit.js";
import { onProgress } from "../learn.js";

const STEPS = {
  intro: ["c5-start"], guess: ["c5-guess"], tables: ["c5-tables", "c5-times"], niobium: ["c5-over", "c5-zero"],
  run: ["c5-file", "c5-ran"], differs: ["c5-wait"], wrap: ["c5-quiz"],
};
const data = await fetch("data/waste-data.json").then((r) => r.json());
const screenDoc = await fetch("files/waste-screen.json").then((r) => r.json());

boot({
  steps: STEPS, version: data.actinv_version,
  terms: {
    part61: ["10 CFR 61.55", "The section of the U.S. Code of Federal Regulations that sorts low-level radioactive waste into classes A, B and C for burial near the surface, by the concentration of certain nuclides."],
    sof: ["Sum of fractions", "Each nuclide's concentration divided by its limit, added up over the nuclides in a table. A sum of 1 means the waste is exactly at the limit, as if one nuclide alone were at its limit."],
  },
});

const NICE = { C14: "C-14", Ni59: "Ni-59", Nb94: "Nb-94", Tc99: "Tc-99", I129: "I-129", Ni63: "Ni-63", Co60: "Co-60", H3: "H-3", short_lived: "all under 5 years" };
const HALF = { C14: "5,700 years", Ni59: "76,000 years", Nb94: "20,000 years", Tc99: "211,000 years", I129: "16 million years", Ni63: "101 years" };
const FROM = { C14: "nitrogen, by (n,p)", Ni59: "nickel-58, by capture", Nb94: "niobium-93 by capture, and molybdenum", Tc99: "molybdenum-98, by capture then decay", I129: "none here", Ni63: "nickel-62, by capture" };
const COLOR = { C14: "#2bb39a", Ni59: "#e07b39", Nb94: "#c25bd6", Tc99: "#d6b336", I129: "#8a9a5b", Ni63: "#4f7cff" };
const CLASS = { A: "Class A", B: "Class B", C: "Class C", above_class_c: "Above class C" };
const T = data.times;

/* ---------- Step 2 ---------- */
predict($("#guess-opts"), "guess", (a, quiet) => {
  const c = T[0].class.C.nominal;
  $("#guess-out").innerHTML = (a === c ? "<b>Right.</b> " : "") + `ACTINV says <b>${CLASS[c]}</b>. Not because of the dose rate, though, and not because of the cobalt that ruled chapters 1 to 4. Step 3 shows why.`;
  document.querySelectorAll("#ladder [data-c]").forEach((d) => d.classList.toggle("on", d.dataset.c === c));
  if (!quiet) sound.reveal();
  complete("c5-guess", { quiet });
});

/* ---------- Step 3: the tables ---------- */
function bar(host, segs, total) {
  const max = Math.max(1.2, total * 1.1), w = (x) => `${(100 * x / max).toFixed(2)}%`;
  host.innerHTML = segs.filter(([, v]) => v > 0).map(([label, v, color]) => `<i style="width:${w(v)};background:${color}" title="${label}"><span>${label}</span></i>`).join("") +
    `<span class="mk" style="left:${w(0.1)}"><span>0.1</span></span><span class="mk one" style="left:${w(1)}"><span>1</span></span>`;
}
(() => {
  let ti = 0, tb = "t1";
  const seenT = new Set(store.get(key("times"), [])), seenTb = new Set(store.get(key("tables"), []));
  const host = $("#times");
  T.forEach((t, i) => host.insertAdjacentHTML("beforeend", `<button type="button" class="sel${i === 0 ? " on" : ""}" data-t="${i}">${t.label}</button>`));
  host.querySelectorAll("[data-t]").forEach((b) => b.addEventListener("click", () => { ti = +b.dataset.t; sound.tick(); host.querySelectorAll("[data-t]").forEach((x) => x.classList.toggle("on", x === b)); draw(); }));
  document.querySelectorAll("[data-tb]").forEach((b) => b.addEventListener("click", () => { tb = b.dataset.tb; sound.tick(); document.querySelectorAll("[data-tb]").forEach((x) => x.classList.toggle("on", x === b)); draw(); }));
  function draw() {
    const t = T[ti], sh = tb === "t1" ? t.shares.table1_C : t.shares.table2_C;
    const rows = Object.entries(sh).filter(([, v]) => v > 1e-6).sort((a, b) => b[1] - a[1]);
    const total = rows.reduce((s, [, v]) => s + v, 0);
    bar($("#sumbar"), rows.map(([n, v]) => [NICE[n], v, COLOR[n] || "#888"]), total);
    $("#sum-read").textContent = `Sum of fractions: ${total.toFixed(3)}`;
    $("#tbl").innerHTML = `<tr><th>Nuclide</th><th>Ci per m³</th><th>Limit</th><th>Fraction</th><th>Half-life, made from</th></tr>` +
      rows.map(([n, v]) => `<tr><td>${NICE[n]}</td><td>${t.ci_per_m3[n].toPrecision(3)}</td><td>${tb === "t1" ? data.limits_ci_m3[n][0] : data.limits_ci_m3[n][2]}</td><td>${v.toFixed(3)}</td><td>${HALF[n]}; ${FROM[n]}</td></tr>`).join("");
    $("#tbl-cap").textContent = tb === "t1"
      ? `Above 0.1 and below 1: class C at best, whatever table 2 says. Concentrations are per cubic metre of steel; the rules use curies (Ci), an older unit: 1 Ci is 37 billion decays a second.`
      : `Nickel-63's class C limit for activated metal is 7,000 Ci/m³. Its sum falls as nickel-63 decays (half-life 101 years), from ${T[0].shares.table2_C.Ni63.toFixed(2)} at 1 day to ${T[3].shares.table2_C.Ni63.toFixed(2)} at 100 years, but table 1 already decided class C.`;
    seenT.add(ti); seenTb.add(tb); store.set(key("times"), [...seenT]); store.set(key("tables"), [...seenTb]);
    if (seenTb.size === 2) complete("c5-tables");
    if (seenT.has(0) && seenT.has(3)) complete("c5-times");
  }
  draw();
})();

/* ---------- Step 4: niobium and nitrogen ---------- */
(() => {
  const t = T[0], imp = t.impurities, matrix = t.matrix_sum_C_table1;
  const nb = $("#nb"), n = $("#n");
  const coPart = imp.Co.gradient * data.impurities.Co.nominal;
  const over = (1 - matrix - coPart - imp.N.gradient * data.impurities.N.nominal) / imp.Nb.gradient;
  let last = null;
  function draw() {
    const wNb = +nb.value, wN = +n.value;
    $("#nb-out").textContent = `${wNb.toFixed(4)}%`; $("#n-out").textContent = `${wN.toFixed(3)}%`;
    const pNb = imp.Nb.gradient * wNb, pN = imp.N.gradient * wN, sum = matrix + coPart + pNb + pN;
    const cls = sum > 1 ? "above_class_c" : "C";
    $("#nb-class").textContent = CLASS[cls];
    $("#nb-class").classList.toggle("bad", cls !== "C");
    bar($("#nbbar"), [["the steel itself", matrix + coPart, "#868e96"], ["nitrogen → C-14", pN, COLOR.C14], ["niobium → Nb-94", pNb, COLOR.Nb94]], sum);
    $("#nb-read").textContent = `Table 1 sum: ${sum.toFixed(3)}`;
    $("#nb-cap").textContent = cls !== "C"
      ? `Above class C from about ${over.toFixed(4)}% niobium, with nitrogen at its typical amount. The certificate allows up to 0.05%, so the worst steel it allows can't be buried near the surface.`
      : wNb === 0
        ? `Even with no niobium the sum is ${sum.toFixed(2)}, well above 0.1: the steel's own nickel and molybdenum make nickel-59, niobium-94 and technetium-99. This block can't be better than class C, so class B is out of reach.`
        : `Each 0.01% of niobium adds ${(imp.Nb.gradient * 0.01).toFixed(2)} to the sum; it crosses 1 at about ${over.toFixed(4)}%. A purchase limit on niobium keeps the block class C.`;
    if (last !== null && cls !== last) cls === "C" ? sound.done() : sound.wrong();
    last = cls;
    if (cls !== "C") complete("c5-over");
    if (wNb === 0) complete("c5-zero");
  }
  nb.addEventListener("input", draw); n.addEventListener("input", draw);
  draw();
})();

/* ---------- Step 5: the file, three ways in ---------- */
const ran = (how) => { if (complete("c5-ran", { quiet: true })) { sound.reveal(); toast(`Block classified (${how}).`); } };
fileExplorer({
  pre: $("#file"), chips: $("#chips"), explain: $("#explain"), doc: screenDoc, task: "c5-file",
  shorten: {
    matrix: (v) => "{" + Object.entries(v).map(([k, x]) => `<span class="k">"${k}"</span>: <span class="n">${x}</span>`).join(", ") + "}",
    impurities: (v) => "{\n" + Object.entries(v).map(([k, x]) => `    <span class="k">"${k}"</span>: {<span class="k">"nominal"</span>: <span class="n">${x.nominal}</span>, <span class="k">"min"</span>: <span class="n">${x.min}</span>, <span class="k">"max"</span>: <span class="n">${x.max}</span>}`).join(",\n") + "\n  }",
    endpoints: (v) => "[" + v.map((e) => `{<span class="k">"endpoint"</span>: <span class="s">"${e.endpoint}"</span>, <span class="k">"class"</span>: <span class="s">"${e.class}"</span>}`).join(", ") + "]",
  },
  parts: [
    { id: "base", keys: ["schema", "base_spec", "balance", "matrix"], name: "The steel",
      text: "<p>As in chapter 3: <code>base.json</code> is the block's problem, five years in the strong field and cooling to 100 years, and <code>matrix</code> the steel's main ingredients, with iron making up the rest.</p>" },
    { id: "imps", keys: ["impurities"], name: "Impurities",
      text: "<p>The three traces to screen, each with its range. Niobium may be anywhere from none to 0.05%.</p>" },
    { id: "ask", keys: ["targets", "endpoints"], name: "When and what",
      text: "<p>Steps 6 to 9 are 1 day, 1, 10 and 100 years after the block comes out. Each <code>waste_class</code> endpoint asks whether the block meets a class: here A and C.</p>" },
    { id: "waste", keys: ["waste"], name: "waste",
      text: "<p>How to classify: the rule pack (<code>us-nrc-10cfr61.55-v1</code>), the block's mass and density, which give its volume, and the waste type: activated metal has its own limits for some nuclides. Tritium from outside the block doesn't apply here, and the file says so rather than leaving it out.</p>" },
  ],
});
tabs(document.querySelector(".tabs"));
terminal({
  screen: $("#screen"), cue: $("#cue"), folder: "waste-lesson", version: data.actinv_version,
  files: ["waste-screen.json", "base.json", "block.json", "actinv-data/"],
  script: [
    { cmd: "actinv impurities waste-screen.json waste-screen-result.json", task: "c5-t-screen", needs: ["waste-screen.json", "base.json"], makes: ["waste-screen-result.json"],
      ms: 3200, working: "one run per element, then full runs to check… (about a minute on a laptop)",
      out: "impurities: 12 element solves, 4 targets, verification passed (max rel dev 1.8042966464498896e-15) -> waste-screen-result.json",
      hint: "Now read the class at each time.", then: () => ran("command line") },
    { cmd: `jq -r '.targets[] | .step as $s | .endpoints[] | select(.response == "waste_class:C") | "step \\($s)  \\(.nominal.class)  worst \\(.worst_case.class)"' waste-screen-result.json`,
      task: "c5-t-jq", needs: ["waste-screen-result.json"],
      out: ["step 6  C  worst above_class_c", "step 7  C  worst above_class_c", "step 8  C  worst above_class_c", "step 9  C  worst above_class_c"],
      hint: "Class C at every time for the typical steel; above class C for the worst steel the certificate allows. Who's to blame?" },
    { cmd: `jq -r '.targets[0].endpoints[] | select(.response == "waste_class:C") | .impurities | to_entries[] | "\\(.key)  now \\(.value.contribution_nominal)  at its most \\(.value.contribution_at_max)"' waste-screen-result.json`,
      needs: ["waste-screen-result.json"],
      out: ["Co  now 1.3166873974394908E-7  at its most 2.6333747948789817E-7", "N  now 0.06683729503234213  at its most 0.13367459006468427", "Nb  now 0.3211123663219655  at its most 1.6055618316098277"],
      hint: "Each impurity's part of the table 1 sum. Niobium at its most adds 1.6 on its own: above class C. Cobalt adds nothing that counts." },
    { cmd: "actinv run block.json block-result.json", needs: ["block.json"], makes: ["block-result.json"], ms: 1100, working: "solving 9 steps…",
      out: "9 steps, 510 of 3873 states, 1091.9 ms -> block-result.json", hint: "A full run of the block, for every nuclide's concentration; the app's Waste class page reads it." },
  ],
});
$("#app-done").addEventListener("click", () => { complete("c5-app"); ran("desktop app"); });
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
  () => { complete("c5-python"); ran("Python"); }));

/* ---------- Step 6 ---------- */
predict($("#wait-opts"), "wait", (a, quiet) => {
  $("#wait-out").innerHTML = (a === "no" ? "<b>Right.</b> " : "<b>Not this time.</b> ") +
    `Table 1's nuclides last thousands of years: its sum goes from ${T[0].reported_sums["C|1|None"].toFixed(3)} to ${T[3].reported_sums["C|1|None"].toFixed(3)} in a century. Waiting lowers the dose rate, not the class. The class is settled when you buy the steel.`;
  if (!quiet) a === "no" ? sound.done() : sound.wrong();
  complete("c5-wait", { quiet });
});

/* ---------- Step 7 ---------- */
quiz($("#quiz"), [
  { q: "Which nuclides decide this block's class?",
    a: [["Long-lived ones in table 1: niobium-94, nickel-59, technetium-99, carbon-14", true], ["Cobalt-60, which gives most of the dose", false], ["Whatever has the highest activity", false]],
    why: "Cobalt-60 dominates the dose but halves every 5.3 years, and table 1 doesn't list it. The class follows the nuclides that would still be there in thousands of years." },
  { q: "The certificate allows up to 0.05% niobium. What should the buyer do?",
    a: [["Ask for niobium below about 0.014%", true], ["Nothing: the typical amount is fine", false], ["Ask for less cobalt", false]],
    why: "The typical steel is class C, but steel at the top of the range isn't allowed near the surface at all. A niobium limit is the cheap fix, and it has to happen before the steel goes into the machine." },
  { q: "Why can't this block reach class B, even with no niobium or nitrogen?",
    a: [["The steel's own nickel and molybdenum keep the table 1 sum above 0.1", true], ["Class B needs the block to cool longer", false], ["Activated metal is always class C", false]],
    why: "Nickel-59 and the molybdenum's niobium-94 and technetium-99 come from the main ingredients. Reaching class B would take a different steel." },
], "c5-quiz");
const ALL = Object.values(STEPS).flat();
const was = ALL.every(isDone);
onProgress((set) => {
  if (ALL.every((t) => set.has(t))) {
    $("#finish-title").innerHTML = '<svg class="done-mark" viewBox="0 0 16 16" aria-hidden="true"><path d="M2.5 8.5l3.5 3.5L13.5 4"/></svg>Chapter 5 complete';
    $("#finish-text").textContent = "You classified the block, found the nuclides and the trace of niobium that decide its class, and saw that waiting won't change it.";
    if (!was) sound.fanfare();
  }
});
