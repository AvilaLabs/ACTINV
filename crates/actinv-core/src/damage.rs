#![allow(non_snake_case)] // JSON wire names carry their physical units.
//! Damage observables (P23 G3): hash-pinned `actinv-damage-table-1` folded to NRT dpa.
//!
//! A target row is a per-group damage-energy production cross section in barn·eV, keyed by an
//! explicit nuclide or an element. The fold uses the material's composition-resolved nuclide
//! inventories — the constant reservoir in trace mode, evolved states in coupled mode —
//! never transmutation products. The displacement model is NRT:
//! `dpa_rate = 0.8 * damage_energy_rate_per_atom / (2 * E_d)`.
//!
//! An `actinv-damage-table-2` file (P154, `actinv build-damage --method kinematic`) carries the same total
//! damage energy, so the linear NRT dpa is identical to a table-1 file with those totals. In addition it carries
//! the recoil-integrated exact-NRT and arc-dpa displacement cross sections and the damage-energy upper bound of
//! the uncovered channels, folded into `dpa_nrt`, `dpa_arc` and the uncovered bounds. They were integrated at the
//! displacement energy the table was built with, so a run whose `displacement_energy_eV` differs is refused.

use actinv_data::composition::{self, MaterialKey};
use actinv_data::damage_kinematic::{DamageTable2, TABLE_FORMAT_2, TABLE_UNITS_2};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet, HashMap};

const TABLE_FORMAT: &str = "actinv-damage-table-1";
const TABLE_UNITS: &str = "damage_energy_barn_eV_per_group";
/// NRT viable fraction of damage energy deposited as displacements.
const NRT_KAPPA: f64 = 0.8;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DamageSource {
    pub citation: String,
    pub edition: String,
    pub url: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DamageFileProvenance {
    pub path: String,
    pub sha256: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct TableWire {
    format: String,
    source: DamageSource,
    projectile: String,
    group_structure: String,
    boundaries_eV: Vec<f64>,
    units: String,
    targets: BTreeMap<String, Vec<f64>>,
    #[serde(default)]
    temperature_K: Option<f64>,
    #[serde(default)]
    files: Vec<DamageFileProvenance>,
    #[serde(default)]
    uncovered: Vec<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum TargetSelector {
    /// (za, liso)
    Nuclide(i32, i32),
    /// element Z
    Element(i32),
}

/// The recoil-integrated columns of a table-2 target row.
#[derive(Debug)]
struct RecoilRow {
    nrt: Vec<f64>,
    arc: Option<Vec<f64>>,
    uncovered_upper: Vec<f64>,
}

#[derive(Debug)]
struct TargetRow {
    selector: TargetSelector,
    sigma: Vec<f64>,
    recoil: Option<RecoilRow>,
}

/// What a table-2 file declares beyond the totals.
#[derive(Debug)]
struct Table2Meta {
    /// Element symbol -> the displacement energy the recoil integrals were built with (eV).
    displacement_energy_eV: BTreeMap<String, f64>,
    /// Element symbol -> arc-dpa `(b, c)`.
    arc: BTreeMap<String, (f64, f64)>,
    /// Element symbol -> the `E_d` of the arc-dpa fit, at which the table's arc column was integrated.
    arc_reference_eV: BTreeMap<String, f64>,
    arc_citation: String,
}

#[derive(Debug)]
pub struct PreparedDamageTable {
    source: DamageSource,
    projectile: String,
    group_structure: String,
    boundaries_eV: Vec<f64>,
    temperature_K: Option<f64>,
    files: Vec<DamageFileProvenance>,
    declared_uncovered: Vec<String>,
    targets: BTreeMap<String, TargetRow>,
    table2: Option<Table2Meta>,
}

/// Recoil-integrated quantities of a table-2 fold: exact-NRT and arc-dpa displacements per atom, and the bound on
/// the damage energy of channels the table could not compute.
///
/// `dpa_uncovered_upper_bound` is the damage-energy bound divided by `E_d`: the exact NRT count never exceeds
/// `T_d / E_d` (it is 0 below `E_d`, 1 up to `2.5 E_d`, `0.4 T_d / E_d` above), and the arc-dpa count never
/// exceeds the NRT count at the same `E_d`, so the bound holds for both. The arc column is integrated at the
/// reference `E_d` of its fit, which can be below the NRT `E_d` (W: 70 against 90 eV); the divisor is then the
/// lower of the two, which bounds both counts.
#[derive(Debug, Serialize, Clone, Default)]
pub struct DamageRecoilOut {
    pub dpa_nrt_rate_per_s: f64,
    pub dpa_nrt: f64,
    /// Present only when every covered element has published arc-dpa constants.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub dpa_arc_rate_per_s: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub dpa_arc: Option<f64>,
    pub damage_energy_uncovered_upper_eV_per_g_s: f64,
    pub dpa_uncovered_upper_bound_rate_per_s: f64,
    pub dpa_uncovered_upper_bound: f64,
    /// Cumulative helium (He-3 + He-4) appm over the cumulative linear `dpa`; present with `gas` output and
    /// positive `dpa`.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub he_appm_per_dpa: Option<f64>,
}

#[derive(Debug, Serialize, Clone)]
pub struct DamageElementOut {
    pub atoms_per_g: f64,
    pub damage_energy_eV_per_g_s: f64,
    pub dpa_rate_per_s: f64,
    pub dpa: f64,
    #[serde(flatten)]
    pub recoil: Option<DamageRecoilOut>,
}

#[derive(Debug, Serialize)]
pub struct DamageStepOut {
    pub dpa_rate_per_s: f64,
    pub dpa: f64,
    pub damage_energy_eV_per_g_s: f64,
    pub covered_atom_fraction: f64,
    pub elements: BTreeMap<String, DamageElementOut>,
    #[serde(flatten)]
    pub recoil: Option<DamageRecoilOut>,
}

/// Cumulative recoil-integrated quantities across steps (the caller owns one per run).
#[derive(Debug, Default)]
pub struct RecoilCumulative {
    /// nrt, arc, uncovered bound
    total: [f64; 3],
    elements: BTreeMap<String, [f64; 3]>,
}

impl RecoilCumulative {
    /// Add one step of length `dt` seconds to the running totals and write them into `out`.
    pub fn accumulate(&mut self, out: &mut DamageStepOut, dt: f64) {
        fn add(total: &mut [f64; 3], recoil: &mut DamageRecoilOut, dt: f64) {
            total[0] += recoil.dpa_nrt_rate_per_s * dt;
            recoil.dpa_nrt = total[0];
            if let Some(rate) = recoil.dpa_arc_rate_per_s {
                total[1] += rate * dt;
                recoil.dpa_arc = Some(total[1]);
            }
            total[2] += recoil.dpa_uncovered_upper_bound_rate_per_s * dt;
            recoil.dpa_uncovered_upper_bound = total[2];
        }
        if let Some(recoil) = out.recoil.as_mut() {
            add(&mut self.total, recoil, dt);
        }
        for (name, element) in out.elements.iter_mut() {
            if let Some(recoil) = element.recoil.as_mut() {
                add(self.elements.entry(name.clone()).or_default(), recoil, dt);
            }
        }
    }
}

/// Selector and canonical name of a target key.
fn target_selector(raw: &str) -> Result<(String, TargetSelector), String> {
    let (canonical, selector) = match composition::material_key(raw)
        .map_err(|error| format!("damage table target '{raw}': {error}"))?
    {
        MaterialKey::Nuclide {
            canonical,
            za,
            liso,
            ..
        } => (canonical, TargetSelector::Nuclide(za, liso)),
        MaterialKey::Element(symbol) => {
            let z = composition::z_of(&symbol)
                .ok_or_else(|| format!("damage table target '{raw}': unknown element"))?;
            (symbol, TargetSelector::Element(z))
        }
    };
    if *raw != canonical {
        return Err(format!(
            "damage table target '{raw}' is not canonical; use '{canonical}'"
        ));
    }
    Ok((canonical, selector))
}

#[derive(Deserialize)]
struct FormatProbe {
    format: String,
}

/// Coverage plan fixed once per run: the composition-resolved nuclide set partitioned by element
/// into nuclide-covered and element-row-covered atoms.
#[derive(Debug)]
pub struct DamagePlan {
    /// covered element Z -> displacement energy eV
    pub covered_elements: BTreeMap<i32, f64>,
    /// covered element Z -> where its displacement energy came from: `"spec"` or, for an element a table-2 run
    /// leaves out of `damage.displacement_energy_eV`, `"table"`
    pub displacement_sources: BTreeMap<i32, &'static str>,
    /// uncovered composition nuclides, canonical names
    pub uncovered_targets: Vec<String>,
}

impl DamagePlan {
    /// The spec's displacement energies plus the table's for the covered elements the spec left out.
    pub fn effective_energies(&self, spec: &BTreeMap<String, f64>) -> BTreeMap<String, f64> {
        let mut energies = spec.clone();
        for (z, source) in &self.displacement_sources {
            if *source == "table" {
                energies.insert(
                    composition::symbol_of(*z).to_string(),
                    self.covered_elements[z],
                );
            }
        }
        energies
    }

    /// Covered element symbol -> `"spec"` or `"table"`.
    pub fn energy_sources(&self) -> BTreeMap<String, &'static str> {
        self.displacement_sources
            .iter()
            .map(|(z, source)| (composition::symbol_of(*z).to_string(), *source))
            .collect()
    }
}

impl PreparedDamageTable {
    pub fn from_json(text: &str, spec_projectile: &str) -> Result<Self, String> {
        if serde_json::from_str::<FormatProbe>(text)
            .is_ok_and(|probe| probe.format == TABLE_FORMAT_2)
        {
            return Self::from_table2(text, spec_projectile);
        }
        let table: TableWire =
            serde_json::from_str(text).map_err(|error| format!("damage table: {error}"))?;
        if table.format != TABLE_FORMAT {
            return Err(format!(
                "unsupported damage table format '{}'",
                table.format
            ));
        }
        if table.units != TABLE_UNITS {
            return Err(format!("unsupported damage table units '{}'", table.units));
        }
        for (name, value) in [
            ("citation", &table.source.citation),
            ("edition", &table.source.edition),
            ("url", &table.source.url),
        ] {
            if value.trim().is_empty() {
                return Err(format!("damage table source.{name} must be nonempty"));
            }
        }
        if table.projectile != spec_projectile {
            return Err(format!(
                "damage table projectile '{}' does not match the problem's '{}'",
                table.projectile, spec_projectile
            ));
        }
        if table.boundaries_eV.len() < 2 {
            return Err("damage table needs at least one group".into());
        }
        if table
            .boundaries_eV
            .iter()
            .any(|value| !value.is_finite() || *value <= 0.0)
            || table
                .boundaries_eV
                .windows(2)
                .any(|pair| pair[1] <= pair[0])
        {
            return Err(
                "damage table boundaries_eV must be positive and strictly ascending".into(),
            );
        }
        let mut canonical_keys = BTreeSet::new();
        let mut targets = BTreeMap::new();
        for (raw, sigma) in &table.targets {
            let (canonical, selector) = target_selector(raw)?;
            if !canonical_keys.insert(canonical.clone()) {
                return Err(format!("duplicate damage target '{canonical}'"));
            }
            if sigma.len() + 1 != table.boundaries_eV.len() {
                return Err(format!(
                    "damage target '{canonical}' declares {} group values, expected {}",
                    sigma.len(),
                    table.boundaries_eV.len() - 1
                ));
            }
            if sigma.iter().any(|value| !value.is_finite() || *value < 0.0) {
                return Err(format!(
                    "damage target '{canonical}' contains a nonfinite or negative cross section"
                ));
            }
            targets.insert(
                canonical,
                TargetRow {
                    selector,
                    sigma: sigma.clone(),
                    recoil: None,
                },
            );
        }
        Ok(Self {
            source: table.source,
            projectile: table.projectile,
            group_structure: table.group_structure,
            boundaries_eV: table.boundaries_eV,
            temperature_K: table.temperature_K,
            files: table.files,
            declared_uncovered: table.uncovered,
            targets,
            table2: None,
        })
    }

    /// Read an `actinv-damage-table-2` file: the same totals as table 1 plus the recoil-integrated columns.
    fn from_table2(text: &str, spec_projectile: &str) -> Result<Self, String> {
        let table: DamageTable2 =
            serde_json::from_str(text).map_err(|error| format!("damage table: {error}"))?;
        if table.units != TABLE_UNITS_2 {
            return Err(format!("unsupported damage table units '{}'", table.units));
        }
        if table.method != "kinematic" {
            return Err(format!(
                "unsupported damage table-2 method '{}'",
                table.method
            ));
        }
        for (name, value) in [
            ("citation", &table.source.citation),
            ("edition", &table.source.edition),
            ("url", &table.source.url),
        ] {
            if value.trim().is_empty() {
                return Err(format!("damage table source.{name} must be nonempty"));
            }
        }
        if table.projectile != spec_projectile {
            return Err(format!(
                "damage table projectile '{}' does not match the problem's '{}'",
                table.projectile, spec_projectile
            ));
        }
        if table.boundaries_eV.len() < 2 {
            return Err("damage table needs at least one group".into());
        }
        if table
            .boundaries_eV
            .iter()
            .any(|value| !value.is_finite() || *value <= 0.0)
            || table
                .boundaries_eV
                .windows(2)
                .any(|pair| pair[1] <= pair[0])
        {
            return Err(
                "damage table boundaries_eV must be positive and strictly ascending".into(),
            );
        }
        for (symbol, ed) in &table.displacement.displacement_energy_eV {
            if composition::z_of(symbol).is_none() || !ed.is_finite() || *ed <= 0.0 {
                return Err(format!(
                    "damage table displacement_energy_eV['{symbol}'] must be a positive energy keyed by element symbol"
                ));
            }
        }
        let mut arc = BTreeMap::new();
        let mut arc_reference_eV = BTreeMap::new();
        for (symbol, parameter) in &table.displacement.arc_dpa.parameters {
            if composition::z_of(symbol).is_none()
                || !parameter.b.is_finite()
                || !parameter.c.is_finite()
            {
                return Err(format!(
                    "damage table arc_dpa parameters['{symbol}'] are invalid"
                ));
            }
            arc.insert(symbol.clone(), (parameter.b, parameter.c));
            arc_reference_eV.insert(symbol.clone(), parameter.reference_displacement_energy_eV);
        }
        let groups = table.boundaries_eV.len() - 1;
        let mut canonical_keys = BTreeSet::new();
        let mut targets = BTreeMap::new();
        for (raw, row) in &table.targets {
            let (canonical, selector) = target_selector(raw)?;
            if !canonical_keys.insert(canonical.clone()) {
                return Err(format!("duplicate damage target '{canonical}'"));
            }
            let check = |name: &str, values: &[f64]| -> Result<(), String> {
                if values.len() != groups {
                    return Err(format!(
                        "damage target '{canonical}' {name} declares {} group values, expected {groups}",
                        values.len()
                    ));
                }
                if values
                    .iter()
                    .any(|value| !value.is_finite() || *value < 0.0)
                {
                    return Err(format!(
                        "damage target '{canonical}' {name} contains a nonfinite or negative value"
                    ));
                }
                Ok(())
            };
            check("damage_energy_barn_eV", &row.damage_energy_barn_eV)?;
            check("elastic", &row.elastic)?;
            check("inelastic", &row.inelastic)?;
            check("disappearance", &row.disappearance)?;
            check("uncovered_xs_barn", &row.uncovered_xs_barn)?;
            check(
                "uncovered_damage_upper_barn_eV",
                &row.uncovered_damage_upper_barn_eV,
            )?;
            check("displacement_nrt_barn", &row.displacement_nrt_barn)?;
            if let Some(values) = &row.displacement_arc_barn {
                check("displacement_arc_barn", values)?;
            }
            let element_symbol = composition::symbol_of(match selector {
                TargetSelector::Nuclide(za, _) => za / 1000,
                TargetSelector::Element(z) => z,
            });
            if !table
                .displacement
                .displacement_energy_eV
                .contains_key(element_symbol)
            {
                return Err(format!(
                    "damage table carries no displacement energy for the element '{element_symbol}' of target '{canonical}'"
                ));
            }
            if row.displacement_arc_barn.is_some() && !arc.contains_key(element_symbol) {
                return Err(format!(
                    "damage target '{canonical}' carries arc-dpa displacements but the table has no arc-dpa constants for '{element_symbol}'"
                ));
            }
            targets.insert(
                canonical,
                TargetRow {
                    selector,
                    sigma: row.damage_energy_barn_eV.clone(),
                    recoil: Some(RecoilRow {
                        nrt: row.displacement_nrt_barn.clone(),
                        arc: row.displacement_arc_barn.clone(),
                        uncovered_upper: row.uncovered_damage_upper_barn_eV.clone(),
                    }),
                },
            );
        }
        Ok(Self {
            source: DamageSource {
                citation: table.source.citation,
                edition: table.source.edition,
                url: table.source.url,
            },
            projectile: table.projectile,
            group_structure: table.group_structure,
            boundaries_eV: table.boundaries_eV,
            temperature_K: table.temperature_K,
            files: table
                .files
                .into_iter()
                .map(|file| DamageFileProvenance {
                    path: file.path,
                    sha256: file.sha256,
                })
                .collect(),
            declared_uncovered: table.uncovered,
            targets,
            table2: Some(Table2Meta {
                displacement_energy_eV: table.displacement.displacement_energy_eV,
                arc,
                arc_reference_eV,
                arc_citation: table.displacement.arc_dpa.citation,
            }),
        })
    }

    /// True for a table-2 file (recoil-integrated columns present).
    pub fn has_recoil_data(&self) -> bool {
        self.table2.is_some()
    }

    /// Ledger fields describing the recoil-integrated model; `None` for a table-1 file.
    pub fn recoil_ledger(&self) -> Option<serde_json::Value> {
        self.table2.as_ref().map(|meta| {
            serde_json::json!({
                "table_format": TABLE_FORMAT_2,
                "built_displacement_energy_eV": meta.displacement_energy_eV,
                "arc_dpa": {
                    "citation": meta.arc_citation,
                    "elements": meta.arc.keys().collect::<Vec<_>>(),
                },
                "recoil_integrated": {
                    "dpa_nrt_rate_per_s": "sum_g displacement_nrt_barn_g * phi_g * flux_multiplier, exact NRT integrated over the recoil spectrum at the built E_d",
                    "dpa_arc_rate_per_s": "the same with the arc-dpa efficiency; absent when a covered element has no published constants",
                    "dpa_uncovered_upper_bound": "damage-energy upper bound of the channels the table could not compute, divided by E_d (the NRT count never exceeds T_d / E_d, and arc-dpa never exceeds NRT at the same E_d); the lower of E_d and the arc fit's own E_d where the two differ",
                    "he_appm_per_dpa": "cumulative He-3 + He-4 appm over the cumulative linear dpa; present with gas output",
                },
            })
        })
    }

    /// Resolve coverage against the material's composition-resolved nuclide set.
    /// `composition_atoms` maps (za, liso) -> atoms/g (the constant reservoir in trace mode or the
    /// initial condition in coupled mode). Returns the plan; `require_complete` errors name every
    /// uncovered material target.
    pub fn plan(
        &self,
        composition_atoms: &BTreeMap<(i32, i32), f64>,
        displacement_energy_eV: &BTreeMap<String, f64>,
        require_complete: bool,
    ) -> Result<DamagePlan, String> {
        // per-element coverage: element row covers the whole element; nuclide rows cover
        // specific composition nuclides of that element
        let mut element_rows: BTreeSet<i32> = BTreeSet::new();
        let mut nuclide_rows: BTreeMap<i32, Vec<(i32, i32)>> = BTreeMap::new();
        for row in self.targets.values() {
            match row.selector {
                TargetSelector::Element(z) => {
                    element_rows.insert(z);
                }
                TargetSelector::Nuclide(za, liso) => {
                    nuclide_rows.entry(za / 1000).or_default().push((za, liso));
                }
            }
        }
        let mut uncovered = Vec::new();
        let mut covered_elements = BTreeMap::new();
        for &(za, liso) in composition_atoms.keys() {
            let z = za / 1000;
            let covered = element_rows.contains(&z)
                || nuclide_rows
                    .get(&z)
                    .is_some_and(|list| list.contains(&(za, liso)));
            if covered {
                covered_elements.insert(z, ());
            } else {
                uncovered.push(name_of(za, liso));
            }
        }
        if require_complete && !uncovered.is_empty() {
            return Err(format!(
                "damage: material targets lack table coverage: {}",
                uncovered.join(", ")
            ));
        }
        let mut covered_elements_ed = BTreeMap::new();
        let mut displacement_sources = BTreeMap::new();
        for &z in covered_elements.keys() {
            let symbol = composition::symbol_of(z);
            // an element the spec omits takes the energy a table-2 file records (the one its recoil integrals
            // were built with); table 1 has none to offer
            let table_energy = self
                .table2
                .as_ref()
                .and_then(|meta| meta.displacement_energy_eV.get(symbol));
            match (displacement_energy_eV.get(symbol), table_energy) {
                (Some(&ed), _) if ed.is_finite() && ed > 0.0 => {
                    covered_elements_ed.insert(z, ed);
                    displacement_sources.insert(z, "spec");
                }
                (None, Some(&built)) => {
                    covered_elements_ed.insert(z, built);
                    displacement_sources.insert(z, "table");
                }
                _ => {
                    return Err(format!(
                        "damage: covered element '{symbol}' has no positive displacement_energy_eV"
                    ));
                }
            }
        }
        if let Some(meta) = &self.table2 {
            // the exact-NRT and arc-dpa columns are recoil integrals at the build-time E_d; they cannot
            // be rescaled to another displacement energy
            for (&z, &ed) in &covered_elements_ed {
                let symbol = composition::symbol_of(z);
                match meta.displacement_energy_eV.get(symbol) {
                    Some(&built) if (built - ed).abs() <= 1e-12 * built.abs().max(ed.abs()) => {}
                    Some(&built) => {
                        return Err(format!(
                            "damage: displacement_energy_eV for '{symbol}' ({ed}) differs from the {built} eV the table-2 recoil integrals were built with; rebuild the table with --displacement-energy {symbol}={ed}"
                        ));
                    }
                    None => {
                        return Err(format!(
                            "damage: the table-2 file carries no displacement energy for covered element '{symbol}'"
                        ));
                    }
                }
            }
        }
        Ok(DamagePlan {
            covered_elements: covered_elements_ed,
            displacement_sources,
            uncovered_targets: uncovered,
        })
    }

    /// Fold one step. `atoms` maps (za, liso) -> atoms/g for the composition nuclides at this
    /// step (reservoir + tracked-fed extras in trace mode, evolved states in coupled mode).
    /// `flux_ascending` is the problem's per-group flux in the table's ascending group order.
    pub fn fold(
        &self,
        plan: &DamagePlan,
        atoms: &BTreeMap<(i32, i32), f64>,
        flux_ascending: &[f64],
        multiplier: f64,
    ) -> DamageStepOut {
        // group targets by element
        let mut element_row: HashMap<i32, &TargetRow> = HashMap::new();
        let mut nuclide_row: HashMap<(i32, i32), &TargetRow> = HashMap::new();
        for row in self.targets.values() {
            match row.selector {
                TargetSelector::Element(z) => {
                    element_row.insert(z, row);
                }
                TargetSelector::Nuclide(za, liso) => {
                    nuclide_row.insert((za, liso), row);
                }
            }
        }
        let per_barn: Vec<f64> = flux_ascending
            .iter()
            .map(|f| {
                crate::quantity::RatePerBarnSecond::from_particle_flux(
                    crate::quantity::ParticleFlux::sum_groups(std::slice::from_ref(f)),
                )
                .get()
            })
            .collect();
        let rate_of = |sigma: &[f64]| -> f64 {
            sigma
                .iter()
                .zip(per_barn.iter())
                .map(|(s, r)| s * r)
                .sum::<f64>()
                * multiplier
        };
        let mut elements = BTreeMap::new();
        let mut covered_atoms_total = 0.0;
        let mut total_damage_energy = 0.0;
        let mut total_uncovered_energy = 0.0;
        for &z in plan.covered_elements.keys() {
            let mut atoms_z = 0.0;
            let mut energy_rate_z = 0.0; // eV/s summed over atoms of this element
                                         // table 2: displacements/s and bound eV/s summed over atoms; `None` arc when a row lacks it
            let mut nrt_rate_z = 0.0;
            let mut arc_rate_z = Some(0.0);
            let mut uncovered_energy_z = 0.0;
            for (&key, &atoms) in atoms {
                if key.0 / 1000 != z || atoms <= 0.0 {
                    continue;
                }
                let row = if let Some(row) = nuclide_row.get(&key) {
                    Some(*row)
                } else if element_row.contains_key(&z) {
                    Some(element_row[&z])
                } else {
                    None
                };
                if let Some(row) = row {
                    energy_rate_z += rate_of(&row.sigma) * atoms;
                    atoms_z += atoms;
                    if let Some(recoil) = &row.recoil {
                        nrt_rate_z += rate_of(&recoil.nrt) * atoms;
                        uncovered_energy_z += rate_of(&recoil.uncovered_upper) * atoms;
                        arc_rate_z = match (arc_rate_z, &recoil.arc) {
                            (Some(total), Some(arc)) => Some(total + rate_of(arc) * atoms),
                            _ => None,
                        };
                    }
                }
            }
            if atoms_z <= 0.0 {
                continue;
            }
            let ed = plan.covered_elements[&z];
            let dpa_rate = NRT_KAPPA * (energy_rate_z / atoms_z) / (2.0 * ed);
            // the uncovered bound must cover the arc count too, which counts at its fit's own E_d
            let bound_ed = self
                .table2
                .as_ref()
                .and_then(|meta| meta.arc_reference_eV.get(composition::symbol_of(z)))
                .filter(|reference| reference.is_finite() && **reference > 0.0)
                .map_or(ed, |reference| ed.min(*reference));
            let recoil = self.table2.as_ref().map(|_| DamageRecoilOut {
                dpa_nrt_rate_per_s: nrt_rate_z / atoms_z,
                dpa_nrt: 0.0, // caller accumulates
                dpa_arc_rate_per_s: arc_rate_z.map(|rate| rate / atoms_z),
                dpa_arc: arc_rate_z.map(|_| 0.0),
                damage_energy_uncovered_upper_eV_per_g_s: uncovered_energy_z,
                dpa_uncovered_upper_bound_rate_per_s: uncovered_energy_z / atoms_z / bound_ed,
                dpa_uncovered_upper_bound: 0.0,
                he_appm_per_dpa: None,
            });
            elements.insert(
                composition::symbol_of(z).to_string(),
                DamageElementOut {
                    atoms_per_g: atoms_z,
                    damage_energy_eV_per_g_s: energy_rate_z,
                    dpa_rate_per_s: dpa_rate,
                    dpa: 0.0, // caller accumulates
                    recoil,
                },
            );
            covered_atoms_total += atoms_z;
            total_damage_energy += energy_rate_z;
            total_uncovered_energy += uncovered_energy_z;
        }
        let dpa_rate = if covered_atoms_total > 0.0 {
            elements
                .values()
                .map(|element| element.dpa_rate_per_s * element.atoms_per_g / covered_atoms_total)
                .sum()
        } else {
            0.0
        };
        let recoil = self.table2.as_ref().map(|_| {
            let weighted = |rate: &dyn Fn(&DamageRecoilOut) -> Option<f64>| -> Option<f64> {
                if covered_atoms_total <= 0.0 {
                    return Some(0.0);
                }
                let mut sum = 0.0;
                for element in elements.values() {
                    sum +=
                        rate(element.recoil.as_ref()?)? * element.atoms_per_g / covered_atoms_total;
                }
                Some(sum)
            };
            let arc_rate = weighted(&|recoil| recoil.dpa_arc_rate_per_s);
            DamageRecoilOut {
                dpa_nrt_rate_per_s: weighted(&|recoil| Some(recoil.dpa_nrt_rate_per_s))
                    .unwrap_or(0.0),
                dpa_nrt: 0.0, // caller accumulates
                dpa_arc_rate_per_s: arc_rate,
                dpa_arc: arc_rate.map(|_| 0.0),
                damage_energy_uncovered_upper_eV_per_g_s: total_uncovered_energy,
                dpa_uncovered_upper_bound_rate_per_s: weighted(&|recoil| {
                    Some(recoil.dpa_uncovered_upper_bound_rate_per_s)
                })
                .unwrap_or(0.0),
                dpa_uncovered_upper_bound: 0.0,
                he_appm_per_dpa: None,
            }
        });
        DamageStepOut {
            dpa_rate_per_s: dpa_rate,
            dpa: 0.0, // caller accumulates
            damage_energy_eV_per_g_s: total_damage_energy,
            // fraction of this step's material target atoms carrying damage data
            covered_atom_fraction: {
                let material_atoms: f64 = atoms.values().sum();
                if material_atoms > 0.0 {
                    covered_atoms_total / material_atoms
                } else {
                    1.0
                }
            },
            elements,
            recoil,
        }
    }

    pub fn boundaries_eV(&self) -> &[f64] {
        &self.boundaries_eV
    }

    pub fn certificate_metadata(&self) -> serde_json::Value {
        let format = if self.table2.is_some() {
            TABLE_FORMAT_2
        } else {
            TABLE_FORMAT
        };
        let mut metadata = serde_json::json!({
            "format": format,
            "units": TABLE_UNITS,
            "projectile": self.projectile,
            "group_structure": self.group_structure,
            "groups": self.boundaries_eV.len().saturating_sub(1),
            "temperature_K": self.temperature_K,
            "source": self.source,
            "targets": self.targets.keys().collect::<Vec<_>>(),
            "files": self.files,
            "declared_uncovered": self.declared_uncovered,
            "model": "NRT: dpa_rate_per_s = 0.8 * damage_energy_eV_per_s_per_atom / (2 * E_d)",
        });
        if let (Some(extra), Some(object)) = (self.recoil_ledger(), metadata.as_object_mut()) {
            object.insert("recoil".into(), extra);
        }
        metadata
    }
}

fn name_of(za: i32, liso: i32) -> String {
    let s = composition::symbol_of(za / 1000);
    if liso > 0 {
        format!("{s}{}m{liso}", za % 1000)
    } else {
        format!("{s}{}", za % 1000)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn table_json(targets: serde_json::Value) -> String {
        serde_json::json!({
            "format": "actinv-damage-table-1",
            "source": {"citation": "test", "edition": "t", "url": "local"},
            "projectile": "neutron",
            "group_structure": "test-2g",
            "boundaries_eV": [1.0e-5, 1.0, 1.0e6],
            "units": "damage_energy_barn_eV_per_group",
            "targets": targets,
        })
        .to_string()
    }

    #[test]
    fn canonical_names_required() {
        let bad = table_json(serde_json::json!({"fe56": [1.0, 1.0]}));
        assert!(PreparedDamageTable::from_json(&bad, "neutron")
            .unwrap_err()
            .contains("not canonical"));
        let good = table_json(serde_json::json!({"Fe56": [1.0, 1.0], "Fe": [2.0, 2.0]}));
        assert!(PreparedDamageTable::from_json(&good, "neutron").is_ok());
    }

    #[test]
    fn projectile_must_match() {
        let json = table_json(serde_json::json!({"Fe56": [1.0, 1.0]}));
        assert!(PreparedDamageTable::from_json(&json, "proton")
            .unwrap_err()
            .contains("projectile"));
    }

    #[test]
    fn boundaries_strictly_ascending_positive() {
        let mut wire: serde_json::Value =
            serde_json::from_str(&table_json(serde_json::json!({"Fe56": [1.0, 1.0]}))).unwrap();
        wire["boundaries_eV"] = serde_json::json!([1.0, 1.0, 2.0]);
        assert!(PreparedDamageTable::from_json(&wire.to_string(), "neutron").is_err());
        wire["boundaries_eV"] = serde_json::json!([-1.0, 1.0, 2.0]);
        assert!(PreparedDamageTable::from_json(&wire.to_string(), "neutron").is_err());
    }

    #[test]
    fn rows_must_be_finite_nonnegative_and_sized() {
        let short = table_json(serde_json::json!({"Fe56": [1.0]}));
        assert!(PreparedDamageTable::from_json(&short, "neutron").is_err());
        let negative = table_json(serde_json::json!({"Fe56": [1.0, -1.0]}));
        assert!(PreparedDamageTable::from_json(&negative, "neutron")
            .unwrap_err()
            .contains("negative"));
        let nan = table_json(serde_json::json!({"Fe56": [1.0, null]}));
        assert!(PreparedDamageTable::from_json(&nan, "neutron").is_err());
    }

    #[test]
    fn plan_names_uncovered_and_requires_ed() {
        let table = PreparedDamageTable::from_json(
            &table_json(serde_json::json!({"Fe56": [1.0, 1.0]})),
            "neutron",
        )
        .unwrap();
        // natural Fe -> Fe54/56/57/58; Fe56 row covers only Fe56
        let composition = BTreeMap::from([
            ((26054, 0), 5.845e-2),
            ((26056, 0), 0.91754),
            ((26057, 0), 2.119e-2),
            ((26058, 0), 2.82e-3),
        ]);
        let ed = BTreeMap::from([("Fe".to_string(), 40.0)]);
        let plan = table.plan(&composition, &ed, false).unwrap();
        assert_eq!(plan.uncovered_targets, vec!["Fe54", "Fe57", "Fe58"]);
        assert!(table.plan(&composition, &ed, true).is_err());
        let no_ed = BTreeMap::new();
        let err = table.plan(&composition, &no_ed, false).unwrap_err();
        assert!(err.contains("Fe") && err.contains("displacement_energy_eV"));
    }

    #[test]
    fn fold_matches_nrt_closed_form() {
        let table = PreparedDamageTable::from_json(
            &table_json(serde_json::json!({"Fe56": [4.0, 6.0]})),
            "neutron",
        )
        .unwrap();
        let composition = BTreeMap::from([((26056, 0), 2.0e22), ((26054, 0), 1.0e20)]);
        let ed = BTreeMap::from([("Fe".to_string(), 40.0)]);
        let plan = table.plan(&composition, &ed, false).unwrap();
        let flux = [3.0, 5.0];
        let out = table.fold(&plan, &composition, &flux, 2.0);
        let barn = crate::quantity::RatePerBarnSecond::from_particle_flux(
            crate::quantity::ParticleFlux::new(1.0).unwrap(),
        )
        .get();
        let expected_rate = (4.0 * 3.0 + 6.0 * 5.0) * 2.0 * barn * 2.0e22;
        assert!((out.damage_energy_eV_per_g_s - expected_rate).abs() < 1e-12 * expected_rate);
        let dpa_rate = 0.8 * (expected_rate / 2.0e22) / 80.0;
        assert!((out.dpa_rate_per_s - dpa_rate).abs() < 1e-12 * dpa_rate);
        assert!((out.covered_atom_fraction - 2.0e22 / (2.0e22 + 1.0e20)).abs() < 1e-15);
    }

    // ---- table 2 (P154) ----------------------------------------------------------------------------

    use actinv_data::damage_kinematic as kinematic;

    fn table2_json(with_arc: bool) -> String {
        let row = kinematic::Table2Row {
            damage_energy_barn_eV: vec![4.0, 6.0],
            elastic: vec![3.0, 4.0],
            inelastic: vec![0.5, 1.0],
            disappearance: vec![0.5, 1.0],
            uncovered_xs_barn: vec![0.2, 0.3],
            uncovered_damage_upper_barn_eV: vec![10.0, 20.0],
            displacement_nrt_barn: vec![0.05, 0.07],
            displacement_arc_barn: with_arc.then(|| vec![0.02, 0.03]),
            channels: serde_json::Value::Null,
        };
        let table = kinematic::DamageTable2 {
            format: kinematic::TABLE_FORMAT_2.into(),
            source: kinematic::Table2Source {
                citation: "test".into(),
                edition: "t".into(),
                url: "local".into(),
            },
            projectile: "neutron".into(),
            group_structure: "test-2g".into(),
            boundaries_eV: vec![1.0e-5, 1.0, 1.0e6],
            units: kinematic::TABLE_UNITS_2.into(),
            temperature_K: Some(0.0),
            method: "kinematic".into(),
            files: vec![],
            uncovered: vec![],
            failed: vec![],
            displacement: kinematic::Table2Displacement {
                displacement_energy_eV: BTreeMap::from([("Fe".to_string(), 40.0)]),
                arc_dpa: kinematic::Table2Arc {
                    citation: "paper".into(),
                    parameters: if with_arc {
                        BTreeMap::from([(
                            "Fe".to_string(),
                            kinematic::Table2ArcParameter {
                                b: -0.568,
                                c: 0.286,
                                reference_displacement_energy_eV: 40.0,
                            },
                        )])
                    } else {
                        BTreeMap::new()
                    },
                },
            },
            model: serde_json::Value::Null,
            targets: BTreeMap::from([("Fe56".to_string(), row)]),
        };
        serde_json::to_string(&table).unwrap()
    }

    #[test]
    fn table2_total_feeds_the_linear_dpa_like_table1() {
        let one = PreparedDamageTable::from_json(
            &table_json(serde_json::json!({"Fe56": [4.0, 6.0]})),
            "neutron",
        )
        .unwrap();
        let two = PreparedDamageTable::from_json(&table2_json(true), "neutron").unwrap();
        assert!(two.has_recoil_data() && !one.has_recoil_data());
        let composition = BTreeMap::from([((26056, 0), 2.0e22), ((26054, 0), 1.0e20)]);
        let ed = BTreeMap::from([("Fe".to_string(), 40.0)]);
        let plan_one = one.plan(&composition, &ed, false).unwrap();
        let plan_two = two.plan(&composition, &ed, false).unwrap();
        let flux = [3.0, 5.0];
        let out_one = one.fold(&plan_one, &composition, &flux, 2.0);
        let out_two = two.fold(&plan_two, &composition, &flux, 2.0);
        assert_eq!(out_one.dpa_rate_per_s, out_two.dpa_rate_per_s);
        assert_eq!(
            out_one.damage_energy_eV_per_g_s,
            out_two.damage_energy_eV_per_g_s
        );
        assert_eq!(out_one.covered_atom_fraction, out_two.covered_atom_fraction);
        assert!(out_one.recoil.is_none());
        // the table-1 serialization carries none of the new keys
        let text = serde_json::to_string(&out_one).unwrap();
        assert!(!text.contains("dpa_nrt") && !text.contains("uncovered"));
        // recoil-integrated quantities against the closed forms
        let barn = crate::quantity::RatePerBarnSecond::from_particle_flux(
            crate::quantity::ParticleFlux::new(1.0).unwrap(),
        )
        .get();
        let recoil = out_two.recoil.as_ref().unwrap();
        let nrt = (0.05 * 3.0 + 0.07 * 5.0) * 2.0 * barn;
        let arc = (0.02 * 3.0 + 0.03 * 5.0) * 2.0 * barn;
        let bound_energy = (10.0 * 3.0 + 20.0 * 5.0) * 2.0 * barn * 2.0e22;
        assert!((recoil.dpa_nrt_rate_per_s - nrt).abs() < 1e-12 * nrt);
        assert!((recoil.dpa_arc_rate_per_s.unwrap() - arc).abs() < 1e-12 * arc);
        assert!(
            (recoil.damage_energy_uncovered_upper_eV_per_g_s - bound_energy).abs()
                < 1e-12 * bound_energy
        );
        let bound_dpa = bound_energy / 2.0e22 / 40.0;
        assert!(
            (recoil.dpa_uncovered_upper_bound_rate_per_s - bound_dpa).abs() < 1e-12 * bound_dpa
        );
        let element = out_two.elements["Fe"].recoil.as_ref().unwrap();
        assert!((element.dpa_nrt_rate_per_s - nrt).abs() < 1e-12 * nrt);
        // the serialized step carries the new keys flat beside the existing ones
        let value = serde_json::to_value(&out_two).unwrap();
        for key in [
            "dpa",
            "dpa_nrt",
            "dpa_arc",
            "dpa_nrt_rate_per_s",
            "dpa_uncovered_upper_bound",
        ] {
            assert!(value.get(key).is_some(), "{key}");
        }
        assert!(value.get("he_appm_per_dpa").is_none());
    }

    #[test]
    fn table2_refuses_a_different_displacement_energy() {
        let table = PreparedDamageTable::from_json(&table2_json(true), "neutron").unwrap();
        let composition = BTreeMap::from([((26056, 0), 2.0e22)]);
        let other = BTreeMap::from([("Fe".to_string(), 41.0)]);
        let error = table.plan(&composition, &other, false).unwrap_err();
        assert!(error.contains("differs") && error.contains("Fe"), "{error}");
        // table 1 has no such constraint
        let one = PreparedDamageTable::from_json(
            &table_json(serde_json::json!({"Fe56": [4.0, 6.0]})),
            "neutron",
        )
        .unwrap();
        assert!(one.plan(&composition, &other, false).is_ok());
    }

    #[test]
    fn table2_energy_defaults_to_the_table_and_names_its_source() {
        let table = PreparedDamageTable::from_json(&table2_json(true), "neutron").unwrap();
        let composition = BTreeMap::from([((26056, 0), 2.0e22)]);
        // omitted: the table's energy, source "table"
        let plan = table.plan(&composition, &BTreeMap::new(), false).unwrap();
        assert_eq!(plan.covered_elements[&26], 40.0);
        assert_eq!(plan.displacement_sources[&26], "table");
        // explicit and equal: source "spec", same fold
        let ed = BTreeMap::from([("Fe".to_string(), 40.0)]);
        let explicit = table.plan(&composition, &ed, false).unwrap();
        assert_eq!(explicit.displacement_sources[&26], "spec");
        assert_eq!(explicit.covered_elements, plan.covered_elements);
        // explicit and different: still refused
        let other = BTreeMap::from([("Fe".to_string(), 41.0)]);
        assert!(table.plan(&composition, &other, false).is_err());
        // table 1 records no energy, so an omitted element is still refused
        let one = PreparedDamageTable::from_json(
            &table_json(serde_json::json!({"Fe56": [4.0, 6.0]})),
            "neutron",
        )
        .unwrap();
        let error = one.plan(&composition, &BTreeMap::new(), false).unwrap_err();
        assert!(error.contains("Fe") && error.contains("displacement_energy_eV"));
    }

    #[test]
    fn table2_without_arc_constants_omits_the_arc_outputs() {
        let table = PreparedDamageTable::from_json(&table2_json(false), "neutron").unwrap();
        let composition = BTreeMap::from([((26056, 0), 2.0e22)]);
        let ed = BTreeMap::from([("Fe".to_string(), 40.0)]);
        let plan = table.plan(&composition, &ed, false).unwrap();
        let out = table.fold(&plan, &composition, &[3.0, 5.0], 1.0);
        let recoil = out.recoil.as_ref().unwrap();
        assert!(recoil.dpa_arc_rate_per_s.is_none() && recoil.dpa_arc.is_none());
        assert!(recoil.dpa_nrt_rate_per_s > 0.0);
        let value = serde_json::to_value(&out).unwrap();
        assert!(value.get("dpa_arc").is_none() && value.get("dpa_nrt").is_some());
    }

    #[test]
    fn recoil_quantities_accumulate_over_steps() {
        let table = PreparedDamageTable::from_json(&table2_json(true), "neutron").unwrap();
        let composition = BTreeMap::from([((26056, 0), 2.0e22)]);
        let ed = BTreeMap::from([("Fe".to_string(), 40.0)]);
        let plan = table.plan(&composition, &ed, false).unwrap();
        let mut cumulative = RecoilCumulative::default();
        let mut first = table.fold(&plan, &composition, &[3.0, 5.0], 1.0);
        let mut second = table.fold(&plan, &composition, &[3.0, 5.0], 1.0);
        cumulative.accumulate(&mut first, 10.0);
        cumulative.accumulate(&mut second, 5.0);
        let rate = first.recoil.as_ref().unwrap().dpa_nrt_rate_per_s;
        assert!((first.recoil.as_ref().unwrap().dpa_nrt - 10.0 * rate).abs() < 1e-18);
        assert!((second.recoil.as_ref().unwrap().dpa_nrt - 15.0 * rate).abs() < 1e-18);
        assert!(
            (second.elements["Fe"].recoil.as_ref().unwrap().dpa_nrt - 15.0 * rate).abs() < 1e-18
        );
        assert!(second.recoil.as_ref().unwrap().dpa_arc.unwrap() > 0.0);
    }

    #[test]
    fn table2_reader_rejects_malformed_files() {
        let good: serde_json::Value = serde_json::from_str(&table2_json(true)).unwrap();
        let read = |value: &serde_json::Value| {
            PreparedDamageTable::from_json(&value.to_string(), "neutron").map(|_| ())
        };
        assert!(read(&good).is_ok());
        let mut short = good.clone();
        short["targets"]["Fe56"]["displacement_nrt_barn"] = serde_json::json!([0.1]);
        assert!(read(&short).unwrap_err().contains("displacement_nrt_barn"));
        let mut method = good.clone();
        method["method"] = "other".into();
        assert!(read(&method).unwrap_err().contains("method"));
        let mut negative = good.clone();
        negative["targets"]["Fe56"]["uncovered_damage_upper_barn_eV"] =
            serde_json::json!([1.0, -1.0]);
        assert!(read(&negative).unwrap_err().contains("negative"));
        let mut no_ed = good.clone();
        no_ed["displacement"]["displacement_energy_eV"] = serde_json::json!({});
        assert!(read(&no_ed).unwrap_err().contains("displacement energy"));
        let mut no_params = good.clone();
        no_params["displacement"]["arc_dpa"]["parameters"] = serde_json::json!({});
        assert!(read(&no_params).unwrap_err().contains("arc-dpa"));
        let mut unknown = good.clone();
        unknown["surprise"] = 1.into();
        assert!(read(&unknown).is_err());
        let wrong_projectile = PreparedDamageTable::from_json(&good.to_string(), "proton");
        assert!(wrong_projectile.unwrap_err().contains("projectile"));
    }
}
