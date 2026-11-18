import { boot, tabs, quiz, replay, fileExplorer, terminal, python, $, sound, complete, isDone, store, toast, key, wait } from "../kit.js";
import { onProgress, sci } from "../learn.js";

const STEPS = {
  intro: ["c3-start"], guess: ["c3-guess"], sheet: ["c3-times", "c3-under", "c3-clear"], how: ["c3-check"],
  run: ["c3-file", "c3-ran"], limit: ["c3-limit"], wrap: ["c3-quiz"],
};
const data = await fetch("data/impurities-data.json").then((r) => r.json());
const screenDoc = await fetch("files/screen.json").then((r) => r.json());

boot({
  steps: STEPS, version: data.actinv_version,
  terms: {
    impurity: ["Impurity", "An element in the steel that nobody added on purpose, or added only in traces. The certificate gives a range for each, and the steel can be anywhere in it."],
  },
});

const IMP = ["Co", "Cu", "N", "Nb", "Ta", "B", "Ag"];
const NAME = { Co: "cobalt", Cu: "copper", N: "nitrogen", Nb: "niobium", Ta: "tantalum", B: "boron", Ag: "silver" };
const COLOR = { Co: "#4f7cff", Cu: "#e07b39", N: "#2bb39a", Nb: "#c25bd6", Ta: "#d6b336", B: "#8a9a5b", Ag: "#e0567a" };
const NICE = (n) => n.replace(/^([A-Z][a-z]?)(\d+)(m1)?$/, (_, e, a, m) => `${e}-${a}${m ? "m" : ""}`);
const ranges = data.impurities;
const pct = (w) => (w >= 0.01 ? w.toFixed(3) : w.toFixed(4)) + "%";
const LIMIT = data.limit_Gy_h;

/* ---------- Step 1: the certificate ---------- */
$("#cert").innerHTML = `<tr><th>Impurity</th><th>Typical</th><th>Allowed range</th><th></th></tr>` +
  IMP.map((e) => `<tr><td>${NAME[e]} (${e})</td><td>${pct(ranges[e].nominal)}</td><td>${pct(ranges[e].min)} to ${pct(ranges[e].max)}</td><td></td></tr>`).join("");

/* ---------- Step 2: guesses ---------- */
const QS = [
  { id: "dose10", q: "The dose rate when a crew removes the bracket after 10 years", t: 2, ep: "dose.contact_proxy" },
  { id: "clear100", q: "Whether the steel can go out as ordinary scrap after 100 years", t: 3, ep: "clearance_index" },
];
const leader = (q) => {
  const imps = data.screen[q.t][q.ep].impurities;
  return IMP.slice().sort((a, b) => imps[b].contribution - imps[a].contribution)[0];
};
(() => {
  const host = $("#guesses"), lock = $("#lock");
  const saved = store.get(key("guess"), {});
  const locked = () => store.get(key("guess-locked"), false);
  QS.forEach((q) => {
    const row = document.createElement("div"); row.className = "guess-row";
    row.innerHTML = `<span>${q.q}</span><span class="controls" style="margin:0">${IMP.map((e) => `<button type="button" class="sel" data-el="${e}">${NAME[e]}</button>`).join("")}</span><span class="verdict"></span>`;
    row.querySelectorAll("[data-el]").forEach((b) => b.addEventListener("click", () => {
      if (locked()) return;
      saved[q.id] = b.dataset.el; store.set(key("guess"), saved); sound.tick(); draw();
    }));
    host.append(row);
  });
  function draw() {
    host.querySelectorAll(".guess-row").forEach((row, i) => {
      const q = QS[i], g = saved[q.id];
      row.querySelectorAll("[data-el]").forEach((b) => b.classList.toggle("on", b.dataset.el === g));
      if (!locked()) return;
      const best = leader(q), e = data.screen[q.t][q.ep], c = e.impurities[best];
      row.querySelectorAll("[data-el]").forEach((b) => { b.disabled = true; b.classList.toggle("right", b.dataset.el === best); b.classList.toggle("miss", b.dataset.el === g && g !== best); });
      row.querySelector(".verdict").textContent = (g === best ? "Right: " : "Most of it: ") +
        `${NAME[best]}, ${Math.round(100 * c.contribution / e.nominal)}% of the total, through ${NICE(c.top[0])}.` +
        (q.id === "clear100" ? " Nitrogen-14 turns into carbon-14 by an (n,p) reaction, and carbon-14 lasts 5,700 years. Cobalt's cobalt-60 is mostly gone by then." : " The cobalt-60 of chapters 1 and 2.");
    });
    const n = QS.filter((q) => saved[q.id]).length;
    lock.disabled = locked() || n < QS.length;
    $("#guess-note").textContent = locked() ? "Step 3 lets you see the whole picture." : n < QS.length ? "Pick one for each." : "Ready.";
  }
  lock.addEventListener("click", () => { store.set(key("guess-locked"), true); sound.reveal(); complete("c3-guess"); draw(); });
  draw();
})();

/* ---------- Step 3: the spec sheet ---------- */
const EP = {
  "dose.contact_proxy": { name: "Dose rate at the surface", fmt: (v) => `${sci(v, 3)} Gy/h` },
  clearance_index: { name: "Clearance index (1 or less clears)", fmt: (v) => sci(v, 3) },
  "activity.total": { name: "Activity", fmt: (v) => `${sci(v, 3)} Bq per gram` },
};
let ti = 2, ep = "dose.contact_proxy";
const wt = Object.fromEntries(IMP.map((e) => [e, ranges[e].nominal]));
const seenTimes = new Set(store.get(key("times"), []));
(() => {
  const ts = $("#times");
  data.times.forEach((t, i) => ts.insertAdjacentHTML("beforeend", `<button type="button" class="sel${i === ti ? " on" : ""}" data-t="${i}">${t}</button>`));
  ts.querySelectorAll("[data-t]").forEach((b) => b.addEventListener("click", () => { ti = +b.dataset.t; sound.tick(); ts.querySelectorAll("[data-t]").forEach((x) => x.classList.toggle("on", x === b)); draw(); }));
  document.querySelectorAll("[data-ep]").forEach((b) => b.addEventListener("click", () => { ep = b.dataset.ep; sound.tick(); document.querySelectorAll("[data-ep]").forEach((x) => x.classList.toggle("on", x === b)); draw(); }));
  const host = $("#sliders");
  IMP.forEach((e) => {
    const r = ranges[e], row = document.createElement("label"); row.className = "imp";
    row.innerHTML = `<span class="el"><i style="background:${COLOR[e]}"></i>${NAME[e]}</span><input type="range" min="${r.min}" max="${r.max}" step="${(r.max - r.min) / 200}" value="${r.nominal}" aria-label="${NAME[e]}, wt%"><output></output>`;
    const inp = row.querySelector("input");
    inp.addEventListener("input", () => { wt[e] = +inp.value; draw(); });
    inp.addEventListener("change", () => sound.tick());
    host.append(row);
  });
  const setAll = (f) => { IMP.forEach((e, i) => { wt[e] = f(e); host.querySelectorAll("input")[i].value = wt[e]; }); sound.tick(); draw(); };
  $("#nominal").addEventListener("click", () => setAll((e) => ranges[e].nominal));
  $("#worst").addEventListener("click", () => setAll((e) => { const g = data.screen[ti][ep].impurities[e].gradient; return g > 0 ? ranges[e].max : ranges[e].min; }));
})();
function total(t, endpoint, w) {
  const e = data.screen[t][endpoint];
  return e.matrix_only + IMP.reduce((s, x) => s + e.impurities[x].gradient * w[x], 0);
}
function draw() {
  const e = data.screen[ti][ep], v = total(ti, ep, wt);
  $("#sliders").querySelectorAll("output").forEach((o, i) => (o.textContent = pct(wt[IMP[i]])));
  $("#ep-name").textContent = `${EP[ep].name}, ${data.times[ti]} after shutdown`;
  const over = ep === "dose.contact_proxy" ? v > LIMIT : ep === "clearance_index" ? v > 1 : false;
  $("#ep-value").textContent = EP[ep].fmt(v);
  $("#ep-value").classList.toggle("over", over);
  $("#ep-limit").textContent = ep === "dose.contact_proxy" ? (v > LIMIT ? `Above this lesson's limit of ${LIMIT} Gy/h` : `Under this lesson's limit of ${LIMIT} Gy/h`)
    : ep === "clearance_index" ? (v > 1 ? `${sci(v, 2)} times the level for release as scrap` : "Can be released as scrap") : "";
  const contrib = Object.fromEntries(IMP.map((x) => [x, e.impurities[x].gradient * wt[x]]));
  const neg = IMP.reduce((s, x) => s + Math.min(0, contrib[x]), 0);
  const worstV = Math.max(e.worst, total(ti, ep, wt));
  const scaleMax = ep === "dose.contact_proxy" ? Math.max(worstV, LIMIT) * 1.05 : worstV * 1.05;
  const w = (x) => `${Math.max(0, 100 * x / scaleMax).toFixed(2)}%`;
  $("#stack").innerHTML = `<i style="width:${w(e.matrix_only + neg)};background:var(--weak);opacity:.5" title="the steel without impurities"></i>` +
    IMP.map((x) => `<i style="width:${w(Math.max(0, contrib[x]))};background:${COLOR[x]}" title="${NAME[x]}"></i>`).join("") +
    (ep === "dose.contact_proxy" ? `<span class="lim" style="left:${w(LIMIT)}"><span>limit</span></span>` : "");
  $("#legend").innerHTML = `<span><i style="background:var(--weak);opacity:.5"></i>steel without impurities</span>` + IMP.map((x) => `<span><i style="background:${COLOR[x]}"></i>${NAME[x]}</span>`).join("");
  const rows = IMP.slice().sort((a, b) => Math.abs(contrib[b]) - Math.abs(contrib[a]));
  $("#contrib").innerHTML = `<tr><th>Impurity</th><th>Amount</th><th>Share of the total</th><th>Through</th></tr>` +
    rows.map((x) => `<tr><td>${NAME[x]}</td><td>${pct(wt[x])}</td><td>${(100 * contrib[x] / v).toFixed(Math.abs(contrib[x] / v) < 0.001 ? 3 : 1)}%</td><td>${Math.abs(contrib[x] / v) < 0.001 ? "–" : e.impurities[x].top.slice(0, 2).map(NICE).join(", ")}</td></tr>`).join("");
  $("#ep-cap").textContent = `The steel without impurities alone gives ${EP[ep].fmt(e.matrix_only)}. ` +
    (ep === "dose.contact_proxy" ? "The dose rate at the surface depends a little on how much the steel itself absorbs, so the sliders draw a straight line through ACTINV's numbers; over these small amounts it stays within a fraction of a percent." : "This quantity adds up exactly, element by element.");
  seenTimes.add(ti); store.set(key("times"), [...seenTimes]);
  if (seenTimes.size === 4) complete("c3-times");
  if (ti === 2 && ep === "dose.contact_proxy" && v <= LIMIT) complete("c3-under");
  if (ti === 3 && ep === "clearance_index") complete("c3-clear");
}
draw();

/* ---------- Step 4: add it up ---------- */
(() => {
  const e = data.screen[2]["dose.contact_proxy"], host = $("#sum");
  const lines = [`<span class="t">${sci(e.matrix_only, 4)}</span>  the steel without impurities`]
    .concat(IMP.map((x) => `${e.impurities[x].gradient >= 0 ? "+" : "−"} ${sci(Math.abs(e.impurities[x].gradient), 4)} × ${ranges[x].nominal}  ${NAME[x]}`))
    .concat([`= <span class="t">${e.nominal.toPrecision(8)} Gy/h</span>`]);
  host.insertAdjacentHTML("beforebegin", `<p class="note" style="margin-top:0">Each impurity line is its effect per wt% (Gy/h), times its typical amount in wt%.</p>`);
  host.innerHTML = lines.map((l) => `<div>${l}</div>`).join("");
  const show = async (quiet) => {
    $("#sum-go").disabled = true;
    for (const d of host.children) { d.classList.add("in"); if (!quiet) { sound.tick(); await wait(220); } }
    $("#check").disabled = false;
  };
  $("#sum-go").addEventListener("click", () => show(false));
  // Recorded from screen-result.json: verification.points, the nominal check at step 10.
  const V = { predicted: 0.048502452034839466, solved: 0.04850245203483947, rel_dev: 1.430627445169782e-16 };
  const check = async (quiet) => {
    $("#check").disabled = true;
    if (!quiet) { $("#check-out").textContent = "running the full calculation…"; for (let i = 0; i < 8; i++) { sound.key(); await wait(110); } sound.reveal(); }
    $("#check-out").innerHTML = `Full run of this exact steel: <strong>${V.solved} Gy/h</strong>. Added up: ${V.predicted}. They differ by ${sci(V.rel_dev, 1)} of the value, which is rounding in the last digit. ACTINV makes this check itself, for the typical steel, every corner of the certificate's ranges and every limit it reports.`;
    store.set(key("checked"), true);
    complete("c3-check");
  };
  $("#check").addEventListener("click", () => check(false));
  if (store.get(key("checked"), false)) { show(true); check(true); }
})();

/* ---------- Step 5: the file, three ways in ---------- */
const ran = (how) => { if (complete("c3-ran", { quiet: true })) { sound.reveal(); toast(`Screen run (${how}).`); } };
fileExplorer({
  pre: $("#file"), chips: $("#chips"), explain: $("#explain"), doc: screenDoc, task: "c3-file",
  shorten: {
    matrix: (v) => "{" + Object.entries(v).map(([k, x]) => `<span class="k">"${k}"</span>: <span class="n">${x}</span>`).join(", ") + "}",
    impurities: (v) => "{\n" + Object.entries(v).map(([k, x]) => `    <span class="k">"${k}"</span>: {<span class="k">"nominal"</span>: <span class="n">${x.nominal}</span>, <span class="k">"min"</span>: <span class="n">${x.min}</span>, <span class="k">"max"</span>: <span class="n">${x.max}</span>}`).join(",\n") + "\n  }",
  },
  parts: [
    { id: "base", keys: ["schema", "base_spec"], name: "The problem",
      text: "<p><code>base_spec</code> names an ordinary problem file: the bracket's field, schedule and photon settings, the same as chapter 1. ACTINV takes everything from it except the composition.</p>" },
    { id: "steel", keys: ["balance", "matrix"], name: "The steel",
      text: "<p><code>matrix</code> holds the main ingredients fixed, in wt%. <code>balance</code> names the element that makes up the rest, here iron: add an impurity and the same weight of iron goes.</p>" },
    { id: "imps", keys: ["impurities"], name: "Impurities",
      text: "<p>The certificate: each impurity's typical amount and the range it can be in. Add any element you like, even one the problem doesn't mention.</p>" },
    { id: "ask", keys: ["targets", "endpoints"], name: "When and what",
      text: "<p><code>targets</code> are step numbers of the schedule: 8 to 11 are 30 days, 1, 10 and 100 years after shutdown. <code>endpoints</code> are the quantities to screen. Give one a <code>limit</code> and ACTINV also works out the most of each impurity you can allow, as <code>limit.json</code> does in step 6.</p>" },
  ],
});
tabs(document.querySelector(".tabs"));
terminal({
  screen: $("#screen"), cue: $("#cue"), folder: "impurities-lesson", version: data.actinv_version,
  files: ["screen.json", "limit.json", "base.json", "photon-response.json", "actinv-data/"],
  script: [
    { cmd: "actinv impurities screen.json screen-result.json", task: "c3-t-screen", needs: ["screen.json", "base.json"], makes: ["screen-result.json"],
      ms: 3200, working: "one run per element, then full runs to check… (about a minute on a laptop)",
      out: "impurities: 16 element solves, 4 targets, verification passed (max rel dev 1.6412896759952994e-15) -> screen-result.json",
      hint: "Sixteen runs, every reported number checked. Next, rank the impurities for the dose rate at 10 years (step 10).", then: () => ran("command line") },
    { cmd: `jq -r '.targets[] | select(.step == 10) | .endpoints[] | select(.response == "dose.contact_proxy") | .impurities | to_entries | sort_by(-.value.contribution_nominal)[] | "\\(.key)  \\(.value.contribution_nominal)  \\(.value.top_nuclides[0][0])"' screen-result.json`,
      task: "c3-t-jq", needs: ["screen-result.json"],
      out: ["Co  0.04309134407794584  Co60", "Cu  0.00003390623631339268  Co60", "Nb  1.1266781309998697E-7  Co60", "Ag  5.8892018671430346E-8  Ag108m1",
        "B  7.749627259356906E-9  Co60", "Ta  -1.0042104615914926E-7  Co60", "N  -0.0000015435865653723457  Co60"],
      hint: "Each impurity's effect in Gy/h at typical amounts, and the nuclide it mostly acts through. Cobalt is a thousand times ahead of the rest." },
    { cmd: "actinv impurities limit.json limit-result.json", needs: ["limit.json", "base.json"], makes: ["limit-result.json"],
      ms: 3200, working: "one run per element, then full runs to check… (about a minute on a laptop)",
      out: "impurities: 16 element solves, 1 targets, verification passed (max rel dev 2.835585931921538e-10) -> limit-result.json",
      hint: "limit.json is the same screen with a limit of 0.02 Gy/h at 10 years. Step 6 shows what it found." },
    { cmd: `jq -r '.targets[0].endpoints[0].impurities | to_entries[] | "\\(.key)  \\(.value.limits.alone.status)  \\(.value.limits.alone.wt_pct // "-")"' limit-result.json`,
      needs: ["limit-result.json"],
      out: ["Ag  no response  -", "B  no response  -", "Co  limit  0.033932380215151166", "Cu  limit  43.728731197561146", "N  no response  -", "Nb  no response  -", "Ta  no response  -"],
      hint: "The most of each impurity you can allow, with the others at zero. 'no response' means that impurity can't reach the limit by itself." },
  ],
});
$("#app-done").addEventListener("click", () => { complete("c3-app"); ran("desktop app"); });
$("#py1").innerHTML = python(`import json
import subprocess

# actinv impurities has no Python function of its own; it's one command, and its result is JSON.
subprocess.run(["actinv", "impurities", "limit.json", "limit-result.json"], check=True)
result = json.load(open("limit-result.json"))

dose = result["targets"][0]["endpoints"][0]
print(f'Limit: {dose["limit"]} {dose["unit"]} after 10 years')
for name, imp in dose["impurities"].items():
    alone = imp["limits"]["alone"]
    if alone["status"] == "limit":
        print(f'{name:3} at most {alone["wt_pct"]:.3f} wt%')
    else:
        print(f'{name:3} can\\'t reach the limit on its own')`);
$("#py-run").addEventListener("click", () => replay($("#py-run"), $("#py-status"), $("#py1-out"),
  ["impurities: 16 element solves, 1 targets, verification passed (max rel dev 2.835585931921538e-10) -> limit-result.json",
    "Limit: 0.02 Gy/h after 10 years", "Ag  can't reach the limit on its own", "B   can't reach the limit on its own", "Co  at most 0.034 wt%",
    "Cu  at most 43.729 wt%", "N   can't reach the limit on its own", "Nb  can't reach the limit on its own", "Ta  can't reach the limit on its own"].join("\n"),
  () => { complete("c3-python"); ran("Python"); }));

/* ---------- Step 6: a purchase limit ---------- */
(() => {
  const s = $("#co-guess"), out = $("#co-guess-out"), L = data.limits;
  s.addEventListener("input", () => (out.textContent = pct(+s.value)));
  const reveal = (g, quiet) => {
    s.value = g; out.textContent = pct(g); s.disabled = true; $("#co-lock").disabled = true;
    const lim = L.Co.alone.wt_pct;
    $("#co-reveal").hidden = false;
    $("#co-limit").textContent = pct(lim);
    const off = g / lim;
    $("#co-text").innerHTML = `You guessed ${pct(g)}${Math.abs(off - 1) < 0.25 ? ", close" : off > 1 ? `, ${off.toFixed(1)} times too much` : ""}. ` +
      `That's about a third of the typical 0.1%. With the other impurities at their typical amounts it's ${pct(L.Co.others_at_nominal.wt_pct)}, and at their worst ${pct(L.Co.others_at_worst.wt_pct)}: the others hardly matter here. ` +
      `If you'd rather keep the mix, scaling every impurity to ${Math.round(100 * data.joint_k.value)}% of its typical amount meets the limit too.`;
    $("#limits").innerHTML = `<tr><th>Impurity</th><th>Most you can allow, others at zero</th><th>What it means</th></tr>` + IMP.map((e) => {
      const a = L[e].alone;
      return `<tr><td>${NAME[e]}</td><td>${a.status === "limit" ? pct(a.wt_pct) : "–"}</td><td>${a.status === "limit" ? (a.wt_pct > 1 ? "no real limit: that's far beyond any certificate" : "the one that sets the purchase spec") : "can't reach the limit on its own"}</td></tr>`;
    }).join("");
    if (!quiet) sound.reveal();
    complete("c3-limit", { quiet });
  };
  $("#co-lock").addEventListener("click", () => { store.set(key("co-guess"), +s.value); reveal(+s.value, false); });
  const g = store.get(key("co-guess"), null);
  if (g !== null) reveal(g, true);
})();

/* ---------- Step 7 ---------- */
quiz($("#quiz"), [
  { q: "Which impurity matters most depends on…",
    a: [["when you ask, and what you ask about", true], ["only how much of it there is", false], ["only its half-life", false]],
    why: "Cobalt rules the dose rate for the first decades; at 100 years nitrogen leads the clearance index through carbon-14. Screen every time and quantity you care about." },
  { q: "How can actinv impurities answer for every steel the certificate allows without running each one?",
    a: [["What each element turns into doesn't depend on the rest, so element runs add up", true], ["It interpolates between a few sample steels", false], ["It assumes the impurities don't matter", false]],
    why: "At a fixed neutron field the result is linear in the composition: matrix plus each impurity's effect per wt% times its amount. ACTINV then checks the numbers it reports with full runs." },
  { q: "The screen says copper can go up to 43.7% for the dose limit. What does that tell a buyer?",
    a: [["Copper isn't a concern for this limit", true], ["Buy steel with 43.7% copper", false], ["Copper is the most dangerous impurity", false]],
    why: "A limit far beyond any real certificate means that impurity doesn't constrain you. The spec only needs a tighter cobalt limit." },
], "c3-quiz");
const ALL = Object.values(STEPS).flat();
const was = ALL.every(isDone);
onProgress((set) => {
  if (ALL.every((t) => set.has(t))) {
    $("#finish-title").innerHTML = '<svg class="done-mark" viewBox="0 0 16 16" aria-hidden="true"><path d="M2.5 8.5l3.5 3.5L13.5 4"/></svg>Chapter 3 complete';
    $("#finish-text").textContent = "You screened every impurity in the steel at four times, found the one that sets the purchase spec, and turned a dose limit into a cobalt limit.";
    if (!was) sound.fanfare();
  }
});
