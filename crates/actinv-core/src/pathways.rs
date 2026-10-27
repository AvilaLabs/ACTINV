//! Full pathways/routes analysis (P127). Every route from a source nuclide to a target nuclide through the
//! reaction and decay graph is enumerated by a pruned depth-first search, and its contribution to the target
//! inventory is integrated exactly: the chain `x0 -> x1 -> ... -> xk` is a lower-bidiagonal linear system with
//! each node's full removal rate (decay, every reaction, declared removal) on the diagonal and only the route's
//! own edge as the source term. It is propagated through every schedule step with a dense matrix exponential
//! (scaling and squaring, Pade 13) of the (k+1)x(k+1) system, using the very per-step matrices the solve built.
//! There is no Bateman closed form, so equal rates are harmless.
//!
//! The decomposition is the walk expansion of the matrix exponential: the target inventory is the sum, over every
//! walk through the graph, of that walk's chain-system contribution, and every term is nonnegative. A walk that
//! repeats a nuclide is a loop walk; it is reported against its loop-erased path. Because the real run supplies
//! the target total, the report states a closure per target (sum of retained routes, total, residual), so the
//! decomposition is an accounting rather than a list.
//!
//! The analysis is linear in the source atoms, so it serves trace mode (reservoir sources through the unit
//! state) and coupled mode (initial nuclides) alike.
use crate::chain::{self, Chain};
use crate::gas;
use crate::run::name_of;
use actinv_data::composition::MaterialKey;
use actinv_data::decay::Nuclide;
use actinv_data::fission::EffectiveYields;
use actinv_data::library::Row;
use num_complex::Complex64 as C64;
use serde::{Deserialize, Serialize};
use std::cmp::Ordering;
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};

/// Longest route a spec may ask for (edges). The dense exponential is O(depth^3) per step per route.
const MAX_DEPTH_LIMIT: usize = 64;
/// Hard cap on search-tree nodes visited; hitting it is reported as `search_truncated`. The shared first pass
/// spends it over all sources and targets; when it runs out, every target is searched again on its own floor
/// with this budget, divided fairly among the sources.
const EXPLORE_LIMIT: usize = 2_000_000;
/// Candidate routes integrated per target, as a multiple of `max_paths` (the best by upper bound are kept).
const CANDIDATE_FACTOR: usize = 8;
/// Label of an edge the label tables do not cover (for example a gas-ejectile bookkeeping edge).
const UNKNOWN_EDGE: &str = "edge";

/// Opt-in `pathways` spec section.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PathwayOptions {
    /// Target nuclides; empty selects the dominant nuclides of the final step automatically.
    #[serde(default)]
    pub targets: Vec<String>,
    /// Source nuclides; empty selects every initial nuclide (and, in a run with a feed, `feed`).
    #[serde(default)]
    pub sources: Vec<String>,
    /// Maximum number of edges between a source and a target (FISPACT-II default 10).
    #[serde(default = "default_max_depth")]
    pub max_depth: usize,
    /// A route is kept when it carries at least this fraction of its target's inventory.
    #[serde(default = "default_path_floor")]
    pub path_floor: f64,
    /// A loop walk is kept when it raises its loop-erased path's contribution by at least this fraction.
    #[serde(default = "default_loop_floor")]
    pub loop_floor: f64,
    /// Cap on routes reported per target; the excess is dropped smallest first and lands in the residual.
    #[serde(default = "default_max_paths")]
    pub max_paths: usize,
    /// Ranking for automatic targets: `activity` or `heat` at the final step.
    #[serde(default = "default_rank_by")]
    pub rank_by: String,
    /// Number of automatic targets.
    #[serde(default = "default_top_targets")]
    pub top_targets: usize,
    /// Also report every route's atoms at the end of each schedule step.
    #[serde(default)]
    pub by_step: bool,
    /// P153: when nonempty, routes may pass through fission edges only from these parents. The inventory is
    /// unchanged; what other parents' fission products received is reported as unexplained in the closure.
    /// Empty (the default) places no restriction.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub fission_parents: Vec<String>,
}

fn default_max_depth() -> usize {
    10
}
fn default_path_floor() -> f64 {
    0.005
}
fn default_loop_floor() -> f64 {
    0.01
}
fn default_max_paths() -> usize {
    100
}
fn default_rank_by() -> String {
    "activity".into()
}
fn default_top_targets() -> usize {
    10
}

impl Default for PathwayOptions {
    fn default() -> Self {
        Self {
            targets: Vec::new(),
            sources: Vec::new(),
            max_depth: default_max_depth(),
            path_floor: default_path_floor(),
            loop_floor: default_loop_floor(),
            max_paths: default_max_paths(),
            rank_by: default_rank_by(),
            top_targets: default_top_targets(),
            by_step: false,
            fission_parents: Vec::new(),
        }
    }
}

/// Canonical nuclide name of a spec nuclide spelling, the same naming the result inventory uses.
fn nuclide_name(raw: &str, field: &str) -> Result<String, String> {
    match actinv_data::composition::material_key(raw)
        .map_err(|error| format!("pathways.{field} '{raw}': {error}"))?
    {
        MaterialKey::Nuclide { za, liso, .. } => Ok(name_of(za, liso)),
        _ => Err(format!(
            "pathways.{field} '{raw}' must be an explicit nuclide, e.g. 'Mn56'"
        )),
    }
}

/// A source is a nuclide or the literal `feed` (the schedule's declared feed).
fn source_name(raw: &str) -> Result<String, String> {
    if raw == "feed" {
        Ok(raw.to_owned())
    } else {
        nuclide_name(raw, "sources")
    }
}

impl PathwayOptions {
    pub fn validate(&self) -> Result<(), String> {
        if self.max_depth == 0 || self.max_depth > MAX_DEPTH_LIMIT {
            return Err(format!(
                "pathways.max_depth must be between 1 and {MAX_DEPTH_LIMIT}"
            ));
        }
        if !self.path_floor.is_finite() || self.path_floor <= 0.0 || self.path_floor > 1.0 {
            return Err("pathways.path_floor must be in (0, 1]".into());
        }
        if !self.loop_floor.is_finite() || !(0.0..=1.0).contains(&self.loop_floor) {
            return Err("pathways.loop_floor must be in [0, 1]".into());
        }
        if self.max_paths == 0 || self.max_paths > 100_000 {
            return Err("pathways.max_paths must be between 1 and 100000".into());
        }
        if self.top_targets == 0 || self.top_targets > 1000 {
            return Err("pathways.top_targets must be between 1 and 1000".into());
        }
        if self.rank_by != "activity" && self.rank_by != "heat" {
            return Err(format!(
                "pathways.rank_by must be 'activity' or 'heat', got '{}'",
                self.rank_by
            ));
        }
        for raw in &self.targets {
            nuclide_name(raw, "targets")?;
        }
        for raw in &self.sources {
            source_name(raw)?;
        }
        for raw in &self.fission_parents {
            nuclide_name(raw, "fission_parents")?;
        }
        Ok(())
    }

    /// The nuclides `fission_parents` names as `(ZA, LISO)`; `None` when the restriction is off.
    pub fn allowed_fission_parents(&self) -> Result<Option<BTreeSet<(i32, i32)>>, String> {
        if self.fission_parents.is_empty() {
            return Ok(None);
        }
        self.fission_parents
            .iter()
            .map(|raw| crate::spec::nuclide_key(raw, "pathways.fission_parents"))
            .collect::<Result<_, _>>()
            .map(Some)
    }
}

// ---- dense matrix exponential -------------------------------------------------------------------------------

const PADE13: [f64; 14] = [
    64764752532480000.0,
    32382376266240000.0,
    7771770303897600.0,
    1187353796428800.0,
    129060195264000.0,
    10559470521600.0,
    670442572800.0,
    33522128640.0,
    1323241920.0,
    40840800.0,
    960960.0,
    16380.0,
    182.0,
    1.0,
];
/// Largest 1-norm for which the degree-13 Pade approximant meets double-precision backward error (Higham 2005).
const THETA13: f64 = 5.371920351148152;

fn identity(n: usize) -> Vec<f64> {
    let mut m = vec![0.0; n * n];
    for i in 0..n {
        m[i * n + i] = 1.0;
    }
    m
}

/// Row-major `n x n` product.
fn matmul(a: &[f64], b: &[f64], n: usize) -> Vec<f64> {
    let mut c = vec![0.0; n * n];
    for i in 0..n {
        for k in 0..n {
            let aik = a[i * n + k];
            if aik == 0.0 {
                continue;
            }
            for j in 0..n {
                c[i * n + j] += aik * b[k * n + j];
            }
        }
    }
    c
}

/// Solve `a x = b` for an `n x n` right-hand-side block by Gaussian elimination with partial pivoting.
fn solve(mut a: Vec<f64>, mut b: Vec<f64>, n: usize) -> Result<Vec<f64>, String> {
    for col in 0..n {
        let mut pivot = col;
        let mut best = a[col * n + col].abs();
        for row in col + 1..n {
            let value = a[row * n + col].abs();
            if value > best {
                best = value;
                pivot = row;
            }
        }
        if best == 0.0 || !best.is_finite() {
            return Err("matrix exponential: singular Pade denominator".into());
        }
        if pivot != col {
            for j in 0..n {
                a.swap(col * n + j, pivot * n + j);
                b.swap(col * n + j, pivot * n + j);
            }
        }
        let diagonal = a[col * n + col];
        for row in col + 1..n {
            let factor = a[row * n + col] / diagonal;
            if factor == 0.0 {
                continue;
            }
            for j in col..n {
                let above = a[col * n + j];
                a[row * n + j] -= factor * above;
            }
            for j in 0..n {
                let above = b[col * n + j];
                b[row * n + j] -= factor * above;
            }
        }
    }
    for col in 0..n {
        for row in (0..n).rev() {
            let mut value = b[row * n + col];
            for j in row + 1..n {
                value -= a[row * n + j] * b[j * n + col];
            }
            b[row * n + col] = value / a[row * n + row];
        }
    }
    Ok(b)
}

/// `sinh(x) / x`, with a short series where the quotient would lose digits.
fn sinhc(x: f64) -> f64 {
    if x.abs() < 0.1 {
        let x2 = x * x;
        1.0 + x2 / 6.0
            * (1.0 + x2 / 20.0 * (1.0 + x2 / 42.0 * (1.0 + x2 / 72.0 * (1.0 + x2 / 110.0))))
    } else {
        x.sinh() / x
    }
}

/// Off-diagonal entry of `exp([[a11, 0], [off, a22]])` (or its transpose, which is the same value): Higham,
/// Functions of Matrices (2008), eq. (10.42), `off * exp((a11 + a22) / 2) * sinhc((a22 - a11) / 2)`. For well
/// separated diagonal entries the algebraically equal divided difference avoids overflow of the half-sum form.
fn block_off_diagonal(a11: f64, a22: f64, off: f64) -> f64 {
    if off == 0.0 {
        return 0.0;
    }
    let half_gap = 0.5 * (a22 - a11);
    if half_gap.abs() < 1.0 {
        off * (0.5 * (a11 + a22)).exp() * sinhc(half_gap)
    } else {
        off * (a22.exp() - a11.exp()) / (a22 - a11)
    }
}

/// Overwrite the diagonal and first off-diagonal of the triangular `r ~ exp(factor * a)` with values computed
/// directly from `a` (Al-Mohy and Higham, SIAM J. Matrix Anal. Appl. 31(3):970-989, 2009, section 2 and Code
/// Fragment 2.1). Repeated squaring otherwise multiplies the rounding error of a tiny leading exponential into
/// the slow modes ("overscaling"); the exact diagonal and first off-diagonal stop that.
fn triangular_refresh(r: &mut [f64], a: &[f64], n: usize, lower: bool, factor: f64) {
    for i in 0..n {
        r[i * n + i] = (factor * a[i * n + i]).exp();
    }
    for i in 0..n.saturating_sub(1) {
        let (row, col) = if lower { (i + 1, i) } else { (i, i + 1) };
        r[row * n + col] = block_off_diagonal(
            factor * a[i * n + i],
            factor * a[(i + 1) * n + i + 1],
            factor * a[row * n + col],
        );
    }
}

/// `Some(true)` when every entry above the diagonal is exactly zero, `Some(false)` when every entry below it is
/// (and some above is not), `None` for a general matrix.
fn triangular_kind(a: &[f64], n: usize) -> Option<bool> {
    let upper_zero = (0..n).all(|i| (i + 1..n).all(|j| a[i * n + j] == 0.0));
    if upper_zero {
        return Some(true);
    }
    let lower_zero = (0..n).all(|i| (0..i).all(|j| a[i * n + j] == 0.0));
    lower_zero.then_some(false)
}

/// Dense matrix exponential of a row-major `n x n` matrix: scaling and squaring with the degree-13 Pade
/// approximant (Higham, SIAM J. Matrix Anal. Appl. 26, 2005). Exactly triangular input (the chain matrices)
/// additionally gets the Al-Mohy and Higham (2009) diagonal and first off-diagonal correction after the Pade
/// step and after every squaring, so a huge norm from a short-lived nuclide does not cost the slow modes digits.
pub(crate) fn expm(a: &[f64], n: usize) -> Result<Vec<f64>, String> {
    if a.len() != n * n {
        return Err(format!(
            "matrix exponential: {} entries for an {n}x{n} matrix",
            a.len()
        ));
    }
    if n == 0 {
        return Ok(Vec::new());
    }
    if a.iter().any(|x| !x.is_finite()) {
        return Err("matrix exponential: input is not finite".into());
    }
    let norm = (0..n)
        .map(|j| (0..n).map(|i| a[i * n + j].abs()).sum::<f64>())
        .fold(0.0, f64::max);
    let squarings = if norm > THETA13 {
        ((norm / THETA13).log2().ceil() as usize).min(1000)
    } else {
        0
    };
    let scale = 0.5f64.powi(squarings as i32);
    let a1: Vec<f64> = a.iter().map(|x| x * scale).collect();
    let a2 = matmul(&a1, &a1, n);
    let a4 = matmul(&a2, &a2, n);
    let a6 = matmul(&a4, &a2, n);
    let ident = identity(n);
    let b = &PADE13;
    let combine = |terms: &[(f64, &[f64])]| -> Vec<f64> {
        let mut out = vec![0.0; n * n];
        for (coefficient, matrix) in terms {
            for (o, x) in out.iter_mut().zip(matrix.iter()) {
                *o += coefficient * x;
            }
        }
        out
    };
    // U = A [ A6 (b13 A6 + b11 A4 + b9 A2) + b7 A6 + b5 A4 + b3 A2 + b1 I ]
    let inner_u = combine(&[
        (b[13], a6.as_slice()),
        (b[11], a4.as_slice()),
        (b[9], a2.as_slice()),
    ]);
    let mut w = matmul(&a6, &inner_u, n);
    let low_u = combine(&[
        (b[7], a6.as_slice()),
        (b[5], a4.as_slice()),
        (b[3], a2.as_slice()),
        (b[1], ident.as_slice()),
    ]);
    for (x, y) in w.iter_mut().zip(&low_u) {
        *x += y;
    }
    let u = matmul(&a1, &w, n);
    // V = A6 (b12 A6 + b10 A4 + b8 A2) + b6 A6 + b4 A4 + b2 A2 + b0 I
    let inner_v = combine(&[
        (b[12], a6.as_slice()),
        (b[10], a4.as_slice()),
        (b[8], a2.as_slice()),
    ]);
    let mut v = matmul(&a6, &inner_v, n);
    let low_v = combine(&[
        (b[6], a6.as_slice()),
        (b[4], a4.as_slice()),
        (b[2], a2.as_slice()),
        (b[0], ident.as_slice()),
    ]);
    for (x, y) in v.iter_mut().zip(&low_v) {
        *x += y;
    }
    let numerator: Vec<f64> = v.iter().zip(&u).map(|(v, u)| v + u).collect();
    let denominator: Vec<f64> = v.iter().zip(&u).map(|(v, u)| v - u).collect();
    let mut r = solve(denominator, numerator, n)?;
    if let Some(lower) = triangular_kind(a, n) {
        triangular_refresh(&mut r, a, n, lower, scale);
        for j in 1..=squarings {
            r = matmul(&r, &r, n);
            triangular_refresh(&mut r, a, n, lower, 0.5f64.powi((squarings - j) as i32));
        }
    } else {
        for _ in 0..squarings {
            r = matmul(&r, &r, n);
        }
    }
    if r.iter().any(|x| !x.is_finite()) {
        return Err("matrix exponential: result is not finite".into());
    }
    Ok(r)
}

/// Advance the chain system `x0 -> x1 -> ... -> xk` by `dt` seconds: `diag[i]` is node i's (nonpositive) removal
/// rate and `sub[i]` the rate of the edge i -> i+1. When every edge rate is positive the system is first
/// rescaled by the diagonal similarity that makes each sub-diagonal entry 1 (`D^-1 M D`, exact), so the
/// exponential works on an O(1)-balanced matrix whatever the rate magnitudes; otherwise it works on the raw matrix.
fn chain_step(diag: &[f64], sub: &[f64], dt: f64, v: &mut [f64]) -> Result<(), String> {
    let k = diag.len();
    if k == 1 {
        v[0] *= (diag[0] * dt).exp();
        return Ok(());
    }
    let mut scale = vec![1.0f64; k];
    let mut balanced = true;
    for i in 0..k - 1 {
        let a = sub[i] * dt;
        let s = scale[i] * a;
        if a > 0.0 && s.is_finite() && s > 1e-250 && s < 1e250 {
            scale[i + 1] = s;
        } else {
            balanced = false;
            break;
        }
    }
    let mut m = vec![0.0; k * k];
    for i in 0..k {
        m[i * k + i] = diag[i] * dt;
        if i + 1 < k {
            m[(i + 1) * k + i] = if balanced { 1.0 } else { sub[i] * dt };
        }
    }
    let e = expm(&m, k)?;
    let start: Vec<f64> = if balanced {
        v.iter().zip(&scale).map(|(x, s)| x / s).collect()
    } else {
        v.to_vec()
    };
    for i in 0..k {
        let mut acc = 0.0;
        for (j, x) in start.iter().enumerate().take(i + 1) {
            acc += e[i * k + j] * x;
        }
        v[i] = if balanced { acc * scale[i] } else { acc };
    }
    for x in v.iter_mut() {
        if !x.is_finite() {
            return Err("pathway propagation produced a non-finite value".into());
        }
        // round-off can leave a -1e-30 where the exact entry is zero; every true term is nonnegative
        if *x < 0.0 {
            *x = 0.0;
        }
    }
    Ok(())
}

// ---- graph input -----------------------------------------------------------------------------------------------

/// One schedule step's graph, taken from the matrix the solve used for that step.
#[derive(Debug, Clone)]
pub struct StepGraph {
    pub duration_s: f64,
    /// `(to, from, value)` over tracked nodes: off-diagonal entries are edge rates (s^-1), diagonal entries are
    /// the negative total removal rates.
    pub edges: Vec<(usize, usize, f64)>,
    /// `(to, reservoir, rate)`: constant external supply from reservoir `reservoir`, in atoms g^-1 s^-1.
    pub feeds: Vec<(usize, usize, f64)>,
}

impl StepGraph {
    /// Remove the off-diagonal edges named by `dropped` (matrix positions `(to, from)`). Diagonal entries stay,
    /// so every node keeps its full removal rate; the reservoir feeds are filtered by the caller.
    pub fn without_edges(mut self, dropped: &HashSet<(usize, usize)>) -> Self {
        if !dropped.is_empty() {
            self.edges
                .retain(|&(to, from, _)| to == from || !dropped.contains(&(to, from)));
        }
        self
    }

    /// Split a step's assembled matrix triplets into tracked-node edges. Entries in the unit-state row or column
    /// are the reservoir supply, which the caller splits by origin into `feeds`.
    pub fn from_matrix(
        duration_s: f64,
        trip: &[(usize, usize, C64)],
        unit: usize,
        feeds: Vec<(usize, usize, f64)>,
    ) -> Self {
        let edges = trip
            .iter()
            .filter(|(i, j, _)| *i != unit && *j != unit)
            .map(|(i, j, v)| (*i, *j, v.re))
            .collect();
        Self {
            duration_s,
            edges,
            feeds,
        }
    }
}

/// Everything the analysis needs from a finished run.
#[derive(Debug, Clone)]
pub struct PathwayInput {
    /// Name of each tracked node (matrix position).
    pub node_names: Vec<String>,
    /// Constant reservoir sources (trace mode bulk nuclides, `feed`); reservoir p is node `node_names.len() + p`.
    pub reservoirs: Vec<String>,
    pub steps: Vec<StepGraph>,
    /// `(node, atoms g^-1)` initial inventory of tracked nodes (coupled mode).
    pub initial: Vec<(usize, f64)>,
    /// Edge labels keyed `(to, from)` over the extended node space.
    pub labels: HashMap<(usize, usize), String>,
    /// Final-step inventory by nuclide name (atoms g^-1), the real run's target totals.
    pub final_atoms: HashMap<String, f64>,
    /// Final-step activity by nuclide name, for automatic target ranking.
    pub activity: HashMap<String, f64>,
    /// Final-step decay heat by nuclide name, for automatic target ranking.
    pub heat: HashMap<String, f64>,
}

// ---- edge labels --------------------------------------------------------------------------------------------

/// Edge labels over global chain indices, `(row, column)`; a pair can carry several (a decay and a reaction).
pub type GlobalLabels = BTreeMap<(usize, usize), BTreeSet<String>>;

/// FISPACT-style name of a decay mode digit (ENDF RTYP).
fn decay_mode_name(digit: u32) -> &'static str {
    match digit {
        1 => "b-",
        2 => "b+/EC",
        3 => "IT",
        4 => "a",
        5 => "n",
        7 => "p",
        _ => "?",
    }
}

/// Decay edge labels, replaying the daughter resolution of `chain::build` mode by mode.
pub fn decay_edge_labels(
    nuclides: &HashMap<(i32, i32), Nuclide>,
    network: &Chain,
    labels: &mut GlobalLabels,
) {
    // gas bookkeeping edges (alpha into He4, proton into H1) exist only when the gas states are in the chain
    let he4 = network.index.get(&gas::HE4).copied();
    let h1 = network.index.get(&gas::H1).copied();
    for (k, key) in network.keys.iter().enumerate() {
        let Some(nuclide) = nuclides.get(key) else {
            continue;
        };
        if nuclide.lambda() == 0.0 {
            continue;
        }
        let (z, a) = (nuclide.za / 1000, nuclide.za % 1000);
        let mut spontaneous_fission = false;
        let mut decay_rows: BTreeSet<usize> = BTreeSet::new();
        for mode in &nuclide.modes {
            if mode.br <= 0.0 {
                continue;
            }
            let digits = chain::rtyp_digits(mode.rtyp);
            spontaneous_fission |= digits.first() == Some(&6);
            let (mut zz, mut aa) = (z, a);
            let mut names: Vec<&'static str> = Vec::new();
            let mut resolved = true;
            for digit in &digits {
                match chain::step_of(*digit) {
                    Some((dz, da)) => {
                        zz += dz;
                        aa += da;
                        names.push(decay_mode_name(*digit));
                    }
                    None => {
                        resolved = false;
                        break;
                    }
                }
            }
            if let Some(row) = he4.filter(|_| digits.contains(&4)) {
                labels.entry((row, k)).or_default().insert("a".to_owned());
                decay_rows.insert(row);
            }
            if let Some(row) = h1.filter(|_| digits.contains(&7)) {
                labels.entry((row, k)).or_default().insert("p".to_owned());
                decay_rows.insert(row);
            }
            if !resolved {
                continue;
            }
            let want = (zz * 1000 + aa, mode.rfs.round() as i32);
            let daughter = network
                .index
                .get(&want)
                .or_else(|| network.index.get(&(zz * 1000 + aa, 0)));
            if let Some(&j) = daughter {
                if j != k {
                    labels.entry((j, k)).or_default().insert(names.join(","));
                    decay_rows.insert(j);
                }
            }
        }
        // Spontaneous-fission products (P125) enter the matrix as edges from this state; any edge the
        // other modes do not explain is a fission product.
        if spontaneous_fission {
            for &(row, column, rate) in &network.decay {
                if column == k
                    && row != k
                    && row != network.leak
                    && row != network.unit
                    && rate > 0.0
                    && !decay_rows.contains(&row)
                {
                    labels.entry((row, k)).or_default().insert("SF".to_owned());
                }
            }
        }
    }
}

/// ENDF MT as a reaction name. Only neutron libraries have the conventional names; other projectiles keep `MTnnn`.
pub fn reaction_label(mt: i32, neutron: bool) -> String {
    if !neutron {
        return format!("MT{mt}");
    }
    let name = match mt {
        4 | 51..=91 => "(n,n')",
        11 => "(n,2nd)",
        16 => "(n,2n)",
        17 => "(n,3n)",
        18 => "(n,f)",
        22 => "(n,na)",
        23 => "(n,n3a)",
        24 => "(n,2na)",
        25 => "(n,3na)",
        28 => "(n,np)",
        29 => "(n,n2a)",
        30 => "(n,2n2a)",
        32 => "(n,nd)",
        33 => "(n,nt)",
        34 => "(n,nh)",
        35 => "(n,nd2a)",
        36 => "(n,nt2a)",
        37 => "(n,4n)",
        41 => "(n,2np)",
        42 => "(n,3np)",
        44 => "(n,n2p)",
        45 => "(n,npa)",
        102 => "(n,g)",
        103 => "(n,p)",
        104 => "(n,d)",
        105 => "(n,t)",
        106 => "(n,h)",
        107 => "(n,a)",
        108 => "(n,2a)",
        109 => "(n,3a)",
        111 => "(n,2p)",
        112 => "(n,pa)",
        113 => "(n,t2a)",
        114 => "(n,d2a)",
        115 => "(n,pd)",
        116 => "(n,pt)",
        117 => "(n,da)",
        _ => return format!("(n,MT{mt})"),
    };
    name.to_owned()
}

/// Reaction edge labels from the activation library's rows, replaying the product resolution of
/// `chain::reaction_rates` (the isomer fallback to the ground state, fission through the effective yields).
pub fn reaction_edge_labels(
    labels: &mut GlobalLabels,
    rows: &[Row],
    lib_targets: &[(i32, i32)],
    chain: &Chain,
    fission_yields: &HashMap<(i32, i32), EffectiveYields>,
    neutron: bool,
) {
    for row in rows {
        if row.zap == -1 {
            continue; // loss term: no product
        }
        let Some(&target) = lib_targets.get(row.target) else {
            continue;
        };
        let Some(&column) = chain.index.get(&target) else {
            continue;
        };
        if row.mt == 18 && row.zap == 0 {
            if let Some(yields) = fission_yields.get(&target) {
                for (product, yield_value) in &yields.products {
                    if *yield_value == 0.0 {
                        continue;
                    }
                    if let Some(&product_row) = chain.index.get(product) {
                        labels
                            .entry((product_row, column))
                            .or_default()
                            .insert("fission".to_owned());
                    }
                }
            }
            continue;
        }
        if row.lmf == -2 {
            continue; // unmapped residual: the product leaks
        }
        let product = chain
            .index
            .get(&(row.zap, row.lfs))
            .or_else(|| chain.index.get(&(row.zap, 0)));
        if let Some(&product_row) = product {
            labels
                .entry((product_row, column))
                .or_default()
                .insert(reaction_label(row.mt, neutron));
        }
    }
}

/// Fission-product edges the `fission_parents` restriction removes, as global `(product row, parent column)`
/// pairs: for each library fission row (MT 18) of a parent outside `allowed`, every chain product with a
/// nonzero yield in any table of `kind`. A pair that also carries a decay or non-fission reaction (`other`,
/// the labels of those edges) keeps its edge: the matrix entry cannot be split, so that attribution stays.
pub fn excluded_fission_edges(
    rows: &[Row],
    lib_targets: &[(i32, i32)],
    chain: &Chain,
    yields: &HashMap<(i32, i32), actinv_data::fission::FissionYields>,
    kind: actinv_data::fission::YieldKind,
    allowed: &BTreeSet<(i32, i32)>,
    other: &GlobalLabels,
) -> HashSet<(usize, usize)> {
    let mut out = HashSet::new();
    for row in rows {
        if row.mt != 18 || row.zap != 0 {
            continue;
        }
        let Some(&parent) = lib_targets.get(row.target) else {
            continue;
        };
        if allowed.contains(&parent) {
            continue;
        }
        let (Some(&column), Some(data)) = (chain.index.get(&parent), yields.get(&parent)) else {
            continue;
        };
        for table in data.tables(kind) {
            for (product, value) in &table.products {
                if value.value == 0.0 {
                    continue;
                }
                if let Some(&product_row) = chain.index.get(product) {
                    if other
                        .get(&(product_row, column))
                        .is_none_or(|set| set.is_empty())
                    {
                        out.insert((product_row, column));
                    }
                }
            }
        }
    }
    out
}

/// Map global-index labels onto matrix positions: `pos[global]` is the tracked position (`usize::MAX` if pruned),
/// `reservoir_states[p]` the chain state reservoir p stands for (`None` for `feed`). The result is keyed
/// `(to, from)` over the extended node space, where reservoir p is node `n_tracked + p` and carries the labels of
/// the chain state it stands for.
pub fn position_labels(
    global: &GlobalLabels,
    pos: &[usize],
    reservoir_states: &[Option<usize>],
    n_tracked: usize,
) -> HashMap<(usize, usize), String> {
    let reservoir_of: HashMap<usize, usize> = reservoir_states
        .iter()
        .enumerate()
        .filter_map(|(p, state)| state.map(|c| (c, p)))
        .collect();
    let mut out = HashMap::new();
    for (&(row, column), set) in global {
        if row >= pos.len() || column >= pos.len() || pos[row] == usize::MAX {
            continue;
        }
        let label = set.iter().cloned().collect::<Vec<_>>().join("/");
        if pos[column] != usize::MAX {
            out.insert((pos[row], pos[column]), label.clone());
        }
        if let Some(&p) = reservoir_of.get(&column) {
            out.insert((pos[row], n_tracked + p), label);
        }
    }
    out
}

// ---- search ---------------------------------------------------------------------------------------------------

struct Graph {
    n_tracked: usize,
    durations: Vec<f64>,
    /// per step, per node: total removal rate (s^-1, nonnegative)
    removal: Vec<Vec<f64>>,
    /// per step: `(from, to)` -> edge rate (s^-1, or atoms g^-1 s^-1 from a reservoir)
    rate: Vec<HashMap<(usize, usize), f64>>,
    /// adjacency over the union of the steps
    out: Vec<Vec<usize>>,
    /// Upper bound on the fraction of atoms that ever enter `from` and leave through the edge to `to`:
    /// `min(max_s rate_s / removal_s, sum_s rate_s dt_s)`. The first bound is conservation, the second is
    /// `N <= cumulative inflow`. For a reservoir the removal is zero, so the bound is the atoms supplied.
    weight: HashMap<(usize, usize), f64>,
}

impl Graph {
    fn build(input: &PathwayInput) -> Result<Self, String> {
        let n_tracked = input.node_names.len();
        let n_nodes = n_tracked + input.reservoirs.len();
        let mut durations = Vec::with_capacity(input.steps.len());
        let mut removal = Vec::with_capacity(input.steps.len());
        let mut rate = Vec::with_capacity(input.steps.len());
        let mut out_sets: Vec<BTreeSet<usize>> = vec![BTreeSet::new(); n_nodes];
        for step in &input.steps {
            let mut step_removal = vec![0.0f64; n_nodes];
            let mut step_rate: HashMap<(usize, usize), f64> = HashMap::new();
            for &(to, from, value) in &step.edges {
                if to >= n_tracked || from >= n_tracked {
                    return Err(format!(
                        "pathway edge ({to}, {from}) is outside the {n_tracked} tracked nodes"
                    ));
                }
                if to == from {
                    step_removal[from] -= value;
                } else if value > 0.0 {
                    *step_rate.entry((from, to)).or_insert(0.0) += value;
                    out_sets[from].insert(to);
                }
            }
            for &(to, reservoir, value) in &step.feeds {
                if to >= n_tracked || reservoir >= input.reservoirs.len() {
                    return Err(format!(
                        "pathway feed ({to}, {reservoir}) is outside the graph"
                    ));
                }
                if value > 0.0 {
                    let from = n_tracked + reservoir;
                    *step_rate.entry((from, to)).or_insert(0.0) += value;
                    out_sets[from].insert(to);
                }
            }
            for x in step_removal.iter_mut() {
                if *x < 0.0 {
                    *x = 0.0;
                }
            }
            durations.push(step.duration_s);
            removal.push(step_removal);
            rate.push(step_rate);
        }
        let mut weight = HashMap::new();
        for (from, targets) in out_sets.iter().enumerate() {
            for &to in targets {
                let mut ratio = 0.0f64;
                let mut cumulative = 0.0f64;
                for (s, step_rate) in rate.iter().enumerate() {
                    if let Some(&r) = step_rate.get(&(from, to)) {
                        cumulative += r * durations[s];
                        let loss = removal[s][from];
                        ratio = ratio.max(if loss > 0.0 { r / loss } else { f64::INFINITY });
                    }
                }
                weight.insert((from, to), ratio.min(cumulative));
            }
        }
        let out: Vec<Vec<usize>> = out_sets
            .into_iter()
            .map(|set| set.into_iter().collect())
            .collect();
        Ok(Self {
            n_tracked,
            durations,
            removal,
            rate,
            out,
            weight,
        })
    }

    /// Atoms at every node of the chain system for `walk`, after each schedule step, starting from `initial`
    /// atoms at the first node.
    fn history(&self, walk: &[usize], initial: f64) -> Result<Vec<Vec<f64>>, String> {
        let mut v = vec![0.0; walk.len()];
        v[0] = initial;
        let mut out = Vec::with_capacity(self.durations.len());
        for s in 0..self.durations.len() {
            let diag: Vec<f64> = walk.iter().map(|&u| -self.removal[s][u]).collect();
            let sub: Vec<f64> = walk
                .windows(2)
                .map(|w| self.rate[s].get(&(w[0], w[1])).copied().unwrap_or(0.0))
                .collect();
            chain_step(&diag, &sub, self.durations[s], &mut v)?;
            out.push(v.clone());
        }
        Ok(out)
    }
}

struct Candidate {
    target: usize,
    walk: Vec<usize>,
    bound: f64,
}

struct Search<'a> {
    graph: &'a Graph,
    max_depth: usize,
    /// branches whose bound falls under this are pruned
    floor_min: f64,
    target_floor: &'a HashMap<usize, f64>,
    /// tree nodes this search may visit
    limit: usize,
    explored: usize,
    truncated: bool,
    found: Vec<Candidate>,
}

impl Search<'_> {
    fn dfs(&mut self, walk: &mut Vec<usize>, bound: f64) {
        if self.explored >= self.limit {
            self.truncated = true;
            return;
        }
        self.explored += 1;
        let node = walk[walk.len() - 1];
        if let Some(&floor) = self.target_floor.get(&node) {
            if bound >= floor {
                self.found.push(Candidate {
                    target: node,
                    walk: walk.clone(),
                    bound,
                });
            }
        }
        if walk.len() > self.max_depth {
            return;
        }
        let graph = self.graph;
        for &next in &graph.out[node] {
            let next_bound = bound * graph.weight[&(node, next)];
            if next_bound >= self.floor_min {
                walk.push(next);
                self.dfs(walk, next_bound);
                walk.pop();
            }
        }
    }
}

/// Chronological loop erasure: drop every cycle, keeping the first visit of each node.
fn erase_loops(walk: &[usize]) -> Vec<usize> {
    let mut stack: Vec<usize> = Vec::with_capacity(walk.len());
    for &node in walk {
        match stack.iter().position(|&n| n == node) {
            Some(at) => stack.truncate(at + 1),
            None => stack.push(node),
        }
    }
    stack
}

/// Per-walk integration with a prefix cache: the chain system of a long walk contains the exact contribution
/// of every prefix, so integrating the longest candidates first fills the cache for the shorter ones.
struct Evaluator<'a> {
    graph: &'a Graph,
    initial: &'a HashMap<usize, f64>,
    cache: HashMap<Vec<usize>, Vec<f64>>,
    integrated: usize,
}

impl Evaluator<'_> {
    /// Atoms at the walk's last node after each schedule step.
    fn contribution(&mut self, walk: &[usize]) -> Result<Vec<f64>, String> {
        if let Some(found) = self.cache.get(walk) {
            return Ok(found.clone());
        }
        let start = self.initial.get(&walk[0]).copied().unwrap_or(0.0);
        let history = self.graph.history(walk, start)?;
        self.integrated += 1;
        for j in 0..walk.len() {
            self.cache
                .entry(walk[..=j].to_vec())
                .or_insert_with(|| history.iter().map(|row| row[j]).collect());
        }
        Ok(self.cache[walk].clone())
    }
}

// ---- report ------------------------------------------------------------------------------------------------

fn is_zero(n: &usize) -> bool {
    *n == 0
}

fn is_false(flag: &bool) -> bool {
    !*flag
}

#[derive(Debug, Serialize)]
pub struct PathRoute {
    pub source: String,
    pub nuclides: Vec<String>,
    pub edges: Vec<String>,
    /// Atoms g^-1 at the final step carried by this route.
    pub atoms_per_g: f64,
    /// Share of the target's real-run inventory.
    pub fraction: f64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub atoms_per_g_by_step: Option<Vec<f64>>,
    pub is_loop: bool,
    /// Loop walks only: the contribution relative to the loop-erased path.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub loop_gain: Option<f64>,
    /// Loop walks only: the loop-erased path.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub base_path: Option<Vec<String>>,
}

#[derive(Debug, Serialize)]
pub struct GenericPath {
    /// Space-separated edge types from source to target, for example `(n,g) b-`.
    pub edge_types: String,
    pub n_paths: usize,
    pub atoms_per_g: f64,
    pub fraction: f64,
}

#[derive(Debug, Serialize)]
pub struct TargetPaths {
    pub target: String,
    /// The real run's final-step inventory of the target.
    pub total_atoms_per_g: f64,
    /// Sum of the reported routes.
    pub paths_atoms_per_g: f64,
    /// `total - paths`: the part of the inventory no reported route explains.
    pub residual_atoms_per_g: f64,
    pub residual_fraction: f64,
    pub explained_fraction: f64,
    /// Portion of `paths_atoms_per_g` carried by loop walks.
    pub loop_atoms_per_g: f64,
    /// Routes that passed the floors but fell outside `max_paths`.
    pub paths_dropped_by_cap: usize,
    /// Candidate routes of this target beyond `8 * max_paths` that were never integrated (smallest upper
    /// bound dropped first). Omitted when none.
    #[serde(skip_serializing_if = "is_zero")]
    pub candidates_dropped_by_cap: usize,
    /// The route search for this target ran out of budget, so routes may be missing. Omitted when it did not.
    #[serde(skip_serializing_if = "is_false")]
    pub search_truncated: bool,
    /// Sources whose search tree for this target was cut short by the budget. Omitted when none.
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub truncated_sources: Vec<String>,
    pub paths: Vec<PathRoute>,
    pub generic: Vec<GenericPath>,
}

#[derive(Debug, Serialize)]
pub struct PathwayReport {
    pub schema: &'static str,
    pub method: &'static str,
    pub options: PathwayOptions,
    pub sources: Vec<String>,
    pub explored_walks: usize,
    pub routes_integrated: usize,
    pub candidates_dropped_by_cap: usize,
    pub search_truncated: bool,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub notes: Vec<String>,
    pub targets: Vec<TargetPaths>,
}

struct Hit {
    walk: Vec<usize>,
    by_step: Vec<f64>,
    atoms: f64,
    base: Option<Vec<usize>>,
    gain: Option<f64>,
}

fn node_label(input: &PathwayInput, node: usize) -> &str {
    let n_tracked = input.node_names.len();
    if node < n_tracked {
        &input.node_names[node]
    } else {
        &input.reservoirs[node - n_tracked]
    }
}

/// Label of an edge without a decay or reaction label: the declared feed and the removal sink name themselves.
fn edge_label(input: &PathwayInput, from: usize, to: usize) -> String {
    if let Some(label) = input.labels.get(&(to, from)) {
        return label.clone();
    }
    let n_tracked = input.node_names.len();
    if from >= n_tracked && input.reservoirs[from - n_tracked] == "feed" {
        "feed".to_owned()
    } else if to < n_tracked && input.node_names[to] == "removed" {
        "removal".to_owned()
    } else {
        UNKNOWN_EDGE.to_owned()
    }
}

/// Enumerate and integrate the routes into each target.
pub fn analyse(input: &PathwayInput, options: &PathwayOptions) -> Result<PathwayReport, String> {
    options.validate()?;
    let graph = Graph::build(input)?;
    let n_tracked = graph.n_tracked;
    let mut notes: Vec<String> = Vec::new();

    // ---- sources: reservoirs supply a constant unit population, initial nuclides their initial atoms
    let mut sources: Vec<(usize, f64, String)> = input
        .reservoirs
        .iter()
        .enumerate()
        .map(|(p, name)| (n_tracked + p, 1.0, name.clone()))
        .collect();
    for &(node, atoms) in &input.initial {
        if node >= n_tracked {
            return Err(format!(
                "pathway initial node {node} is outside the {n_tracked} tracked nodes"
            ));
        }
        if atoms > 0.0 {
            sources.push((node, atoms, input.node_names[node].clone()));
        }
    }
    if !options.sources.is_empty() {
        let mut wanted: BTreeSet<String> = BTreeSet::new();
        for raw in &options.sources {
            wanted.insert(source_name(raw)?);
        }
        for name in &wanted {
            if !sources.iter().any(|source| &source.2 == name) {
                notes.push(format!(
                    "source '{name}' is not an initial nuclide of this run"
                ));
            }
        }
        sources.retain(|source| wanted.contains(&source.2));
    }

    // ---- targets
    let target_names: Vec<String> = if options.targets.is_empty() {
        let table = if options.rank_by == "heat" {
            &input.heat
        } else {
            &input.activity
        };
        let mut ranked: Vec<(&String, f64)> = table
            .iter()
            .filter(|(_, value)| **value > 0.0)
            .map(|(name, value)| (name, *value))
            .collect();
        ranked.sort_by(|a, b| {
            b.1.partial_cmp(&a.1)
                .unwrap_or(Ordering::Equal)
                .then_with(|| a.0.cmp(b.0))
        });
        ranked.truncate(options.top_targets);
        ranked.into_iter().map(|(name, _)| name.clone()).collect()
    } else {
        let mut seen: HashSet<String> = HashSet::new();
        let mut names = Vec::new();
        for raw in &options.targets {
            let name = nuclide_name(raw, "targets")?;
            if seen.insert(name.clone()) {
                names.push(name);
            }
        }
        names
    };
    let name_to_node: HashMap<&str, usize> = input
        .node_names
        .iter()
        .enumerate()
        .map(|(node, name)| (name.as_str(), node))
        .collect();
    let mut target_floor: HashMap<usize, f64> = HashMap::new();
    for name in &target_names {
        let total = input.final_atoms.get(name).copied().unwrap_or(0.0);
        match name_to_node.get(name.as_str()) {
            None => notes.push(format!(
                "target '{name}' is not a tracked state of this run"
            )),
            Some(&node) if total > 0.0 => {
                target_floor.insert(node, options.path_floor * total);
            }
            Some(_) => {}
        }
    }

    // ---- search: a route's atoms are bounded by the product of its edge weights, so a branch dies as soon as
    // the bound falls under the smallest target floor
    let floor_min = target_floor.values().copied().fold(f64::INFINITY, f64::min);
    let mut explored = 0usize;
    let mut found: Vec<Candidate> = Vec::new();
    // per target node: the sources whose tree was cut short by the budget
    let mut truncated_by_target: BTreeMap<usize, Vec<String>> = BTreeMap::new();
    if floor_min.is_finite() {
        let mut search = Search {
            graph: &graph,
            max_depth: options.max_depth,
            floor_min,
            target_floor: &target_floor,
            limit: EXPLORE_LIMIT,
            explored: 0,
            truncated: false,
            found: Vec::new(),
        };
        for (node, atoms, _) in &sources {
            let mut walk = vec![*node];
            search.dfs(&mut walk, *atoms);
        }
        explored = search.explored;
        let shared_truncated = search.truncated;
        found = search.found;
        if shared_truncated {
            // The shared floor is the smallest target's and the budget is spent source by source, so one
            // tiny target or one wide source can starve the rest. Search every target again on its own
            // floor, with the budget divided fairly among the sources.
            notes.push(format!(
                "the shared route search stopped after {EXPLORE_LIMIT} tree nodes; each target was searched again on its own floor with a per-source budget"
            ));
            explored = 0;
            found.clear();
            let mut nodes: Vec<(usize, f64)> = target_floor.iter().map(|(&n, &f)| (n, f)).collect();
            nodes.sort_by_key(|(node, _)| *node);
            for (target, floor) in nodes {
                let floors = HashMap::from([(target, floor)]);
                let mut remaining = EXPLORE_LIMIT;
                for (index, (node, atoms, name)) in sources.iter().enumerate() {
                    let share = remaining / (sources.len() - index);
                    let mut one = Search {
                        graph: &graph,
                        max_depth: options.max_depth,
                        floor_min: floor,
                        target_floor: &floors,
                        limit: share,
                        explored: 0,
                        truncated: false,
                        found: Vec::new(),
                    };
                    let mut walk = vec![*node];
                    one.dfs(&mut walk, *atoms);
                    remaining -= one.explored;
                    explored += one.explored;
                    found.append(&mut one.found);
                    if one.truncated {
                        truncated_by_target
                            .entry(target)
                            .or_default()
                            .push(name.clone());
                    }
                }
            }
        }
    }
    let truncated = !truncated_by_target.is_empty();
    for (&node, names) in &truncated_by_target {
        notes.push(format!(
            "target '{}': the route search stopped after its budget of {EXPLORE_LIMIT} tree nodes (sources cut short: {}); raise path_floor or lower max_depth",
            input.node_names[node],
            names.join(", ")
        ));
    }
    found.sort_by(|a, b| {
        b.bound
            .partial_cmp(&a.bound)
            .unwrap_or(Ordering::Equal)
            .then_with(|| a.walk.cmp(&b.walk))
    });
    let candidate_cap = options.max_paths.saturating_mul(CANDIDATE_FACTOR);
    let mut per_target: HashMap<usize, usize> = HashMap::new();
    let mut dropped_by_target: BTreeMap<usize, usize> = BTreeMap::new();
    let mut kept: Vec<Candidate> = Vec::new();
    let mut candidates_dropped = 0usize;
    for candidate in found {
        let count = per_target.entry(candidate.target).or_insert(0);
        if *count >= candidate_cap {
            candidates_dropped += 1;
            *dropped_by_target.entry(candidate.target).or_insert(0) += 1;
        } else {
            *count += 1;
            kept.push(candidate);
        }
    }
    for (&node, &count) in &dropped_by_target {
        notes.push(format!(
            "target '{}': {count} candidate routes beyond the cap of {candidate_cap} (8 x max_paths) were not integrated, smallest upper bound first; raise max_paths",
            input.node_names[node]
        ));
    }
    // longest first: a long walk's chain system carries all of its prefixes
    kept.sort_by(|a, b| {
        b.walk
            .len()
            .cmp(&a.walk.len())
            .then_with(|| a.walk.cmp(&b.walk))
    });

    // ---- integrate
    let initial: HashMap<usize, f64> = sources.iter().map(|s| (s.0, s.1)).collect();
    let mut evaluator = Evaluator {
        graph: &graph,
        initial: &initial,
        cache: HashMap::new(),
        integrated: 0,
    };
    let mut hits: HashMap<usize, Vec<Hit>> = HashMap::new();
    for candidate in &kept {
        let total = input
            .final_atoms
            .get(&input.node_names[candidate.target])
            .copied()
            .unwrap_or(0.0);
        let by_step = evaluator.contribution(&candidate.walk)?;
        let atoms = by_step.last().copied().unwrap_or(0.0);
        if atoms <= 0.0 || total <= 0.0 || atoms / total < options.path_floor {
            continue;
        }
        let mut seen: HashSet<usize> = HashSet::new();
        let is_loop = candidate.walk.iter().any(|node| !seen.insert(*node));
        let (base, gain) = if is_loop {
            let base_walk = erase_loops(&candidate.walk);
            let base_atoms = evaluator
                .contribution(&base_walk)?
                .last()
                .copied()
                .unwrap_or(0.0);
            if base_atoms <= 0.0 || atoms / base_atoms < options.loop_floor {
                continue;
            }
            (Some(base_walk), Some(atoms / base_atoms))
        } else {
            (None, None)
        };
        hits.entry(candidate.target).or_default().push(Hit {
            walk: candidate.walk.clone(),
            by_step,
            atoms,
            base,
            gain,
        });
    }
    let routes_integrated = evaluator.integrated;

    // ---- assemble
    let mut targets_out = Vec::with_capacity(target_names.len());
    for name in &target_names {
        let total = input.final_atoms.get(name).copied().unwrap_or(0.0);
        let mut target_hits = name_to_node
            .get(name.as_str())
            .and_then(|node| hits.remove(node))
            .unwrap_or_default();
        target_hits.sort_by(|a, b| {
            b.atoms
                .partial_cmp(&a.atoms)
                .unwrap_or(Ordering::Equal)
                .then_with(|| a.walk.cmp(&b.walk))
        });
        let paths_dropped = target_hits.len().saturating_sub(options.max_paths);
        target_hits.truncate(options.max_paths);
        let paths_sum: f64 = target_hits.iter().map(|hit| hit.atoms).sum();
        let loop_sum: f64 = target_hits
            .iter()
            .filter(|hit| hit.base.is_some())
            .map(|hit| hit.atoms)
            .sum();
        let share = |atoms: f64| if total > 0.0 { atoms / total } else { 0.0 };
        let mut generic: BTreeMap<String, (usize, f64)> = BTreeMap::new();
        let mut paths = Vec::with_capacity(target_hits.len());
        for hit in &target_hits {
            let nuclides: Vec<String> = hit
                .walk
                .iter()
                .map(|&node| node_label(input, node).to_owned())
                .collect();
            let edges: Vec<String> = hit
                .walk
                .windows(2)
                .map(|w| edge_label(input, w[0], w[1]))
                .collect();
            let key = if edges.is_empty() {
                "(none)".to_owned()
            } else {
                edges.join(" ")
            };
            let entry = generic.entry(key).or_insert((0, 0.0));
            entry.0 += 1;
            entry.1 += hit.atoms;
            paths.push(PathRoute {
                source: nuclides[0].clone(),
                nuclides,
                edges,
                atoms_per_g: hit.atoms,
                fraction: share(hit.atoms),
                atoms_per_g_by_step: options.by_step.then(|| hit.by_step.clone()),
                is_loop: hit.base.is_some(),
                loop_gain: hit.gain,
                base_path: hit.base.as_ref().map(|base| {
                    base.iter()
                        .map(|&node| node_label(input, node).to_owned())
                        .collect()
                }),
            });
        }
        let mut generic: Vec<GenericPath> = generic
            .into_iter()
            .map(|(edge_types, (n_paths, atoms))| GenericPath {
                edge_types,
                n_paths,
                atoms_per_g: atoms,
                fraction: share(atoms),
            })
            .collect();
        generic.sort_by(|a, b| {
            b.atoms_per_g
                .partial_cmp(&a.atoms_per_g)
                .unwrap_or(Ordering::Equal)
                .then_with(|| a.edge_types.cmp(&b.edge_types))
        });
        targets_out.push(TargetPaths {
            target: name.clone(),
            total_atoms_per_g: total,
            paths_atoms_per_g: paths_sum,
            residual_atoms_per_g: total - paths_sum,
            residual_fraction: share(total - paths_sum),
            explained_fraction: share(paths_sum),
            loop_atoms_per_g: loop_sum,
            paths_dropped_by_cap: paths_dropped,
            candidates_dropped_by_cap: name_to_node
                .get(name.as_str())
                .and_then(|node| dropped_by_target.get(node))
                .copied()
                .unwrap_or(0),
            search_truncated: name_to_node
                .get(name.as_str())
                .is_some_and(|node| truncated_by_target.contains_key(node)),
            truncated_sources: name_to_node
                .get(name.as_str())
                .and_then(|node| truncated_by_target.get(node))
                .cloned()
                .unwrap_or_default(),
            paths,
            generic,
        });
    }
    Ok(PathwayReport {
        schema: "actinv-pathways-1",
        method: "exact chain-system integration of pruned walk enumeration",
        options: options.clone(),
        sources: sources.into_iter().map(|source| source.2).collect(),
        explored_walks: explored,
        routes_integrated,
        candidates_dropped_by_cap: candidates_dropped,
        search_truncated: truncated,
        notes,
        targets: targets_out,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use actinv_data::decay::Mode;

    fn assert_close(actual: f64, expected: f64, relative: f64, what: &str) {
        let scale = expected.abs().max(f64::MIN_POSITIVE);
        assert!(
            (actual - expected).abs() <= relative * scale,
            "{what}: {actual} vs {expected}"
        );
    }

    /// Full-system reference: `exp(M t) x0` through the same dense exponential, but on the whole matrix.
    fn full_solution(m: &[f64], n: usize, t: f64, x0: &[f64]) -> Vec<f64> {
        let scaled: Vec<f64> = m.iter().map(|x| x * t).collect();
        let e = expm(&scaled, n).unwrap();
        (0..n)
            .map(|i| (0..n).map(|j| e[i * n + j] * x0[j]).sum())
            .collect()
    }

    fn single_step(
        duration_s: f64,
        edges: Vec<(usize, usize, f64)>,
        feeds: Vec<(usize, usize, f64)>,
    ) -> StepGraph {
        StepGraph {
            duration_s,
            edges,
            feeds,
        }
    }

    fn input(
        names: &[&str],
        reservoirs: &[&str],
        steps: Vec<StepGraph>,
        initial: Vec<(usize, f64)>,
        totals: &[(&str, f64)],
    ) -> PathwayInput {
        let mut labels = HashMap::new();
        // every off-diagonal edge is a beta decay unless a test says otherwise
        for step in &steps {
            for &(to, from, v) in &step.edges {
                if to != from && v > 0.0 {
                    labels.insert((to, from), "b-".to_owned());
                }
            }
            for &(to, reservoir, _) in &step.feeds {
                labels.insert((to, names.len() + reservoir), "(n,g)".to_owned());
            }
        }
        PathwayInput {
            node_names: names.iter().map(|n| (*n).to_owned()).collect(),
            reservoirs: reservoirs.iter().map(|n| (*n).to_owned()).collect(),
            steps,
            initial,
            labels,
            final_atoms: totals.iter().map(|(n, v)| ((*n).to_owned(), *v)).collect(),
            activity: totals.iter().map(|(n, v)| ((*n).to_owned(), *v)).collect(),
            heat: HashMap::new(),
        }
    }

    fn options_for(target: &str) -> PathwayOptions {
        PathwayOptions {
            targets: vec![target.to_owned()],
            path_floor: 1e-9,
            loop_floor: 1e-9,
            ..PathwayOptions::default()
        }
    }

    #[test]
    fn expm_matches_the_analytic_two_by_two_exponentials() {
        // lower-triangular M = [[-a, 0], [a, -b]]: exp(Mt) = [[e^-at, 0], [a (e^-at - e^-bt) / (b - a), e^-bt]]
        let (a, b, t) = (0.7, 2.3, 1.9);
        let e = expm(&[-a * t, 0.0, a * t, -b * t], 2).unwrap();
        assert_close(e[0], (-a * t).exp(), 1e-14, "e00");
        assert!(e[1].abs() < 1e-15, "upper triangle stays empty");
        let lower = a * ((-a * t).exp() - (-b * t).exp()) / (b - a);
        assert_close(e[2], lower, 1e-13, "e10");
        assert_close(e[3], (-b * t).exp(), 1e-14, "e11");
        // rotation generator [[0, -w], [w, 0]]: exp = [[cos w, -sin w], [sin w, cos w]]
        let w = 25.0;
        let r = expm(&[0.0, -w, w, 0.0], 2).unwrap();
        assert_close(r[0], w.cos(), 1e-12, "cos");
        assert_close(r[1], -w.sin(), 1e-12, "-sin");
        assert_close(r[2], w.sin(), 1e-12, "sin");
        assert_close(r[3], w.cos(), 1e-12, "cos");
        // the identity for the zero matrix, and a loud failure for non-finite input
        assert_eq!(expm(&[0.0; 4], 2).unwrap(), vec![1.0, 0.0, 0.0, 1.0]);
        assert!(expm(&[f64::NAN, 0.0, 0.0, 0.0], 2).is_err());
    }

    #[test]
    fn expm_of_a_large_norm_matrix_uses_scaling_and_squaring() {
        let e = expm(&[-1.0e6, 0.0, 0.0, -2.0], 2).unwrap();
        assert!(e[0].abs() < 1e-300, "fast mode underflows");
        assert_close(e[3], (-2.0f64).exp(), 1e-13, "slow mode");
    }

    /// The dense exponential as it was before the triangular refresh: Pade 13 scaling and squaring only.
    fn expm_plain(a: &[f64], n: usize) -> Vec<f64> {
        let norm = (0..n)
            .map(|j| (0..n).map(|i| a[i * n + j].abs()).sum::<f64>())
            .fold(0.0, f64::max);
        let squarings = if norm > THETA13 {
            ((norm / THETA13).log2().ceil() as usize).min(1000)
        } else {
            0
        };
        let scale = 0.5f64.powi(squarings as i32);
        let a1: Vec<f64> = a.iter().map(|x| x * scale).collect();
        let a2 = matmul(&a1, &a1, n);
        let a4 = matmul(&a2, &a2, n);
        let a6 = matmul(&a4, &a2, n);
        let ident = identity(n);
        let b = &PADE13;
        let combine = |terms: &[(f64, &[f64])]| -> Vec<f64> {
            let mut out = vec![0.0; n * n];
            for (coefficient, matrix) in terms {
                for (o, x) in out.iter_mut().zip(matrix.iter()) {
                    *o += coefficient * x;
                }
            }
            out
        };
        let inner_u = combine(&[
            (b[13], a6.as_slice()),
            (b[11], a4.as_slice()),
            (b[9], a2.as_slice()),
        ]);
        let mut w = matmul(&a6, &inner_u, n);
        let low_u = combine(&[
            (b[7], a6.as_slice()),
            (b[5], a4.as_slice()),
            (b[3], a2.as_slice()),
            (b[1], ident.as_slice()),
        ]);
        for (x, y) in w.iter_mut().zip(&low_u) {
            *x += y;
        }
        let u = matmul(&a1, &w, n);
        let inner_v = combine(&[
            (b[12], a6.as_slice()),
            (b[10], a4.as_slice()),
            (b[8], a2.as_slice()),
        ]);
        let mut v = matmul(&a6, &inner_v, n);
        let low_v = combine(&[
            (b[6], a6.as_slice()),
            (b[4], a4.as_slice()),
            (b[2], a2.as_slice()),
            (b[0], ident.as_slice()),
        ]);
        for (x, y) in v.iter_mut().zip(&low_v) {
            *x += y;
        }
        let numerator: Vec<f64> = v.iter().zip(&u).map(|(v, u)| v + u).collect();
        let denominator: Vec<f64> = v.iter().zip(&u).map(|(v, u)| v - u).collect();
        let mut r = solve(denominator, numerator, n).unwrap();
        for _ in 0..squarings {
            r = matmul(&r, &r, n);
        }
        r
    }

    /// Entry `(i, j)` of `exp(t M)` for the chain `M` with `M[k][k] = -rates[k]` and `M[k+1][k] = rates[k]`, by the
    /// Bateman sum over distinct rates.
    fn bateman_entry(rates: &[f64], t: f64, i: usize, j: usize) -> f64 {
        if i < j {
            return 0.0;
        }
        let feed: f64 = rates[j..i].iter().product();
        let mut sum = 0.0;
        for k in j..=i {
            let mut denominator = 1.0;
            for (l, rate) in rates.iter().enumerate().take(i + 1).skip(j) {
                if l != k {
                    denominator *= rate - rates[k];
                }
            }
            sum += (-rates[k] * t).exp() / denominator;
        }
        feed * sum
    }

    #[test]
    fn expm_of_a_stiff_lower_bidiagonal_chain_matches_the_bateman_solution() {
        // rates span 1e3 .. 1e-9 /s over 1e8 s: the matrix 1-norm is 1e11 (about 34 squarings)
        let rates: [f64; 4] = [1.0e3, 3.0e-8, 1.0e-8, 1.0e-9];
        let t: f64 = 1.0e8;
        let n = rates.len();
        let mut lower = vec![0.0; n * n];
        for (k, rate) in rates.iter().enumerate() {
            lower[k * n + k] = -(rate * t);
            if k + 1 < n {
                lower[(k + 1) * n + k] = rate * t;
            }
        }
        let mut upper = vec![0.0; n * n];
        for i in 0..n {
            for j in 0..n {
                upper[j * n + i] = lower[i * n + j];
            }
        }
        let e_lower = expm(&lower, n).unwrap();
        let e_upper = expm(&upper, n).unwrap();
        let mut checked = 0;
        for i in 0..n {
            for j in 0..n {
                let expected = bateman_entry(&rates, t, i, j);
                let what = format!("lower ({i},{j})");
                if expected > 1e-300 {
                    assert_close(e_lower[i * n + j], expected, 1e-12, &what);
                    assert_close(
                        e_upper[j * n + i],
                        expected,
                        1e-12,
                        &format!("upper ({j},{i})"),
                    );
                    checked += 1;
                } else {
                    assert!(e_lower[i * n + j].abs() < 1e-300, "{what} should vanish");
                    assert!(
                        e_upper[j * n + i].abs() < 1e-300,
                        "{what} (upper) should vanish"
                    );
                }
            }
        }
        // everything on and below the diagonal survives except the fast mode itself
        assert_eq!(checked, 9);
    }

    #[test]
    fn expm_of_a_non_triangular_matrix_is_unchanged_by_the_triangular_refresh() {
        let mut state = 0x2545_f491_4f6c_dd1du64;
        let mut next = || {
            state ^= state << 13;
            state ^= state >> 7;
            state ^= state << 17;
            (state >> 11) as f64 / (1u64 << 53) as f64 - 0.5
        };
        let mut cases: Vec<(Vec<f64>, usize)> = vec![
            (vec![0.0, -25.0, 25.0, 0.0], 2),
            (vec![-1.0e6, 1.0, 1.0, -2.0], 2),
            (vec![-3.0, 0.5, 0.0, 2.0, -4.0, 0.0, 0.0, 1.0e-3, -0.1], 3),
        ];
        for (n, magnitude) in [(3usize, 1.0), (4, 40.0), (5, 3.0e3), (6, 0.2)] {
            // Rate-matrix shaped: a negative, dominant diagonal keeps every eigenvalue in the
            // left half-plane (Gershgorin), so the exponential stays finite as in real chains.
            let mut a: Vec<f64> = (0..n * n).map(|_| magnitude * next()).collect();
            for i in 0..n {
                a[i * n + i] = -(magnitude * n as f64) - a[i * n + i].abs();
            }
            cases.push((a, n));
        }
        for (a, n) in &cases {
            let new = expm(a, *n).unwrap();
            let old = expm_plain(a, *n);
            let new_bits: Vec<u64> = new.iter().map(|x| x.to_bits()).collect();
            let old_bits: Vec<u64> = old.iter().map(|x| x.to_bits()).collect();
            assert_eq!(new_bits, old_bits, "{n}x{n} case");
        }
    }

    #[test]
    fn triangular_refresh_handles_equal_and_close_diagonal_entries() {
        // equal diagonal: exp([[a, 0], [c, a]]) = e^a [[1, 0], [c, 1]]
        let (a, c): (f64, f64) = (-2.5, 4.0);
        let e = expm(&[a, 0.0, c, a], 2).unwrap();
        assert_close(e[2], c * a.exp(), 1e-14, "equal diagonal");
        // nearly equal diagonal still agrees with the confluent limit
        let e = expm(&[a, 0.0, c, a * (1.0 + 1e-12)], 2).unwrap();
        assert_close(e[2], c * a.exp(), 1e-10, "close diagonal");
        // block formula and the divided difference agree where they meet
        let (a11, a22, off): (f64, f64, f64) = (-1.2, -0.2, 3.0);
        let direct = off * (f64::exp(a22) - f64::exp(a11)) / (a22 - a11);
        assert_close(
            block_off_diagonal(a11, a22, off),
            direct,
            1e-14,
            "half gap 0.5",
        );
        let (a11, a22): (f64, f64) = (-2.0, 0.0);
        let direct = off * (f64::exp(a22) - f64::exp(a11)) / (a22 - a11);
        assert_close(
            block_off_diagonal(a11, a22, off),
            direct,
            1e-14,
            "half gap 1",
        );
    }

    #[test]
    fn linear_chain_single_path_equals_the_full_solution() {
        let (la, lb, n0, t): (f64, f64, f64, f64) = (1.0e-3, 3.0e-4, 1.0e20, 5000.0);
        // Bateman, distinct rates: N_C = N0 (1 - (lb e^-la t - la e^-lb t) / (lb - la))
        let total = n0 * (1.0 - (lb * (-la * t).exp() - la * (-lb * t).exp()) / (lb - la));
        let graph = input(
            &["Mn54", "Fe54", "Co54"],
            &[],
            vec![single_step(
                t,
                vec![(0, 0, -la), (1, 1, -lb), (1, 0, la), (2, 1, lb)],
                vec![],
            )],
            vec![(0, n0)],
            &[("Co54", total)],
        );
        let report = analyse(&graph, &options_for("Co54")).unwrap();
        assert_eq!(report.targets.len(), 1);
        let target = &report.targets[0];
        assert_eq!(target.paths.len(), 1);
        assert_eq!(target.paths[0].nuclides, ["Mn54", "Fe54", "Co54"]);
        assert_eq!(target.paths[0].edges, ["b-", "b-"]);
        assert!(!target.paths[0].is_loop);
        assert_close(target.paths[0].atoms_per_g, total, 1e-11, "path atoms");
        assert!(target.residual_fraction.abs() < 1e-11, "{target:?}");
        assert_close(target.explained_fraction, 1.0, 1e-11, "explained");
        assert_eq!(target.generic.len(), 1);
        assert_eq!(target.generic[0].edge_types, "b- b-");
    }

    #[test]
    fn branching_paths_sum_to_the_total_and_group_into_generic_paths() {
        // A -> B (30%), A -> C (70%), B -> D, C -> D
        let (la, lb, lc, t, n0) = (2.0e-3, 1.0e-3, 4.0e-4, 3000.0, 1.0e18);
        let edges = vec![
            (0, 0, -la),
            (1, 0, 0.3 * la),
            (2, 0, 0.7 * la),
            (1, 1, -lb),
            (3, 1, lb),
            (2, 2, -lc),
            (3, 2, lc),
        ];
        let mut m = vec![0.0; 16];
        for &(to, from, v) in &edges {
            m[to * 4 + from] += v;
        }
        let total = full_solution(&m, 4, t, &[n0, 0.0, 0.0, 0.0])[3];
        let graph = input(
            &["Cr54", "Mn54", "Fe54", "Co54"],
            &[],
            vec![single_step(t, edges, vec![])],
            vec![(0, n0)],
            &[("Co54", total)],
        );
        let report = analyse(&graph, &options_for("Co54")).unwrap();
        let target = &report.targets[0];
        assert_eq!(target.paths.len(), 2);
        let sum: f64 = target.paths.iter().map(|p| p.atoms_per_g).sum();
        assert_close(sum, total, 1e-10, "sum of the two paths");
        assert!(target.residual_fraction.abs() < 1e-10);
        // ranked by contribution, and both routes share one edge-type sequence
        assert!(target.paths[0].atoms_per_g >= target.paths[1].atoms_per_g);
        assert_eq!(target.generic.len(), 1);
        assert_eq!(target.generic[0].n_paths, 2);
        assert_close(target.generic[0].atoms_per_g, total, 1e-10, "generic");
    }

    #[test]
    fn equal_rate_chain_has_no_bateman_singularity() {
        let (l, t, n0): (f64, f64, f64) = (2.0e-3, 3000.0, 1.0e15);
        let x = l * t;
        // equal rates: N_C = N0 (1 - e^-x (1 + x))
        let total = n0 * (1.0 - (-x).exp() * (1.0 + x));
        let graph = input(
            &["Mn54", "Fe54", "Co54"],
            &[],
            vec![single_step(
                t,
                vec![(0, 0, -l), (1, 1, -l), (1, 0, l), (2, 1, l)],
                vec![],
            )],
            vec![(0, n0)],
            &[("Co54", total)],
        );
        let report = analyse(&graph, &options_for("Co54")).unwrap();
        let path = &report.targets[0].paths[0];
        assert!(path.atoms_per_g.is_finite());
        assert_close(path.atoms_per_g, total, 1e-11, "equal-rate path");
        assert!(report.targets[0].residual_fraction.abs() < 1e-11);
    }

    #[test]
    fn loops_are_reported_against_their_loop_erased_path() {
        // A -> B at a; B -> A at m, B -> C at n; C stable. Target B.
        let (a, m_rate, n_rate, t, n0) = (1.0e-3, 1.0e-4, 5.0e-4, 2000.0, 1.0e12);
        let edges = vec![
            (0, 0, -a),
            (1, 0, a),
            (1, 1, -(m_rate + n_rate)),
            (0, 1, m_rate),
            (2, 1, n_rate),
        ];
        let mut full = vec![0.0; 9];
        for &(to, from, v) in &edges {
            full[to * 3 + from] += v;
        }
        let total = full_solution(&full, 3, t, &[n0, 0.0, 0.0])[1];
        let graph = input(
            &["Mn54", "Fe54", "Co54"],
            &[],
            vec![single_step(t, edges, vec![])],
            vec![(0, n0)],
            &[("Fe54", total)],
        );
        let report = analyse(&graph, &options_for("Fe54")).unwrap();
        let target = &report.targets[0];
        let simple = target.paths.iter().find(|p| !p.is_loop).unwrap();
        assert_eq!(simple.nuclides, ["Mn54", "Fe54"]);
        let looped: Vec<_> = target.paths.iter().filter(|p| p.is_loop).collect();
        assert!(!looped.is_empty());
        for walk in &looped {
            let gain = walk.loop_gain.unwrap();
            assert!(gain > 0.0 && gain < 1.0, "{gain}");
            assert_eq!(walk.base_path.as_deref().unwrap(), ["Mn54", "Fe54"]);
            assert!(walk.nuclides.len() > 2);
        }
        assert!(target.loop_atoms_per_g > 0.0);
        // with every loop walk retained the decomposition closes
        assert!(target.residual_fraction.abs() < 1e-4, "{target:?}");
        assert!(target.residual_atoms_per_g >= -1e-9 * total);

        // a high loop floor keeps only the loop-free path and the closure shows what was left out
        let mut strict = options_for("Fe54");
        strict.loop_floor = 0.9;
        let report = analyse(&graph, &strict).unwrap();
        let target = &report.targets[0];
        assert!(target.paths.iter().all(|p| !p.is_loop));
        assert!(target.residual_fraction > 1e-4, "{target:?}");
    }

    #[test]
    fn reservoir_source_with_two_steps_matches_the_full_solution() {
        // reservoir supplies A at r during step 1 only; A -> B; target B, read after the cooling step
        let (r, l, t1, t2): (f64, f64, f64, f64) = (5.0e9, 2.0e-4, 4000.0, 6000.0);
        let step1 = single_step(t1, vec![(0, 0, -l), (1, 0, l)], vec![(0, 0, r)]);
        let step2 = single_step(t2, vec![(0, 0, -l), (1, 0, l)], vec![]);
        // Closed form, not `full_solution`: the reservoir column gives the full matrix a norm of
        // r*t1 = 2e13, where plain scaling and squaring overscales and loses about 1e-4.
        let a1 = r / l * (1.0 - (-l * t1).exp());
        let after1 = [a1, r * t1 - a1, 1.0];
        let after2 = [
            a1 * (-l * t2).exp(),
            after1[1] + a1 * (1.0 - (-l * t2).exp()),
            1.0,
        ];
        let graph = input(
            &["Mn54", "Fe54"],
            &["Cr54"],
            vec![step1, step2],
            vec![],
            &[("Fe54", after2[1])],
        );
        let mut options = options_for("Fe54");
        options.by_step = true;
        let report = analyse(&graph, &options).unwrap();
        assert_eq!(report.sources, ["Cr54"]);
        let target = &report.targets[0];
        assert_eq!(target.paths.len(), 1);
        let path = &target.paths[0];
        assert_eq!(path.nuclides, ["Cr54", "Mn54", "Fe54"]);
        assert_eq!(path.edges, ["(n,g)", "b-"]);
        assert_close(path.atoms_per_g, after2[1], 1e-10, "final");
        let by_step = path.atoms_per_g_by_step.as_ref().unwrap();
        assert_eq!(by_step.len(), 2);
        assert_close(by_step[0], after1[1], 1e-10, "after the first step");
        assert!(target.residual_fraction.abs() < 1e-10);
    }

    #[test]
    fn automatic_targets_follow_the_ranking_and_sources_can_be_filtered() {
        let (l, t, n0) = (1.0e-3, 1000.0, 1.0e10);
        let graph = input(
            &["Mn54", "Fe54", "Co54"],
            &[],
            vec![single_step(
                t,
                vec![(0, 0, -l), (1, 1, -l), (1, 0, l), (2, 1, l)],
                vec![],
            )],
            vec![(0, n0), (1, 1.0e10)],
            &[("Fe54", 3.0e9), ("Co54", 1.0e8)],
        );
        let mut options = PathwayOptions {
            top_targets: 1,
            path_floor: 1e-9,
            ..PathwayOptions::default()
        };
        let report = analyse(&graph, &options).unwrap();
        assert_eq!(report.targets.len(), 1);
        assert_eq!(report.targets[0].target, "Fe54");
        assert_eq!(report.sources, ["Mn54", "Fe54"]);
        // restrict to the Fe54 source: the Mn54 -> Fe54 route disappears
        options.sources = vec!["Fe54".into(), "Mn99".into()];
        let report = analyse(&graph, &options).unwrap();
        assert_eq!(report.sources, ["Fe54"]);
        assert!(report.notes.iter().any(|n| n.contains("Mn99")));
        assert!(report.targets[0].paths.iter().all(|p| p.source == "Fe54"));
        // a target that is not tracked is named, with an empty accounting
        let report = analyse(&graph, &options_for("Ni60")).unwrap();
        assert!(report.notes.iter().any(|n| n.contains("Ni60")));
        assert!(report.targets[0].paths.is_empty());
    }

    #[test]
    fn max_paths_caps_the_report_and_the_closure_keeps_the_difference() {
        let (la, t, n0) = (1.0e-3, 4000.0, 1.0e14);
        // A feeds B1..B4 equally, each decaying to D
        let mut edges = vec![(0, 0, -la)];
        for b in 1..=4 {
            edges.push((b, 0, la / 4.0));
            edges.push((b, b, -1.0e-3));
            edges.push((5, b, 1.0e-3));
        }
        let mut m = vec![0.0; 36];
        for &(to, from, v) in &edges {
            m[to * 6 + from] += v;
        }
        let mut x0 = vec![0.0; 6];
        x0[0] = n0;
        let total = full_solution(&m, 6, t, &x0)[5];
        let graph = input(
            &["Mn54", "Fe54", "Co54", "Ni54", "Cu54", "Zn54"],
            &[],
            vec![single_step(t, edges, vec![])],
            vec![(0, n0)],
            &[("Zn54", total)],
        );
        let mut options = options_for("Zn54");
        options.max_paths = 3;
        let report = analyse(&graph, &options).unwrap();
        let target = &report.targets[0];
        assert_eq!(target.paths.len(), 3);
        assert_eq!(target.paths_dropped_by_cap, 1);
        assert_close(target.explained_fraction, 0.75, 1e-9, "three of four");
        assert_close(target.residual_fraction, 0.25, 1e-9, "residual");
    }

    #[test]
    fn step_graph_drops_the_unit_state_entries() {
        let trip = vec![
            (0, 0, C64::new(-1.0, 0.0)),
            (1, 0, C64::new(0.5, 0.0)),
            (1, 2, C64::new(9.0, 0.0)), // reservoir supply through the unit state at position 2
        ];
        let step = StepGraph::from_matrix(10.0, &trip, 2, vec![(1, 0, 9.0)]);
        assert_eq!(step.edges, vec![(0, 0, -1.0), (1, 0, 0.5)]);
        assert_eq!(step.feeds, vec![(1, 0, 9.0)]);
    }

    #[test]
    fn fission_parent_filter_drops_only_pure_fission_edges_of_other_parents() {
        use actinv_data::fission::{FissionYields, YieldKind, YieldTable, YieldValue};
        let keys = vec![(92_235, 0), (92_238, 0), (53_135, 0), (54_135, 0)];
        let chain = Chain {
            index: keys.iter().enumerate().map(|(i, &k)| (k, i)).collect(),
            keys,
            lambda: vec![0.0; 4],
            decay: Vec::new(),
            leak: 4,
            unit: 5,
            n: 6,
            ledger: chain::ChainLedger::default(),
        };
        let fission_row = |target| Row {
            target,
            mt: 18,
            zap: 0,
            lfs: 0,
            lmf: 3,
        };
        let rows = [fission_row(0), fission_row(1)];
        let lib_targets = [(92_235, 0), (92_238, 0)];
        let evaluation = |parent| FissionYields {
            parent,
            awr: 0.0,
            independent: vec![YieldTable {
                energy_ev: 0.0253,
                products: [(53_135, 0), (54_135, 0)]
                    .into_iter()
                    .map(|key| {
                        (
                            key,
                            YieldValue {
                                value: 0.5,
                                uncertainty: 0.0,
                            },
                        )
                    })
                    .collect(),
                sum: 1.0,
            }],
            cumulative: Vec::new(),
        };
        let yields: HashMap<_, _> = [(92_235, 0), (92_238, 0)]
            .into_iter()
            .map(|parent| (parent, evaluation(parent)))
            .collect();
        let allowed = BTreeSet::from([(92_235, 0)]);
        // Xe135 also arises from U238 by a non-fission label: that pair keeps its edge
        let mut other = GlobalLabels::new();
        other.entry((3, 1)).or_default().insert("(n,x)".to_owned());
        let dropped = excluded_fission_edges(
            &rows,
            &lib_targets,
            &chain,
            &yields,
            YieldKind::Independent,
            &allowed,
            &other,
        );
        assert_eq!(dropped, HashSet::from([(2, 1)]));
        // nothing is dropped for a parent that is allowed, and an empty `allowed` drops both parents' edges
        let all = excluded_fission_edges(
            &rows,
            &lib_targets,
            &chain,
            &yields,
            YieldKind::Independent,
            &BTreeSet::new(),
            &GlobalLabels::new(),
        );
        assert_eq!(all.len(), 4);
        // the graph loses the dropped edge and keeps the diagonal
        let step = StepGraph {
            duration_s: 1.0,
            edges: vec![(1, 1, -2.0), (2, 1, 0.5), (2, 0, 0.25)],
            feeds: Vec::new(),
        }
        .without_edges(&HashSet::from([(2, 1), (1, 1)]));
        assert_eq!(step.edges, vec![(1, 1, -2.0), (2, 0, 0.25)]);
    }

    #[test]
    fn fission_parents_option_is_opt_in_and_validated() {
        let options = PathwayOptions::default();
        assert!(options.allowed_fission_parents().unwrap().is_none());
        assert!(serde_json::to_value(&options)
            .unwrap()
            .get("fission_parents")
            .is_none());
        let restricted = PathwayOptions {
            fission_parents: vec!["U235".into(), "Pu239".into()],
            ..options.clone()
        };
        assert!(restricted.validate().is_ok());
        assert_eq!(
            restricted.allowed_fission_parents().unwrap().unwrap(),
            BTreeSet::from([(92_235, 0), (94_239, 0)])
        );
        let bad = PathwayOptions {
            fission_parents: vec!["Unobtainium".into()],
            ..options
        };
        assert!(bad.validate().unwrap_err().contains("fission_parents"));
    }

    #[test]
    fn options_reject_bad_values_and_unknown_fields() {
        let ok = PathwayOptions::default();
        assert!(ok.validate().is_ok());
        for bad in [
            PathwayOptions {
                max_depth: 0,
                ..ok.clone()
            },
            PathwayOptions {
                max_depth: 1000,
                ..ok.clone()
            },
            PathwayOptions {
                path_floor: 0.0,
                ..ok.clone()
            },
            PathwayOptions {
                path_floor: f64::NAN,
                ..ok.clone()
            },
            PathwayOptions {
                loop_floor: 1.5,
                ..ok.clone()
            },
            PathwayOptions {
                max_paths: 0,
                ..ok.clone()
            },
            PathwayOptions {
                rank_by: "dose".into(),
                ..ok.clone()
            },
            PathwayOptions {
                targets: vec!["Fe".into()],
                ..ok.clone()
            },
            PathwayOptions {
                sources: vec!["Qq9".into()],
                ..ok.clone()
            },
        ] {
            assert!(bad.validate().is_err(), "{bad:?}");
        }
        let defaults: PathwayOptions = serde_json::from_str("{}").unwrap();
        assert_eq!(defaults, ok);
        assert_eq!(defaults.max_depth, 10);
        assert_eq!(defaults.path_floor, 0.005);
        assert_eq!(defaults.loop_floor, 0.01);
        assert!(serde_json::from_str::<PathwayOptions>(r#"{"depth": 3}"#).is_err());
        let with_feed = PathwayOptions {
            sources: vec!["feed".into(), "Fe54".into()],
            ..ok
        };
        assert!(with_feed.validate().is_ok());
    }

    #[test]
    fn loop_erasure_keeps_the_first_visit_of_each_node() {
        assert_eq!(erase_loops(&[0, 1, 0, 1]), vec![0, 1]);
        assert_eq!(erase_loops(&[0, 1, 2, 1, 3]), vec![0, 1, 3]);
        assert_eq!(erase_loops(&[4, 5, 4]), vec![4]);
        assert_eq!(erase_loops(&[7]), vec![7]);
    }

    #[test]
    fn reaction_labels_use_fispact_names_for_neutrons() {
        assert_eq!(reaction_label(102, true), "(n,g)");
        assert_eq!(reaction_label(16, true), "(n,2n)");
        assert_eq!(reaction_label(57, true), "(n,n')");
        assert_eq!(reaction_label(999, true), "(n,MT999)");
        assert_eq!(reaction_label(102, false), "MT102");
    }

    #[test]
    fn decay_labels_follow_the_chain_builder() {
        let nuclide = |za: i32, half_life: f64, modes: &[(f64, f64, f64)]| Nuclide {
            mat: 0,
            za,
            awr: 0.0,
            liso: 0,
            nst: if half_life > 0.0 { 0 } else { 1 },
            half_life,
            d_half_life: 0.0,
            energies: Vec::new(),
            modes: modes
                .iter()
                .map(|&(rtyp, rfs, br)| Mode {
                    rtyp,
                    rfs,
                    q: 0.0,
                    dq: 0.0,
                    br,
                    dbr: 0.0,
                })
                .collect(),
            spectra: Vec::new(),
        };
        let table: HashMap<(i32, i32), Nuclide> = [
            nuclide(26059, 3.8e6, &[(1.0, 0.0, 1.0)]),
            nuclide(27059, 0.0, &[]),
        ]
        .into_iter()
        .map(|n| ((n.za, n.liso), n))
        .collect();
        let chain = chain::build(&table);
        let mut labels = GlobalLabels::new();
        decay_edge_labels(&table, &chain, &mut labels);
        let fe = chain.index[&(26059, 0)];
        let co = chain.index[&(27059, 0)];
        assert_eq!(
            labels
                .get(&(co, fe))
                .map(|s| s.iter().cloned().collect::<Vec<_>>()),
            Some(vec!["b-".to_owned()])
        );
        // position mapping: both kept, and a reservoir standing for Fe59 inherits its edges
        let mut pos = vec![usize::MAX; chain.n];
        pos[fe] = 0;
        pos[co] = 1;
        let mapped = position_labels(&labels, &pos, &[Some(fe), None], 2);
        assert_eq!(mapped.get(&(1, 0)).map(String::as_str), Some("b-"));
        assert_eq!(mapped.get(&(1, 2)).map(String::as_str), Some("b-"));
    }

    #[test]
    fn p140_ps_f2_a_tiny_target_does_not_starve_the_routes_of_a_later_source() {
        // Junk cluster J0..J5 (nodes 0..5): fully connected, every edge carries 1/5 of its source's atoms, and
        // none of it reaches a target. Source Fe54 (node 6) feeds the real target Ni58 (node 7). Ni60 (node 8) is
        // a target with a tiny inventory, so the shared floor drops to 1e-36 and the junk tree (5^10 leaves)
        // exhausts the 2,000,000-node budget inside the first source before Fe54 is ever expanded.
        let (rate, t, n0): (f64, f64, f64) = (1.0e-3, 1000.0, 1.0e12);
        let mut edges = Vec::new();
        for from in 0..6 {
            edges.push((from, from, -5.0 * rate));
            for to in (0..6).filter(|to| *to != from) {
                edges.push((to, from, rate));
            }
        }
        edges.push((6, 6, -rate));
        edges.push((7, 6, rate));
        let total_a = n0 * (1.0 - (-rate * t).exp());
        let graph = input(
            &[
                "Cr50", "Cr51", "Cr52", "Cr53", "Cr54", "Mn55", "Fe54", "Ni58", "Ni60",
            ],
            &[],
            vec![single_step(t, edges, vec![])],
            vec![(0, n0), (6, n0)],
            &[("Ni58", total_a), ("Ni60", 1.0e-30)],
        );
        let with_targets = |targets: &[&str]| PathwayOptions {
            targets: targets.iter().map(|name| (*name).to_owned()).collect(),
            path_floor: 1e-6,
            loop_floor: 1e-9,
            ..PathwayOptions::default()
        };
        let alone = analyse(&graph, &with_targets(&["Ni58"])).unwrap();
        assert!(
            !alone.search_truncated,
            "the single-target search must finish"
        );
        assert_close(alone.targets[0].explained_fraction, 1.0, 1e-9, "alone");

        let both = analyse(&graph, &with_targets(&["Ni58", "Ni60"])).unwrap();
        let ni58 = both.targets.iter().find(|t| t.target == "Ni58").unwrap();
        // the routes into Ni58 must not depend on which other targets were asked for
        assert_close(
            ni58.explained_fraction,
            alone.targets[0].explained_fraction,
            1e-9,
            "Ni58 explained fraction with a tiny second target",
        );
        assert!(
            ni58.paths.iter().any(|path| path.source == "Fe54"),
            "the Fe54 -> Ni58 route was never explored (search_truncated = {})",
            both.search_truncated
        );
    }

    #[test]
    fn p140_ps_f5_candidate_cap_drops_are_reported_per_target_and_in_notes() {
        // one source, twelve parallel two-edge routes into the target; max_paths 1 keeps 8 candidates
        let (la, lm, t, n0) = (1.0e-3, 2.0e-3, 3000.0, 1.0e14);
        let mids = 12usize;
        let mut edges = vec![(0, 0, -(mids as f64) * la)];
        for m in 1..=mids {
            edges.push((m, 0, la));
            edges.push((m, m, -lm));
            edges.push((mids + 1, m, lm));
        }
        let n = mids + 2;
        let mut matrix = vec![0.0; n * n];
        for &(to, from, v) in &edges {
            matrix[to * n + from] += v;
        }
        let mut x0 = vec![0.0; n];
        x0[0] = n0;
        let total = full_solution(&matrix, n, t, &x0)[mids + 1];
        let mut names: Vec<String> = vec!["Fe54".to_owned()];
        names.extend((1..=mids).map(|m| format!("Mid{m}")));
        names.push("Zn54".to_owned());
        let names: Vec<&str> = names.iter().map(String::as_str).collect();
        let graph = input(
            &names,
            &[],
            vec![single_step(t, edges, vec![])],
            vec![(0, n0)],
            &[("Zn54", total)],
        );
        let mut options = options_for("Zn54");
        options.max_paths = 1;
        let report = analyse(&graph, &options).unwrap();
        assert_eq!(report.candidates_dropped_by_cap, mids - 8);
        let target = serde_json::to_value(&report.targets[0]).unwrap();
        assert_eq!(
            target["candidates_dropped_by_cap"],
            mids - 8,
            "the target block must say how many of its candidates the cap dropped: {target}"
        );
        assert!(
            report.notes.iter().any(|note| note.contains("candidate")),
            "the cap must be mentioned in the notes: {:?}",
            report.notes
        );
    }

    #[test]
    fn p140_ps_f8_spontaneous_fission_edges_are_labelled_sf() {
        let nuclide = |za: i32, half_life: f64, modes: &[(f64, f64, f64)]| Nuclide {
            mat: 0,
            za,
            awr: 0.0,
            liso: 0,
            nst: if half_life > 0.0 { 0 } else { 1 },
            half_life,
            d_half_life: 0.0,
            energies: Vec::new(),
            modes: modes
                .iter()
                .map(|&(rtyp, rfs, br)| Mode {
                    rtyp,
                    rfs,
                    q: 0.0,
                    dq: 0.0,
                    br,
                    dbr: 0.0,
                })
                .collect(),
            spectra: Vec::new(),
        };
        // Cf252 fissions spontaneously (RTYP 6) into two Ba140 per fission
        let table: HashMap<(i32, i32), Nuclide> = [
            nuclide(98252, 8.3e7, &[(6.0, 0.0, 1.0)]),
            nuclide(56140, 0.0, &[]),
        ]
        .into_iter()
        .map(|n| ((n.za, n.liso), n))
        .collect();
        let yields: chain::SpontaneousYields =
            HashMap::from([((98252, 0), BTreeMap::from([((56140, 0), 2.0)]))]);
        let network = chain::build_with_spontaneous(&table, Some(&yields));
        let (cf, ba) = (network.index[&(98252, 0)], network.index[&(56140, 0)]);
        assert!(
            network
                .decay
                .iter()
                .any(|&(row, column, rate)| row == ba && column == cf && rate > 0.0),
            "the spontaneous-fission edge must exist in the matrix"
        );
        let mut labels = GlobalLabels::new();
        decay_edge_labels(&table, &network, &mut labels);
        assert_eq!(
            labels
                .get(&(ba, cf))
                .map(|set| set.iter().cloned().collect::<Vec<_>>()),
            Some(vec!["SF".to_owned()])
        );
    }

    #[test]
    fn p140_ps_f8_feed_edges_are_labelled_feed() {
        // the run builds no label for a declared feed (its reservoir stands for no chain state), so the
        // route's first edge must not fall back to the generic "edge"
        let (l, t) = (1.0e-3, 2000.0);
        let mut graph = input(
            &["Mn54", "Fe54"],
            &["feed"],
            vec![single_step(
                t,
                vec![(0, 0, -l), (1, 0, l)],
                vec![(0, 0, 1.0e6)],
            )],
            vec![],
            &[("Fe54", 1.0e9)],
        );
        graph.labels.remove(&(0, 2));
        let report = analyse(&graph, &options_for("Fe54")).unwrap();
        let route = report.targets[0]
            .paths
            .iter()
            .find(|path| path.source == "feed")
            .expect("the route fed from the reservoir is reported");
        assert_eq!(route.edges, ["feed", "b-"]);
    }
}
