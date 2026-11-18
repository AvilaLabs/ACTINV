import { boot, tabs, quiz, replay, fileExplorer, terminal, python, $, sound, complete, isDone, store, toast, key, predict, wait } from "../kit.js";
import { onProgress, plot, svg, reducedMotion } from "../learn.js";

const STEPS = {
  intro: ["c4-start"], guess: ["c4-guess"], wait: ["c4-wait"], cost: ["c4-persv"],
  run: ["c4-file", "c4-ran"], cobalt: ["c4-cobalt"], wrap: ["c4-quiz"],
};
const data = await fetch("data/maintenance-data.json").then((r) => r.json());
const planDoc = await fetch("files/plan.json").then((r) => r.json());

boot({
  steps: STEPS, version: data.actinv_version,
  terms: {
    access: ["Access limit", "The highest dose rate at which a crew may enter to work. Set by the site; 3 mSv/h is an example. Above it, the job waits."],
    personsv: ["Person-sievert", "The dose of everyone in a crew added up: three people getting 10 mSv each is 30 person-mSv. It measures the total harm a job does, and is what a money value on dose is applied to."],
  },
});

const DAY = 86400;
const PLAN = data.plan;
const LIM = PLAN.access_limit_Sv_h, IND = PLAN.individual_limit_Sv;
const fmtD = (d) => d < 1.5 ? `${d.toFixed(1)} day` : `${Math.round(d)} days`;
const msv = (sv) => (sv * 1000).toFixed(sv < 0.01 ? 2 : 1);
const money = (x) => Math.round(x).toLocaleString("en-US");

$("#jobs-list").innerHTML = `<tr><th>Job</th><th>Where</th><th>Hours</th><th>Crew</th></tr>` +
  PLAN.jobs.map((j) => `<tr><td>${j.name}</td><td>${j.location === "pipe" ? "copper pipe" : "bracket"}</td><td>${j.duration_h}</td><td>${j.crew}</td></tr>`).join("");

/* ---------- Step 2 ---------- */
const EARLIEST = data.plans["cobalt|1e+06"].earliest_wait_d;
predict($("#guess-opts"), "guess", (a, quiet) => {
  $("#guess-out").innerHTML = (a === "months" ? "<b>Right.</b> " : "") +
    `ACTINV's answer is <b>${Math.round(EARLIEST)} days</b>, about four and a half months. The pipe could be cut on day one; the bracket holds everything up. Step 3 shows why.`;
  if (!quiet) { sound.reveal(); }
  complete("c4-guess", { quiet });
});

/* ---------- Step 3: dose-rate curves and a wait slider ---------- */
const curves = Object.fromEntries(Object.entries(data.curves).map(([k, c]) => [k, c.map(([t, v]) => [t / DAY, v])]));
function rate(name, d) {
  const c = curves[name];
  if (d <= c[0][0]) return c[0][1];
  for (let i = 1; i < c.length; i++) if (c[i][0] >= d) {
    const [t0, v0] = c[i - 1], [t1, v1] = c[i], f = Math.log(d / t0) / Math.log(t1 / t0);
    return Math.exp(Math.log(v0) + f * Math.log(v1 / v0));
  }
  return c[c.length - 1][1];
}
// The page works out each job's dose from the curves as actinv maintenance does: jobs one after another from the
// wait, each worker's dose the dose rate integrated over the job, the crew's that times the crew.
function schedule(waitD, bracket = "bracket") {
  let t = waitD;
  const jobs = PLAN.jobs.map((j) => {
    const place = j.location === "pipe" ? "pipe" : bracket, dur = j.duration_h / 24, n = 24;
    let dose = 0, peak = 0;
    for (let k = 0; k < n; k++) { const r = rate(place, t + (k + 0.5) * dur / n); dose += r * j.duration_h / n; peak = Math.max(peak, r); }
    peak = Math.max(peak, rate(place, t));
    const out = { ...j, start: t, individual: dose, collective: dose * j.crew, ok: peak <= LIM };
    t += dur;
    return out;
  });
  const feasible = jobs.every((j) => j.ok && j.individual <= IND);
  return { jobs, feasible, collective: jobs.reduce((s, j) => s + j.collective, 0) };
}
(() => {
  const p = plot($("#rateplot"), {
    height: 300, margin: { l: 90 }, label: "Dose rate at 1 m from each part after shutdown",
    x: { domain: [1, 730], log: true, ticks: [[1, "1 d"], [7, "1 wk"], [30, "1 mo"], [91, "3 mo"], [182, "6 mo"], [365, "1 y"], [730, "2 y"]], narrow: [[1, "1 d"], [30, "1 mo"], [365, "1 y"]], title: "wait after shutdown" },
    y: { domain: [1e-4, 1e-2], log: true, fmt: (v) => v >= 1e-3 ? `${v * 1000} mSv/h` : `${v * 1e6} µSv/h`, title: "dose rate at 1 m" },
  });
  svg("rect", { x: p.x(1), y: p.y(1e-2), width: p.x(EARLIEST) - p.x(1), height: p.y(1e-4) - p.y(1e-2), class: "zone" }, p.layer);
  svg("line", { x1: p.x(1), x2: p.x(730), y1: p.y(LIM), y2: p.y(LIM), class: "limit" }, p.layer);
  svg("text", { x: p.x(730) - 4, y: p.y(LIM) - 6, "text-anchor": "end", class: "limit-label" }, p.layer).textContent = "access limit 3 mSv/h";
  const sel = (n) => curves[n].filter(([d]) => d >= 1 && d <= 730);
  for (const [n, cls] of [["bracket-no-cobalt", "s-noco"], ["pipe", "s-pipe"], ["bracket", "ink"]])
    p.line(sel(n).map((x) => x[0]), sel(n).map((x) => x[1]), { class: cls }, { animate: true });
  const cursor = svg("line", { y1: p.m.t, y2: p.H - p.m.b, class: "cursor" }, p.top);
  const dotB = svg("circle", { r: 5, fill: "var(--accent)", class: "mark" }, p.top);
  const dotP = svg("circle", { r: 5, fill: "#2bb39a", class: "mark" }, p.top);
  const s = $("#wait-s");
  const toD = (v) => Math.exp(Math.log(1) + v / 1000 * Math.log(365));
  let lastOk = null;
  function draw() {
    const d = toD(+s.value), plan = schedule(d);
    $("#wait-out").textContent = fmtD(d);
    cursor.setAttribute("x1", p.x(d)); cursor.setAttribute("x2", p.x(d));
    dotB.setAttribute("cx", p.x(d)); dotB.setAttribute("cy", p.y(rate("bracket", d)));
    dotP.setAttribute("cx", p.x(d)); dotP.setAttribute("cy", p.y(rate("pipe", d)));
    $("#wait-jobs").innerHTML = `<tr><th>Job</th><th>Allowed?</th><th>Each worker</th><th>Whole crew</th></tr>` +
      plan.jobs.map((j) => `<tr><td>${j.name}</td><td class="${j.ok ? "" : "no"}">${j.ok ? "yes" : "no, above 3 mSv/h"}</td><td class="${j.individual > IND ? "no" : ""}">${msv(j.individual)} mSv</td><td>${msv(j.collective)} mSv</td></tr>`).join("") +
      `<tr><td>All three</td><td></td><td></td><td>${msv(plan.collective)} mSv</td></tr>`;
    const v = $("#wait-verdict");
    v.classList.toggle("no", !plan.feasible);
    v.textContent = plan.feasible ? `After ${fmtD(d)}, every job is allowed and every worker stays under 20 mSv.`
      : `Not yet: ${plan.jobs.filter((j) => !j.ok).map((j) => j.name).join(" and ")} ${plan.jobs.filter((j) => !j.ok).length > 1 ? "are" : "is"} still above the access limit.`;
    if (lastOk !== null && plan.feasible !== lastOk) plan.feasible ? sound.done() : sound.tick();
    lastOk = plan.feasible;
    if (plan.feasible && d < EARLIEST + 12) complete("c4-wait");
  }
  s.addEventListener("input", draw);
  draw();
})();

/* ---------- Step 4: the cost of waiting ---------- */
const PERSV = data.per_sv;
let perSv = 1e6, co = "cobalt";
const triedSv = new Set(store.get(key("persv"), []));
(() => {
  const host = $("#persv");
  PERSV.forEach((v) => host.insertAdjacentHTML("beforeend", `<button type="button" class="sel${v === perSv ? " on" : ""}" data-v="${v}">${money(v)}</button>`));
  host.querySelectorAll("[data-v]").forEach((b) => b.addEventListener("click", () => {
    perSv = +b.dataset.v; sound.tick(); host.querySelectorAll("[data-v]").forEach((x) => x.classList.toggle("on", x === b)); draw();
  }));
  document.querySelectorAll("[data-co]").forEach((b) => b.addEventListener("click", () => {
    co = b.dataset.co; sound.tick(); document.querySelectorAll("[data-co]").forEach((x) => x.classList.toggle("on", x === b)); draw();
  }));
  let p = null;
  function draw() {
    const plan = data.plans[`${co}|${perSv < 1e6 ? String(perSv) : perSv.toExponential().replace(/e\+(\d)$/, "e+0$1")}`];
    const pts = plan.waits.map((w) => ({ d: w.wait_d, total: w.cost, dose: w.collective_Sv * perSv, out: w.outage_d * data.per_day, ok: w.feasible }))
      .concat([{ d: plan.best.wait_d, total: plan.best.cost, dose: plan.best.collective_Sv * perSv, out: plan.best.outage_d * data.per_day, ok: true }])
      .sort((a, b) => a.d - b.d);
    const ymax = Math.max(...pts.map((x) => x.total)) * 1.1;
    $("#costplot").innerHTML = "";
    p = plot($("#costplot"), {
      height: 280, margin: { l: 90 }, label: "Cost of each wait",
      x: { domain: [1, 365], log: true, ticks: [[1, "1 d"], [7, "1 wk"], [30, "1 mo"], [91, "3 mo"], [182, "6 mo"], [365, "1 y"]], narrow: [[1, "1 d"], [30, "1 mo"], [365, "1 y"]], title: "wait after shutdown" },
      y: { domain: [0, ymax], ticks: niceTicks(ymax), fmt: (v) => v >= 1e6 ? `${+(v / 1e6).toFixed(1)}M` : `${Math.round(v / 1e3)}k`, title: "cost" },
    });
    svg("rect", { x: p.x(1), y: p.m.t, width: p.x(plan.earliest_wait_d) - p.x(1), height: p.H - p.m.b - p.m.t, class: "zone" }, p.layer);
    svg("text", { x: p.x(1) + 6, y: p.m.t + 14, class: "limit-label" }, p.layer).textContent = "not allowed yet";
    p.line(pts.map((x) => x.d), pts.map((x) => x.out), { class: "s-out" });
    p.line(pts.map((x) => x.d), pts.map((x) => x.dose), { class: "s-dose" });
    p.line(pts.map((x) => x.d), pts.map((x) => x.total), { class: "ink" }, { animate: true });
    const b = plan.best;
    svg("circle", { cx: p.x(b.wait_d), cy: p.y(b.cost), r: 6, fill: "var(--accent)", class: "mark" }, p.top);
    svg("text", { x: p.x(b.wait_d), y: p.y(b.cost) - 12, "text-anchor": "middle", class: "landmark" }, p.top).textContent = "cheapest allowed";
    const later = b.wait_d > plan.earliest_wait_d + 0.5;
    $("#best-out").textContent = `Best: wait ${fmtD(b.wait_d)}. Crew dose ${msv(b.collective_Sv)} person-mSv, total cost ${money(b.cost)}.`;
    $("#cost-cap").textContent = later
      ? `At ${money(perSv)} per person-sievert, waiting ${Math.round(b.wait_d - plan.earliest_wait_d)} days beyond the earliest allowed day (${fmtD(plan.earliest_wait_d)}) pays for itself in dose saved.`
      : `At ${money(perSv)} per person-sievert, the cheapest allowed wait is the earliest one: each extra day of outage costs more than the dose it saves.`;
    triedSv.add(perSv); store.set(key("persv"), [...triedSv]);
    if (triedSv.size === PERSV.length) complete("c4-persv");
  }
  draw();
})();
function niceTicks(max) {
  const step = 10 ** Math.floor(Math.log10(max / 4)), m = [1, 2, 5, 10].find((k) => max / (k * step) <= 6) * step;
  return Array.from({ length: Math.floor(max / m) + 1 }, (_, i) => i * m);
}

/* ---------- Step 5: the file, three ways in ---------- */
const ran = (how) => { if (complete("c4-ran", { quiet: true })) { sound.reveal(); toast(`Plan made (${how}).`); } };
fileExplorer({
  pre: $("#file"), chips: $("#chips"), explain: $("#explain"), doc: planDoc, task: "c4-file",
  shorten: {
    jobs: (v) => "[\n" + v.map((j) => `    {<span class="k">"name"</span>: <span class="s">"${j.name}"</span>, <span class="k">"location"</span>: <span class="s">"${j.location}"</span>, <span class="k">"duration_h"</span>: <span class="n">${j.duration_h}</span>, <span class="k">"crew"</span>: <span class="n">${j.crew}</span>}`).join(",\n") + "\n  ]",
    dose_rates: (v) => `{<span class="k">"runs"</span>: [\n` + v.runs.map((r) => `    {<span class="k">"result"</span>: <span class="s">"${r.result}"</span>, <span class="k">"location"</span>: <span class="s">"${r.location}"</span>}`).join(",\n") + "\n  ]}",
  },
  parts: [
    { id: "rates", keys: ["schema", "dose_rates"], name: "Dose rates",
      text: "<p>Where the dose rates come from: the result of an ordinary <code>actinv run</code> for each place, given a name. The planner uses each result's dose rate 1 m away at every cooling step. You can give a table of dose rates instead, from a measurement or another calculation.</p>" },
    { id: "limits", keys: ["access_limit_Sv_h", "individual_limit_Sv"], name: "Limits",
      text: "<p>The access limit, in Sv/h, and the most any one worker may receive from the whole outage, in Sv. Your site sets both.</p>" },
    { id: "jobs", keys: ["jobs"], name: "Jobs",
      text: "<p>The jobs in the order they're done: where each happens, how long it takes and how many people it needs. Each starts when the one before ends.</p>" },
    { id: "waits", keys: ["waits_d", "cost"], name: "Waits and costs",
      text: "<p><code>waits_d</code> are the waits after shutdown, in days, to tabulate. <code>cost</code> is optional: the value on one person-sievert and the cost of a day of outage. With it, ACTINV also finds the cheapest allowed wait.</p>" },
  ],
});
tabs(document.querySelector(".tabs"));
terminal({
  screen: $("#screen"), cue: $("#cue"), folder: "maintenance-lesson", version: data.actinv_version,
  files: ["bracket.json", "pipe.json", "plan.json", "photon-response.json", "actinv-data/"],
  script: [
    { cmd: "actinv run bracket.json bracket-result.json", needs: ["bracket.json"], makes: ["bracket-result.json"], ms: 1000, working: "solving 41 steps…",
      out: "41 steps, 318 of 3873 states, 926.9 ms -> bracket-result.json", hint: "Now the pipe: actinv run pipe.json pipe-result.json" },
    { cmd: "actinv run pipe.json pipe-result.json", task: "c4-t-runs", needs: ["pipe.json"], makes: ["pipe-result.json"], ms: 1000, working: "solving 41 steps…",
      out: "41 steps, 73 of 3873 states, 1205.3 ms -> pipe-result.json", hint: "Both places have dose rates. Now the plan." },
    { cmd: "actinv maintenance plan.json plan-result.json", task: "c4-t-plan", needs: ["plan.json", "bracket-result.json", "pipe-result.json"], makes: ["plan-result.json"],
      out: [], hint: "No news is good news: like most command-line tools, it prints nothing when it succeeds. The plan is in plan-result.json.", then: () => ran("command line") },
    { cmd: `jq -r '.alone[] | "\\(.name)  from day \\(.earliest_start_d | floor)"' plan-result.json`, needs: ["plan-result.json"],
      out: ["cut out the cooling pipe  from day 0", "unbolt the bracket  from day 136", "inspect the bracket's mounting  from day 136"],
      hint: "When each job could start on its own: the bracket's jobs set the date." },
    { cmd: `jq -r '.best | "wait \\(.wait_d | floor) days, \\(.collective_Sv * 1000 | floor) person-mSv, cost \\(.cost | floor)"' plan-result.json`,
      task: "c4-t-jq", needs: ["plan-result.json"], out: ["wait 135 days, 63 person-mSv, cost 337146"],
      hint: "The cheapest allowed plan at one million per person-sievert, as in step 4." },
  ],
});
$("#app-done").addEventListener("click", () => { complete("c4-app"); ran("desktop app"); });
$("#py1").innerHTML = python(`import json
import subprocess

# Plan at four values put on a person-sievert; the plan is JSON, so change it in Python and write it back.
plan = json.load(open("plan.json"))
for per_sv in [2e5, 1e6, 5e6, 2e7]:
    plan["cost"]["per_person_Sv"] = per_sv
    json.dump(plan, open("plan-try.json", "w"))
    subprocess.run(["actinv", "maintenance", "plan-try.json", "plan-try-result.json"], check=True)
    best = json.load(open("plan-try-result.json"))["best"]
    print(f'{per_sv:9,.0f} per person-Sv: wait {best["wait_d"]:5.1f} days, crew dose {best["collective_Sv"] * 1000:4.1f} mSv')`);
$("#py-run").addEventListener("click", () => replay($("#py-run"), $("#py-status"), $("#py1-out"),
  ["  200,000 per person-Sv: wait 135.9 days, crew dose 64.0 mSv", "1,000,000 per person-Sv: wait 135.9 days, crew dose 64.0 mSv",
    "5,000,000 per person-Sv: wait 135.9 days, crew dose 64.0 mSv", "20,000,000 per person-Sv: wait 179.7 days, crew dose 57.8 mSv"].join("\n"),
  () => { complete("c4-python"); ran("Python"); }));

/* ---------- Step 6: cobalt ---------- */
(() => {
  const a = data.plans["cobalt|1e+06"], b = data.plans["no_cobalt|1e+06"];
  const show = async (quiet) => {
    $("#cmp-go").disabled = true;
    const fill = (el, t, plan) => { $(el).textContent = fmtD(plan.best.wait_d); $(t).textContent = `${msv(plan.best.collective_Sv)} person-mSv, cost ${money(plan.best.cost)}`; };
    fill("#cmp-co", "#cmp-co-t", a); if (!quiet) { sound.tick(); await wait(500); }
    fill("#cmp-no", "#cmp-no-t", b); if (!quiet) { sound.tick(); await wait(500); sound.reveal(); }
    $("#cmp-out").textContent = `0.1% cobalt holds the crew back ${Math.round(a.best.wait_d - b.best.wait_d)} more days and costs ${money(a.best.cost - b.best.cost)} more. Chapter 3's cobalt limit pays off here too.`;
    store.set(key("cmp"), true);
    complete("c4-cobalt", { quiet });
  };
  $("#cmp-go").addEventListener("click", () => show(false));
  if (store.get(key("cmp"), false)) show(true);
})();

/* ---------- Step 7 ---------- */
quiz($("#quiz"), [
  { q: "The pipe could be cut on day one, but the bracket only after 136 days. What sets the outage?",
    a: [["The slowest part to cool: the bracket", true], ["The average of the two", false], ["The pipe, because it's done first", false]],
    why: "A plan is held up by its worst job. That's where effort pays: a cleaner bracket steel, shielding, or a remote tool." },
  { q: "Why does a higher value on dose make the best wait longer?",
    a: [["Each day of waiting saves dose, which is now worth more than the day costs", true], ["The dose rate falls faster", false], ["The access limit changes", false]],
    why: "The outage costs the same per day; only the value of the dose saved changed. At 20 million per person-sievert, waiting to day 180 pays." },
  { q: "actinv maintenance printed nothing. What happened?",
    a: [["It worked: the plan is in plan-result.json", true], ["It failed silently", false], ["It needs a --verbose flag to run", false]],
    why: "Like most command-line tools, ACTINV is quiet on success and says what went wrong on failure. The exit status says which: echo $? prints 0." },
], "c4-quiz");
const ALL = Object.values(STEPS).flat();
const was = ALL.every(isDone);
onProgress((set) => {
  if (ALL.every((t) => set.has(t))) {
    $("#finish-title").innerHTML = '<svg class="done-mark" viewBox="0 0 16 16" aria-hidden="true"><path d="M2.5 8.5l3.5 3.5L13.5 4"/></svg>Chapter 4 complete';
    $("#finish-text").textContent = "You found when every job is allowed, weighed the crew's dose against the outage, and saw what the bracket's cobalt costs.";
    if (!was) sound.fanfare();
  }
});
void reducedMotion;
