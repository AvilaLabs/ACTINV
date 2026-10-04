#![allow(non_snake_case)] // JSON wire names carry their physical units.
//! Strict, hash-pinned radiological response tables and per-step evaluation.

use actinv_data::composition::{self, MaterialKey};
use serde::de::{Error as _, MapAccess, Visitor};
use serde::{Deserialize, Deserializer, Serialize};
use std::collections::{BTreeMap, HashSet};
use std::fmt;

const TABLE_FORMAT: &str = "actinv-radiological-table-1";

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RadiologicalSource {
    citation: String,
    edition: String,
    url: String,
    jurisdiction: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RadiologicalKind {
    ClearanceIndex,
    WasteIndex,
    IngestionDose,
    InhalationDose,
}

impl RadiologicalKind {
    fn name(self) -> &'static str {
        match self {
            Self::ClearanceIndex => "clearance_index",
            Self::WasteIndex => "waste_index",
            Self::IngestionDose => "ingestion_dose",
            Self::InhalationDose => "inhalation_dose",
        }
    }

    fn output_unit(self) -> &'static str {
        match self {
            Self::ClearanceIndex | Self::WasteIndex => "dimensionless",
            Self::IngestionDose | Self::InhalationDose => "Sv/g_material_intake",
        }
    }

    fn coefficient_unit(self) -> &'static str {
        match self {
            Self::ClearanceIndex | Self::WasteIndex => "Bq/kg",
            Self::IngestionDose | Self::InhalationDose => "Sv/Bq",
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
        }
    }

    fn contribution(self, activity: f64, coefficient: f64) -> f64 {
        match self {
            Self::ClearanceIndex | Self::WasteIndex => 1000.0 * activity / coefficient,
            Self::IngestionDose | Self::InhalationDose => activity * coefficient,
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
            ("citation", &table.source.citation),
            ("edition", &table.source.edition),
            ("url", &table.source.url),
            ("jurisdiction", &table.source.jurisdiction),
        ] {
            if value.trim().is_empty() {
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
}
