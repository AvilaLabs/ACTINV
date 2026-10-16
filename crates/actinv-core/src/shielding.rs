#![allow(non_snake_case)] // JSON wire names carry their physical units.
//! Finite-dilution self-shielding (P19 G2): a hash-pinned `actinv-shield-table-1`
//! supplies Bondarenko factors on the frozen sigma0 x temperature grid. The
//! covered nuclide's effective dilution picks the factor at run time; each
//! group's unresolved-range overlap fraction blends it into the group rate.
//!
//! The table is produced by `actinv build-shielding` (crates/actinv-data);
//! this module only consumes it. Factors interpolate linearly in ln(sigma0)
//! and in sqrt(T), clamped at the grid ends.

use actinv_data::activation::{parse_evaluations, Projectile};
use actinv_data::composition::{self, MaterialKey};
use actinv_data::resonance::{
    legacy_effective_total_width, rml_resonance_widths, RangeData, ResonanceEvaluation, K_WAVE,
};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

const TABLE_FORMAT: &str = "actinv-shield-table-1";
/// Reaction-channel order inside each group row's factor matrix.
pub const CHANNELS: [&str; 4] = ["total", "elastic", "fission", "capture"];

fn name_of(za: i32, liso: i32) -> String {
    let s = composition::symbol_of(za / 1000);
    if liso > 0 {
        format!("{s}{}m{liso}", za % 1000)
    } else {
        format!("{s}{}", za % 1000)
    }
}

// The wire structs pin `actinv-shield-table-1` strictly; fields the runtime
// does not fold still must parse, so dead_code is allowed on them.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
#[allow(dead_code)]
struct WireGroup {
    group: usize,
    overlap_fraction: f64,
    /// D6e: resolved-range coverage inside `overlap_fraction`; informational.
    #[serde(default)]
    resolved_overlap_fraction: Option<f64>,
    sigma_p_b: f64,
    infinite_dilution_b: Vec<f64>,
    factors: BTreeMap<String, Vec<Vec<f64>>>,
    shielded_b: BTreeMap<String, Vec<Vec<f64>>>,
    #[serde(default)]
    background_b: Option<Vec<f64>>,
    #[serde(default)]
    weight_mean: Option<Vec<Vec<f64>>>,
    #[serde(default)]
    group_unshielded_b: Option<Vec<f64>>,
    #[serde(default)]
    group_shielded_b: Option<BTreeMap<String, Vec<Vec<f64>>>>,
    /// Full-group Bondarenko factor per channel; when present it is the
    /// applied scale, superseding the flat `(1-c)+c*f` lethargy blend.
    #[serde(default)]
    group_factors: Option<BTreeMap<String, Vec<Vec<f64>>>>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
#[allow(dead_code)]
struct WireNuclide {
    za: i32,
    liso: i32,
    unresolved_ranges_ev: Vec<Vec<f64>>,
    /// D6e: resolved-resonance ranges folded pointwise into group_factors.
    #[serde(default)]
    resolved_ranges_ev: Option<Vec<Vec<f64>>>,
    groups: Vec<WireGroup>,
    #[serde(default)]
    nodes: Vec<serde_json::Value>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
#[allow(dead_code)]
struct TableWire {
    format: String,
    generator: String,
    source: serde_json::Value,
    method: serde_json::Value,
    sigma0_b: Vec<f64>,
    temperatures_K: Vec<f64>,
    group_structure: serde_json::Value,
    files: Vec<serde_json::Value>,
    uncovered: Vec<String>,
    nuclides: BTreeMap<String, WireNuclide>,
}

/// Per-group scale map for one nuclide and channel.
type GroupScales = BTreeMap<usize, f64>;

#[derive(Debug)]
#[allow(dead_code)]
struct PreparedNuclide {
    sigma_p_b: f64,
    sigma0_eff_b: f64,
    /// channel -> (group -> applied scale: the full-group Bondarenko factor
    /// when the table carries `group_factors`, else (1-c_g) + c_g*f_g);
    /// absent groups are 1.
    scales: [GroupScales; 4],
}

/// The per-run plan: which composition nuclides are covered, their effective
/// dilutions, and the per-(nuclide, channel, group) scale factors.
#[derive(Debug)]
pub struct ShieldPlan {
    covered: BTreeMap<(i32, i32), PreparedNuclide>,
    /// Composition nuclides named absent from the table.
    pub uncovered_targets: Vec<String>,
    /// Composition members whose sigma_p was estimated (not table-resident).
    pub sigma_p_estimated: Vec<String>,
    pub dilution: String,
    pub sigma0_fixed_b: Option<f64>,
    /// nuclide -> sigma0_eff (barns), for the ledger/certificate.
    pub sigma0_eff_b: BTreeMap<String, f64>,
    /// nuclide -> group -> per-channel applied factors, for the ledger.
    pub applied: BTreeMap<String, BTreeMap<usize, [f64; 4]>>,
    /// nuclide -> group -> user dilution (barns) that replaced the computed
    /// background dilution (SSFDILUTION); empty unless overrides were given.
    pub dilution_overrides: BTreeMap<String, BTreeMap<usize, f64>>,
    /// Sigmoid-curve geometry report; `None` on the Bondarenko path.
    pub geometry: Option<GeometryReport>,
}

#[derive(Debug)]
#[allow(dead_code)]
pub struct PreparedShieldTable {
    sigma0_b: Vec<f64>,
    temperatures_K: Vec<f64>,
    boundaries_eV: Vec<f64>,
    files: Vec<serde_json::Value>,
    uncovered_declared: Vec<String>,
    nuclides: BTreeMap<(i32, i32), WireNuclide>,
    /// nuclide -> overlap-weighted mean sigma_p over covered groups.
    sigma_p_mean: BTreeMap<(i32, i32), f64>,
}

/// Reaction-channel assignment for an activation MT: elastic for 2, fission
/// for 18/19, radiative capture for 102, and the total factor for every other
/// channel — the unresolved-region flux depression on a channel whose width
/// has no dedicated unresolved statistics.
fn channel_of_mt(mt: i32) -> usize {
    match mt {
        2 => 1,
        18 | 19 => 2,
        102 => 3,
        _ => 0,
    }
}

impl PreparedShieldTable {
    pub fn from_json(text: &str) -> Result<Self, String> {
        let table: TableWire =
            serde_json::from_str(text).map_err(|error| format!("shield table: {error}"))?;
        if table.format != TABLE_FORMAT {
            return Err(format!(
                "unsupported shield table format '{}'",
                table.format
            ));
        }
        if table.sigma0_b.len() < 2 || !table.sigma0_b.windows(2).all(|w| w[0] > w[1] && w[1] > 0.0)
        {
            return Err("shield table sigma0_b must be strictly descending positive".into());
        }
        if table.temperatures_K.len() < 2
            || !table
                .temperatures_K
                .windows(2)
                .all(|w| w[1] > w[0] && w[0] > 0.0)
        {
            return Err("shield table temperatures_K must be strictly ascending positive".into());
        }
        let boundaries_eV = table
            .group_structure
            .get("boundaries_eV")
            .and_then(|v| v.as_array())
            .ok_or("shield table group_structure lacks boundaries_eV")?
            .iter()
            .map(|v| {
                v.as_f64()
                    .ok_or("shield table group boundary is not numeric")
            })
            .collect::<Result<Vec<_>, _>>()?;
        if boundaries_eV.len() < 2
            || boundaries_eV.iter().any(|v| !v.is_finite() || *v <= 0.0)
            || boundaries_eV.windows(2).any(|w| w[1] <= w[0])
        {
            return Err(
                "shield table boundaries_eV must be positive and strictly ascending".into(),
            );
        }
        let nsig = table.sigma0_b.len();
        let ntemp = table.temperatures_K.len();
        let mut nuclides = BTreeMap::new();
        let mut sigma_p_mean = BTreeMap::new();
        for (raw, nuc) in &table.nuclides {
            let key = match composition::material_key(raw)
                .map_err(|error| format!("shield table nuclide '{raw}': {error}"))?
            {
                MaterialKey::Nuclide { za, liso, .. } => (za, liso),
                MaterialKey::Element(_) => {
                    return Err(format!(
                        "shield table nuclide '{raw}' is elemental; expected a nuclide"
                    ));
                }
            };
            if *raw != name_of(key.0, key.1) {
                return Err(format!(
                    "shield table nuclide '{raw}' is not canonical; use '{}'",
                    name_of(key.0, key.1)
                ));
            }
            if nuc.za != key.0 || nuc.liso != key.1 {
                return Err(format!("shield table nuclide '{raw}' za/liso mismatch"));
            }
            let mut sp_num = 0.0;
            let mut sp_den = 0.0;
            for g in &nuc.groups {
                if !(0.0..=1.0).contains(&g.overlap_fraction) {
                    return Err(format!(
                        "shield table nuclide '{raw}' group {} overlap_fraction out of [0,1]",
                        g.group
                    ));
                }
                if g.infinite_dilution_b.len() != 4 {
                    return Err(format!(
                        "shield table nuclide '{raw}' group {} needs 4 infinite_dilution_b",
                        g.group
                    ));
                }
                for channel in CHANNELS {
                    let rows = g.factors.get(channel).ok_or_else(|| {
                        format!(
                            "shield table nuclide '{raw}' group {gi} lacks '{channel}' factors",
                            gi = g.group
                        )
                    })?;
                    let mut grids = vec![rows];
                    if let Some(gf) = &g.group_factors {
                        if let Some(rows) = gf.get(channel) {
                            grids.push(rows);
                        }
                    }
                    for rows in grids {
                        if rows.len() != nsig
                            || rows.iter().any(|r| r.len() != ntemp)
                            || rows.iter().flatten().any(|v| !v.is_finite() || *v < 0.0)
                        {
                            return Err(format!(
                                "shield table nuclide '{raw}' group {gi} '{channel}' factors are not {nsig}x{ntemp} finite nonnegative",
                                gi = g.group
                            ));
                        }
                    }
                }
                if g.sigma_p_b.is_finite() && g.sigma_p_b > 0.0 {
                    sp_num += g.sigma_p_b * g.overlap_fraction;
                    sp_den += g.overlap_fraction;
                }
            }
            if sp_den > 0.0 {
                sigma_p_mean.insert(key, sp_num / sp_den);
            }
            nuclides.insert(key, nuc.clone());
        }
        Ok(Self {
            sigma0_b: table.sigma0_b,
            temperatures_K: table.temperatures_K,
            boundaries_eV,
            files: table.files,
            uncovered_declared: table.uncovered,
            nuclides,
            sigma_p_mean,
        })
    }

    pub fn boundaries_eV(&self) -> &[f64] {
        &self.boundaries_eV
    }

    /// Potential-scattering cross section for a composition nuclide: the
    /// table's overlap-weighted mean when covered, else the analytic estimate
    /// `4*pi*(0.123*A^(1/3)+0.08 fm)^2 * ((A+1)/A)^2` in barns — the same
    /// channel-radius formula PURR uses when NAPS=0. Returns (sigma_p,
    /// estimated).
    fn sigma_p(&self, za: i32, liso: i32) -> (f64, bool) {
        if let Some(&sp) = self.sigma_p_mean.get(&(za, liso)) {
            return (sp, false);
        }
        let a_mass = (za % 1000).max(1) as f64;
        let radius_fm = 0.123 * a_mass.cbrt() + 0.08;
        let lab = (a_mass + 1.0) / a_mass;
        (
            4.0 * std::f64::consts::PI * radius_fm * radius_fm * lab * lab * 0.01,
            true,
        )
    }

    /// Bilinear interpolation in (ln sigma0, sqrt T), clamped at the grid
    /// ends. `values` is the [sigma0][temperature] factor matrix.
    fn factor_at(&self, values: &[Vec<f64>], sigma0: f64, temperature_K: f64) -> f64 {
        let ln0 = sigma0.ln();
        let lns: Vec<f64> = self.sigma0_b.iter().map(|s| s.ln()).collect();
        let (si_lo, si_hi, sw) = if ln0 >= lns[0] {
            (0, 0, 0.0)
        } else if ln0 <= *lns.last().unwrap() {
            let last = lns.len() - 1;
            (last, last, 0.0)
        } else {
            // sigma0_b is strictly descending: `hi` is the first (smaller-sigma0)
            // entry at or below the query and `lo = hi - 1` its larger neighbour.
            let hi = lns.iter().position(|&l| l <= ln0).unwrap();
            if lns[hi] == ln0 {
                (hi, hi, 0.0)
            } else {
                let lo = hi - 1;
                // Weight toward `hi`: 0 at lns[lo], 1 at lns[hi].
                let w = (lns[lo] - ln0) / (lns[lo] - lns[hi]);
                (lo, hi, w)
            }
        };
        let st = temperature_K.sqrt();
        let ts: Vec<f64> = self.temperatures_K.iter().map(|t| t.sqrt()).collect();
        let (ti_lo, ti_hi, tw) = if st <= ts[0] {
            (0, 0, 0.0)
        } else if st >= *ts.last().unwrap() {
            let last = ts.len() - 1;
            (last, last, 0.0)
        } else {
            let hi = ts.iter().position(|&t| t >= st).unwrap();
            let lo = hi - 1;
            let w = (st - ts[lo]) / (ts[hi] - ts[lo]);
            (lo, hi, w)
        };
        let a = values[si_lo][ti_lo] + tw * (values[si_lo][ti_hi] - values[si_lo][ti_lo]);
        let b = values[si_hi][ti_lo] + tw * (values[si_hi][ti_hi] - values[si_hi][ti_lo]);
        a + sw * (b - a)
    }

    /// Build the per-run plan: coverage against the material composition,
    /// effective dilution per covered nuclide, and per-group per-channel
    /// scales s_g = (1-c_g) + c_g*f(sigma0_eff, T).
    pub fn plan(
        &self,
        composition_atoms: &BTreeMap<(i32, i32), f64>,
        dilution: &str,
        sigma0_fixed_b: Option<f64>,
        temperature_K: f64,
        require_complete: bool,
    ) -> Result<ShieldPlan, String> {
        self.plan_with_overrides(
            composition_atoms,
            dilution,
            sigma0_fixed_b,
            &BTreeMap::new(),
            temperature_K,
            require_complete,
        )
    }

    /// [`Self::plan`] with user dilution cross sections (FISPACT-II
    /// SSFDILUTION): `dilution_overrides[nuclide][group]` replaces the
    /// nuclide's computed (or fixed) sigma0 in that group only. Every named
    /// nuclide must be a covered material nuclide with table data in every
    /// named group.
    pub fn plan_with_overrides(
        &self,
        composition_atoms: &BTreeMap<(i32, i32), f64>,
        dilution: &str,
        sigma0_fixed_b: Option<f64>,
        dilution_overrides: &BTreeMap<String, BTreeMap<usize, f64>>,
        temperature_K: f64,
        require_complete: bool,
    ) -> Result<ShieldPlan, String> {
        let mut covered = BTreeMap::new();
        let mut uncovered = Vec::new();
        let mut estimated = Vec::new();
        let mut sigma0_eff = BTreeMap::new();
        let mut applied: BTreeMap<String, BTreeMap<usize, [f64; 4]>> = BTreeMap::new();
        // sigma_p for every composition member (table mean or estimate).
        let mut estimated_set = BTreeSet::new();
        let sigma_p: BTreeMap<(i32, i32), f64> = composition_atoms
            .keys()
            .map(|&key| {
                let (sp, est) = self.sigma_p(key.0, key.1);
                if est {
                    estimated_set.insert(key);
                }
                (key, sp)
            })
            .collect();
        for key in estimated_set {
            estimated.push(name_of(key.0, key.1));
        }
        let grid_floor = *self.sigma0_b.last().unwrap();
        for &(za, liso) in composition_atoms.keys() {
            let Some(nuc) = self.nuclides.get(&(za, liso)) else {
                uncovered.push(name_of(za, liso));
                continue;
            };
            let ni = composition_atoms[&(za, liso)];
            let sigma0 = match dilution {
                "fixed" => sigma0_fixed_b.expect("fixed dilution is validated at spec parse"),
                _ => {
                    if ni <= 0.0 {
                        0.0
                    } else {
                        (composition_atoms
                            .iter()
                            .filter(|(&k, _)| k != (za, liso))
                            .map(|(k, &n)| n * sigma_p[k])
                            .sum::<f64>()
                            / ni)
                            .max(0.0)
                    }
                }
            };
            let name = name_of(za, liso);
            let nuclide_overrides = dilution_overrides.get(&name);
            let mut scales: [GroupScales; 4] = Default::default();
            let mut rows = BTreeMap::new();
            for g in &nuc.groups {
                let mut row = [0.0f64; 4];
                // SSFDILUTION: a user dilution for this group replaces the
                // nuclide's computed one.
                let group_sigma0 = nuclide_overrides
                    .and_then(|groups| groups.get(&g.group))
                    .copied()
                    .unwrap_or(sigma0);
                for (c, channel) in CHANNELS.iter().enumerate() {
                    let s0 = group_sigma0.clamp(grid_floor, f64::INFINITY);
                    // Prefer the full-group Bondarenko factor (probability-
                    // table weight over the covered segments plus the smooth
                    // background's own suppression); the flat lethargy blend
                    // remains the fallback for tables predating it.
                    let (f, scale) = match g.group_factors.as_ref().and_then(|gf| gf.get(*channel))
                    {
                        Some(gf) => {
                            let gfac = self.factor_at(gf, s0, temperature_K);
                            let seg = self.factor_at(&g.factors[*channel], s0, temperature_K);
                            (seg, gfac)
                        }
                        None => {
                            let f = self.factor_at(&g.factors[*channel], s0, temperature_K);
                            (f, (1.0 - g.overlap_fraction) + g.overlap_fraction * f)
                        }
                    };
                    row[c] = f;
                    scales[c].insert(g.group, scale);
                }
                rows.insert(g.group, row);
            }
            sigma0_eff.insert(name.clone(), sigma0);
            applied.insert(name, rows);
            covered.insert(
                (za, liso),
                PreparedNuclide {
                    sigma_p_b: sigma_p[&(za, liso)],
                    sigma0_eff_b: sigma0,
                    scales,
                },
            );
        }
        for (name, groups) in dilution_overrides {
            let Some(rows) = applied.get(name) else {
                return Err(format!(
                    "self_shielding.dilution_overrides names '{name}', which is not a table-covered material nuclide"
                ));
            };
            if let Some(group) = groups.keys().find(|group| !rows.contains_key(*group)) {
                return Err(format!(
                    "self_shielding.dilution_overrides for '{name}' names group {group}, which the table does not carry for that nuclide"
                ));
            }
        }
        if require_complete && !uncovered.is_empty() {
            return Err(format!(
                "self_shielding: material nuclides lack table coverage: {}",
                uncovered.join(", ")
            ));
        }
        Ok(ShieldPlan {
            covered,
            uncovered_targets: uncovered,
            sigma_p_estimated: estimated,
            dilution: dilution.into(),
            sigma0_fixed_b,
            sigma0_eff_b: sigma0_eff,
            applied,
            dilution_overrides: dilution_overrides.clone(),
            geometry: None,
        })
    }
}

// ---- Geometry self-shielding (P129): the universal sigmoid curve -------------
//
// Martinho, Goncalves and Salgado's "universal sigmoid curve" model of resonance
// self-shielding, as FISPACT-II implements it behind SSFGEOMETRY (manual
// Appendix C.4.4): a dimensionless length z per resonance maps through one
// sigmoid to a factor G, the factors of the resonances whose peaks fall in a
// group are averaged with weights proportional to the resonance strength, and
// the group average scales that nuclide's group cross sections.

/// Sigmoid parameters A1, A2, z0 and p, fitted by Martinho et al. to MCNP
/// results; FISPACT-II manual Eqs. 45-48 (published uncertainties 0.005,
/// 0.011, 0.09 and 0.02 respectively).
pub const SIGMOID_A1: f64 = 1.000;
pub const SIGMOID_A2: f64 = 0.060;
pub const SIGMOID_Z0: f64 = 2.70;
pub const SIGMOID_P: f64 = 0.82;

/// Universal sigmoid curve `G(z) = (A1 - A2) / (1 + (z/z0)^p) + A2`
/// (FISPACT-II manual Eq. 44). G -> A1 = 1 as z -> 0 (thin target) and falls
/// monotonically toward A2 as z grows.
pub fn sigmoid_factor(z: f64) -> f64 {
    if z.is_nan() || z <= 0.0 {
        return SIGMOID_A1;
    }
    (SIGMOID_A1 - SIGMOID_A2) / (1.0 + (z / SIGMOID_Z0).powf(SIGMOID_P)) + SIGMOID_A2
}

/// Target shapes of SSFGEOMETRY types 1-4.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum GeometryShape {
    Foil,
    Wire,
    Sphere,
    Cylinder,
}

impl GeometryShape {
    pub fn parse(value: &str) -> Result<Self, String> {
        match value {
            "foil" => Ok(Self::Foil),
            "wire" => Ok(Self::Wire),
            "sphere" => Ok(Self::Sphere),
            "cylinder" => Ok(Self::Cylinder),
            other => Err(format!(
                "unknown self_shielding.geometry.shape '{other}' (expected foil, wire, sphere or cylinder)"
            )),
        }
    }

    pub fn name(self) -> &'static str {
        match self {
            Self::Foil => "foil",
            Self::Wire => "wire",
            Self::Sphere => "sphere",
            Self::Cylinder => "cylinder",
        }
    }

    /// The effective length y of FISPACT-II manual Table 25 in cm: foil of
    /// thickness t, y = 1.5 t; wire of radius r, y = 2 r; sphere of radius r,
    /// y = r; cylinder of radius r and height h, y = 1.65 r h / (r + h).
    /// `length_cm` is the thickness (foil) or radius; `height_cm` is required
    /// for, and only for, a cylinder. Dimensions must be finite and positive.
    pub fn effective_length_cm(
        self,
        length_cm: f64,
        height_cm: Option<f64>,
    ) -> Result<f64, String> {
        let positive = |value: f64, label: &str| -> Result<f64, String> {
            if value.is_finite() && value > 0.0 {
                Ok(value)
            } else {
                Err(format!(
                    "self_shielding.geometry.{label} must be a positive finite number of cm"
                ))
            }
        };
        let length = positive(length_cm, "length_cm")?;
        match (self, height_cm) {
            (Self::Cylinder, Some(height)) => {
                let height = positive(height, "height_cm")?;
                Ok(1.65 * length * height / (length + height))
            }
            (Self::Cylinder, None) => {
                Err("self_shielding.geometry shape 'cylinder' requires height_cm".into())
            }
            (_, Some(_)) => Err(format!(
                "self_shielding.geometry.height_cm applies only to a cylinder, not a {}",
                self.name()
            )),
            (Self::Foil, None) => Ok(1.5 * length),
            (Self::Wire, None) => Ok(2.0 * length),
            (Self::Sphere, None) => Ok(length),
        }
    }
}

/// One resolved resonance reduced to what the sigmoid model needs.
#[derive(Debug, Clone, PartialEq)]
struct ResonanceLine {
    energy_ev: f64,
    /// Peak total cross section of the isolated resonance, barns:
    /// `4 pi/k^2 * g * Gamma_n/Gamma`, the single-level Breit-Wigner peak. The
    /// FISPACT-II manual takes this from Froehner (JEFF Report 18, Eq. 186)
    /// without reproducing it.
    peak_b: f64,
    /// `sqrt(Gamma_gamma / Gamma)`, the width factor of manual Eq. 43.
    capture_root: f64,
    /// Averaging weight `(Gamma_gamma / E^2) * (g Gamma_n / Gamma)` of Salgado
    /// et al. (manual Eq. 49).
    weight: f64,
}

impl ResonanceLine {
    /// None for a resonance the model cannot use: a bound level (E <= 0) or
    /// one without a positive neutron or capture width.
    fn from_widths(
        energy_ev: f64,
        spin_j: f64,
        target_spin: f64,
        awri: f64,
        gamma_n: f64,
        gamma_g: f64,
        gamma_total: f64,
    ) -> Option<Self> {
        let finite = [
            energy_ev,
            spin_j,
            target_spin,
            awri,
            gamma_n,
            gamma_g,
            gamma_total,
        ]
        .iter()
        .all(|value| value.is_finite());
        if !finite
            || energy_ev <= 0.0
            || awri <= 0.0
            || target_spin < 0.0
            || gamma_n <= 0.0
            || gamma_g <= 0.0
        {
            return None;
        }
        // The listed total can fall below its components through rounding.
        let gamma = gamma_total.max(gamma_n + gamma_g);
        // Statistical factor g = (2J+1) / (2(2I+1)) (manual, after Eq. 49).
        let statistical = (2.0 * spin_j.abs() + 1.0) / (2.0 * (2.0 * target_spin + 1.0));
        // ENDF-6 wave number in 1/sqrt(barn): pi/k^2 is a cross section in barns.
        let wave = K_WAVE * awri / (awri + 1.0) * energy_ev.sqrt();
        let neutron_fraction = statistical * gamma_n / gamma;
        Some(Self {
            energy_ev,
            peak_b: 4.0 * std::f64::consts::PI / (wave * wave) * neutron_fraction,
            capture_root: (gamma_g / gamma).sqrt(),
            weight: gamma_g / (energy_ev * energy_ev) * neutron_fraction,
        })
    }

    /// `z = Sigma_tot(E_res) * y * sqrt(Gamma_gamma / Gamma)` (manual Eq. 43)
    /// with `Sigma_tot = N * sigma_peak` for the nuclide's own number density
    /// `N` (cm^-3) and the target's effective length `y` (cm).
    fn z(&self, number_density_per_cm3: f64, effective_length_cm: f64) -> f64 {
        number_density_per_cm3
            * self.peak_b
            * crate::power::square_cm_per_barn()
            * effective_length_cm
            * self.capture_root
    }
}

/// The resolved (LRU=1) Breit-Wigner, Reich-Moore and R-matrix-limited
/// resonances of the MF=2 isotope whose ZA is `za`. R-matrix-limited (LRF=7)
/// spin groups give Gamma_n, Gamma_gamma and Gamma by summing the neutron,
/// capture and all channel widths; a feature that cannot be read that way is
/// an error rather than silently dropped.
fn resolved_lines(evaluation: &ResonanceEvaluation, za: i32) -> Result<Vec<ResonanceLine>, String> {
    let mut out = Vec::new();
    for isotope in evaluation
        .isotopes
        .iter()
        .filter(|isotope| isotope.zai == za || evaluation.isotopes.len() == 1)
    {
        for range in &isotope.ranges {
            match &range.data {
                RangeData::BreitWigner(resolved) => {
                    for group in &resolved.groups {
                        for resonance in &group.resonances {
                            let total = legacy_effective_total_width(group, resonance);
                            out.extend(ResonanceLine::from_widths(
                                resonance.energy,
                                resonance.spin,
                                resolved.spin,
                                group.awri,
                                resonance.neutron,
                                resonance.capture,
                                total,
                            ));
                        }
                    }
                }
                RangeData::ReichMoore(resolved) => {
                    for group in &resolved.groups {
                        for resonance in &group.resonances {
                            // Reich-Moore lists no total; fission widths are signed.
                            let total = resonance.neutron
                                + resonance.capture
                                + resonance.fission_a.abs()
                                + resonance.fission_b.abs();
                            out.extend(ResonanceLine::from_widths(
                                resonance.energy,
                                resonance.spin,
                                resolved.spin,
                                group.awri,
                                resonance.neutron,
                                resonance.capture,
                                total,
                            ));
                        }
                    }
                }
                RangeData::RMatrixLimited(rml) => {
                    let resonances = rml_resonance_widths(rml).map_err(|error| {
                        format!(
                            "resolved range {}-{} eV (LRF=7): {error}",
                            range.energy_min, range.energy_max
                        )
                    })?;
                    for resonance in resonances {
                        out.extend(ResonanceLine::from_widths(
                            resonance.energy,
                            resonance.spin,
                            resonance.target_spin,
                            resonance.awri,
                            resonance.neutron,
                            resonance.capture,
                            resonance.total,
                        ));
                    }
                }
                RangeData::ScatteringOnly { .. } | RangeData::Unresolved(_) => {}
            }
        }
    }
    Ok(out)
}

/// Library group index containing `energy_ev` for ascending `boundaries_ev`
/// (`[b_g, b_{g+1})`), or None outside the structure.
fn group_of(boundaries_ev: &[f64], energy_ev: f64) -> Option<usize> {
    let at_or_below = boundaries_ev.partition_point(|&boundary| boundary <= energy_ev);
    (at_or_below >= 1 && at_or_below < boundaries_ev.len()).then(|| at_or_below - 1)
}

/// One SSFCHOOSE entry.
#[derive(Debug, Clone, Copy)]
enum ChooseEntry {
    Nuclide(i32, i32),
    Element(i32),
}

impl ChooseEntry {
    fn matches(self, key: (i32, i32)) -> bool {
        match self {
            Self::Nuclide(za, liso) => key == (za, liso),
            Self::Element(z) => key.0 / 1000 == z,
        }
    }
}

/// Applied factor of one nuclide in one library group, for the output.
#[derive(Debug, Clone, Serialize)]
pub struct GeometryGroupFactor {
    pub group: usize,
    pub energy_min_eV: f64,
    pub energy_max_eV: f64,
    /// Resonances whose peaks fall in the group.
    pub resonances: usize,
    /// Weighted mean G of those resonances (manual Eq. 50).
    pub factor: f64,
}

/// What geometry mode applied, for the ledger and certificate.
#[derive(Debug, Clone, Serialize)]
pub struct GeometryReport {
    pub shape: String,
    pub length_cm: f64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub height_cm: Option<f64>,
    /// Effective length y of manual Table 25.
    pub effective_length_cm: f64,
    pub density_g_cm3: f64,
    /// "all" or "capture": which reactions of a shielded nuclide scale.
    pub reactions: String,
    /// nuclide -> number density (cm^-3) used for z.
    pub number_density_per_cm3: BTreeMap<String, f64>,
    /// nuclide -> per-group applied factors with their energy ranges.
    pub factors: BTreeMap<String, Vec<GeometryGroupFactor>>,
    /// Nuclides with resonance data that took no part (not in the material
    /// or not chosen).
    pub resonance_data_unused: Vec<String>,
}

/// Parsed geometry-mode inputs: the target size and the resolved resonances
/// of each nuclide supplied by the declared evaluations.
#[derive(Debug)]
pub struct PreparedGeometry {
    shape: GeometryShape,
    length_cm: f64,
    height_cm: Option<f64>,
    effective_length_cm: f64,
    density_g_cm3: f64,
    capture_only: bool,
    choose: Vec<(String, ChooseEntry)>,
    lines: BTreeMap<(i32, i32), Vec<ResonanceLine>>,
}

impl PreparedGeometry {
    /// `files` holds (label, ENDF-6 text) per declared resonance file; the
    /// caller has already verified each file's hash. Every evaluation must
    /// carry MF=2/MT=151 with resolved resonances.
    pub fn from_evaluations(
        geometry: &crate::spec::SelfShieldingGeometry,
        files: &[(String, String)],
    ) -> Result<Self, String> {
        let shape = GeometryShape::parse(&geometry.shape)?;
        let effective_length_cm =
            shape.effective_length_cm(geometry.length_cm, geometry.height_cm)?;
        if !geometry.density_g_cm3.is_finite() || geometry.density_g_cm3 <= 0.0 {
            return Err(
                "self_shielding.geometry.density_g_cm3 must be a positive finite number".into(),
            );
        }
        let capture_only = match geometry.reactions.as_str() {
            "all" => false,
            "capture" => true,
            value => {
                return Err(format!(
                    "unknown self_shielding.geometry.reactions '{value}' (expected all or capture)"
                ));
            }
        };
        let mut choose = Vec::new();
        for raw in geometry.choose.iter().flatten() {
            let entry = match composition::material_key(raw)
                .map_err(|error| format!("self_shielding.geometry.choose: {error}"))?
            {
                MaterialKey::Nuclide { za, liso, .. } => ChooseEntry::Nuclide(za, liso),
                MaterialKey::Element(symbol) => {
                    ChooseEntry::Element(composition::z_of(&symbol).ok_or_else(|| {
                        format!("self_shielding.geometry.choose: unknown element '{symbol}'")
                    })?)
                }
            };
            choose.push((raw.clone(), entry));
        }
        let mut lines: BTreeMap<(i32, i32), Vec<ResonanceLine>> = BTreeMap::new();
        for (label, text) in files {
            let evaluations =
                parse_evaluations(text, Some(Projectile::Neutron)).map_err(|error| {
                    format!("self_shielding.geometry resonance file {label}: {error}")
                })?;
            for evaluation in &evaluations {
                let key = (evaluation.metadata.za, evaluation.metadata.liso);
                let name = name_of(key.0, key.1);
                let Some(resonance) = &evaluation.resonance else {
                    return Err(format!(
                        "self_shielding.geometry resonance file {label}: {name} has no MF=2/MT=151 resonance parameters; geometry self-shielding needs resolved resonance data"
                    ));
                };
                let found = resolved_lines(resonance, evaluation.metadata.za).map_err(|error| {
                    format!("self_shielding.geometry resonance file {label}: {name}: {error}")
                })?;
                if found.is_empty() {
                    return Err(format!(
                        "self_shielding.geometry resonance file {label}: {name} has no usable resolved (LRU=1) resonances"
                    ));
                }
                if lines.insert(key, found).is_some() {
                    return Err(format!(
                        "self_shielding.geometry resonance files supply {name} more than once"
                    ));
                }
            }
        }
        Ok(Self {
            shape,
            length_cm: geometry.length_cm,
            height_cm: geometry.height_cm,
            effective_length_cm,
            density_g_cm3: geometry.density_g_cm3,
            capture_only,
            choose,
            lines,
        })
    }

    fn is_chosen(&self, key: (i32, i32)) -> bool {
        self.choose.is_empty() || self.choose.iter().any(|(_, entry)| entry.matches(key))
    }

    /// Build the per-run plan. `composition_atoms` is atoms per gram of the
    /// declared material; `boundaries_ev` the activation library's ascending
    /// group boundaries. Each shielded nuclide's number density is
    /// `atoms_per_gram * density`; per group, the Eq. 50 weighted mean of the
    /// resonances' G(z) scales that nuclide's cross sections. Groups without
    /// resonance peaks keep factor one.
    pub fn plan(
        &self,
        composition_atoms: &BTreeMap<(i32, i32), f64>,
        boundaries_ev: &[f64],
        require_complete: bool,
    ) -> Result<ShieldPlan, String> {
        if boundaries_ev.len() < 2
            || boundaries_ev
                .iter()
                .any(|boundary| !boundary.is_finite() || *boundary <= 0.0)
            || boundaries_ev.windows(2).any(|w| w[1] <= w[0])
        {
            return Err(
                "self_shielding geometry needs positive, strictly ascending activation-library group boundaries"
                    .into(),
            );
        }
        for (raw, entry) in &self.choose {
            if !composition_atoms.keys().any(|&key| entry.matches(key)) {
                return Err(format!(
                    "self_shielding.geometry.choose names '{raw}', which is not in the material"
                ));
            }
        }
        let mut covered = BTreeMap::new();
        let mut uncovered = Vec::new();
        let mut applied: BTreeMap<String, BTreeMap<usize, [f64; 4]>> = BTreeMap::new();
        let mut factors: BTreeMap<String, Vec<GeometryGroupFactor>> = BTreeMap::new();
        let mut densities = BTreeMap::new();
        for (&key, &atoms_per_gram) in composition_atoms {
            if !self.is_chosen(key) {
                continue;
            }
            let name = name_of(key.0, key.1);
            let Some(lines) = self.lines.get(&key) else {
                if self.choose.is_empty() {
                    uncovered.push(name);
                    continue;
                }
                return Err(format!(
                    "self_shielding.geometry.choose selects {name}, but no resonance file supplies it"
                ));
            };
            let number_density = atoms_per_gram * self.density_g_cm3;
            // group -> (sum of w*G, sum of w, resonance count)
            let mut sums: BTreeMap<usize, (f64, f64, usize)> = BTreeMap::new();
            for line in lines {
                let Some(group) = group_of(boundaries_ev, line.energy_ev) else {
                    continue;
                };
                let g = sigmoid_factor(line.z(number_density, self.effective_length_cm));
                let entry = sums.entry(group).or_insert((0.0, 0.0, 0));
                entry.0 += line.weight * g;
                entry.1 += line.weight;
                entry.2 += 1;
            }
            let mut scale_map = GroupScales::new();
            let mut rows = BTreeMap::new();
            let mut report_rows = Vec::new();
            for (group, (weighted, weight, count)) in sums {
                if weight <= 0.0 {
                    continue;
                }
                let factor = weighted / weight;
                scale_map.insert(group, factor);
                rows.insert(
                    group,
                    if self.capture_only {
                        [1.0, 1.0, 1.0, factor]
                    } else {
                        [factor; 4]
                    },
                );
                report_rows.push(GeometryGroupFactor {
                    group,
                    energy_min_eV: boundaries_ev[group],
                    energy_max_eV: boundaries_ev[group + 1],
                    resonances: count,
                    factor,
                });
            }
            let mut scales: [GroupScales; 4] = Default::default();
            if self.capture_only {
                scales[3] = scale_map;
            } else {
                for channel in &mut scales {
                    *channel = scale_map.clone();
                }
            }
            covered.insert(
                key,
                PreparedNuclide {
                    sigma_p_b: 0.0,
                    sigma0_eff_b: 0.0,
                    scales,
                },
            );
            applied.insert(name.clone(), rows);
            factors.insert(name.clone(), report_rows);
            densities.insert(name, number_density);
        }
        if require_complete && !uncovered.is_empty() {
            return Err(format!(
                "self_shielding: material nuclides lack resonance data: {}",
                uncovered.join(", ")
            ));
        }
        let unused: Vec<String> = self
            .lines
            .keys()
            .filter(|&&key| !composition_atoms.contains_key(&key) || !self.is_chosen(key))
            .map(|&(za, liso)| name_of(za, liso))
            .collect();
        Ok(ShieldPlan {
            covered,
            uncovered_targets: uncovered,
            sigma_p_estimated: Vec::new(),
            dilution: "geometry".into(),
            sigma0_fixed_b: None,
            sigma0_eff_b: BTreeMap::new(),
            applied,
            dilution_overrides: BTreeMap::new(),
            geometry: Some(GeometryReport {
                shape: self.shape.name().into(),
                length_cm: self.length_cm,
                height_cm: self.height_cm,
                effective_length_cm: self.effective_length_cm,
                density_g_cm3: self.density_g_cm3,
                reactions: if self.capture_only { "capture" } else { "all" }.into(),
                number_density_per_cm3: densities,
                factors,
                resonance_data_unused: unused,
            }),
        })
    }
}

impl ShieldPlan {
    /// The plan for a `self_shielding` section: the Bondarenko table (with
    /// any dilution overrides) or the sigmoid-curve geometry, never both.
    /// `boundaries_ev` are the activation library's group boundaries.
    pub fn build(
        table: Option<&PreparedShieldTable>,
        geometry: Option<&PreparedGeometry>,
        options: &crate::spec::SelfShieldingOptions,
        composition_atoms: &BTreeMap<(i32, i32), f64>,
        boundaries_ev: &[f64],
        temperature_K: f64,
        require_complete: bool,
    ) -> Result<ShieldPlan, String> {
        match (table, geometry) {
            (Some(table), None) => table.plan_with_overrides(
                composition_atoms,
                &options.dilution,
                options.sigma0_b,
                &options.dilution_overrides,
                temperature_K,
                require_complete,
            ),
            (None, Some(geometry)) => {
                geometry.plan(composition_atoms, boundaries_ev, require_complete)
            }
            _ => Err("self_shielding needs exactly one of table or geometry".into()),
        }
    }

    /// The per-group scale map for one library row, or None when the row's
    /// target is uncovered. Callers multiply `sigma_g * scale(g)` inside the
    /// group fold; groups absent from the map scale by 1.
    pub fn row_scales(&self, target_za: i32, target_liso: i32, mt: i32) -> Option<&GroupScales> {
        let nuc = self.covered.get(&(target_za, target_liso))?;
        let map = &nuc.scales[channel_of_mt(mt)];
        (!map.is_empty()).then_some(map)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn table_json(factors: serde_json::Value) -> String {
        serde_json::json!({
            "format": "actinv-shield-table-1",
            "generator": "test",
            "source": {"citation": "t", "edition": "t", "url": "t"},
            "method": {},
            "sigma0_b": [1e10, 1e3, 1e2, 1e1, 1.0, 0.1],
            "temperatures_K": [293.6, 600.0, 900.0, 1200.0],
            "group_structure": {"name": "test", "boundaries_eV": [1.0, 100.0, 1.0e4]},
            "files": [],
            "uncovered": [],
            "nuclides": {
                "W186": {
                    "za": 74186, "liso": 0,
                    "unresolved_ranges_ev": [[100.0, 1.0e4]],
                    "nodes": [],
                    "groups": [{
                        "group": 1, "overlap_fraction": 0.5, "sigma_p_b": 9.0,
                        "infinite_dilution_b": [10.0, 9.0, 0.0, 1.0],
                        "factors": factors,
                        "shielded_b": {}
                    }]
                }
            }
        })
        .to_string()
    }

    fn uniform_factors(v: f64) -> serde_json::Value {
        let rows: Vec<Vec<f64>> = vec![vec![v; 4]; 6];
        serde_json::json!({
            "total": rows, "elastic": rows, "fission": rows, "capture": rows
        })
    }

    #[test]
    fn composition_dilution_and_overlap_blend() {
        let table = PreparedShieldTable::from_json(&table_json(uniform_factors(0.5))).unwrap();
        // Pure W186: no dilution partners -> sigma0 = 0 -> clamps to grid floor
        // (0.1) whose factor is 0.5 -> scale = 0.5 + 0.5*0.5 = 0.75.
        let atoms = BTreeMap::from([((74186, 0), 1.0e22)]);
        let plan = table
            .plan(&atoms, "composition", None, 293.6, false)
            .unwrap();
        let scale = plan.row_scales(74186, 0, 102).unwrap()[&1];
        assert!((scale - 0.75).abs() < 1e-12);
        // Outside the covered group the scale is the identity.
        assert!(plan.row_scales(74186, 0, 102).unwrap().get(&0).is_none());
    }

    #[test]
    fn fixed_dilution_applies_user_sigma0() {
        let table = PreparedShieldTable::from_json(&table_json(uniform_factors(0.5))).unwrap();
        let atoms = BTreeMap::from([((74186, 0), 1.0e22)]);
        let plan = table
            .plan(&atoms, "fixed", Some(1e3), 293.6, false)
            .unwrap();
        assert_eq!(plan.sigma0_eff_b["W186"], 1e3);
    }

    #[test]
    fn uncovered_composition_members_are_named() {
        let table = PreparedShieldTable::from_json(&table_json(uniform_factors(0.5))).unwrap();
        let atoms = BTreeMap::from([((74186, 0), 1.0), ((25055, 0), 1.0)]);
        let plan = table
            .plan(&atoms, "composition", None, 293.6, false)
            .unwrap();
        assert_eq!(plan.uncovered_targets, vec!["Mn55".to_string()]);
        assert!(table
            .plan(&atoms, "composition", None, 293.6, true)
            .is_err());
    }

    #[test]
    fn factor_interpolates_in_ln_sigma0() {
        // sigma0 grid [1e10,1e3,1e2,10,1,0.1]: capture f=0.8 at 1e3, 0.4 at 1e2.
        // sigma0 = sqrt(1e3*1e2) = 1e2.5 sits at the ln midpoint -> f = 0.6,
        // scale = (1-0.5) + 0.5*0.6 = 0.8.
        let mut factors = uniform_factors(1.0);
        factors["capture"][1] = serde_json::json!(vec![0.8; 4]);
        factors["capture"][2] = serde_json::json!(vec![0.4; 4]);
        let table = PreparedShieldTable::from_json(&table_json(factors)).unwrap();
        let atoms = BTreeMap::from([((74186, 0), 1.0e22)]);
        let plan = table
            .plan(&atoms, "fixed", Some(10f64.powf(2.5)), 293.6, false)
            .unwrap();
        let scale = plan.row_scales(74186, 0, 102).unwrap()[&1];
        assert!((scale - 0.8).abs() < 1e-9, "scale {scale}");
        // The total channel stays at its uniform factor.
        assert_eq!(plan.row_scales(74186, 0, 11).unwrap()[&1], 1.0);
    }

    #[test]
    fn factor_weights_follow_the_nearer_sigma0_row() {
        // The midpoint test above cannot tell w from 1-w. Exact grid points and
        // off-midpoint queries can: capture f=0.8 at 1e3, 0.4 at 1e2, 1.0 at 1e10.
        let mut factors = uniform_factors(1.0);
        factors["capture"][1] = serde_json::json!(vec![0.8; 4]);
        factors["capture"][2] = serde_json::json!(vec![0.4; 4]);
        let table = PreparedShieldTable::from_json(&table_json(factors)).unwrap();
        let atoms = BTreeMap::from([((74186, 0), 1.0e22)]);
        let scale_at = |sigma0: f64| {
            let plan = table
                .plan(&atoms, "fixed", Some(sigma0), 293.6, false)
                .unwrap();
            plan.row_scales(74186, 0, 102).unwrap()[&1]
        };
        // scale = (1-0.5) + 0.5*f with overlap fraction 0.5.
        for (sigma0, f) in [
            (1e3, 0.8),                                   // exact interior grid point
            (1e2, 0.4),                                   // exact interior grid point
            (10f64.powf(2.75), 0.8 + 0.25 * (0.4 - 0.8)), // nearer 1e3
            (10f64.powf(2.25), 0.8 + 0.75 * (0.4 - 0.8)), // nearer 1e2
            (1e5, 1.0 + (5.0 / 7.0) * (0.8 - 1.0)),       // first interval, ln-weighted
        ] {
            let scale = scale_at(sigma0);
            assert!(
                (scale - (0.5 + 0.5 * f)).abs() < 1e-12,
                "sigma0 {sigma0}: scale {scale}, expected {}",
                0.5 + 0.5 * f
            );
        }
    }

    #[test]
    fn factor_interpolates_in_sqrt_temperature() {
        // capture: 1.0 at 293.6 K, 0.6 at 600 K; T chosen at the sqrt midpoint.
        let mut factors = uniform_factors(1.0);
        for row in factors["capture"].as_array_mut().unwrap() {
            row[0] = serde_json::json!(1.0);
            row[1] = serde_json::json!(0.6);
        }
        let table = PreparedShieldTable::from_json(&table_json(factors)).unwrap();
        let sqrt_mid = ((293.6_f64.sqrt() + 600.0_f64.sqrt()) / 2.0).powi(2);
        let atoms = BTreeMap::from([((74186, 0), 1.0e22)]);
        let plan = table
            .plan(&atoms, "fixed", Some(1e10), sqrt_mid, false)
            .unwrap();
        // f = 0.8 at the sqrt midpoint -> scale = 0.5 + 0.5*0.8 = 0.9.
        let scale = plan.row_scales(74186, 0, 102).unwrap()[&1];
        assert!((scale - 0.9).abs() < 1e-9, "scale {scale}");
    }

    #[test]
    fn group_factors_supersede_the_flat_blend() {
        // With group_factors present, the applied scale is the full-group
        // Bondarenko factor directly, not (1-c)+c*f.
        let mut doc: serde_json::Value =
            serde_json::from_str(&table_json(uniform_factors(0.5))).unwrap();
        let group = &mut doc["nuclides"]["W186"]["groups"][0];
        let gfac = uniform_factors(0.2);
        group["group_factors"] = gfac;
        group["background_b"] = serde_json::json!([15.0, 14.0, 0.0, 0.3]);
        group["weight_mean"] = serde_json::json!(vec![vec![0.1; 4]; 6]);
        group["group_unshielded_b"] = serde_json::json!([10.0, 9.0, 0.0, 1.0]);
        group["group_shielded_b"] = uniform_factors(0.5);
        let table = PreparedShieldTable::from_json(&doc.to_string()).unwrap();
        let atoms = BTreeMap::from([((74186, 0), 1.0e22)]);
        let plan = table
            .plan(&atoms, "composition", None, 293.6, false)
            .unwrap();
        // capture: group_factor 0.2 applies directly (flat blend gave 0.75).
        let scale = plan.row_scales(74186, 0, 102).unwrap()[&1];
        assert!((scale - 0.2).abs() < 1e-12, "scale {scale}");
        // The ledger still records the segment factor 0.5.
        assert!((plan.applied["W186"][&1][3] - 0.5).abs() < 1e-12);
    }

    #[test]
    fn factor_clamps_at_grid_ends() {
        let mut factors = uniform_factors(1.0);
        factors["capture"][0] = serde_json::json!(vec![0.9; 4]); // sigma0 = 1e10
        factors["capture"][5] = serde_json::json!(vec![0.2; 4]); // sigma0 = 0.1
        let table = PreparedShieldTable::from_json(&table_json(factors)).unwrap();
        let atoms = BTreeMap::from([((74186, 0), 1.0e22)]);
        // Beyond the grid: sigma0 = 1e12 clamps to the 1e10 row -> 0.95.
        let plan = table
            .plan(&atoms, "fixed", Some(1e12), 293.6, false)
            .unwrap();
        assert!((plan.row_scales(74186, 0, 102).unwrap()[&1] - 0.95).abs() < 1e-12);
        // Below the floor: sigma0 = 1e-9 clamps to the 0.1 row -> 0.6.
        let plan = table
            .plan(&atoms, "fixed", Some(1e-9), 293.6, false)
            .unwrap();
        assert!((plan.row_scales(74186, 0, 102).unwrap()[&1] - 0.6).abs() < 1e-12);
    }

    #[test]
    fn malformed_tables_are_rejected() {
        // Wrong format marker.
        let bad = table_json(uniform_factors(0.5)).replace("actinv-shield-table-1", "bogus-format");
        assert!(PreparedShieldTable::from_json(&bad).is_err());
        // Overlap fraction outside [0,1].
        let mut bad_overlap = table_json(uniform_factors(0.5));
        bad_overlap = bad_overlap.replace("0.5", "1.5");
        assert!(PreparedShieldTable::from_json(&bad_overlap).is_err());
        // Elemental nuclide key.
        let bad_key = table_json(uniform_factors(0.5)).replace("W186", "W");
        assert!(PreparedShieldTable::from_json(&bad_key).is_err());
    }

    #[test]
    fn dilution_override_bypasses_the_computed_sigma0() {
        // capture f = 0.4 at sigma0 = 1e2, 1.0 elsewhere on the grid floor row.
        let mut factors = uniform_factors(1.0);
        factors["capture"][2] = serde_json::json!(vec![0.4; 4]);
        let table = PreparedShieldTable::from_json(&table_json(factors)).unwrap();
        let atoms = BTreeMap::from([((74186, 0), 1.0e22)]);
        // Pure W186 computes sigma0 = 0 (grid floor, f = 1): scale 1.
        let base = table
            .plan(&atoms, "composition", None, 293.6, false)
            .unwrap();
        assert_eq!(base.row_scales(74186, 0, 102).unwrap()[&1], 1.0);
        assert!(base.dilution_overrides.is_empty());
        // The user dilution 1e2 in group 1 replaces it: scale = 0.5 + 0.5*0.4.
        let overrides = BTreeMap::from([("W186".to_string(), BTreeMap::from([(1usize, 1e2)]))]);
        let plan = table
            .plan_with_overrides(&atoms, "composition", None, &overrides, 293.6, false)
            .unwrap();
        let scale = plan.row_scales(74186, 0, 102).unwrap()[&1];
        assert!((scale - 0.7).abs() < 1e-12, "scale {scale}");
        // The computed effective dilution is still reported; the override is named.
        assert_eq!(plan.sigma0_eff_b["W186"], 0.0);
        assert_eq!(plan.dilution_overrides["W186"][&1], 1e2);
        // The override also wins over a fixed dilution.
        let plan = table
            .plan_with_overrides(&atoms, "fixed", Some(1e10), &overrides, 293.6, false)
            .unwrap();
        assert!((plan.row_scales(74186, 0, 102).unwrap()[&1] - 0.7).abs() < 1e-12);
    }

    #[test]
    fn dilution_override_names_must_be_covered() {
        let table = PreparedShieldTable::from_json(&table_json(uniform_factors(0.5))).unwrap();
        let atoms = BTreeMap::from([((74186, 0), 1.0e22)]);
        let unknown = BTreeMap::from([("W184".to_string(), BTreeMap::from([(1usize, 1e2)]))]);
        let error = table
            .plan_with_overrides(&atoms, "composition", None, &unknown, 293.6, false)
            .unwrap_err();
        assert!(error.contains("'W184'"), "{error}");
        let bad_group = BTreeMap::from([("W186".to_string(), BTreeMap::from([(7usize, 1e2)]))]);
        let error = table
            .plan_with_overrides(&atoms, "composition", None, &bad_group, 293.6, false)
            .unwrap_err();
        assert!(error.contains("group 7"), "{error}");
    }

    #[test]
    fn empty_overrides_leave_the_plan_unchanged() {
        let table = PreparedShieldTable::from_json(&table_json(uniform_factors(0.5))).unwrap();
        let atoms = BTreeMap::from([((74186, 0), 1.0e22)]);
        let plain = table
            .plan(&atoms, "composition", None, 293.6, false)
            .unwrap();
        let empty = table
            .plan_with_overrides(&atoms, "composition", None, &BTreeMap::new(), 293.6, false)
            .unwrap();
        assert_eq!(
            plain.row_scales(74186, 0, 102).unwrap(),
            empty.row_scales(74186, 0, 102).unwrap()
        );
        assert_eq!(plain.applied, empty.applied);
        assert!(plain.dilution_overrides.is_empty() && plain.geometry.is_none());
    }

    #[test]
    fn sigmoid_limits_and_monotone_decrease() {
        // Thin limit: G -> A1 = 1.
        assert_eq!(sigmoid_factor(0.0), 1.0);
        assert!((sigmoid_factor(1e-12) - 1.0).abs() < 1e-9);
        // Thick limit: G -> A2 = 0.06.
        assert!((sigmoid_factor(1e30) - SIGMOID_A2).abs() < 1e-6);
        // At z = z0 the sigmoid is at its midpoint: (A1 - A2)/2 + A2 = 0.53.
        assert!((sigmoid_factor(SIGMOID_Z0) - 0.53).abs() < 1e-12);
        let mut previous = sigmoid_factor(0.0);
        for exponent in -6..=12 {
            let g = sigmoid_factor(10f64.powf(f64::from(exponent) / 2.0));
            assert!(g < previous, "G must fall with z: {g} !< {previous}");
            assert!((SIGMOID_A2..=SIGMOID_A1).contains(&g));
            previous = g;
        }
    }

    #[test]
    fn effective_lengths_follow_the_manual_table() {
        let y = |shape: GeometryShape, length: f64, height: Option<f64>| {
            shape.effective_length_cm(length, height).unwrap()
        };
        assert!((y(GeometryShape::Foil, 0.8, None) - 1.2).abs() < 1e-15);
        assert!((y(GeometryShape::Wire, 0.5, None) - 1.0).abs() < 1e-15);
        assert!((y(GeometryShape::Sphere, 0.5, None) - 0.5).abs() < 1e-15);
        // 1.65 r h / (r + h) with r = 1, h = 3.
        assert!((y(GeometryShape::Cylinder, 1.0, Some(3.0)) - 1.2375).abs() < 1e-15);
        for bad in [0.0, -1.0, f64::NAN, f64::INFINITY] {
            assert!(GeometryShape::Foil.effective_length_cm(bad, None).is_err());
            assert!(GeometryShape::Cylinder
                .effective_length_cm(1.0, Some(bad))
                .is_err());
        }
        assert!(GeometryShape::Cylinder
            .effective_length_cm(1.0, None)
            .unwrap_err()
            .contains("height_cm"));
        assert!(GeometryShape::Sphere
            .effective_length_cm(1.0, Some(1.0))
            .is_err());
        assert!(GeometryShape::parse("slab").is_err());
        assert_eq!(GeometryShape::parse("wire").unwrap(), GeometryShape::Wire);
    }

    /// W184-like toy: two resonances in library group 1 ([100 eV, 1e4 eV)),
    /// one below the structure.
    fn toy_geometry(length_cm: f64, capture_only: bool) -> PreparedGeometry {
        let line = |energy: f64, gamma_n: f64, gamma_g: f64| {
            ResonanceLine::from_widths(energy, 0.5, 0.0, 183.0, gamma_n, gamma_g, gamma_n + gamma_g)
                .unwrap()
        };
        PreparedGeometry {
            shape: GeometryShape::Foil,
            length_cm,
            height_cm: None,
            effective_length_cm: 1.5 * length_cm,
            density_g_cm3: 19.3,
            capture_only,
            choose: Vec::new(),
            lines: BTreeMap::from([(
                (74184, 0),
                vec![
                    line(0.5, 1e-3, 5e-2),
                    line(200.0, 2e-3, 6e-2),
                    line(900.0, 5e-3, 4e-2),
                ],
            )]),
        }
    }

    #[test]
    fn geometry_factor_tends_to_one_when_thin_and_falls_with_thickness() {
        let atoms = BTreeMap::from([((74184, 0), 3.0e21)]);
        let bounds = [1.0, 100.0, 1.0e4];
        let factor = |length: f64| {
            let plan = toy_geometry(length, false)
                .plan(&atoms, &bounds, false)
                .unwrap();
            plan.row_scales(74184, 0, 102).unwrap()[&1]
        };
        assert!((factor(1e-12) - 1.0).abs() < 1e-6);
        let (thin, mid, thick) = (factor(1e-4), factor(1e-2), factor(1.0));
        assert!(thin > mid && mid > thick, "{thin} {mid} {thick}");
        assert!(thick > SIGMOID_A2 && thin < 1.0);
    }

    #[test]
    fn geometry_group_factor_is_the_weighted_sigmoid_mean() {
        let geometry = toy_geometry(0.01, false);
        let atoms = BTreeMap::from([((74184, 0), 3.0e21)]);
        let plan = geometry.plan(&atoms, &[1.0, 100.0, 1.0e4], false).unwrap();
        let lines = &geometry.lines[&(74184, 0)];
        let n = 3.0e21 * 19.3;
        // Only the 200 and 900 eV resonances fall inside the structure; the
        // 0.5 eV one lies below its first boundary.
        let (mut num, mut den) = (0.0, 0.0);
        for line in &lines[1..] {
            num += line.weight * sigmoid_factor(line.z(n, 0.015));
            den += line.weight;
        }
        let want = num / den;
        let got = plan.row_scales(74184, 0, 102).unwrap()[&1];
        assert!((got - want).abs() < 1e-15, "{got} vs {want}");
        assert!(plan.row_scales(74184, 0, 102).unwrap().get(&0).is_none());
        let report = plan.geometry.as_ref().unwrap();
        let rows = &report.factors["W184"];
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].group, 1);
        assert_eq!(rows[0].resonances, 2);
        assert_eq!(
            (rows[0].energy_min_eV, rows[0].energy_max_eV),
            (100.0, 1.0e4)
        );
        assert!((rows[0].factor - want).abs() < 1e-15);
        assert_eq!(plan.dilution, "geometry");
        assert!((report.effective_length_cm - 0.015).abs() < 1e-15);
        assert!((report.number_density_per_cm3["W184"] - n).abs() < 1e-9 * n);
    }

    #[test]
    fn geometry_reaction_scope_and_selection() {
        let atoms = BTreeMap::from([((74184, 0), 3.0e21), ((25055, 0), 1.0e21)]);
        let bounds = [1.0, 100.0, 1.0e4];
        // All reactions: elastic and capture both scale.
        let all = toy_geometry(0.01, false)
            .plan(&atoms, &bounds, false)
            .unwrap();
        assert!(all.row_scales(74184, 0, 2).is_some());
        assert!(all.row_scales(74184, 0, 102).is_some());
        // Mn55 has no resonance data: named uncovered, untouched.
        assert_eq!(all.uncovered_targets, vec!["Mn55".to_string()]);
        assert!(all.row_scales(25055, 0, 102).is_none());
        assert!(toy_geometry(0.01, false)
            .plan(&atoms, &bounds, true)
            .is_err());
        // Capture only: MT 102 scales, other channels do not.
        let capture = toy_geometry(0.01, true)
            .plan(&atoms, &bounds, false)
            .unwrap();
        assert!(capture.row_scales(74184, 0, 2).is_none());
        assert!(capture.row_scales(74184, 0, 16).is_none());
        assert_eq!(
            capture.row_scales(74184, 0, 102).unwrap(),
            all.row_scales(74184, 0, 102).unwrap()
        );
        assert_eq!(capture.applied["W184"][&1][0], 1.0);
        // Choosing a nuclide outside the material is an error.
        let mut chosen = toy_geometry(0.01, false);
        chosen.choose = vec![("W186".to_string(), ChooseEntry::Nuclide(74186, 0))];
        let error = chosen.plan(&atoms, &bounds, false).unwrap_err();
        assert!(error.contains("W186"), "{error}");
        // Choosing the element selects its covered isotope and leaves Mn alone.
        chosen.choose = vec![("W".to_string(), ChooseEntry::Element(74))];
        let plan = chosen.plan(&atoms, &bounds, false).unwrap();
        assert!(plan.uncovered_targets.is_empty());
        assert!(plan.row_scales(74184, 0, 102).is_some());
    }

    #[test]
    fn geometry_rejects_bad_boundaries_and_unusable_files() {
        let atoms = BTreeMap::from([((74184, 0), 3.0e21)]);
        assert!(toy_geometry(0.01, false)
            .plan(&atoms, &[100.0, 1.0], false)
            .is_err());
        // Bound levels and resonances without a positive neutron or capture
        // width are not usable lines.
        assert!(ResonanceLine::from_widths(-1.0, 0.5, 0.0, 183.0, 1e-3, 1e-2, 1.1e-2).is_none());
        assert!(ResonanceLine::from_widths(5.0, 0.5, 0.0, 183.0, 0.0, 1e-2, 1e-2).is_none());
        assert!(ResonanceLine::from_widths(5.0, 0.5, 0.0, 183.0, 1e-3, 0.0, 1e-3).is_none());
        // Malformed ENDF text fails with the file label in the message.
        let geometry = crate::spec::SelfShieldingGeometry {
            shape: "foil".into(),
            length_cm: 0.01,
            height_cm: None,
            density_g_cm3: 19.3,
            resonance_files: Vec::new(),
            choose: None,
            reactions: "all".into(),
        };
        let error = PreparedGeometry::from_evaluations(
            &geometry,
            &[("w184.endf".to_string(), "not an ENDF tape".to_string())],
        )
        .unwrap_err();
        assert!(error.contains("w184.endf"), "{error}");
        let mut bad = geometry.clone();
        bad.length_cm = f64::NAN;
        assert!(PreparedGeometry::from_evaluations(&bad, &[]).is_err());
        let mut bad = geometry;
        bad.reactions = "elastic".into();
        assert!(PreparedGeometry::from_evaluations(&bad, &[]).is_err());
    }

    #[test]
    fn resonance_peak_cross_section_scales_inversely_with_energy() {
        // Same widths and spins: sigma_peak = 4 pi/k^2 g Gn/G with k^2 ~ E.
        let a = ResonanceLine::from_widths(10.0, 0.5, 0.0, 100.0, 1e-3, 1e-2, 1.1e-2).unwrap();
        let b = ResonanceLine::from_widths(40.0, 0.5, 0.0, 100.0, 1e-3, 1e-2, 1.1e-2).unwrap();
        assert!((a.peak_b / b.peak_b - 4.0).abs() < 1e-12);
        // g = (2J+1)/(2(2I+1)) = 1 for J = 1/2, I = 0.
        let wave = K_WAVE * 100.0 / 101.0 * 10.0_f64.sqrt();
        let want = 4.0 * std::f64::consts::PI / (wave * wave) * (1e-3 / 1.1e-2);
        assert!((a.peak_b - want).abs() < 1e-9 * want);
        assert!((a.capture_root - (1e-2_f64 / 1.1e-2).sqrt()).abs() < 1e-12);
    }

    fn test_evaluation(data: RangeData) -> ResonanceEvaluation {
        use actinv_data::resonance::{ResonanceIsotope, ResonanceRange};
        ResonanceEvaluation {
            za: 74183,
            awr: 181.0,
            isotopes: vec![ResonanceIsotope {
                zai: 74183,
                abundance: 1.0,
                fission_widths: false,
                ranges: vec![ResonanceRange {
                    energy_min: 1e-5,
                    energy_max: 1e4,
                    lru: 1,
                    lrf: 7,
                    naps: 1,
                    scattering_radius: None,
                    data,
                }],
            }],
        }
    }

    /// One spin group of J=2 on a spin-3/2 target (A=181): capture, neutron
    /// (l=0, radius 0.7) and fission channels.
    fn rml_test_evaluation(
        reduced_widths: bool,
        resonances: Vec<actinv_data::resonance::RmlResonance>,
    ) -> ResonanceEvaluation {
        use actinv_data::resonance::{ParticlePair, RMatrixLimited, RmlChannel, SpinGroup};
        let pair = |mass_a: f64, mass_b: f64, mt: i32, penetrability: i32| ParticlePair {
            mass_a,
            mass_b,
            za: 0,
            zb: 74,
            spin_a: if mt == 2 { 0.5 } else { 0.0 },
            spin_b: if mt == 2 { 1.5 } else { 0.0 },
            q_value: 0.0,
            penetrability,
            shift: 0,
            mt,
            parity_a: 1,
            parity_b: 1,
        };
        let channel = |pair: usize, radius: f64| RmlChannel {
            pair,
            l: 0,
            spin: 0.0,
            boundary: 0.0,
            effective_radius: radius,
            true_radius: radius,
        };
        test_evaluation(RangeData::RMatrixLimited(RMatrixLimited {
            reduced_widths,
            krm: 3,
            particle_pairs: vec![
                pair(0.0, 182.0, 102, 0),
                pair(1.0, 181.0, 2, 1),
                pair(0.0, 182.0, 18, 0),
            ],
            spin_groups: vec![SpinGroup {
                spin: 2.0,
                parity: 1.0,
                channels: vec![channel(0, 0.0), channel(1, 0.7), channel(2, 0.0)],
                resonances,
                backgrounds: Vec::new(),
                phase_shifts: Vec::new(),
            }],
        }))
    }

    fn assert_line_close(got: &ResonanceLine, want: &ResonanceLine) {
        for (g, w) in [
            (got.energy_ev, want.energy_ev),
            (got.peak_b, want.peak_b),
            (got.capture_root, want.capture_root),
            (got.weight, want.weight),
        ] {
            assert!((g - w).abs() <= 1e-12 * w.abs(), "{g} vs {w}");
        }
    }

    #[test]
    fn rml_lines_match_the_equivalent_breit_wigner_lines() {
        use actinv_data::resonance::{LegacyLGroup, LegacyResolved, LegacyResonance, RmlResonance};
        // Neutron 0.4, capture 0.02, fission 0.05 (listed with a sign); J=2 on
        // a target of spin 3/2 gives g = 5/8, not the I=0 value.
        let resonance = |energy: f64, widths: [f64; 3]| RmlResonance {
            energy,
            widths: widths.to_vec(),
        };
        let evaluation = rml_test_evaluation(
            false,
            vec![
                resonance(10.0, [0.02, 0.4, -0.05]),
                // A bound level, a resonance without capture and one without
                // a neutron width are not lines.
                resonance(-3.0, [0.02, 0.4, 0.0]),
                resonance(20.0, [0.0, 0.4, 0.05]),
                resonance(30.0, [0.02, 0.0, 0.05]),
                resonance(40.0, [0.01, 0.9, 0.0]),
            ],
        );
        let lines = resolved_lines(&evaluation, 74183).unwrap();
        assert_eq!(lines.len(), 2);
        let legacy = |energy: f64, neutron: f64, capture: f64, fission: f64| LegacyResonance {
            energy,
            spin: 2.0,
            total: neutron + capture + fission,
            neutron,
            capture,
            fission_a: fission,
            fission_b: 0.0,
        };
        let breit_wigner = test_evaluation(RangeData::BreitWigner(LegacyResolved {
            spin: 1.5,
            ap: 0.7,
            groups: vec![LegacyLGroup {
                awri: 181.0,
                apl: 0.7,
                qx: 0.0,
                l: 0,
                lrx: 0,
                resonances: vec![legacy(10.0, 0.4, 0.02, 0.05), legacy(40.0, 0.9, 0.01, 0.0)],
            }],
        }));
        let expected = resolved_lines(&breit_wigner, 74183).unwrap();
        assert_eq!(expected.len(), 2);
        for (got, want) in lines.iter().zip(&expected) {
            assert_line_close(got, want);
        }
        // g = (2J+1)/(2(2I+1)) = 5/8 rather than the I=0 value.
        let wave = K_WAVE * 181.0 / 182.0 * 10.0_f64.sqrt();
        let want = 4.0 * std::f64::consts::PI / (wave * wave) * 0.625 * (0.4 / 0.47);
        assert!((lines[0].peak_b - want).abs() <= 1e-12 * want);
    }

    #[test]
    fn rml_reduced_widths_give_the_same_lines_as_listed_widths() {
        use actinv_data::resonance::RmlResonance;
        // Neutron l=0 channel with true radius 0.7: P = rho = k a.
        let widths = [(10.0, [0.02, 0.4, 0.05]), (250.0, [0.03, 0.8, 0.0])];
        let listed = rml_test_evaluation(
            false,
            widths
                .iter()
                .map(|&(energy, widths)| RmlResonance {
                    energy,
                    widths: widths.to_vec(),
                })
                .collect(),
        );
        let reduced = rml_test_evaluation(
            true,
            widths
                .iter()
                .map(|&(energy, widths)| {
                    let wave = K_WAVE * 181.0 / 182.0 * f64::sqrt(energy);
                    let penetrability = wave * 0.7;
                    RmlResonance {
                        energy,
                        widths: vec![
                            (widths[0] / 2.0).sqrt(),
                            (widths[1] / (2.0 * penetrability)).sqrt(),
                            (widths[2] / 2.0).sqrt(),
                        ],
                    }
                })
                .collect(),
        );
        let from_listed = resolved_lines(&listed, 74183).unwrap();
        let from_reduced = resolved_lines(&reduced, 74183).unwrap();
        assert_eq!(from_listed.len(), 2);
        assert_eq!(from_reduced.len(), 2);
        for (got, want) in from_reduced.iter().zip(&from_listed) {
            assert_line_close(got, want);
        }
    }

    #[test]
    fn rml_range_with_an_unclassifiable_channel_is_an_error() {
        use actinv_data::resonance::RmlResonance;
        let mut evaluation = rml_test_evaluation(
            false,
            vec![RmlResonance {
                energy: 10.0,
                widths: vec![0.02, 0.4, 0.05],
            }],
        );
        let RangeData::RMatrixLimited(rml) = &mut evaluation.isotopes[0].ranges[0].data else {
            unreachable!("fixture is R-matrix limited");
        };
        rml.spin_groups[0].channels[2].pair = 7;
        let error = resolved_lines(&evaluation, 74183).unwrap_err();
        assert!(error.contains("LRF=7"), "{error}");
        assert!(error.contains("particle pair 8"), "{error}");
    }

    // ---- P140 regressions (SG-01, SG-03, SG-07): they assert the intended behaviour ----

    /// The cards of one ENDF-6 section: six right-aligned 11-column fields per record, then SEND.
    fn p140_section(mat: i32, mf: i32, mt: i32, records: &[Vec<String>]) -> Vec<String> {
        let mut lines: Vec<String> = records
            .iter()
            .enumerate()
            .map(|(index, record)| {
                let mut fields = record.clone();
                fields.resize(6, "0".to_string());
                let body: String = fields.iter().map(|field| format!("{field:>11}")).collect();
                format!("{body}{mat:>4}{mf:>2}{mt:>3}{:>5}", index + 1)
            })
            .collect();
        lines.push(format!("{:66}{mat:>4}{mf:>2}{:>3}{:>5}", "", 0, 99_999));
        lines
    }

    /// One MF=2 isotope: its ZAI and ranges, each range (EL, EH, levels as [E, J, GN, GG]).
    type P140Isotope = (i32, Vec<(f64, f64, Vec<[f64; 4]>)>);

    /// A neutron ENDF tape (one MAT) whose MF=2/MT=151 holds one single-level Breit-Wigner
    /// L-group per range of every isotope (target spin 0, no fission width).
    fn p140_tape(mat: i32, za: i32, awr: f64, isotopes: &[P140Isotope]) -> String {
        let num = |value: f64| format!("{value:.6e}");
        let count = |value: usize| value.to_string();
        let mf1 = vec![
            vec![za.to_string(), num(awr)],
            vec![],
            vec![
                num(1.0),
                num(2.0e7),
                "0".into(),
                "0".into(),
                "10".into(),
                "6".into(),
            ],
            vec![],
        ];
        let mut mf2 = vec![vec![
            za.to_string(),
            num(awr),
            "0".into(),
            "0".into(),
            count(isotopes.len()),
            "0".into(),
        ]];
        for (zai, ranges) in isotopes {
            mf2.push(vec![
                zai.to_string(),
                num(1.0),
                "0".into(),
                "0".into(),
                count(ranges.len()),
                "0".into(),
            ]);
            for (low, high, rows) in ranges {
                mf2.push(vec![
                    num(*low),
                    num(*high),
                    "1".into(),
                    "1".into(),
                    "0".into(),
                    "0".into(),
                ]);
                mf2.push(vec![
                    num(0.0),
                    num(0.7),
                    "0".into(),
                    "0".into(),
                    "1".into(),
                    "0".into(),
                ]);
                mf2.push(vec![
                    num(awr),
                    num(0.0),
                    "0".into(),
                    "0".into(),
                    count(6 * rows.len()),
                    count(rows.len()),
                ]);
                for [energy, spin, neutron, capture] in rows {
                    mf2.push(vec![
                        num(*energy),
                        num(*spin),
                        num(neutron + capture),
                        num(*neutron),
                        num(*capture),
                        num(0.0),
                    ]);
                }
            }
        }
        let mut lines = p140_section(mat, 1, 451, &mf1);
        lines.extend(p140_section(mat, 2, 151, &mf2));
        lines.join("\n")
    }

    fn p140_geometry_spec() -> crate::spec::SelfShieldingGeometry {
        serde_json::from_value(serde_json::json!({
            "shape": "foil",
            "length_cm": 0.01,
            "density_g_cm3": 19.3,
            "resonance_files": [{"path": "w.endf", "sha256": "0".repeat(64)}],
        }))
        .unwrap()
    }

    /// W186 with a resolved range of 1e-5 .. 1e4 eV: levels at 10 and 200 eV lie inside it, the one
    /// at 12 000 eV is an R-matrix pole above the range (like W186's three levels near 10.1-10.4 keV).
    fn p140_w186_geometry() -> PreparedGeometry {
        let tape = p140_tape(
            7437,
            74186,
            183.0,
            &[(
                74186,
                vec![(
                    1e-5,
                    1e4,
                    vec![
                        [10.0, 0.5, 1e-3, 5e-2],
                        [200.0, 0.5, 2e-3, 6e-2],
                        [12_000.0, 0.5, 5e-3, 4e-2],
                    ],
                )],
            )],
        );
        PreparedGeometry::from_evaluations(
            &p140_geometry_spec(),
            &[("w186.endf".to_string(), tape)],
        )
        .expect("the synthetic tape parses")
    }

    #[test]
    fn p140_sg01_resonances_above_the_resolved_range_are_not_shielding_peaks() {
        let geometry = p140_w186_geometry();
        // Expected: the level above EH is clipped away. Current code keeps all three.
        assert_eq!(
            geometry.lines[&(74186, 0)].len(),
            2,
            "a level above the evaluation's EH must not become a shielding line"
        );
        let atoms = BTreeMap::from([((74186, 0), 3.0e21)]);
        // Groups: [1, 100), [100, 1e4), [1e4, 1e5). The 12 000 eV level would land in group 2.
        let plan = geometry
            .plan(&atoms, &[1.0, 100.0, 1.0e4, 1.0e5], false)
            .unwrap();
        let rows = &plan.geometry.as_ref().unwrap().factors["W186"];
        assert!(
            rows.iter().all(|row| row.group != 2),
            "no applied factor may exist above EH: {rows:?}"
        );
        assert_eq!(rows.iter().map(|row| row.resonances).sum::<usize>(), 2);
        assert!(plan.row_scales(74186, 0, 102).unwrap().get(&2).is_none());
    }

    #[test]
    fn p140_sg01_every_arm_keeps_only_levels_inside_its_own_range() {
        use actinv_data::resonance::{
            LegacyLGroup, LegacyResolved, LegacyResonance, ResonanceIsotope, ResonanceRange,
            RmlResonance,
        };
        let legacy_range = |low: f64, high: f64, energies: &[f64], reich_moore: bool| {
            let resolved = LegacyResolved {
                spin: 0.0,
                ap: 0.7,
                groups: vec![LegacyLGroup {
                    awri: 183.0,
                    apl: 0.7,
                    qx: 0.0,
                    l: 0,
                    lrx: 0,
                    resonances: energies
                        .iter()
                        .map(|&energy| LegacyResonance {
                            energy,
                            spin: 0.5,
                            total: 5.1e-2,
                            neutron: 1e-3,
                            capture: 5e-2,
                            fission_a: 0.0,
                            fission_b: 0.0,
                        })
                        .collect(),
                }],
            };
            ResonanceRange {
                energy_min: low,
                energy_max: high,
                lru: 1,
                lrf: if reich_moore { 3 } else { 1 },
                naps: 1,
                scattering_radius: None,
                data: if reich_moore {
                    RangeData::ReichMoore(resolved)
                } else {
                    RangeData::BreitWigner(resolved)
                },
            }
        };
        let rml_range = |low: f64, high: f64, energies: &[f64]| {
            let mut range = rml_test_evaluation(
                false,
                energies
                    .iter()
                    .map(|&energy| RmlResonance {
                        energy,
                        widths: vec![0.02, 0.4, 0.05],
                    })
                    .collect(),
            )
            .isotopes
            .remove(0)
            .ranges
            .remove(0);
            range.energy_min = low;
            range.energy_max = high;
            range
        };
        let evaluation = |ranges: Vec<ResonanceRange>| ResonanceEvaluation {
            za: 74183,
            awr: 181.0,
            isotopes: vec![ResonanceIsotope {
                zai: 74183,
                abundance: 1.0,
                fission_widths: false,
                ranges,
            }],
        };
        // Range 1 spans 1e-5 .. 100 eV, range 2 spans 100 .. 1e4 eV. Each lists one level below its
        // lower edge, one above its upper edge (150 eV belongs to range 2's span, but it is range
        // 1's parameter) and one inside.
        for (arm, ranges) in [
            (
                "Breit-Wigner",
                vec![
                    legacy_range(1e-5, 100.0, &[1e-7, 50.0, 150.0], false),
                    legacy_range(100.0, 1e4, &[500.0, 2.0e4, 20.0], false),
                ],
            ),
            (
                "Reich-Moore",
                vec![
                    legacy_range(1e-5, 100.0, &[1e-7, 50.0, 150.0], true),
                    legacy_range(100.0, 1e4, &[500.0, 2.0e4, 20.0], true),
                ],
            ),
            (
                "R-matrix limited",
                vec![
                    rml_range(1e-5, 100.0, &[1e-7, 50.0, 150.0]),
                    rml_range(100.0, 1e4, &[500.0, 2.0e4, 20.0]),
                ],
            ),
        ] {
            let mut energies: Vec<f64> = resolved_lines(&evaluation(ranges), 74183)
                .unwrap()
                .iter()
                .map(|line| line.energy_ev)
                .collect();
            energies.sort_by(f64::total_cmp);
            assert_eq!(energies, vec![50.0, 500.0], "{arm} arm");
        }
    }

    #[test]
    fn p140_sg01_geometry_ledger_counts_the_levels_it_dropped() {
        let atoms = BTreeMap::from([((74186, 0), 3.0e21)]);
        let plan = p140_w186_geometry()
            .plan(&atoms, &[1.0, 100.0, 1.0e4, 1.0e5], false)
            .unwrap();
        let report = serde_json::to_value(plan.geometry.as_ref().unwrap()).unwrap();
        assert_eq!(
            report["resonances_outside_range"]["W186"], 1,
            "the geometry ledger must count levels clipped to [EL, EH]: {report}"
        );
    }

    #[test]
    fn p140_sg03_choose_element_reports_isotopes_without_files_as_uncovered() {
        let atoms = BTreeMap::from([((74184, 0), 3.0e21), ((74186, 0), 2.0e21)]);
        let bounds = [1.0, 100.0, 1.0e4];
        let mut geometry = toy_geometry(0.01, false);
        // Choosing the element selects W184 (supplied) and W186 (no file): the latter is uncovered,
        // exactly as without `choose`. Current code refuses the whole run.
        geometry.choose = vec![("W".to_string(), ChooseEntry::Element(74))];
        let plan = geometry
            .plan(&atoms, &bounds, false)
            .expect("an element entry must not fail because one isotope has no file");
        assert_eq!(plan.uncovered_targets, vec!["W186".to_string()]);
        assert!(plan.row_scales(74184, 0, 102).is_some());
        assert!(plan.row_scales(74186, 0, 102).is_none());
        // require_shielding_complete still turns the gap into an error.
        let error = geometry.plan(&atoms, &bounds, true).unwrap_err();
        assert!(error.contains("W186"), "{error}");
        // An explicit nuclide entry without a file stays a hard error.
        geometry.choose = vec![("W186".to_string(), ChooseEntry::Nuclide(74186, 0))];
        let error = geometry.plan(&atoms, &bounds, false).unwrap_err();
        assert!(error.contains("W186"), "{error}");
    }

    #[test]
    fn p140_sg07_natural_element_evaluation_is_keyed_by_isotope_or_refused_clearly() {
        // One MAT with ZA 40000 and two MF=2 isotopes, as a JEFF natural-element file has.
        let tape = p140_tape(
            4000,
            40000,
            91.22,
            &[
                (40090, vec![(1e-5, 1e4, vec![[10.0, 0.5, 1e-3, 5e-2]])]),
                (40091, vec![(1e-5, 1e4, vec![[20.0, 0.5, 2e-3, 6e-2]])]),
            ],
        );
        match PreparedGeometry::from_evaluations(
            &p140_geometry_spec(),
            &[("zr.endf".to_string(), tape)],
        ) {
            Err(error) => assert!(
                error.contains("isotope"),
                "a multi-isotope refusal must say so: {error}"
            ),
            Ok(geometry) => {
                assert!(geometry.lines.contains_key(&(40090, 0)));
                assert!(geometry.lines.contains_key(&(40091, 0)));
            }
        }
    }

    #[test]
    fn p140_sg07_multi_isotope_material_does_not_silently_drop_isotopes() {
        // The MAT's ZA equals the first isotope's, so the ad hoc filter keeps only that one.
        let tape = p140_tape(
            4009,
            40090,
            89.13,
            &[
                (40090, vec![(1e-5, 1e4, vec![[10.0, 0.5, 1e-3, 5e-2]])]),
                (40091, vec![(1e-5, 1e4, vec![[20.0, 0.5, 2e-3, 6e-2]])]),
            ],
        );
        match PreparedGeometry::from_evaluations(
            &p140_geometry_spec(),
            &[("zr90.endf".to_string(), tape)],
        ) {
            Err(error) => assert!(error.contains("isotope"), "{error}"),
            Ok(geometry) => {
                assert!(geometry.lines.contains_key(&(40090, 0)));
                assert!(
                    geometry.lines.contains_key(&(40091, 0)),
                    "Zr91 was dropped without a word"
                );
            }
        }
    }

    #[test]
    fn p140_sg07_isotope_zai_differing_from_the_mat_za_is_not_accepted_under_the_mat_name() {
        // MAT ZA 74186 whose single MF=2 isotope is W184: its levels must not be filed under W186.
        let tape = p140_tape(
            7437,
            74186,
            183.0,
            &[(74184, vec![(1e-5, 1e4, vec![[10.0, 0.5, 1e-3, 5e-2]])])],
        );
        match PreparedGeometry::from_evaluations(
            &p140_geometry_spec(),
            &[("w.endf".to_string(), tape)],
        ) {
            Err(_) => {}
            Ok(geometry) => assert!(
                !geometry.lines.contains_key(&(74186, 0)),
                "W184 levels were silently registered as W186"
            ),
        }
    }
}
