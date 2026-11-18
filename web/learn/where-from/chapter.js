import { boot, tabs, quiz, replay, fileExplorer, terminal, python, esc, $, sound, complete, isDone, store, toast, key } from "../kit.js";
import { onProgress, bqText, doseText } from "../learn.js";

const STEPS = {
  intro: ["c2-start"], reactions: ["c2-reactions"], guess: ["c2-guess"], routes: ["c2-routes", "c2-field"],
  run: ["c2-file", "c2-ran"], change: ["c2-change"], wrap: ["c2-quiz"],
};
const data = await fetch("data/routes-data.json").then((r) => r.json());
const problem = await fetch("files/routes.json").then((r) => r.json());

boot({
  steps: STEPS, version: data.actinv_version,
  terms: {
    route: ["Route", "One chain from an atom you started with to a nuclide you care about: Co-59 captures a neutron and becomes Co-60, for example. A nuclide is usually made by several routes at once."],
    capture: ["Neutron capture", "The nucleus absorbs the neutron and keeps it, giving off the extra energy as a gamma ray. Written (n,γ)."],
    chart: ["Chart of nuclides", "A map of every known nucleus: one row per element (number of protons), one column per number of neutrons. Reactions move you around it in fixed steps."],
    isomer: ["Isomer", "A nucleus in a long-lived excited state. It has the same protons and neutrons as the ordinary one, but extra energy, which it gives off later. Written with an m: Co-60m."],
    jq: ["jq", "A free command-line tool for reading JSON: you give it a path into the data and it prints what's there. Install it from jqlang.org or your package manager."],
  },
});

const NICE = (n) => n.replace(/^([A-Z][a-z]?)(\d+)(m1)?$/, (_, e, a, m) => `${e}-${a}${m ? "m" : ""}`);
const ELEMENT = { 22: "Ti", 23: "V", 24: "Cr", 25: "Mn", 26: "Fe", 27: "Co", 28: "Ni" };
const NAME = { Ti: "titanium", V: "vanadium", Cr: "chromium", Mn: "manganese", Fe: "iron", Co: "cobalt", Ni: "nickel" };

/* ---------- Step 2: four reactions on a piece of the chart ---------- */
(() => {
  const STABLE = { 24: [50, 52, 53, 54], 25: [55], 26: [54, 56, 57, 58], 27: [59], 28: [58, 60, 61, 62, 64] };
  const OTHER_STABLE = new Set(["Ti47", "Ti48", "Ti49", "Ti50", "V51", "V50"]);
  const HL = {
    Cr49: "42 minutes", Cr51: "27.7 days", Cr55: "3.5 minutes", Mn52: "5.6 days", Mn53: "3.7 million years", Mn54: "312 days",
    Mn56: "2.6 hours", Mn57: "1.4 minutes", Mn58: "3 seconds", Fe53: "8.5 minutes", Fe55: "2.7 years", Fe59: "44.5 days",
    Fe61: "6 minutes", Co56: "77 days", Co57: "272 days", Co58: "70.9 days", Co60: "5.27 years", Co61: "1.6 hours",
    Co62: "1.5 minutes", Co64: "0.3 seconds", Ni57: "36 hours", Ni59: "76,000 years", Ni63: "101 years", Ni65: "2.5 hours",
    V52: "3.7 minutes", V53: "1.6 minutes", V54: "50 seconds", Ti51: "5.8 minutes",
  };
  const RX = { ng: [0, 1, "(n,γ)"], np: [-1, 0, "(n,p)"], n2n: [0, -1, "(n,2n)"], na: [-2, -3, "(n,α)"] };
  const chart = $("#chart"), out = $("#rx-out");
  const N0 = 26, N1 = 36;
  let pick = null;
  const tried = new Set(store.get(key("rx"), []));
  for (let z = 28; z >= 24; z--) {
    const row = document.createElement("div"); row.className = "row";
    row.innerHTML = `<span class="zl">${ELEMENT[z]}</span>`;
    for (let n = N0; n <= N1; n++) {
      const a = z + n, cell = document.createElement("div");
      cell.className = "cell"; cell.dataset.z = z; cell.dataset.a = a;
      if (STABLE[z].includes(a)) {
        cell.classList.add("stable"); cell.textContent = `${ELEMENT[z]}-${a}`; cell.tabIndex = 0; cell.setAttribute("role", "button");
        const choose = () => {
          pick = [z, a]; sound.tick();
          chart.querySelectorAll(".stable").forEach((c) => c.classList.toggle("on", c === cell));
          chart.querySelectorAll(".made").forEach((c) => { c.classList.remove("made", "radio"); if (!c.classList.contains("stable")) c.textContent = ""; });
          out.textContent = `${ELEMENT[z]}-${a} picked. Now choose a reaction.`;
        };
        cell.addEventListener("click", choose);
        cell.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); choose(); } });
      }
      row.append(cell);
    }
    chart.append(row);
  }
  const nl = document.createElement("div"); nl.className = "row";
  nl.innerHTML = `<span></span>` + Array.from({ length: N1 - N0 + 1 }, (_, i) => `<span class="nl">${N0 + i}</span>`).join("");
  chart.append(nl);
  chart.insertAdjacentHTML("afterend", `<p class="cap" style="margin-top:0">Columns are the number of neutrons in the nucleus. Element-mass, e.g. Fe-54: iron with 54 protons and neutrons in all.</p>`);
  document.querySelectorAll("[data-rx]").forEach((b) => b.addEventListener("click", () => {
    if (!pick) { out.textContent = "Pick a grey square first."; sound.wrong(); return; }
    const [dz, da, label] = RX[b.dataset.rx];
    const z = pick[0] + dz, a = pick[1] + da, el = ELEMENT[z], name = `${el}${a}`;
    const stable = (STABLE[z] || []).includes(a) || OTHER_STABLE.has(name);
    chart.querySelectorAll(".made").forEach((c) => { c.classList.remove("made", "radio"); if (!c.classList.contains("stable")) c.textContent = ""; });
    const cell = chart.querySelector(`.cell[data-z="${z}"][data-a="${a}"]`);
    if (cell) { cell.classList.add("made"); if (!stable) { cell.classList.add("radio"); cell.textContent = `${el}-${a}`; } }
    const from = `${ELEMENT[pick[0]]}-${pick[1]}`;
    out.innerHTML = `${from} ${label} → <strong>${el}-${a}</strong>, ${NAME[el]}: ` + (stable ? "stable, so nothing more happens."
      : `radioactive, with a half-life of ${HL[name] || "under a second"}.`) + (cell ? "" : " (Off this piece of the chart.)");
    stable ? sound.tick() : sound.click(0.5);
    tried.add(b.dataset.rx); store.set(key("rx"), [...tried]);
    if (tried.size === 4) complete("c2-reactions");
  }));
})();

/* ---------- Step 3: guesses ---------- */
const GUESS = ["Co60", "Co58", "Mn54", "Cr51"];
const CHOICES = ["Fe", "Cr", "Ni", "Mn", "Co"];
const bySource = (target, kase = "cobalt") => {
  const out = {};
  for (const r of data.cases[kase].targets[target].routes) {
    const el = r.nuclides[0].match(/^[A-Z][a-z]?/)[0];
    out[el] = (out[el] || 0) + r.fraction;
  }
  return out;
};
(() => {
  const host = $("#guesses"), lock = $("#lock");
  const saved = store.get(key("guess"), {});
  const locked = () => store.get(key("guess-locked"), false);
  GUESS.forEach((t) => {
    const row = document.createElement("div"); row.className = "guess-row";
    row.innerHTML = `<span class="who">${NICE(t)}</span><span class="controls" style="margin:0">${CHOICES.map((c) => `<button type="button" class="sel" data-el="${c}">${NAME[c]}</button>`).join("")}</span><span class="verdict"></span>`;
    row.querySelectorAll("[data-el]").forEach((b) => b.addEventListener("click", () => {
      if (locked()) return;
      saved[t] = b.dataset.el; store.set(key("guess"), saved); sound.tick(); draw();
    }));
    host.append(row);
  });
  function draw() {
    host.querySelectorAll(".guess-row").forEach((row, i) => {
      const t = GUESS[i], g = saved[t];
      row.querySelectorAll("[data-el]").forEach((b) => b.classList.toggle("on", b.dataset.el === g));
      if (locked()) {
        const src = bySource(t), best = Object.entries(src).sort((a, b) => b[1] - a[1])[0][0];
        row.querySelectorAll("[data-el]").forEach((b) => { b.disabled = true; b.classList.toggle("right", b.dataset.el === best); b.classList.toggle("miss", b.dataset.el === g && g !== best); });
        const parts = Object.entries(src).sort((a, b) => b[1] - a[1]).map(([e, f]) => `${NAME[e]} ${Math.round(f * 100)}%`).join(", ");
        row.querySelector(".verdict").textContent = (g === best ? "Right. " : "") + `Made from ${parts}.`;
      }
    });
    const n = GUESS.filter((t) => saved[t]).length;
    lock.disabled = locked() || n < GUESS.length;
    $("#guess-note").textContent = locked() ? `You got ${GUESS.filter((t) => { const s = bySource(t); return Object.entries(s).sort((a, b) => b[1] - a[1])[0][0] === saved[t]; }).length} of 4. Step 4 shows each route.` : n < 4 ? "Pick an element for each." : "Ready.";
  }
  lock.addEventListener("click", () => { store.set(key("guess-locked"), true); sound.reveal(); complete("c2-guess"); draw(); });
  draw();
})();

/* ---------- Step 4: route explorer ---------- */
(() => {
  const ORDER = ["Co60", "Co58", "Mn54", "Cr51", "Fe55", "Co57", "Fe59", "Mn56"];
  const RXNAME = { "(n,g)": "(n,γ)", "(n,a)/(n,nh)/(n,pt)": "(n,α)", "(n,d)/(n,np)": "(n,d) or (n,np)", "(n,p)/b+/EC": "β+ / EC", IT: "settles (IT)" };
  let target = "Co60", kase = "cobalt";
  const seen = new Set(store.get(key("seen-targets"), []));
  const host = $("#targets");
  ORDER.forEach((t) => host.insertAdjacentHTML("beforeend", `<button type="button" class="sel" data-t="${t}">${NICE(t)}</button>`));
  host.querySelectorAll("[data-t]").forEach((b) => b.addEventListener("click", () => { target = b.dataset.t; sound.tick(); draw(); }));
  document.querySelectorAll("[data-case]").forEach((b) => b.addEventListener("click", () => {
    kase = b.dataset.case; sound.tick();
    document.querySelectorAll("[data-case]").forEach((x) => x.classList.toggle("on", x === b));
    draw();
  }));
  function draw() {
    host.querySelectorAll("[data-t]").forEach((b) => b.classList.toggle("on", b.dataset.t === target));
    const c = data.cases[kase], t = c.targets[target];
    $("#route-title").textContent = `${NICE(target)}: ${bqText(t.activity_Bq)}, ${Math.round(100 * t.dose_Gy_h / c.total_dose_Gy_h)}% of the dose rate one day after shutdown`;
    $("#route-list").innerHTML = t.routes.map((r) => {
      const chain = r.nuclides.map((n, i) => (i ? ` <i>→${esc(RXNAME[r.edges[i - 1]] || r.edges[i - 1])}→</i> ` : "") + NICE(n)).join("");
      return `<div class="route"><span class="chain">${chain}</span><span class="bar"><i style="width:${(100 * r.fraction).toFixed(1)}%"></i></span><span class="pc">${(100 * r.fraction).toFixed(r.fraction < 0.1 ? 1 : 0)}%</span></div>`;
    }).join("") || `<p class="note">No route above 0.5% in this case.</p>`;
    const notes = {
      no_cobalt: "Without cobalt in the steel, only the nickel routes are left for cobalt nuclides.",
      fast_only: "With only 14 MeV neutrons, capture routes (n,γ) all but vanish: they need slow neutrons. Reactions that need fast neutrons, like (n,p) and (n,2n), take over. This field has four times more 14 MeV neutrons than chapter 1's, so those reactions make more.",
      cobalt: "The routes shown carry at least 0.5% of the nuclide each.",
    };
    $("#route-cap").textContent = `Together these routes explain ${(100 * t.explained).toFixed(1)}% of the ${NICE(target)} ACTINV computed. ` + notes[kase];
    seen.add(target); store.set(key("seen-targets"), [...seen]);
    if (seen.size >= 4) complete("c2-routes");
    if (kase === "fast_only" && target === "Co60") complete("c2-field");
  }
  draw();
})();

/* ---------- Step 5: the file, three ways in ---------- */
const ran = (how) => { if (complete("c2-ran", { quiet: true })) { sound.reveal(); toast(`Routes computed (${how}).`); } };
fileExplorer({
  pre: $("#file"), chips: $("#chips"), explain: $("#explain"), doc: problem, task: "c2-file",
  shorten: {
    flux_per_group: (v) => `[${v.slice(0, 3).join(", ")}, <span class="c">… ${v.length - 3} more numbers …</span>]`,
    schedule: (v) => `[ <span class="c">… ${v.length} steps: two years on, then cooling to 1 day …</span> ]`,
    composition: (v) => "{" + Object.entries(v).map(([k, x]) => `<span class="k">"${k}"</span>: <span class="n">${x}</span>`).join(", ") + "}",
  },
  parts: [
    { id: "about", keys: ["spec", "title", "projectile", "library", "decay", "fission_yields"], name: "Data and title",
      text: "<p>The same nuclear data as chapter 1, pinned by fingerprint. Nothing here changes for routes.</p>" },
    { id: "material", keys: ["material"], name: "Material", text: "<p>The bracket: 20 kg of SS316 with 0.1% cobalt, which replaces the same weight of iron.</p>" },
    { id: "field", keys: ["spectrum", "schedule"], name: "Field and schedule",
      text: "<p>Chapter 1's neutron field and two years of irradiation, but the cooling stops at one day: routes are worked out for the last step of the schedule, so the problem ends at the moment you're asking about.</p>" },
    { id: "outputs", keys: ["options", "photon", "summary", "uncertainty", "radiological", "self_shielding"], name: "Other outputs",
      text: "<p>As in chapter 1: the photons and the dose rate 1 m away.</p>" },
    { id: "pathways", keys: ["pathways"], name: "pathways",
      text: `<p>The new block. <code>targets</code> lists the nuclides to trace; leave it empty and ACTINV picks the ones that dominate. <code>path_floor</code> 0.005 keeps every route that carries at least 0.5% of its nuclide, and <code>max_paths</code> caps how many are listed per nuclide.</p><p>Everything else has a sensible default: how long a chain can be, where to start (every atom in the material), and how to treat loops.</p>` },
  ],
});
tabs(document.querySelector(".tabs"));
terminal({
  screen: $("#screen"), cue: $("#cue"), folder: "routes-lesson", version: data.actinv_version,
  files: ["routes.json", "photon-response.json", "actinv-data/"],
  script: [
    { cmd: "actinv validate routes.json", task: "c2-t-validate", needs: ["routes.json"], out: "ok (--schema): actinv-spec-1 — 709 groups, 14 steps" },
    { cmd: "actinv run routes.json routes-result.json", task: "c2-t-run", needs: ["routes.json"], makes: ["routes-result.json"], ms: 1200, working: "solving 14 steps and tracing routes…",
      out: "14 steps, 283 of 3873 states, 1169.5 ms -> routes-result.json",
      hint: "The routes are in the result under pathway_routes. Next, read cobalt-60's with jq.", then: () => ran("command line") },
    { cmd: `jq -c '.pathway_routes.targets[] | select(.target == "Co60") | .paths[] | [.nuclides, .edges, .fraction]' routes-result.json`,
      task: "c2-t-jq", needs: ["routes-result.json"],
      out: ['[["Co59","Co60"],["(n,g)"],0.6579698860815536]', '[["Co59","Co60m1","Co60"],["(n,g)","IT"],0.23160449955631796]',
        '[["Ni60","Co60m1","Co60"],["(n,p)","IT"],0.06341052410927613]', '[["Ni60","Co60"],["(n,p)"],0.046218605809839726]'],
      hint: "Each line is one route: the nuclides along it, the reactions or decays between them, and its share. Co-59 (the cobalt) makes 89%, Ni-60 11%. Now one line per nuclide:" },
    { cmd: `jq -r '.pathway_routes.targets[] | "\\(.target)  \\(.paths[0].nuclides | join(" -> "))  \\(.paths[0].edges | join(", "))  \\(.paths[0].fraction * 100 | floor)%"' routes-result.json`,
      needs: ["routes-result.json"],
      out: ["Mn56  Mn55 -> Mn56  (n,g)  63%", "Cr51  Cr50 -> Cr51  (n,g)  67%", "Fe55  Fe56 -> Fe55  (n,2n)  70%", "Co58  Ni58 -> Co58  (n,p)  49%",
        "Mn54  Fe54 -> Mn54  (n,p)  63%", "Co60  Co59 -> Co60  (n,g)  65%", "Fe59  Fe58 -> Fe59  (n,g)  94%", "Co57  Ni58 -> Co57  (n,d)/(n,np)  96%"],
      hint: "The biggest route of each nuclide. Any program that reads JSON can do the same." },
  ],
});
$("#app-done").addEventListener("click", () => { complete("c2-app"); ran("desktop app"); });
$("#py1").innerHTML = python(`from actinv import Problem, solve

problem = Problem.from_file("routes.json")
result = solve(problem)

for target in result["pathway_routes"]["targets"]:
    top = target["paths"][0]                      # the biggest route
    chain = " -> ".join(top["nuclides"])
    print(f'{target["target"]:5}  {top["fraction"]:4.0%} from {chain}  {", ".join(top["edges"])}')`);
$("#py-run").addEventListener("click", () => replay($("#py-run"), $("#py-status"), $("#py1-out"),
  ["Mn56    63% from Mn55 -> Mn56  (n,g)", "Cr51    67% from Cr50 -> Cr51  (n,g)", "Fe55    70% from Fe56 -> Fe55  (n,2n)",
    "Co58    50% from Ni58 -> Co58  (n,p)", "Mn54    63% from Fe54 -> Mn54  (n,p)", "Co60    66% from Co59 -> Co60  (n,g)",
    "Fe59    95% from Fe58 -> Fe59  (n,g)", "Co57    96% from Ni58 -> Co57  (n,d)/(n,np)"].join("\n"),
  () => { complete("c2-python"); ran("Python"); }));

/* ---------- Step 6: levers ---------- */
(() => {
  const LEVERS = [
    ["Co60", true, "Yes. 89% comes from the 0.1% cobalt. Buy steel with less cobalt and most of it goes; the nickel route (11%) stays."],
    ["Co58", false, "Not by an impurity limit: 98% comes from nickel-58, and the 12% nickel is what makes this steel stainless. Only a different alloy would change it. It halves every 71 days, so waiting works."],
    ["Mn54", false, "No: 63% from iron-54 and 37% from manganese-55, both main ingredients. Every steel makes it."],
    ["Cr51", false, "No: it comes from the chromium, 17% of the steel. But it lasts weeks, not years: waiting a few months removes it."],
  ];
  const host = $("#levers"), answers = store.get(key("levers"), {});
  LEVERS.forEach(([t, yes, why]) => {
    const row = document.createElement("div"); row.className = "guess-row";
    row.innerHTML = `<span class="who">${NICE(t)}</span><span class="controls" style="margin:0"><button type="button" class="sel" data-v="yes">Yes, limit an impurity</button><button type="button" class="sel" data-v="no">No, it's the alloy itself</button></span><span class="verdict"></span>`;
    const show = (v) => {
      row.querySelectorAll("[data-v]").forEach((b) => { b.classList.toggle("on", b.dataset.v === v); b.classList.toggle("miss", b.dataset.v === v && (v === "yes") !== yes); });
      row.querySelector(".verdict").textContent = ((v === "yes") === yes ? "Right. " : "Not quite. ") + why;
    };
    row.querySelectorAll("[data-v]").forEach((b) => b.addEventListener("click", () => {
      answers[t] = b.dataset.v; store.set(key("levers"), answers);
      (b.dataset.v === "yes") === yes ? sound.done() : sound.wrong();
      show(b.dataset.v);
      if (LEVERS.every(([x]) => answers[x])) complete("c2-change");
    }));
    if (answers[t]) show(answers[t]);
    host.append(row);
  });
})();

/* ---------- Step 7 ---------- */
quiz($("#quiz"), [
  { q: "The bracket has no cobalt at all. Where does its cobalt-60 come from?",
    a: [["Nickel-60, by an (n,p) reaction", true], ["Iron, by neutron capture", false], ["It can't have any", false]],
    why: "Ni-60 (n,p) makes Co-60 directly and through Co-60m. With no cobalt in the steel, that's all of it, about a tenth of what 0.1% cobalt makes." },
  { q: "In a field of only fast 14 MeV neutrons, why does cobalt make so little cobalt-60?",
    a: [["Capture, (n,γ), mostly needs slow neutrons", true], ["Fast neutrons pass through cobalt", false], ["Cobalt-60 decays faster in a fast field", false]],
    why: "Capture is far more likely for slow neutrons. Where nothing slows the neutrons down, the threshold reactions on nickel are left. The same steel can give a different answer in a different place in the machine." },
  { q: "Manganese-54 dominates part of the dose. Why won't a purchase specification fix it?",
    a: [["It's made from iron and manganese, the steel's main ingredients", true], ["Its half-life is too short to matter", false], ["ACTINV can't trace it", false]],
    why: "Its routes start at Fe-54 and Mn-55. Routes tell you which atoms to blame, and so which ones you can actually do something about." },
], "c2-quiz");
const ALL = Object.values(STEPS).flat();
const was = ALL.every(isDone);
onProgress((set) => {
  if (ALL.every((t) => set.has(t))) {
    $("#finish-title").innerHTML = '<svg class="done-mark" viewBox="0 0 16 16" aria-hidden="true"><path d="M2.5 8.5l3.5 3.5L13.5 4"/></svg>Chapter 2 complete';
    $("#finish-text").textContent = "You traced every radioactive nuclide in the bracket to the atom that made it, and sorted out which ones a purchase specification can change.";
    if (!was) sound.fanfare();
  }
});
