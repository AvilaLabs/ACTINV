import { boot, terminal, $, sound, complete, isDone, store, key, esc } from "../kit.js";
import { onProgress, doseText } from "../learn.js";
import { plan, rate } from "./plan-model.js";

const STEPS = { order: ["c4-day"], pick: ["c4-red", "c4-first", "c4-cobalt"], worth: ["c4-persv", "c4-match"], ask: ["c4-t-plan", "c4-t-jq"] };
const data = await fetch("data/maintenance-data.json").then((r) => r.json());
const P = data.plan;

boot({ steps: STEPS, version: data.actinv_version, terms: {} });

const MONTHS = [["Mar", 31], ["Apr", 30], ["May", 31], ["Jun", 30], ["Jul", 31], ["Aug", 31], ["Sep", 30], ["Oct", 31], ["Nov", 30], ["Dec", 31], ["Jan", 31], ["Feb", 28]];
const dateOf = (n) => { let d = Math.floor(n); for (const [m, len] of MONTHS) { if (d < len) return `${d + 1} ${m}`; d -= len; } return `day ${n}`; };
const money = (x) => Math.round(x).toLocaleString("en");
const mSv = (sv) => `${(sv * 1000).toFixed(sv < 0.01 ? 1 : 0)} mSv`;
const status = (t) => { $("#status").textContent = t; $("#status").classList.add("fresh"); };
const curvesFor = (co) => ({ pipe: data.curves.pipe, bracket: data.curves[co === "cobalt" ? "bracket" : "bracket-no-cobalt"] });

let co = store.get(key("co"), "cobalt");
let perSv = store.get(key("persv"), null); // null until the learner picks a value; the cost model uses 1e6 until then
let day = store.get(key("day"), null);
const sv = () => perSv ?? P.cost.per_person_Sv;
const actinvBest = () => {
  const k = Object.keys(data.plans).find((x) => x.startsWith(co + "|") && parseFloat(x.split("|")[1]) === sv());
  return data.plans[k].best;
};

/* ---------- Work order ---------- */
$("#jobs").innerHTML = P.jobs.map((j) => `<div class="job ${j.location}"><b>${j.name[0].toUpperCase() + j.name.slice(1)}</b><span>at the ${j.location} · ${j.duration_h} h · crew of ${j.crew}</span></div>`).join("");
$("#per-day").textContent = money(P.cost.per_outage_day);

/* ---------- The planner ---------- */
const planner = $("#planner");
planner.insertAdjacentHTML("beforeend", `<span></span>` + Array.from({ length: 31 }, (_, i) => `<span class="dn">${i + 1}</span>`).join(""));
let n = 0;
for (const [m, len] of MONTHS) {
  planner.insertAdjacentHTML("beforeend", `<span class="mo">${m}</span>`);
  for (let d = 0; d < 31; d++) {
    if (d >= len) { planner.insertAdjacentHTML("beforeend", `<span class="void"></span>`); continue; }
    const b = document.createElement("button");
    b.type = "button"; b.dataset.n = n;
    if (n === 0) { b.className = "shut"; b.title = "1 Mar: shutdown"; b.disabled = true; }
    else { b.title = `${dateOf(n)}: day ${n} after shutdown`; b.addEventListener("click", () => pick(+b.dataset.n)); }
    planner.append(b); n++;
  }
}
const cells = [...planner.querySelectorAll("button[data-n]")];
let cache = {};
const planAt = (d) => (cache[d] ??= plan(P, curvesFor(co), d, sv()));
const firstAllowed = () => { for (let d = 1; d < 365; d++) if (planAt(d).feasible) return d; return null; };

function paint() {
  cache = {};
  const best = actinvBest(), bestDay = Math.round(best.jobs[0].start_d);
  const allowed = cells.slice(1).map((c) => planAt(+c.dataset.n)).filter((p) => p.feasible);
  const lo = Math.min(...allowed.map((p) => p.cost)), span = Math.max(...allowed.map((p) => p.cost)) - lo;
  cells.forEach((c) => {
    const d = +c.dataset.n; if (!d) return;
    const p = planAt(d);
    c.classList.toggle("hot", !p.feasible);
    c.classList.toggle("chosen", d === day);
    c.classList.toggle("best", perSv !== null && d === bestDay);
    c.style.background = p.feasible && perSv !== null ? `rgb(24 0 173 / ${(0.65 * (1 - (p.cost - lo) / span) ** 3 + 0.04).toFixed(2)})` : "";
  });
  $("#legend-cost").hidden = perSv === null;
}

function pick(d, quiet = false) {
  day = d; store.set(key("day"), d);
  const p = planAt(d);
  const rows = p.jobs.map((j) => {
    const why = !j.accessible ? `${doseText(j.peak_Sv_h)} is over the access limit` : j.individual_Sv > P.individual_limit_Sv ? "over 20 mSv each" : "allowed";
    return `<tr><td>${esc(j.name)}</td><td class="n">${doseText(rate(curvesFor(co)[j.location], j.start_d * 86400))}</td><td class="n">${mSv(j.individual_Sv)}</td><td class="n">${j.crew}</td><td class="${j.ok ? "" : "bad"}">${why}</td></tr>`;
  }).join("");
  $("#day-out").innerHTML = `<p class="verdict ${p.feasible ? "" : "no"}">${dateOf(d)}, day ${d}: ${p.feasible ? "every job is allowed." : "not allowed."}</p>
    <table class="sheet"><thead><tr><th>Job</th><th style="text-align:right">dose rate</th><th style="text-align:right">each worker</th><th style="text-align:right">crew</th><th></th></tr></thead><tbody>${rows}</tbody></table>
    <div class="totals"><div><b>${mSv(p.largest_individual_Sv)}</b><span>most anyone gets</span></div><div><b>${(p.collective_Sv * 1000).toFixed(0)}</b><span>person-mSv, everyone</span></div>
    <div><b>${p.outage_d.toFixed(1)}</b><span>outage days</span></div><div><b>${money(p.cost)}</b><span>total cost at ${money(sv())} per person-Sv</span></div></div>`;
  if (!quiet) {
    complete("c4-day", { quiet: true });
    p.feasible ? sound.tick() : sound.wrong();
    status(`${dateOf(d)}: ${p.feasible ? `allowed, total cost ${money(p.cost)}` : "a rule is broken"}.`);
    if (!p.feasible) complete("c4-red");
    if (d === firstAllowed()) { complete("c4-first"); sound.done(); }
    if (perSv !== null && p.feasible && p.cost <= actinvBest().cost * 1.01) complete("c4-match");
  }
  paint(); worth(); po();
}

document.querySelectorAll("[data-co]").forEach((b) => b.addEventListener("click", () => {
  co = b.dataset.co; store.set(key("co"), co); sound.tick();
  document.querySelectorAll("[data-co]").forEach((x) => x.classList.toggle("on", x === b));
  cache = {};
  if (co === "no_cobalt") {
    complete("c4-cobalt");
    const a = data.plans[`cobalt|1e+06`].earliest_wait_d, z = data.plans[`no_cobalt|1e+06`].earliest_wait_d;
    status(`No cobalt: the first allowed day moves from day ${Math.ceil(a)} to day ${Math.ceil(z)}, ${Math.round(a - z)} days sooner.`);
  }
  if (day) pick(day, true); else { paint(); worth(); po(); }
}));
document.querySelectorAll("[data-co]").forEach((x) => x.classList.toggle("on", x.dataset.co === co));

/* ---------- What a day is worth ---------- */
const persvHost = $("#persv");
data.per_sv.forEach((v) => persvHost.insertAdjacentHTML("beforeend", `<button type="button" class="sel" data-v="${v}">${money(v)} per person-Sv</button>`));
persvHost.querySelectorAll("[data-v]").forEach((b) => b.addEventListener("click", () => {
  perSv = +b.dataset.v; store.set(key("persv"), perSv); sound.tick(); cache = {};
  if (perSv === Math.max(...data.per_sv)) complete("c4-persv");
  if (day) pick(day, true); else { paint(); worth(); po(); }
}));
function worth() {
  persvHost.querySelectorAll("[data-v]").forEach((b) => b.classList.toggle("on", +b.dataset.v === perSv));
  if (perSv === null) { $("#worth-out").innerHTML = `<p class="note">Pick a figure above.</p>`; return; }
  const best = actinvBest(), bd = best.jobs[0].start_d, first = firstAllowed();
  const mine = day ? planAt(day) : null;
  $("#worth-out").innerHTML = `<p>At ${money(perSv)} per person-sievert, ACTINV's cheapest plan sends the crew in on <strong>${dateOf(bd)}</strong> (day ${bd.toFixed(1)}): ${(best.collective_Sv * 1000).toFixed(0)} person-mSv, total ${money(best.cost)}. It's outlined in blue on the planner; darker blue days are cheaper.</p>`
    + `<p>${Math.abs(bd - first) < 1.5 ? "That's the first allowed day: at this figure, dose is cheap next to outage days, so the plan goes in as soon as the rules allow." : `That's ${Math.round(bd - first)} days after the first allowed day: at this figure, the dose saved by waiting is worth more than the extra outage days.`}</p>`
    + (mine && mine.feasible ? `<p>Your day, ${dateOf(day)}, costs ${money(mine.cost)}: ${mine.cost <= best.cost * 1.01 ? "within 1% of ACTINV's plan." : `${Math.round(100 * (mine.cost / best.cost - 1))}% more than ACTINV's plan.`}</p>` : "");
}

/* ---------- Side panel ---------- */
function po() {
  const p = day ? planAt(day) : null;
  $("#po").innerHTML = `<div><span>Bracket</span><b>${co === "cobalt" ? "0.1% Co" : "no Co"}</b></div><div><span>Crew goes in</span><b>${day ? dateOf(day) : "—"}</b></div>
    <div><span>Allowed</span><b>${p ? (p.feasible ? "yes" : "no") : "—"}</b></div><div><span>Cost</span><b>${p ? money(p.cost) : "—"}</b></div>`;
}

/* ---------- Ask ACTINV ---------- */
$("#planfile").textContent = JSON.stringify(P, null, 2).replace(/\[\n\s+([\d,\s]+?)\n\s+\]/g, (m, inner) => `[${inner.replace(/\s+/g, " ").trim()}]`);
terminal({
  screen: $("#screen"), cue: $("#cue"), folder: "maintenance-lesson", version: data.actinv_version,
  files: ["bracket.json", "pipe.json", "plan.json", "photon-response.json", "actinv-data/"],
  script: [
    { cmd: "actinv run bracket.json bracket-result.json", needs: ["bracket.json"], makes: ["bracket-result.json"], ms: 1000, working: "solving 41 steps…",
      out: "41 steps, 318 of 3873 states, 926.9 ms -> bracket-result.json", hint: "Now the pipe: actinv run pipe.json pipe-result.json" },
    { cmd: "actinv run pipe.json pipe-result.json", needs: ["pipe.json"], makes: ["pipe-result.json"], ms: 1000, working: "solving 41 steps…",
      out: "41 steps, 73 of 3873 states, 1205.3 ms -> pipe-result.json", hint: "Both places have dose rates. Now the plan." },
    { cmd: "actinv maintenance plan.json plan-result.json", task: "c4-t-plan", needs: ["plan.json", "bracket-result.json", "pipe-result.json"], makes: ["plan-result.json"],
      out: [], hint: "No news is good news: like most command-line tools, it prints nothing when it succeeds. The plan is in plan-result.json." },
    { cmd: `jq -r '.alone[] | "\\(.name)  from day \\(.earliest_start_d | floor)"' plan-result.json`, needs: ["plan-result.json"],
      out: ["cut out the cooling pipe  from day 0", "unbolt the bracket  from day 136", "inspect the bracket's mounting  from day 136"],
      hint: "When each job could start on its own: the bracket's jobs set the date." },
    { cmd: `jq -r '.best | "wait \\(.wait_d | floor) days, \\(.collective_Sv * 1000 | floor) person-mSv, cost \\(.cost | floor)"' plan-result.json`,
      task: "c4-t-jq", needs: ["plan-result.json"], out: ["wait 135 days, 63 person-mSv, cost 337146"],
      hint: "The cheapest allowed plan at one million per person-sievert: the same day the planner shows." },
  ],
});

paint(); worth(); po();
if (day) pick(day, true);

const ALL = Object.values(STEPS).flat();
const was = ALL.every(isDone);
onProgress((set) => {
  if (ALL.every((t) => set.has(t))) {
    $("#finish-title").innerHTML = '<svg class="done-mark" viewBox="0 0 16 16" aria-hidden="true"><path d="M2.5 8.5l3.5 3.5L13.5 4"/></svg>Chapter 4 complete';
    $("#finish-text").textContent = "You planned an outage against the access limit and the dose budget, and saw how the value put on dose moves the best day.";
    if (!was) sound.fanfare();
  }
});
