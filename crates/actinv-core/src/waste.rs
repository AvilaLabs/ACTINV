//! Nominal component classification under the selected U.S. 10 CFR 61.55 rule pack.
//!
//! This module evaluates inventory activity after the caller has applied the declared component
//! geometry and any separately declared external tritium. It does not assess site acceptance,
//! waste form, uncertainty, transport, or mixed-package averaging.

use actinv_data::composition::{self, MaterialKey};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

const BUNDLED_RULES: &str = include_str!("../data/waste_us_nrc_61_55_v1.json");
#[cfg(test)]
const CI_BQ: f64 = 37_000_000_000.0;
#[cfg(test)]
const YEAR_S: f64 = 31_557_600.0;
const T1_ALPHA_SELECTOR: &str = "alpha_transuranic_gt5y";
const T2_SHORT_SELECTOR: &str = "half_life_lt5y";

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct RulePack {
    pub schema: String,
    pub id: String,
    pub version: u32,
    pub source_url: String,
    pub source_as_of: String,
    pub annual_source_url: String,
    pub annual_source_sha256: String,
    pub ci_bq: f64,
    pub year_s: f64,
    rows: Vec<RuleRow>,
    #[serde(skip)]
    pub sha256: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct RuleRow {
    table: u8,
    id: String,
    selector: String,
    applicability: String,
    unit: String,
    limits: Vec<Option<f64>>,
}

impl RulePack {
    /// Parse and validate a selected `actinv-waste-rules-1` rule pack.
    pub fn parse(text: &str) -> Result<Self, String> {
        let mut pack: Self = serde_json::from_str(text)
            .map_err(|error| format!("waste rule pack is not valid JSON: {error}"))?;
        pack.validate()?;
        pack.sha256 = sha256_hex(text.as_bytes());
        Ok(pack)
    }

    /// Load the bundled U.S. Part 61 rule pack. Callers must still explicitly select it.
    pub fn bundled() -> Result<Self, String> {
        Self::parse(BUNDLED_RULES)
    }

    /// Whether a nuclide belongs to an applicable named or aggregate Part 61 row.
    ///
    /// Missing properties leave aggregate membership unknown. This queries the
    /// selected pack's rows without substituting a separate draft catalog.
    pub fn nuclide_listed(
        &self,
        waste_type: WasteType,
        key: &str,
        properties: Option<NuclideProperties>,
    ) -> Result<Option<bool>, String> {
        self.validate()?;
        let canonical = canonical_nuclide(key)?;
        if let Some(value) = properties {
            let expected_z = nuclide_z(&canonical)?;
            if value.z != expected_z {
                return Err(format!(
                    "nuclide properties Z={} does not match {canonical} (Z={expected_z})",
                    value.z
                ));
            }
            if !value.half_life_s.is_finite() || value.half_life_s <= 0.0 {
                return Err(format!(
                    "nuclide {canonical} half_life_s must be positive and finite"
                ));
            }
        }
        let mut unknown = false;
        for row in self
            .applicable_rows(1, waste_type)
            .chain(self.applicable_rows(2, waste_type))
        {
            match row.selector.as_str() {
                T1_ALPHA_SELECTOR | T2_SHORT_SELECTOR => match properties {
                    None => unknown = true,
                    Some(value) => {
                        let years = value.half_life_s / self.year_s;
                        let member = if row.selector == T1_ALPHA_SELECTOR {
                            value.z > 92 && value.alpha_emitting && years > 5.0
                        } else {
                            years < 5.0
                        };
                        if member {
                            return Ok(Some(true));
                        }
                    }
                },
                _ if canonical_nuclide(&row.selector)? == canonical => return Ok(Some(true)),
                _ => {}
            }
        }
        Ok(if unknown { None } else { Some(false) })
    }

    fn validate(&self) -> Result<(), String> {
        if self.schema != "actinv-waste-rules-1" {
            return Err(format!("unsupported waste rule schema '{}'", self.schema));
        }
        if self.id.trim().is_empty() || self.version == 0 {
            return Err("waste rule pack id and positive version are required".into());
        }
        if self.source_url.trim().is_empty()
            || self.source_as_of.trim().is_empty()
            || self.annual_source_url.trim().is_empty()
            || !valid_sha256(&self.annual_source_sha256)
        {
            return Err(
                "waste rule pack source metadata or annual source SHA-256 is invalid".into(),
            );
        }
        if self.ci_bq != 37_000_000_000.0 || self.year_s != 31_557_600.0 {
            return Err("waste rule pack has unsupported physical conversion constants".into());
        }
        if self.rows.is_empty() {
            return Err("waste rule pack contains no rows".into());
        }

        let mut ids = BTreeSet::new();
        let mut selectors: BTreeMap<(u8, String), BTreeSet<&str>> = BTreeMap::new();
        for row in &self.rows {
            if row.id.trim().is_empty() || !ids.insert(row.id.as_str()) {
                return Err(format!(
                    "waste rule row id '{}' is empty or duplicated",
                    row.id
                ));
            }
            if !matches!(row.table, 1 | 2) {
                return Err(format!(
                    "waste rule row '{}' has unsupported table {}",
                    row.id, row.table
                ));
            }
            if !matches!(
                row.applicability.as_str(),
                "all" | "general" | "activated_metal"
            ) {
                return Err(format!(
                    "waste rule row '{}' has unsupported applicability",
                    row.id
                ));
            }
            if !matches!(row.unit.as_str(), "Ci/m3" | "nCi/g")
                || (row.table == 2 && row.unit != "Ci/m3")
            {
                return Err(format!(
                    "waste rule row '{}' has unsupported unit '{}'",
                    row.id, row.unit
                ));
            }
            let expected_columns = if row.table == 1 { 1 } else { 3 };
            if row.limits.len() != expected_columns {
                return Err(format!(
                    "waste rule row '{}' must have {expected_columns} limit column(s)",
                    row.id
                ));
            }
            let nulls_allowed = row.table == 2
                && matches!(row.selector.as_str(), T2_SHORT_SELECTOR | "H-3" | "Co-60");
            for (index, limit) in row.limits.iter().enumerate() {
                match limit {
                    Some(value) if value.is_finite() && *value > 0.0 => {}
                    None if nulls_allowed && index > 0 => {}
                    _ => return Err(format!("waste rule row '{}' has an invalid limit", row.id)),
                }
            }
            if row.table == 2 && row.limits[0].is_none() {
                return Err(format!(
                    "waste rule row '{}' must have a Column 1 limit",
                    row.id
                ));
            }
            if nulls_allowed && row.limits[1..].iter().any(Option::is_some) {
                return Err(format!(
                    "footnote-1 row '{}' must omit Columns 2 and 3",
                    row.id
                ));
            }
            let selector_key = canonical_selector(&row.selector)?;
            if row.table == 1 {
                let expected_unit =
                    if matches!(selector_key.as_str(), T1_ALPHA_SELECTOR | "Pu241" | "Cm242") {
                        "nCi/g"
                    } else {
                        "Ci/m3"
                    };
                if row.unit != expected_unit {
                    return Err(format!(
                        "waste rule row '{}' has unit inconsistent with its selector",
                        row.id
                    ));
                }
            }
            let existing = selectors.entry((row.table, selector_key)).or_default();
            if existing.iter().any(|prior| {
                *prior == row.applicability || *prior == "all" || row.applicability == "all"
            }) {
                return Err(format!(
                    "waste rule selector '{}' overlaps another row for this table",
                    row.selector
                ));
            }
            existing.insert(row.applicability.as_str());
            if (row.selector == T1_ALPHA_SELECTOR && (row.table != 1 || row.unit != "nCi/g"))
                || (row.selector == T2_SHORT_SELECTOR && (row.table != 2 || row.unit != "Ci/m3"))
            {
                return Err(format!(
                    "waste category row '{}' has incompatible table or unit",
                    row.id
                ));
            }
            if row.selector != T1_ALPHA_SELECTOR && row.selector != T2_SHORT_SELECTOR {
                match canonical_nuclide(&row.selector) {
                    Ok(_) => {}
                    Err(error) => return Err(format!("waste rule row '{}': {error}", row.id)),
                }
            }
        }
        for (table, selector, applicability) in [
            (1, "C14", "general"),
            (1, "C14", "activated_metal"),
            (1, "Ni59", "activated_metal"),
            (1, "Nb94", "activated_metal"),
            (1, "Tc99", "all"),
            (1, "I129", "all"),
            (1, T1_ALPHA_SELECTOR, "all"),
            (1, "Pu241", "all"),
            (1, "Cm242", "all"),
            (2, T2_SHORT_SELECTOR, "all"),
            (2, "H3", "all"),
            (2, "Co60", "all"),
            (2, "Ni63", "general"),
            (2, "Ni63", "activated_metal"),
            (2, "Sr90", "all"),
            (2, "Cs137", "all"),
        ] {
            let present = self.rows.iter().any(|row| {
                row.table == table
                    && row.applicability == applicability
                    && canonical_selector(&row.selector).ok().as_deref() == Some(selector)
            });
            if !present {
                return Err(format!("waste rule pack is missing required Table {table} selector '{selector}' for '{applicability}'"));
            }
        }
        Ok(())
    }

    fn applicable_rows(&self, table: u8, waste_type: WasteType) -> impl Iterator<Item = &RuleRow> {
        self.rows.iter().filter(move |row| {
            row.table == table
                && match row.applicability.as_str() {
                    "all" => true,
                    "general" => waste_type == WasteType::General,
                    "activated_metal" => waste_type == WasteType::ActivatedMetal,
                    _ => false,
                }
        })
    }
}

fn valid_sha256(value: &str) -> bool {
    value.len() == 64 && value.bytes().all(|byte| byte.is_ascii_hexdigit())
}

fn canonical_selector(raw: &str) -> Result<String, String> {
    if matches!(raw, T1_ALPHA_SELECTOR | T2_SHORT_SELECTOR) {
        return Ok(raw.to_string());
    }
    canonical_nuclide(raw)
}

fn canonical_nuclide(raw: &str) -> Result<String, String> {
    // Rule-pack keys use the conventional dashed form; inventory keys use ACTINV's native form.
    let normalized = if let Some(dash) = raw.find('-') {
        let (element, rest) = raw.split_at(dash);
        if !(1..=2).contains(&element.len())
            || rest.len() < 2
            || !rest.as_bytes()[1].is_ascii_digit()
        {
            return Err(format!("invalid nuclide identity '{raw}'"));
        }
        format!("{}{}", element, &rest[1..])
    } else {
        raw.to_string()
    };
    match composition::material_key(&normalized)? {
        MaterialKey::Nuclide { canonical, .. } => Ok(canonical),
        MaterialKey::Element(_) => Err(format!("'{raw}' is an element, not a nuclide")),
    }
}

/// Normalize an ACTINV or dashed nuclide key to the canonical ACTINV isotope identity.
pub fn normalize_nuclide_key(raw: &str) -> Result<String, String> {
    canonical_nuclide(raw)
}

fn nuclide_z(canonical: &str) -> Result<i32, String> {
    match composition::material_key(canonical)? {
        MaterialKey::Nuclide { za, .. } => Ok(za / 1000),
        MaterialKey::Element(_) => Err(format!("'{canonical}' is not a nuclide")),
    }
}

fn sha256_hex(bytes: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    Sha256::digest(bytes)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum WasteType {
    General,
    ActivatedMetal,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum WasteClass {
    #[serde(rename = "A")]
    A,
    #[serde(rename = "B")]
    B,
    #[serde(rename = "C")]
    C,
    AboveClassC,
    Unknown,
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct NuclideProperties {
    pub z: i32,
    pub half_life_s: f64,
    pub alpha_emitting: bool,
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct WasteGeometry {
    pub mass_g: f64,
    pub displaced_volume_cm3: f64,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Coverage {
    Complete,
    Incomplete,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct RowFraction {
    pub table: u8,
    pub column: Option<u8>,
    pub row_id: String,
    pub nuclide: String,
    pub unit: String,
    pub concentration: f64,
    pub limit: Option<f64>,
    pub fraction: Option<f64>,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ClassConstraint {
    pub target_class: WasteClass,
    pub table: u8,
    /// Table 1 uses `None`; Table 2 uses its 1-based column number.
    pub column: Option<u8>,
    pub source_sum_fraction: f64,
    /// Sum normalized to the selected class boundary, so the boundary is 1.0.
    pub normalized_sum: f64,
    /// Signed distance from the normalized boundary; strict mixtures require a positive margin.
    pub normalized_margin: f64,
    pub strict: bool,
    pub passes: bool,
    pub contributor_count: usize,
    pub contributors: Vec<String>,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Evaluation {
    pub class: WasteClass,
    pub calculated_only_class: WasteClass,
    pub coverage: Coverage,
    pub unknown_nuclides: Vec<String>,
    pub unlisted_nuclides: Vec<String>,
    pub unlisted_activity_bq: f64,
    pub row_fractions: Vec<RowFraction>,
    /// Constraints for A, B and C, including zero-valued constraints and null-limit columns.
    pub constraints: Vec<ClassConstraint>,
    pub binding_constraints: Vec<ClassConstraint>,
}

impl Evaluation {
    /// Return the independent Table 1 and Table 2 constraints for an at-most class target.
    pub fn constraints_for_class(
        &self,
        target_class: WasteClass,
    ) -> Result<Vec<ClassConstraint>, String> {
        if !matches!(target_class, WasteClass::A | WasteClass::B | WasteClass::C) {
            return Err("class constraints require target class A, B or C".into());
        }
        if self.coverage != Coverage::Complete {
            return Err("cannot derive class constraints from incomplete nuclide coverage".into());
        }
        Ok(self
            .constraints
            .iter()
            .filter(|constraint| constraint.target_class == target_class)
            .cloned()
            .collect())
    }
}

#[derive(Clone, Debug)]
struct NormalizedNuclide {
    activity_bq: f64,
    properties: NuclideProperties,
}

#[derive(Clone, Debug)]
struct RawConstraint {
    table: u8,
    column: Option<u8>,
    sum: f64,
    contributors: Vec<String>,
}

/// Evaluate one component's summed inventory activities against a selected Part 61 rule pack.
/// The caller supplies total activity per nuclide in Bq, not activity concentration.
pub fn evaluate_component(
    pack: &RulePack,
    waste_type: WasteType,
    geometry: WasteGeometry,
    activities_bq: &BTreeMap<String, f64>,
    properties: &BTreeMap<String, NuclideProperties>,
) -> Result<Evaluation, String> {
    // `RulePack` is deserializable for CLI/config use, so revalidate even if a caller bypassed
    // `RulePack::parse` and constructed a value directly through serde.
    pack.validate()?;
    if !geometry.mass_g.is_finite() || geometry.mass_g <= 0.0 {
        return Err("component mass must be positive and finite".into());
    }
    if !geometry.displaced_volume_cm3.is_finite() || geometry.displaced_volume_cm3 <= 0.0 {
        return Err("component displaced volume must be positive and finite".into());
    }

    let mut normalized_properties = BTreeMap::new();
    for (raw, value) in properties {
        let key = canonical_nuclide(raw)?;
        if normalized_properties.contains_key(&key) {
            return Err(format!(
                "duplicate nuclide properties after identity normalization: '{key}'"
            ));
        }
        let expected_z = nuclide_z(&key)?;
        if value.z != expected_z {
            return Err(format!(
                "nuclide properties Z={} does not match {key} (Z={expected_z})",
                value.z
            ));
        }
        if !value.half_life_s.is_finite() || value.half_life_s <= 0.0 {
            return Err(format!(
                "nuclide {key} half_life_s must be positive and finite"
            ));
        }
        normalized_properties.insert(key, *value);
    }

    let mut normalized_activity = BTreeMap::new();
    for (raw, activity) in activities_bq {
        if !activity.is_finite() || *activity < 0.0 {
            return Err(format!(
                "activity for '{raw}' must be nonnegative and finite"
            ));
        }
        let key = canonical_nuclide(raw)?;
        if normalized_activity.insert(key.clone(), *activity).is_some() {
            return Err(format!(
                "duplicate activity keys after identity normalization: '{key}'"
            ));
        }
    }

    let mut nuclides = BTreeMap::new();
    let mut unknown_nuclides = Vec::new();
    for (key, activity_bq) in normalized_activity {
        if activity_bq == 0.0 {
            continue;
        }
        match normalized_properties.get(&key) {
            Some(props) => {
                nuclides.insert(
                    key,
                    NormalizedNuclide {
                        activity_bq,
                        properties: *props,
                    },
                );
            }
            None => unknown_nuclides.push(key),
        }
    }

    let mut rows = Vec::new();
    let mut t1_sum = 0.0;
    let mut t1_contributors = Vec::new();
    let mut t2_sums = [0.0; 3];
    let mut t2_contributors: [Vec<String>; 3] = std::array::from_fn(|_| Vec::new());
    let mut classified_nuclides = BTreeSet::new();
    let mut unlisted_nuclides = Vec::new();
    let mut unlisted_activity_bq = 0.0;

    for (key, value) in &nuclides {
        let props = value.properties;
        let half_life_years = props.half_life_s / pack.year_s;
        let table1_specific = pack.applicable_rows(1, waste_type).find(|row| {
            row.selector != T1_ALPHA_SELECTOR
                && canonical_nuclide(&row.selector).ok().as_deref() == Some(key.as_str())
        });
        if let Some(row) = table1_specific {
            let (concentration, limit) =
                concentration_and_limit(pack, geometry, value.activity_bq, row)?;
            let fraction =
                fraction_from_activity(pack, geometry, value.activity_bq, &row.unit, limit)?;
            t1_sum = finite_add(t1_sum, fraction, "Table 1 sum")?;
            t1_contributors.push(key.clone());
            classified_nuclides.insert(key.clone());
            rows.push(row_fraction(
                row,
                None,
                key,
                concentration,
                Some(limit),
                Some(fraction),
            ));
        } else if let Some(row) = pack
            .applicable_rows(1, waste_type)
            .find(|row| row.selector == T1_ALPHA_SELECTOR)
            .filter(|_| props.z > 92 && props.alpha_emitting && half_life_years > 5.0)
        {
            let (concentration, limit) =
                concentration_and_limit(pack, geometry, value.activity_bq, row)?;
            let fraction =
                fraction_from_activity(pack, geometry, value.activity_bq, &row.unit, limit)?;
            t1_sum = finite_add(t1_sum, fraction, "Table 1 alpha-TRU sum")?;
            t1_contributors.push(key.clone());
            classified_nuclides.insert(key.clone());
            rows.push(row_fraction(
                row,
                None,
                key,
                concentration,
                Some(limit),
                Some(fraction),
            ));
        }

        let table2_specific = pack.applicable_rows(2, waste_type).find(|row| {
            row.selector != T2_SHORT_SELECTOR
                && canonical_nuclide(&row.selector).ok().as_deref() == Some(key.as_str())
        });
        if let Some(row) = table2_specific {
            add_table2_row(
                pack,
                geometry,
                row,
                key,
                value.activity_bq,
                &mut rows,
                &mut t2_sums,
                &mut t2_contributors,
            )?;
            classified_nuclides.insert(key.clone());
        } else if half_life_years < 5.0 {
            if let Some(row) = pack
                .applicable_rows(2, waste_type)
                .find(|row| row.selector == T2_SHORT_SELECTOR)
            {
                add_table2_row(
                    pack,
                    geometry,
                    row,
                    key,
                    value.activity_bq,
                    &mut rows,
                    &mut t2_sums,
                    &mut t2_contributors,
                )?;
                classified_nuclides.insert(key.clone());
            }
        }

        if !classified_nuclides.contains(key) {
            unlisted_nuclides.push(key.clone());
            unlisted_activity_bq =
                finite_add(unlisted_activity_bq, value.activity_bq, "unlisted activity")?;
        }
    }

    let raw_constraints = raw_constraints(t1_sum, &t1_contributors, t2_sums, &t2_contributors);
    let mut constraints = Vec::new();
    for target in [WasteClass::A, WasteClass::B, WasteClass::C] {
        constraints.extend(make_class_constraints(target, &raw_constraints)?);
    }
    let calculated_only_class = classify(&constraints);
    let coverage = if unknown_nuclides.is_empty() {
        Coverage::Complete
    } else {
        Coverage::Incomplete
    };
    let class = if coverage == Coverage::Complete {
        calculated_only_class
    } else {
        WasteClass::Unknown
    };
    let binding_constraints = binding_constraints(class, calculated_only_class, &constraints);

    Ok(Evaluation {
        class,
        calculated_only_class,
        coverage,
        unknown_nuclides,
        unlisted_nuclides,
        unlisted_activity_bq,
        row_fractions: rows,
        constraints,
        binding_constraints,
    })
}

fn concentration_and_limit(
    pack: &RulePack,
    geometry: WasteGeometry,
    activity_bq: f64,
    row: &RuleRow,
) -> Result<(f64, f64), String> {
    let concentration = concentration_for_unit(pack, geometry, activity_bq, &row.unit)?;
    let limit = row.limits[0].ok_or_else(|| format!("Table 1 row '{}' has no limit", row.id))?;
    Ok((concentration, limit))
}

fn concentration_for_unit(
    pack: &RulePack,
    geometry: WasteGeometry,
    activity_bq: f64,
    unit: &str,
) -> Result<f64, String> {
    let concentration = match unit {
        "Ci/m3" => activity_bq / pack.ci_bq / (geometry.displaced_volume_cm3 * 1.0e-6),
        "nCi/g" => activity_bq / (pack.ci_bq / 1.0e9) / geometry.mass_g,
        _ => return Err(format!("unsupported waste concentration unit '{unit}'")),
    };
    if !concentration.is_finite() || concentration < 0.0 {
        return Err("activity concentration overflowed or became invalid".into());
    }
    Ok(concentration)
}

fn finite_div(value: f64, limit: f64, label: &str) -> Result<f64, String> {
    let fraction = value / limit;
    if !fraction.is_finite() || fraction < 0.0 {
        return Err(format!("fraction for {label} overflowed or became invalid"));
    }
    Ok(fraction)
}

fn fraction_from_activity(
    pack: &RulePack,
    geometry: WasteGeometry,
    activity_bq: f64,
    unit: &str,
    limit: f64,
) -> Result<f64, String> {
    let denominator = match unit {
        "Ci/m3" => limit * (pack.ci_bq / 1.0e6) * geometry.displaced_volume_cm3,
        "nCi/g" => limit * (pack.ci_bq / 1.0e9) * geometry.mass_g,
        _ => return Err(format!("unsupported waste concentration unit '{unit}'")),
    };
    if !denominator.is_finite() || denominator <= 0.0 {
        return Err("waste fraction denominator overflowed or became invalid".into());
    }
    finite_div(activity_bq, denominator, "waste table fraction")
}

fn finite_add(total: f64, value: f64, label: &str) -> Result<f64, String> {
    let sum = total + value;
    if !sum.is_finite() || sum < 0.0 {
        return Err(format!("{label} overflowed or became invalid"));
    }
    Ok(sum)
}

fn row_fraction(
    row: &RuleRow,
    column: Option<u8>,
    key: &str,
    concentration: f64,
    limit: Option<f64>,
    fraction: Option<f64>,
) -> RowFraction {
    RowFraction {
        table: row.table,
        column,
        row_id: row.id.clone(),
        nuclide: key.to_string(),
        unit: row.unit.clone(),
        concentration,
        limit,
        fraction,
    }
}

// Keep the three independent column accumulators together with their inventory evidence.
#[allow(clippy::too_many_arguments)]
fn add_table2_row(
    pack: &RulePack,
    geometry: WasteGeometry,
    row: &RuleRow,
    key: &str,
    activity_bq: f64,
    rows: &mut Vec<RowFraction>,
    sums: &mut [f64; 3],
    contributors: &mut [Vec<String>; 3],
) -> Result<(), String> {
    let concentration = concentration_for_unit(pack, geometry, activity_bq, &row.unit)?;
    for column in 0..3 {
        if let Some(limit) = row.limits[column] {
            let fraction = fraction_from_activity(pack, geometry, activity_bq, &row.unit, limit)?;
            sums[column] = finite_add(sums[column], fraction, "Table 2 column sum")?;
            if activity_bq > 0.0 {
                contributors[column].push(key.to_string());
            }
            rows.push(row_fraction(
                row,
                Some((column + 1) as u8),
                key,
                concentration,
                Some(limit),
                Some(fraction),
            ));
        } else {
            rows.push(row_fraction(
                row,
                Some((column + 1) as u8),
                key,
                concentration,
                None,
                None,
            ));
        }
    }
    Ok(())
}

fn raw_constraints(
    t1_sum: f64,
    t1_contributors: &[String],
    t2_sums: [f64; 3],
    t2_contributors: &[Vec<String>; 3],
) -> Vec<RawConstraint> {
    let mut constraints = vec![RawConstraint {
        table: 1,
        column: None,
        sum: t1_sum,
        contributors: t1_contributors.to_vec(),
    }];
    for column in 0..3 {
        constraints.push(RawConstraint {
            table: 2,
            column: Some((column + 1) as u8),
            sum: t2_sums[column],
            contributors: t2_contributors[column].clone(),
        });
    }
    constraints
}

fn make_class_constraints(
    target: WasteClass,
    raw: &[RawConstraint],
) -> Result<Vec<ClassConstraint>, String> {
    let t1_threshold = if target == WasteClass::C { 1.0 } else { 0.1 };
    let t2_column = match target {
        WasteClass::A => 1,
        WasteClass::B => 2,
        WasteClass::C => 3,
        _ => unreachable!("only at-most classes have constraints"),
    };
    raw.iter()
        .filter(|constraint| constraint.table == 1 || constraint.column == Some(t2_column))
        .map(|constraint| {
            let normalized_sum = if constraint.table == 1 {
                finite_div(constraint.sum, t1_threshold, "class normalization")?
            } else {
                constraint.sum
            };
            let strict = constraint.contributors.len() > 1;
            let passes = if strict {
                normalized_sum < 1.0
            } else {
                normalized_sum <= 1.0
            };
            Ok(ClassConstraint {
                target_class: target,
                table: constraint.table,
                column: constraint.column,
                source_sum_fraction: constraint.sum,
                normalized_sum,
                normalized_margin: 1.0 - normalized_sum,
                strict,
                passes,
                contributor_count: constraint.contributors.len(),
                contributors: constraint.contributors.clone(),
            })
        })
        .collect()
}

fn classify(constraints: &[ClassConstraint]) -> WasteClass {
    for target in [WasteClass::A, WasteClass::B, WasteClass::C] {
        if constraints
            .iter()
            .filter(|constraint| constraint.target_class == target)
            .all(|constraint| constraint.passes)
        {
            return target;
        }
    }
    WasteClass::AboveClassC
}

fn binding_constraints(
    class: WasteClass,
    calculated_only_class: WasteClass,
    constraints: &[ClassConstraint],
) -> Vec<ClassConstraint> {
    let target = match class {
        WasteClass::Unknown => match calculated_only_class {
            WasteClass::AboveClassC => WasteClass::C,
            other => other,
        },
        WasteClass::AboveClassC => WasteClass::C,
        other => other,
    };
    let selected: Vec<ClassConstraint> = constraints
        .iter()
        .filter(|constraint| constraint.target_class == target)
        .cloned()
        .collect();
    if target == WasteClass::Unknown {
        return Vec::new();
    }
    let max = selected
        .iter()
        .map(|constraint| constraint.normalized_sum)
        .fold(0.0, f64::max);
    let failed = selected.iter().any(|constraint| !constraint.passes);
    selected
        .into_iter()
        .filter(|constraint| {
            (constraint.normalized_sum - max).abs() <= 1e-15 * max.abs().max(1.0)
                || (failed && !constraint.passes)
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::Value;

    #[test]
    fn listing_uses_part61_applicability_and_keeps_missing_properties_unknown() {
        let pack = RulePack::bundled().unwrap();
        let long_lived = |z| {
            Some(NuclideProperties {
                z,
                half_life_s: 100.0 * YEAR_S,
                alpha_emitting: false,
            })
        };
        assert_eq!(
            pack.nuclide_listed(WasteType::General, "C-14", None)
                .unwrap(),
            Some(true)
        );
        for (name, z) in [("Ni59", 28), ("Nb94", 41)] {
            assert_eq!(
                pack.nuclide_listed(WasteType::General, name, long_lived(z))
                    .unwrap(),
                Some(false)
            );
            assert_eq!(
                pack.nuclide_listed(WasteType::ActivatedMetal, name, long_lived(z))
                    .unwrap(),
                Some(true)
            );
        }
        assert_eq!(
            pack.nuclide_listed(WasteType::General, "Cl36", long_lived(17))
                .unwrap(),
            Some(false)
        );
        assert_eq!(
            pack.nuclide_listed(WasteType::General, "Mg24", None)
                .unwrap(),
            None
        );
        assert_eq!(
            pack.nuclide_listed(WasteType::General, "Mg24", long_lived(12))
                .unwrap(),
            Some(false)
        );
    }

    #[test]
    fn listing_uses_strict_half_life_groups_and_rejects_invalid_properties() {
        let pack = RulePack::bundled().unwrap();
        for (half_life_s, expected) in [
            (5.0 * YEAR_S - 1.0, Some(true)),
            (5.0 * YEAR_S, Some(false)),
            (5.0 * YEAR_S + 1.0, Some(false)),
        ] {
            assert_eq!(
                pack.nuclide_listed(
                    WasteType::General,
                    "Xe135",
                    Some(NuclideProperties {
                        z: 54,
                        half_life_s,
                        alpha_emitting: false,
                    }),
                )
                .unwrap(),
                expected
            );
        }
        let alpha = NuclideProperties {
            z: 95,
            half_life_s: 100.0 * YEAR_S,
            alpha_emitting: true,
        };
        assert_eq!(
            pack.nuclide_listed(WasteType::General, "Am242", Some(alpha))
                .unwrap(),
            Some(true)
        );
        assert!(pack
            .nuclide_listed(
                WasteType::General,
                "Am242",
                Some(NuclideProperties { z: 94, ..alpha }),
            )
            .is_err());
        for half_life_s in [0.0, -1.0, f64::INFINITY, f64::NAN] {
            assert!(pack
                .nuclide_listed(
                    WasteType::General,
                    "Am242",
                    Some(NuclideProperties {
                        half_life_s,
                        ..alpha
                    }),
                )
                .is_err());
        }
    }

    fn props(entries: &[(&str, i32, f64, bool)]) -> BTreeMap<String, NuclideProperties> {
        entries
            .iter()
            .map(|(name, z, half_life_s, alpha_emitting)| {
                (
                    (*name).to_string(),
                    NuclideProperties {
                        z: *z,
                        half_life_s: *half_life_s,
                        alpha_emitting: *alpha_emitting,
                    },
                )
            })
            .collect()
    }

    fn evaluate(activities: &[(&str, f64)], properties: &[(&str, i32, f64, bool)]) -> Evaluation {
        let pack = RulePack::bundled().unwrap();
        evaluate_component(
            &pack,
            WasteType::General,
            WasteGeometry {
                mass_g: 1000.0,
                displaced_volume_cm3: 1000.0,
            },
            &activities
                .iter()
                .map(|(key, activity)| ((*key).to_string(), *activity))
                .collect(),
            &props(properties),
        )
        .unwrap()
    }

    #[test]
    fn sr90_cs137_worked_example_is_class_b() {
        // 1 Ci/m3 in the component volume is exactly 3.7e7 Bq for 1 L.
        let volume_l = 1000.0;
        let bq_per_ci_m3 = 37_000_000_000.0 * volume_l * 1e-6;
        let result = evaluate(
            &[
                ("Sr90", 50.0 * bq_per_ci_m3),
                ("Cs137", 22.0 * bq_per_ci_m3),
            ],
            &[
                ("Sr90", 38, 28.8 * YEAR_S, false),
                ("Cs137", 55, 30.0 * YEAR_S, false),
            ],
        );
        assert_eq!(result.class, WasteClass::B);
        let col2 = result
            .constraints_for_class(WasteClass::B)
            .unwrap()
            .into_iter()
            .find(|c| c.table == 2)
            .unwrap();
        assert!((col2.source_sum_fraction - 5.0 / 6.0).abs() < 1e-14);
        assert!((col2.normalized_margin - 1.0 / 6.0).abs() < 1e-14);
    }

    #[test]
    fn class_normalization_overflow_is_an_error() {
        let raw = [RawConstraint {
            table: 1,
            column: None,
            sum: f64::MAX,
            contributors: vec!["Tc99".into()],
        }];
        assert!(make_class_constraints(WasteClass::A, &raw).is_err());
    }

    #[test]
    fn table1_single_nuclide_boundaries_are_inclusive_and_mix_boundaries_strict() {
        let pack = RulePack::bundled().unwrap();
        let geom = WasteGeometry {
            mass_g: 1.0,
            displaced_volume_cm3: 1.0,
        };
        let c14 = props(&[("C14", 6, 10.0 * YEAR_S, false)]);
        let bq_for_ci = |ci_m3: f64| ci_m3 * CI_BQ * 1e-6;
        let one = |activity| {
            evaluate_component(
                &pack,
                WasteType::General,
                geom,
                &BTreeMap::from([("C14".into(), activity)]),
                &c14,
            )
            .unwrap()
        };
        assert_eq!(one(bq_for_ci(0.8)).class, WasteClass::A);
        assert_eq!(one(bq_for_ci(8.0)).class, WasteClass::C);
        assert_eq!(one(bq_for_ci(8.0 + 1e-9)).class, WasteClass::AboveClassC);

        let two_props = props(&[
            ("C14", 6, 10.0 * YEAR_S, false),
            ("Tc99", 43, 2.1e5 * YEAR_S, false),
        ]);
        let two_at_a = BTreeMap::from([
            ("C14".into(), bq_for_ci(0.4)),
            ("Tc99".into(), bq_for_ci(0.15)),
        ]);
        let two_at_c = BTreeMap::from([
            ("C14".into(), bq_for_ci(4.0)),
            ("Tc99".into(), bq_for_ci(1.5)),
        ]);
        let at_a =
            evaluate_component(&pack, WasteType::General, geom, &two_at_a, &two_props).unwrap();
        let at_c =
            evaluate_component(&pack, WasteType::General, geom, &two_at_c, &two_props).unwrap();
        assert_eq!(at_a.class, WasteClass::C); // S1=0.1 is not strictly below the A threshold.
        assert_eq!(at_c.class, WasteClass::AboveClassC); // S1=1 is not strictly below C.
    }

    #[test]
    fn activated_metal_variants_replace_general_rows() {
        let pack = RulePack::bundled().unwrap();
        let geom = WasteGeometry {
            mass_g: 1.0,
            displaced_volume_cm3: 1.0,
        };
        let activities = BTreeMap::from([("C14".into(), 40.0 * CI_BQ * 1e-6)]);
        let properties = props(&[("C14", 6, 10.0 * YEAR_S, false)]);
        let general =
            evaluate_component(&pack, WasteType::General, geom, &activities, &properties).unwrap();
        let metal = evaluate_component(
            &pack,
            WasteType::ActivatedMetal,
            geom,
            &activities,
            &properties,
        )
        .unwrap();
        assert_eq!(general.class, WasteClass::AboveClassC);
        assert_eq!(metal.class, WasteClass::C);
        assert_eq!(metal.row_fractions[0].row_id, "C-14_activated_metal");
    }

    #[test]
    fn pu241_uses_its_specific_mass_based_limit() {
        let pack = RulePack::bundled().unwrap();
        let result = evaluate_component(
            &pack,
            WasteType::General,
            WasteGeometry {
                mass_g: 1.0,
                displaced_volume_cm3: 1.0,
            },
            &BTreeMap::from([("Pu241".into(), 3_500.0 * 37.0)]),
            &props(&[("Pu241", 94, 14.3 * YEAR_S, true)]),
        )
        .unwrap();
        assert_eq!(result.class, WasteClass::C);
        assert_eq!(result.row_fractions.len(), 1);
        assert_eq!(result.row_fractions[0].row_id, "Pu-241");
        assert!((result.row_fractions[0].concentration - 3_500.0).abs() < 1e-12);
    }

    #[test]
    fn table2_mixture_column_boundaries_are_strict() {
        let one_ci_m3_bq = CI_BQ * 1.0e-3;
        let result = evaluate(
            &[("Sr90", 0.02 * one_ci_m3_bq), ("Cs137", 0.5 * one_ci_m3_bq)],
            &[
                ("Sr90", 38, 28.8 * YEAR_S, false),
                ("Cs137", 55, 30.0 * YEAR_S, false),
            ],
        );
        assert_eq!(result.class, WasteClass::B);
        let column1 = result
            .constraints_for_class(WasteClass::A)
            .unwrap()
            .into_iter()
            .find(|constraint| constraint.table == 2)
            .unwrap();
        assert!(column1.strict);
        assert!(!column1.passes);
        assert!((column1.source_sum_fraction - 1.0).abs() < 1e-15);
    }

    #[test]
    fn short_lived_aggregate_captures_cm242_and_dedicated_table1_row() {
        let result = evaluate(
            &[("Cm242", 37.0)],
            &[("Cm242", 96, 162.0 * 24.0 * 3600.0, true)],
        );
        assert!(result
            .row_fractions
            .iter()
            .any(|row| row.table == 1 && row.row_id == "Cm-242"));
        assert!(result
            .row_fractions
            .iter()
            .any(|row| row.table == 2 && row.row_id == "half_life_lt5y"));
    }

    #[test]
    fn unknown_active_nuclide_makes_class_unknown_but_keeps_calculated_only_class() {
        let pack = RulePack::bundled().unwrap();
        let result = evaluate_component(
            &pack,
            WasteType::General,
            WasteGeometry {
                mass_g: 1.0,
                displaced_volume_cm3: 1.0,
            },
            &BTreeMap::from([("C14".into(), 0.0), ("Xe135".into(), 1.0)]),
            &BTreeMap::new(),
        )
        .unwrap();
        assert_eq!(result.class, WasteClass::Unknown);
        assert_eq!(result.calculated_only_class, WasteClass::A);
        assert_eq!(result.unknown_nuclides, vec!["Xe135"]);
        assert!(result.constraints_for_class(WasteClass::A).is_err());
    }

    #[test]
    fn rejects_alias_duplicates_invalid_properties_and_nonfinite_activity() {
        let pack = RulePack::bundled().unwrap();
        let geom = WasteGeometry {
            mass_g: 1.0,
            displaced_volume_cm3: 1.0,
        };
        let duplicate = BTreeMap::from([("Nb94".into(), 1.0), ("Nb-94".into(), 2.0)]);
        assert!(evaluate_component(
            &pack,
            WasteType::ActivatedMetal,
            geom,
            &duplicate,
            &BTreeMap::new()
        )
        .is_err());
        let mismatch = BTreeMap::from([(
            "Nb94".into(),
            NuclideProperties {
                z: 40,
                half_life_s: 1.0,
                alpha_emitting: false,
            },
        )]);
        assert!(evaluate_component(
            &pack,
            WasteType::ActivatedMetal,
            geom,
            &BTreeMap::from([("Nb94".into(), 1.0)]),
            &mismatch
        )
        .is_err());
        assert!(evaluate_component(
            &pack,
            WasteType::General,
            geom,
            &BTreeMap::from([("C14".into(), f64::NAN)]),
            &BTreeMap::new()
        )
        .is_err());
    }

    #[test]
    fn malformed_rule_pack_fails_closed() {
        let doc: Value = serde_json::from_str(BUNDLED_RULES).unwrap();
        let mut malformed = doc.clone();
        malformed["rows"][0]["limits"][0] = Value::from(0.0);
        assert!(RulePack::parse(&malformed.to_string()).is_err());
        let mut malformed = doc;
        malformed["rows"][0]["limits"] = serde_json::json!([8.0, 9.0]);
        assert!(RulePack::parse(&malformed.to_string()).is_err());
    }

    #[test]
    fn exposes_separate_zero_and_nonzero_class_constraints() {
        let result = evaluate(
            &[("C14", CI_BQ * 1e-6)],
            &[("C14", 6, 10.0 * YEAR_S, false)],
        );
        let target = result.constraints_for_class(WasteClass::C).unwrap();
        assert_eq!(target.len(), 2);
        assert!(target
            .iter()
            .any(|c| c.table == 1 && c.normalized_sum > 0.0));
        assert!(target
            .iter()
            .any(|c| c.table == 2 && c.source_sum_fraction == 0.0));
        assert!(result.constraints_for_class(WasteClass::Unknown).is_err());
    }
}
