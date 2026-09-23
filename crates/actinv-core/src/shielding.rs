#![allow(non_snake_case)] // JSON wire names carry their physical units.
//! Finite-dilution self-shielding (P19 G2): a hash-pinned `actinv-shield-table-1`
//! supplies Bondarenko factors on the frozen sigma0 x temperature grid. The
//! covered nuclide's effective dilution picks the factor at run time; each
//! group's unresolved-range overlap fraction blends it into the group rate.
//!
//! The table is produced by `actinv build-shielding` (crates/actinv-data);
//! this module only consumes it. Factors interpolate linearly in ln(sigma0)
//! and in sqrt(T), clamped at the grid ends.

use actinv_data::composition::{self, MaterialKey};
use serde::Deserialize;
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
            let mut scales: [GroupScales; 4] = Default::default();
            let mut rows = BTreeMap::new();
            for g in &nuc.groups {
                let mut row = [0.0f64; 4];
                for (c, channel) in CHANNELS.iter().enumerate() {
                    let s0 = sigma0.clamp(grid_floor, f64::INFINITY);
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
            let name = name_of(za, liso);
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
        })
    }
}

impl ShieldPlan {
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
}
