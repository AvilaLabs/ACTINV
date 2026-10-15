#![allow(non_snake_case)] // JSON wire names carry their physical units.
//! Strict, hash-pinned radiological response tables and per-step evaluation.

use actinv_data::composition::{self, MaterialKey};
use serde::de::{Error as _, MapAccess, Visitor};
use serde::{Deserialize, Deserializer, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, HashSet};
use std::fmt;

const TABLE_FORMAT: &str = "actinv-radiological-table-1";

/// Prefix of a `radiological.table.path` that names a table shipped inside the binary.
pub const BUNDLED_PREFIX: &str = "bundled:";

/// Regulatory tables converted from public sources (`scripts/regulatory/`). Each is opt-in by
/// name; none is ever a default. Per-row source text lives in the `*.rows.json` sidecars next
/// to the tables, which are not read at run time.
const BUNDLED_TABLES: &[(&str, &str)] = &[
    (
        "euratom-2013-59-annex-vii-a",
        include_str!("../data/regulatory/euratom-2013-59-annex-vii-a.json"),
    ),
    (
        "euratom-2013-59-annex-vii-b-activity",
        include_str!("../data/regulatory/euratom-2013-59-annex-vii-b-activity.json"),
    ),
    (
        "euratom-2013-59-annex-vii-b-concentration",
        include_str!("../data/regulatory/euratom-2013-59-annex-vii-b-concentration.json"),
    ),
    (
        "us-49cfr173-a1",
        include_str!("../data/regulatory/us-49cfr173-a1.json"),
    ),
    (
        "us-49cfr173-a2",
        include_str!("../data/regulatory/us-49cfr173-a2.json"),
    ),
    (
        "us-49cfr173-exempt-concentration",
        include_str!("../data/regulatory/us-49cfr173-exempt-concentration.json"),
    ),
    (
        "us-49cfr173-exempt-consignment",
        include_str!("../data/regulatory/us-49cfr173-exempt-consignment.json"),
    ),
];

/// Names of the bundled tables, in alphabetical order.
pub fn bundled_table_names() -> Vec<&'static str> {
    BUNDLED_TABLES.iter().map(|(name, _)| *name).collect()
}

/// The table name in a `bundled:NAME` path, or `None` for an ordinary file path.
pub fn bundled_name(path: &str) -> Option<&str> {
    path.strip_prefix(BUNDLED_PREFIX)
}

/// Text of a bundled table. An unknown name is an error that lists the available names;
/// there is no fallback table.
pub fn bundled_table(name: &str) -> Result<&'static str, String> {
    BUNDLED_TABLES
        .iter()
        .find(|(known, _)| *known == name)
        .map(|(_, text)| *text)
        .ok_or_else(|| {
            format!(
                "unknown bundled radiological table '{name}'; available: {}",
                bundled_table_names().join(", ")
            )
        })
}

/// SHA-256 of a bundled table's bytes: the digest a spec must declare.
pub fn bundled_sha256(name: &str) -> Result<String, String> {
    Ok(format!(
        "{:x}",
        Sha256::digest(bundled_table(name)?.as_bytes())
    ))
}

/// Text and SHA-256 of a bundled table. A declared digest that differs is an error, as for a
/// table read from a file.
pub fn read_bundled(name: &str, declared: Option<&str>) -> Result<(String, String), String> {
    let text = bundled_table(name)?;
    let computed = bundled_sha256(name)?;
    if let Some(expected) = declared {
        if !computed.eq_ignore_ascii_case(expected) {
            return Err(format!(
                "SHA-256 mismatch for {BUNDLED_PREFIX}{name}: declared {expected}, computed {computed}"
            ));
        }
    }
    Ok((text.to_owned(), computed))
}

/// Source metadata. The first four fields are required. The rest are optional provenance that
/// bundled and generated tables carry; an absent field is not serialized, so certificates of
/// tables without them are unchanged.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RadiologicalSource {
    citation: String,
    edition: String,
    url: String,
    jurisdiction: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    scope: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    retrieved: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    source_file: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    source_sha256: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    converter: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    licence: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    row_provenance: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RadiologicalKind {
    ClearanceIndex,
    WasteIndex,
    IngestionDose,
    InhalationDose,
    /// FISPACT-II `ATWO` transport index: activity over the nuclide's A2 value, summed.
    #[serde(rename = "a2")]
    A2,
    /// Any absolute activity limit in Bq (A1, exempt consignment): activity over the limit, summed.
    ActivityLimit,
}

impl RadiologicalKind {
    fn name(self) -> &'static str {
        match self {
            Self::ClearanceIndex => "clearance_index",
            Self::WasteIndex => "waste_index",
            Self::IngestionDose => "ingestion_dose",
            Self::InhalationDose => "inhalation_dose",
            Self::A2 => "a2",
            Self::ActivityLimit => "activity_limit",
        }
    }

    fn output_unit(self) -> &'static str {
        match self {
            Self::ClearanceIndex | Self::WasteIndex => "dimensionless",
            Self::IngestionDose | Self::InhalationDose => "Sv/g_material_intake",
            Self::A2 => "A2_per_g_material",
            Self::ActivityLimit => "limits_per_g_material",
        }
    }

    fn coefficient_unit(self) -> &'static str {
        match self {
            Self::ClearanceIndex | Self::WasteIndex => "Bq/kg",
            Self::IngestionDose | Self::InhalationDose => "Sv/Bq",
            Self::A2 | Self::ActivityLimit => "Bq",
        }
    }

    fn formula(self) -> &'static str {
        match self {
            Self::ClearanceIndex | Self::WasteIndex => {
                "sum(1000 * activity_Bq_per_g / limit_Bq_per_kg)"
            }
            Self::IngestionDose | Self::InhalationDose => {
                "sum(activity_Bq_per_g * dose_coefficient_Sv_per_Bq)"
            }
            Self::A2 => "sum(activity_Bq_per_g / A2_Bq)",
            Self::ActivityLimit => "sum(activity_Bq_per_g / limit_Bq)",
        }
    }

    fn contribution(self, activity: f64, coefficient: f64) -> f64 {
        match self {
            Self::ClearanceIndex | Self::WasteIndex => 1000.0 * activity / coefficient,
            Self::IngestionDose | Self::InhalationDose => activity * coefficient,
            Self::A2 | Self::ActivityLimit => activity / coefficient,
        }
    }
}

#[derive(Debug)]
struct Coefficients(BTreeMap<String, f64>);

impl<'de> Deserialize<'de> for Coefficients {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        struct CoefficientVisitor;

        impl<'de> Visitor<'de> for CoefficientVisitor {
            type Value = Coefficients;

            fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
                formatter.write_str("a map of canonical nuclide names to positive coefficients")
            }

            fn visit_map<M>(self, mut map: M) -> Result<Self::Value, M::Error>
            where
                M: MapAccess<'de>,
            {
                let mut coefficients = BTreeMap::new();
                let mut canonical_names = HashSet::new();
                while let Some((raw, coefficient)) = map.next_entry::<String, f64>()? {
                    let canonical =
                        match composition::material_key(&raw).map_err(M::Error::custom)? {
                            MaterialKey::Nuclide { canonical, .. } => canonical,
                            MaterialKey::Element(_) => {
                                return Err(M::Error::custom(format!(
                                "radiological coefficient key '{raw}' is an element, not a nuclide"
                            )));
                            }
                        };
                    if raw != canonical {
                        return Err(M::Error::custom(format!(
                            "radiological coefficient key '{raw}' is not canonical; use '{canonical}'"
                        )));
                    }
                    if !canonical_names.insert(canonical.clone())
                        || coefficients
                            .insert(canonical.clone(), coefficient)
                            .is_some()
                    {
                        return Err(M::Error::custom(format!(
                            "duplicate radiological coefficient for '{canonical}'"
                        )));
                    }
                    if !coefficient.is_finite() || coefficient <= 0.0 {
                        return Err(M::Error::custom(format!(
                            "radiological coefficient for '{canonical}' must be finite and strictly positive"
                        )));
                    }
                }
                if coefficients.is_empty() {
                    return Err(M::Error::custom(
                        "radiological response coefficients must not be empty",
                    ));
                }
                Ok(Coefficients(coefficients))
            }
        }

        deserializer.deserialize_map(CoefficientVisitor)
    }
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ResponseWire {
    id: String,
    kind: RadiologicalKind,
    basis: String,
    coefficients: Coefficients,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct TableWire {
    format: String,
    title: String,
    source: RadiologicalSource,
    responses: Vec<ResponseWire>,
}

#[derive(Debug)]
struct PreparedResponse {
    id: String,
    kind: RadiologicalKind,
    basis: String,
    coefficients: BTreeMap<String, f64>,
}

/// A parsed table reduced to the selected response IDs in source-file order.
#[derive(Debug)]
pub struct PreparedRadiologicalTable {
    title: String,
    source: RadiologicalSource,
    responses: Vec<PreparedResponse>,
}

#[derive(Debug, Serialize)]
pub struct RadiologicalResponseOut {
    pub id: String,
    pub value: f64,
    pub unit: &'static str,
    pub covered_activity_Bq_per_g: f64,
    pub missing_activity_Bq_per_g: f64,
    pub activity_coverage_fraction: f64,
    pub contributing_nuclide_count: usize,
    pub missing_active_nuclides: Vec<String>,
}

#[derive(Debug, Serialize)]
pub struct RadiologicalStepOut {
    pub responses: Vec<RadiologicalResponseOut>,
}

impl PreparedRadiologicalTable {
    pub fn from_json(text: &str, selectors: &[String]) -> Result<Self, String> {
        let table: TableWire =
            serde_json::from_str(text).map_err(|error| format!("radiological table: {error}"))?;
        if table.format != TABLE_FORMAT {
            return Err(format!(
                "unsupported radiological table format '{}'",
                table.format
            ));
        }
        if table.title.trim().is_empty() {
            return Err("radiological table title must be nonempty".into());
        }
        for (name, value) in [
            ("citation", Some(&table.source.citation)),
            ("edition", Some(&table.source.edition)),
            ("url", Some(&table.source.url)),
            ("jurisdiction", Some(&table.source.jurisdiction)),
            ("scope", table.source.scope.as_ref()),
            ("retrieved", table.source.retrieved.as_ref()),
            ("source_file", table.source.source_file.as_ref()),
            ("source_sha256", table.source.source_sha256.as_ref()),
            ("converter", table.source.converter.as_ref()),
            ("licence", table.source.licence.as_ref()),
            ("row_provenance", table.source.row_provenance.as_ref()),
        ] {
            if value.is_some_and(|value| value.trim().is_empty()) {
                return Err(format!("radiological table source.{name} must be nonempty"));
            }
        }
        if table.responses.is_empty() {
            return Err("radiological table responses must not be empty".into());
        }

        let mut response_ids = HashSet::new();
        for response in &table.responses {
            if response.id.trim().is_empty() {
                return Err("radiological response id must be nonempty".into());
            }
            if !response_ids.insert(response.id.clone()) {
                return Err(format!(
                    "duplicate radiological response id '{}'",
                    response.id
                ));
            }
            if response.basis.trim().is_empty() {
                return Err(format!(
                    "radiological response '{}' basis must be nonempty",
                    response.id
                ));
            }
        }

        let mut selected_ids = HashSet::new();
        for selector in selectors {
            if selector.trim().is_empty() {
                return Err("radiological response selector must be nonempty".into());
            }
            if !selected_ids.insert(selector.as_str()) {
                return Err(format!(
                    "duplicate radiological response selector '{selector}'"
                ));
            }
            if !response_ids.contains(selector) {
                return Err(format!(
                    "unknown radiological response selector '{selector}'"
                ));
            }
        }

        let responses = table
            .responses
            .into_iter()
            .filter(|response| selectors.is_empty() || selected_ids.contains(response.id.as_str()))
            .map(|response| PreparedResponse {
                id: response.id,
                kind: response.kind,
                basis: response.basis,
                coefficients: response.coefficients.0,
            })
            .collect();
        Ok(Self {
            title: table.title,
            source: table.source,
            responses,
        })
    }

    pub fn evaluate(
        &self,
        activity_Bq_per_g: &BTreeMap<String, f64>,
        require_complete: bool,
    ) -> Result<RadiologicalStepOut, String> {
        let mut total_activity = 0.0;
        for (nuclide, activity) in activity_Bq_per_g {
            if !activity.is_finite() || *activity < 0.0 {
                return Err(format!(
                    "activity for '{nuclide}' must be finite and nonnegative"
                ));
            }
            total_activity += activity;
            if !total_activity.is_finite() {
                return Err("total activity is not finite".into());
            }
        }

        let mut outputs = Vec::with_capacity(self.responses.len());
        for response in &self.responses {
            let mut value = 0.0;
            let mut covered_activity = 0.0;
            let mut missing_activity = 0.0;
            let mut contributing_nuclide_count = 0usize;
            let mut missing_active_nuclides = Vec::new();
            for (nuclide, activity) in activity_Bq_per_g {
                if *activity <= 0.0 {
                    continue;
                }
                if let Some(coefficient) = response.coefficients.get(nuclide) {
                    value += response.kind.contribution(*activity, *coefficient);
                    covered_activity += activity;
                    contributing_nuclide_count += 1;
                } else {
                    missing_activity += activity;
                    missing_active_nuclides.push(nuclide.clone());
                }
            }
            if !(value.is_finite() && covered_activity.is_finite() && missing_activity.is_finite())
            {
                return Err(format!(
                    "radiological response '{}' overflowed",
                    response.id
                ));
            }
            if require_complete && !missing_active_nuclides.is_empty() {
                return Err(format!(
                    "radiological response '{}' has no coefficient for positive-activity nuclide(s): {}",
                    response.id,
                    missing_active_nuclides.join(", ")
                ));
            }
            outputs.push(RadiologicalResponseOut {
                id: response.id.clone(),
                value,
                unit: response.kind.output_unit(),
                covered_activity_Bq_per_g: covered_activity,
                missing_activity_Bq_per_g: missing_activity,
                activity_coverage_fraction: if total_activity > 0.0 {
                    covered_activity / total_activity
                } else {
                    1.0
                },
                contributing_nuclide_count,
                missing_active_nuclides,
            });
        }
        Ok(RadiologicalStepOut { responses: outputs })
    }

    /// True when a selected response is an A2 transport index.
    pub fn has_a2(&self) -> bool {
        self.has_kind(RadiologicalKind::A2)
    }

    /// True when a selected response is an absolute activity limit other than A2.
    pub fn has_activity_limit(&self) -> bool {
        self.has_kind(RadiologicalKind::ActivityLimit)
    }

    fn has_kind(&self, kind: RadiologicalKind) -> bool {
        self.responses.iter().any(|response| response.kind == kind)
    }

    pub fn certificate_metadata(&self) -> serde_json::Value {
        serde_json::json!({
            "format": TABLE_FORMAT,
            "title": self.title,
            "source": self.source,
            "responses": self.responses.iter().map(|response| serde_json::json!({
                "id": response.id,
                "kind": response.kind.name(),
                "basis": response.basis,
                "coefficient_count": response.coefficients.len(),
                "coefficient_unit": response.kind.coefficient_unit(),
                "output_unit": response.kind.output_unit(),
                "formula": response.kind.formula(),
            })).collect::<Vec<_>>(),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn table(extra: &str) -> String {
        format!(
            r#"{{
  "format": "actinv-radiological-table-1",
  "title": "two-nuclide fixture",
  "source": {{
    "citation": "fixture",
    "edition": "1",
    "url": "https://example.invalid/fixture",
    "jurisdiction": "test"
  }},
  "responses": [
    {{"id":"clear", "kind":"clearance_index", "basis":"limit", "coefficients":{{"Co60":4.0{extra}}}}},
    {{"id":"ingest", "kind":"ingestion_dose", "basis":"dose", "coefficients":{{"Co60":0.25}}}}
  ]
}}"#
        )
    }

    #[test]
    fn formulas_and_missing_activity_are_explicit() {
        let prepared = PreparedRadiologicalTable::from_json(&table(""), &[]).unwrap();
        let activity = BTreeMap::from([("Co60".to_owned(), 2.0), ("Cs137".to_owned(), 3.0)]);
        let result = prepared.evaluate(&activity, false).unwrap();
        assert_eq!(result.responses.len(), 2);
        assert_eq!(result.responses[0].value, 500.0);
        assert_eq!(result.responses[0].unit, "dimensionless");
        assert_eq!(result.responses[0].covered_activity_Bq_per_g, 2.0);
        assert_eq!(result.responses[0].missing_activity_Bq_per_g, 3.0);
        assert_eq!(result.responses[0].activity_coverage_fraction, 0.4);
        assert_eq!(result.responses[0].contributing_nuclide_count, 1);
        assert_eq!(
            result.responses[0].missing_active_nuclides,
            vec!["Cs137".to_owned()]
        );
        assert_eq!(result.responses[1].value, 0.5);
        assert_eq!(result.responses[1].unit, "Sv/g_material_intake");
        assert!(prepared.evaluate(&activity, true).is_err());
    }

    #[test]
    fn a2_kind_sums_activity_over_the_supplied_a2_values() {
        // The A2 values are the table author's (Bq); nothing is built in.
        let text = table("").replace(
            r#"{"id":"ingest", "kind":"ingestion_dose", "basis":"dose", "coefficients":{"Co60":0.25}}"#,
            r#"{"id":"a2", "kind":"a2", "basis":"A2 in Bq", "coefficients":{"Co60":4.0,"Cs137":6.0}}"#,
        );
        let prepared = PreparedRadiologicalTable::from_json(&text, &[]).unwrap();
        assert!(prepared.has_a2());
        let activity = BTreeMap::from([
            ("Co60".to_owned(), 2.0),
            ("Cs137".to_owned(), 3.0),
            ("H3".to_owned(), 5.0),
        ]);
        let result = prepared.evaluate(&activity, false).unwrap();
        let a2 = &result.responses[1];
        assert_eq!(a2.id, "a2");
        assert_eq!(a2.value, 2.0 / 4.0 + 3.0 / 6.0);
        assert_eq!(a2.unit, "A2_per_g_material");
        assert_eq!(a2.contributing_nuclide_count, 2);
        // A nuclide with no A2 value is reported, not counted as zero.
        assert_eq!(a2.missing_active_nuclides, vec!["H3".to_owned()]);
        assert_eq!(a2.missing_activity_Bq_per_g, 5.0);
        assert!(prepared.evaluate(&activity, true).is_err());
        let metadata = prepared.certificate_metadata();
        assert_eq!(metadata["responses"][1]["kind"], "a2");
        assert_eq!(metadata["responses"][1]["coefficient_unit"], "Bq");
        assert_eq!(
            metadata["responses"][1]["formula"],
            "sum(activity_Bq_per_g / A2_Bq)"
        );
        // A table without an A2 response does not claim one.
        let plain = PreparedRadiologicalTable::from_json(&table(""), &[]).unwrap();
        assert!(!plain.has_a2());
    }

    #[test]
    fn zero_activity_has_complete_coverage() {
        let prepared = PreparedRadiologicalTable::from_json(&table(""), &[]).unwrap();
        let result = prepared.evaluate(&BTreeMap::new(), true).unwrap();
        assert!(result
            .responses
            .iter()
            .all(|response| response.activity_coverage_fraction == 1.0));
    }

    #[test]
    fn selectors_preserve_file_order() {
        let prepared =
            PreparedRadiologicalTable::from_json(&table(""), &["ingest".into(), "clear".into()])
                .unwrap();
        let metadata = prepared.certificate_metadata();
        assert_eq!(metadata["responses"][0]["id"], "clear");
        assert_eq!(metadata["responses"][1]["id"], "ingest");
    }

    #[test]
    fn strict_table_rejects_duplicate_and_noncanonical_coefficients() {
        let duplicate = table(",\"Co60\":5.0");
        assert!(PreparedRadiologicalTable::from_json(&duplicate, &[])
            .unwrap_err()
            .contains("duplicate radiological coefficient"));
        let noncanonical = table(",\"co60\":5.0");
        assert!(PreparedRadiologicalTable::from_json(&noncanonical, &[])
            .unwrap_err()
            .contains("not canonical"));
    }

    #[test]
    fn strict_table_rejects_unknown_fields_and_bad_values() {
        let unknown = table("").replacen(
            "\"title\": \"two-nuclide fixture\"",
            "\"title\": \"two-nuclide fixture\", \"surprise\": true",
            1,
        );
        assert!(PreparedRadiologicalTable::from_json(&unknown, &[]).is_err());
        let zero = table("").replace("\"Co60\":4.0", "\"Co60\":0.0");
        assert!(PreparedRadiologicalTable::from_json(&zero, &[])
            .unwrap_err()
            .contains("strictly positive"));
    }

    #[test]
    fn activity_limit_kind_divides_by_a_limit_in_bq() {
        let text = table("").replace(
            r#"{"id":"ingest", "kind":"ingestion_dose", "basis":"dose", "coefficients":{"Co60":0.25}}"#,
            r#"{"id":"a1", "kind":"activity_limit", "basis":"A1 in Bq", "coefficients":{"Co60":8.0}}"#,
        );
        let prepared = PreparedRadiologicalTable::from_json(&text, &[]).unwrap();
        assert!(prepared.has_activity_limit());
        assert!(!prepared.has_a2());
        let activity = BTreeMap::from([("Co60".to_owned(), 2.0)]);
        let a1 = &prepared.evaluate(&activity, false).unwrap().responses[1];
        assert_eq!(a1.value, 0.25);
        assert_eq!(a1.unit, "limits_per_g_material");
        let bundled = bundled_table("us-49cfr173-a1").unwrap();
        let metadata = PreparedRadiologicalTable::from_json(bundled, &[])
            .unwrap()
            .certificate_metadata();
        assert!(metadata["responses"]
            .as_array()
            .unwrap()
            .iter()
            .all(|response| response["kind"] == "activity_limit"));
    }

    #[test]
    fn every_bundled_table_parses_and_carries_provenance() {
        let names = bundled_table_names();
        assert_eq!(names.len(), 7);
        let mut sorted = names.clone();
        sorted.sort_unstable();
        sorted.dedup();
        assert_eq!(names, sorted, "bundled names are unique and alphabetical");
        for name in names {
            let text = bundled_table(name).unwrap();
            let prepared = PreparedRadiologicalTable::from_json(text, &[]).unwrap();
            let metadata = prepared.certificate_metadata();
            let source = &metadata["source"];
            for field in [
                "scope",
                "retrieved",
                "source_file",
                "source_sha256",
                "converter",
                "licence",
                "row_provenance",
            ] {
                assert!(
                    source[field]
                        .as_str()
                        .is_some_and(|value| !value.is_empty()),
                    "{name}: source.{field}"
                );
            }
            assert!(metadata["responses"]
                .as_array()
                .unwrap()
                .iter()
                .all(|response| response["coefficient_count"].as_u64().unwrap() > 100));
        }
    }

    #[test]
    fn bundled_table_resolves_by_name_and_evaluates() {
        let (text, sha256) = read_bundled("us-49cfr173-a2", None).unwrap();
        assert_eq!(sha256, bundled_sha256("us-49cfr173-a2").unwrap());
        assert_eq!(
            bundled_name("bundled:us-49cfr173-a2"),
            Some("us-49cfr173-a2")
        );
        assert_eq!(bundled_name("tables/a2.json"), None);
        let prepared =
            PreparedRadiologicalTable::from_json(&text, &["a2-u-medium".to_owned()]).unwrap();
        // Co-60 A2 is 0.4 TBq = 4e11 Bq in 49 CFR 173.435.
        let activity = BTreeMap::from([("Co60".to_owned(), 2.0)]);
        let result = prepared.evaluate(&activity, true).unwrap();
        assert_eq!(result.responses.len(), 1);
        assert!(prepared.has_a2());
        assert_eq!(result.responses[0].value, 2.0 / 4.0e11);
        assert_eq!(result.responses[0].unit, "A2_per_g_material");
        // Uranium rows are explicit per lung-absorption response; U-236 fast is unlimited.
        let uranium = BTreeMap::from([("U236".to_owned(), 1.0)]);
        let fast = PreparedRadiologicalTable::from_json(&text, &["a2-u-fast".to_owned()]).unwrap();
        assert_eq!(
            fast.evaluate(&uranium, false).unwrap().responses[0].missing_active_nuclides,
            vec!["U236".to_owned()]
        );
        assert!(prepared.evaluate(&uranium, true).is_ok());
    }

    #[test]
    fn bundled_table_unit_conversions_match_the_source_units() {
        let (concentration, _) = read_bundled("euratom-2013-59-annex-vii-a", None).unwrap();
        let prepared = PreparedRadiologicalTable::from_json(&concentration, &[]).unwrap();
        // Table A Part 1 lists H-3 at 100 kBq/kg = 1e5 Bq/kg, so 100 Bq/g is an index of 1.
        let result = prepared
            .evaluate(&BTreeMap::from([("H3".to_owned(), 100.0)]), true)
            .unwrap();
        assert!((result.responses[0].value - 1.0).abs() < 1e-12);
    }

    #[test]
    fn unknown_bundled_name_is_an_error_listing_the_choices() {
        let error = bundled_table("iaea-ssr6-rev1-a2").unwrap_err();
        assert!(error.contains("unknown bundled radiological table 'iaea-ssr6-rev1-a2'"));
        assert!(error.contains("us-49cfr173-a2"));
        assert!(read_bundled("", None).is_err());
    }

    #[test]
    fn bundled_table_declared_hash_is_enforced() {
        let wrong = "0".repeat(64);
        assert!(read_bundled("us-49cfr173-a1", Some(&wrong))
            .unwrap_err()
            .contains("SHA-256 mismatch for bundled:us-49cfr173-a1"));
        let right = bundled_sha256("us-49cfr173-a1").unwrap().to_uppercase();
        assert!(read_bundled("us-49cfr173-a1", Some(&right)).is_ok());
    }

    #[test]
    fn empty_optional_provenance_is_rejected() {
        let text = table("").replacen(
            "\"jurisdiction\": \"test\"",
            "\"jurisdiction\": \"test\", \"licence\": \" \"",
            1,
        );
        assert!(PreparedRadiologicalTable::from_json(&text, &[])
            .unwrap_err()
            .contains("source.licence"));
    }
}
