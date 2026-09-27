//! `actinv optimize` — chance-constrained activation design optimization (P49).
//!
//! A bounded, seeded, derivative-free search over a declared design box. Every
//! candidate is a generated `actinv-spec-1` document solved through the
//! identical `run::run` path; objectives and constraints are evaluated on
//! nominal values or propagated-band edges. Every attempted evaluation is
//! recorded in an append-only ledger before the next one begins.

use serde::Deserialize;
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::io::Write;
use std::path::{Path, PathBuf};

pub const OPT_SCHEMA: &str = "actinv-optimize-1";
const MAX_EVALS: usize = 64;
const TIME_REL_TOL: f64 = 1e-3;
const TINY: f64 = 1e-300;

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OptimizeSpec {
    pub schema: String,
    pub base_spec: String,
    pub design_axes: Vec<Axis>,
    pub objective: Objective,
    /// P67: additional objectives measured on every candidate's solve
    /// (free — same run) and used to emit the certified Pareto frontier
    /// over the feasible set. ≥2 entries required when present; names
    /// `"{response}@{time_s}"` must be unique.
    #[serde(default)]
    pub objectives: Vec<Objective>,
    #[serde(default)]
    pub constraints: Vec<Constraint>,
    pub optimizer: OptimizerCfg,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum Axis {
    CompositionFraction { element: String, bounds: [f64; 2] },
    FluxScale { bounds: [f64; 2] },
    StepDt { step: usize, bounds: [f64; 2] },
}

impl Axis {
    fn bounds(&self) -> [f64; 2] {
        match self {
            Axis::CompositionFraction { bounds, .. }
            | Axis::FluxScale { bounds }
            | Axis::StepDt { bounds, .. } => *bounds,
        }
    }
}

/// Edge selector for an objective or constraint. `nominal` is the computed
/// value; `normal_*` and `conservative_*` select the propagated-band edges
/// produced by the spec's `uncertainty` block at its declared confidence.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Edge {
    Nominal,
    NormalLower,
    NormalUpper,
    ConservativeLower,
    ConservativeUpper,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Objective {
    pub response: String,
    pub time_s: f64,
    #[serde(default = "edge_nominal")]
    pub edge: Edge,
    pub direction: String,
}

fn edge_nominal() -> Edge {
    Edge::Nominal
}

/// Stable ledger key for a named objective: `"{response}@{time_s}"`.
fn objective_name(o: &Objective) -> String {
    format!("{}@{}", o.response, o.time_s)
}

fn constraint_response() -> String {
    "response".into()
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Constraint {
    pub name: String,
    /// "response" (default): evaluate a solver response edge at a time.
    /// "axis": bound a design-axis value directly — evaluated on the
    /// parameter vector before solving; a violation is ledgered
    /// `infeasible_by_axis` and never consumes a solver run.
    #[serde(default = "constraint_response")]
    pub kind: String,
    /// Required for kind="response".
    #[serde(default)]
    pub response: Option<String>,
    #[serde(default)]
    pub time_s: f64,
    #[serde(default = "edge_nominal")]
    pub edge: Edge,
    /// Required for kind="axis": index into `design_axes`.
    #[serde(default)]
    pub axis: Option<usize>,
    pub sense: String,
    pub limit: f64,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OptimizerCfg {
    pub algorithm: String,
    pub seed: u64,
    pub init_points: usize,
    pub refine_points: usize,
    #[serde(default = "default_refine_fraction")]
    pub refine_step_fraction: f64,
}

fn default_refine_fraction() -> f64 {
    0.25
}

// ---------------------------------------------------------------------------
// Seeded RNG: xorshift64* — the only randomness in the optimizer.
// ---------------------------------------------------------------------------

pub struct Rng(u64);

impl Rng {
    pub fn new(seed: u64) -> Self {
        // zero state is a fixed point; fold the seed so 0 still mixes
        Rng(seed ^ 0x9E37_79B9_7F4A_7C15 | 1)
    }
    fn next_u64(&mut self) -> u64 {
        let mut x = self.0;
        x ^= x >> 12;
        x ^= x << 25;
        x ^= x >> 27;
        self.0 = x;
        x.wrapping_mul(0x2545_F491_4F6C_DD1D)
    }
    fn next_f64(&mut self) -> f64 {
        (self.next_u64() >> 11) as f64 * (1.0 / (1u64 << 53) as f64)
    }
}

// ---------------------------------------------------------------------------
// Optimizer engine — generic over the evaluator so controls can inject a
// synthetic landscape without touching the solver.
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Default)]
pub struct EvalOutcome {
    /// None when the run failed or the objective is not computable.
    pub objective: Option<f64>,
    /// Per-constraint violation margin (≤0 satisfied); None = not computable.
    pub violations: Vec<Option<f64>>,
    /// P67: per-`objectives`-entry values on the same solve; parallel to
    /// `OptimizeSpec.objectives`. Empty when no extra objectives declared.
    pub objective_values: Vec<Option<f64>>,
    pub status: String,
}

impl EvalOutcome {
    fn violation_sum(&self) -> f64 {
        // a not-computable constraint counts as +inf violation
        if self.violations.iter().any(|v| v.is_none()) {
            return f64::INFINITY;
        }
        self.violations.iter().map(|v| v.unwrap().max(0.0)).sum()
    }
    pub fn feasible(&self) -> bool {
        self.objective.is_some() && self.violation_sum() <= 0.0
    }
}

/// Rank key: feasible first, then total positive violation, then objective.
fn better(a: &EvalOutcome, b: &EvalOutcome, minimize: bool) -> bool {
    match (a.feasible(), b.feasible()) {
        (true, false) => return true,
        (false, true) => return false,
        _ => {}
    }
    let (va, vb) = (a.violation_sum(), b.violation_sum());
    if va != vb {
        return va < vb;
    }
    match (a.objective, b.objective) {
        (Some(x), Some(y)) if x != y => return if minimize { x < y } else { x > y },
        (Some(_), None) => return true,
        (None, Some(_)) => return false,
        _ => {}
    }
    false
}

/// Seeded LHS fill + coordinate-descent refinement. Returns (points evaluated,
/// in evaluation order) — the caller records each against the ledger.
pub fn run_search<F: FnMut(&[f64]) -> EvalOutcome>(
    bounds: &[[f64; 2]],
    cfg: &OptimizerCfg,
    minimize: bool,
    mut evaluate: F,
) -> Vec<(Vec<f64>, EvalOutcome)> {
    let d = bounds.len();
    let mut rng = Rng::new(cfg.seed);
    let mut evaluated: Vec<(Vec<f64>, EvalOutcome)> = Vec::new();

    // Stage 1: optional box corners, then Latin hypercube fill of the
    // remaining init budget (per-axis strata permuted by the seeded RNG).
    let n1 = cfg.init_points.min(MAX_EVALS);
    if n1 == 0 || d == 0 {
        return evaluated;
    }
    let n_corners = if cfg.algorithm == "lhs_corners_coordinate" {
        (1usize << d).min(n1)
    } else {
        0
    };
    for i in 0..n_corners {
        let x: Vec<f64> = (0..d)
            .map(|k| {
                if (i >> k) & 1 == 1 {
                    bounds[k][1]
                } else {
                    bounds[k][0]
                }
            })
            .collect();
        let out = evaluate(&x);
        evaluated.push((x, out));
    }
    let n_lhs = n1 - n_corners;
    if n_lhs > 0 {
        let mut strata: Vec<Vec<usize>> = (0..d).map(|_| (0..n_lhs).collect()).collect();
        for axis_strata in strata.iter_mut() {
            for i in (1..n_lhs).rev() {
                let j = (rng.next_u64() % (i as u64 + 1)) as usize;
                axis_strata.swap(i, j);
            }
        }
        // i selects one stratum per axis — it indexes strata, not the points
        #[allow(clippy::needless_range_loop)]
        for i in 0..n_lhs {
            let x: Vec<f64> = (0..d)
                .map(|k| {
                    let u = (strata[k][i] as f64 + rng.next_f64()) / n_lhs as f64;
                    bounds[k][0] + u * (bounds[k][1] - bounds[k][0])
                })
                .collect();
            let out = evaluate(&x);
            evaluated.push((x, out));
        }
    }

    // Stage 2: coordinate descent from the best-ranked point.
    let mut best_idx = 0usize;
    for (i, (_, out)) in evaluated.iter().enumerate() {
        if better(out, &evaluated[best_idx].1, minimize) {
            best_idx = i;
        }
    }
    let mut center = evaluated[best_idx].0.clone();
    let mut center_out = evaluated[best_idx].1.clone();
    let mut steps: Vec<f64> = bounds
        .iter()
        .map(|b| (b[1] - b[0]) * cfg.refine_step_fraction)
        .collect();
    let mut spent = 0usize;
    while spent < cfg.refine_points && evaluated.len() < MAX_EVALS {
        let mut improved = false;
        for k in 0..d {
            if spent >= cfg.refine_points || evaluated.len() >= MAX_EVALS {
                break;
            }
            let width = bounds[k][1] - bounds[k][0];
            if steps[k] < 1e-3 * width {
                continue;
            }
            for sign in [1.0f64, -1.0] {
                if spent >= cfg.refine_points || evaluated.len() >= MAX_EVALS {
                    break;
                }
                let mut x = center.clone();
                x[k] = (x[k] + sign * steps[k]).clamp(bounds[k][0], bounds[k][1]);
                if x[k] == center[k] {
                    continue;
                }
                let out = evaluate(&x);
                spent += 1;
                if better(&out, &center_out, minimize) {
                    center = x.clone();
                    center_out = out.clone();
                    improved = true;
                }
                evaluated.push((x, out));
            }
        }
        if !improved {
            for s in steps.iter_mut() {
                *s *= 0.5;
            }
            if steps
                .iter()
                .zip(bounds)
                .all(|(s, b)| *s < 1e-3 * (b[1] - b[0]))
            {
                break;
            }
        }
    }
    evaluated
}

// ---------------------------------------------------------------------------
// Axis application — joint multi-axis mutation of a spec document.
// ---------------------------------------------------------------------------

pub fn apply_axes(base: &Value, axes: &[Axis], x: &[f64]) -> Result<Value, String> {
    let mut doc = base.clone();
    // composition axes are applied jointly: axed elements take their values,
    // all others are rescaled so the total is 100 wt%.
    let comp_axes: Vec<(usize, &str)> = axes
        .iter()
        .enumerate()
        .filter_map(|(i, a)| match a {
            Axis::CompositionFraction { element, .. } => Some((i, element.as_str())),
            _ => None,
        })
        .collect();
    if !comp_axes.is_empty() {
        let comp = doc["material"]["composition"]
            .as_object_mut()
            .ok_or("material.composition is not a map")?;
        let axed_total: f64 = comp_axes.iter().map(|(i, _)| x[*i]).sum();
        if axed_total > 100.0 + 1e-9 {
            return Err(format!("axed composition sum {axed_total} exceeds 100 wt%"));
        }
        let rest: f64 = comp
            .iter()
            .filter(|(k, _)| !comp_axes.iter().any(|(_, e)| k.eq_ignore_ascii_case(e)))
            .map(|(_, v)| v.as_f64().unwrap_or(0.0))
            .sum();
        if rest <= 0.0 && axed_total < 100.0 {
            return Err("cannot renormalize: no free constituents".into());
        }
        let scale = (100.0 - axed_total).max(0.0) / rest;
        let mut set: Vec<bool> = vec![false; comp_axes.len()];
        for (k, v) in comp.iter_mut() {
            if let Some((pos, (i, _))) = comp_axes
                .iter()
                .enumerate()
                .find(|(_, (_, e))| k.eq_ignore_ascii_case(e))
            {
                *v = Value::from(x[*i]);
                set[pos] = true;
            } else {
                *v = Value::from(v.as_f64().unwrap_or(0.0) * scale);
            }
        }
        for (pos, (i, element)) in comp_axes.iter().enumerate() {
            if !set[pos] {
                comp.insert(element.to_string(), Value::from(x[*i]));
            }
        }
    }
    for (i, axis) in axes.iter().enumerate() {
        match axis {
            Axis::CompositionFraction { .. } => {}
            Axis::FluxScale { .. } => {
                let cur = doc["spectrum"]["total"].as_f64().unwrap_or(0.0);
                if cur <= 0.0 {
                    return Err("spectrum.total is not positive".into());
                }
                doc["spectrum"]["total"] = Value::from(cur * x[i]);
            }
            Axis::StepDt { step, .. } => {
                let steps = doc["schedule"]
                    .as_array_mut()
                    .ok_or("schedule is not a list")?;
                let s = steps
                    .get_mut(*step)
                    .ok_or("step_dt step index out of range")?;
                if x[i] <= 0.0 {
                    return Err("step duration must be positive".into());
                }
                s["dt"] = Value::from(format!("{} s", x[i]));
            }
        }
    }
    Ok(doc)
}

// ---------------------------------------------------------------------------
// Spec/document plumbing
// ---------------------------------------------------------------------------

pub fn sha256_hex(bytes: &[u8]) -> String {
    hex_lower(&Sha256::digest(bytes))
}

fn hex_lower(bytes: &[u8]) -> String {
    let mut s = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        s.push_str(&format!("{b:02x}"));
    }
    s
}

fn param_digest(x: &[f64]) -> String {
    // rounded to 12 significant digits so resume matches bitwise-equal params
    let mut s = String::new();
    for v in x {
        s.push_str(&format!("{v:.12e};"));
    }
    sha256_hex(s.as_bytes())
}

pub fn load_optimize_spec(path: &Path) -> Result<OptimizeSpec, String> {
    let text = std::fs::read_to_string(path)
        .map_err(|e| format!("cannot read {}: {e}", path.display()))?;
    let spec: OptimizeSpec =
        serde_json::from_str(&text).map_err(|e| format!("cannot parse {}: {e}", path.display()))?;
    spec.validate()?;
    Ok(spec)
}

impl OptimizeSpec {
    fn validate(&self) -> Result<(), String> {
        if self.schema != OPT_SCHEMA {
            return Err(format!("schema must be '{OPT_SCHEMA}'"));
        }
        if self.design_axes.is_empty() || self.design_axes.len() > 6 {
            return Err("design_axes needs 1..=6 entries".into());
        }
        for (i, a) in self.design_axes.iter().enumerate() {
            let [lo, hi] = a.bounds();
            if !lo.is_finite() || !hi.is_finite() || lo >= hi {
                return Err(format!("axis {i} bounds must satisfy lo < hi"));
            }
        }
        if !matches!(
            self.optimizer.algorithm.as_str(),
            "lhs_coordinate" | "lhs_corners_coordinate"
        ) {
            return Err(format!(
                "unknown optimizer algorithm '{}'",
                self.optimizer.algorithm
            ));
        }
        let total = self.optimizer.init_points + self.optimizer.refine_points;
        if self.optimizer.init_points == 0 || total == 0 || total > MAX_EVALS {
            return Err(format!(
                "optimizer budget must be 1..={MAX_EVALS} evaluations"
            ));
        }
        if !matches!(self.objective.direction.as_str(), "min" | "max") {
            return Err("objective.direction must be 'min' or 'max'".into());
        }
        if self.objective.response == "activity:*" {
            return Err("activity:* cannot be an objective".into());
        }
        if !self.objectives.is_empty() {
            if self.objectives.len() < 2 {
                return Err("objectives needs >=2 entries when present".into());
            }
            let mut names = std::collections::BTreeSet::new();
            for (i, o) in self.objectives.iter().enumerate() {
                if !matches!(o.direction.as_str(), "min" | "max") {
                    return Err(format!("objectives[{i}].direction must be 'min' or 'max'"));
                }
                if o.response == "activity:*" {
                    return Err(format!(
                        "objectives[{i}]: activity:* cannot be an objective"
                    ));
                }
                if !names.insert(objective_name(o)) {
                    return Err(format!(
                        "objectives[{i}] duplicates the name '{}'",
                        objective_name(o)
                    ));
                }
            }
        }
        for c in &self.constraints {
            if c.name.is_empty() {
                return Err("constraint name must be nonempty".into());
            }
            if !matches!(c.sense.as_str(), "le" | "ge") {
                return Err(format!(
                    "constraint '{}' sense must be 'le' or 'ge'",
                    c.name
                ));
            }
            match c.kind.as_str() {
                "axis" => {
                    let ok = c.axis.map(|i| i < self.design_axes.len()).unwrap_or(false);
                    if !ok {
                        return Err(format!(
                            "constraint '{}': axis index required and must be < design_axes.len()",
                            c.name
                        ));
                    }
                }
                "response" => {
                    if c.response.as_deref() == Some("activity:*") {
                        return Err(format!(
                            "constraint '{}': activity:* cannot be constrained",
                            c.name
                        ));
                    }
                    if c.response.is_none() {
                        return Err(format!("constraint '{}': response required", c.name));
                    }
                    if !c.time_s.is_finite() {
                        return Err(format!("constraint '{}': time_s must be finite", c.name));
                    }
                }
                other => return Err(format!("constraint '{}': unknown kind '{other}'", c.name)),
            }
            if !c.limit.is_finite() {
                return Err(format!("constraint '{}': limit must be finite", c.name));
            }
        }
        if !self.objective.time_s.is_finite() {
            return Err("objective.time_s must be finite".into());
        }
        Ok(())
    }
}

/// Extract a response value or band edge from a step — typed access, no
/// full-result serialization (a serialized step is ~100 MB and would blow
/// the bounded-memory envelope across candidates).
pub(crate) fn response_edge(
    step: &actinv_core::run::StepOut,
    response: &str,
    edge: Edge,
) -> Result<Option<f64>, String> {
    let nominal = |st: &actinv_core::run::StepOut| -> Option<f64> {
        if let Some(k) = response.strip_prefix("heat.") {
            match k {
                "total" => Some(st.heat_W_per_g.total),
                "alpha" => Some(st.heat_W_per_g.alpha),
                "beta" => Some(st.heat_W_per_g.beta),
                "gamma" => Some(st.heat_W_per_g.gamma),
                _ => None,
            }
        } else if response == "activity.total" {
            // mirrors snapshot_value: sum over every activity entry
            Some(st.activity_Bq_per_g.values().sum())
        } else {
            response
                .strip_prefix("activity:")
                .map(|k| st.activity_Bq_per_g.get(k).copied().unwrap_or(0.0))
        }
    };
    if edge == Edge::Nominal {
        return Ok(nominal(step));
    }
    let u = step
        .uncertainty
        .as_ref()
        .and_then(|u| u.responses.get(response))
        .ok_or_else(|| format!("band edge for '{response}' not present in step uncertainty"))?;
    let iv = if matches!(edge, Edge::NormalLower | Edge::NormalUpper) {
        u.normal_interval
    } else {
        u.conservative_interval
    };
    Ok(Some(
        if matches!(edge, Edge::NormalLower | Edge::ConservativeLower) {
            iv[0]
        } else {
            iv[1]
        },
    ))
}

/// Select the step whose cumulative t_s is nearest `time_s`.
pub(crate) fn select_step(
    steps: &[actinv_core::run::StepOut],
    time_s: f64,
) -> Result<&actinv_core::run::StepOut, String> {
    let mut best: Option<(&actinv_core::run::StepOut, f64)> = None;
    for s in steps {
        let t = s.t_s;
        if !t.is_finite() {
            continue;
        }
        let rel = ((t - time_s) / time_s.max(1e-30)).abs();
        if best.map(|(_, b)| rel < b).unwrap_or(true) {
            best = Some((s, rel));
        }
    }
    let (step, rel) = best.ok_or("spec produced no steps with a finite t_s")?;
    if rel > TIME_REL_TOL && time_s > 0.0 {
        return Err(format!(
            "no step within {TIME_REL_TOL} of time_s={time_s} (nearest rel dev {rel:.3e})"
        ));
    }
    Ok(step)
}

/// Full candidate evaluation: mutate spec, solve, extract objective/constraints.
/// Returns (outcome, canonical_spec_json, detail_map).
fn evaluate_candidate(
    base_doc: &Value,
    opt: &OptimizeSpec,
    x: &[f64],
    cache: &mut actinv_core::run::PreparedCache,
) -> (EvalOutcome, Option<String>, BTreeMap<String, Value>) {
    let mut detail = BTreeMap::new();
    let doc = match apply_axes(base_doc, &opt.design_axes, x) {
        Ok(d) => d,
        Err(e) => {
            return (
                EvalOutcome {
                    objective: None,
                    violations: vec![None; opt.constraints.len()],
                    status: format!("axis_apply_error: {e}"),
                    ..Default::default()
                },
                None,
                detail,
            )
        }
    };
    let canon = serde_json::to_string(&doc).unwrap_or_default();

    // Axis constraints evaluate on the parameter vector — a violated one is
    // ledgered infeasible and never consumes a solver run.
    let axis_violations: Vec<Option<f64>> = opt
        .constraints
        .iter()
        .map(|c| {
            if c.kind != "axis" {
                return None;
            }
            let i = c.axis.unwrap();
            let v = x[i];
            let viol = if c.sense == "le" {
                (v - c.limit) / c.limit.abs().max(TINY)
            } else {
                (c.limit - v) / c.limit.abs().max(TINY)
            };
            detail.insert(
                format!("constraint.{}", c.name),
                serde_json::json!({"axis": i, "value": v, "limit": c.limit,
                                   "violation": viol}),
            );
            Some(viol)
        })
        .collect();
    if let Some(failed) = opt
        .constraints
        .iter()
        .zip(&axis_violations)
        .find(|(_, v)| v.map(|v| v > 0.0).unwrap_or(false))
    {
        // response constraints stay not-computable: no solve was run
        let violations: Vec<Option<f64>> = opt
            .constraints
            .iter()
            .zip(&axis_violations)
            .map(|(c, av)| if c.kind == "axis" { *av } else { None })
            .collect();
        for c in &opt.constraints {
            if c.kind == "response" {
                detail.insert(
                    format!("constraint.{}", c.name),
                    Value::from("constraint_not_evaluated: axis constraint violated"),
                );
            }
        }
        return (
            EvalOutcome {
                objective: None,
                violations,
                status: format!("infeasible_by_axis: {}", failed.0.name),
                ..Default::default()
            },
            Some(canon),
            detail,
        );
    }

    let resolved = match crate::resolve_catalog_json(&canon) {
        Ok(t) => t,
        Err(e) => {
            return (
                EvalOutcome {
                    objective: None,
                    violations: vec![None; opt.constraints.len()],
                    status: format!("catalog_resolve_error: {e}"),
                    ..Default::default()
                },
                Some(canon),
                detail,
            )
        }
    };
    let spec = match actinv_core::spec::Spec::from_json(&resolved) {
        Ok(s) => s,
        Err(e) => {
            return (
                EvalOutcome {
                    objective: None,
                    violations: vec![None; opt.constraints.len()],
                    status: format!("spec_error: {e}"),
                    ..Default::default()
                },
                Some(canon),
                detail,
            )
        }
    };
    // Candidates share the prepared-input cache: design axes never touch
    // the file-derived inputs (library, decay, spectra), so after the
    // first solve preparation cost is paid once per optimize session.
    // The cache fingerprints every prep-relevant input, so an axis that
    // did change them would miss and reprepare — never a stale reuse.
    let result = match actinv_core::run::run_with_cache(&spec, "optimize", cache) {
        Ok(r) => {
            detail.insert(
                "prepared_cache".into(),
                serde_json::json!({"hit": cache.last_hit(),
                                   "fingerprint_ms": cache.last_fingerprint_ms()}),
            );
            r
        }
        Err(e) => {
            return (
                EvalOutcome {
                    objective: None,
                    violations: vec![None; opt.constraints.len()],
                    status: format!("run_error: {e}"),
                    ..Default::default()
                },
                Some(canon),
                detail,
            )
        }
    };
    if result.steps.is_empty() {
        return (
            EvalOutcome {
                objective: None,
                violations: vec![None; opt.constraints.len()],
                status: "run_error: empty steps".into(),
                ..Default::default()
            },
            Some(canon),
            detail,
        );
    }
    let steps = &result.steps;

    let objective = match select_step(steps, opt.objective.time_s)
        .and_then(|st| response_edge(st, &opt.objective.response, opt.objective.edge))
    {
        Ok(Some(v)) => {
            detail.insert(
                "objective_components".into(),
                serde_json::json!({"edge": format!("{:?}", opt.objective.edge)}),
            );
            Some(v)
        }
        Ok(None) => {
            detail.insert("objective_error".into(), Value::from("response absent"));
            None
        }
        Err(e) => {
            detail.insert("objective_error".into(), Value::from(e));
            None
        }
    };

    let mut violations = Vec::with_capacity(opt.constraints.len());
    for (c, av) in opt.constraints.iter().zip(&axis_violations) {
        if c.kind == "axis" {
            violations.push(*av);
            continue;
        }
        let step = match select_step(steps, c.time_s) {
            Ok(st) => st,
            Err(e) => {
                detail.insert(
                    format!("constraint.{}", c.name),
                    Value::from(format!("constraint_not_computable: {e}")),
                );
                violations.push(None);
                continue;
            }
        };
        match response_edge(step, c.response.as_deref().unwrap(), c.edge) {
            Ok(Some(edge)) => {
                let viol = if c.sense == "le" {
                    (edge - c.limit) / c.limit.abs().max(TINY)
                } else {
                    (c.limit - edge) / c.limit.abs().max(TINY)
                };
                let mut entry =
                    serde_json::json!({"edge": edge, "limit": c.limit, "violation": viol});
                if c.edge != Edge::Nominal {
                    if let Ok(Some(nominal)) =
                        response_edge(step, c.response.as_deref().unwrap(), Edge::Nominal)
                    {
                        let nviol = if c.sense == "le" {
                            (nominal - c.limit) / c.limit.abs().max(TINY)
                        } else {
                            (c.limit - nominal) / c.limit.abs().max(TINY)
                        };
                        entry["nominal_edge"] = serde_json::json!(nominal);
                        entry["nominal_violation"] = serde_json::json!(nviol);
                    }
                }
                detail.insert(format!("constraint.{}", c.name), entry);
                violations.push(Some(viol));
            }
            Ok(None) => {
                detail.insert(
                    format!("constraint.{}", c.name),
                    Value::from("constraint_not_computable"),
                );
                violations.push(None);
            }
            Err(e) => {
                detail.insert(
                    format!("constraint.{}", c.name),
                    Value::from(format!("constraint_not_computable: {e}")),
                );
                violations.push(None);
            }
        }
    }

    // P67: extra objectives are read off this same solve — no extra runs.
    let objective_values: Vec<Option<f64>> = opt
        .objectives
        .iter()
        .map(|o| {
            select_step(steps, o.time_s)
                .ok()
                .and_then(|st| response_edge(st, &o.response, o.edge).ok().flatten())
        })
        .collect();
    if !opt.objectives.is_empty() {
        detail.insert(
            "objectives".into(),
            serde_json::json!(opt
                .objectives
                .iter()
                .map(objective_name)
                .zip(objective_values.iter().copied())
                .collect::<BTreeMap<String, Option<f64>>>()),
        );
    }
    (
        EvalOutcome {
            objective,
            violations,
            objective_values,
            status: "executed".into(),
        },
        Some(canon),
        detail,
    )
}

// ---------------------------------------------------------------------------
// Driver
// ---------------------------------------------------------------------------

pub struct OptimizeSummary {
    pub n_evals: usize,
    pub best_feasible: Option<usize>,
    pub infeasible: bool,
    pub wall_s: f64,
    pub out_dir: PathBuf,
}

pub fn run_optimize(
    optspec_path: &str,
    out_arg: Option<&str>,
    resume: bool,
) -> Result<OptimizeSummary, String> {
    let started = std::time::Instant::now();
    let opt_path = Path::new(optspec_path);
    let opt = load_optimize_spec(opt_path)?;
    let opt_dir = opt_path
        .parent()
        .map(Path::to_path_buf)
        .unwrap_or_else(|| PathBuf::from("."));
    let base_path = opt_dir.join(&opt.base_spec);
    let base_text = std::fs::read_to_string(&base_path)
        .map_err(|e| format!("cannot read base spec {}: {e}", base_path.display()))?;
    let base_doc: Value =
        serde_json::from_str(&base_text).map_err(|e| format!("cannot parse base spec: {e}"))?;

    let out_dir = out_arg
        .map(PathBuf::from)
        .unwrap_or_else(|| opt_dir.join("optimize_out"));
    std::fs::create_dir_all(&out_dir)
        .map_err(|e| format!("cannot create {}: {e}", out_dir.display()))?;
    let cand_dir = out_dir.join("candidates");
    std::fs::create_dir_all(&cand_dir)
        .map_err(|e| format!("cannot create {}: {e}", cand_dir.display()))?;
    let ledger_path = out_dir.join("optimize_ledger.jsonl");
    let result_path = out_dir.join("optimize_result.json");

    let bounds: Vec<[f64; 2]> = opt.design_axes.iter().map(|a| a.bounds()).collect();
    let minimize = opt.objective.direction == "min";

    // Resume cache: param digest -> reconstructed outcome + its ledger id.
    let mut resumed: std::collections::HashMap<String, (usize, EvalOutcome, Option<String>)> =
        Default::default();
    if resume && ledger_path.exists() {
        let text = std::fs::read_to_string(&ledger_path)
            .map_err(|e| format!("cannot read ledger: {e}"))?;
        for line in text.lines() {
            if let Ok(row) = serde_json::from_str::<Value>(line) {
                if let Some(d) = row["param_digest"].as_str() {
                    let out = EvalOutcome {
                        objective: row["objective"].as_f64(),
                        violations: row["violations"]
                            .as_array()
                            .map(|a| a.iter().map(|v| v.as_f64()).collect())
                            .unwrap_or_default(),
                        objective_values: row["objectives"]
                            .as_object()
                            .map(|m| {
                                opt.objectives
                                    .iter()
                                    .map(|o| m.get(&objective_name(o)).and_then(Value::as_f64))
                                    .collect::<Vec<_>>()
                            })
                            .unwrap_or_default(),
                        status: "resumed".into(),
                    };
                    resumed.insert(
                        d.to_string(),
                        (
                            row["eval_id"].as_u64().unwrap_or(0) as usize,
                            out,
                            row["spec_sha256"].as_str().map(str::to_string),
                        ),
                    );
                }
            }
        }
    }

    struct Row {
        eval_id: usize,
        x: Vec<f64>,
        out: EvalOutcome,
        spec_sha: Option<String>,
        constraint_detail: BTreeMap<String, Value>,
    }
    let mut rows: Vec<Row> = Vec::new();
    let mut next_id = resumed.values().map(|(id, _, _)| id + 1).max().unwrap_or(0);
    let mut eval_err: Option<String> = None;

    // The engine drives point selection; each point is evaluated through the
    // identical run::run path and ledgered before the next begins.
    let mut ledger_file = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&ledger_path)
        .map_err(|e| format!("cannot open ledger: {e}"))?;
    let evaluated_count = {
        let ledger = &mut ledger_file;
        let resumed = &resumed;
        let cand_dir = &cand_dir;
        let opt_ref = &opt;
        let base_ref = &base_doc;
        let rows_ref = &mut rows;
        let next_id_ref = &mut next_id;
        let err_ref = &mut eval_err;
        let mut cache = actinv_core::run::PreparedCache::new();
        let cache_ref = &mut cache;
        run_search(&bounds, &opt.optimizer, minimize, move |x| {
            let digest = param_digest(x);
            if let Some((id, out, sha)) = resumed.get(&digest) {
                rows_ref.push(Row {
                    eval_id: *id,
                    x: x.to_vec(),
                    out: out.clone(),
                    spec_sha: sha.clone(),
                    constraint_detail: BTreeMap::new(),
                });
                return out.clone();
            }
            let t0 = std::time::Instant::now();
            let (outcome, canon, detail) = evaluate_candidate(base_ref, opt_ref, x, cache_ref);
            let row_detail = detail.clone();
            let wall = t0.elapsed().as_secs_f64();
            let spec_sha = canon.as_deref().map(|c| sha256_hex(c.as_bytes()));
            let eval_id = *next_id_ref;
            *next_id_ref += 1;
            let row = serde_json::json!({
                "eval_id": eval_id,
                "x": x,
                "param_digest": digest,
                "spec_sha256": spec_sha,
                "status": outcome.status,
                "objective": outcome.objective,
                "objectives": opt_ref
                    .objectives
                    .iter()
                    .map(objective_name)
                    .zip(outcome.objective_values.iter().copied())
                    .collect::<BTreeMap<String, Option<f64>>>(),
                "violations": outcome.violations,
                "feasible": outcome.feasible(),
                "violation_sum": outcome.violation_sum(),
                "constraints": detail,
                "wall_s": wall,
            });
            if let Err(e) = writeln!(ledger, "{}", serde_json::to_string(&row).unwrap())
                .and_then(|_| ledger.flush())
            {
                *err_ref = Some(format!("ledger write: {e}"));
            }
            if let Some(c) = &canon {
                let p = cand_dir.join(format!("eval_{eval_id:04}.json"));
                let _ = std::fs::write(&p, c);
            }
            rows_ref.push(Row {
                eval_id,
                x: x.to_vec(),
                out: outcome.clone(),
                spec_sha,
                constraint_detail: row_detail,
            });
            outcome
        })
        .len()
    };
    if let Some(e) = eval_err {
        return Err(e);
    }
    let _ = evaluated_count;

    // rank
    let mut order: Vec<usize> = (0..rows.len()).collect();
    order.sort_by(|&a, &b| {
        let oa = &rows[a].out;
        let ob = &rows[b].out;
        if better(oa, ob, minimize) {
            std::cmp::Ordering::Less
        } else if better(ob, oa, minimize) {
            std::cmp::Ordering::Greater
        } else {
            rows[a].eval_id.cmp(&rows[b].eval_id)
        }
    });
    let best_idx = order.iter().copied().find(|&i| rows[i].out.feasible());
    let infeasible = best_idx.is_none();

    // winner re-verification: fresh in-process run of the winning spec
    let mut verify = serde_json::json!({"performed": false});
    let mut best_block = Value::Null;
    if let Some(bi) = best_idx {
        let row = &rows[bi];
        let cand_path = cand_dir.join(format!("eval_{:04}.json", row.eval_id));
        let canon = row
            .spec_sha
            .is_some()
            .then(|| std::fs::read_to_string(&cand_path).ok())
            .flatten();
        if let Some(c) = canon {
            let re_spec = actinv_core::spec::Spec::from_json(&crate::resolve_catalog_json(&c)?)?;
            let re = actinv_core::run::run(&re_spec, "optimize-verify")?;
            let re_obj = select_step(&re.steps, opt.objective.time_s)
                .ok()
                .and_then(|st| {
                    response_edge(st, &opt.objective.response, opt.objective.edge)
                        .ok()
                        .flatten()
                });
            let identical = re_obj == row.out.objective;
            // Re-execute the winner's banded constraint edges too — the
            // certification stands on the re-run, not only the ledgered
            // numbers.
            let mut constraint_checks = Vec::new();
            let mut constraints_identical = true;
            let has_detail = row
                .constraint_detail
                .keys()
                .any(|k| k.starts_with("constraint."));
            for c in opt.constraints.iter().filter(|c| c.kind == "response") {
                let name = format!("constraint.{}", c.name);
                let re_edge = select_step(&re.steps, c.time_s).ok().and_then(|st| {
                    response_edge(st, c.response.as_deref().unwrap(), c.edge)
                        .ok()
                        .flatten()
                });
                let ledgered = row
                    .constraint_detail
                    .get(&name)
                    .and_then(|d| d.get("edge"))
                    .and_then(Value::as_f64);
                // Resumed rows carry no detail: the fresh re-run is the
                // certification edge on its own — skip the comparison.
                let same = if !has_detail {
                    re_edge.is_some()
                } else {
                    match (re_edge, ledgered) {
                        (Some(a), Some(b)) => a == b,
                        (None, None) => true,
                        _ => false,
                    }
                };
                constraints_identical &= same;
                constraint_checks.push(serde_json::json!({
                    "name": c.name,
                    "edge_rule": format!("{:?}", c.edge).to_lowercase(),
                    "re_executed_edge": re_edge,
                    "ledgered_edge": ledgered,
                    "bit_identical": same,
                }));
            }
            verify = serde_json::json!({
                "performed": true,
                "re_executed_objective": re_obj,
                "ledgered_objective": row.out.objective,
                "bit_identical": identical,
                "constraint_edges": constraint_checks,
                "constraint_edges_bit_identical": constraints_identical
                    && !constraint_checks.is_empty(),
            });
            if !identical || !constraints_identical {
                return Err("winner re-execution diverged from the ledgered value".into());
            }
        } else {
            return Err("best candidate has no recorded spec — cannot verify".into());
        }
        best_block = serde_json::json!({
            "eval_id": row.eval_id,
            "x": row.x,
            "objective": row.out.objective,
            "spec_sha256": row.spec_sha,
            "spec_file": format!("candidates/eval_{:04}.json", row.eval_id),
        });
    }

    // ---- P56 certification: the formal statement that the winner satisfies
    // every response constraint at its declared band edge, plus the count of
    // candidates a nominal-only search would have over-certified.
    let banded: Vec<&Constraint> = opt
        .constraints
        .iter()
        .filter(|c| c.kind == "response" && c.edge != Edge::Nominal)
        .collect();
    let n_response = opt
        .constraints
        .iter()
        .filter(|c| c.kind == "response")
        .count();
    // Re-read the append-only ledger: covers resumed rows too.
    let mut nominal_overcertify = 0usize;
    if !banded.is_empty() {
        if let Ok(text) = std::fs::read_to_string(&ledger_path) {
            for line in text.lines().filter(|l| !l.trim().is_empty()) {
                let Ok(row) = serde_json::from_str::<Value>(line) else {
                    continue;
                };
                let violations = row["violations"].as_array();
                let detail = &row["constraints"];
                let mut nominal_ok = true;
                let mut banded_fail = false;
                let mut has_all = true;
                for (i, c) in opt.constraints.iter().enumerate() {
                    let v = violations.and_then(|vs| vs.get(i)).and_then(Value::as_f64);
                    if c.kind == "axis" {
                        if v.is_some_and(|x| x > 0.0) {
                            nominal_ok = false;
                        }
                        continue;
                    }
                    let entry = detail.get(format!("constraint.{}", c.name));
                    if c.edge == Edge::Nominal {
                        if v.is_none_or(|x| x > 0.0) {
                            nominal_ok = false;
                        }
                        continue;
                    }
                    let Some(entry) = entry else {
                        has_all = false;
                        break;
                    };
                    let nviol = entry["nominal_violation"].as_f64();
                    match nviol {
                        Some(x) if x <= 0.0 => {}
                        _ => nominal_ok = false,
                    }
                    if v.is_some_and(|x| x > 0.0) {
                        banded_fail = true;
                    }
                }
                if has_all && nominal_ok && banded_fail {
                    nominal_overcertify += 1;
                }
            }
        }
    }
    let (confidence, covariance_id) = base_doc
        .get("uncertainty")
        .map(|u| {
            (
                u.get("confidence_level").cloned().unwrap_or(Value::Null),
                u.get("covariance")
                    .map(|c| {
                        serde_json::json!({
                            "path": c.get("path"),
                            "sha256": c.get("sha256"),
                        })
                    })
                    .unwrap_or(Value::Null),
            )
        })
        .unwrap_or((Value::Null, Value::Null));
    let winner_constraints: Vec<Value> = best_idx
        .map(|bi| {
            opt.constraints
                .iter()
                .filter(|c| c.kind == "response")
                .map(|c| {
                    let entry = rows[bi]
                        .constraint_detail
                        .get(&format!("constraint.{}", c.name))
                        .cloned()
                        .unwrap_or(Value::Null);
                    let edge_v = entry.get("edge").and_then(Value::as_f64);
                    let margin = edge_v.map(|e| {
                        if c.sense == "le" {
                            (c.limit - e) / c.limit.abs().max(TINY)
                        } else {
                            (e - c.limit) / c.limit.abs().max(TINY)
                        }
                    });
                    serde_json::json!({
                        "name": c.name,
                        "edge_rule": format!("{:?}", c.edge).to_lowercase(),
                        "edge_value": edge_v,
                        "nominal_edge": entry.get("nominal_edge"),
                        "limit": c.limit,
                        "sense": c.sense,
                        "margin_fraction": margin,
                    })
                })
                .collect()
        })
        .unwrap_or_default();
    let certification = if let Some(bi) = best_idx {
        let k = winner_constraints
            .iter()
            .filter(|c| c["margin_fraction"].as_f64().is_some_and(|m| m >= 0.0))
            .count();
        serde_json::json!({
            "statement": format!(
                "eval {} satisfies {}/{} response constraints at their declared \
                 band edges ({} at {}) under the base spec's covariance set",
                rows[bi].eval_id, k, n_response,
                banded.iter().map(|c| format!("{:?}", c.edge).to_lowercase())
                    .collect::<Vec<_>>().join("/"),
                confidence),
            "confidence_level": confidence,
            "covariance": covariance_id,
            "winner": {
                "eval_id": rows[bi].eval_id,
                "objective": {
                    "value": rows[bi].out.objective,
                    "edge": format!("{:?}", opt.objective.edge).to_lowercase(),
                    "response": opt.objective.response,
                },
                "constraints": winner_constraints,
            },
            "nominal_would_overcertify": nominal_overcertify,
        })
    } else {
        serde_json::json!({
            "statement": "no candidate is feasible under the declared band \
                          edges — the design box contains no certifiable design",
            "confidence_level": confidence,
            "covariance": covariance_id,
            "winner": null,
            "nominal_would_overcertify": nominal_overcertify,
        })
    };

    // ---- P67 Pareto frontier: the nondominated subset of evals that are
    // certified under the declared constraint edges (violation_sum ≤ 0)
    // and have every listed objective computable.
    let pareto_block = if opt.objectives.len() >= 2 {
        let eligible: Vec<usize> = (0..rows.len())
            .filter(|&i| {
                rows[i].out.violation_sum() <= 0.0
                    && rows[i].out.objective_values.iter().all(Option::is_some)
            })
            .collect();
        let dominates = |a: usize, b: usize| -> bool {
            let mut strict = false;
            for (j, o) in opt.objectives.iter().enumerate() {
                let av = rows[a].out.objective_values[j].unwrap();
                let bv = rows[b].out.objective_values[j].unwrap();
                let (better, worse) = if o.direction == "min" {
                    (av < bv, av > bv)
                } else {
                    (av > bv, av < bv)
                };
                if worse {
                    return false;
                }
                strict |= better;
            }
            strict
        };
        let front: Vec<usize> = eligible
            .iter()
            .copied()
            .filter(|&a| !eligible.iter().any(|&b| b != a && dominates(b, a)))
            .collect();
        Some(serde_json::json!({
            "objectives": opt.objectives.iter().map(|o| serde_json::json!({
                "name": objective_name(o),
                "response": o.response,
                "time_s": o.time_s,
                "edge": format!("{:?}", o.edge).to_lowercase(),
                "direction": o.direction,
            })).collect::<Vec<_>>(),
            "n_evals": rows.len(),
            "n_certified": eligible.len(),
            "n_front": front.len(),
            "front": front.iter().map(|&i| serde_json::json!({
                "eval_id": rows[i].eval_id,
                "x": rows[i].x,
                "param_digest": param_digest(&rows[i].x),
                "objectives": opt.objectives.iter()
                    .map(objective_name)
                    .zip(rows[i].out.objective_values.iter().copied())
                    .collect::<BTreeMap<String, Option<f64>>>(),
                "violation_sum": rows[i].out.violation_sum(),
                "certified": true,
            })).collect::<Vec<_>>(),
        }))
    } else {
        None
    };

    let opt_text = std::fs::read_to_string(opt_path).unwrap_or_default();
    let mut result = serde_json::json!({
        "schema": "actinv-optimize-result-1",
        "optspec_sha256": sha256_hex(opt_text.as_bytes()),
        "base_spec": opt.base_spec,
        "optimizer": {
            "algorithm": opt.optimizer.algorithm,
            "seed": opt.optimizer.seed,
            "init_points": opt.optimizer.init_points,
            "refine_points": opt.optimizer.refine_points,
        },
        "objective": {
            "response": opt.objective.response,
            "time_s": opt.objective.time_s,
            "edge": format!("{:?}", opt.objective.edge).to_lowercase(),
            "direction": opt.objective.direction,
        },
        "constraints": opt.constraints.iter().map(|c| serde_json::json!({
            "name": c.name, "kind": c.kind, "response": c.response,
            "time_s": c.time_s, "axis": c.axis,
            "edge": format!("{:?}", c.edge).to_lowercase(),
            "sense": c.sense, "limit": c.limit,
        })).collect::<Vec<_>>(),
        "n_evals": rows.len(),
        "resumed_hits": resumed.len(),
        "resumed": resume && !resumed.is_empty(),
        "infeasible": infeasible,
        "certification": certification,
        "best_feasible": best_block,
        "ranked": order.iter().map(|&i| serde_json::json!({
            "eval_id": rows[i].eval_id,
            "x": rows[i].x,
            "objective": rows[i].out.objective,
            "feasible": rows[i].out.feasible(),
            "violation_sum": rows[i].out.violation_sum(),
            "status": rows[i].out.status,
        })).collect::<Vec<_>>(),
        "winner_verification": verify,
        "wall_s": started.elapsed().as_secs_f64(),
    });
    if let Some(p) = pareto_block {
        result
            .as_object_mut()
            .expect("result object")
            .insert("pareto".into(), p);
    }
    std::fs::write(&result_path, serde_json::to_string_pretty(&result).unwrap())
        .map_err(|e| format!("cannot write result: {e}"))?;

    Ok(OptimizeSummary {
        n_evals: rows.len(),
        best_feasible: best_idx,
        infeasible,
        wall_s: started.elapsed().as_secs_f64(),
        out_dir,
    })
}

// ---------------------------------------------------------------------------
// Tests — synthetic landscapes only; no solver runs under cfg(test).
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    fn cfg(init: usize, refine: usize) -> OptimizerCfg {
        OptimizerCfg {
            algorithm: "lhs_coordinate".into(),
            seed: 49,
            init_points: init,
            refine_points: refine,
            refine_step_fraction: 0.25,
        }
    }

    fn cfg_corners(init: usize, refine: usize) -> OptimizerCfg {
        OptimizerCfg {
            algorithm: "lhs_corners_coordinate".into(),
            ..cfg(init, refine)
        }
    }

    fn feasible(v: f64) -> EvalOutcome {
        EvalOutcome {
            objective: Some(v),
            violations: vec![],
            status: "ok".into(),
            ..Default::default()
        }
    }

    #[test]
    fn lhs_is_seeded_and_deterministic() {
        let b = [[0.0, 1.0], [0.0, 1.0]];
        let a = run_search(&b, &cfg(8, 0), true, |x| feasible(x[0] + x[1]));
        let c = run_search(&b, &cfg(8, 0), true, |x| feasible(x[0] + x[1]));
        let pa: Vec<Vec<f64>> = a.iter().map(|(x, _)| x.clone()).collect();
        let pb: Vec<Vec<f64>> = c.iter().map(|(x, _)| x.clone()).collect();
        assert_eq!(pa, pb, "same seed must produce identical points");
        assert_eq!(pa.len(), 8);
        // every axis stratum is hit once per dimension
        for k in 0..2 {
            let mut strata: Vec<usize> = pa.iter().map(|x| (x[k] * 8.0).floor() as usize).collect();
            strata.sort();
            assert_eq!(strata, (0..8).collect::<Vec<_>>());
        }
    }

    #[test]
    fn recovers_planted_optimum() {
        let b = [[0.0, 10.0]];
        // planted minimum at x=7.5
        let res = run_search(&b, &cfg(16, 12), true, |x| feasible((x[0] - 7.5).powi(2)));
        let best = res
            .iter()
            .min_by(|a, b| a.1.objective.partial_cmp(&b.1.objective).unwrap())
            .unwrap();
        assert!(
            (best.0[0] - 7.5).abs() < 1.0,
            "planted vertex not recovered: {}",
            best.0[0]
        );
    }

    #[test]
    fn infeasible_landscape_stays_infeasible() {
        let b = [[0.0, 1.0]];
        let res = run_search(&b, &cfg(8, 4), true, |_| EvalOutcome {
            objective: Some(1.0),
            violations: vec![Some(1.0)], // always violated
            status: "ok".into(),
            ..Default::default()
        });
        assert!(res.iter().all(|(_, o)| !o.feasible()));
    }

    #[test]
    fn constraint_ranking_prefers_feasible() {
        let b = [[0.0, 1.0]];
        // objective prefers x=1 but constraint kills x>0.5
        let res = run_search(&b, &cfg(16, 8), true, |x| EvalOutcome {
            objective: Some(-x[0]),
            violations: vec![Some(if x[0] > 0.5 { x[0] - 0.5 } else { -1.0 })],
            status: "ok".into(),
            ..Default::default()
        });
        let best = res
            .iter()
            .filter(|(_, o)| o.feasible())
            .min_by(|a, b| a.1.objective.partial_cmp(&b.1.objective).unwrap())
            .unwrap();
        assert!(best.0[0] <= 0.5 + 1e-9);
        assert!(best.0[0] > 0.3, "feasible best too far from boundary");
    }

    #[test]
    fn composition_axes_renormalize_jointly() {
        let base = serde_json::json!({
            "material": {"composition": {"FE": 91.0, "CR": 9.0}},
        });
        let axes = vec![
            Axis::CompositionFraction {
                element: "NI".into(),
                bounds: [0.0, 3.0],
            },
            Axis::CompositionFraction {
                element: "MO".into(),
                bounds: [0.0, 1.0],
            },
        ];
        let doc = apply_axes(&base, &axes, &[2.0, 0.5]).unwrap();
        let c = &doc["material"]["composition"];
        let sum: f64 = ["FE", "CR", "NI", "MO"]
            .iter()
            .map(|k| c[*k].as_f64().unwrap_or(0.0))
            .sum();
        assert!((sum - 100.0).abs() < 1e-9, "composition sum {sum}");
        assert_eq!(c["NI"].as_f64().unwrap(), 2.0);
        assert_eq!(c["MO"].as_f64().unwrap(), 0.5);
    }

    #[test]
    fn corner_variant_evaluates_corners_first() {
        let b = [[0.0, 3.0], [0.0, 1.0]];
        let res = run_search(&b, &cfg_corners(6, 0), true, |x| feasible(x[0] + x[1]));
        let pts: Vec<&Vec<f64>> = res.iter().map(|(x, _)| x).collect();
        // first 2^d=4 evals are the corners in binary-counting order
        assert_eq!(*pts[0], vec![0.0, 0.0]);
        assert_eq!(*pts[1], vec![3.0, 0.0]);
        assert_eq!(*pts[2], vec![0.0, 1.0]);
        assert_eq!(*pts[3], vec![3.0, 1.0]);
        assert_eq!(res.len(), 6, "corners + remaining LHS fill");
        // planted optimum at the lower corner is found as eval 0
        let res = run_search(&b, &cfg_corners(6, 4), true, |x| {
            feasible((x[0] - 0.0).powi(2) + (x[1] - 0.0).powi(2))
        });
        let best = res
            .iter()
            .min_by(|a, b| a.1.objective.partial_cmp(&b.1.objective).unwrap())
            .unwrap();
        assert_eq!(*best.0, vec![0.0, 0.0]);
    }

    #[test]
    fn axis_constraint_validation() {
        let mut opt: OptimizeSpec = serde_json::from_value(serde_json::json!({
            "schema": "actinv-optimize-1",
            "base_spec": "b.json",
            "design_axes": [
                {"kind": "composition_fraction", "element": "NI",
                 "bounds": [0.0, 3.0]}
            ],
            "objective": {"response": "heat.total", "time_s": 1.0,
                          "edge": "nominal", "direction": "min"},
            "constraints": [
                {"name": "ni_min", "kind": "axis", "axis": 0,
                 "sense": "ge", "limit": 1.0}
            ],
            "optimizer": {"algorithm": "lhs_coordinate", "seed": 1,
                          "init_points": 4, "refine_points": 0}
        }))
        .unwrap();
        assert!(opt.validate().is_ok());

        // out-of-range axis index rejected
        opt.constraints[0].axis = Some(1);
        assert!(opt.validate().is_err());
        opt.constraints[0].axis = Some(0);

        // axis constraint missing the index rejected
        opt.constraints[0].axis = None;
        assert!(opt.validate().is_err());
        opt.constraints[0].axis = Some(0);

        // response constraint without response rejected
        opt.constraints[0].kind = "response".into();
        assert!(opt.validate().is_err());
    }
}
