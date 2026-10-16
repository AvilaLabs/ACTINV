#![allow(non_snake_case)] // JSON wire names carry their physical units.
//! Kerma tables (P126): hash-pinned `actinv-kerma-table-1`, groupwise nuclear heating factors.
//!
//! A target row is a per-group kerma factor in barn·eV, keyed by an explicit nuclide or an
//! element: the group average of the NJOY HEATR total heating kerma (MT=301) over the group's
//! flux weighting. The heating rate of one atom in a group flux `phi_g` (particles cm^-2 s^-1) is
//! `sum_g K_g * phi_g * 1e-24` eV/s. An optional second row set carries the HEATR total damage
//! energy production (MT=444) in the same units. This module is I/O and validation only; the
//! fold with a material's inventories lives in `actinv-core`.
//!
//! Format (strict JSON, unknown fields are an error):
//!
//! ```text
//! {
//!   "format": "actinv-kerma-table-1",
//!   "source": {"citation": "...", "edition": "...", "url": "..."},
//!   "projectile": "neutron",
//!   "group_structure": "fispact-709",
//!   "boundaries_eV": [ngroups + 1 ascending positive values],
//!   "units": "kerma_barn_eV_per_group",
//!   "targets": {"Fe56": [ngroups finite values], "Fe": [...]},        // MT=301
//!   "damage_energy_targets": {"Fe56": [ngroups values >= 0]},          // optional, MT=444
//!   "temperature_K": 293.6,                                            // optional
//!   "files": [{"path": "...", "sha256": "..."}],                       // optional provenance
//!   "uncovered": ["..."]                                               // optional, evaluations lacking MT=301
//! }
//! ```
use crate::composition::{self, MaterialKey};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet, HashMap};

const TABLE_FORMAT: &str = "actinv-kerma-table-1";
const TABLE_UNITS: &str = "kerma_barn_eV_per_group";

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct KermaSource {
    pub citation: String,
    pub edition: String,
    pub url: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct KermaFileProvenance {
    pub path: String,
    pub sha256: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct TableWire {
    format: String,
    source: KermaSource,
    projectile: String,
    group_structure: String,
    boundaries_eV: Vec<f64>,
    units: String,
    targets: BTreeMap<String, Vec<f64>>,
    #[serde(default)]
    damage_energy_targets: BTreeMap<String, Vec<f64>>,
    #[serde(default)]
    temperature_K: Option<f64>,
    #[serde(default)]
    files: Vec<KermaFileProvenance>,
    #[serde(default)]
    uncovered: Vec<String>,
}

/// One row set (heating or damage energy): nuclide rows take precedence over element rows.
#[derive(Debug, Default)]
struct Rows {
    nuclides: HashMap<(i32, i32), Vec<f64>>,
    elements: HashMap<i32, Vec<f64>>,
    names: Vec<String>,
    negative: Vec<String>,
}

impl Rows {
    fn parse(
        label: &str,
        raw: &BTreeMap<String, Vec<f64>>,
        groups: usize,
        allow_negative: bool,
    ) -> Result<Self, String> {
        let mut rows = Rows::default();
        let mut canonical_keys = BTreeSet::new();
        for (name, values) in raw {
            let (canonical, nuclide, element) = match composition::material_key(name)
                .map_err(|error| format!("kerma table {label} '{name}': {error}"))?
            {
                MaterialKey::Nuclide {
                    canonical,
                    za,
                    liso,
                    ..
                } => (canonical, Some((za, liso)), None),
                MaterialKey::Element(symbol) => {
                    let z = composition::z_of(&symbol)
                        .ok_or_else(|| format!("kerma table {label} '{name}': unknown element"))?;
                    (symbol, None, Some(z))
                }
            };
            if *name != canonical {
                return Err(format!(
                    "kerma table {label} '{name}' is not canonical; use '{canonical}'"
                ));
            }
            if !canonical_keys.insert(canonical.clone()) {
                return Err(format!("duplicate kerma table {label} '{canonical}'"));
            }
            if values.len() != groups {
                return Err(format!(
                    "kerma table {label} '{canonical}' declares {} group values, expected {groups}",
                    values.len()
                ));
            }
            if values.iter().any(|value| !value.is_finite()) {
                return Err(format!(
                    "kerma table {label} '{canonical}' contains a nonfinite value"
                ));
            }
            if !allow_negative && values.iter().any(|value| *value < 0.0) {
                return Err(format!(
                    "kerma table {label} '{canonical}' contains a negative value"
                ));
            }
            if values.iter().any(|value| *value < 0.0) {
                rows.negative.push(canonical.clone());
            }
            if let Some(key) = nuclide {
                rows.nuclides.insert(key, values.clone());
            }
            if let Some(z) = element {
                rows.elements.insert(z, values.clone());
            }
            rows.names.push(canonical);
        }
        Ok(rows)
    }

    fn lookup(&self, za: i32, liso: i32) -> Option<&[f64]> {
        self.nuclides
            .get(&(za, liso))
            .or_else(|| self.elements.get(&(za / 1000)))
            .map(Vec::as_slice)
    }
}

#[derive(Debug)]
pub struct KermaTable {
    source: KermaSource,
    projectile: String,
    group_structure: String,
    boundaries_eV: Vec<f64>,
    temperature_K: Option<f64>,
    files: Vec<KermaFileProvenance>,
    declared_uncovered: Vec<String>,
    heating: Rows,
    damage_energy: Rows,
}

impl KermaTable {
    pub fn from_json(text: &str, spec_projectile: &str) -> Result<Self, String> {
        let table: TableWire =
            serde_json::from_str(text).map_err(|error| format!("kerma table: {error}"))?;
        if table.format != TABLE_FORMAT {
            return Err(format!("unsupported kerma table format '{}'", table.format));
        }
        if table.units != TABLE_UNITS {
            return Err(format!("unsupported kerma table units '{}'", table.units));
        }
        for (name, value) in [
            ("citation", &table.source.citation),
            ("edition", &table.source.edition),
            ("url", &table.source.url),
        ] {
            if value.trim().is_empty() {
                return Err(format!("kerma table source.{name} must be nonempty"));
            }
        }
        if table.projectile != spec_projectile {
            return Err(format!(
                "kerma table projectile '{}' does not match the problem's '{}'",
                table.projectile, spec_projectile
            ));
        }
        if table.boundaries_eV.len() < 2 {
            return Err("kerma table needs at least one group".into());
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
            return Err("kerma table boundaries_eV must be positive and strictly ascending".into());
        }
        if table.targets.is_empty() {
            return Err("kerma table declares no heating targets".into());
        }
        if table
            .temperature_K
            .is_some_and(|value| !value.is_finite() || value < 0.0)
        {
            return Err("kerma table temperature_K must be finite and nonnegative".into());
        }
        let groups = table.boundaries_eV.len() - 1;
        // Energy-balance kerma can dip below zero in an evaluation with inconsistent photon
        // production data; such a row is loaded and named in the certificate metadata. Damage
        // energy is a physical energy deposition and cannot be negative.
        let heating = Rows::parse("target", &table.targets, groups, true)?;
        let damage_energy = Rows::parse(
            "damage_energy_target",
            &table.damage_energy_targets,
            groups,
            false,
        )?;
        Ok(Self {
            source: table.source,
            projectile: table.projectile,
            group_structure: table.group_structure,
            boundaries_eV: table.boundaries_eV,
            temperature_K: table.temperature_K,
            files: table.files,
            declared_uncovered: table.uncovered,
            heating,
            damage_energy,
        })
    }

    pub fn boundaries_eV(&self) -> &[f64] {
        &self.boundaries_eV
    }

    /// True when a heating row (nuclide, else element) covers this state.
    pub fn covers(&self, za: i32, liso: i32) -> bool {
        self.heating.lookup(za, liso).is_some()
    }

    /// True when the table carries any MT=444 damage-energy rows.
    pub fn has_damage_energy(&self) -> bool {
        !self.damage_energy.names.is_empty()
    }

    /// Heating of one atom of (za, liso) in eV/s: `sum_g K_g * rate_per_barn_s[g]`, where
    /// `rate_per_barn_s[g]` is the group flux converted to the reaction rate of a one-barn cross
    /// section (s^-1). `None` when no row covers the state.
    pub fn heating_eV_per_s(&self, za: i32, liso: i32, rate_per_barn_s: &[f64]) -> Option<f64> {
        self.heating
            .lookup(za, liso)
            .map(|row| fold(row, rate_per_barn_s))
    }

    /// Damage-energy production of one atom of (za, liso) in eV/s, as `heating_eV_per_s`.
    pub fn damage_energy_eV_per_s(
        &self,
        za: i32,
        liso: i32,
        rate_per_barn_s: &[f64],
    ) -> Option<f64> {
        self.damage_energy
            .lookup(za, liso)
            .map(|row| fold(row, rate_per_barn_s))
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
            "targets": self.heating.names,
            "damage_energy_targets": self.damage_energy.names,
            "targets_with_negative_group_values": self.heating.negative,
            "files": self.files,
            "declared_uncovered": self.declared_uncovered,
            "quantity": "group-averaged NJOY HEATR total heating kerma (MT=301) and, when present, total damage energy production (MT=444), barn*eV per atom",
        })
    }
}

fn fold(row: &[f64], rate_per_barn_s: &[f64]) -> f64 {
    row.iter()
        .zip(rate_per_barn_s.iter())
        .map(|(factor, rate)| factor * rate)
        .sum()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn table_json(targets: serde_json::Value) -> String {
        serde_json::json!({
            "format": "actinv-kerma-table-1",
            "source": {"citation": "test", "edition": "t", "url": "local"},
            "projectile": "neutron",
            "group_structure": "test-2g",
            "boundaries_eV": [1.0e-5, 1.0, 1.0e6],
            "units": "kerma_barn_eV_per_group",
            "targets": targets,
        })
        .to_string()
    }

    #[test]
    fn canonical_names_required_and_duplicates_impossible() {
        let bad = table_json(serde_json::json!({"fe56": [1.0, 1.0]}));
        assert!(KermaTable::from_json(&bad, "neutron")
            .unwrap_err()
            .contains("not canonical"));
        let good = table_json(serde_json::json!({"Fe56": [1.0, 1.0], "Fe": [2.0, 2.0]}));
        assert!(KermaTable::from_json(&good, "neutron").is_ok());
    }

    #[test]
    fn format_units_projectile_and_provenance_are_enforced() {
        let mut wire: serde_json::Value =
            serde_json::from_str(&table_json(serde_json::json!({"Fe56": [1.0, 1.0]}))).unwrap();
        assert!(KermaTable::from_json(&wire.to_string(), "proton")
            .unwrap_err()
            .contains("projectile"));
        wire["units"] = serde_json::json!("damage_energy_barn_eV_per_group");
        assert!(KermaTable::from_json(&wire.to_string(), "neutron")
            .unwrap_err()
            .contains("units"));
        wire["units"] = serde_json::json!("kerma_barn_eV_per_group");
        wire["format"] = serde_json::json!("actinv-damage-table-1");
        assert!(KermaTable::from_json(&wire.to_string(), "neutron")
            .unwrap_err()
            .contains("format"));
        wire["format"] = serde_json::json!("actinv-kerma-table-1");
        wire["source"]["url"] = serde_json::json!(" ");
        assert!(KermaTable::from_json(&wire.to_string(), "neutron")
            .unwrap_err()
            .contains("source.url"));
        wire["source"]["url"] = serde_json::json!("local");
        wire["surprise"] = serde_json::json!(1);
        assert!(KermaTable::from_json(&wire.to_string(), "neutron").is_err());
    }

    #[test]
    fn boundaries_strictly_ascending_positive() {
        let mut wire: serde_json::Value =
            serde_json::from_str(&table_json(serde_json::json!({"Fe56": [1.0, 1.0]}))).unwrap();
        wire["boundaries_eV"] = serde_json::json!([1.0, 1.0, 2.0]);
        assert!(KermaTable::from_json(&wire.to_string(), "neutron").is_err());
        wire["boundaries_eV"] = serde_json::json!([-1.0, 1.0, 2.0]);
        assert!(KermaTable::from_json(&wire.to_string(), "neutron").is_err());
    }

    #[test]
    fn rows_must_be_finite_and_sized() {
        let short = table_json(serde_json::json!({"Fe56": [1.0]}));
        assert!(KermaTable::from_json(&short, "neutron").is_err());
        let nan = table_json(serde_json::json!({"Fe56": [1.0, null]}));
        assert!(KermaTable::from_json(&nan, "neutron").is_err());
        let empty = table_json(serde_json::json!({}));
        assert!(KermaTable::from_json(&empty, "neutron")
            .unwrap_err()
            .contains("no heating targets"));
    }

    #[test]
    fn negative_heating_is_named_but_negative_damage_energy_is_refused() {
        let negative = table_json(serde_json::json!({"Fe56": [1.0, -1.0]}));
        let table = KermaTable::from_json(&negative, "neutron").unwrap();
        assert_eq!(
            table.certificate_metadata()["targets_with_negative_group_values"],
            serde_json::json!(["Fe56"])
        );
        let mut wire: serde_json::Value =
            serde_json::from_str(&table_json(serde_json::json!({"Fe56": [1.0, 1.0]}))).unwrap();
        wire["damage_energy_targets"] = serde_json::json!({"Fe56": [1.0, -1.0]});
        assert!(KermaTable::from_json(&wire.to_string(), "neutron")
            .unwrap_err()
            .contains("negative"));
        wire["damage_energy_targets"] = serde_json::json!({"Fe56": [1.0, 2.0]});
        let with_damage = KermaTable::from_json(&wire.to_string(), "neutron").unwrap();
        assert!(with_damage.has_damage_energy());
        assert!(!table.has_damage_energy());
    }

    #[test]
    fn nuclide_rows_take_precedence_over_element_rows() {
        let table = KermaTable::from_json(
            &table_json(serde_json::json!({"Fe56": [4.0, 6.0], "Fe": [1.0, 1.0]})),
            "neutron",
        )
        .unwrap();
        let rates = [3.0, 5.0];
        // Fe56 has its own row: 4*3 + 6*5
        assert_eq!(table.heating_eV_per_s(26056, 0, &rates), Some(42.0));
        // Fe54 falls back to the element row: 1*3 + 1*5
        assert_eq!(table.heating_eV_per_s(26054, 0, &rates), Some(8.0));
        // no row for Ni
        assert_eq!(table.heating_eV_per_s(28058, 0, &rates), None);
        assert!(table.covers(26054, 0));
        assert!(!table.covers(28058, 0));
        assert_eq!(table.damage_energy_eV_per_s(26056, 0, &rates), None);
    }

    #[test]
    fn p140_pf_f10_damage_rows_without_a_heating_row_are_refused() {
        // a damage row for a nuclide the heating rows do not cover would be dropped in silence
        let mut wire: serde_json::Value =
            serde_json::from_str(&table_json(serde_json::json!({"Fe56": [1.0, 1.0]}))).unwrap();
        wire["damage_energy_targets"] = serde_json::json!({"Co59": [1.0, 2.0]});
        let error = KermaTable::from_json(&wire.to_string(), "neutron")
            .expect_err("a damage row with no heating row is unreachable and must be refused");
        assert!(
            error.contains("damage") && error.contains("Co59"),
            "{error}"
        );
        // damage rows that sit on heating rows stay valid
        wire["damage_energy_targets"] = serde_json::json!({"Fe56": [1.0, 2.0]});
        assert!(KermaTable::from_json(&wire.to_string(), "neutron").is_ok());
    }
}
