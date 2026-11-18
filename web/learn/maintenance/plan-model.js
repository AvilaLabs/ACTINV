// The maintenance model of `actinv maintenance`, for the page: dose-rate curves tabulated in seconds after
// shutdown, exponential between points; jobs run one after another from the wait.
const HOUR = 3600, DAY = 86400;
function piece(c, t) { let i = 0; while (i < c.length - 2 && c[i + 1][0] <= t) i++; return i; }
function rateIn(c, i, t) {
  const [t0, d0] = c[i], [t1, d1] = c[i + 1], x = (t - t0) / (t1 - t0);
  return d0 > 0 && d1 > 0 ? d0 * Math.exp(Math.log(d1 / d0) * x) : d0 + (d1 - d0) * x;
}
export function rate(c, t) { return rateIn(c, piece(c, t), t); }
export function dose(c, a, b) {
  let total = 0;
  for (let i = piece(c, a); i < c.length - 1; i++) {
    const lo = Math.max(a, c[i][0]), hi = Math.min(b, c[i + 1][0]);
    if (hi > lo) {
      const [t0, d0] = c[i], [t1, d1] = c[i + 1], ra = rateIn(c, i, lo), rb = rateIn(c, i, hi), h = hi - lo;
      if (d0 > 0 && d1 > 0) { const kh = Math.log(d0 / d1) / (t1 - t0) * h; total += ra * h * (Math.abs(kh) < 1e-12 ? 1 - kh / 2 : -Math.expm1(-kh) / kh); }
      else total += (ra + rb) / 2 * h;
    }
    if (c[i + 1][0] >= b) break;
  }
  return total / HOUR;
}
function peak(c, a, b) { let p = Math.max(rate(c, a), rate(c, b)); for (const [t, d] of c) if (t > a && t < b) p = Math.max(p, d); return p; }
export function plan(p, curves, waitD, perSv) {
  let t = waitD * DAY, collective = 0, largest = 0, feasible = true;
  const jobs = p.jobs.map((j) => {
    const c = curves[j.location], a = t, b = t + j.duration_h * HOUR; t = b;
    const individual = dose(c, a, b), pk = peak(c, a, b), accessible = pk <= p.access_limit_Sv_h;
    const ok = accessible && individual <= p.individual_limit_Sv;
    feasible = feasible && ok; collective += individual * j.crew; largest = Math.max(largest, individual);
    return { ...j, start_d: a / DAY, end_d: b / DAY, individual_Sv: individual, peak_Sv_h: pk, accessible, ok };
  });
  const outage = t / DAY;
  return { wait_d: waitD, jobs, collective_Sv: collective, largest_individual_Sv: largest, outage_d: outage, feasible, cost: collective * perSv + outage * p.cost.per_outage_day };
}
