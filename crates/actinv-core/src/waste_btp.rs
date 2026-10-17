//! Numbers of the NRC Concentration Averaging and Encapsulation Branch Technical Position
//! (Revision 1, Volume 1, February 2015) used by the mixed-package command.
//!
//! The bundled file carries BTP Tables 2 and 3 and the thresholds of BTP Sections 3.3.2 to 3.3.4 as printed in the
//! SI units of the BTP. BTP Table 1 (blending) and Table 4 (volume and mass basis) are not data: blending is out of
//! scope and the volume basis is chosen per item by the caller. This module only loads and validates the numbers;
//! the averaging rules are applied by `actinv waste package`.

use std::collections::{BTreeMap, BTreeSet};

use serde::{Deserialize, Serialize};

use crate::waste::normalize_nuclide_key;

const BUNDLED_BTP: &str = include_str!("../data/waste_us_nrc_btp_2015_v1.json");
/// Key of the alpha-emitting transuranic group (half-life above 5 years, excluding Pu-241 and Cm-242) in BTP Table 3.
pub const ALPHA_TRU_KEY: &str = "alpha_transuranic_gt5y";

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct BtpSource {
    pub title: String,
    pub adams: String,
    pub sha256_2015_original: String,
    pub review: String,
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct BtpThresholds {
    /// Activity of one item below which the mixture may be averaged whole (BTP 3.3.2.1), Bq.
    pub screen_activity_bq: f64,
    /// Items of at least this displaced volume are treated individually for BTP Table 2, cm3.
    pub small_item_cm3: f64,
    pub factor_controlling: f64,
    pub factor_other: f64,
    /// Share of the more restrictive CFR table that makes the primary gammas classification-controlling.
    pub controlling_share: f64,
    /// A radionuclide of concern exceeds this fraction of its class limit.
    pub concern_fraction: f64,
    pub encapsulation_min_loading: f64,
    pub encapsulation_max_volume_cm3: f64,
    pub low_loading_max_volume_cm3: f64,
    pub low_loading_max_mass_g: f64,
    pub encapsulant_max_density_g_cm3: f64,
}

/// Limits by nuclide in the order Class A, B, C; `None` is "No limit".
type Columns = [Option<f64>; 3];

#[derive(Clone, Debug, Default)]
struct Canonical {
    primary: Vec<String>,
    table2: BTreeMap<String, Columns>,
    table3: BTreeMap<String, Columns>,
    tru: Columns,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct BtpData {
    pub schema: String,
    pub id: String,
    pub source: BtpSource,
    pub primary_gamma: Vec<String>,
    pub table2_bq: BTreeMap<String, Columns>,
    pub table3_bq: BTreeMap<String, Columns>,
    pub thresholds: BtpThresholds,
    #[serde(skip)]
    pub sha256: String,
    #[serde(skip)]
    canonical: Canonical,
}

impl BtpData {
    /// Parse and validate an `actinv-waste-btp-1` document.
    pub fn parse(text: &str) -> Result<Self, String> {
        let mut data: Self = serde_json::from_str(text)
            .map_err(|error| format!("BTP data is not valid JSON: {error}"))?;
        data.canonical = data.validate()?;
        data.sha256 = sha256_hex(text.as_bytes());
        Ok(data)
    }

    /// The bundled BTP numbers. Callers must still select them explicitly.
    pub fn bundled() -> Result<Self, String> {
        Self::parse(BUNDLED_BTP)
    }

    /// Primary gamma emitters in the file's order, as canonical ACTINV nuclide keys.
    pub fn primary_gammas(&self) -> &[String] {
        &self.canonical.primary
    }

    /// BTP Table 2 limit in Bq for a canonical nuclide and class index (A, B, C = 0, 1, 2); `None` is no limit or a
    /// nuclide outside the table.
    pub fn table2_limit_bq(&self, nuclide: &str, class_index: usize) -> Option<f64> {
        self.canonical
            .table2
            .get(nuclide)
            .and_then(|columns| columns.get(class_index).copied().flatten())
    }

    /// BTP Table 3 nuclides other than the transuranic group, as canonical keys with their A, B, C limits in Bq.
    pub fn table3_nuclides(&self) -> impl Iterator<Item = (&str, &[Option<f64>; 3])> {
        self.canonical
            .table3
            .iter()
            .map(|(nuclide, columns)| (nuclide.as_str(), columns))
    }

    /// `Some(limit)` when the nuclide is in BTP Table 3 (`limit` is `None` for "No limit"), `None` otherwise.
    pub fn table3_limit_bq(&self, nuclide: &str, class_index: usize) -> Option<Option<f64>> {
        self.canonical
            .table3
            .get(nuclide)
            .map(|columns| columns.get(class_index).copied().flatten())
    }

    /// BTP Table 3 limit in Bq of the alpha-emitting transuranic group; `None` is no limit.
    pub fn table3_tru_limit_bq(&self, class_index: usize) -> Option<f64> {
        self.canonical.tru.get(class_index).copied().flatten()
    }

    fn validate(&self) -> Result<Canonical, String> {
        if self.schema != "actinv-waste-btp-1" {
            return Err(format!("unsupported BTP data schema '{}'", self.schema));
        }
        if self.id.trim().is_empty() {
            return Err("BTP data id is required".into());
        }
        if self.source.title.trim().is_empty()
            || self.source.adams.trim().is_empty()
            || self.source.review.trim().is_empty()
            || !valid_sha256(&self.source.sha256_2015_original)
        {
            return Err("BTP data source metadata or source SHA-256 is invalid".into());
        }
        let t = &self.thresholds;
        let positive = [
            t.screen_activity_bq,
            t.small_item_cm3,
            t.factor_controlling,
            t.factor_other,
            t.encapsulation_max_volume_cm3,
            t.low_loading_max_volume_cm3,
            t.low_loading_max_mass_g,
            t.encapsulant_max_density_g_cm3,
        ];
        if positive.iter().any(|v| !(v.is_finite() && *v > 0.0)) {
            return Err("BTP data thresholds must be positive and finite".into());
        }
        for (name, value) in [
            ("controlling_share", t.controlling_share),
            ("concern_fraction", t.concern_fraction),
        ] {
            if !(value > 0.0 && value < 1.0) {
                return Err(format!("BTP data threshold {name} must be in (0, 1)"));
            }
        }
        if !(t.encapsulation_min_loading > 0.0 && t.encapsulation_min_loading <= 1.0) {
            return Err("BTP data threshold encapsulation_min_loading must be in (0, 1]".into());
        }

        let mut canonical = Canonical::default();
        let mut seen = BTreeSet::new();
        for raw in &self.primary_gamma {
            let key = normalize_nuclide_key(raw)?;
            if !seen.insert(key.clone()) {
                return Err(format!("BTP primary gamma '{raw}' is repeated"));
            }
            canonical.primary.push(key);
        }
        if canonical.primary.is_empty() {
            return Err("BTP data lists no primary gamma emitter".into());
        }
        for (raw, columns) in &self.table2_bq {
            check_columns("Table 2", raw, columns)?;
            let key = normalize_nuclide_key(raw)?;
            if !seen.contains(&key) || canonical.table2.insert(key, *columns).is_some() {
                return Err(format!(
                    "BTP Table 2 nuclide '{raw}' is not a unique primary gamma"
                ));
            }
        }
        if canonical.table2.len() != canonical.primary.len() {
            return Err("BTP Table 2 must list every primary gamma emitter".into());
        }
        let mut tru = None;
        for (raw, columns) in &self.table3_bq {
            check_columns("Table 3", raw, columns)?;
            if raw == ALPHA_TRU_KEY {
                tru = Some(*columns);
            } else if canonical
                .table3
                .insert(normalize_nuclide_key(raw)?, *columns)
                .is_some()
            {
                return Err(format!("BTP Table 3 nuclide '{raw}' is repeated"));
            }
        }
        canonical.tru = tru.ok_or_else(|| format!("BTP Table 3 has no {ALPHA_TRU_KEY} row"))?;
        Ok(canonical)
    }
}

fn check_columns(table: &str, name: &str, columns: &Columns) -> Result<(), String> {
    if columns
        .iter()
        .flatten()
        .any(|value| !(value.is_finite() && *value > 0.0))
        || columns.iter().all(Option::is_none)
    {
        return Err(format!(
            "BTP {table} row '{name}' needs positive limits or null, and at least one limit"
        ));
    }
    Ok(())
}

fn valid_sha256(value: &str) -> bool {
    value.len() == 64 && value.bytes().all(|byte| byte.is_ascii_hexdigit())
}

fn sha256_hex(bytes: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    Sha256::digest(bytes)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::Value;

    /// 1 Ci in Bq, for the BTP's parenthetical conventional units.
    const CI_BQ: f64 = 3.7e10;

    fn bundled() -> BtpData {
        BtpData::bundled().unwrap()
    }

    #[test]
    fn table2_values_are_the_btp_si_numbers() {
        let btp = bundled();
        assert_eq!(btp.primary_gammas(), ["Co60", "Nb94", "Cs137"]);
        // BTP Table 2 (V1 p.25): 60Co 5.2 TBq / no limit / no limit; 94Nb 37 MBq in every class;
        // 137Cs 266 MBq, 27 GBq, 4.8 TBq.
        assert_eq!(btp.table2_limit_bq("Co60", 0), Some(5.2e12));
        assert_eq!(btp.table2_limit_bq("Co60", 1), None);
        assert_eq!(btp.table2_limit_bq("Co60", 2), None);
        for class in 0..3 {
            assert_eq!(btp.table2_limit_bq("Nb94", class), Some(3.7e7));
        }
        assert_eq!(btp.table2_limit_bq("Cs137", 0), Some(2.66e8));
        assert_eq!(btp.table2_limit_bq("Cs137", 1), Some(2.7e10));
        assert_eq!(btp.table2_limit_bq("Cs137", 2), Some(4.8e12));
        assert_eq!(btp.table2_limit_bq("Ni63", 0), None);
        assert_eq!(btp.table2_limit_bq("Cs137", 3), None);
    }

    #[test]
    fn table3_values_are_the_btp_si_numbers() {
        let btp = bundled();
        // BTP Table 3 (V1 p.26): columns are "Class A or B" (both given) and "Class C".
        let expected: [(&str, [Option<f64>; 3]); 4] = [
            ("H3", [Some(3.0e11), Some(3.0e11), None]),
            ("C14", [Some(4.0e10), Some(4.0e10), Some(4.0e11)]),
            ("Ni59", [Some(1.5e11), Some(1.5e11), Some(1.5e12)]),
            ("Ni63", [Some(2.6e11), Some(2.6e11), Some(5.5e13)]),
        ];
        assert_eq!(btp.table3_nuclides().count(), expected.len());
        for (nuclide, columns) in expected {
            for (class, limit) in columns.iter().enumerate() {
                assert_eq!(
                    btp.table3_limit_bq(nuclide, class),
                    Some(*limit),
                    "{nuclide}"
                );
            }
        }
        assert_eq!(btp.table3_limit_bq("Cs137", 0), None);
        assert_eq!(btp.table3_tru_limit_bq(0), Some(1.11e8));
        assert_eq!(btp.table3_tru_limit_bq(1), Some(1.11e8));
        assert_eq!(btp.table3_tru_limit_bq(2), Some(1.1e9));
    }

    #[test]
    fn si_numbers_agree_with_the_btp_conventional_units() {
        let btp = bundled();
        // The conventional units printed beside the SI numbers; the BTP rounds each pair differently.
        let pairs = [
            (btp.table2_limit_bq("Co60", 0).unwrap(), 140.0),
            (btp.table2_limit_bq("Nb94", 0).unwrap(), 1.0e-3),
            (btp.table2_limit_bq("Cs137", 0).unwrap(), 7.2e-3),
            (btp.table2_limit_bq("Cs137", 1).unwrap(), 0.72),
            (btp.table2_limit_bq("Cs137", 2).unwrap(), 130.0),
            (btp.table3_limit_bq("H3", 0).unwrap().unwrap(), 8.0),
            (btp.table3_limit_bq("C14", 0).unwrap().unwrap(), 1.0),
            (btp.table3_limit_bq("C14", 2).unwrap().unwrap(), 10.0),
            (btp.table3_limit_bq("Ni59", 0).unwrap().unwrap(), 4.0),
            (btp.table3_limit_bq("Ni59", 2).unwrap().unwrap(), 40.0),
            (btp.table3_limit_bq("Ni63", 0).unwrap().unwrap(), 7.0),
            (btp.table3_limit_bq("Ni63", 2).unwrap().unwrap(), 1500.0),
            (btp.table3_tru_limit_bq(0).unwrap(), 3.0e-3),
            (btp.table3_tru_limit_bq(2).unwrap(), 30.0e-3),
        ];
        for (bq, ci) in pairs {
            assert!(
                (bq / (ci * CI_BQ) - 1.0).abs() < 0.09,
                "{bq} Bq against {ci} Ci"
            );
        }
    }

    #[test]
    fn thresholds_are_the_btp_values() {
        let t = bundled().thresholds;
        assert_eq!(t.screen_activity_bq, 3.7e7); // 37 MBq (1 mCi), V1 p.22
        assert_eq!(t.small_item_cm3, 280.0); // 280 cc (0.01 ft3), V1 p.26
        assert_eq!(t.factor_controlling, 2.0); // V1 p.25
        assert_eq!(t.factor_other, 10.0); // V1 p.25
        assert_eq!(t.controlling_share, 0.5); // V1 p.25
        assert_eq!(t.concern_fraction, 0.01); // V1 App. A p.45
        assert_eq!(t.encapsulation_min_loading, 0.14); // V1 p.30
        assert_eq!(t.encapsulation_max_volume_cm3, 9_372_876.221_952); // 331 ft3 = 9.373 m3
        assert!((t.encapsulation_max_volume_cm3 - 331.0 * 28_316.846_592).abs() < 1e-6);
        assert_eq!(t.low_loading_max_volume_cm3, 200_000.0); // 0.2 m3
        assert_eq!(t.low_loading_max_mass_g, 500_000.0); // 500 kg
        assert_eq!(t.encapsulant_max_density_g_cm3, 2.5); // V1 p.31 fn.43
    }

    #[test]
    fn source_metadata_and_hash_are_recorded() {
        let btp = bundled();
        assert_eq!(btp.id, "us-nrc-ca-btp-2015-rev1-v1");
        assert_eq!(btp.source.adams, "ML12254B065");
        assert_eq!(
            btp.source.sha256_2015_original,
            "4e0e3df3017b0f546630c94fbbd637c7fb4ee124f830afa575dfc05ad67ba7eb"
        );
        assert_eq!(btp.sha256, sha256_hex(BUNDLED_BTP.as_bytes()));
        assert_eq!(btp.sha256.len(), 64);
    }

    #[test]
    fn repository_mirror_has_the_same_bytes() {
        // `data/` is the canonical copy and the crate directory carries the bytes into the published tarball.
        let canonical = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../data/waste_us_nrc_btp_2015_v1.json");
        if let Ok(text) = std::fs::read_to_string(canonical) {
            assert_eq!(text, BUNDLED_BTP);
        }
    }

    #[test]
    fn malformed_data_fails_closed() {
        let doc: Value = serde_json::from_str(BUNDLED_BTP).unwrap();
        let edits: [(&str, Value); 9] = [
            ("/schema", Value::from("actinv-waste-btp-0")),
            ("/table2_bq/Nb-94/0", Value::from(0.0)),
            ("/table2_bq/Nb-94/1", Value::from(-1.0)),
            ("/table2_bq/Nb-94", serde_json::json!([null, null, null])),
            ("/table2_bq/Nb-94", serde_json::json!([1.0, 2.0])),
            ("/table3_bq/H-3/0", Value::from(-5.0)),
            ("/thresholds/controlling_share", Value::from(1.5)),
            ("/thresholds/small_item_cm3", Value::from(0.0)),
            ("/source/sha256_2015_original", Value::from("abc")),
        ];
        for (pointer, value) in edits {
            let mut edited = doc.clone();
            *edited.pointer_mut(pointer).unwrap() = value;
            assert!(BtpData::parse(&edited.to_string()).is_err(), "{pointer}");
        }
        // a missing primary gamma row, an extra Table 2 nuclide, no transuranic row and unknown keys
        let mut missing = doc.clone();
        missing["table2_bq"]
            .as_object_mut()
            .unwrap()
            .remove("Cs-137");
        assert!(BtpData::parse(&missing.to_string()).is_err());
        let mut extra = doc.clone();
        extra["table2_bq"]["Ni-63"] = serde_json::json!([1.0, 1.0, 1.0]);
        assert!(BtpData::parse(&extra.to_string()).is_err());
        let mut no_tru = doc.clone();
        no_tru["table3_bq"]
            .as_object_mut()
            .unwrap()
            .remove(ALPHA_TRU_KEY);
        assert!(BtpData::parse(&no_tru.to_string()).is_err());
        let mut unknown = doc;
        unknown["extra"] = Value::from(1);
        assert!(BtpData::parse(&unknown.to_string()).is_err());
    }
}
