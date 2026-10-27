#![allow(non_snake_case)] // eV and K suffixes are part of the wire vocabulary.
//! Native damage energy from ENDF-6 neutron evaluations (P154): `actinv build-damage --method kinematic`.
//!
//! The damage-energy production cross section is computed channel by channel on the 0 K pointwise grid the
//! activation-library builder reconstructs (resonances included for MT=2 and MT=102), from the recoil spectrum of
//! each channel:
//!
//! * elastic (MT=2) and discrete two-body levels (MT=51-90, 600-648, 650-698, 700-748, 750-798, 800-848) from
//!   MF=4 or MF=6 `LAW=2` angular distributions and the reaction Q value;
//! * single-particle continuum (MT=91, 649, 699, 749, 799, 849, and the lumped MT=103-107 when no level MTs exist)
//!   from MF=6 `LAW=1` Kalbach-Mann or Legendre distributions: the recoil follows from momentum conservation
//!   for each ejectile energy and angle;
//! * capture (MT=102): the incident-neutron momentum plus the photon recoil.
//!
//! Recoil energy `T` becomes damage energy through the Lindhard partition in Robinson's fit
//! ([`crate::displacement::Partition`], NJOY HEATR `df`). Every other non-elastic channel with a cross section is
//! *uncovered*: its cross section and an upper bound on its damage energy are reported, never zero.
//!
//! # Quadrature and refinement
//!
//! At one incident energy the recoil energy is linear in the emission cosine, `T = c0 - c1 mu`, in every channel
//! (centre-of-mass or laboratory ejectile angle at fixed ejectile energy). The cosine integral therefore runs over
//! `[-1, mu_cut]` with `mu_cut` the exact cosine at which `T` falls to the displacement threshold, split at the
//! cosines where the damage energy reaches `E_d` and `2 E_d / 0.8` (the NRT breakpoints) and at tabulated angular
//! points, each piece integrated by Gauss-Legendre of order `max(8, NL/2 + 8)` (at most 40; 8 for Kalbach-Mann).
//! Ejectile-energy bins are integrated by three-point Gauss-Legendre. The incident-energy grid starts from the
//! channel's cross-section points, the group boundaries and the kinematic onset energies; each segment's midpoint
//! is then evaluated and the segment bisected while the lin-lin interpolant misses the midpoint by more than
//! [`REFINE_TOLERANCE`] of its magnitude (plus 1e-9 of the channel maximum), at most [`MAX_REFINE_PASSES`] passes.

use crate::activation::{parse_evaluations, Evaluation, Projectile};
use crate::builder::{
    builder_fingerprint, check_fispact_structure, damage_nuclide_name, discover_inputs,
    inspect_projectile, sha256_file, write_json_atomic,
};
use crate::composition;
use crate::displacement::{
    arc_dpa_entry, arc_displacements, nrt_displacements, Partition, ARC_DPA_CITATION,
    NEUTRON_REST_EV, NRT_KAPPA,
};
use crate::endf_distributions::{
    interpolate, parse_damage_endf, AngularDistribution, AngularRecord, DamageEndf, Law1,
    Law1Energy, Mf6Body, Mf6Particle,
};
use crate::groups::{GroupStructure, Tabulated};
use crate::processing::{has_resonance_contribution, process_reaction};
use crate::resonance::{validate_rmatrix_limited, RangeData};
use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::OnceLock;

pub const TABLE_FORMAT_2: &str = "actinv-damage-table-2";
pub const TABLE_UNITS_2: &str = "damage_energy_barn_eV_per_group";
/// Displacement energy used for an element with no `--displacement-energy` entry (eV).
pub const DEFAULT_DISPLACEMENT_ENERGY_EV: f64 = 25.0;
/// Relative midpoint tolerance of the incident-energy grid refinement.
pub const REFINE_TOLERANCE: f64 = 1.0e-3;
pub const MAX_REFINE_PASSES: usize = 12;
const MAX_GRID_POINTS: usize = 4_000_000;

// ---------------------------------------------------------------------------------------------------------
// Table-2 wire format (shared by the writer here and the reader in actinv-core)
// ---------------------------------------------------------------------------------------------------------

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Table2Source {
    pub citation: String,
    pub edition: String,
    pub url: String,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Table2File {
    pub path: String,
    pub sha256: String,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Table2ArcParameter {
    pub b: f64,
    pub c: f64,
    /// The displacement energy of the paper's fit, for comparison with the build value.
    pub reference_displacement_energy_eV: f64,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Table2Arc {
    pub citation: String,
    /// Element symbol -> constants; elements the paper does not cover are absent.
    pub parameters: BTreeMap<String, Table2ArcParameter>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Table2Displacement {
    /// Element symbol -> the displacement energy `E_d` (eV) the recoil integrals were built with.
    pub displacement_energy_eV: BTreeMap<String, f64>,
    pub arc_dpa: Table2Arc,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Table2Row {
    /// Total damage energy production (barn eV per group): `elastic + inelastic + disappearance`.
    pub damage_energy_barn_eV: Vec<f64>,
    pub elastic: Vec<f64>,
    /// MT=51-91.
    pub inelastic: Vec<f64>,
    /// MT=102-120 and 600-849.
    pub disappearance: Vec<f64>,
    pub uncovered_xs_barn: Vec<f64>,
    pub uncovered_damage_upper_barn_eV: Vec<f64>,
    /// Exact-NRT displacements times barn per group at the element's `E_d`.
    pub displacement_nrt_barn: Vec<f64>,
    /// arc-dpa displacements times barn per group; absent for elements without published constants.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub displacement_arc_barn: Option<Vec<f64>>,
    /// Covered and uncovered channels and the treatments used (informational).
    #[serde(default)]
    pub channels: serde_json::Value,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DamageTable2 {
    pub format: String,
    pub source: Table2Source,
    pub projectile: String,
    pub group_structure: String,
    pub boundaries_eV: Vec<f64>,
    pub units: String,
    #[serde(default)]
    pub temperature_K: Option<f64>,
    pub method: String,
    #[serde(default)]
    pub files: Vec<Table2File>,
    /// Evaluations with no damage row (no MT=2 cross section).
    #[serde(default)]
    pub uncovered: Vec<String>,
    pub displacement: Table2Displacement,
    #[serde(default)]
    pub model: serde_json::Value,
    pub targets: BTreeMap<String, Table2Row>,
}

// ---------------------------------------------------------------------------------------------------------
// Small numerical tools
// ---------------------------------------------------------------------------------------------------------

/// Gauss-Legendre nodes and weights on `[-1, 1]`, computed by Newton iteration and cached per order.
fn gauss_legendre(n: usize) -> &'static (Vec<f64>, Vec<f64>) {
    static CACHE: [OnceLock<(Vec<f64>, Vec<f64>)>; 65] = [const { OnceLock::new() }; 65];
    let n = n.clamp(1, 64);
    CACHE[n].get_or_init(|| {
        let mut nodes = Vec::with_capacity(n);
        let mut weights = Vec::with_capacity(n);
        for i in 0..n {
            let mut z = (std::f64::consts::PI * (i as f64 + 0.75) / (n as f64 + 0.5)).cos();
            let mut derivative = 1.0;
            for _ in 0..100 {
                let (mut p0, mut p1) = (1.0_f64, z);
                for k in 2..=n {
                    let kf = k as f64;
                    let p2 = ((2.0 * kf - 1.0) * z * p1 - (kf - 1.0) * p0) / kf;
                    p0 = p1;
                    p1 = p2;
                }
                derivative = n as f64 * (z * p1 - p0) / (z * z - 1.0);
                let step = p1 / derivative;
                z -= step;
                if step.abs() < 1e-15 {
                    break;
                }
            }
            nodes.push(z);
            weights.push(2.0 / ((1.0 - z * z) * derivative * derivative));
        }
        (nodes, weights)
    })
}

/// Response of one reaction event: damage energy and the two displacement counts.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct Resp {
    pub d: f64,
    pub nrt: f64,
    pub arc: f64,
}

impl std::ops::Add for Resp {
    type Output = Resp;
    fn add(self, other: Resp) -> Resp {
        Resp {
            d: self.d + other.d,
            nrt: self.nrt + other.nrt,
            arc: self.arc + other.arc,
        }
    }
}

impl std::ops::AddAssign for Resp {
    fn add_assign(&mut self, other: Resp) {
        *self = *self + other;
    }
}

impl std::ops::Mul<f64> for Resp {
    type Output = Resp;
    fn mul(self, factor: f64) -> Resp {
        Resp {
            d: self.d * factor,
            nrt: self.nrt * factor,
            arc: self.arc * factor,
        }
    }
}

fn blend_resp(law: i32, e0: f64, e1: f64, e: f64, low: Resp, high: Resp) -> Resp {
    Resp {
        d: interpolate(law, e0, e1, e, low.d, high.d),
        nrt: interpolate(law, e0, e1, e, low.nrt, high.nrt),
        arc: interpolate(law, e0, e1, e, low.arc, high.arc),
    }
}

/// Integral over `[-1, 1]`-mapped `[a, b]` of `f` by `n`-point Gauss-Legendre.
fn gl_integral<F: Fn(f64) -> Resp>(a: f64, b: f64, n: usize, f: F) -> Resp {
    let (nodes, weights) = gauss_legendre(n);
    let (half, middle) = (0.5 * (b - a), 0.5 * (b + a));
    let mut total = Resp::default();
    for (x, w) in nodes.iter().zip(weights) {
        total += f(middle + half * x) * (w * half);
    }
    total
}

/// `integral pdf(mu) * response(c0 - c1 mu) dmu` over the cosines with `T >= threshold`. `kinks` are recoil
/// energies at which the response is not smooth; `extra` are cosines at which the density is not smooth.
#[allow(clippy::too_many_arguments)]
fn linear_response<R, P>(
    response: &R,
    threshold: f64,
    kinks: &[f64],
    c0: f64,
    c1: f64,
    pdf: &P,
    nodes: usize,
    extra: &[f64],
) -> Resp
where
    R: Fn(f64) -> Resp,
    P: Fn(f64) -> f64,
{
    if !(c0 + c1 >= threshold && c0.is_finite() && c1.is_finite()) {
        return Resp::default();
    }
    if c1 <= 0.0 {
        return if c0 >= threshold {
            response(c0)
        } else {
            Resp::default()
        };
    }
    let hi = ((c0 - threshold) / c1).min(1.0);
    if hi <= -1.0 {
        return Resp::default();
    }
    let mut cuts = vec![-1.0, hi];
    for &t in kinks {
        if t.is_finite() {
            let mu = (c0 - t) / c1;
            if mu > -1.0 && mu < hi {
                cuts.push(mu);
            }
        }
    }
    cuts.extend(extra.iter().copied().filter(|mu| *mu > -1.0 && *mu < hi));
    cuts.sort_by(f64::total_cmp);
    cuts.dedup();
    let mut total = Resp::default();
    for piece in cuts.windows(2) {
        if piece[1] > piece[0] {
            total += gl_integral(piece[0], piece[1], nodes, |mu| {
                response(c0 - c1 * mu) * pdf(mu)
            });
        }
    }
    total
}

/// The recoil of one channel: partition, displacement energy and the recoil energies at which the damage energy
/// reaches `E_d` and `2 E_d / 0.8`.
#[derive(Clone, Copy, Debug)]
struct Recoil {
    partition: Partition,
    ed: f64,
    arc: Option<(f64, f64)>,
    t_on: f64,
    t_knee: f64,
}

impl Recoil {
    fn new(zr: f64, ar: f64, zl: f64, al: f64, ed: f64, arc: Option<(f64, f64)>) -> Self {
        let partition = Partition::new(zr, ar, zl, al, ed);
        Self {
            partition,
            ed,
            arc,
            t_on: partition.recoil_for_damage(ed).unwrap_or(f64::INFINITY),
            t_knee: partition
                .recoil_for_damage(2.0 * ed / NRT_KAPPA)
                .unwrap_or(f64::INFINITY),
        }
    }

    fn response(&self, t: f64) -> Resp {
        let d = self.partition.damage_energy(t);
        if d <= 0.0 {
            return Resp::default();
        }
        Resp {
            d,
            nrt: nrt_displacements(d, self.ed),
            arc: self
                .arc
                .map_or(0.0, |(b, c)| arc_displacements(d, self.ed, b, c)),
        }
    }

    fn integrate<P: Fn(f64) -> f64>(
        &self,
        c0: f64,
        c1: f64,
        pdf: &P,
        nodes: usize,
        extra: &[f64],
    ) -> Resp {
        linear_response(
            &|t| self.response(t),
            self.ed,
            &[self.t_on, self.t_knee],
            c0,
            c1,
            pdf,
            nodes,
            extra,
        )
    }
}

// ---------------------------------------------------------------------------------------------------------
// Kinematics
// ---------------------------------------------------------------------------------------------------------

/// Atomic masses over the neutron mass (the ENDF AWP convention) of the light ejectiles.
const NEUTRON_MASS_U: f64 = 1.008_664_915_95;

fn ejectile_mass(zap: i32) -> Option<f64> {
    let atomic_u = match zap {
        1 => return Some(1.0),
        1001 => 1.007_825_032_23,
        1002 => 2.014_101_778_12,
        1003 => 3.016_049_277_9,
        2003 => 3.016_029_322_65,
        2004 => 4.002_603_254_13,
        _ => return None,
    };
    Some(atomic_u / NEUTRON_MASS_U)
}

/// Ejectile ZAP of a one-ejectile MT: 51-91 neutron, then proton, deuteron, triton, helion, alpha by range.
fn ejectile_zap(mt: i32) -> Option<i32> {
    match mt {
        2 | 51..=91 => Some(1),
        103 | 600..=649 => Some(1001),
        104 | 650..=699 => Some(1002),
        105 | 700..=749 => Some(1003),
        106 | 750..=799 => Some(2003),
        107 | 800..=849 => Some(2004),
        _ => None,
    }
}

/// Centre-of-mass map `T = c0 - c1 mu` of the residual's laboratory recoil energy for a two-body reaction:
/// `mu` is the cosine of the ejectile in the centre of mass relative to the incident direction, `m_ej` and
/// `m_res` are the ejectile and residual masses in neutron masses (`m_ej + m_res = AWR + 1`) and `q` is the Q
/// value (eV). `None` below the reaction threshold.
fn two_body_cm_map(awr: f64, m_ej: f64, m_res: f64, q: f64, e: f64) -> Option<(f64, f64)> {
    let a1 = awr + 1.0;
    let e_c = awr * e / a1 + q;
    if !(e_c > 0.0 && e > 0.0) {
        return None;
    }
    let v2 = 2.0 * e / (a1 * a1);
    let u2 = 2.0 * m_ej * e_c / (m_res * a1);
    Some((0.5 * m_res * (v2 + u2), m_res * (v2 * u2).sqrt()))
}

/// Kalbach-Mann angular density with slope `a` and precompound fraction `r`.
fn km_pdf(a: f64, r: f64, mu: f64) -> f64 {
    if a.abs() < 1e-6 {
        0.5
    } else {
        a / (2.0 * a.sinh()) * ((a * mu).cosh() + r * (a * mu).sinh())
    }
}

/// Kalbach (1988) systematics for the slope `a(E, E')` of a neutron-induced reaction on a target with `z`
/// protons and `a_t` nucleons emitting the particle `ej_zap`; `e_ev` is the incident and `ep_ev` the outgoing
/// energy. A port of the formula NJOY ACER uses (`bachaa`), energies in MeV.
fn kalbach_a(z: i32, a_t: i32, ej_zap: i32, e_ev: f64, ep_ev: f64) -> f64 {
    let (e, ep) = (e_ev / 1.0e6, ep_ev / 1.0e6);
    let a_ej = f64::from(ej_zap % 1000);
    let z_ej = f64::from(ej_zap / 1000);
    let aa = f64::from(a_t);
    let za = f64::from(z);
    let ac = aa + 1.0;
    let zc = za;
    let ab = ac - a_ej;
    let zb = zc - z_ej;
    if ab <= 0.0 || aa <= 0.0 {
        return 0.0;
    }
    let (nb, nc) = (ab - zb, ac - zc);
    let separation = |a_x: f64, z_x: f64, n_x: f64| {
        15.68 * (ac - a_x)
            - 28.07 * ((nc - zc).powi(2) / ac - (n_x - z_x).powi(2) / a_x)
            - 18.56 * (ac.powf(2.0 / 3.0) - a_x.powf(2.0 / 3.0))
            + 33.22 * ((nc - zc).powi(2) / ac.powf(4.0 / 3.0) - (n_x - z_x).powi(2) / a_x.powf(4.0 / 3.0))
            - 0.717 * (zc * zc / ac.powf(1.0 / 3.0) - z_x * z_x / a_x.powf(1.0 / 3.0))
            + 1.211 * (zc * zc / ac - z_x * z_x / a_x)
    };
    // the incident particle is a neutron: no light-particle binding correction
    let sa = separation(aa, za, aa - za);
    let mut sb = separation(ab, zb, nb);
    match ej_zap {
        1002 => sb -= 2.22,
        1003 => sb -= 8.48,
        2003 => sb -= 7.72,
        2004 => sb -= 28.3,
        _ => {}
    }
    let ecm = aa * e / ac;
    let ea = ecm + sa;
    let eb = ep * ac / ab + sb;
    let x1 = if ea > 130.0 { 130.0 * eb / ea } else { eb };
    let x3 = if ea > 41.0 { 41.0 * eb / ea } else { eb };
    let fb = match ej_zap {
        1 => 0.5,
        2004 => 2.0,
        _ => 1.0,
    };
    0.04 * x1 + 1.8e-6 * x1.powi(3) + 6.7e-7 * fb * x3.powi(4)
}

// ---------------------------------------------------------------------------------------------------------
// Channel models
// ---------------------------------------------------------------------------------------------------------

struct TwoBody<'a> {
    awr: f64,
    m_ej: f64,
    m_res: f64,
    q: f64,
    /// Ejectile angles are in the laboratory frame.
    lab: bool,
    dist: &'a AngularDistribution,
    rc: Recoil,
    /// Evaluations where laboratory data could not be converted (double-valued kinematics) and the
    /// centre-of-mass reading was used instead, as NJOY does for heavy targets.
    cm_fallbacks: AtomicUsize,
}

impl TwoBody<'_> {
    fn kernel(&self, e: f64) -> Resp {
        let Some((c0, c1)) = two_body_cm_map(self.awr, self.m_ej, self.m_res, self.q, e) else {
            return Resp::default();
        };
        let at = self.dist.at(e);
        let nodes = (at.order() / 2 + 8).clamp(8, 40);
        let mut breaks = Vec::new();
        at.breakpoints(&mut breaks);
        if self.lab {
            // Laboratory ejectile cosine: the ejectile momentum solves a quadratic whose positive root is
            // unique when the ejectile is faster than the centre of mass (c < 0).
            let a1 = self.awr + 1.0;
            let p_in = (2.0 * e).sqrt();
            let c = 2.0 * self.m_ej * (e - self.m_res * (e + self.q));
            if c < 0.0 {
                let t_of = |mu: f64| {
                    let b = self.m_ej * p_in * mu;
                    let x = (b + (b * b - a1 * c).max(0.0).sqrt()) / a1;
                    (p_in * p_in + x * x - 2.0 * p_in * x * mu) / (2.0 * self.m_res)
                };
                breaks.sort_by(f64::total_cmp);
                let mut cuts = vec![-1.0];
                cuts.extend(breaks.iter().copied().filter(|mu| *mu > -1.0 && *mu < 1.0));
                cuts.push(1.0);
                cuts.dedup();
                let mut total = Resp::default();
                for piece in cuts.windows(2) {
                    total += gl_integral(piece[0], piece[1], nodes.max(24), |mu| {
                        self.rc.response(t_of(mu)) * at.pdf(mu)
                    });
                }
                return total;
            }
            self.cm_fallbacks.fetch_add(1, Ordering::Relaxed);
        }
        self.rc.integrate(c0, c1, &|mu| at.pdf(mu), nodes, &breaks)
    }
}

struct Continuum<'a> {
    awr: f64,
    z_t: i32,
    a_t: i32,
    ej_zap: i32,
    m_ej: f64,
    m_res: f64,
    law1: &'a Law1,
    lab: bool,
    rc: Recoil,
}

/// Records bracketing `e` and the law blending them (`None` when one record applies).
fn bracket(energies: &[f64], laws: &[i32], e: f64) -> (usize, Option<(usize, i32)>) {
    let n = energies.len();
    if n == 1 || e <= energies[0] {
        return (0, None);
    }
    if e >= energies[n - 1] {
        return (n - 1, None);
    }
    let index = energies.partition_point(|energy| *energy <= e) - 1;
    let law = laws.get(index).copied().unwrap_or(2);
    if law == 1 {
        (index, None)
    } else {
        (index, Some((index + 1, law)))
    }
}

const GL3_NODES: [f64; 3] = [-0.774_596_669_241_483_4, 0.0, 0.774_596_669_241_483_4];
const GL3_WEIGHTS: [f64; 3] = [
    0.555_555_555_555_555_6,
    0.888_888_888_888_888_9,
    0.555_555_555_555_555_6,
];

impl Continuum<'_> {
    fn kernel(&self, e: f64) -> Resp {
        let energies: Vec<f64> = self.law1.energies.iter().map(|r| r.energy).collect();
        let (lo, hi) = bracket(&energies, &self.law1.laws, e);
        let low = self.record(&self.law1.energies[lo], e);
        match hi {
            None => low,
            Some((index, law)) => {
                let high = self.record(&self.law1.energies[index], e);
                blend_resp(law, energies[lo], energies[index], e, low, high)
            }
        }
    }

    /// Recoil response of one ejectile-energy-angle point `(E', f0, angular parameters)`.
    fn point(&self, rec: &Law1Energy, i: usize, frac: f64, ep: f64, e: f64) -> Resp {
        let lerp = |a: f64, b: f64| a + frac * (b - a);
        let a1 = self.awr + 1.0;
        let (c0, c1) = if self.lab {
            (
                (e + self.m_ej * ep) / self.m_res,
                2.0 * (e * self.m_ej * ep).sqrt() / self.m_res,
            )
        } else {
            (
                e * self.m_res / (a1 * a1) + self.m_ej * ep / self.m_res,
                2.0 * (e * self.m_ej * ep).sqrt() / a1,
            )
        };
        let na = rec.na;
        let next = (i + 1).min(rec.eprime.len() - 1);
        let value = |k: usize| -> f64 {
            let first = rec.angular[i * na + k];
            if self.law1.lep == 2 {
                lerp(first, rec.angular[next * na + k])
            } else {
                first
            }
        };
        if self.law1.lang == 2 {
            let r = value(0);
            let a = if na >= 2 {
                value(1)
            } else {
                kalbach_a(self.z_t, self.a_t, self.ej_zap, e, ep)
            };
            self.rc.integrate(c0, c1, &|mu| km_pdf(a, r, mu), 8, &[])
        } else {
            let f0 = if self.law1.lep == 2 {
                lerp(rec.f0[i], rec.f0[next])
            } else {
                rec.f0[i]
            };
            if f0 <= 0.0 {
                return Resp::default();
            }
            let moments: Vec<f64> = (0..na).map(|k| value(k) / f0).collect();
            let record = AngularRecord::Legendre(moments);
            let nodes = (record.order() / 2 + 8).clamp(8, 40);
            self.rc.integrate(c0, c1, &|mu| record.pdf(mu), nodes, &[])
        }
    }

    /// Spectrum-averaged response of one incident-energy record at incident energy `e`.
    fn record(&self, rec: &Law1Energy, e: f64) -> Resp {
        let nep = rec.eprime.len();
        let mut norm = 0.0;
        let mut total = Resp::default();
        for i in 0..rec.nd.min(nep) {
            let weight = rec.f0[i];
            norm += weight;
            if weight > 0.0 {
                total += self.point(rec, i, 0.0, rec.eprime[i], e) * weight;
            }
        }
        for i in rec.nd..nep.saturating_sub(1) {
            let (x0, x1) = (rec.eprime[i], rec.eprime[i + 1]);
            let (f0, f1) = (rec.f0[i], rec.f0[i + 1]);
            if x1 <= x0 {
                continue;
            }
            let width = x1 - x0;
            norm += if self.law1.lep == 1 {
                f0 * width
            } else {
                0.5 * (f0 + f1) * width
            };
            if f0 <= 0.0 && f1 <= 0.0 {
                continue;
            }
            for (node, weight) in GL3_NODES.iter().zip(GL3_WEIGHTS) {
                let frac = 0.5 * (node + 1.0);
                let ep = x0 + frac * width;
                let f = if self.law1.lep == 1 {
                    f0
                } else {
                    f0 + frac * (f1 - f0)
                };
                if f > 0.0 {
                    total += self.point(rec, i, frac, ep, e) * (f * weight * 0.5 * width);
                }
            }
        }
        if norm > 0.0 {
            total * (1.0 / norm)
        } else {
            Resp::default()
        }
    }
}

struct Capture<'a> {
    awr: f64,
    q: Option<f64>,
    photons: Option<(&'a Law1, &'a Tabulated)>,
    rc: Recoil,
    /// NJOY HEATR `capdam`: one photon of the excitation energy, recoil `E/(A+1) + E_x^2 / (2 (A+1) m_n c^2)`
    /// with no photon-angle term (the cross term averages out), a single recoil energy per incident energy.
    njoy_one_photon: bool,
}

/// `E[E'^2]` of the normalized photon spectrum of one record (discrete lines plus continuum).
fn record_second_moment(rec: &Law1Energy, lep: i32) -> Option<f64> {
    let nep = rec.eprime.len();
    let (mut norm, mut moment) = (0.0, 0.0);
    for i in 0..rec.nd.min(nep) {
        norm += rec.f0[i];
        moment += rec.f0[i] * rec.eprime[i] * rec.eprime[i];
    }
    for i in rec.nd..nep.saturating_sub(1) {
        let (x0, x1) = (rec.eprime[i], rec.eprime[i + 1]);
        let (f0, f1) = (rec.f0[i], rec.f0[i + 1]);
        if x1 <= x0 {
            continue;
        }
        let width = x1 - x0;
        norm += if lep == 1 {
            f0 * width
        } else {
            0.5 * (f0 + f1) * width
        };
        for (node, weight) in GL3_NODES.iter().zip(GL3_WEIGHTS) {
            let frac = 0.5 * (node + 1.0);
            let ep = x0 + frac * width;
            let f = if lep == 1 { f0 } else { f0 + frac * (f1 - f0) };
            moment += f * ep * ep * weight * 0.5 * width;
        }
    }
    (norm > 0.0).then(|| moment / norm)
}

impl Capture<'_> {
    /// `E[sum of photon energies squared]` per capture (eV^2): the evaluated photon multiplicity times the
    /// second moment of its spectrum, else the excitation energy emitted as one photon.
    fn photon_energy_squared(&self, e: f64) -> f64 {
        if let Some((law1, yield_table)) = self.photons {
            let energies: Vec<f64> = law1.energies.iter().map(|r| r.energy).collect();
            let (lo, hi) = bracket(&energies, &law1.laws, e);
            let low = record_second_moment(&law1.energies[lo], law1.lep);
            let moment = match (low, hi) {
                (Some(low), None) => Some(low),
                (Some(low), Some((index, law))) => {
                    record_second_moment(&law1.energies[index], law1.lep)
                        .map(|high| interpolate(law, energies[lo], energies[index], e, low, high))
                }
                _ => None,
            };
            if let Some(moment) = moment {
                return yield_table.evaluate(e).unwrap_or(0.0) * moment;
            }
        }
        let a1 = self.awr + 1.0;
        let excitation = self.q.unwrap_or(0.0) + self.awr * e / a1;
        excitation * excitation
    }

    fn kernel(&self, e: f64) -> Resp {
        let a1 = self.awr + 1.0;
        let t0 = e / a1;
        let tg = self.photon_energy_squared(e) / (2.0 * a1 * NEUTRON_REST_EV);
        let c0 = t0 + tg;
        let c1 = if self.njoy_one_photon {
            0.0
        } else {
            2.0 * (t0 * tg).sqrt()
        };
        self.rc.integrate(c0, c1, &|_| 0.5, 8, &[])
    }
}

enum Model<'a> {
    TwoBody(TwoBody<'a>),
    Continuum(Continuum<'a>),
    Capture(Capture<'a>),
}

impl Model<'_> {
    fn kernel(&self, e: f64) -> Resp {
        match self {
            Self::TwoBody(model) => model.kernel(e),
            Self::Continuum(model) => model.kernel(e),
            Self::Capture(model) => model.kernel(e),
        }
    }

    /// Energies where the damage onset or reaction threshold lies, to seed the grid.
    fn onsets(&self, ed: f64) -> Vec<f64> {
        match self {
            Self::TwoBody(model) => {
                let a1 = model.awr + 1.0;
                let mut onsets = Vec::new();
                if model.q < 0.0 {
                    onsets.push(-model.q * a1 / model.awr);
                }
                if model.q == 0.0 && model.m_ej == 1.0 && (model.m_res - model.awr).abs() < 1e-9 {
                    // elastic: T_max = 4 A E / (A + 1)^2 reaches E_d
                    onsets.push(ed * a1 * a1 / (4.0 * model.awr));
                }
                onsets
            }
            _ => Vec::new(),
        }
    }
}

// ---------------------------------------------------------------------------------------------------------
// Pointwise tabulation with refinement
// ---------------------------------------------------------------------------------------------------------

struct Tabulation<const N: usize> {
    energies: Vec<f64>,
    values: Vec<[f64; N]>,
    /// Segments still above tolerance after the last pass.
    unresolved: usize,
}

fn midpoint(e0: f64, e1: f64) -> f64 {
    if e0 > 0.0 && e1 / e0 > 4.0 {
        (e0 * e1).sqrt()
    } else {
        0.5 * (e0 + e1)
    }
}

/// Evaluate `eval` on the channel's cross-section grid (plus group boundaries and `extra`) and bisect segments
/// whose midpoint departs from the lin-lin interpolant.
fn tabulate<const N: usize, F>(
    sigma: &Tabulated,
    groups: &GroupStructure,
    extra: &[f64],
    eval: F,
) -> Result<Tabulation<N>, String>
where
    F: Fn(f64) -> [f64; N] + Sync,
{
    let low = sigma.x[0];
    let high = sigma.x[sigma.x.len() - 1];
    let mut grid: Vec<f64> = sigma.x.iter().copied().filter(|e| *e > 0.0).collect();
    for pair in sigma.x.windows(2) {
        if pair[0] == pair[1] && pair[0] > 0.0 {
            grid.push(pair[0].next_down());
        }
    }
    grid.extend(
        groups
            .boundaries_ev
            .iter()
            .copied()
            .filter(|b| *b > low && *b < high),
    );
    grid.extend(extra.iter().copied().filter(|e| *e > low && *e < high));
    grid.sort_by(f64::total_cmp);
    grid.dedup_by(|a, b| a.to_bits() == b.to_bits());
    if grid.len() < 2 {
        return Err("cross-section grid has fewer than two positive points".into());
    }
    let values: Vec<[f64; N]> = grid.par_iter().map(|&e| eval(e)).collect();
    let mut floor = [0.0; N];
    for row in &values {
        for (slot, value) in floor.iter_mut().zip(row) {
            *slot = slot.max(value.abs());
        }
    }
    for slot in &mut floor {
        *slot *= 1e-9;
    }
    #[derive(Clone, Copy)]
    struct Segment<const N: usize> {
        e0: f64,
        v0: [f64; N],
        e1: f64,
        v1: [f64; N],
    }
    let mut frontier: Vec<Segment<N>> = grid
        .windows(2)
        .zip(values.windows(2))
        .map(|(e, v)| Segment {
            e0: e[0],
            v0: v[0],
            e1: e[1],
            v1: v[1],
        })
        .collect();
    let mut points: Vec<(f64, [f64; N])> = grid.iter().copied().zip(values).collect();
    for _ in 0..MAX_REFINE_PASSES {
        let mids: Vec<f64> = frontier.iter().map(|s| midpoint(s.e0, s.e1)).collect();
        let mid_values: Vec<[f64; N]> = mids.par_iter().map(|&e| eval(e)).collect();
        let mut next = Vec::new();
        let mut added = Vec::new();
        for ((segment, &m), vm) in frontier.iter().zip(&mids).zip(&mid_values) {
            if !(m > segment.e0 && m < segment.e1) {
                continue;
            }
            let fails = (0..N).any(|j| {
                let linear = 0.5 * (segment.v0[j] + segment.v1[j]);
                (vm[j] - linear).abs() > REFINE_TOLERANCE * vm[j].abs().max(linear.abs()) + floor[j]
            });
            if fails {
                added.push((m, *vm));
                next.push(Segment {
                    e0: segment.e0,
                    v0: segment.v0,
                    e1: m,
                    v1: *vm,
                });
                next.push(Segment {
                    e0: m,
                    v0: *vm,
                    e1: segment.e1,
                    v1: segment.v1,
                });
            }
        }
        if points.len() + added.len() > MAX_GRID_POINTS {
            return Err(format!(
                "damage-energy refinement would exceed the {MAX_GRID_POINTS}-point safety cap"
            ));
        }
        points.extend(added);
        frontier = next;
        if frontier.is_empty() {
            break;
        }
    }
    points.sort_by(|a, b| a.0.total_cmp(&b.0));
    let (energies, values) = points.into_iter().unzip();
    Ok(Tabulation {
        energies,
        values,
        unresolved: frontier.len(),
    })
}

/// Group values of component `j` of a tabulation with the builder's flat-lethargy flux weight.
fn collapse_component<const N: usize>(
    tabulation: &Tabulation<N>,
    j: usize,
    groups: &GroupStructure,
) -> Result<Vec<f64>, String> {
    let n = tabulation.energies.len();
    let table = Tabulated {
        interpolation: vec![(n, 2)],
        x: tabulation.energies.clone(),
        y: tabulation.values.iter().map(|row| row[j]).collect(),
    };
    let collapsed = groups.collapse(&table)?;
    let scale = collapsed.iter().fold(0.0_f64, |m, x| m.max(x.abs()));
    if collapsed.iter().any(|v| !v.is_finite() || *v < -1e-9 * scale) {
        return Err("collapsed damage quantity is nonfinite or negative".into());
    }
    Ok(collapsed.into_iter().map(|v| v.max(0.0)).collect())
}

// ---------------------------------------------------------------------------------------------------------
// Uncovered channels
// ---------------------------------------------------------------------------------------------------------

/// Nucleons carried away by the emitted particles of an MT, where the MT fixes them.
fn emitted_nucleons(mt: i32) -> Option<f64> {
    Some(match mt {
        4 | 50..=91 => 1.0,
        11 => 4.0,
        16 | 875..=891 => 2.0,
        17 => 3.0,
        22 => 5.0,
        23 => 13.0,
        24 => 6.0,
        25 => 7.0,
        28 => 2.0,
        29 => 9.0,
        30 => 10.0,
        32 => 3.0,
        33 | 34 => 4.0,
        35 => 11.0,
        36 => 12.0,
        37 => 4.0,
        41 => 3.0,
        42 => 4.0,
        44 => 3.0,
        45 => 6.0,
        103 | 600..=649 => 1.0,
        104 | 650..=699 => 2.0,
        105 | 700..=749 => 3.0,
        106 | 750..=799 => 3.0,
        107 | 800..=849 => 4.0,
        108 => 8.0,
        109 => 12.0,
        111 => 2.0,
        112 => 5.0,
        113 => 11.0,
        114 => 10.0,
        115 => 3.0,
        116 => 4.0,
        117 => 6.0,
        152 => 5.0,
        153 => 6.0,
        _ => return None,
    })
}

/// Upper bound (eV) on the damage energy of one event of a reaction with Q value `q`.
///
/// With the residual of mass `M_r` (neutron masses) and the other products of total mass `A + 1 - M_r`, the
/// residual's centre-of-mass kinetic energy cannot exceed `E_c' (A + 1 - M_r) / (A + 1)` (the others carry at
/// least that share, with equal and opposite momentum), and its laboratory speed at most the sum of its
/// centre-of-mass speed and the centre-of-mass drift: `T <= (sqrt(M_r E) / (A + 1) + sqrt(E_c' (A + 1 - M_r) /
/// (A + 1)))^2`, `E_c' = A E / (A + 1) + Q`. Damage energy never exceeds the recoil energy, so `T` also bounds
/// it. When the MT does not fix the emitted nucleons the bound is the total kinetic energy `E + Q`, which no
/// recoil can exceed.
fn damage_upper_bound(awr: f64, q: f64, emitted: Option<f64>, e: f64) -> f64 {
    let a1 = awr + 1.0;
    let total = (e + q).max(0.0);
    let Some(n) = emitted else {
        return total;
    };
    let m_res = a1 - n;
    let kinetic = awr * e / a1 + q;
    if m_res <= 0.0 || kinetic <= 0.0 {
        return 0.0;
    }
    let t = ((m_res * e).sqrt() / a1 + (kinetic * (a1 - m_res) / a1).sqrt()).powi(2);
    t.min(total)
}

// ---------------------------------------------------------------------------------------------------------
// One evaluation -> one table row
// ---------------------------------------------------------------------------------------------------------

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CapturePhotons {
    /// Evaluated MF=6 photon spectra where the evaluation carries them, else the excitation energy as one photon.
    Evaluated,
    /// Always the excitation energy emitted as one photon (NJOY HEATR's capture treatment).
    OnePhoton,
}

impl CapturePhotons {
    pub fn parse(value: &str) -> Result<Self, String> {
        match value {
            "evaluated" => Ok(Self::Evaluated),
            "one-photon" => Ok(Self::OnePhoton),
            _ => Err(format!(
                "unknown capture photon treatment '{value}'; expected evaluated or one-photon"
            )),
        }
    }

    fn name(self) -> &'static str {
        match self {
            Self::Evaluated => "evaluated",
            Self::OnePhoton => "one-photon",
        }
    }
}

#[derive(Clone, Debug)]
pub struct KinematicOptions {
    pub projectile: Option<Projectile>,
    pub groups: GroupStructure,
    pub temperature_K: f64,
    pub cache: Option<PathBuf>,
    /// Element symbol -> `E_d` (eV); elements absent use `default_displacement_energy_eV`.
    pub displacement_energy_eV: BTreeMap<String, f64>,
    pub default_displacement_energy_eV: f64,
    pub capture_photons: CapturePhotons,
    pub grid_density: f64,
}

/// Which table partial an MT belongs to (NJOY's MT=445/446/447 grouping).
#[derive(Clone, Copy, PartialEq, Eq)]
enum Partial {
    Elastic,
    Inelastic,
    Disappearance,
}

fn level_range(mt: i32) -> bool {
    matches!(
        mt,
        51..=90 | 600..=648 | 650..=698 | 700..=748 | 750..=798 | 800..=848
    )
}

fn continuum_mt(mt: i32) -> bool {
    matches!(mt, 91 | 649 | 699 | 749 | 799 | 849)
}

fn lumped_charged(mt: i32) -> bool {
    matches!(mt, 103..=107)
}

fn partial_of(mt: i32) -> Option<Partial> {
    match mt {
        2 => Some(Partial::Elastic),
        51..=91 => Some(Partial::Inelastic),
        102..=120 | 600..=849 => Some(Partial::Disappearance),
        _ => None,
    }
}

/// MTs that are redundant sums of others when their parts are present, or never a damage channel.
fn is_redundant(mt: i32, present: &BTreeSet<i32>) -> bool {
    let any = |range: std::ops::RangeInclusive<i32>| present.iter().any(|m| range.contains(m));
    match mt {
        1 | 3 | 27 | 101 => true,
        4 => any(50..=91),
        16 => any(875..=891),
        18 => [19, 20, 21, 38].iter().any(|m| present.contains(m)),
        103 => any(600..=649),
        104 => any(650..=699),
        105 => any(700..=749),
        106 => any(750..=799),
        107 => any(800..=849),
        _ => false,
    }
}

/// MTs considered by the damage method: elastic, the non-elastic reaction channels and discrete (n,2n) levels.
fn is_channel_mt(mt: i32) -> bool {
    matches!(mt, 2 | 4..=200 | 600..=849 | 875..=891)
}

struct Context {
    z: i32,
    a_nucleon: i32,
    awr: f64,
    ed: f64,
    arc: Option<(f64, f64)>,
}

fn light_ejectile_in_lab(lct: i32, ej_zap: i32) -> bool {
    lct == 1 || (lct == 3 && ej_zap % 1000 <= 4)
}

fn find_product(products: &[Mf6Particle], zap: i32, want_law: i32) -> Option<&Mf6Particle> {
    products
        .iter()
        .find(|product| product.zap == zap && product.law == want_law)
}

/// Build the channel model of one MT, or the reason the channel is not covered.
fn build_model<'a>(
    mt: i32,
    ctx: &Context,
    endf: &'a DamageEndf,
    capture_photons: CapturePhotons,
) -> Result<Model<'a>, String> {
    let awr = ctx.awr;
    let z = f64::from(ctx.z);
    let q_of = |mt: i32| endf.q.get(&mt).map(|q| q.qi_ev);
    if mt == 102 {
        let q = q_of(102);
        let photons = if capture_photons == CapturePhotons::Evaluated {
            endf.mf6
                .get(&102)
                .and_then(|data| data.as_ref().ok())
                .and_then(|data| {
                    data.products.iter().find_map(|product| match &product.body {
                        Mf6Body::Law1(law1) if product.zap == 0 && !law1.energies.is_empty() => {
                            Some((law1, &product.yield_table))
                        }
                        _ => None,
                    })
                })
        } else {
            None
        };
        if photons.is_none() && q.is_none() {
            return Err("no evaluated photon spectrum and no Q value for the excitation energy".into());
        }
        return Ok(Model::Capture(Capture {
            awr,
            q,
            photons,
            rc: Recoil::new(z, awr + 1.0, z, awr, ctx.ed, ctx.arc),
            njoy_one_photon: capture_photons == CapturePhotons::OnePhoton,
        }));
    }
    let ej_zap = ejectile_zap(mt).ok_or("no single-ejectile kinematics for this MT")?;
    let z_ej = f64::from(ej_zap / 1000);
    if continuum_mt(mt) || lumped_charged(mt) {
        let data = endf
            .mf6
            .get(&mt)
            .ok_or("no MF=6 distribution")?
            .as_ref()
            .map_err(|error| format!("MF=6 not readable: {error}"))?;
        let product = data
            .products
            .iter()
            .find(|product| product.zap == ej_zap && matches!(product.body, Mf6Body::Law1(_)))
            .ok_or("no MF=6 LAW=1 distribution for the ejectile")?;
        let Mf6Body::Law1(law1) = &product.body else {
            return Err("no MF=6 LAW=1 distribution for the ejectile".into());
        };
        if !matches!(law1.lang, 1 | 2) {
            return Err(format!("MF=6 LAW=1 LANG={} is not supported", law1.lang));
        }
        if law1.energies.is_empty()
            || law1.energies.iter().any(|rec| {
                rec.eprime.len() < 2
                    || (law1.lang == 2 && rec.na == 0)
                    || rec.angular.len() != rec.eprime.len() * rec.na
            })
        {
            return Err("MF=6 LAW=1 record is empty or lacks angular parameters".into());
        }
        if product.yield_table.y.iter().any(|y| (y - 1.0).abs() > 1e-6) {
            return Err("ejectile multiplicity differs from one".into());
        }
        let m_ej = if product.awp > 0.0 {
            product.awp
        } else {
            ejectile_mass(ej_zap).ok_or("unknown ejectile mass")?
        };
        let m_res = awr + 1.0 - m_ej;
        return Ok(Model::Continuum(Continuum {
            awr,
            z_t: ctx.z,
            a_t: ctx.a_nucleon,
            ej_zap,
            m_ej,
            m_res,
            law1,
            lab: light_ejectile_in_lab(data.lct, ej_zap),
            rc: Recoil::new(z - z_ej, m_res, z, awr, ctx.ed, ctx.arc),
        }));
    }
    // discrete two-body: MF=4 first, then MF=6 LAW=2
    let q = if mt == 2 {
        0.0
    } else {
        q_of(mt).ok_or("no MF=3 Q value")?
    };
    let (dist, lab, m_ej): (&AngularDistribution, bool, f64) = match endf.mf4.get(&mt) {
        Some(Ok(mf4)) => (
            &mf4.distribution,
            mf4.lct == 1,
            ejectile_mass(ej_zap).ok_or("unknown ejectile mass")?,
        ),
        Some(Err(error)) => return Err(format!("MF=4 not readable: {error}")),
        None => {
            let data = endf
                .mf6
                .get(&mt)
                .ok_or("no MF=4 or MF=6 angular distribution")?
                .as_ref()
                .map_err(|error| format!("MF=6 not readable: {error}"))?;
            let product = find_product(&data.products, ej_zap, 2)
                .ok_or("no MF=6 LAW=2 distribution for the ejectile")?;
            let Mf6Body::Law2(dist) = &product.body else {
                return Err("no MF=6 LAW=2 distribution for the ejectile".into());
            };
            let m_ej = if product.awp > 0.0 {
                product.awp
            } else {
                ejectile_mass(ej_zap).ok_or("unknown ejectile mass")?
            };
            (dist, light_ejectile_in_lab(data.lct, ej_zap), m_ej)
        }
    };
    let m_res = awr + 1.0 - m_ej;
    Ok(Model::TwoBody(TwoBody {
        awr,
        m_ej,
        m_res,
        q,
        lab,
        dist,
        rc: Recoil::new(z - z_ej, m_res, z, awr, ctx.ed, ctx.arc),
        cm_fallbacks: AtomicUsize::new(0),
    }))
}

/// Cross-section table of an MT on the 0 K pointwise grid: the reconstructed resonance sum for MT=2 and
/// MT=102 when the evaluation carries MF=2, else the MF=3 table as given.
fn sigma_table(
    evaluation: &Evaluation,
    mt: i32,
    use_resonance: bool,
    options: &KinematicOptions,
) -> Result<Option<Tabulated>, String> {
    let groups = &options.groups;
    if use_resonance && matches!(mt, 2 | 102) {
        let resonance = evaluation
            .resonance
            .as_ref()
            .expect("use_resonance implies resonance parameters");
        if has_resonance_contribution(resonance, mt) {
            let zero_background = (!evaluation.mf3.contains_key(&mt)).then(|| Tabulated {
                interpolation: vec![(2, 2)],
                x: vec![
                    groups.boundaries_ev[0],
                    groups.boundaries_ev[groups.groups()],
                ],
                y: vec![0.0, 0.0],
            });
            let background = evaluation
                .mf3
                .get(&mt)
                .or(zero_background.as_ref())
                .expect("an MF=3 or explicit zero background is present");
            let reaction = process_reaction(
                resonance,
                background,
                groups,
                mt,
                0.0,
                options.grid_density,
            )
            .map_err(|error| format!("MT{mt} resonance processing: {error}"))?;
            return Ok(Some(reaction.table));
        }
    }
    Ok(evaluation.mf3.get(&mt).cloned())
}

/// Per-group arrays of one evaluation plus a note on every channel.
fn compute_row(
    evaluation: &Evaluation,
    endf: &DamageEndf,
    options: &KinematicOptions,
    ed: f64,
    arc: Option<(f64, f64)>,
) -> Result<Option<Table2Row>, String> {
    let metadata = &evaluation.metadata;
    let groups = &options.groups;
    let ng = groups.groups();
    let name = damage_nuclide_name(metadata.za, metadata.liso);
    let use_resonance = evaluation.resonance.is_some() && metadata.evaluation_temperature_k == 0.0;
    if use_resonance {
        if options.temperature_K != 0.0 {
            return Err(format!(
                "{name}: kinematic damage is computed on the 0 K reconstruction; --temperature-K must be 0"
            ));
        }
        if let Some(resonance) = &evaluation.resonance {
            for isotope in &resonance.isotopes {
                for range in &isotope.ranges {
                    if matches!(range.data, RangeData::RMatrixLimited(_)) {
                        validate_rmatrix_limited(range)
                            .map_err(|error| format!("{name}: RML validation: {error}"))?;
                    }
                }
            }
        }
    } else if metadata.evaluation_temperature_k.to_bits() != options.temperature_K.to_bits() {
        return Err(format!(
            "{name}: evaluation is at {} K but {} K was requested and no raw MF=2 reconstruction applies",
            metadata.evaluation_temperature_k, options.temperature_K
        ));
    }
    let present: BTreeSet<i32> = evaluation.mf3.keys().copied().collect();
    if !present.contains(&2) {
        return Ok(None);
    }
    let ctx = Context {
        z: metadata.za / 1000,
        a_nucleon: metadata.za % 1000,
        awr: metadata.awr,
        ed,
        arc,
    };
    let zeros = || vec![0.0; ng];
    let (mut elastic, mut inelastic, mut disappearance) = (zeros(), zeros(), zeros());
    let (mut nrt, mut arc_values) = (zeros(), zeros());
    let (mut uncovered_xs, mut uncovered_bound) = (zeros(), zeros());
    let mut covered_notes = Vec::new();
    let mut uncovered_notes = Vec::new();
    let mut photon_mode = "not_applicable".to_string();
    let mut lab_fallbacks = 0usize;
    let mut unresolved_segments = 0usize;

    for &mt in &present {
        if !is_channel_mt(mt) || is_redundant(mt, &present) {
            continue;
        }
        let Some(sigma) = sigma_table(evaluation, mt, use_resonance, options)? else {
            continue;
        };
        if sigma.y.iter().all(|value| *value == 0.0) {
            continue;
        }
        let covered = if mt == 2
            || mt == 102
            || level_range(mt)
            || continuum_mt(mt)
            || lumped_charged(mt)
        {
            Some(build_model(mt, &ctx, endf, options.capture_photons))
        } else {
            None
        };
        match covered {
            Some(Ok(model)) => {
                let onsets = model.onsets(ed);
                let tabulation: Tabulation<3> = tabulate(&sigma, groups, &onsets, |e| {
                    let sigma_e = sigma.evaluate(e).unwrap_or(0.0);
                    let response = model.kernel(e);
                    [sigma_e * response.d, sigma_e * response.nrt, sigma_e * response.arc]
                })
                .map_err(|error| format!("{name} MT{mt}: {error}"))?;
                unresolved_segments += tabulation.unresolved;
                let damage = collapse_component(&tabulation, 0, groups)
                    .map_err(|error| format!("{name} MT{mt}: {error}"))?;
                let displaced = collapse_component(&tabulation, 1, groups)
                    .map_err(|error| format!("{name} MT{mt}: {error}"))?;
                let arc_displaced = collapse_component(&tabulation, 2, groups)
                    .map_err(|error| format!("{name} MT{mt}: {error}"))?;
                let target = match partial_of(mt) {
                    Some(Partial::Elastic) => &mut elastic,
                    Some(Partial::Inelastic) => &mut inelastic,
                    _ => &mut disappearance,
                };
                for g in 0..ng {
                    target[g] += damage[g];
                    nrt[g] += displaced[g];
                    arc_values[g] += arc_displaced[g];
                }
                let mut note = serde_json::json!({
                    "mt": mt,
                    "kind": match &model {
                        Model::TwoBody(_) if mt == 2 => "elastic",
                        Model::TwoBody(_) => "discrete_two_body",
                        Model::Continuum(_) => "single_particle_continuum",
                        Model::Capture(_) => "capture",
                    },
                });
                match &model {
                    Model::Capture(capture) => {
                        photon_mode = if capture.photons.is_some() {
                            "evaluated_photon_spectrum"
                        } else {
                            "excitation_energy_one_photon_upper_bound"
                        }
                        .to_string();
                        note["photon_recoil"] = photon_mode.clone().into();
                    }
                    Model::TwoBody(two_body) => {
                        let count = two_body.cm_fallbacks.load(Ordering::Relaxed);
                        if count > 0 {
                            lab_fallbacks += count;
                            note["lab_frame_cm_fallback_evaluations"] = count.into();
                        }
                    }
                    Model::Continuum(_) => {}
                }
                covered_notes.push(note);
            }
            other => {
                let reason = match other {
                    Some(Err(reason)) => reason,
                    _ => "multi-particle or unspecified emission, or no distribution data".to_string(),
                };
                let q = endf.q.get(&mt).map_or(0.0, |q| q.qi_ev);
                let emitted = emitted_nucleons(mt);
                let tabulation: Tabulation<2> = tabulate(&sigma, groups, &[], |e| {
                    let sigma_e = sigma.evaluate(e).unwrap_or(0.0);
                    [sigma_e, sigma_e * damage_upper_bound(ctx.awr, q, emitted, e)]
                })
                .map_err(|error| format!("{name} MT{mt}: {error}"))?;
                let cross_section = collapse_component(&tabulation, 0, groups)
                    .map_err(|error| format!("{name} MT{mt}: {error}"))?;
                let bound = collapse_component(&tabulation, 1, groups)
                    .map_err(|error| format!("{name} MT{mt}: {error}"))?;
                for g in 0..ng {
                    uncovered_xs[g] += cross_section[g];
                    uncovered_bound[g] += bound[g];
                }
                uncovered_notes.push(serde_json::json!({
                    "mt": mt,
                    "reason": reason,
                    "bound": if emitted.is_some() {
                        "residual-mass kinematic maximum recoil energy"
                    } else {
                        "total kinetic energy E + Q"
                    },
                }));
            }
        }
    }
    if covered_notes
        .iter()
        .all(|note| note["mt"].as_i64() != Some(2))
    {
        // no elastic damage could be computed: the target has no row
        return Ok(None);
    }
    let total: Vec<f64> = (0..ng)
        .map(|g| elastic[g] + inelastic[g] + disappearance[g])
        .collect();
    Ok(Some(Table2Row {
        damage_energy_barn_eV: total,
        elastic,
        inelastic,
        disappearance,
        uncovered_xs_barn: uncovered_xs,
        uncovered_damage_upper_barn_eV: uncovered_bound,
        displacement_nrt_barn: nrt,
        displacement_arc_barn: arc.map(|_| arc_values),
        channels: serde_json::json!({
            "covered": covered_notes,
            "uncovered": uncovered_notes,
            "capture_photon_recoil": photon_mode,
            "lab_frame_cm_fallback_evaluations": lab_fallbacks,
            "unresolved_refinement_segments": unresolved_segments,
        }),
    }))
}

// ---------------------------------------------------------------------------------------------------------
// Table build
// ---------------------------------------------------------------------------------------------------------

#[derive(Debug)]
pub struct KinematicBuildSummary {
    pub targets: usize,
    pub uncovered_evaluations: usize,
    pub cache_hits: usize,
    pub projectile: Projectile,
    pub output: PathBuf,
    pub sha256: String,
}

#[derive(Debug, Serialize, Deserialize)]
struct KinematicCacheEntry {
    targets: BTreeMap<String, Table2Row>,
    uncovered: Vec<String>,
}

fn kinematic_cache_key(source_sha256: &str, options: &KinematicOptions, projectile: Projectile) -> String {
    static CODE: OnceLock<String> = OnceLock::new();
    let code = CODE.get_or_init(|| {
        let mut hash = Sha256::new();
        hash.update(builder_fingerprint().as_bytes());
        hash.update(include_bytes!("damage_kinematic.rs"));
        hash.update(include_bytes!("displacement.rs"));
        hash.update(include_bytes!("endf_distributions.rs"));
        format!("{:x}", hash.finalize())
    });
    let mut hasher = Sha256::new();
    hasher.update(b"ACTINV-DAMAGE-KINEMATIC-v1\0");
    hasher.update(source_sha256.as_bytes());
    hasher.update(options.temperature_K.to_bits().to_le_bytes());
    hasher.update(options.groups.hash().as_bytes());
    hasher.update(projectile.name().as_bytes());
    hasher.update([0]);
    hasher.update(options.default_displacement_energy_eV.to_bits().to_le_bytes());
    for (symbol, ed) in &options.displacement_energy_eV {
        hasher.update(symbol.as_bytes());
        hasher.update([0]);
        hasher.update(ed.to_bits().to_le_bytes());
    }
    hasher.update(options.capture_photons.name().as_bytes());
    hasher.update(options.grid_density.to_bits().to_le_bytes());
    hasher.update(code.as_bytes());
    format!("{:x}", hasher.finalize())
}

/// The displacement energy of an element: the explicit entry, else the default.
pub fn displacement_energy_for(options: &KinematicOptions, symbol: &str) -> f64 {
    options
        .displacement_energy_eV
        .get(symbol)
        .copied()
        .unwrap_or(options.default_displacement_energy_eV)
}

fn kinematic_source(
    path: &Path,
    options: &KinematicOptions,
    projectile: Projectile,
) -> Result<(KinematicCacheEntry, Table2File, bool), String> {
    let before = sha256_file(path)?;
    let filename = path
        .file_name()
        .and_then(|name| name.to_str())
        .ok_or_else(|| format!("input filename '{}' is not UTF-8", path.display()))?
        .to_owned();
    let provenance = Table2File {
        path: filename,
        sha256: before.clone(),
    };
    let checkpoint = options.cache.as_ref().map(|cache| {
        cache.join(format!(
            "{}.json",
            kinematic_cache_key(&before, options, projectile)
        ))
    });
    if let Some(checkpoint) = &checkpoint {
        if checkpoint.exists() {
            let entry: KinematicCacheEntry = serde_json::from_str(
                &std::fs::read_to_string(checkpoint).map_err(|error| {
                    format!(
                        "cannot read damage checkpoint {}: {error}",
                        checkpoint.display()
                    )
                })?,
            )
            .map_err(|error| {
                format!(
                    "cannot parse damage checkpoint {}: {error}",
                    checkpoint.display()
                )
            })?;
            if sha256_file(path)? != before {
                return Err(format!(
                    "source {} changed while its damage checkpoint was validated",
                    path.display()
                ));
            }
            return Ok((entry, provenance, true));
        }
    }
    let text = std::fs::read_to_string(path)
        .map_err(|error| format!("cannot read {} as ENDF text: {error}", path.display()))?;
    let evaluations = parse_evaluations(&text, Some(projectile))
        .map_err(|error| format!("{}: {error}", path.display()))?;
    let distributions =
        parse_damage_endf(&text).map_err(|error| format!("{}: {error}", path.display()))?;
    let mut entry = KinematicCacheEntry {
        targets: BTreeMap::new(),
        uncovered: Vec::new(),
    };
    for evaluation in &evaluations {
        let metadata = &evaluation.metadata;
        let name = damage_nuclide_name(metadata.za, metadata.liso);
        let symbol = composition::symbol_of(metadata.za / 1000);
        let ed = displacement_energy_for(options, symbol);
        let arc = arc_dpa_entry(symbol).map(|entry| (entry.b, entry.c));
        let empty = DamageEndf::default();
        let endf = distributions.get(&metadata.mat).unwrap_or(&empty);
        match compute_row(evaluation, endf, options, ed, arc)
            .map_err(|error| format!("{}: {error}", path.display()))?
        {
            Some(row) => {
                if entry.targets.insert(name.clone(), row).is_some() {
                    return Err(format!(
                        "{} declares two evaluations for {name}",
                        path.display()
                    ));
                }
            }
            None => entry.uncovered.push(name),
        }
    }
    if let Some(checkpoint) = &checkpoint {
        write_json_atomic(checkpoint, &entry)?;
    }
    Ok((entry, provenance, false))
}

/// Build and atomically publish an `actinv-damage-table-2` from a neutron evaluation directory or file.
pub fn build_damage_kinematic(
    input: impl AsRef<Path>,
    output: impl AsRef<Path>,
    options: &KinematicOptions,
) -> Result<KinematicBuildSummary, String> {
    let input = input.as_ref();
    let output = output.as_ref();
    options.groups.validate()?;
    if !options.temperature_K.is_finite() || options.temperature_K < 0.0 {
        return Err("damage temperature must be finite and nonnegative".into());
    }
    if !options.grid_density.is_finite()
        || options.grid_density <= 0.0
        || options.grid_density > crate::processing::MAX_GRID_DENSITY
    {
        return Err(format!(
            "grid density must be finite, positive and at most {}",
            crate::processing::MAX_GRID_DENSITY
        ));
    }
    let defaults_ok = |ed: f64| ed.is_finite() && ed > 0.0;
    if !defaults_ok(options.default_displacement_energy_eV)
        || options
            .displacement_energy_eV
            .iter()
            .any(|(symbol, ed)| composition::z_of(symbol).is_none() || !defaults_ok(*ed))
    {
        return Err(
            "displacement energies must be positive and keyed by element symbol".to_string(),
        );
    }
    let files = discover_inputs(input, Some(output))?;
    if let Some(cache) = &options.cache {
        if input.is_dir() && cache.starts_with(input) {
            return Err("damage checkpoint cache must be outside the input directory".into());
        }
        std::fs::create_dir_all(cache).map_err(|error| {
            format!(
                "cannot create damage checkpoint cache {}: {error}",
                cache.display()
            )
        })?;
    }
    let projectile = match options.projectile {
        Some(projectile) => projectile,
        None => inspect_projectile(input)?,
    };
    if !projectile.is_neutron() {
        return Err(format!(
            "--method kinematic is available for neutron evaluations only, not {}",
            projectile.name()
        ));
    }
    check_fispact_structure(&options.groups, projectile, "damage tables")?;
    let mut targets: BTreeMap<String, Table2Row> = BTreeMap::new();
    let mut target_files: BTreeMap<String, String> = BTreeMap::new();
    let mut provenance = Vec::new();
    let mut uncovered = Vec::new();
    let mut cache_hits = 0usize;
    for path in &files {
        let (entry, file_provenance, hit) = kinematic_source(path, options, projectile)
            .map_err(|error| format!("{}: {error}", path.display()))?;
        cache_hits += usize::from(hit);
        provenance.push(file_provenance);
        for (name, row) in entry.targets {
            if let Some(previous) = target_files.get(&name) {
                return Err(format!(
                    "duplicate damage target {name} in '{previous}' and '{}'",
                    path.display()
                ));
            }
            target_files.insert(name.clone(), path.display().to_string());
            targets.insert(name, row);
        }
        uncovered.extend(entry.uncovered);
    }
    let mut displacement_energy_eV = BTreeMap::new();
    let mut parameters = BTreeMap::new();
    for name in targets.keys() {
        let symbol: String = name.chars().take_while(|c| c.is_ascii_alphabetic()).collect();
        displacement_energy_eV.insert(symbol.clone(), displacement_energy_for(options, &symbol));
        if let Some(entry) = arc_dpa_entry(&symbol) {
            parameters.insert(
                symbol,
                Table2ArcParameter {
                    b: entry.b,
                    c: entry.c,
                    reference_displacement_energy_eV: entry.ed_eV,
                },
            );
        }
    }
    let input_label = input
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or("evaluations")
        .to_owned();
    let table = DamageTable2 {
        format: TABLE_FORMAT_2.into(),
        source: Table2Source {
            citation: "ENDF-6 neutron evaluations: damage energy and displacement cross sections computed channel by channel from the evaluated cross sections, angular and energy-angle distributions by actinv build-damage --method kinematic (Lindhard partition in Robinson's fit; exact NRT; arc-dpa)".into(),
            edition: input_label,
            url: input.display().to_string(),
        },
        projectile: projectile.name().into(),
        group_structure: options.groups.name.clone(),
        boundaries_eV: options.groups.boundaries_ev.clone(),
        units: TABLE_UNITS_2.into(),
        temperature_K: Some(options.temperature_K),
        method: "kinematic".into(),
        files: provenance,
        uncovered,
        displacement: Table2Displacement {
            displacement_energy_eV,
            arc_dpa: Table2Arc {
                citation: ARC_DPA_CITATION.into(),
                parameters,
            },
        },
        model: serde_json::json!({
            "partition": "Lindhard partition in Robinson's fit as NJOY HEATR df; zero for recoil energy below E_d",
            "lattice": "the target nuclide (Z, AWR); recoil Z and A are those of the residual nucleus",
            "nrt": "0 below E_d, 1 up to 2 E_d / 0.8, 0.8 T_d / (2 E_d) above",
            "arc_dpa": "N_NRT * xi, xi = (1 - c) / (2 E_d / 0.8)^b * T_d^b + c for T_d >= 2 E_d / 0.8",
            "capture_photons": options.capture_photons.name(),
            "default_displacement_energy_eV": options.default_displacement_energy_eV,
            "refine_tolerance": REFINE_TOLERANCE,
            "max_refine_passes": MAX_REFINE_PASSES,
            "units": {
                "damage_energy_barn_eV": "barn eV per group",
                "displacement_nrt_barn": "displacements times barn per group",
                "uncovered_xs_barn": "barn per group",
            },
        }),
        targets,
    };
    write_json_atomic(output, &table)?;
    let sha256 = sha256_file(output)?;
    Ok(KinematicBuildSummary {
        targets: table.targets.len(),
        uncovered_evaluations: table.uncovered.len(),
        cache_hits,
        projectile,
        output: output.to_path_buf(),
        sha256,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::endf_distributions::test_support::{num, packed, record};

    #[test]
    fn gauss_legendre_integrates_polynomials_exactly() {
        for n in [3usize, 8, 16, 40] {
            let (x, w) = gauss_legendre(n);
            let sum: f64 = w.iter().sum();
            assert!((sum - 2.0).abs() < 1e-13, "n={n}");
            // exact to degree 2n - 1: integral of x^4 over [-1, 1] is 0.4
            if 2 * n - 1 >= 4 {
                let fourth: f64 = x.iter().zip(w).map(|(x, w)| w * x.powi(4)).sum();
                assert!((fourth - 0.4).abs() < 1e-13, "n={n}");
            }
        }
    }

    #[test]
    fn elastic_recoil_kinematics() {
        let awr = 55.45;
        let e = 1.0e6;
        let (c0, c1) = two_body_cm_map(awr, 1.0, awr, 0.0, e).unwrap();
        // T = 2 A E / (A + 1)^2 (1 - mu)
        let expected = 2.0 * awr * e / (awr + 1.0).powi(2);
        assert!((c0 - expected).abs() < 1e-9 * expected);
        assert!((c1 - expected).abs() < 1e-9 * expected);
        assert!((c0 + c1 - 4.0 * awr * e / (awr + 1.0).powi(2)).abs() < 1e-6);
        assert!((c0 - c1).abs() < 1e-6);
    }

    #[test]
    fn inelastic_kinematics_with_q() {
        let awr = 55.45;
        let q = -8.46778e5;
        let threshold = -q * (awr + 1.0) / awr;
        assert!(two_body_cm_map(awr, 1.0, awr, q, threshold * 0.999).is_none());
        // just above threshold the ejectile is at rest in the centre of mass: T = M_r E / (A + 1)^2
        let e = threshold * 1.000001;
        let (c0, c1) = two_body_cm_map(awr, 1.0, awr, q, e).unwrap();
        let drift = awr * e / (awr + 1.0).powi(2);
        assert!((c0 - drift).abs() < 1e-3 * drift);
        assert!(c1 < 0.01 * c0);
        // energy conservation on average over an isotropic distribution: the mean recoil energy c0 plus
        // the mean ejectile laboratory energy equals E + Q
        let e = 5.0e6;
        let (c0, _) = two_body_cm_map(awr, 1.0, awr, q, e).unwrap();
        let a1 = awr + 1.0;
        let e_c = awr * e / a1 + q;
        // <E_ej> = m (V^2 + s^2) / 2 with s^2 = 2 M_r E_c' / (m (A + 1)), m = 1
        let s2 = 2.0 * awr * e_c / a1;
        let v2 = 2.0 * e / (a1 * a1);
        let mean_ej = 0.5 * (v2 + s2);
        assert!((c0 + mean_ej - (e + q)).abs() < 1e-6 * (e + q));
    }

    #[test]
    fn isotropic_elastic_with_a_constant_partition_matches_the_closed_form() {
        let awr = 55.45;
        let e = 2.0e6;
        let (c0, c1) = two_body_cm_map(awr, 1.0, awr, 0.0, e).unwrap();
        let identity = |t: f64| Resp {
            d: t,
            nrt: 0.0,
            arc: 0.0,
        };
        let pdf = |_: f64| 0.5;
        // no threshold: the mean recoil energy is c0
        let full = linear_response(&identity, 0.0, &[], c0, c1, &pdf, 8, &[]);
        assert!((full.d - c0).abs() < 1e-9 * c0);
        // threshold t0: integral over mu <= (c0 - t0)/c1 of (c0 - c1 mu)/2
        let t0 = 0.3 * (c0 + c1);
        let cut = (c0 - t0) / c1;
        let expected = 0.5 * (c0 * (cut + 1.0) - c1 * (cut * cut - 1.0) / 2.0);
        let cut_response = linear_response(&identity, t0, &[], c0, c1, &pdf, 8, &[]);
        assert!((cut_response.d - expected).abs() < 1e-9 * expected);
        // a kink inside the range does not change a smooth integrand
        let kinked = linear_response(&identity, t0, &[0.6 * (c0 + c1)], c0, c1, &pdf, 8, &[]);
        assert!((kinked.d - expected).abs() < 1e-9 * expected);
        // above the maximum recoil energy nothing is produced
        let none = linear_response(&identity, 1.1 * (c0 + c1), &[], c0, c1, &pdf, 8, &[]);
        assert_eq!(none, Resp::default());
    }

    #[test]
    fn kalbach_density_is_normalized_and_a_is_sensible() {
        for (a, r) in [(0.0, 0.0), (1e-9, 0.3), (0.5, 0.0), (2.0, 0.4), (6.0, 1.0)] {
            let n = 4000;
            let mut sum = 0.0;
            for i in 0..n {
                let mu = -1.0 + (i as f64 + 0.5) * 2.0 / n as f64;
                sum += km_pdf(a, r, mu) * 2.0 / n as f64;
            }
            assert!((sum - 1.0).abs() < 1e-5, "a={a} r={r}: {sum}");
        }
        // forward peaking grows with the outgoing energy
        let low = kalbach_a(26, 56, 1, 14.0e6, 1.0e6);
        let high = kalbach_a(26, 56, 1, 14.0e6, 8.0e6);
        assert!(low.is_finite() && high.is_finite());
        assert!(high > low && low > 0.0, "{low} {high}");
        // r = 0 symmetrical density about mu = 0 is even
        assert!((km_pdf(3.0, 0.0, 0.4) - km_pdf(3.0, 0.0, -0.4)).abs() < 1e-14);
    }

    #[test]
    fn continuum_kernel_matches_a_brute_force_integral() {
        // one histogram bin of ejectile energies, Kalbach-Mann with given a and r
        let law1 = Law1 {
            lang: 2,
            lep: 1,
            energies: vec![Law1Energy {
                energy: 10.0e6,
                nd: 0,
                na: 2,
                eprime: vec![1.0e6, 3.0e6, 5.0e6],
                f0: vec![2.0e-7, 1.0e-7, 0.0],
                angular: vec![0.2, 1.5, 0.4, 2.5, 0.0, 0.0],
            }],
            laws: vec![],
        };
        let awr = 55.45;
        let ed = 40.0;
        let m_res = awr;
        let rc = Recoil::new(26.0, m_res, 26.0, awr, ed, None);
        let model = Continuum {
            awr,
            z_t: 26,
            a_t: 56,
            ej_zap: 1,
            m_ej: 1.0,
            m_res,
            law1: &law1,
            lab: true,
            rc,
        };
        let e = 10.0e6;
        let got = model.kernel(e);
        // brute force: histogram bin 1 has f = 2e-7 over [1, 3] MeV, bin 2 has 1e-7 over [3, 5] MeV;
        // normalization = 2e-7 * 2e6 + 1e-7 * 2e6 = 0.6
        let mut sum = 0.0;
        let n_e = 400;
        let n_mu = 2000;
        for (lo, hi, f, r, a) in [(1.0e6, 3.0e6, 2.0e-7, 0.2, 1.5), (3.0e6, 5.0e6, 1.0e-7, 0.4, 2.5)] {
            for i in 0..n_e {
                let ep = lo + (i as f64 + 0.5) * (hi - lo) / n_e as f64;
                let mut inner = 0.0;
                for k in 0..n_mu {
                    let mu = -1.0 + (k as f64 + 0.5) * 2.0 / n_mu as f64;
                    let t = (e + ep - 2.0 * (e * ep).sqrt() * mu) / m_res;
                    inner += km_pdf(a, r, mu) * rc.partition.damage_energy(t) * 2.0 / n_mu as f64;
                }
                sum += f * inner * (hi - lo) / n_e as f64;
            }
        }
        let expected = sum / 0.6;
        assert!(
            (got.d - expected).abs() < 2e-3 * expected,
            "{} vs {expected}",
            got.d
        );
        assert!(got.nrt > 0.0);
    }

    #[test]
    fn centre_of_mass_continuum_reduces_to_the_two_body_map_for_a_line() {
        // a CM-frame discrete ejectile energy (single bin) must give the same c0, c1 as the two-body map
        let awr = 55.45;
        let e = 5.0e6;
        let q = -8.46778e5;
        let e_c = awr * e / (awr + 1.0) + q;
        // ejectile CM energy: E'_cm = M_r E_c' / (A + 1)
        let ep_cm = awr * e_c / (awr + 1.0);
        let (c0, c1) = two_body_cm_map(awr, 1.0, awr, q, e).unwrap();
        let a1 = awr + 1.0;
        let c0_cont = e * awr / (a1 * a1) + ep_cm / awr;
        let c1_cont = 2.0 * (e * ep_cm).sqrt() / a1;
        assert!((c0 - c0_cont).abs() < 1e-9 * c0, "{c0} {c0_cont}");
        assert!((c1 - c1_cont).abs() < 1e-9 * c1, "{c1} {c1_cont}");
    }

    #[test]
    fn upper_bound_dominates_the_actual_recoil() {
        let awr = 55.45;
        // (n,2n) with Q = -11.2 MeV at 14 MeV: the bound is above the two-neutron isotropic maximum
        let e = 14.0e6;
        let q = -11.2e6;
        let bound = damage_upper_bound(awr, q, Some(2.0), e);
        assert!(bound > 0.0 && bound <= e + q);
        // the residual of mass A - 1 cannot exceed the bound even in the most favourable emission
        let m_res = awr - 1.0;
        let a1 = awr + 1.0;
        let kinetic = awr * e / a1 + q;
        let tmax = ((m_res * e).sqrt() / a1 + (kinetic * 2.0 / a1).sqrt()).powi(2);
        assert!((bound - tmax.min(e + q)).abs() < 1e-6);
        // unknown emission: total kinetic energy
        assert_eq!(damage_upper_bound(awr, -1.0e6, None, 2.0e6), 1.0e6);
        // below threshold nothing
        assert_eq!(damage_upper_bound(awr, -11.2e6, Some(2.0), 1.0e6), 0.0);
    }

    #[test]
    fn redundant_and_channel_mt_rules() {
        let present: BTreeSet<i32> = [2, 4, 16, 51, 91, 102, 103, 649, 18, 19].into_iter().collect();
        assert!(is_redundant(4, &present));
        assert!(!is_redundant(16, &present));
        assert!(is_redundant(103, &present));
        assert!(is_redundant(18, &present));
        assert!(is_redundant(1, &present));
        assert!(!is_redundant(102, &present));
        assert!(is_channel_mt(2) && is_channel_mt(849) && is_channel_mt(5));
        assert!(!is_channel_mt(301) && !is_channel_mt(444) && !is_channel_mt(1));
    }

    fn table2() -> DamageTable2 {
        let row = Table2Row {
            damage_energy_barn_eV: vec![1.0, 2.0],
            elastic: vec![0.5, 1.0],
            inelastic: vec![0.25, 0.5],
            disappearance: vec![0.25, 0.5],
            uncovered_xs_barn: vec![0.1, 0.2],
            uncovered_damage_upper_barn_eV: vec![3.0, 4.0],
            displacement_nrt_barn: vec![0.01, 0.02],
            displacement_arc_barn: Some(vec![0.005, 0.01]),
            channels: serde_json::json!({"covered": [{"mt": 2}]}),
        };
        DamageTable2 {
            format: TABLE_FORMAT_2.into(),
            source: Table2Source {
                citation: "c".into(),
                edition: "e".into(),
                url: "u".into(),
            },
            projectile: "neutron".into(),
            group_structure: "g".into(),
            boundaries_eV: vec![1.0e-5, 1.0, 1.0e6],
            units: TABLE_UNITS_2.into(),
            temperature_K: Some(0.0),
            method: "kinematic".into(),
            files: vec![Table2File {
                path: "n-Fe056.tendl".into(),
                sha256: "0".repeat(64),
            }],
            uncovered: vec![],
            displacement: Table2Displacement {
                displacement_energy_eV: BTreeMap::from([("Fe".to_string(), 40.0)]),
                arc_dpa: Table2Arc {
                    citation: ARC_DPA_CITATION.into(),
                    parameters: BTreeMap::from([(
                        "Fe".to_string(),
                        Table2ArcParameter {
                            b: -0.568,
                            c: 0.286,
                            reference_displacement_energy_eV: 40.0,
                        },
                    )]),
                },
            },
            model: serde_json::json!({}),
            targets: BTreeMap::from([("Fe56".to_string(), row)]),
        }
    }

    #[test]
    fn table2_round_trips_through_json() {
        let table = table2();
        let text = serde_json::to_string_pretty(&table).unwrap();
        let back: DamageTable2 = serde_json::from_str(&text).unwrap();
        assert_eq!(back, table);
        // an element without arc constants serializes without the arc array
        let mut no_arc = table2();
        no_arc.targets.get_mut("Fe56").unwrap().displacement_arc_barn = None;
        let text = serde_json::to_string(&no_arc).unwrap();
        assert!(!text.contains("displacement_arc_barn"));
        let back: DamageTable2 = serde_json::from_str(&text).unwrap();
        assert_eq!(back, no_arc);
    }

    // ---- end to end on a synthetic evaluation ------------------------------------------------------

    fn send(mat: i32, mf: i32) -> String {
        record(["", "", "", "", "", ""], mat, mf, 0, 99_999)
    }

    /// A TAB1 section `[mat, mf, mt]` with linear interpolation through `(x, y)` points.
    fn tab1_lines(mf: i32, mt: i32, q: [f64; 2], points: &[(f64, f64)]) -> Vec<String> {
        let mat = 2631;
        let mut lines = vec![record(["26056", "55.45", "0", "0", "0", "0"], mat, mf, mt, 1)];
        let mut seq = 2;
        lines.push(record(
            [&num(q[0]), &num(q[1]), "0", "0", "1", &points.len().to_string()],
            mat,
            mf,
            mt,
            seq,
        ));
        seq += 1;
        lines.push(record(
            [&points.len().to_string(), "2", "", "", "", ""],
            mat,
            mf,
            mt,
            seq,
        ));
        seq += 1;
        let values: Vec<String> = points.iter().flat_map(|(x, y)| [num(*x), num(*y)]).collect();
        lines.extend(packed(&values, mat, mf, mt, &mut seq));
        lines.push(send(mat, mf));
        lines
    }

    fn synthetic_tape() -> String {
        let mat = 2631;
        let mut lines = vec![
            record(["26056", "55.45", "0", "0", "0", "0"], mat, 1, 451, 1),
            record(["0", "0", "0", "0", "0", "0"], mat, 1, 451, 2),
            record(["1", "2e8", "1", "0", "10", "2025"], mat, 1, 451, 3),
            record(["0", "0", "0", "0", "0", "0"], mat, 1, 451, 4),
            send(mat, 1),
        ];
        lines.extend(tab1_lines(3, 2, [0.0, 0.0], &[(1.0e-5, 3.0), (2.0e7, 3.0)]));
        lines.extend(tab1_lines(3, 4, [0.0, -1.0e6], &[(1.1e6, 1.0), (2.0e7, 1.0)]));
        lines.extend(tab1_lines(3, 16, [-11.0e6, -11.0e6], &[(1.2e7, 0.1), (2.0e7, 0.2)]));
        // MF=4 MT=2: isotropic (LI=1) in the centre of mass
        lines.push(record(["26056", "55.45", "0", "0", "0", "0"], mat, 4, 2, 1));
        lines.push(record(["0", "55.45", "1", "2", "0", "0"], mat, 4, 2, 2));
        lines.push(send(mat, 4));
        lines.join("\n")
    }

    #[test]
    fn synthetic_evaluation_matches_an_independent_integral_and_bounds_uncovered() {
        let text = synthetic_tape();
        let evaluations = parse_evaluations(&text, Some(Projectile::Neutron)).unwrap();
        let distributions = parse_damage_endf(&text).unwrap();
        let evaluation = &evaluations[0];
        let endf = &distributions[&evaluation.metadata.mat];
        let groups = GroupStructure {
            name: "test".into(),
            boundaries_ev: vec![1.0e-5, 1.0e3, 1.0e5, 1.0e6, 1.0e7, 2.0e7],
        };
        let options = KinematicOptions {
            projectile: Some(Projectile::Neutron),
            groups: groups.clone(),
            temperature_K: 0.0,
            cache: None,
            displacement_energy_eV: BTreeMap::new(),
            default_displacement_energy_eV: 40.0,
            capture_photons: CapturePhotons::Evaluated,
            grid_density: 1.0,
        };
        let row = compute_row(evaluation, endf, &options, 40.0, Some((-0.568, 0.286)))
            .unwrap()
            .unwrap();
        assert_eq!(row.damage_energy_barn_eV.len(), 5);
        // independent value of the elastic damage in the group [1e6, 1e7]: sigma = 3 b, isotropic CM,
        // W(E) = 1/(2 c1) integral_{E_d}^{T_max} D(T) dT with the same Lindhard partition
        let awr = 55.45;
        let partition = Partition::new(26.0, awr, 26.0, awr, 40.0);
        let w = |e: f64| {
            let c = 2.0 * awr * e / (awr + 1.0).powi(2);
            let n = 4000;
            let (lo, hi) = (40.0, 2.0 * c);
            let mut sum = 0.0;
            for i in 0..n {
                let t = lo + (i as f64 + 0.5) * (hi - lo) / n as f64;
                sum += partition.damage_energy(t) * (hi - lo) / n as f64;
            }
            sum / (2.0 * c)
        };
        let (g0, g1) = (1.0e6, 1.0e7);
        let n = 400;
        let mut integral = 0.0;
        for i in 0..n {
            let l0 = (g0_f(g0, g1, i, n), g0_f(g0, g1, i + 1, n));
            let em = (l0.0 * l0.1).sqrt();
            integral += 3.0 * w(em) * (l0.1 / l0.0).ln();
        }
        let expected = integral / (g1 / g0).ln();
        let got = row.elastic[3];
        assert!(
            (got - expected).abs() < 5e-3 * expected,
            "elastic {got} vs {expected}"
        );
        // below the displacement threshold of the elastic channel (E_d (A+1)^2/(4A) ~ 570 eV) the first
        // group [1e-5, 1e3] is only partly active but nonnegative
        assert!(row.elastic[0] >= 0.0);
        // the elastic total equals damage energy when no other channel is covered
        assert_eq!(row.inelastic, vec![0.0; 5]);
        assert_eq!(row.disappearance, vec![0.0; 5]);
        assert_eq!(row.damage_energy_barn_eV, row.elastic);
        // displacements follow from the same integral: NRT count is positive in active groups and the
        // arc count is below it at high energy
        assert!(row.displacement_nrt_barn[3] > 0.0);
        let arc = row.displacement_arc_barn.as_ref().unwrap();
        assert!(arc[3] < row.displacement_nrt_barn[3] && arc[3] > 0.0);
        // uncovered: MT=16 (n,2n) is reported with its cross section, and MT=4 is skipped (no MT=51..91
        // present, so it is uncovered too: sigma = 1 b above 1.1 MeV)
        assert!(row.uncovered_xs_barn[3] > 0.9);
        assert!(row.uncovered_damage_upper_barn_eV[3] > 0.0);
        // the bound never lies below zero and the covered damage plus the bound is finite
        let notes = row.channels["uncovered"].as_array().unwrap();
        let mts: Vec<i64> = notes.iter().map(|n| n["mt"].as_i64().unwrap()).collect();
        assert_eq!(mts, vec![4, 16]);
        assert_eq!(row.channels["covered"][0]["mt"], 2);
    }

    fn g0_f(g0: f64, g1: f64, i: usize, n: usize) -> f64 {
        g0 * (g1 / g0).powf(i as f64 / n as f64)
    }

    #[test]
    fn capture_one_photon_recoil_and_threshold() {
        let awr = 55.45;
        let capture = Capture {
            awr,
            q: Some(7.646e6),
            photons: None,
            rc: Recoil::new(26.0, awr + 1.0, 26.0, awr, 40.0, None),
            njoy_one_photon: true,
        };
        // thermal capture: recoil from one photon of the Q-value energy, E_gamma^2 / (2 M c^2)
        let e = 0.0253;
        let kernel = capture.kernel(e);
        let tg = 7.646e6_f64.powi(2) / (2.0 * (awr + 1.0) * NEUTRON_REST_EV);
        let expected = Partition::new(26.0, awr + 1.0, 26.0, awr, 40.0).damage_energy(tg);
        assert!((kernel.d - expected).abs() < 1e-5 * expected, "{} {expected}", kernel.d);
        assert!(kernel.nrt > 0.0);
        // a lower Q puts the photon recoil below E_d: no damage at thermal energy
        let weak = Capture {
            q: Some(1.0e6),
            ..capture
        };
        assert_eq!(weak.kernel(e).d, 0.0);
    }

    // Pinned to NJOY2016.79 acecm.f90 `bachaa` (neutron incident, MeV), Fe-56 target, hand-evaluated:
    //   (n,n'), E = 14, E' = 5: Ac = 57, Zc = 26, Nc = 31; the residual equals the target, so sb = sa = 9.95787.
    //     ecm = 56 * 14 / 57 = 13.75439, ea = ecm + sa = 23.71226 (below both breakpoints 41 and 130, so x1 = x3 = eb)
    //     eb = 5 * 57 / 56 + sb = 5.08929 + 9.95787 = 15.04716
    //     a = 0.04 eb + 1.8e-6 eb^3 + 6.7e-7 * fb * eb^4, fb = 1/2 for an emitted neutron
    //       = 0.601886 + 0.006132 + 0.017174 = 0.625192
    //   (n,n'), E = 2, E' = 0.5: ea = 1.96491 + 9.95787 = 11.92278, eb = 0.50893 + 9.95787 = 10.46680,
    //     a = 0.418672 + 0.002064 + 0.004020 = 0.424757
    //   (n,p), E = 20, E' = 8 (zap 1001; Zb = 25, Ab = 56): sb = 8.42682 (no light-particle correction for protons),
    //     ea = 29.60699, eb = 8 * 57 / 56 + 8.42682 = 16.56968, fb = 1,
    //     a = 0.662787 + 0.008189 + 0.050507 = 0.721483
    // sa = 15.68 - 28.07 (25/57 - 16/56) - 18.56 (57^(2/3) - 56^(2/3)) + 33.22 (25/57^(4/3) - 16/56^(4/3))
    //      - 0.717 (676/57^(1/3) - 676/56^(1/3)) + 1.211 (676/57 - 676/56) = 9.95787.
    #[test]
    fn kalbach_a_pinned_to_njoy_bachaa() {
        for (z, a_t, zap, e, ep, expected) in [
            (26, 56, 1, 14.0e6, 5.0e6, 0.625192),
            (26, 56, 1, 2.0e6, 0.5e6, 0.424757),
            (26, 56, 1001, 20.0e6, 8.0e6, 0.721481),
        ] {
            let a = kalbach_a(z, a_t, zap, e, ep);
            assert!((a - expected).abs() < 1e-5, "{a} {expected}");
        }
    }
}
