#![allow(non_snake_case)] // JSON wire names carry their physical units.
//! Spectrum unfolding (P195): the group fluxes that explain measured foil activities and reaction
//! rates, adjusted from a prior spectrum by generalized least squares on ln φ.
//!
//! A foil activity is modelled by a full ACTINV solve of the foil over the problem's schedule, with
//! the P93 flux channel supplying ∂m/∂ln φ_g for every adjustable group from the same solve. A
//! reaction rate is the library's group cross section of one row folded with the spectrum. When the
//! problem names an MF=33 covariance sidecar, the cross-section covariance of every measurement is
//! carried into the measurement covariance through the P55 sparse collapse.
//!
//! The fit minimises (y − f(x))ᵀ C⁻¹ (y − f(x)) + (x − x⁰)ᵀ P⁰⁻¹ (x − x⁰) over x = ln φ. Each outer
//! iteration solves every foil at the current spectrum and builds the local model
//! f_i(x) = ln(m_i + Σ_g s_ig (exp(x_g − xᵏ_g) − 1)), exact for any measurement linear in φ; the inner
//! solve is damped Newton with that model's exact Hessian, in the whitened coordinates
//! u = L⁻¹ (x − x⁰) (P⁰ = L Lᵀ), so the prior covariance is never inverted. The outer loop stops when
//! the inner solve built at the current point no longer moves it.

use crate::quantity::{CrossSectionBarns, ParticleFlux, RatePerBarnSecond};
use crate::run;
use crate::spec::{Material, Spec, UncertaintyOptions};
use serde::Deserialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};

pub const INPUT_FORMAT: &str = "actinv-unfold-input-1";
pub const RESULT_FORMAT: &str = "actinv-unfold-1";

/// Default report bands (eV); the last edge is replaced by the structure's top.
const DEFAULT_BAND_EDGES_EV: [f64; 5] = [1e-5, 0.5, 1e5, 1e6, 1e7];
/// Response-window levels: the energies below which these fractions of a response lie.
const WINDOW_LEVELS: [f64; 3] = [0.05, 0.5, 0.95];
/// Largest number of damping increases tried before an inner step is declared stalled.
const MAX_DAMPING_TRIES: usize = 60;
/// First damping tried when the Newton step is refused, and the level below which damping is dropped.
const DAMPING_START: f64 = 1e-3;
const DAMPING_FLOOR: f64 = 1e-6;
/// Relative rise of the objective treated as rounding by the step test.
const OBJECTIVE_ROUNDING: f64 = 1e-12;
/// Relative tolerance of the internal anchor: a foil's MF=33 variance assembled here must equal the
/// variance its own solve reports.
const MF33_ANCHOR_TOLERANCE: f64 = 1e-6;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct InputFile {
    format: String,
    prior: PriorIn,
    #[serde(default)]
    foils: Vec<FoilIn>,
    #[serde(default)]
    reaction_rates: Vec<RateIn>,
    #[serde(default)]
    report_bands_eV: Option<Vec<f64>>,
    #[serde(default)]
    emit_covariance: bool,
    #[serde(default)]
    iterations: IterationsIn,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct PriorIn {
    relative_uncertainty: UncertaintyIn,
    correlation: Correlation,
}

#[derive(Debug, Deserialize)]
#[serde(untagged)]
enum UncertaintyIn {
    Uniform(f64),
    PerGroup(Vec<f64>),
}

/// Correlation of the prior's ln φ between groups.
#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum Correlation {
    Diagonal,
    /// ρ_gh = exp(−|u_g − u_h| / length), u the logarithm of the group's geometric-mean energy.
    LethargyExponential {
        length: f64,
    },
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct FoilIn {
    id: String,
    #[serde(default)]
    material: Option<Material>,
    measurements: Vec<FoilMeasurementIn>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct FoilMeasurementIn {
    nuclide: String,
    step: usize,
    activity_Bq_per_g: f64,
    sigma_Bq_per_g: f64,
    #[serde(default)]
    model_relative_uncertainty: f64,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct RateIn {
    id: String,
    target: String,
    mt: i32,
    #[serde(default)]
    product: Option<String>,
    rate_per_atom_s: f64,
    sigma_per_atom_s: f64,
    #[serde(default)]
    model_relative_uncertainty: f64,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields, default)]
struct IterationsIn {
    max_outer: usize,
    max_inner: usize,
    inner_tolerance: f64,
    outer_tolerance: f64,
}

impl Default for IterationsIn {
    fn default() -> Self {
        Self {
            max_outer: 20,
            max_inner: 100,
            inner_tolerance: 1e-10,
            outer_tolerance: 1e-9,
        }
    }
}

enum Kind {
    Activity {
        foil: usize,
        nuclide: String,
        step: usize,
    },
    Rate {
        /// Global library row.
        row: usize,
        target: String,
        mt: i32,
        product: Option<String>,
    },
}

struct Measurement {
    id: String,
    kind: Kind,
    value: f64,
    sigma: f64,
    model_relative_uncertainty: f64,
}

/// The model evaluated at one spectrum.
struct Point {
    /// Model value of every measurement.
    m: Vec<f64>,
    /// ∂m_i/∂ln φ over the adjustable groups.
    s: Vec<Vec<f64>>,
    /// Relative cross-section covariance between measurements (zero without a sidecar).
    c_xs: Vec<Vec<f64>>,
    /// Covered and uncovered library rows behind each measurement's cross-section term.
    covered: Vec<usize>,
    uncovered: Vec<usize>,
    /// The collapse's excluded (target, MT, MT1, reason) blocks at this point.
    excluded: Vec<Value>,
    modes: Vec<String>,
}

/// The library pieces a reaction rate and the covariance collapse need.
struct LibraryView {
    path: String,
    /// Every global row, in file order.
    rows: Vec<actinv_data::library::Row>,
    /// `(za, liso)` of each target index.
    targets: Vec<(i32, i32)>,
}

impl LibraryView {
    /// The group cross sections of the given global rows, read by streaming only their targets.
    fn sigma_of(&self, global_rows: &[usize]) -> Result<Vec<Vec<f64>>, String> {
        let targets: BTreeSet<usize> = global_rows.iter().map(|&r| self.rows[r].target).collect();
        let subset = actinv_data::library::read_npz_targets(&self.path, &targets)?;
        let local = self.local_index(&targets);
        global_rows
            .iter()
            .map(|r| Ok(subset.sigma(local[r]).to_vec()))
            .collect()
    }

    /// Global row → row index inside a subset library holding exactly `targets`.
    fn local_index(&self, targets: &BTreeSet<usize>) -> BTreeMap<usize, usize> {
        let mut map = BTreeMap::new();
        let mut next = 0usize;
        for (global, row) in self.rows.iter().enumerate() {
            if targets.contains(&row.target) {
                map.insert(global, next);
                next += 1;
            }
        }
        map
    }
}

/// Why an unfold stopped: a refused input (exit 2) or a failed solve (exit 1).
#[derive(Debug, Clone, PartialEq)]
pub enum UnfoldError {
    Input(String),
    Failed(String),
}

impl std::fmt::Display for UnfoldError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            UnfoldError::Input(message) | UnfoldError::Failed(message) => f.write_str(message),
        }
    }
}

impl From<String> for UnfoldError {
    fn from(message: String) -> Self {
        UnfoldError::Failed(message)
    }
}

impl From<&str> for UnfoldError {
    fn from(message: &str) -> Self {
        UnfoldError::Failed(message.to_string())
    }
}

fn refuse<T>(message: impl Into<String>) -> Result<T, UnfoldError> {
    Err(UnfoldError::Input(message.into()))
}

fn sha256_text(text: &str) -> String {
    format!("{:x}", Sha256::digest(text.as_bytes()))
}

fn index_path(library_path: &str) -> String {
    match library_path.strip_suffix(".npz") {
        Some(stem) => format!("{stem}_index.json"),
        None => format!("{library_path}_index.json"),
    }
}

fn finite_or_null(value: f64) -> Value {
    if value.is_finite() {
        json!(value)
    } else {
        Value::Null
    }
}

// ---- dense linear algebra ---------------------------------------------------------------------

/// Lower-triangular Cholesky factor of a symmetric positive-definite matrix.
#[allow(clippy::needless_range_loop)]
fn cholesky(c: &[Vec<f64>]) -> Option<Vec<Vec<f64>>> {
    let n = c.len();
    let mut l = vec![vec![0.0; n]; n];
    for i in 0..n {
        for j in 0..=i {
            let mut s = c[i][j];
            for k in 0..j {
                s -= l[i][k] * l[j][k];
            }
            if i == j {
                if !s.is_finite() || s <= 0.0 {
                    return None;
                }
                l[i][i] = s.sqrt();
            } else {
                l[i][j] = s / l[j][j];
            }
        }
    }
    Some(l)
}

/// Solve (L Lᵀ) x = b.
#[allow(clippy::needless_range_loop)]
fn cholesky_solve(l: &[Vec<f64>], b: &[f64]) -> Vec<f64> {
    let n = b.len();
    let mut y = vec![0.0; n];
    for i in 0..n {
        let mut s = b[i];
        for j in 0..i {
            s -= l[i][j] * y[j];
        }
        y[i] = s / l[i][i];
    }
    let mut x = vec![0.0; n];
    for i in (0..n).rev() {
        let mut s = y[i];
        for j in i + 1..n {
            s -= l[j][i] * x[j];
        }
        x[i] = s / l[i][i];
    }
    x
}

fn dot(a: &[f64], b: &[f64]) -> f64 {
    a.iter().zip(b).map(|(x, y)| x * y).sum()
}

fn mat_vec(a: &[Vec<f64>], v: &[f64]) -> Vec<f64> {
    a.iter().map(|row| dot(row, v)).collect()
}

// ---- prior ------------------------------------------------------------------------------------

/// The prior covariance of ln φ over the adjustable groups: D R D.
#[allow(clippy::needless_range_loop)]
pub fn prior_covariance(sd: &[f64], lethargy: &[f64], correlation: &Correlation) -> Vec<Vec<f64>> {
    let n = sd.len();
    let mut p = vec![vec![0.0; n]; n];
    for a in 0..n {
        for b in 0..n {
            let rho = match correlation {
                Correlation::Diagonal => {
                    if a == b {
                        1.0
                    } else {
                        0.0
                    }
                }
                Correlation::LethargyExponential { length } => {
                    (-(lethargy[a] - lethargy[b]).abs() / length).exp()
                }
            };
            p[a][b] = sd[a] * rho * sd[b];
        }
    }
    p
}

// ---- the local model and the inner solve ------------------------------------------------------

/// The local model built at xᵏ: f_i(x) = ln(m_i + Σ_a s_ia (exp(x_a − xᵏ_a) − 1)).
pub struct LocalModel<'a> {
    pub m: &'a [f64],
    pub s: &'a [Vec<f64>],
    pub xk: &'a [f64],
}

impl LocalModel<'_> {
    /// Model values and Jacobian at x, or `None` where a prediction is not positive.
    pub fn evaluate(&self, x: &[f64]) -> Option<(Vec<f64>, Vec<Vec<f64>>)> {
        let ratio: Vec<f64> = x.iter().zip(self.xk).map(|(a, b)| (a - b).exp()).collect();
        let mut f = Vec::with_capacity(self.m.len());
        let mut jac = Vec::with_capacity(self.m.len());
        for (mi, si) in self.m.iter().zip(self.s) {
            let mut q = *mi;
            for (sa, ra) in si.iter().zip(&ratio) {
                q += sa * (ra - 1.0);
            }
            if !q.is_finite() || q <= 0.0 {
                return None;
            }
            f.push(q.ln());
            jac.push(si.iter().zip(&ratio).map(|(sa, ra)| sa * ra / q).collect());
        }
        Some((f, jac))
    }
}

/// The Gaussian parts of the objective: prior mean and covariance, data and data covariance.
pub struct Objective<'a> {
    pub x0: &'a [f64],
    pub p0: &'a [Vec<f64>],
    pub y: &'a [f64],
    pub c: &'a [Vec<f64>],
}

pub struct InnerSolution {
    pub x: Vec<f64>,
    pub iterations: usize,
    /// Iterations whose Newton step had to be damped.
    pub damped: usize,
    pub objective: f64,
}

/// One evaluation of the objective in whitened coordinates u, with x = x⁰ + L u and P⁰ = L Lᵀ.
struct Trial {
    u: Vec<f64>,
    x: Vec<f64>,
    f: Vec<f64>,
    jac: Vec<Vec<f64>>,
    value: f64,
}

fn trial(
    model: &LocalModel,
    objective: &Objective,
    l: &[Vec<f64>],
    c_factor: &[Vec<f64>],
    u: Vec<f64>,
) -> Option<Trial> {
    let x: Vec<f64> = objective
        .x0
        .iter()
        .enumerate()
        .map(|(a, x0)| x0 + dot(&l[a][..=a], &u[..=a]))
        .collect();
    let (f, jac) = model.evaluate(&x)?;
    let r: Vec<f64> = objective.y.iter().zip(&f).map(|(a, b)| a - b).collect();
    let value = dot(&r, &cholesky_solve(c_factor, &r)) + dot(&u, &u);
    value.is_finite().then_some(Trial {
        u,
        x,
        f,
        jac,
        value,
    })
}

/// (x − x⁰)ᵀ P⁰⁻¹ (x − x⁰) as uᵀu with L u = x − x⁰, P⁰ = L Lᵀ.
#[allow(clippy::needless_range_loop)]
fn prior_quadratic(p0: &[Vec<f64>], x: &[f64], x0: &[f64]) -> Option<f64> {
    let l = cholesky(p0)?;
    let n = x.len();
    let mut u = vec![0.0; n];
    for a in 0..n {
        let mut s = x[a] - x0[a];
        for b in 0..a {
            s -= l[a][b] * u[b];
        }
        u[a] = s / l[a][a];
    }
    Some(dot(&u, &u))
}

/// Damped Newton on (y − f(x))ᵀ C⁻¹ (y − f(x)) + (x − x⁰)ᵀ P⁰⁻¹ (x − x⁰), from x, in the whitened
/// coordinates u = L⁻¹ (x − x⁰) where the prior term is uᵀu and P⁰ is never inverted. The Hessian is
/// the model's own: f_i is the logarithm of a positive sum of exponentials, so
/// ∂²f_i/∂x_a∂x_b = δ_ab J_ia − J_ia J_ib exactly. Gauss–Newton drops that term and does not contract
/// when the data contradict the prior by many standard deviations; Newton converges there too.
#[allow(clippy::needless_range_loop)]
pub fn inner_solve(
    model: &LocalModel,
    objective: &Objective,
    start_x: &[f64],
    max_inner: usize,
    tolerance: f64,
) -> Result<InnerSolution, String> {
    let n = start_x.len();
    let m = objective.y.len();
    let c_factor = cholesky(objective.c)
        .ok_or("unfold: the measurement covariance is not positive definite")?;
    let l =
        cholesky(objective.p0).ok_or("unfold: the prior covariance is not positive definite")?;
    let c_inv: Vec<Vec<f64>> = (0..m)
        .map(|i| {
            let unit: Vec<f64> = (0..m).map(|j| if i == j { 1.0 } else { 0.0 }).collect();
            cholesky_solve(&c_factor, &unit)
        })
        .collect();
    // u = L⁻¹ (x − x⁰) by forward substitution.
    let mut u0 = vec![0.0; n];
    for a in 0..n {
        let mut s = start_x[a] - objective.x0[a];
        for b in 0..a {
            s -= l[a][b] * u0[b];
        }
        u0[a] = s / l[a][a];
    }
    let mut current = trial(model, objective, &l, &c_factor, u0)
        .ok_or("unfold: the starting point predicts a non-positive measurement")?;
    let mut damping = 0.0f64;
    let mut damped = 0usize;
    for iteration in 1..=max_inner {
        let r: Vec<f64> = objective
            .y
            .iter()
            .zip(&current.f)
            .map(|(a, b)| a - b)
            .collect();
        let lambda = mat_vec(&c_inv, &r);
        // B = J L (m × n), the Jacobian in u.
        let mut b = vec![vec![0.0; n]; m];
        for i in 0..m {
            for k in 0..n {
                let jik = current.jac[i][k];
                if jik != 0.0 {
                    for a in 0..=k {
                        b[i][a] += jik * l[k][a];
                    }
                }
            }
        }
        // Gradient of half the objective: −Bᵀ λ + u.
        let gradient: Vec<f64> = (0..n)
            .map(|a| current.u[a] - (0..m).map(|i| b[i][a] * lambda[i]).sum::<f64>())
            .collect();
        // Half the Hessian: I + Bᵀ (C⁻¹ + diag λ) B − Lᵀ diag(Σ_i λ_i J_i) L.
        let kernel: Vec<Vec<f64>> = (0..m)
            .map(|i| {
                (0..m)
                    .map(|j| c_inv[i][j] + if i == j { lambda[i] } else { 0.0 })
                    .collect()
            })
            .collect();
        let kb: Vec<Vec<f64>> = (0..m)
            .map(|i| {
                (0..n)
                    .map(|a| (0..m).map(|j| kernel[i][j] * b[j][a]).sum())
                    .collect()
            })
            .collect();
        let d: Vec<f64> = (0..n)
            .map(|k| (0..m).map(|i| lambda[i] * current.jac[i][k]).sum())
            .collect();
        let mut h = vec![vec![0.0; n]; n];
        for a in 0..n {
            for bb in 0..=a {
                let mut s = if a == bb { 1.0 } else { 0.0 };
                for i in 0..m {
                    s += b[i][a] * kb[i][bb];
                }
                // (Lᵀ D L)_{a,bb} = Σ_k L_ka d_k L_kbb over k ≥ a.
                for k in a..n {
                    s -= l[k][a] * d[k] * l[k][bb];
                }
                h[a][bb] = s;
                h[bb][a] = s;
            }
        }
        let ceiling = current.value + OBJECTIVE_ROUNDING * current.value.abs().max(1.0);
        let mut accepted = None;
        for _ in 0..MAX_DAMPING_TRIES {
            let mut shifted = h.clone();
            for a in 0..n {
                shifted[a][a] += damping;
            }
            if let Some(factor) = cholesky(&shifted) {
                let minus: Vec<f64> = gradient.iter().map(|g| -g).collect();
                let delta = cholesky_solve(&factor, &minus);
                let u: Vec<f64> = current.u.iter().zip(&delta).map(|(a, b)| a + b).collect();
                if let Some(next) = trial(model, objective, &l, &c_factor, u) {
                    if next.value <= ceiling {
                        accepted = Some(next);
                        break;
                    }
                }
            }
            damping = (4.0 * damping).max(DAMPING_START);
        }
        let Some(next) = accepted else {
            return Err(
                "unfold: the inner solve stalled: no damped Newton step lowers the objective"
                    .to_string(),
            );
        };
        let step = next
            .x
            .iter()
            .zip(&current.x)
            .fold(0.0f64, |acc, (a, b)| acc.max((a - b).abs()));
        let undamped = damping == 0.0;
        if !undamped {
            damped += 1;
        }
        current = next;
        // Converged on a short undamped step: a damped step is shortened and says nothing about
        // how far the stationary point still is.
        if undamped && step <= tolerance {
            return Ok(InnerSolution {
                x: current.x,
                iterations: iteration,
                damped,
                objective: current.value,
            });
        }
        damping = if damping / 10.0 < DAMPING_FLOOR {
            0.0
        } else {
            damping / 10.0
        };
    }
    Err(format!(
        "unfold: the inner solve did not converge in {max_inner} iterations"
    ))
}

// ---- bands and windows ------------------------------------------------------------------------

/// Each requested edge snapped to the nearest group boundary in ln E; duplicates collapse.
pub fn snap_bands(bounds: &[f64], edges: &[f64]) -> Result<Vec<(f64, usize)>, String> {
    let mut snapped: Vec<(f64, usize)> = Vec::new();
    for &edge in edges {
        if !edge.is_finite() || edge <= 0.0 {
            return Err(format!("unfold: report band edge {edge} must be positive"));
        }
        let mut best = 0usize;
        let mut best_distance = f64::INFINITY;
        for (k, &b) in bounds.iter().enumerate() {
            if b <= 0.0 {
                continue;
            }
            let distance = (b.ln() - edge.ln()).abs();
            if distance < best_distance {
                best_distance = distance;
                best = k;
            }
        }
        if let Some(&(_, last)) = snapped.last() {
            if best < last {
                return Err("unfold: report_bands_eV must ascend".into());
            }
            if best == last {
                continue;
            }
        }
        snapped.push((edge, best));
    }
    if snapped.len() < 2 {
        return Err("unfold: report_bands_eV must span at least one group".into());
    }
    Ok(snapped)
}

/// Index of the first entry where the ascending cumulative share of `weights` reaches `level`.
pub fn window_index(weights: &[f64], level: f64) -> Option<usize> {
    let total: f64 = weights.iter().sum();
    if !total.is_finite() || total <= 0.0 {
        return None;
    }
    let mut cumulative = 0.0;
    for (k, w) in weights.iter().enumerate() {
        cumulative += w;
        if cumulative >= level * total {
            return Some(k);
        }
    }
    Some(weights.len() - 1)
}

// ---- the unfold -------------------------------------------------------------------------------

fn validate_problem(spec: &Spec, has_rates: bool) -> Result<(), String> {
    if !spec.projectile.is_neutron() {
        return Err("unfold: the problem must be a neutron problem".into());
    }
    if spec.spectrum.rebin.is_some() {
        return Err(
            "unfold: spectrum.rebin is not supported; give the prior on the library's groups"
                .into(),
        );
    }
    if spec.spectrum.relative_error.is_some() {
        return Err(
            "unfold: spectrum.relative_error is not used; declare the prior's uncertainty in the measurements file"
                .into(),
        );
    }
    for (index, step) in spec.schedule.iter().enumerate() {
        let n = index + 1;
        if step.spectrum.is_some() || step.library.is_some() {
            return Err(format!(
                "unfold: schedule step {n} declares its own spectrum or library; the unfolded spectrum is the problem's"
            ));
        }
        if step.feed.is_some() || step.removal.is_some() {
            return Err(format!(
                "unfold: schedule step {n} declares feed or removal"
            ));
        }
        if step.is_power_driven() || step.wall_loading_MW_m2.is_some() || step.partition.is_some() {
            return Err(format!(
                "unfold: schedule step {n} is power-driven, wall-loading or partitioned; its flux would not be the spectrum's"
            ));
        }
    }
    if let Some(options) = &spec.uncertainty {
        let allowed: UncertaintyOptions = serde_json::from_value(json!({
            "covariance": options.covariance,
            "confidence_level": options.confidence_level,
        }))
        .map_err(|e| format!("unfold: {e}"))?;
        if *options != allowed {
            return Err(
                "unfold: the problem's uncertainty block may hold only covariance and confidence_level; unfold sets the responses and channels itself"
                    .into(),
            );
        }
    }
    if has_rates && spec.self_shielding.is_some() {
        return Err(
            "unfold: reaction-rate measurements use the infinitely dilute group cross section and cannot accompany self_shielding"
                .into(),
        );
    }
    Ok(())
}

/// Reactions per atom per second from a group cross section (barns) and its group flux, through
/// the typed barn boundary.
fn rate_per_atom(sigma_barns: f64, flux: f64) -> Result<f64, UnfoldError> {
    let sigma = CrossSectionBarns::new(sigma_barns)
        .map_err(|error| UnfoldError::Failed(format!("unfold: library {error}")))?;
    Ok((sigma * RatePerBarnSecond::from_particle_flux(particle_flux(flux)?)).get())
}

/// A flux expressed per barn: the derivative of a rate per atom with respect to a cross section.
fn per_barn(flux: f64) -> Result<f64, UnfoldError> {
    Ok(RatePerBarnSecond::from_particle_flux(particle_flux(flux)?).get())
}

fn particle_flux(flux: f64) -> Result<ParticleFlux, UnfoldError> {
    ParticleFlux::new(flux).map_err(|error| UnfoldError::Failed(format!("unfold: {error}")))
}

/// The canonical name of an explicit nuclide (Co58, In115m1), as results key it.
fn canonical_nuclide(name: &str) -> Result<String, String> {
    match actinv_data::composition::material_key(name) {
        Ok(actinv_data::composition::MaterialKey::Nuclide { canonical, .. }) => Ok(canonical),
        Ok(_) => Err(format!(
            "measured nuclide '{name}' must be an explicit nuclide, e.g. 'Co58' or 'In115m'"
        )),
        Err(error) => Err(format!("measured nuclide '{name}': {error}")),
    }
}

fn find_target(view: &LibraryView, name: &str) -> Result<usize, String> {
    let (za, liso) = match actinv_data::composition::material_key(name) {
        Ok(actinv_data::composition::MaterialKey::Nuclide { za, liso, .. }) => (za, liso),
        Ok(_) => {
            return Err(format!(
                "unfold: target '{name}' must be an explicit nuclide, e.g. 'Ni58'"
            ))
        }
        Err(error) => return Err(format!("unfold: target '{name}': {error}")),
    };
    view.targets
        .iter()
        .position(|&key| key == (za, liso))
        .ok_or_else(|| format!("unfold: target '{name}' is not in the activation library"))
}

fn find_rate_row(view: &LibraryView, rate: &RateIn) -> Result<usize, String> {
    let target = find_target(view, &rate.target)?;
    let (zap, lfs) = match &rate.product {
        None => (-1, None),
        Some(name) => match actinv_data::composition::material_key(name) {
            Ok(actinv_data::composition::MaterialKey::Nuclide { za, liso, .. }) => (za, Some(liso)),
            _ => {
                return Err(format!(
                    "unfold: reaction rate {}: product '{name}' must be an explicit nuclide",
                    rate.id
                ))
            }
        },
    };
    let matches: Vec<usize> = view
        .rows
        .iter()
        .enumerate()
        .filter(|(_, row)| {
            row.target == target
                && row.mt == rate.mt
                && row.zap == zap
                && lfs.is_none_or(|state| row.lfs == state)
        })
        .map(|(index, _)| index)
        .collect();
    match matches.as_slice() {
        [row] => Ok(*row),
        [] => Err(match &rate.product {
            None => format!(
                "unfold: reaction rate {}: the library has no total row for {} MT {}",
                rate.id, rate.target, rate.mt
            ),
            Some(product) => format!(
                "unfold: reaction rate {}: the library has no production row of {product} from {} MT {}",
                rate.id, rate.target, rate.mt
            ),
        }),
        _ => Err(format!(
            "unfold: reaction rate {}: {} library rows match {} MT {}{}; the reaction is ambiguous",
            rate.id,
            matches.len(),
            rate.target,
            rate.mt,
            rate.product
                .as_ref()
                .map(|p| format!(" to {p}"))
                .unwrap_or_default()
        )),
    }
}

fn check_measured(id: &str, value: f64, sigma: f64, model_u: f64) -> Result<(), String> {
    if !value.is_finite() || value <= 0.0 {
        return Err(format!(
            "unfold: measurement {id}: the value must be positive"
        ));
    }
    if !sigma.is_finite() || sigma <= 0.0 {
        return Err(format!("unfold: measurement {id}: sigma must be positive"));
    }
    if !model_u.is_finite() || model_u < 0.0 {
        return Err(format!(
            "unfold: measurement {id}: model_relative_uncertainty must be finite and nonnegative"
        ));
    }
    Ok(())
}

struct Context<'a> {
    foil_specs: Vec<Spec>,
    foil_measurements: Vec<Vec<usize>>,
    measurements: Vec<Measurement>,
    adjustable: Vec<usize>,
    phi0: Vec<f64>,
    view: &'a LibraryView,
    /// Group cross sections of each reaction-rate measurement (indexed like `measurements`).
    rate_sigma: BTreeMap<usize, Vec<f64>>,
    covariance: Option<actinv_data::covariance::CovarianceLibrary>,
    /// The library subset of the targets the covariance collapse last needed; reading it streams
    /// the whole cross-section member, so it is kept while the target set stays the same.
    subset: Option<(BTreeSet<usize>, actinv_data::library::Library)>,
    solves: usize,
}

impl Context<'_> {
    fn phi_of(&self, x: &[f64], x0: &[f64]) -> Vec<f64> {
        let mut phi = self.phi0.clone();
        for (a, &g) in self.adjustable.iter().enumerate() {
            phi[g] = self.phi0[g] * (x[a] - x0[a]).exp();
        }
        phi
    }

    #[allow(clippy::needless_range_loop)]
    fn evaluate(&mut self, phi: &[f64]) -> Result<Point, UnfoldError> {
        let n_meas = self.measurements.len();
        let na = self.adjustable.len();
        let ngroups = phi.len();
        let mut position = vec![usize::MAX; ngroups];
        for (a, &g) in self.adjustable.iter().enumerate() {
            position[g] = a;
        }
        let mut m = vec![0.0; n_meas];
        let mut s = vec![vec![0.0; na]; n_meas];
        // Per measurement: (global row, ∂m/∂σ̄_row) of each covered row, and the solve's own
        // MF=33 standard deviation where it reports one.
        let mut gradients: Vec<Vec<(usize, f64)>> = vec![Vec::new(); n_meas];
        let mut anchor: Vec<Option<f64>> = vec![None; n_meas];
        let mut modes = Vec::new();
        let relative_error: Vec<f64> = (0..ngroups)
            .map(|g| if position[g] != usize::MAX { 1.0 } else { 0.0 })
            .collect();
        for (foil, base) in self.foil_specs.iter().enumerate() {
            let mut spec = base.clone();
            spec.spectrum.flux_per_group = phi.to_vec();
            spec.spectrum.total = None;
            spec.spectrum.descending = false;
            spec.spectrum.relative_error = Some(relative_error.clone());
            let result = run::run(&spec, "unfold")?;
            self.solves += 1;
            modes.push(result.mode.clone());
            for &i in &self.foil_measurements[foil] {
                let Kind::Activity { nuclide, step, .. } = &self.measurements[i].kind else {
                    unreachable!("foil measurements are activities");
                };
                let id = &self.measurements[i].id;
                let out = &result.steps[step - 1];
                let value = out.activity_Bq_per_g.get(nuclide).copied().unwrap_or(0.0);
                if !value.is_finite() || value <= 0.0 {
                    return refuse(format!(
                        "unfold: measurement {id}: the spectrum does not produce {nuclide} at step {step}"
                    ));
                }
                let response = out
                    .uncertainty
                    .as_ref()
                    .and_then(|u| u.responses.get(&format!("activity:{nuclide}")))
                    .ok_or_else(|| {
                        format!("unfold: measurement {id}: the solve reports no derivatives for {nuclide}")
                    })?;
                m[i] = value;
                for sensitivity in &response.flux_sensitivities {
                    if sensitivity.parameter.spectrum != 0 {
                        return Err(
                            "unfold: multi-spectrum flux derivatives are unsupported".into()
                        );
                    }
                    let g = sensitivity.parameter.group;
                    if g >= ngroups || position[g] == usize::MAX {
                        return Err(UnfoldError::Failed(format!(
                            "unfold: measurement {id}: a derivative names group {g}, which is held fixed"
                        )));
                    }
                    s[i][position[g]] = sensitivity.value;
                }
                if self.covariance.is_some() {
                    for sensitivity in &response.sensitivities {
                        if sensitivity.parameter.spectrum != 0 {
                            return Err(
                                "unfold: multi-spectrum cross-section sensitivities are unsupported".into(),
                            );
                        }
                        if sensitivity.parameter.covariance_covered && sensitivity.value != 0.0 {
                            gradients[i]
                                .push((sensitivity.parameter.library_row, sensitivity.value));
                        }
                    }
                    anchor[i] = Some(response.mf33_standard_uncertainty);
                }
            }
        }
        let total_flux: f64 = phi.iter().sum();
        for (&i, sigma) in &self.rate_sigma {
            let mut value = 0.0;
            for g in 0..ngroups {
                let term = rate_per_atom(sigma[g], phi[g])?;
                value += term;
                if position[g] != usize::MAX {
                    s[i][position[g]] = term;
                }
            }
            if !value.is_finite() || value <= 0.0 {
                return refuse(format!(
                    "unfold: measurement {}: the spectrum gives this reaction no rate",
                    self.measurements[i].id
                ));
            }
            m[i] = value;
            if self.covariance.is_some() {
                let Kind::Rate { row, .. } = self.measurements[i].kind else {
                    unreachable!("rate measurements carry a row");
                };
                // R = σ̄ Φ with σ̄ collapsed under φ, so ∂R/∂σ̄ = Φ, per barn.
                gradients[i].push((row, per_barn(total_flux)?));
            }
        }
        let mut c_xs = vec![vec![0.0; n_meas]; n_meas];
        let mut covered = vec![0usize; n_meas];
        let mut uncovered = vec![0usize; n_meas];
        let mut excluded = Vec::new();
        if let Some(covariance) = &self.covariance {
            let global: BTreeSet<usize> = gradients
                .iter()
                .flat_map(|list| list.iter().map(|&(row, _)| row))
                .collect();
            if !global.is_empty() {
                let targets: BTreeSet<usize> =
                    global.iter().map(|&r| self.view.rows[r].target).collect();
                if self.subset.as_ref().map(|(held, _)| held) != Some(&targets) {
                    let library =
                        actinv_data::library::read_npz_targets(&self.view.path, &targets)?;
                    self.subset = Some((targets.clone(), library));
                }
                let subset = &self.subset.as_ref().expect("subset just read").1;
                let local = self.view.local_index(&targets);
                let selected: Vec<usize> = global.iter().map(|r| local[r]).collect();
                let sparse = covariance.collapse_sparse_weighted_multi(
                    subset,
                    &[phi],
                    &selected,
                    &|_, _| 1.0,
                )?;
                excluded = sparse
                    .excluded_blocks
                    .iter()
                    .map(|block| {
                        json!({
                            "target": block.target,
                            "MT": block.mt,
                            "MT1": block.mt1,
                            "reason": block.reason,
                        })
                    })
                    .collect();
                let mut position_of_local: BTreeMap<usize, usize> = BTreeMap::new();
                for (p, &row) in sparse.row_indices.iter().enumerate() {
                    position_of_local.insert(row, p);
                }
                let uncovered_local: BTreeSet<usize> =
                    sparse.uncovered_rows.iter().copied().collect();
                let dense: Vec<BTreeMap<usize, f64>> = gradients
                    .iter()
                    .enumerate()
                    .map(|(i, list)| {
                        let mut map = BTreeMap::new();
                        for &(row, value) in list {
                            let l = local[&row];
                            match position_of_local.get(&l) {
                                Some(&p) => {
                                    *map.entry(p).or_insert(0.0) += value;
                                    covered[i] += 1;
                                }
                                None => {
                                    if uncovered_local.contains(&l) {
                                        uncovered[i] += 1;
                                    }
                                }
                            }
                        }
                        map
                    })
                    .collect();
                // The collapse stores both directions of every pair; summing in key order keeps the
                // result independent of the map's iteration order.
                let mut entries: Vec<((usize, usize), f64)> =
                    sparse.entries.iter().map(|(&k, &v)| (k, v)).collect();
                entries.sort_by_key(|entry| entry.0);
                let mut at_position: BTreeMap<usize, Vec<(usize, f64)>> = BTreeMap::new();
                for (i, map) in dense.iter().enumerate() {
                    for (&p, &g) in map {
                        at_position.entry(p).or_default().push((i, g));
                    }
                }
                let mut absolute = vec![vec![0.0; n_meas]; n_meas];
                for &((l, r), v) in &entries {
                    if let (Some(left), Some(right)) = (at_position.get(&l), at_position.get(&r)) {
                        for &(i, gi) in left {
                            for &(j, gj) in right {
                                absolute[i][j] += gi * v * gj;
                            }
                        }
                    }
                }
                for i in 0..n_meas {
                    for j in 0..n_meas {
                        c_xs[i][j] = 0.5 * (absolute[i][j] + absolute[j][i]) / (m[i] * m[j]);
                    }
                }
                for i in 0..n_meas {
                    if let Some(sd) = anchor[i] {
                        let assembled = c_xs[i][i] * m[i] * m[i];
                        let reported = sd * sd;
                        let scale = assembled.abs().max(reported.abs());
                        if scale > 0.0
                            && (assembled - reported).abs() > MF33_ANCHOR_TOLERANCE * scale
                        {
                            return Err(UnfoldError::Failed(format!(
                                "unfold: measurement {}: the assembled MF=33 variance {assembled:e} differs from the solve's {reported:e}",
                                self.measurements[i].id
                            )));
                        }
                    }
                }
            }
        }
        Ok(Point {
            m,
            s,
            c_xs,
            covered,
            uncovered,
            excluded,
            modes,
        })
    }
}

/// Total measurement covariance (relative): C_xs plus the measurement and model terms.
fn total_covariance(measurements: &[Measurement], c_xs: &[Vec<f64>]) -> Vec<Vec<f64>> {
    let mut c = c_xs.to_vec();
    for (i, measurement) in measurements.iter().enumerate() {
        let u = measurement.sigma / measurement.value;
        c[i][i] += u * u + measurement.model_relative_uncertainty.powi(2);
    }
    c
}

/// J P Jᵀ for a Jacobian over the adjustable groups.
fn quadratic(jac: &[Vec<f64>], p: &[Vec<f64>]) -> Vec<Vec<f64>> {
    let pj: Vec<Vec<f64>> = jac.iter().map(|ji| mat_vec(p, ji)).collect();
    jac.iter()
        .map(|ji| pj.iter().map(|pjj| dot(ji, pjj)).collect())
        .collect()
}

fn sparse_row(values: &[f64], adjustable: &[usize]) -> Value {
    let mut groups = Vec::new();
    let mut entries = Vec::new();
    for (a, &v) in values.iter().enumerate() {
        if v != 0.0 {
            groups.push(adjustable[a]);
            entries.push(v);
        }
    }
    json!({"groups": groups, "values": entries})
}

/// Unfold the spectrum. `problem_json` and `input_json` are hashed into the record.
#[allow(clippy::needless_range_loop)]
pub fn unfold(spec: &Spec, problem_json: &str, input_json: &str) -> Result<Value, UnfoldError> {
    let started = std::time::Instant::now();
    let input: InputFile = serde_json::from_str(input_json).map_err(|e| {
        UnfoldError::Input(format!(
            "unfold: the measurements file is not {INPUT_FORMAT}: {e}"
        ))
    })?;
    if input.format != INPUT_FORMAT {
        return refuse(format!(
            "unfold: measurements format '{}' is not {INPUT_FORMAT}",
            input.format
        ));
    }
    if input.foils.is_empty() && input.reaction_rates.is_empty() {
        return refuse("unfold: the measurements file declares no foils and no reaction rates");
    }
    validate_problem(spec, !input.reaction_rates.is_empty()).map_err(UnfoldError::Input)?;
    let iterations = input.iterations.clone();
    if iterations.max_outer == 0
        || iterations.max_inner == 0
        || iterations.inner_tolerance.is_nan()
        || iterations.inner_tolerance <= 0.0
        || iterations.outer_tolerance.is_nan()
        || iterations.outer_tolerance <= 0.0
    {
        return refuse("unfold: iterations need positive limits and tolerances");
    }
    if let Correlation::LethargyExponential { length } = input.prior.correlation {
        if !length.is_finite() || length <= 0.0 {
            return refuse("unfold: the prior's correlation length must be positive");
        }
    }

    // ---- library structure and prior ----------------------------------------------------------
    let library_path = spec.library.path.clone();
    let library_sha = crate::flux::sha256_file(&library_path)?;
    if let Some(declared) = &spec.library.sha256 {
        if !library_sha.eq_ignore_ascii_case(declared) {
            return refuse(
                "unfold: the activation library does not match the problem's declared hash",
            );
        }
    }
    let structure = actinv_data::library::read_npz_structure(&library_path)?;
    let bounds = structure.bounds.clone();
    if bounds
        .windows(2)
        .any(|pair| pair[1].is_nan() || pair[1] <= pair[0])
        || bounds.first().is_none_or(|&b| b.is_nan() || b <= 0.0)
    {
        return refuse("unfold: the library's group boundaries must be positive and ascending");
    }
    let ngroups = structure.ngroups;
    let index_file = index_path(&library_path);
    let index_text = std::fs::read_to_string(&index_file)
        .map_err(|e| format!("unfold: cannot read the library index {index_file}: {e}"))?;
    let index: Value = serde_json::from_str(&index_text)
        .map_err(|e| format!("unfold: library index {index_file}: {e}"))?;
    let targets = run::library_index_target_keys(&index, &index_file)?;
    let view = LibraryView {
        path: library_path.clone(),
        rows: structure.rows,
        targets,
    };

    let phi0 = spec.spectrum.ascending_flux();
    if phi0.len() != ngroups {
        return refuse(format!(
            "unfold: the prior has {} groups; the library has {ngroups}",
            phi0.len()
        ));
    }
    let prior_sd_all: Vec<f64> = match &input.prior.relative_uncertainty {
        UncertaintyIn::Uniform(value) => vec![*value; ngroups],
        UncertaintyIn::PerGroup(values) => {
            if values.len() != ngroups {
                return refuse(format!(
                    "unfold: prior.relative_uncertainty has {} values; the spectrum has {ngroups}",
                    values.len()
                ));
            }
            let mut v = values.clone();
            if spec.spectrum.descending {
                v.reverse();
            }
            v
        }
    };
    if prior_sd_all.iter().any(|v| !v.is_finite() || *v < 0.0) {
        return refuse("unfold: prior.relative_uncertainty must be finite and nonnegative");
    }
    let adjustable: Vec<usize> = (0..ngroups)
        .filter(|&g| phi0[g] > 0.0 && prior_sd_all[g] > 0.0)
        .collect();
    if adjustable.is_empty() {
        return refuse("unfold: every group is held fixed (zero prior flux or zero uncertainty)");
    }
    let na = adjustable.len();
    let lethargy: Vec<f64> = adjustable
        .iter()
        .map(|&g| 0.5 * (bounds[g].ln() + bounds[g + 1].ln()))
        .collect();
    let prior_sd: Vec<f64> = adjustable.iter().map(|&g| prior_sd_all[g]).collect();
    let p0 = prior_covariance(&prior_sd, &lethargy, &input.prior.correlation);
    let x0: Vec<f64> = adjustable.iter().map(|&g| phi0[g].ln()).collect();

    // ---- measurements -------------------------------------------------------------------------
    let mut measurements: Vec<Measurement> = Vec::new();
    let mut foil_specs = Vec::new();
    let mut foil_measurements = Vec::new();
    let mut seen_ids = BTreeSet::new();
    let covariance_ref = spec.uncertainty.as_ref().and_then(|u| u.covariance.clone());
    let confidence_level = spec
        .uncertainty
        .as_ref()
        .map_or(0.95, |u| u.confidence_level);
    for (foil_index, foil) in input.foils.iter().enumerate() {
        if foil.measurements.is_empty() {
            return refuse(format!("unfold: foil {} has no measurements", foil.id));
        }
        let mut responses = Vec::new();
        let mut indices = Vec::new();
        for measurement in &foil.measurements {
            // Results name nuclides canonically (a first isomer is In115m1); In115m and In115m1
            // name the same state, so the given name is resolved before it is looked up.
            let nuclide = canonical_nuclide(&measurement.nuclide).map_err(|error| {
                UnfoldError::Input(format!("unfold: foil {}: {error}", foil.id))
            })?;
            let id = format!("{}:{}@{}", foil.id, nuclide, measurement.step);
            check_measured(
                &id,
                measurement.activity_Bq_per_g,
                measurement.sigma_Bq_per_g,
                measurement.model_relative_uncertainty,
            )
            .map_err(UnfoldError::Input)?;
            if measurement.step == 0 || measurement.step > spec.schedule.len() {
                return refuse(format!(
                    "unfold: measurement {id}: step {} is outside the schedule's {} steps",
                    measurement.step,
                    spec.schedule.len()
                ));
            }
            if !seen_ids.insert(id.clone()) {
                return refuse(format!("unfold: measurement {id} is declared twice"));
            }
            let response = format!("activity:{nuclide}");
            if !responses.contains(&response) {
                responses.push(response);
            }
            indices.push(measurements.len());
            measurements.push(Measurement {
                id,
                kind: Kind::Activity {
                    foil: foil_index,
                    nuclide,
                    step: measurement.step,
                },
                value: measurement.activity_Bq_per_g,
                sigma: measurement.sigma_Bq_per_g,
                model_relative_uncertainty: measurement.model_relative_uncertainty,
            });
        }
        let mut foil_spec = spec.clone();
        if let Some(material) = &foil.material {
            foil_spec.material = material.clone();
        }
        let channels = if covariance_ref.is_some() {
            json!(["cross_section_mf33", "flux"])
        } else {
            json!(["flux"])
        };
        foil_spec.uncertainty = Some(
            serde_json::from_value(json!({
                "covariance": covariance_ref,
                "responses": responses,
                "channels": channels,
                "confidence_level": confidence_level,
            }))
            .map_err(|e| UnfoldError::Input(format!("unfold: {e}")))?,
        );
        foil_spec
            .validate()
            .map_err(|e| UnfoldError::Input(format!("unfold: foil {}: {e}", foil.id)))?;
        foil_specs.push(foil_spec);
        foil_measurements.push(indices);
    }
    let mut rate_rows = Vec::new();
    for rate in &input.reaction_rates {
        check_measured(
            &rate.id,
            rate.rate_per_atom_s,
            rate.sigma_per_atom_s,
            rate.model_relative_uncertainty,
        )
        .map_err(UnfoldError::Input)?;
        if !seen_ids.insert(rate.id.clone()) {
            return refuse(format!("unfold: measurement {} is declared twice", rate.id));
        }
        let row = find_rate_row(&view, rate).map_err(UnfoldError::Input)?;
        rate_rows.push((measurements.len(), row));
        measurements.push(Measurement {
            id: rate.id.clone(),
            kind: Kind::Rate {
                row,
                target: rate.target.clone(),
                mt: rate.mt,
                product: rate.product.clone(),
            },
            value: rate.rate_per_atom_s,
            sigma: rate.sigma_per_atom_s,
            model_relative_uncertainty: rate.model_relative_uncertainty,
        });
    }
    let rate_sigma: BTreeMap<usize, Vec<f64>> = {
        let rows: Vec<usize> = rate_rows.iter().map(|&(_, row)| row).collect();
        let sigma = if rows.is_empty() {
            Vec::new()
        } else {
            view.sigma_of(&rows)?
        };
        rate_rows.iter().map(|&(i, _)| i).zip(sigma).collect()
    };
    let covariance_sha = match &covariance_ref {
        Some(reference) => {
            let sha = crate::flux::sha256_file(&reference.path)?;
            if !sha.eq_ignore_ascii_case(&reference.sha256) {
                return refuse(
                    "unfold: the covariance sidecar does not match the problem's declared hash",
                );
            }
            Some(sha)
        }
        None => None,
    };
    let covariance = match &covariance_ref {
        Some(reference) => Some(actinv_data::covariance::read_npz(&reference.path)?),
        None => None,
    };
    let y: Vec<f64> = measurements.iter().map(|m| m.value.ln()).collect();
    let n_meas = measurements.len();

    let mut context = Context {
        foil_specs,
        foil_measurements,
        measurements,
        adjustable: adjustable.clone(),
        phi0: phi0.clone(),
        view: &view,
        rate_sigma,
        covariance,
        subset: None,
        solves: 0,
    };

    // ---- outer loop ---------------------------------------------------------------------------
    let mut x = x0.clone();
    let mut point = context.evaluate(&phi0)?;
    let prior_point_m = point.m.clone();
    let prior_excluded = point.excluded.clone();
    let prior_jac: Vec<Vec<f64>> = point
        .s
        .iter()
        .zip(&point.m)
        .map(|(si, mi)| si.iter().map(|v| v / mi).collect())
        .collect();
    let prior_c = total_covariance(&context.measurements, &point.c_xs);
    let mut inner_log = Vec::new();
    let mut converged = None;
    let mut last_step = f64::NAN;
    for outer in 1..=iterations.max_outer {
        let c = total_covariance(&context.measurements, &point.c_xs);
        let model = LocalModel {
            m: &point.m,
            s: &point.s,
            xk: &x,
        };
        let objective = Objective {
            x0: &x0,
            p0: &p0,
            y: &y,
            c: &c,
        };
        let solution = inner_solve(
            &model,
            &objective,
            &x,
            iterations.max_inner,
            iterations.inner_tolerance,
        )?;
        let step = solution
            .x
            .iter()
            .zip(&x)
            .fold(0.0f64, |acc, (a, b)| acc.max((a - b).abs()));
        last_step = step;
        inner_log.push(json!({
            "outer": outer,
            "inner_iterations": solution.iterations,
            "damped_steps": solution.damped,
            "step_ln": step,
        }));
        if step <= iterations.outer_tolerance {
            converged = Some(outer);
            break;
        }
        x = solution.x;
        let phi = context.phi_of(&x, &x0);
        point = context.evaluate(&phi)?;
    }
    let Some(outer_iterations) = converged else {
        return Err(UnfoldError::Failed(format!(
            "unfold: did not converge in {} outer iterations (last step {last_step:e} in ln φ)",
            iterations.max_outer
        )));
    };

    // ---- posterior ----------------------------------------------------------------------------
    let phi = context.phi_of(&x, &x0);
    let measurements = &context.measurements;
    let c = total_covariance(measurements, &point.c_xs);
    let f: Vec<f64> = point.m.iter().map(|v| v.ln()).collect();
    let jac: Vec<Vec<f64>> = point
        .s
        .iter()
        .zip(&point.m)
        .map(|(si, mi)| si.iter().map(|v| v / mi).collect())
        .collect();
    let p_jt: Vec<Vec<f64>> = jac.iter().map(|ji| mat_vec(&p0, ji)).collect();
    let k_prior = quadratic(&jac, &p0);
    let mut a = c.clone();
    for i in 0..n_meas {
        for j in 0..n_meas {
            a[i][j] += k_prior[i][j];
        }
    }
    let a_factor = cholesky(&a).ok_or("unfold: J P⁰ Jᵀ + C is not positive definite")?;
    // B = A⁻¹ (P⁰ Jᵀ)ᵀ, one column per adjustable group.
    let mut b = vec![vec![0.0; na]; n_meas];
    for k in 0..na {
        let column: Vec<f64> = (0..n_meas).map(|i| p_jt[i][k]).collect();
        let solved = cholesky_solve(&a_factor, &column);
        for i in 0..n_meas {
            b[i][k] = solved[i];
        }
    }
    let mut p = p0.clone();
    for u in 0..na {
        for v in 0..na {
            let mut acc = 0.0;
            for i in 0..n_meas {
                acc += p_jt[i][u] * b[i][v];
            }
            p[u][v] -= acc;
        }
    }
    let k_post = quadratic(&jac, &p);
    let c_factor =
        cholesky(&c).ok_or("unfold: the measurement covariance is not positive definite")?;
    let residual: Vec<f64> = y.iter().zip(&f).map(|(a, b)| a - b).collect();
    let data_term = dot(&residual, &cholesky_solve(&c_factor, &residual));
    let prior_term = prior_quadratic(&p0, &x, &x0)
        .ok_or("unfold: the prior covariance is not positive definite")?;
    let chi_square = data_term + prior_term;
    let k_prior0 = quadratic(&prior_jac, &p0);

    // ---- report -------------------------------------------------------------------------------
    let multiplier = crate::uncertainty::normal_multiplier(confidence_level);
    let mut prior_sd_ln = vec![0.0; ngroups];
    let mut posterior_sd_ln = vec![0.0; ngroups];
    let mut resolution = vec![Value::Null; ngroups];
    let mut adjustable_flag = vec![false; ngroups];
    for (a, &g) in adjustable.iter().enumerate() {
        prior_sd_ln[g] = p0[a][a].sqrt();
        posterior_sd_ln[g] = p[a][a].max(0.0).sqrt();
        resolution[g] = finite_or_null(1.0 - p[a][a] / p0[a][a]);
        adjustable_flag[g] = true;
    }
    let mut band_edges: Vec<f64> = input
        .report_bands_eV
        .clone()
        .unwrap_or_else(|| DEFAULT_BAND_EDGES_EV.to_vec());
    if input.report_bands_eV.is_none() {
        band_edges.push(*bounds.last().expect("library bounds"));
    }
    let snapped = snap_bands(&bounds, &band_edges).map_err(UnfoldError::Input)?;
    let mut position = vec![usize::MAX; ngroups];
    for (a, &g) in adjustable.iter().enumerate() {
        position[g] = a;
    }
    let band_members: Vec<(usize, usize)> = snapped
        .windows(2)
        .map(|pair| (pair[0].1, pair[1].1))
        .collect();
    let band_variance = |cov: &[Vec<f64>], flux: &[f64], first: usize, end: usize| -> f64 {
        let members: Vec<usize> = (first..end)
            .filter(|&g| position[g] != usize::MAX)
            .collect();
        let mut acc = 0.0;
        for &g in &members {
            for &h in &members {
                acc += flux[g] * flux[h] * cov[position[g]][position[h]];
            }
        }
        acc
    };
    let mut bands = Vec::new();
    for (index, &(first, end)) in band_members.iter().enumerate() {
        let prior_flux: f64 = phi0[first..end].iter().sum();
        let posterior_flux: f64 = phi[first..end].iter().sum();
        let prior_sd = band_variance(&p0, &phi0, first, end).max(0.0).sqrt();
        let posterior_sd = band_variance(&p, &phi, first, end).max(0.0).sqrt();
        bands.push(json!({
            "requested_lower_eV": snapped[index].0,
            "requested_upper_eV": snapped[index + 1].0,
            "lower_eV": bounds[first],
            "upper_eV": bounds[end],
            "first_group": first,
            "end_group": end,
            "prior_flux": prior_flux,
            "prior_sd": prior_sd,
            "posterior_flux": posterior_flux,
            "posterior_sd": posterior_sd,
            "interval": [posterior_flux - multiplier * posterior_sd, posterior_flux + multiplier * posterior_sd],
        }));
    }
    let nb = band_members.len();
    let mut band_correlation = vec![vec![Value::Null; nb]; nb];
    for u in 0..nb {
        for v in 0..nb {
            let (fu, eu) = band_members[u];
            let (fv, ev) = band_members[v];
            let mut acc = 0.0;
            for g in (fu..eu).filter(|&g| position[g] != usize::MAX) {
                for h in (fv..ev).filter(|&h| position[h] != usize::MAX) {
                    acc += phi[g] * phi[h] * p[position[g]][position[h]];
                }
            }
            let su = band_variance(&p, &phi, fu, eu).sqrt();
            let sv = band_variance(&p, &phi, fv, ev).sqrt();
            band_correlation[u][v] = finite_or_null(acc / (su * sv));
        }
    }
    let mut measurement_records = Vec::new();
    for (i, measurement) in measurements.iter().enumerate() {
        let window: Vec<Value> = WINDOW_LEVELS
            .iter()
            .map(|&level| {
                window_index(&point.s[i], level)
                    .map(|a| json!(bounds[adjustable[a] + 1]))
                    .unwrap_or(Value::Null)
            })
            .collect();
        let prior_variance = prior_c[i][i] + k_prior0[i][i];
        let posterior_residual_variance = c[i][i] - k_post[i][i];
        let mut record = json!({
            "id": measurement.id,
            "measured": measurement.value,
            "sigma": measurement.sigma,
            "model_relative_uncertainty": measurement.model_relative_uncertainty,
            "prior_prediction": prior_point_m[i],
            "posterior_prediction": point.m[i],
            "prior_pull": finite_or_null((y[i] - prior_point_m[i].ln()) / prior_variance.sqrt()),
            "posterior_pull": if posterior_residual_variance > 0.0 {
                finite_or_null(residual[i] / posterior_residual_variance.sqrt())
            } else {
                Value::Null
            },
            "prior_measurement_variance_ln": prior_c[i][i],
            "cross_section_relative_sd": point.c_xs[i][i].max(0.0).sqrt(),
            "covered_rows": point.covered[i],
            "uncovered_rows": point.uncovered[i],
            "response_window_eV": {"e05": window[0], "e50": window[1], "e95": window[2]},
            "jacobian": sparse_row(&jac[i], &adjustable),
            "prior_jacobian": sparse_row(&prior_jac[i], &adjustable),
        });
        match &measurement.kind {
            Kind::Activity {
                foil,
                nuclide,
                step,
            } => {
                record["kind"] = json!("activity");
                record["foil"] = json!(input.foils[*foil].id);
                record["nuclide"] = json!(nuclide);
                record["step"] = json!(step);
                record["unit"] = json!("Bq/g");
            }
            Kind::Rate {
                row,
                target,
                mt,
                product,
            } => {
                let descriptor = view.rows[*row];
                record["kind"] = json!("reaction_rate");
                record["target"] = json!(target);
                record["mt"] = json!(mt);
                record["product"] = json!(product);
                record["library_row"] = json!({
                    "index": row,
                    "target": descriptor.target,
                    "MT": descriptor.mt,
                    "ZAP": descriptor.zap,
                    "LFS": descriptor.lfs,
                    "LMF": descriptor.lmf,
                });
                record["unit"] = json!("per atom per s");
            }
        }
        measurement_records.push(record);
    }
    let correlation = match &input.prior.correlation {
        Correlation::Diagonal => json!({"kind": "diagonal"}),
        Correlation::LethargyExponential { length } => {
            json!({"kind": "lethargy_exponential", "length": length})
        }
    };
    let mut document = json!({
        "format": RESULT_FORMAT,
        "inputs": {
            "problem_sha256": sha256_text(problem_json),
            "measurements_sha256": sha256_text(input_json),
            "library_sha256": library_sha,
            "covariance_sha256": covariance_sha,
        },
        "method": {
            "estimate": "generalized least squares on ln φ (damped Newton with the local model's exact Hessian, in whitened coordinates)",
            "model": "ACTINV solve per foil with P93 flux derivatives; library group cross section per reaction rate",
            "cross_section_covariance": if covariance_ref.is_some() { "MF=33 sidecar, P55 sparse collapse at the accepted spectrum" } else { "not propagated" },
            "confidence_level": confidence_level,
            "normal_multiplier": multiplier,
            "prior": {
                "correlation": correlation,
                "adjustable_groups": na,
                "fixed_groups": ngroups - na,
            },
            "iterations": {
                "max_outer": iterations.max_outer,
                "max_inner": iterations.max_inner,
                "inner_tolerance": iterations.inner_tolerance,
                "outer_tolerance": iterations.outer_tolerance,
            },
        },
        "groups": {
            "lower_eV": &bounds[..ngroups],
            "upper_eV": &bounds[1..],
            "adjustable": adjustable_flag,
            "prior_flux": phi0,
            "posterior_flux": phi,
            "prior_sd_ln": prior_sd_ln,
            "posterior_sd_ln": posterior_sd_ln,
            "resolution": resolution,
        },
        "bands": bands,
        "band_correlation": band_correlation,
        "measurements": measurement_records,
        "measurement_covariance_ln": c,
        "cross_section_excluded_blocks": {
            "prior": prior_excluded,
            "posterior": point.excluded,
        },
        "consistency": {
            "chi_square": chi_square,
            "data_term": data_term,
            "prior_term": prior_term,
            "degrees_of_freedom": n_meas,
            "chi_square_per_degree_of_freedom": chi_square / n_meas as f64,
        },
        "convergence": {
            "outer_iterations": outer_iterations,
            "inner": inner_log,
            "forward_solves": context.solves,
            "modes": point.modes,
        },
    });
    if input.emit_covariance {
        document["posterior_covariance_ln"] = json!({"groups": adjustable, "matrix": p});
    }
    document["wall_time_s"] = json!(started.elapsed().as_secs_f64());
    Ok(document)
}

#[cfg(test)]
#[allow(clippy::needless_range_loop)]
mod tests {
    use super::*;

    fn close(a: f64, b: f64, tolerance: f64) -> bool {
        (a - b).abs() <= tolerance * a.abs().max(b.abs()).max(1e-300)
    }

    #[test]
    fn exponential_kernel_matches_its_definition() {
        let sd = [0.3, 0.2, 0.1];
        let u = [0.0, 0.5, 2.0];
        let p = prior_covariance(&sd, &u, &Correlation::LethargyExponential { length: 1.0 });
        assert!(close(p[0][1], 0.3 * 0.2 * (-0.5f64).exp(), 1e-15));
        assert!(close(p[1][2], 0.2 * 0.1 * (-1.5f64).exp(), 1e-15));
        assert!(close(p[2][2], 0.01, 1e-15));
        assert_eq!(p[0][2], p[2][0]);
        assert!(
            cholesky(&p).is_some(),
            "the exponential kernel is positive definite"
        );
        let d = prior_covariance(&sd, &u, &Correlation::Diagonal);
        assert_eq!(d[0][1], 0.0);
        assert!(close(d[1][1], 0.04, 1e-15));
    }

    #[test]
    fn local_model_is_exact_for_a_linear_response() {
        // m(φ) = Σ K_g φ_g; at φᵏ the derivatives are s_g = K_g φᵏ_g.
        let k = [2.0, 0.5, 3.0];
        let phi_k = [1.0, 4.0, 0.25];
        let m = [k.iter().zip(&phi_k).map(|(a, b)| a * b).sum::<f64>()];
        let s = vec![k
            .iter()
            .zip(&phi_k)
            .map(|(a, b)| a * b)
            .collect::<Vec<f64>>()];
        let xk: Vec<f64> = phi_k.iter().map(|v: &f64| v.ln()).collect();
        let model = LocalModel {
            m: &m,
            s: &s,
            xk: &xk,
        };
        let phi_new = [1.7, 2.2, 0.9];
        let x_new: Vec<f64> = phi_new.iter().map(|v: &f64| v.ln()).collect();
        let (f, jac) = model.evaluate(&x_new).expect("positive");
        let exact: f64 = k.iter().zip(&phi_new).map(|(a, b)| a * b).sum();
        assert!(close(f[0], exact.ln(), 1e-14));
        for g in 0..3 {
            assert!(close(jac[0][g], k[g] * phi_new[g] / exact, 1e-14));
        }
    }

    #[test]
    fn inner_solve_matches_the_linear_gaussian_update() {
        // Each measurement sees one group, so f is linear in x and the MAP point is closed form.
        let m = [5.0, 2.0];
        let s = vec![vec![5.0, 0.0, 0.0], vec![0.0, 0.0, 2.0]];
        let x0 = [0.0, 0.1, -0.2];
        let model = LocalModel {
            m: &m,
            s: &s,
            xk: &x0,
        };
        let sd = [0.3, 0.3, 0.3];
        let u = [0.0, 1.0, 2.0];
        let p0 = prior_covariance(&sd, &u, &Correlation::LethargyExponential { length: 1.0 });
        let y = [5.5f64.ln(), 1.7f64.ln()];
        let c = vec![vec![0.03f64.powi(2), 0.0], vec![0.0, 0.05f64.powi(2)]];
        let objective = Objective {
            x0: &x0,
            p0: &p0,
            y: &y,
            c: &c,
        };
        let solution = inner_solve(&model, &objective, &x0, 100, 1e-12).expect("solve");
        // Closed form: x = x0 + P0 Hᵀ (H P0 Hᵀ + C)⁻¹ (y − ln m), H selecting groups 0 and 2.
        let h = [[1.0, 0.0, 0.0], [0.0, 0.0, 1.0]];
        let d = [y[0] - m[0].ln(), y[1] - m[1].ln()];
        let mut a = c.clone();
        for i in 0..2 {
            for j in 0..2 {
                for p in 0..3 {
                    for q in 0..3 {
                        a[i][j] += h[i][p] * p0[p][q] * h[j][q];
                    }
                }
            }
        }
        let w = cholesky_solve(&cholesky(&a).expect("pd"), &d);
        for g in 0..3 {
            let mut expected = x0[g];
            for i in 0..2 {
                let ph: f64 = (0..3).map(|q| p0[g][q] * h[i][q]).sum();
                expected += ph * w[i];
            }
            assert!(close(solution.x[g], expected, 1e-10), "group {g}");
        }
    }

    #[test]
    fn inner_solve_never_raises_the_objective_on_a_nonlinear_case() {
        // Two groups feeding two measurements far from the prior's predictions: strongly nonlinear
        // in ln φ.
        let m = [1.0, 1.0];
        let s = vec![vec![0.9, 0.1], vec![0.2, 0.8]];
        let x0 = [0.0, 0.0];
        let model = LocalModel {
            m: &m,
            s: &s,
            xk: &x0,
        };
        let p0 = prior_covariance(&[2.0, 2.0], &[0.0, 1.0], &Correlation::Diagonal);
        let y = [30.0f64.ln(), 8.0f64.ln()];
        let c = vec![vec![1e-4, 0.0], vec![0.0, 1e-4]];
        let objective = Objective {
            x0: &x0,
            p0: &p0,
            y: &y,
            c: &c,
        };
        let factor = cholesky(&c).expect("pd");
        let start_value = {
            let (f, _) = model.evaluate(&x0).expect("positive");
            let r: Vec<f64> = y.iter().zip(&f).map(|(a, b)| a - b).collect();
            dot(&r, &cholesky_solve(&factor, &r))
        };
        let solution = inner_solve(&model, &objective, &x0, 200, 1e-12).expect("solve");
        assert!(solution.objective <= start_value);
        // Stationarity: Jᵀ C⁻¹ (y − f) = P⁰⁻¹ (x − x⁰) = z.
        let (f, jac) = model.evaluate(&solution.x).expect("positive");
        let r: Vec<f64> = y.iter().zip(&f).map(|(a, b)| a - b).collect();
        let w = cholesky_solve(&factor, &r);
        for k in 0..2 {
            let gradient: f64 = (0..2).map(|i| jac[i][k] * w[i]).sum();
            let prior = (solution.x[k] - x0[k]) / p0[k][k];
            assert!(
                close(gradient, prior, 1e-6),
                "group {k}: {gradient} vs {prior}"
            );
        }
    }

    #[test]
    fn inner_solve_reaches_a_tight_tolerance_past_the_objective_rounding_floor() {
        // Many strongly correlated groups and three threshold responses: the last steps to a tight
        // tolerance change the objective by less than rounding.
        let n = 300;
        let lethargy: Vec<f64> = (0..n).map(|g| 0.06 * g as f64).collect();
        let x0 = vec![0.0; n];
        let s: Vec<Vec<f64>> = [100, 180, 260]
            .iter()
            .map(|&first| {
                (0..n)
                    .map(|g| {
                        if g >= first {
                            1.0 + 0.01 * g as f64
                        } else {
                            0.0
                        }
                    })
                    .collect()
            })
            .collect();
        let m: Vec<f64> = s.iter().map(|row| row.iter().sum()).collect();
        let model = LocalModel {
            m: &m,
            s: &s,
            xk: &x0,
        };
        let p0 = prior_covariance(
            &vec![0.3; n],
            &lethargy,
            &Correlation::LethargyExponential { length: 1.0 },
        );
        let y: Vec<f64> = m
            .iter()
            .zip([1.15, 0.92, 1.1])
            .map(|(mi, k)| (mi * k).ln())
            .collect();
        let c: Vec<Vec<f64>> = (0..3)
            .map(|i| (0..3).map(|j| if i == j { 9e-4 } else { 0.0 }).collect())
            .collect();
        let objective = Objective {
            x0: &x0,
            p0: &p0,
            y: &y,
            c: &c,
        };
        let solution = inner_solve(&model, &objective, &x0, 100, 1e-10)
            .expect("converges below the rounding floor");
        assert!(solution.iterations < 100);
    }

    #[test]
    fn inner_solve_converges_on_data_that_contradict_the_prior() {
        // Three overlapping responses asked to move by factors 21, 0.5 and 4 at 3%: Gauss–Newton
        // cycles here without converging; Newton with the model's Hessian converges.
        let n = 6;
        let lethargy: Vec<f64> = (0..n).map(|g| g as f64).collect();
        let x0 = vec![0.0; n];
        let s = vec![
            vec![0.0, 0.0, 1.0, 2.0, 4.0, 8.0],
            vec![0.0, 0.0, 0.0, 1.0, 4.0, 9.0],
            vec![0.0, 1.0, 2.0, 3.0, 3.0, 3.0],
        ];
        let m: Vec<f64> = s.iter().map(|row| row.iter().sum()).collect();
        let model = LocalModel {
            m: &m,
            s: &s,
            xk: &x0,
        };
        let p0 = prior_covariance(
            &vec![0.3; n],
            &lethargy,
            &Correlation::LethargyExponential { length: 1.0 },
        );
        let y: Vec<f64> = m
            .iter()
            .zip([21.0, 0.5, 4.0])
            .map(|(mi, k)| (mi * k).ln())
            .collect();
        let c: Vec<Vec<f64>> = (0..3)
            .map(|i| (0..3).map(|j| if i == j { 9e-4 } else { 0.0 }).collect())
            .collect();
        let objective = Objective {
            x0: &x0,
            p0: &p0,
            y: &y,
            c: &c,
        };
        let solution = inner_solve(&model, &objective, &x0, 100, 1e-10).expect("converges");
        // Stationarity in group space: Jᵀ C⁻¹ (y − f) = P⁰⁻¹ (x − x⁰), checked as
        // P⁰ Jᵀ C⁻¹ (y − f) = x − x⁰ so that P⁰ is not inverted.
        let (f, jac) = model.evaluate(&solution.x).expect("positive");
        let w: Vec<f64> = (0..3).map(|i| (y[i] - f[i]) / 9e-4).collect();
        let gradient: Vec<f64> = (0..n)
            .map(|k| (0..3).map(|i| jac[i][k] * w[i]).sum())
            .collect();
        let moved = mat_vec(&p0, &gradient);
        for k in 0..n {
            assert!(
                (moved[k] - (solution.x[k] - x0[k])).abs() < 1e-8,
                "group {k}: {} vs {}",
                moved[k],
                solution.x[k] - x0[k]
            );
        }
    }

    #[test]
    fn bands_snap_to_the_nearest_boundary_and_collapse_duplicates() {
        let bounds = [1.0, 10.0, 100.0, 1000.0];
        let snapped = snap_bands(&bounds, &[1.0, 12.0, 15.0, 900.0]).expect("bands");
        assert_eq!(
            snapped.iter().map(|&(_, k)| k).collect::<Vec<_>>(),
            vec![0, 1, 3]
        );
        assert!(snap_bands(&bounds, &[100.0, 10.0]).is_err());
        assert!(snap_bands(&bounds, &[5.0, 6.0]).is_err());
        assert!(snap_bands(&bounds, &[0.0, 6.0]).is_err());
    }

    #[test]
    fn window_index_is_the_first_group_reaching_the_level() {
        let weights = [1.0, 1.0, 2.0, 6.0];
        assert_eq!(window_index(&weights, 0.05), Some(0));
        assert_eq!(window_index(&weights, 0.2), Some(1));
        assert_eq!(window_index(&weights, 0.5), Some(3));
        assert_eq!(window_index(&[0.0, 0.0], 0.5), None);
    }

    fn spec_with(extra: impl FnOnce(&mut Value)) -> Spec {
        let mut value = json!({
            "spec": "actinv-spec-1",
            "title": "unfold refusal fixture",
            "projectile": "neutron",
            "library": {"path": "missing.npz"},
            "decay": {"primary": "missing.json"},
            "material": {"mass_g": 1.0, "basis": "wt_percent", "composition": {"FE": 100.0}},
            "spectrum": {"structure": "fispact-709", "flux_per_group": vec![1.0; 709]},
            "schedule": [{"dt": "1 h", "flux": 1.0}, {"dt": "1 h", "flux": 0.0}],
        });
        extra(&mut value);
        serde_json::from_value(value).expect("fixture spec")
    }

    #[test]
    fn measured_nuclides_are_resolved_to_the_names_results_use() {
        assert_eq!(canonical_nuclide("In115m").unwrap(), "In115m1");
        assert_eq!(canonical_nuclide("In115m1").unwrap(), "In115m1");
        assert_eq!(canonical_nuclide("Hf178m2").unwrap(), "Hf178m2");
        assert_eq!(canonical_nuclide("Co58").unwrap(), "Co58");
        assert!(canonical_nuclide("Fe")
            .unwrap_err()
            .contains("explicit nuclide"));
    }

    #[test]
    fn refusals_name_the_problem() {
        let ok = spec_with(|_| {});
        assert!(validate_problem(&ok, true).is_ok());
        let cases: Vec<(Spec, &str)> = vec![
            (
                spec_with(|v| v["spectrum"]["relative_error"] = json!(vec![0.1; 709])),
                "relative_error",
            ),
            (
                spec_with(|v| v["schedule"][0]["feed"] = json!({"Fe56": 1.0})),
                "feed or removal",
            ),
            (
                spec_with(|v| v["schedule"][0]["partition"] = json!({"Fe": 0.5})),
                "partitioned",
            ),
            (
                spec_with(|v| {
                    v["uncertainty"] = json!({"channels": ["flux"], "responses": ["activity:Mn56"]})
                }),
                "only covariance and confidence_level",
            ),
            (
                spec_with(|v| v["self_shielding"] = json!({"dilution": "fixed", "sigma0_b": 10.0})),
                "self_shielding",
            ),
        ];
        for (spec, needle) in cases {
            match validate_problem(&spec, true) {
                Err(message) => assert!(message.contains(needle), "{message} lacks {needle}"),
                Ok(()) => panic!("expected a refusal naming {needle}"),
            }
        }
        let confidence_only = spec_with(|v| v["uncertainty"] = json!({"confidence_level": 0.9}));
        assert!(validate_problem(&confidence_only, false).is_ok());
    }

    #[test]
    fn measured_values_need_positive_sigma() {
        assert!(check_measured("a", 1.0, 0.1, 0.0).is_ok());
        assert!(check_measured("a", 0.0, 0.1, 0.0)
            .unwrap_err()
            .contains("positive"));
        assert!(check_measured("a", 1.0, 0.0, 0.0)
            .unwrap_err()
            .contains("sigma"));
        assert!(check_measured("a", 1.0, 0.1, -0.1).is_err());
    }
}
