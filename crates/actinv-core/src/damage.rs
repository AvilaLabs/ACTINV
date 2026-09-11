#![allow(non_snake_case)] // JSON wire names carry their physical units.
//! Damage observables (P23 G3): hash-pinned `actinv-damage-table-1` folded to NRT dpa.
//!
//! A target row is a per-group damage-energy production cross section in barn·eV, keyed by an
//! explicit nuclide or an element. The fold uses the material's composition-resolved nuclide
//! inventories — the constant reservoir in trace mode, evolved states in coupled mode —
//! never transmutation products. The displacement model is NRT:
//! `dpa_rate = 0.8 * damage_energy_rate_per_atom / (2 * E_d)`.

use actinv_data::composition::{self, MaterialKey};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet, HashMap};

const TABLE_FORMAT: &str = "actinv-damage-table-1";
const TABLE_UNITS: &str = "damage_energy_barn_eV_per_group";
/// 1 barn = 1e-24 cm^2; converts barn·eV·cm^-2·s^-1 into eV/s per target atom.
const BARN_TO_CM2: f64 = 1e-24;
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

#[derive(Debug)]
struct TargetRow {
    selector: TargetSelector,
    sigma: Vec<f64>,
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
}

#[derive(Debug, Serialize, Clone)]
pub struct DamageElementOut {
    pub atoms_per_g: f64,
    pub damage_energy_eV_per_g_s: f64,
    pub dpa_rate_per_s: f64,
    pub dpa: f64,
}

#[derive(Debug, Serialize)]
pub struct DamageStepOut {
    pub dpa_rate_per_s: f64,
    pub dpa: f64,
    pub damage_energy_eV_per_g_s: f64,
    pub covered_atom_fraction: f64,
    pub elements: BTreeMap<String, DamageElementOut>,
}

/// Coverage plan fixed once per run: the composition-resolved nuclide set partitioned by element
/// into nuclide-covered and element-row-covered atoms.
#[derive(Debug)]
pub struct DamagePlan {
    /// covered element Z -> displacement energy eV
    pub covered_elements: BTreeMap<i32, f64>,
    /// uncovered composition nuclides, canonical names
    pub uncovered_targets: Vec<String>,
}

impl PreparedDamageTable {
    pub fn from_json(text: &str, spec_projectile: &str) -> Result<Self, String> {
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
        for &z in covered_elements.keys() {
            let symbol = composition::symbol_of(z);
            match displacement_energy_eV.get(symbol) {
                Some(&ed) if ed.is_finite() && ed > 0.0 => {
                    covered_elements_ed.insert(z, ed);
                }
                _ => {
                    return Err(format!(
                        "damage: covered element '{symbol}' has no positive displacement_energy_eV"
                    ));
                }
            }
        }
        Ok(DamagePlan {
            covered_elements: covered_elements_ed,
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
        let rate_of = |sigma: &[f64]| -> f64 {
            sigma
                .iter()
                .zip(flux_ascending.iter())
                .map(|(s, f)| s * f)
                .sum::<f64>()
                * multiplier
                * BARN_TO_CM2
        };
        let mut elements = BTreeMap::new();
        let mut covered_atoms_total = 0.0;
        let mut total_damage_energy = 0.0;
        for &z in plan.covered_elements.keys() {
            let mut atoms_z = 0.0;
            let mut energy_rate_z = 0.0; // eV/s summed over atoms of this element
            for (&key, &atoms) in atoms {
                if key.0 / 1000 != z || atoms <= 0.0 {
                    continue;
                }
                if let Some(row) = nuclide_row.get(&key) {
                    energy_rate_z += rate_of(&row.sigma) * atoms;
                    atoms_z += atoms;
                } else if element_row.contains_key(&z) {
                    energy_rate_z += rate_of(&element_row[&z].sigma) * atoms;
                    atoms_z += atoms;
                }
            }
            if atoms_z <= 0.0 {
                continue;
            }
            let ed = plan.covered_elements[&z];
            let dpa_rate = NRT_KAPPA * (energy_rate_z / atoms_z) / (2.0 * ed);
            elements.insert(
                composition::symbol_of(z).to_string(),
                DamageElementOut {
                    atoms_per_g: atoms_z,
                    damage_energy_eV_per_g_s: energy_rate_z,
                    dpa_rate_per_s: dpa_rate,
                    dpa: 0.0, // caller accumulates
                },
            );
            covered_atoms_total += atoms_z;
            total_damage_energy += energy_rate_z;
        }
        let dpa_rate = if covered_atoms_total > 0.0 {
            elements
                .values()
                .map(|element| element.dpa_rate_per_s * element.atoms_per_g / covered_atoms_total)
                .sum()
        } else {
            0.0
        };
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
        }
    }

    pub fn boundaries_eV(&self) -> &[f64] {
        &self.boundaries_eV
    }

    pub fn certificate_metadata(&self) -> serde_json::Value {
        serde_json::json!({
            "format": TABLE_FORMAT,
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
        })
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
        // energy rate = (4*3 + 6*5) * 2 * 1e-24 * 2e22
        let expected_rate = (4.0 * 3.0 + 6.0 * 5.0) * 2.0 * 1e-24 * 2.0e22;
        assert!((out.damage_energy_eV_per_g_s - expected_rate).abs() < 1e-12 * expected_rate);
        let dpa_rate = 0.8 * (expected_rate / 2.0e22) / 80.0;
        assert!((out.dpa_rate_per_s - dpa_rate).abs() < 1e-12 * dpa_rate);
        assert!((out.covered_atom_fraction - 2.0e22 / (2.0e22 + 1.0e20)).abs() < 1e-15);
    }
}
