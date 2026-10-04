//! Opt-in, nominal Part 61 classification for the original facility mesh.
//!
//! This module adapts the existing twin membership map to the shared ordinary
//! `actinv waste` evaluator. It does not implement classification arithmetic.

use crate::waste::{self, ComponentCell, ComponentSpec, ExternalTritium};
use crate::waste_intrusion;
use actinv_core::waste::{NuclideProperties, RulePack, WasteType};
use serde::Deserialize;
use serde_json::{Map, Value};
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::path::Path;

const WASTE_SCHEMA: &str = "actinv-twin-waste-spec-1";
const WASTE_RULES: &str = "us-nrc-10cfr61.55-v1";
const MAX_TARGETS: usize = 64;
const MAX_COMPONENTS: usize = 128;
const MAX_CELLS: usize = 128;
const MAX_PROPERTIES: usize = 1024;
const MAX_ASSAYS: usize = 128;
pub(crate) const MAX_FILE_BYTES: u64 = 8 * 1024 * 1024;
pub(crate) const MAX_MESH_BYTES: u64 = 64 * 1024 * 1024;
pub(crate) const MAX_OUTER_BYTES: u64 = 8 * 1024 * 1024;
const MAX_RESULT_BYTES: usize = 32 * 1024 * 1024;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct WasteExtension {
    schema: String,
    rules: String,
    targets: Vec<u64>,
    nuclide_properties: BTreeMap<String, NuclideProperties>,
    components: BTreeMap<String, WasteComponent>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct WasteComponent {
    mass_g: f64,
    #[serde(default)]
    displaced_volume_cm3: Option<f64>,
    #[serde(default)]
    density_g_cm3: Option<f64>,
    waste_type: WasteType,
    cell_masses_g: BTreeMap<String, f64>,
    external_tritium: ExternalTritium,
}

/// Return the optional extension after applying the enabled-feature raw-key
/// policy. A present null is an error and never aliases omission.
pub(crate) fn parse_extension(bytes: &[u8]) -> Result<Option<Value>, String> {
    let ordinary: Value = serde_json::from_slice(bytes)
        .map_err(|error| format!("twin spec is not valid JSON: {error}"))?;
    let object = ordinary
        .as_object()
        .ok_or("twin spec must be a JSON object")?;
    let Some(raw_extension) = object.get("waste") else {
        return Ok(None);
    };
    if raw_extension.is_null() {
        return Err("waste must be omitted or an object; null is not allowed".into());
    }
    let unique = waste_intrusion::parse_unique_json(bytes, "twin spec")?;
    let unique_object = unique
        .as_object()
        .ok_or("twin spec must be a JSON object")?;
    Ok(unique_object.get("waste").cloned())
}

pub(crate) fn read_regular_bounded(
    path: &Path,
    max_bytes: u64,
    label: &str,
) -> Result<Vec<u8>, String> {
    waste_intrusion::read_regular_bounded(path, max_bytes, label)
}

pub(crate) fn validate_assay_counts(cell_assays: usize, dose_assays: usize) -> Result<(), String> {
    if cell_assays > MAX_ASSAYS {
        return Err(format!("cell assays exceed {MAX_ASSAYS}"));
    }
    if dose_assays > MAX_ASSAYS {
        return Err(format!("dose assays exceed {MAX_ASSAYS}"));
    }
    Ok(())
}

pub(crate) fn read_assay(path: &Path) -> Result<(String, String), String> {
    let bytes = read_regular_bounded(path, MAX_FILE_BYTES, "assay document")?;
    let sha = sha256(&bytes);
    let text = String::from_utf8(bytes)
        .map_err(|error| format!("assay document {} is not UTF-8: {error}", path.display()))?;
    Ok((text, sha))
}

fn sha256(bytes: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    Sha256::digest(bytes)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

fn validate_component_membership(
    groups: &HashMap<String, Vec<String>>,
    extension: &WasteExtension,
    input: &waste::InputResults,
) -> Result<(), String> {
    if groups.is_empty() {
        return Err("waste classification requires nonempty twin components".into());
    }
    if groups.len() > MAX_COMPONENTS {
        return Err(format!("twin components exceed {MAX_COMPONENTS}"));
    }
    if extension.components.len() != groups.len()
        || extension
            .components
            .keys()
            .any(|id| !groups.contains_key(id))
    {
        return Err("waste component names must exactly match twin component names".into());
    }
    let mut assigned = BTreeSet::new();
    for (component_id, cells) in groups {
        if component_id.trim().is_empty() || cells.is_empty() {
            return Err("twin component names and cell groups must be nonempty".into());
        }
        let component = extension
            .components
            .get(component_id)
            .ok_or_else(|| format!("missing waste component {component_id}"))?;
        let ids: BTreeSet<_> = cells.iter().map(String::as_str).collect();
        if ids.len() != cells.len() {
            return Err(format!("twin component {component_id} repeats a cell id"));
        }
        if cells.iter().any(|id| id.trim().is_empty()) {
            return Err(format!(
                "twin component {component_id} has an empty cell id"
            ));
        }
        if !ids.iter().all(|id| assigned.insert(*id)) {
            return Err("twin component cell groups overlap".into());
        }
        let mass_ids: BTreeSet<_> = component.cell_masses_g.keys().map(String::as_str).collect();
        if mass_ids != ids {
            return Err(format!(
                "waste component {component_id} cell_masses_g keys must exactly match its twin group"
            ));
        }
        for id in cells {
            if !input.cells.contains_key(id) {
                return Err(format!(
                    "component {component_id} references missing mesh cell {id}"
                ));
            }
        }
        let mass_sum: f64 = component.cell_masses_g.values().sum();
        if !mass_sum.is_finite() || !component.mass_g.is_finite() || component.mass_g <= 0.0 {
            return Err(format!("component {component_id} has invalid mass"));
        }
        if component
            .cell_masses_g
            .values()
            .any(|mass| !mass.is_finite() || *mass <= 0.0)
        {
            return Err(format!(
                "component {component_id} cell masses must be finite and positive"
            ));
        }
        let relative = (mass_sum - component.mass_g).abs() / component.mass_g;
        if !relative.is_finite() || relative > 1e-9 {
            return Err(format!(
                "component {component_id} cell masses do not close to mass_g"
            ));
        }
    }
    Ok(())
}

fn validate_external_step_keys(component: &WasteComponent, targets: &[u64]) -> Result<(), String> {
    if let ExternalTritium::Declared { activity_bq, .. } = &component.external_tritium {
        for key in activity_bq.keys() {
            let step = key.parse::<u64>().map_err(|_| {
                format!("external H-3 step key '{key}' must be a canonical positive integer")
            })?;
            if step == 0 || step.to_string() != *key {
                return Err(format!(
                    "external H-3 step key '{key}' must be a canonical positive integer"
                ));
            }
        }
        for step in targets {
            if !activity_bq.contains_key(&step.to_string()) {
                return Err(format!("external H-3 lacks selected step {step}"));
            }
        }
        if activity_bq
            .values()
            .any(|value| !value.is_finite() || *value < 0.0)
        {
            return Err("external H-3 activities must be finite and nonnegative".into());
        }
    }
    Ok(())
}

fn raw_component_to_ordinary(
    component_id: &str,
    typed: &WasteComponent,
    raw: &Value,
) -> Result<Value, String> {
    let object = raw
        .as_object()
        .ok_or_else(|| format!("waste component {component_id} must be an object"))?;
    let mut converted = Map::new();
    converted.insert("id".into(), Value::String(component_id.to_owned()));
    for key in ["mass_g", "waste_type", "external_tritium"] {
        converted.insert(
            key.into(),
            object
                .get(key)
                .cloned()
                .ok_or_else(|| format!("waste component {component_id} lacks {key}"))?,
        );
    }
    for key in ["displaced_volume_cm3", "density_g_cm3"] {
        if let Some(value) = object.get(key) {
            converted.insert(key.into(), value.clone());
        }
    }
    let cells = typed
        .cell_masses_g
        .iter()
        .map(|(id, mass)| {
            let raw_mass = object
                .get("cell_masses_g")
                .and_then(Value::as_object)
                .and_then(|map| map.get(id))
                .cloned()
                .unwrap_or_else(|| Value::from(*mass));
            let mut cell = Map::new();
            cell.insert("id".into(), Value::String(id.clone()));
            cell.insert("mass_g".into(), raw_mass);
            Value::Object(cell)
        })
        .collect::<Vec<_>>();
    converted.insert("cells".into(), Value::Array(cells));
    Ok(Value::Object(converted))
}

fn build_ordinary_spec(
    extension_value: &Value,
    extension: &WasteExtension,
    input_name: &str,
) -> Result<String, String> {
    let raw_extension = extension_value
        .as_object()
        .ok_or("waste extension must be an object")?;
    let raw_components = raw_extension
        .get("components")
        .and_then(Value::as_object)
        .ok_or("waste components must be an object")?;
    let mut components = Vec::with_capacity(extension.components.len());
    for (id, component) in &extension.components {
        let raw = raw_components
            .get(id)
            .ok_or_else(|| format!("missing raw waste component {id}"))?;
        components.push(raw_component_to_ordinary(id, component, raw)?);
    }
    let mut ordinary = Map::new();
    ordinary.insert("schema".into(), Value::String("actinv-waste-spec-1".into()));
    ordinary.insert("rules".into(), raw_extension["rules"].clone());
    ordinary.insert("input".into(), Value::String(input_name.to_owned()));
    ordinary.insert("targets".into(), raw_extension["targets"].clone());
    ordinary.insert(
        "nuclide_properties".into(),
        raw_extension["nuclide_properties"].clone(),
    );
    ordinary.insert("components".into(), Value::Array(components));
    serde_json::to_string(&Value::Object(ordinary)).map_err(|error| error.to_string())
}

fn sort_row_fractions(rows: &mut Value) -> Result<(), String> {
    let rows = rows
        .as_array_mut()
        .ok_or("waste evaluation row_fractions is not an array")?;
    rows.sort_by(|left, right| {
        let left_table = left["table"].as_u64().unwrap_or(0);
        let right_table = right["table"].as_u64().unwrap_or(0);
        let left_column = left["column"].as_u64().unwrap_or(0);
        let right_column = right["column"].as_u64().unwrap_or(0);
        let left_row = left["row_id"].as_str().unwrap_or("");
        let right_row = right["row_id"].as_str().unwrap_or("");
        let left_nuclide = left["nuclide"].as_str().unwrap_or("");
        let right_nuclide = right["nuclide"].as_str().unwrap_or("");
        (left_table, left_column, left_row, left_nuclide).cmp(&(
            right_table,
            right_column,
            right_row,
            right_nuclide,
        ))
    });
    Ok(())
}

fn sort_evaluation_rows(document: &mut Value) -> Result<(), String> {
    let components = document
        .get_mut("components")
        .and_then(Value::as_array_mut)
        .ok_or("nominal waste result has no components array")?;
    for component in components {
        let targets = component
            .get_mut("targets")
            .and_then(Value::as_array_mut)
            .ok_or("nominal waste component has no targets array")?;
        for target in targets {
            let rows = target
                .get_mut("evaluation")
                .and_then(|evaluation| evaluation.get_mut("row_fractions"))
                .ok_or("nominal waste target has no row_fractions")?;
            sort_row_fractions(rows)?;
        }
    }
    Ok(())
}

pub(crate) fn evaluate(
    extension_value: &Value,
    groups: Option<&HashMap<String, Vec<String>>>,
    mesh_bytes: &[u8],
    input_name: &str,
    base: &Path,
) -> Result<(Value, Value), String> {
    let extension: WasteExtension = serde_json::from_value(extension_value.clone())
        .map_err(|error| format!("invalid twin waste block: {error}"))?;
    if extension.schema != WASTE_SCHEMA {
        return Err(format!("waste schema must be '{WASTE_SCHEMA}'"));
    }
    if extension.rules != WASTE_RULES {
        return Err(format!(
            "waste rules must explicitly select '{WASTE_RULES}'"
        ));
    }
    waste::validate_targets(&extension.targets)?;
    if extension.targets.len() > MAX_TARGETS {
        return Err(format!("waste supports at most {MAX_TARGETS} targets"));
    }
    if extension.components.is_empty() || extension.components.len() > MAX_COMPONENTS {
        return Err(format!(
            "waste components must contain 1..={MAX_COMPONENTS} entries"
        ));
    }
    if extension.nuclide_properties.len() > MAX_PROPERTIES {
        return Err(format!(
            "nuclide_properties exceeds {MAX_PROPERTIES} entries"
        ));
    }
    waste_intrusion::validate_unique_result_json(mesh_bytes)?;
    waste_intrusion::validate_result_activity_aliases(mesh_bytes)?;
    let input = waste::load_input_bytes(mesh_bytes)?;
    if input.kind != "mesh" {
        return Err("twin waste classification requires an actinv mesh result".into());
    }
    if input.cells.is_empty() || input.cells.len() > MAX_CELLS {
        return Err(format!("mesh must contain 1..={MAX_CELLS} cells"));
    }
    let groups = groups.ok_or("waste classification requires twin components")?;
    validate_component_membership(groups, &extension, &input)?;

    let rules = RulePack::bundled()?;
    for component in extension.components.values() {
        waste::waste_geometry(&ComponentSpec {
            id: "twin component".into(),
            mass_g: component.mass_g,
            displaced_volume_cm3: component.displaced_volume_cm3,
            density_g_cm3: component.density_g_cm3,
            waste_type: component.waste_type,
            cells: Vec::new(),
            external_tritium: match &component.external_tritium {
                ExternalTritium::NotApplicable => ExternalTritium::NotApplicable,
                ExternalTritium::Required => ExternalTritium::Required,
                ExternalTritium::Declared {
                    source,
                    excludes_activation,
                    activity_bq,
                } => ExternalTritium::Declared {
                    source: source.clone(),
                    excludes_activation: *excludes_activation,
                    activity_bq: activity_bq.clone(),
                },
            },
        })?;
        validate_external_step_keys(component, &extension.targets)?;
    }
    for waste_type in [WasteType::General, WasteType::ActivatedMetal] {
        waste_intrusion::validate_property_keys(&extension.nuclide_properties, &rules, waste_type)?;
    }
    for component in extension.components.values() {
        let ordinary_component = ComponentSpec {
            id: "preflight".into(),
            mass_g: component.mass_g,
            displaced_volume_cm3: component.displaced_volume_cm3,
            density_g_cm3: component.density_g_cm3,
            waste_type: component.waste_type,
            cells: component
                .cell_masses_g
                .iter()
                .map(|(id, mass)| ComponentCell {
                    id: id.clone(),
                    mass_g: *mass,
                })
                .collect(),
            external_tritium: match &component.external_tritium {
                ExternalTritium::NotApplicable => ExternalTritium::NotApplicable,
                ExternalTritium::Required => ExternalTritium::Required,
                ExternalTritium::Declared {
                    source,
                    excludes_activation,
                    activity_bq,
                } => ExternalTritium::Declared {
                    source: source.clone(),
                    excludes_activation: *excludes_activation,
                    activity_bq: activity_bq.clone(),
                },
            },
        };
        waste_intrusion::preflight_aggregated_activities(
            &ordinary_component,
            &extension.targets,
            &input,
        )?;
    }

    let ordinary_text = build_ordinary_spec(extension_value, &extension, input_name)?;
    let mut evaluated = waste::evaluate_doc_with_input_details(&ordinary_text, base, mesh_bytes)?;
    sort_evaluation_rows(&mut evaluated.document)?;
    let assigned: BTreeSet<String> = groups.values().flatten().cloned().collect();
    let unassigned: Vec<String> = input
        .cells
        .keys()
        .filter(|id| !assigned.contains(*id))
        .cloned()
        .collect();
    let mut target_class_counts = Vec::with_capacity(extension.targets.len());
    for step in &extension.targets {
        let mut counts = BTreeMap::from([
            ("A", 0usize),
            ("B", 0usize),
            ("C", 0usize),
            ("above_class_c", 0usize),
            ("unknown", 0usize),
        ]);
        for component in evaluated.document["components"]
            .as_array()
            .ok_or("waste result lacks components")?
        {
            let target = component["targets"]
                .as_array()
                .and_then(|targets| {
                    targets
                        .iter()
                        .find(|target| target["step"].as_u64() == Some(*step))
                })
                .ok_or_else(|| format!("waste result lacks target {step}"))?;
            let class = target["class"].as_str().ok_or("waste target lacks class")?;
            let count = counts
                .get_mut(class)
                .ok_or_else(|| format!("unsupported waste class '{class}'"))?;
            *count += 1;
        }
        target_class_counts.push(serde_json::json!({"step": step, "counts": counts}));
    }
    let summary = serde_json::json!({
        "schema": "actinv-twin-waste-summary-1",
        "inventory_basis": "original_mesh_activity_Bq_per_g",
        "assay_adjustment": "not_applied",
        "assigned_cell_ids": assigned,
        "unassigned_cell_ids": unassigned,
        "assigned_cell_count": assigned.len(),
        "unassigned_cell_count": unassigned.len(),
        "membership_coverage": if unassigned.is_empty() { "complete" } else { "partial" },
        "component_count": extension.components.len(),
        "target_class_counts": target_class_counts,
    });
    Ok((evaluated.document, summary))
}

pub(crate) fn validate_output_size(value: &Value, emitted_newline: bool) -> Result<String, String> {
    serialize_bounded(value, MAX_RESULT_BYTES, emitted_newline)
}

fn serialize_bounded(
    value: &Value,
    max_bytes: usize,
    emitted_newline: bool,
) -> Result<String, String> {
    let text = serde_json::to_string_pretty(value).map_err(|error| error.to_string())?;
    if text.len().saturating_add(usize::from(emitted_newline)) > max_bytes {
        return Err(format!("serialized twin output exceeds {max_bytes} bytes"));
    }
    Ok(text)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn explicit_null_and_recursive_duplicate_waste_specs_are_rejected() {
        assert!(parse_extension(br#"{"spec":"actinv-twin-1","waste":null}"#).is_err());
        assert!(parse_extension(br#"{"spec":"actinv-twin-1","waste":{"x":1,"x":2}}"#).is_err());
        assert!(parse_extension(br#"{"spec":"actinv-twin-1"}"#)
            .unwrap()
            .is_none());
    }

    #[test]
    fn canonical_external_step_keys_are_required() {
        let component: WasteComponent = serde_json::from_value(serde_json::json!({
            "mass_g":1.0,"displaced_volume_cm3":1.0,"waste_type":"general",
            "cell_masses_g":{"cell":1.0},"external_tritium":{"status":"declared",
            "source":"synthetic","excludes_activation":true,"activity_bq":{"01":2.0}}
        }))
        .unwrap();
        assert!(validate_external_step_keys(&component, &[1]).is_err());
    }

    #[test]
    fn output_limit_counts_the_emitted_newline() {
        let short = serde_json::json!({"ok":true});
        let text = serde_json::to_string_pretty(&short).unwrap();
        assert!(serialize_bounded(&short, text.len() + 1, true).is_ok());
        assert!(serialize_bounded(&short, text.len(), true).is_err());
        let payload = "x".repeat(MAX_RESULT_BYTES - 2);
        let mut value = Value::String(payload);
        assert!(validate_output_size(&value, false).is_ok());
        assert!(validate_output_size(&value, true).is_err());
        if let Value::String(payload) = &mut value {
            payload.push('x');
        }
        assert!(validate_output_size(&value, false).is_err());
    }

    fn synthetic_mesh(time_b: f64, h3_per_g: f64) -> Vec<u8> {
        let mut records = vec![serde_json::json!({
            "record":"header","schema":"actinv-mesh-result-1","cell_count":2
        })];
        for (id, time, c14) in [("cell-a", 10.0, 5.0), ("cell-b", time_b, 7.0)] {
            records.push(serde_json::json!({
                "record":"cell","id":id,"ordinal":records.len()-1,
                "result":{"entry_point":"test","mode":"coupled","steps":[{
                    "step":1,"t_s":time,"activity_Bq_per_g":{"C14":c14,"H3":h3_per_g}
                }]}
            }));
        }
        records.push(serde_json::json!({"record":"footer","cell_count":2}));
        records
            .iter()
            .map(|record| format!("{}\n", serde_json::to_string(record).unwrap()))
            .collect::<String>()
            .into_bytes()
    }

    fn synthetic_row_order_mesh() -> Vec<u8> {
        let mut records = vec![serde_json::json!({
            "record":"header","schema":"actinv-mesh-result-1","cell_count":2
        })];
        for (id, ordinal, c14) in [("cell-a", 0, 5.0), ("cell-b", 1, 7.0)] {
            records.push(serde_json::json!({
                "record":"cell","id":id,"ordinal":ordinal,
                "result":{"entry_point":"test","mode":"coupled","steps":[{
                    "step":1,"t_s":10.0,"activity_Bq_per_g":{
                        "C14":c14,"Co60":1.0,"Tc99":1.0
                    }
                }]}
            }));
        }
        records.push(serde_json::json!({"record":"footer","cell_count":2}));
        records
            .iter()
            .map(|record| format!("{}\n", serde_json::to_string(record).unwrap()))
            .collect::<String>()
            .into_bytes()
    }

    fn synthetic_extension() -> Value {
        serde_json::json!({
            "schema":WASTE_SCHEMA,
            "rules":WASTE_RULES,
            "targets":[1],
            "nuclide_properties":{
                "C14":{"z":6,"half_life_s":1.808e11,"alpha_emitting":false},
                "H3":{"z":1,"half_life_s":3.887e8,"alpha_emitting":false}
            },
            "components":{
                "primary":{"mass_g":2.0,"displaced_volume_cm3":2.0,
                    "waste_type":"general",
                    "cell_masses_g":{"cell-a":1.0,"cell-b":1.0},
                    "external_tritium":{"status":"declared","source":"test source",
                        "excludes_activation":true,"activity_bq":{"1":4.0}}}
            }
        })
    }

    #[test]
    fn shared_nominal_evaluator_aggregates_mass_and_external_h3_once() {
        let groups = HashMap::from([(
            "primary".to_string(),
            vec!["cell-a".to_string(), "cell-b".to_string()],
        )]);
        let bytes = synthetic_mesh(10.0, 2.0);
        let (classification, summary) = evaluate(
            &synthetic_extension(),
            Some(&groups),
            &bytes,
            "mesh.ndjson",
            Path::new("."),
        )
        .unwrap();
        let target = &classification["components"][0]["targets"][0];
        assert_eq!(target["inventory_activity_bq"]["C14"], 12.0);
        assert_eq!(target["inventory_activity_bq"]["H3"], 8.0);
        assert_eq!(target["external_tritium_activity_bq"], 4.0);
        assert_eq!(target["calculated_only_class"], "A");
        assert_eq!(
            summary["assigned_cell_ids"],
            serde_json::json!(["cell-a", "cell-b"])
        );
        assert_eq!(summary["membership_coverage"], "complete");
        assert_eq!(summary["target_class_counts"][0]["counts"]["A"], 1);
        assert_eq!(summary["target_class_counts"][0]["counts"]["unknown"], 0);
    }

    #[test]
    fn adapter_evaluation_orders_rows_without_changing_inventory_or_null_columns() {
        let groups = HashMap::from([(
            "primary".to_string(),
            vec!["cell-a".to_string(), "cell-b".to_string()],
        )]);
        let mut extension = synthetic_extension();
        extension["nuclide_properties"]["Co60"] = serde_json::json!({
            "z":27,"half_life_s":1.663e8,"alpha_emitting":false
        });
        extension["nuclide_properties"]["Tc99"] = serde_json::json!({
            "z":43,"half_life_s":6.66e12,"alpha_emitting":false
        });
        extension["components"]["primary"]["external_tritium"] =
            serde_json::json!({"status":"not_applicable"});

        let (classification, _) = evaluate(
            &extension,
            Some(&groups),
            &synthetic_row_order_mesh(),
            "mesh.ndjson",
            Path::new("."),
        )
        .unwrap();
        let target = &classification["components"][0]["targets"][0];
        assert_eq!(target["inventory_activity_bq"]["C14"], 12.0);
        assert_eq!(target["inventory_activity_bq"]["Co60"], 2.0);
        assert_eq!(target["inventory_activity_bq"]["Tc99"], 2.0);

        let rows = target["evaluation"]["row_fractions"].as_array().unwrap();
        let tuples: Vec<_> = rows
            .iter()
            .map(|row| {
                (
                    row["table"].as_u64().unwrap(),
                    row["column"].as_u64().unwrap_or(0),
                    row["row_id"].as_str().unwrap().to_owned(),
                    row["nuclide"].as_str().unwrap().to_owned(),
                )
            })
            .collect();
        assert_eq!(
            tuples,
            vec![
                (1, 0, "C-14".to_owned(), "C14".to_owned()),
                (1, 0, "Tc-99".to_owned(), "Tc99".to_owned()),
                (2, 1, "Co-60".to_owned(), "Co60".to_owned()),
                (2, 2, "Co-60".to_owned(), "Co60".to_owned()),
                (2, 3, "Co-60".to_owned(), "Co60".to_owned()),
            ]
        );
        for row in &rows[3..5] {
            assert!(row["limit"].is_null());
            assert!(row["fraction"].is_null());
        }
    }

    #[test]
    fn component_timestamp_equality_is_exact_and_membership_is_disjoint() {
        let groups = HashMap::from([(
            "primary".to_string(),
            vec!["cell-a".to_string(), "cell-b".to_string()],
        )]);
        let bytes = synthetic_mesh(f64::from_bits(10.0f64.to_bits() + 1), 0.0);
        assert!(evaluate(
            &synthetic_extension(),
            Some(&groups),
            &bytes,
            "mesh.ndjson",
            Path::new("."),
        )
        .is_err());
        let mut extension = synthetic_extension();
        extension["components"]["primary"]["mass_g"] = serde_json::json!(1.0);
        extension["components"]["primary"]["cell_masses_g"] = serde_json::json!({"cell-a":1.0});
        extension["components"]["other"] = serde_json::json!({
            "mass_g":1.0,"displaced_volume_cm3":1.0,"waste_type":"general",
            "cell_masses_g":{"cell-a":1.0},"external_tritium":{"status":"not_applicable"}
        });
        let extension: WasteExtension = serde_json::from_value(extension).unwrap();
        let overlap = validate_component_membership(
            &HashMap::from([
                ("primary".into(), vec!["cell-a".into()]),
                ("other".into(), vec!["cell-a".into()]),
            ]),
            &extension,
            &waste::load_input_bytes(&synthetic_mesh(10.0, 0.0)).unwrap(),
        )
        .unwrap_err();
        assert!(overlap.contains("overlap"));
    }

    #[test]
    fn upper_positive_missing_properties_yield_unknown_nominal_class() {
        let groups = HashMap::from([(
            "primary".to_string(),
            vec!["cell-a".to_string(), "cell-b".to_string()],
        )]);
        let mut extension = synthetic_extension();
        extension["nuclide_properties"] = serde_json::json!({});
        let (classification, summary) = evaluate(
            &extension,
            Some(&groups),
            &synthetic_mesh(10.0, 0.0),
            "mesh.ndjson",
            Path::new("."),
        )
        .unwrap();
        let target = &classification["components"][0]["targets"][0];
        assert_eq!(target["class"], "unknown");
        assert_eq!(target["coverage"], "incomplete");
        assert_eq!(
            target["evaluation"]["unknown_nuclides"],
            serde_json::json!(["C14", "H3"])
        );
        assert_eq!(summary["target_class_counts"][0]["counts"]["unknown"], 1);
    }

    #[test]
    fn activity_aliases_and_required_external_tritium_fail_closed() {
        let groups = HashMap::from([(
            "primary".to_string(),
            vec!["cell-a".to_string(), "cell-b".to_string()],
        )]);
        let alias_mesh = String::from_utf8(synthetic_mesh(10.0, 0.0))
            .unwrap()
            .replace("\"C14\":5.0", "\"C14\":5.0,\"C-14\":5.0")
            .into_bytes();
        assert!(evaluate(
            &synthetic_extension(),
            Some(&groups),
            &alias_mesh,
            "mesh.ndjson",
            Path::new("."),
        )
        .is_err());

        let mut extension = synthetic_extension();
        extension["components"]["primary"]["external_tritium"] =
            serde_json::json!({"status":"required"});
        let (classification, summary) = evaluate(
            &extension,
            Some(&groups),
            &synthetic_mesh(10.0, 0.0),
            "mesh.ndjson",
            Path::new("."),
        )
        .unwrap();
        assert_eq!(
            classification["components"][0]["targets"][0]["class"],
            "unknown"
        );
        assert_eq!(summary["target_class_counts"][0]["counts"]["unknown"], 1);
    }

    #[test]
    fn partial_membership_is_reported_without_inventing_component_class() {
        let mut extension = synthetic_extension();
        extension["components"]["primary"]["mass_g"] = serde_json::json!(1.0);
        extension["components"]["primary"]["cell_masses_g"] = serde_json::json!({"cell-a":1.0});
        let groups = HashMap::from([("primary".to_string(), vec!["cell-a".to_string()])]);
        let (classification, summary) = evaluate(
            &extension,
            Some(&groups),
            &synthetic_mesh(10.0, 0.0),
            "mesh.ndjson",
            Path::new("."),
        )
        .unwrap();
        assert_eq!(summary["membership_coverage"], "partial");
        assert_eq!(
            summary["unassigned_cell_ids"],
            serde_json::json!(["cell-b"])
        );
        assert_eq!(summary["component_count"], 1);
        assert!(classification["components"][0]["targets"][0]["class"] == "A");
    }
}
