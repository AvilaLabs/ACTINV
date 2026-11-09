#![allow(non_snake_case)] // JSON wire names carry their physical units.
//! Maintenance dose planning: when jobs inside a shut-down machine may start, what dose their
//! crews receive, and how collective dose trades against outage length as the wait grows.
//!
//! The dose rate at each job's place is a [`DoseCurve`] tabulated at times after shutdown. Between
//! two tabulated times it is interpolated as an exponential (linearly where either end is zero),
//! which is exact for one decaying nuclide and, for a sum of decaying nuclides (whose logarithm is
//! convex in time), never below the true curve. Nothing is extrapolated: a time outside a curve's
//! tabulated range is refused. Jobs run one after another in the listed order, starting at the
//! wait after shutdown; every crew member is present throughout a job, at its one place.

use serde::{Deserialize, Serialize};

pub const PLAN_SCHEMA: &str = "actinv-maintenance-1";
pub const RESULT_SCHEMA: &str = "actinv-maintenance-result-1";
pub const DOSE_MAP_SCHEMA: &str = "actinv-dose-map-1";

const HOUR_S: f64 = 3600.0;
const DAY_S: f64 = 86_400.0;
/// Samples per interval between consecutive events when searching for the earliest wait.
const SCAN: usize = 32;
/// Bisection and golden-section steps: far below any time a plan can resolve.
const REFINE: usize = 80;
/// The interpolation check flags a plan whose answers move by more than this, unless the plan says
/// otherwise.
pub const DEFAULT_INTERPOLATION_TOLERANCE: f64 = 0.05;

/// A dose rate at one place, in Sv/h, at times after shutdown in seconds.
#[derive(Clone, Debug, PartialEq)]
pub struct DoseCurve {
    pub name: String,
    times_s: Vec<f64>,
    sv_h: Vec<f64>,
}

impl DoseCurve {
    pub fn new(name: &str, times_s: Vec<f64>, sv_h: Vec<f64>) -> Result<Self, String> {
        if times_s.len() != sv_h.len() {
            return Err(format!(
                "{name}: {} times but {} dose rates",
                times_s.len(),
                sv_h.len()
            ));
        }
        if times_s.len() < 2 {
            return Err(format!("{name}: a dose rate needs at least two times"));
        }
        if times_s.iter().any(|t| !t.is_finite() || *t < 0.0) {
            return Err(format!(
                "{name}: every time must be finite and at or after shutdown"
            ));
        }
        if times_s.windows(2).any(|pair| pair[1] <= pair[0]) {
            return Err(format!("{name}: times must increase"));
        }
        if sv_h.iter().any(|d| !d.is_finite() || *d < 0.0) {
            return Err(format!(
                "{name}: every dose rate must be finite and not negative"
            ));
        }
        Ok(Self {
            name: name.to_owned(),
            times_s,
            sv_h,
        })
    }

    pub fn first_s(&self) -> f64 {
        self.times_s[0]
    }

    pub fn last_s(&self) -> f64 {
        self.times_s[self.times_s.len() - 1]
    }

    /// The same curve on every other tabulated time, keeping the first and the last.
    pub fn coarsened(&self) -> Option<Self> {
        let n = self.times_s.len();
        if n < 3 {
            return None;
        }
        let keep: Vec<usize> = (0..n).filter(|i| i % 2 == 0 || *i == n - 1).collect();
        Some(Self {
            name: self.name.clone(),
            times_s: keep.iter().map(|&i| self.times_s[i]).collect(),
            sv_h: keep.iter().map(|&i| self.sv_h[i]).collect(),
        })
    }

    fn piece(&self, t: f64) -> usize {
        let i = self.times_s.partition_point(|&x| x <= t);
        i.clamp(1, self.times_s.len() - 1) - 1
    }

    fn inside(&self, t: f64) -> bool {
        t >= self.first_s() && t <= self.last_s()
    }

    /// The dose rate at `t`, or `None` outside the tabulated times.
    pub fn rate(&self, t: f64) -> Option<f64> {
        self.inside(t).then(|| self.rate_in(self.piece(t), t))
    }

    fn rate_in(&self, i: usize, t: f64) -> f64 {
        let (t0, t1, d0, d1) = (
            self.times_s[i],
            self.times_s[i + 1],
            self.sv_h[i],
            self.sv_h[i + 1],
        );
        let x = (t - t0) / (t1 - t0);
        if d0 > 0.0 && d1 > 0.0 {
            d0 * ((d1 / d0).ln() * x).exp()
        } else {
            d0 + (d1 - d0) * x
        }
    }

    /// The dose in Sv received over `[a, b]`, or `None` if it leaves the tabulated times.
    pub fn dose(&self, a: f64, b: f64) -> Option<f64> {
        if !(self.inside(a) && self.inside(b)) || b < a {
            return None;
        }
        let mut total = 0.0;
        for i in self.piece(a)..self.times_s.len() - 1 {
            let (lo, hi) = (a.max(self.times_s[i]), b.min(self.times_s[i + 1]));
            if hi > lo {
                total += self.piece_dose(i, lo, hi);
            }
            if self.times_s[i + 1] >= b {
                break;
            }
        }
        Some(total / HOUR_S)
    }

    /// Sv·s/h over `[lo, hi]` inside piece `i`.
    fn piece_dose(&self, i: usize, lo: f64, hi: f64) -> f64 {
        let (t0, t1, d0, d1) = (
            self.times_s[i],
            self.times_s[i + 1],
            self.sv_h[i],
            self.sv_h[i + 1],
        );
        let (ra, rb, h) = (self.rate_in(i, lo), self.rate_in(i, hi), hi - lo);
        if d0 > 0.0 && d1 > 0.0 {
            // The rate is ra·exp(-k(t - lo)); its integral is ra·h·(1 - exp(-kh))/(kh).
            let kh = (d0 / d1).ln() / (t1 - t0) * h;
            let phi = if kh.abs() < 1e-12 {
                1.0 - kh / 2.0
            } else {
                -(-kh).exp_m1() / kh
            };
            ra * h * phi
        } else {
            (ra + rb) / 2.0 * h
        }
    }

    /// The highest dose rate over `[a, b]` (each piece is monotone, so it is at an end or a knot).
    pub fn peak(&self, a: f64, b: f64) -> Option<f64> {
        let mut peak = self.rate(a)?.max(self.rate(b)?);
        for (t, d) in self.times_s.iter().zip(&self.sv_h) {
            if *t > a && *t < b {
                peak = peak.max(*d);
            }
        }
        Some(peak)
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Job {
    pub name: String,
    /// The dose curve the job is done at.
    pub location: String,
    pub duration_h: f64,
    pub crew: u32,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Cost {
    pub per_person_Sv: f64,
    pub per_outage_day: f64,
}

/// What a plan asks, apart from where its dose rates come from.
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Plan {
    /// A job may run only while the dose rate at its place stays at or under this.
    pub access_limit_Sv_h: f64,
    /// Each worker's dose over the plan must stay at or under this, when given.
    #[serde(default)]
    pub individual_limit_Sv: Option<f64>,
    pub jobs: Vec<Job>,
    /// Waits after shutdown to tabulate, in days.
    #[serde(default)]
    pub waits_d: Vec<f64>,
    #[serde(default)]
    pub cost: Option<Cost>,
    #[serde(default)]
    pub interpolation_tolerance: Option<f64>,
}

#[derive(Clone, Debug, Serialize, PartialEq)]
pub struct JobAlone {
    pub name: String,
    pub location: String,
    /// The earliest start, in days after shutdown, at which the job's place stays under the access
    /// limit for the whole job; `None` if it never does within the tabulated times.
    pub earliest_start_d: Option<f64>,
}

#[derive(Clone, Debug, Serialize, PartialEq)]
pub struct JobAtWait {
    pub name: String,
    pub start_d: f64,
    pub end_d: f64,
    pub peak_Sv_h: f64,
    pub individual_Sv: f64,
    pub collective_Sv: f64,
    pub accessible: bool,
}

#[derive(Clone, Debug, Serialize, PartialEq)]
pub struct Wait {
    pub wait_d: f64,
    pub outage_d: f64,
    pub jobs: Vec<JobAtWait>,
    pub collective_Sv: f64,
    /// The largest dose any one worker receives: every job's crew is counted separately, so this
    /// is the largest job's individual dose.
    pub largest_individual_Sv: f64,
    /// Every job is accessible and every worker is under the individual limit.
    pub feasible: bool,
    pub cost: Option<f64>,
}

#[derive(Clone, Debug, Serialize, PartialEq)]
pub struct InterpolationCheck {
    pub method: String,
    pub tolerance: f64,
    /// `None` when no curve has a time to drop.
    pub largest_relative_change: Option<f64>,
    /// What changed most.
    pub at: Option<String>,
    pub coarse: bool,
}

#[derive(Clone, Debug, Serialize, PartialEq)]
pub struct Outcome {
    pub schema: String,
    pub interpolation: String,
    pub work_h: f64,
    pub alone: Vec<JobAlone>,
    pub waits: Vec<Wait>,
    /// The earliest wait, in days, at which the plan is feasible.
    pub earliest_wait_d: Option<f64>,
    /// The feasible wait of least cost, when the plan gives costs.
    pub best: Option<Wait>,
    pub interpolation_check: InterpolationCheck,
}

struct Prepared<'a> {
    plan: &'a Plan,
    /// Each job's curve, and its start offset from the wait, in seconds.
    jobs: Vec<(&'a DoseCurve, f64, f64)>,
    work_s: f64,
    /// The range of waits, in seconds, over which every job stays inside its curve.
    lo: f64,
    hi: f64,
}

impl<'a> Prepared<'a> {
    fn new(plan: &'a Plan, curves: &'a [DoseCurve]) -> Result<Self, String> {
        if plan.jobs.is_empty() {
            return Err("the plan has no jobs".into());
        }
        if !(plan.access_limit_Sv_h.is_finite() && plan.access_limit_Sv_h > 0.0) {
            return Err("access_limit_Sv_h must be positive".into());
        }
        if plan
            .individual_limit_Sv
            .is_some_and(|limit| !(limit.is_finite() && limit > 0.0))
        {
            return Err("individual_limit_Sv must be positive".into());
        }
        if let Some(cost) = &plan.cost {
            if [cost.per_person_Sv, cost.per_outage_day]
                .iter()
                .any(|c| !(c.is_finite() && *c >= 0.0))
            {
                return Err("costs must be finite and not negative".into());
            }
        }
        if plan
            .interpolation_tolerance
            .is_some_and(|t| !(t.is_finite() && t > 0.0))
        {
            return Err("interpolation_tolerance must be positive".into());
        }
        let mut jobs = Vec::with_capacity(plan.jobs.len());
        let mut offset = 0.0;
        let (mut lo, mut hi) = (0.0_f64, f64::INFINITY);
        for job in &plan.jobs {
            if !(job.duration_h.is_finite() && job.duration_h > 0.0) {
                return Err(format!("job '{}': duration_h must be positive", job.name));
            }
            if job.crew == 0 {
                return Err(format!("job '{}': crew must be at least 1", job.name));
            }
            let curve = curves
                .iter()
                .find(|curve| curve.name == job.location)
                .ok_or_else(|| {
                    format!(
                        "job '{}': no dose rate for location '{}'",
                        job.name, job.location
                    )
                })?;
            let duration = job.duration_h * HOUR_S;
            lo = lo.max(curve.first_s() - offset);
            hi = hi.min(curve.last_s() - offset - duration);
            jobs.push((curve, offset, duration));
            offset += duration;
        }
        if hi < lo {
            return Err(format!(
                "the jobs take {:.4} d, longer than the tabulated dose rates allow after the first tabulated time",
                offset / DAY_S
            ));
        }
        Ok(Self {
            plan,
            jobs,
            work_s: offset,
            lo,
            hi,
        })
    }

    fn at(&self, wait: f64) -> Wait {
        let mut jobs = Vec::with_capacity(self.jobs.len());
        let (mut collective, mut largest, mut feasible) = (0.0, 0.0_f64, true);
        for (job, (curve, offset, duration)) in self.plan.jobs.iter().zip(&self.jobs) {
            let (a, b) = (wait + offset, wait + offset + duration);
            // `wait` is inside [lo, hi], so both ends are inside the curve.
            let peak = curve.peak(a, b).unwrap_or(f64::INFINITY);
            let individual = curve.dose(a, b).unwrap_or(f64::INFINITY);
            let accessible = peak <= self.plan.access_limit_Sv_h;
            feasible &= accessible
                && self
                    .plan
                    .individual_limit_Sv
                    .is_none_or(|limit| individual <= limit);
            collective += individual * f64::from(job.crew);
            largest = largest.max(individual);
            jobs.push(JobAtWait {
                name: job.name.clone(),
                start_d: a / DAY_S,
                end_d: b / DAY_S,
                peak_Sv_h: peak,
                individual_Sv: individual,
                collective_Sv: individual * f64::from(job.crew),
                accessible,
            });
        }
        let outage = wait + self.work_s;
        let cost = self
            .plan
            .cost
            .as_ref()
            .map(|cost| cost.per_person_Sv * collective + cost.per_outage_day * outage / DAY_S);
        Wait {
            wait_d: wait / DAY_S,
            outage_d: outage / DAY_S,
            jobs,
            collective_Sv: collective,
            largest_individual_Sv: largest,
            feasible,
            cost,
        }
    }

    /// Waits at which some job's start or end meets a tabulated time: between two of them every
    /// job's dose rate is one smooth piece at each end.
    fn events(&self) -> Vec<f64> {
        let mut events = vec![self.lo, self.hi];
        for (curve, offset, duration) in &self.jobs {
            for t in &curve.times_s {
                events.extend([t - offset, t - offset - duration]);
            }
        }
        events.retain(|w| *w >= self.lo && *w <= self.hi);
        events.sort_by(f64::total_cmp);
        events.dedup();
        events
    }

    fn samples(&self) -> Vec<f64> {
        let events = self.events();
        let mut samples = vec![events[0]];
        for pair in events.windows(2) {
            for k in 1..=SCAN {
                samples.push(pair[0] + (pair[1] - pair[0]) * k as f64 / SCAN as f64);
            }
        }
        samples
    }

    /// The earliest feasible wait, in seconds: the first feasible sample, refined by bisection
    /// against the infeasible one before it.
    fn earliest(&self, samples: &[f64]) -> Option<f64> {
        let first = samples.iter().position(|w| self.at(*w).feasible)?;
        if first == 0 {
            return Some(samples[0]);
        }
        let (mut bad, mut good) = (samples[first - 1], samples[first]);
        for _ in 0..REFINE {
            let mid = 0.5 * (bad + good);
            if self.at(mid).feasible {
                good = mid;
            } else {
                bad = mid;
            }
        }
        Some(good)
    }

    /// The feasible wait of least cost: the best feasible sample, refined by golden-section search
    /// between its feasible neighbours, or the earliest feasible wait where the best sample's earlier
    /// neighbour is not feasible.
    fn best(&self, samples: &[f64], earliest: Option<f64>) -> Option<Wait> {
        self.plan.cost.as_ref()?;
        let cost = |w: f64| {
            let wait = self.at(w);
            wait.feasible.then_some(wait.cost.unwrap_or(f64::INFINITY))
        };
        let (index, _) = samples
            .iter()
            .enumerate()
            .filter_map(|(i, w)| cost(*w).map(|c| (i, c)))
            .min_by(|a, b| a.1.total_cmp(&b.1))?;
        let feasible = |i: usize| cost(samples[i]).is_some();
        let lo = if index > 0 && feasible(index - 1) {
            samples[index - 1]
        } else {
            earliest.unwrap_or(samples[index]).min(samples[index])
        };
        let hi = if index + 1 < samples.len() && feasible(index + 1) {
            samples[index + 1]
        } else {
            samples[index]
        };
        let (mut a, mut b) = (lo, hi);
        let ratio = (5.0_f64.sqrt() - 1.0) / 2.0;
        for _ in 0..REFINE {
            if b - a <= 0.0 {
                break;
            }
            let (c, d) = (b - ratio * (b - a), a + ratio * (b - a));
            let (fc, fd) = (
                cost(c).unwrap_or(f64::INFINITY),
                cost(d).unwrap_or(f64::INFINITY),
            );
            if fc <= fd {
                b = d;
            } else {
                a = c;
            }
        }
        let refined = 0.5 * (a + b);
        let candidates = [samples[index], refined, lo];
        candidates
            .iter()
            .filter_map(|w| cost(*w).map(|c| (*w, c)))
            .min_by(|x, y| x.1.total_cmp(&y.1))
            .map(|(w, _)| self.at(w))
    }

    fn alone(&self) -> Vec<JobAlone> {
        self.plan
            .jobs
            .iter()
            .zip(&self.jobs)
            .map(|(job, (curve, _, duration))| {
                let fits = |s: f64| {
                    curve
                        .peak(s, s + duration)
                        .is_some_and(|peak| peak <= self.plan.access_limit_Sv_h)
                };
                let (lo, hi) = (curve.first_s(), curve.last_s() - duration);
                let earliest = if hi < lo {
                    None
                } else {
                    let mut events: Vec<f64> = curve
                        .times_s
                        .iter()
                        .flat_map(|t| [*t, t - duration])
                        .chain([lo, hi])
                        .filter(|s| *s >= lo && *s <= hi)
                        .collect();
                    events.sort_by(f64::total_cmp);
                    events.dedup();
                    let mut samples = vec![events[0]];
                    for pair in events.windows(2) {
                        for k in 1..=SCAN {
                            samples.push(pair[0] + (pair[1] - pair[0]) * k as f64 / SCAN as f64);
                        }
                    }
                    samples.iter().position(|s| fits(*s)).map(|first| {
                        if first == 0 {
                            return samples[0];
                        }
                        let (mut bad, mut good) = (samples[first - 1], samples[first]);
                        for _ in 0..REFINE {
                            let mid = 0.5 * (bad + good);
                            if fits(mid) {
                                good = mid;
                            } else {
                                bad = mid;
                            }
                        }
                        good
                    })
                };
                JobAlone {
                    name: job.name.clone(),
                    location: job.location.clone(),
                    earliest_start_d: earliest.map(|s| s / DAY_S),
                }
            })
            .collect()
    }
}

/// Every answer of a plan, before the interpolation check.
fn answers(plan: &Plan, curves: &[DoseCurve]) -> Result<Outcome, String> {
    let prepared = Prepared::new(plan, curves)?;
    let mut waits = Vec::with_capacity(plan.waits_d.len());
    for wait_d in &plan.waits_d {
        let wait = wait_d * DAY_S;
        if !wait.is_finite() || wait < prepared.lo || wait > prepared.hi {
            return Err(format!(
                "a wait of {wait_d} d is outside the waits the tabulated dose rates cover ({:.6} to {:.6} d)",
                prepared.lo / DAY_S,
                prepared.hi / DAY_S
            ));
        }
        waits.push(prepared.at(wait));
    }
    let samples = prepared.samples();
    let earliest = prepared.earliest(&samples);
    Ok(Outcome {
        schema: RESULT_SCHEMA.into(),
        interpolation: "exponential between tabulated times (linear where a dose rate is zero); no extrapolation".into(),
        work_h: prepared.work_s / HOUR_S,
        alone: prepared.alone(),
        waits,
        earliest_wait_d: earliest.map(|w| w / DAY_S),
        best: prepared.best(&samples, earliest),
        interpolation_check: InterpolationCheck {
            method: "every other tabulated time dropped, the first and last kept".into(),
            tolerance: plan
                .interpolation_tolerance
                .unwrap_or(DEFAULT_INTERPOLATION_TOLERANCE),
            largest_relative_change: None,
            at: None,
            coarse: false,
        },
    })
}

fn change(a: Option<f64>, b: Option<f64>) -> f64 {
    match (a, b) {
        (Some(a), Some(b)) if a == b => 0.0,
        (Some(a), Some(b)) => (a - b).abs() / a.abs().max(b.abs()),
        (None, None) => 0.0,
        _ => f64::INFINITY,
    }
}

/// Solve a plan, and check how far its answers move on the coarsened curves.
pub fn evaluate(plan: &Plan, curves: &[DoseCurve]) -> Result<Outcome, String> {
    let mut outcome = answers(plan, curves)?;
    let used: Vec<&str> = plan.jobs.iter().map(|job| job.location.as_str()).collect();
    if !curves
        .iter()
        .any(|curve| used.contains(&curve.name.as_str()) && curve.coarsened().is_some())
    {
        return Ok(outcome);
    }
    let coarse: Vec<DoseCurve> = curves
        .iter()
        .map(|curve| curve.coarsened().unwrap_or_else(|| curve.clone()))
        .collect();
    let other = answers(plan, &coarse)?;
    let mut moved: Vec<(f64, String)> = vec![(
        change(outcome.earliest_wait_d, other.earliest_wait_d),
        "earliest_wait_d".into(),
    )];
    for (a, b) in outcome.alone.iter().zip(&other.alone) {
        moved.push((
            change(a.earliest_start_d, b.earliest_start_d),
            format!("alone '{}' earliest_start_d", a.name),
        ));
    }
    for (a, b) in outcome.waits.iter().zip(&other.waits) {
        moved.push((
            change(Some(a.collective_Sv), Some(b.collective_Sv)),
            format!("collective_Sv at {} d", a.wait_d),
        ));
    }
    // Near a flat minimum the best wait is ill-conditioned while its cost is not, so only the cost
    // is compared.
    if let (Some(a), Some(b)) = (&outcome.best, &other.best) {
        moved.push((change(a.cost, b.cost), "best cost".into()));
    } else if outcome.best.is_some() != other.best.is_some() {
        moved.push((f64::INFINITY, "best".into()));
    }
    let (largest, at) = moved
        .into_iter()
        .max_by(|a, b| a.0.total_cmp(&b.0))
        .expect("at least the earliest wait");
    let check = &mut outcome.interpolation_check;
    check.largest_relative_change = Some(largest);
    check.at = Some(at);
    check.coarse = largest > check.tolerance;
    Ok(outcome)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn exponential(name: &str, d0: f64, half_life_d: f64, times_d: &[f64]) -> DoseCurve {
        let k = std::f64::consts::LN_2 / (half_life_d * DAY_S);
        let t: Vec<f64> = times_d.iter().map(|d| d * DAY_S).collect();
        let d = t.iter().map(|t| d0 * (-k * t).exp()).collect();
        DoseCurve::new(name, t, d).unwrap()
    }

    fn plan(jobs: Vec<Job>) -> Plan {
        Plan {
            access_limit_Sv_h: 1e-4,
            individual_limit_Sv: None,
            jobs,
            waits_d: Vec::new(),
            cost: None,
            interpolation_tolerance: None,
        }
    }

    fn job(name: &str, location: &str, duration_h: f64, crew: u32) -> Job {
        Job {
            name: name.into(),
            location: location.into(),
            duration_h,
            crew,
        }
    }

    #[test]
    fn one_nuclide_is_exact_on_a_coarse_grid() {
        // 1e-2 Sv/h at shutdown, half-life 5 d: the dose rate reaches 1e-4 Sv/h at 5·log2(100) d.
        let curve = exponential("port", 1e-2, 5.0, &[0.0, 30.0, 90.0]);
        let k = std::f64::consts::LN_2 / (5.0 * DAY_S);
        let a = 40.0 * DAY_S;
        let b = a + 10.0 * HOUR_S;
        let exact = 1e-2 / k * ((-k * a).exp() - (-k * b).exp()) / HOUR_S;
        let dose = curve.dose(a, b).unwrap();
        assert!((dose - exact).abs() / exact < 1e-12, "{dose} {exact}");
        let p = plan(vec![job("swap", "port", 10.0, 3)]);
        let outcome = evaluate(&p, &[curve]).unwrap();
        let expected = 5.0 * 100.0_f64.log2();
        let earliest = outcome.earliest_wait_d.unwrap();
        assert!((earliest - expected).abs() / expected < 1e-9, "{earliest}");
        assert_eq!(outcome.alone[0].earliest_start_d, outcome.earliest_wait_d);
        assert!(!outcome.interpolation_check.coarse);
    }

    #[test]
    fn two_nuclides_are_never_underestimated() {
        let fine: Vec<f64> = (0..=400).map(|i| i as f64 * 0.25).collect();
        let sum = |times: &[f64]| {
            let t: Vec<f64> = times.iter().map(|d| d * DAY_S).collect();
            let k1 = std::f64::consts::LN_2 / (0.6 * DAY_S);
            let k2 = std::f64::consts::LN_2 / (70.0 * DAY_S);
            let d = t
                .iter()
                .map(|t| 3e-2 * (-k1 * t).exp() + 2e-4 * (-k2 * t).exp())
                .collect();
            DoseCurve::new("port", t, d).unwrap()
        };
        let (dense, sparse) = (sum(&fine), sum(&[0.0, 1.0, 7.0, 30.0, 100.0]));
        for a in [0.5, 3.0, 12.0, 50.0] {
            let (a, b) = (a * DAY_S, (a + 2.0) * DAY_S);
            assert!(sparse.dose(a, b).unwrap() >= dense.dose(a, b).unwrap() * (1.0 - 1e-9));
        }
        let p = Plan {
            waits_d: vec![1.0, 7.0, 30.0],
            ..plan(vec![job("swap", "port", 24.0, 2)])
        };
        let check = evaluate(&p, &[sparse]).unwrap().interpolation_check;
        assert!(check.coarse, "{check:?}");
        assert!(!evaluate(&p, &[dense]).unwrap().interpolation_check.coarse);
    }

    #[test]
    fn jobs_run_in_order_and_costs_find_a_best_wait() {
        let curves = [
            exponential("a", 1e-2, 5.0, &[0.0, 10.0, 30.0, 60.0, 120.0, 200.0]),
            exponential("b", 1e-3, 20.0, &[0.0, 10.0, 30.0, 60.0, 120.0, 200.0]),
        ];
        let p = Plan {
            waits_d: vec![35.0, 70.0],
            cost: Some(Cost {
                per_person_Sv: 1e7,
                per_outage_day: 1e3,
            }),
            ..plan(vec![job("one", "a", 48.0, 4), job("two", "b", 24.0, 2)])
        };
        let outcome = evaluate(&p, &curves).unwrap();
        let wait = &outcome.waits[0];
        assert_eq!(wait.jobs[1].start_d, wait.jobs[0].end_d);
        assert!((wait.outage_d - 38.0).abs() < 1e-12);
        let collective: f64 = wait.jobs.iter().map(|job| job.collective_Sv).sum();
        assert_eq!(wait.collective_Sv, collective);
        let best = outcome.best.unwrap();
        assert!(best.feasible);
        assert!(best.wait_d >= outcome.earliest_wait_d.unwrap());
        // Outage costs more than the dose waiting saves, so the best wait is the earliest.
        let rising = Plan {
            cost: Some(Cost {
                per_person_Sv: 1.0,
                per_outage_day: 1e3,
            }),
            ..p.clone()
        };
        let quick = evaluate(&rising, &curves).unwrap();
        assert_eq!(quick.best.unwrap().wait_d, quick.earliest_wait_d.unwrap());
        assert!(!outcome.waits[0].feasible && outcome.waits[1].feasible);
        assert!(best.cost.unwrap() <= outcome.waits[1].cost.unwrap() + 1e-9);
    }

    #[test]
    fn an_individual_limit_delays_the_earliest_wait() {
        let curve = exponential("port", 1e-2, 5.0, &[0.0, 30.0, 60.0, 120.0]);
        let free = evaluate(
            &plan(vec![job("long", "port", 200.0, 1)]),
            std::slice::from_ref(&curve),
        )
        .unwrap();
        let limited = Plan {
            individual_limit_Sv: Some(2e-3),
            ..plan(vec![job("long", "port", 200.0, 1)])
        };
        let curves = [curve];
        let held = evaluate(&limited, &curves).unwrap();
        assert!(held.earliest_wait_d.unwrap() > free.earliest_wait_d.unwrap());
        let wait = held.earliest_wait_d.unwrap();
        let at = Prepared::new(&limited, &curves).unwrap().at(wait * DAY_S);
        assert!(at.largest_individual_Sv <= 2e-3 * (1.0 + 1e-9));
    }

    #[test]
    fn plans_outside_their_dose_rates_are_refused() {
        let curve = exponential("port", 1e-2, 5.0, &[1.0, 30.0]);
        let refused = |p: Plan| evaluate(&p, std::slice::from_ref(&curve)).unwrap_err();
        assert!(refused(plan(vec![job("x", "nowhere", 1.0, 1)])).contains("no dose rate"));
        assert!(refused(plan(vec![job("x", "port", 0.0, 1)])).contains("duration_h"));
        assert!(refused(plan(vec![job("x", "port", 1.0, 0)])).contains("crew"));
        assert!(refused(plan(vec![job("x", "port", 40.0 * 24.0, 1)])).contains("longer"));
        let early = Plan {
            waits_d: vec![0.5],
            ..plan(vec![job("x", "port", 1.0, 1)])
        };
        assert!(refused(early).contains("outside"));
        let late = Plan {
            waits_d: vec![29.99],
            ..plan(vec![job("x", "port", 1.0, 1)])
        };
        assert!(refused(late).contains("outside"));
        assert!(DoseCurve::new("p", vec![0.0, 0.0], vec![1.0, 1.0]).is_err());
        assert!(DoseCurve::new("p", vec![0.0, 1.0], vec![1.0, -1.0]).is_err());
        assert!(DoseCurve::new("p", vec![0.0], vec![1.0]).is_err());
        assert_eq!(curve.rate(0.5 * DAY_S), None);
    }

    #[test]
    fn a_zero_dose_rate_interpolates_linearly() {
        let curve = DoseCurve::new("p", vec![0.0, 3600.0], vec![2e-3, 0.0]).unwrap();
        assert!((curve.rate(1800.0).unwrap() - 1e-3).abs() < 1e-18);
        assert!((curve.dose(0.0, 3600.0).unwrap() - 1e-3).abs() < 1e-15);
    }
}
