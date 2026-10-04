//! Opt-in screening against the February 2026 draft NUREG-1556 Vol. 22 table.
//!
//! This is a draft screening aid only. It preserves the nominal Part 61 result
//! separately and does not make a disposal, dose, or legal determination.

use crate::waste::{self, ComponentSpec, ExternalTritium};
use actinv_core::waste::{
    normalize_nuclide_key, NuclideProperties, RulePack, WasteClass, WasteType,
};
use actinv_core::waste_intrusion::{
    evaluate_intrusion_target, validate_declarations, DotRqDeclaration, DraftRulePack,
    IntrusionDeclarations, IntrusionTargetInput, InventoryCoverage, SiteWacDeclaration, WasteForm,
};
use serde::de::{MapAccess, SeqAccess, Visitor};
use serde::{Deserialize, Deserializer};
use serde_json::{json, Map, Number, Value};
use std::collections::BTreeSet;
use std::fs::{self, File};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};

const MAX_OUTER_BYTES: u64 = 8 * 1024 * 1024;
const MAX_RESULT_BYTES: u64 = 64 * 1024 * 1024;
const MAX_MESH_CELLS: usize = 128;
const MAX_OUTPUT_BYTES: usize = 32 * 1024 * 1024;
const RESULT_SCHEMA: &str = "actinv-waste-intrusion-screen-result-1";
const OUTER_SCHEMA: &str = "actinv-waste-intrusion-screen-spec-1";
const DRAFT_PACK_ID: &str = "us-nrc-nureg1556-v22-draft-2026-02-v1";

/// A JSON value whose object visitor rejects duplicate keys before a normal
/// `serde_json::Value` map could discard them.
struct UniqueJson(Value);

impl<'de> Deserialize<'de> for UniqueJson {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        struct UniqueVisitor;

        impl<'de> Visitor<'de> for UniqueVisitor {
            type Value = UniqueJson;

            fn expecting(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
                formatter.write_str("a JSON value with no duplicate object keys")
            }

            fn visit_unit<E>(self) -> Result<Self::Value, E> {
                Ok(UniqueJson(Value::Null))
            }

            fn visit_none<E>(self) -> Result<Self::Value, E> {
                Ok(UniqueJson(Value::Null))
            }

            fn visit_bool<E>(self, value: bool) -> Result<Self::Value, E> {
                Ok(UniqueJson(Value::Bool(value)))
            }

            fn visit_i64<E>(self, value: i64) -> Result<Self::Value, E> {
                Ok(UniqueJson(Value::Number(Number::from(value))))
            }

            fn visit_u64<E>(self, value: u64) -> Result<Self::Value, E> {
                Ok(UniqueJson(Value::Number(Number::from(value))))
            }

            fn visit_f64<E>(self, value: f64) -> Result<Self::Value, E>
            where
                E: serde::de::Error,
            {
                Number::from_f64(value)
                    .map(|number| UniqueJson(Value::Number(number)))
                    .ok_or_else(|| E::custom("non-finite JSON number"))
            }

            fn visit_str<E>(self, value: &str) -> Result<Self::Value, E> {
                Ok(UniqueJson(Value::String(value.to_owned())))
            }

            fn visit_string<E>(self, value: String) -> Result<Self::Value, E> {
                Ok(UniqueJson(Value::String(value)))
            }

            fn visit_seq<A>(self, mut sequence: A) -> Result<Self::Value, A::Error>
            where
                A: SeqAccess<'de>,
            {
                let mut values = Vec::new();
                while let Some(value) = sequence.next_element::<UniqueJson>()? {
                    values.push(value.0);
                }
                Ok(UniqueJson(Value::Array(values)))
            }

            fn visit_map<A>(self, mut object: A) -> Result<Self::Value, A::Error>
            where
                A: MapAccess<'de>,
            {
                let mut values = Map::new();
                while let Some(key) = object.next_key::<String>()? {
                    if values.contains_key(&key) {
                        return Err(serde::de::Error::custom(format!(
                            "duplicate object key '{key}'"
                        )));
                    }
                    let value = object.next_value::<UniqueJson>()?;
                    values.insert(key, value.0);
                }
                Ok(UniqueJson(Value::Object(values)))
            }
        }

        deserializer.deserialize_any(UniqueVisitor)
    }
}

pub(crate) fn parse_unique_json(bytes: &[u8], context: &str) -> Result<Value, String> {
    let mut deserializer = serde_json::Deserializer::from_slice(bytes);
    let value = UniqueJson::deserialize(&mut deserializer)
        .map_err(|error| format!("{context}: {error}"))?;
    deserializer
        .end()
        .map_err(|error| format!("{context}: {error}"))?;
    Ok(value.0)
}

/// Reject duplicate keys in either a native result object or every mesh NDJSON
/// record. The nominal parser remains responsible for the full file schema.
pub(crate) fn validate_unique_result_json(bytes: &[u8]) -> Result<(), String> {
    if parse_unique_json(bytes, "result input").is_ok() {
        return Ok(());
    }
    let text =
        std::str::from_utf8(bytes).map_err(|error| format!("mesh input is not UTF-8: {error}"))?;
    let mut cell_count = 0usize;
    let mut records = 0usize;
    for (index, line) in text.lines().enumerate() {
        if line.trim().is_empty() {
            continue;
        }
        records += 1;
        let value = parse_unique_json(line.as_bytes(), &format!("mesh line {}", index + 1))?;
        if value.get("record").and_then(Value::as_str) == Some("cell") {
            cell_count += 1;
            if cell_count > MAX_MESH_CELLS {
                return Err(format!("mesh input exceeds {MAX_MESH_CELLS} cells"));
            }
        }
    }
    if records == 0 {
        return Err("mesh input contains no records".into());
    }
    Ok(())
}

pub(crate) fn read_regular_bounded(
    path: &Path,
    max_bytes: u64,
    label: &str,
) -> Result<Vec<u8>, String> {
    let path_meta = fs::symlink_metadata(path)
        .map_err(|error| format!("cannot inspect {label} {}: {error}", path.display()))?;
    if !path_meta.file_type().is_file() {
        return Err(format!(
            "{label} {} must be a regular non-symlink file",
            path.display()
        ));
    }
    if path_meta.len() > max_bytes {
        return Err(format!(
            "{label} {} exceeds {max_bytes} bytes",
            path.display()
        ));
    }
    let file = File::open(path)
        .map_err(|error| format!("cannot open {label} {}: {error}", path.display()))?;
    let opened_meta = file
        .metadata()
        .map_err(|error| format!("cannot inspect opened {label} {}: {error}", path.display()))?;
    if !opened_meta.is_file() {
        return Err(format!(
            "opened {label} {} is not a regular file",
            path.display()
        ));
    }
    if opened_meta.len() > max_bytes {
        return Err(format!(
            "opened {label} {} exceeds {max_bytes} bytes",
            path.display()
        ));
    }
    let mut bytes = Vec::with_capacity(opened_meta.len() as usize);
    file.take(max_bytes + 1)
        .read_to_end(&mut bytes)
        .map_err(|error| format!("cannot read {label} {}: {error}", path.display()))?;
    if bytes.len() as u64 > max_bytes {
        return Err(format!(
            "{label} {} exceeds {max_bytes} bytes",
            path.display()
        ));
    }
    Ok(bytes)
}

fn resolve_base(spec_path: &Path) -> PathBuf {
    spec_path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
        .unwrap_or_else(|| Path::new("."))
        .to_path_buf()
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct IntrusionSpec {
    schema: String,
    draft_rules: String,
    waste_spec: Value,
    package_basis: String,
    waste_form: WasteForm,
    inventory_coverage: InventoryCoverage,
    unbounded_inventory_reasons: Vec<String>,
    #[serde(default)]
    site_wac: Option<SiteWacDeclaration>,
    #[serde(default)]
    dot_rq: Option<DotRqDeclaration>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct EmbeddedWasteSpec {
    schema: String,
    rules: String,
    input: String,
    targets: Vec<u64>,
    nuclide_properties: std::collections::BTreeMap<String, NuclideProperties>,
    components: Vec<ComponentSpec>,
}

fn sha256(bytes: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    Sha256::digest(bytes)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

pub(crate) fn validate_property_keys(
    properties: &std::collections::BTreeMap<String, NuclideProperties>,
    rules: &RulePack,
    waste_type: WasteType,
) -> Result<(), String> {
    if properties.len() > 1024 {
        return Err("nuclide_properties exceeds 1024 entries".into());
    }
    let mut seen = BTreeSet::new();
    for (raw, property) in properties {
        let key = normalize_nuclide_key(raw)?;
        if !seen.insert(key.clone()) {
            return Err(format!(
                "duplicate nuclide properties after normalization: {key}"
            ));
        }
        rules.nuclide_listed(waste_type, &key, Some(*property))?;
    }
    Ok(())
}

pub(crate) fn validate_result_activity_aliases(bytes: &[u8]) -> Result<(), String> {
    let whole = parse_unique_json(bytes, "result input");
    if let Ok(value) = whole {
        validate_run_activity_aliases(&value, "native result")?;
        return Ok(());
    }
    let text =
        std::str::from_utf8(bytes).map_err(|error| format!("mesh input is not UTF-8: {error}"))?;
    let mut cells = 0usize;
    let mut records = 0usize;
    for (index, line) in text.lines().enumerate() {
        if line.trim().is_empty() {
            continue;
        }
        records += 1;
        let value = parse_unique_json(line.as_bytes(), &format!("mesh line {}", index + 1))?;
        if value.get("record").and_then(Value::as_str) == Some("cell") {
            cells += 1;
            if cells > MAX_MESH_CELLS {
                return Err(format!("mesh input exceeds {MAX_MESH_CELLS} cells"));
            }
            let result = value
                .get("result")
                .ok_or_else(|| format!("mesh line {} has no result", index + 1))?;
            validate_run_activity_aliases(result, &format!("mesh line {} result", index + 1))?;
        }
    }
    if records == 0 {
        return Err("mesh input contains no records".into());
    }
    Ok(())
}

fn validate_run_activity_aliases(value: &Value, context: &str) -> Result<(), String> {
    let steps = value
        .get("steps")
        .and_then(Value::as_array)
        .ok_or_else(|| format!("{context} has no steps array"))?;
    for (index, step) in steps.iter().enumerate() {
        let map = step
            .get("activity_Bq_per_g")
            .and_then(Value::as_object)
            .ok_or_else(|| format!("{context} step {} has no activity map", index + 1))?;
        let mut seen = BTreeSet::new();
        for (raw, value) in map {
            let activity = value.as_f64().ok_or_else(|| {
                format!(
                    "{context} step {} has nonnumeric activity for {raw}",
                    index + 1
                )
            })?;
            if !activity.is_finite() || activity < 0.0 {
                return Err(format!(
                    "{context} step {} activity for {raw} must be finite and nonnegative",
                    index + 1
                ));
            }
            let key = normalize_nuclide_key(raw)?;
            if !seen.insert(key.clone()) {
                return Err(format!(
                    "{context} step {} repeats activity alias {key}",
                    index + 1
                ));
            }
        }
    }
    Ok(())
}

pub(crate) fn preflight_aggregated_activities(
    component: &ComponentSpec,
    target_steps: &[u64],
    input: &waste::InputResults,
) -> Result<(), String> {
    let activities = waste::activity_for_component(input, component, target_steps)?;
    for step in target_steps {
        let (_, map) = activities
            .get(step)
            .ok_or_else(|| format!("selected step {step} is absent"))?;
        let mut canonical_activity = std::collections::BTreeMap::new();
        for (raw, activity) in map {
            if !activity.is_finite() || *activity < 0.0 {
                return Err(format!(
                    "whole-component activity for {raw} at step {step} is invalid"
                ));
            }
            let canonical = normalize_nuclide_key(raw)?;
            if canonical_activity
                .insert(canonical.clone(), *activity)
                .is_some()
            {
                return Err(format!(
                    "duplicate whole-component activity alias {canonical} at step {step}"
                ));
            }
        }
        let (external, _) = waste::external_tritium_at(&component.external_tritium, *step)?;
        if external > 0.0 {
            let h3 = canonical_activity.entry("H3".to_string()).or_insert(0.0);
            *h3 += external;
            if !h3.is_finite() {
                return Err(format!(
                    "combined external H-3 activity overflowed at step {step}"
                ));
            }
        }
        let positive_count = canonical_activity
            .values()
            .filter(|value| **value > 0.0)
            .count();
        if positive_count > 1024 {
            return Err(format!(
                "selected step {step} exceeds 1024 positive nuclides"
            ));
        }
    }
    Ok(())
}

fn declaration_metadata(value: &IntrusionSpec) -> (Value, Value) {
    let wac = value.site_wac.as_ref();
    let rq = value.dot_rq.as_ref();
    let wac_meta = json!({
        "supplied": wac.is_some(),
        "source": wac.map(|declaration| declaration.source.as_str()),
        "membership_coverage": wac.map(|declaration| &declaration.membership_coverage),
    });
    let rq_meta = json!({
        "supplied": rq.is_some(),
        "source": rq.map(|declaration| declaration.source.as_str()),
        "coverage": rq.map(|declaration| &declaration.coverage),
        "unit": "Bq",
        "applicability_basis": "caller_declared",
    });
    (wac_meta, rq_meta)
}

fn screen_interpretations() -> Value {
    json!({
        "presence_or": "three_valued_any_true",
        "criterion_1": "concentration_strictly_over_1_percent_site_wac",
        "criterion_2": "consensus_either_or_both",
        "criterion_3": "individual_rq_inclusive_mixture_only_indeterminate",
        "criterion_4": "complete_container_share_at_least_1_percent",
        "row_equality": "indeterminate",
        "short_group": "strict_lt5_julian_years_all_named_included",
        "alpha_group": "fixed_eight",
        "row_arithmetic": "all_known_matching_inventory",
        "selected_column": "computed_nominal_class_only",
        "metal_rows": "paired_variant_replacement",
        "unlisted_form": "review_indicated",
        "source_gaps": "visible_when_presence_true",
    })
}

fn write_screen(doc: &Value, output: Option<&str>) -> Result<(), String> {
    let mut bytes = serde_json::to_vec_pretty(doc).map_err(|error| error.to_string())?;
    bytes.push(b'\n');
    if bytes.len() > MAX_OUTPUT_BYTES {
        return Err(format!(
            "serialized intrusion screen exceeds {MAX_OUTPUT_BYTES} bytes"
        ));
    }
    match output {
        Some(path) => {
            fs::write(path, bytes).map_err(|error| format!("cannot write {path}: {error}"))
        }
        None => std::io::stdout()
            .write_all(&bytes)
            .map_err(|error| format!("cannot write intrusion screen to stdout: {error}")),
    }
}

pub(crate) fn run(path: &str, output: Option<&str>) -> Result<Value, String> {
    let outer_path = Path::new(path);
    let base = resolve_base(outer_path);
    let outer_bytes = read_regular_bounded(outer_path, MAX_OUTER_BYTES, "intrusion screen spec")?;
    let outer_value = parse_unique_json(&outer_bytes, "intrusion screen spec")?;
    let outer_object = outer_value
        .as_object()
        .ok_or("intrusion screen spec must be a JSON object")?;
    for key in ["site_wac", "dot_rq"] {
        if outer_object.get(key).is_some_and(Value::is_null) {
            return Err(format!(
                "{key} must be omitted or an object; null is not allowed"
            ));
        }
    }
    let outer: IntrusionSpec = serde_json::from_value(outer_value)
        .map_err(|error| format!("cannot decode intrusion screen spec: {error}"))?;
    if outer.schema != OUTER_SCHEMA {
        return Err(format!(
            "unsupported intrusion screen schema '{}'",
            outer.schema
        ));
    }
    if outer.draft_rules != DRAFT_PACK_ID {
        return Err(format!(
            "unsupported draft rule pack '{}'",
            outer.draft_rules
        ));
    }
    if outer.package_basis != "single_component_container" {
        return Err("package_basis must be single_component_container".into());
    }
    let embedded: EmbeddedWasteSpec = serde_json::from_value(outer.waste_spec.clone())
        .map_err(|error| format!("cannot decode embedded waste_spec: {error}"))?;
    if embedded.schema != "actinv-waste-spec-1" {
        return Err(format!("unsupported waste schema '{}'", embedded.schema));
    }
    if embedded.rules != "us-nrc-10cfr61.55-v1" {
        return Err("intrusion screen requires bundled us-nrc-10cfr61.55-v1 rules".into());
    }
    waste::validate_targets(&embedded.targets)?;
    if embedded.targets.len() > 64 {
        return Err("intrusion screen supports at most 64 target steps".into());
    }
    if embedded.components.len() != 1 {
        return Err("intrusion screen requires exactly one whole-container component".into());
    }
    let component = &embedded.components[0];
    if component.id.trim().is_empty() {
        return Err("component id must not be empty".into());
    }
    match (outer.waste_form, component.waste_type) {
        (WasteForm::Metal, WasteType::ActivatedMetal)
        | (WasteForm::CementOrPolymer, WasteType::General)
        | (WasteForm::Soil, WasteType::General)
        | (WasteForm::Equipment, WasteType::General)
        | (WasteForm::Rubble, WasteType::General)
        | (WasteForm::Resin, WasteType::General)
        | (WasteForm::Ash, WasteType::General)
        | (WasteForm::Calcined, WasteType::General)
        | (WasteForm::Other, WasteType::General) => {}
        _ => return Err("waste_form and nominal waste_type are incompatible".into()),
    }
    let geometry = waste::waste_geometry(component)?;
    let part61_rules = waste::load_rule_pack("us-nrc-10cfr61.55-v1", &base)?;
    validate_property_keys(
        &embedded.nuclide_properties,
        &part61_rules,
        component.waste_type,
    )?;

    let required_h3_missing = matches!(&component.external_tritium, ExternalTritium::Required);
    if outer
        .site_wac
        .as_ref()
        .is_some_and(|declaration| declaration.nuclides.len() > 1024)
    {
        return Err("site_wac exceeds 1024 nuclide entries".into());
    }
    if outer
        .dot_rq
        .as_ref()
        .is_some_and(|declaration| declaration.nuclides.len() > 1024)
    {
        return Err("dot_rq exceeds 1024 nuclide entries".into());
    }
    let validated_declarations = validate_declarations(IntrusionDeclarations {
        waste_form: outer.waste_form,
        inventory_coverage: outer.inventory_coverage,
        unbounded_inventory_reasons: outer.unbounded_inventory_reasons.clone(),
        required_h3_missing,
        site_wac: outer.site_wac.clone(),
        dot_rq: outer.dot_rq.clone(),
    })?;
    let draft_rules = DraftRulePack::bundled()?;

    let input_path = base.join(&embedded.input);
    let input_bytes = read_regular_bounded(&input_path, MAX_RESULT_BYTES, "waste result input")?;
    validate_unique_result_json(&input_bytes)?;
    validate_result_activity_aliases(&input_bytes)?;
    let parsed_input = waste::load_input_bytes(&input_bytes)?;
    if parsed_input.cells.len() > MAX_MESH_CELLS {
        return Err(format!("result input exceeds {MAX_MESH_CELLS} cells"));
    }
    preflight_aggregated_activities(component, &embedded.targets, &parsed_input)?;

    let inner_json = serde_json::to_string(&outer.waste_spec)
        .map_err(|error| format!("cannot serialize embedded waste_spec: {error}"))?;
    let evaluated = waste::evaluate_doc_with_input_details(&inner_json, &base, &input_bytes)?;
    let component_result = evaluated
        .document
        .get("components")
        .and_then(Value::as_array)
        .and_then(|items| items.first())
        .ok_or("nominal waste evaluation returned no component")?;
    let nominal_targets = component_result
        .get("targets")
        .and_then(Value::as_array)
        .ok_or("nominal waste evaluation returned no targets")?;
    if nominal_targets.len() != evaluated.target_inventories.len() {
        return Err("nominal waste target inventory mismatch".into());
    }
    let mut target_screens = Vec::with_capacity(nominal_targets.len());
    for (nominal_target, inventory) in nominal_targets.iter().zip(&evaluated.target_inventories) {
        if inventory.component_id != component.id {
            return Err("nominal waste returned an unexpected component identity".into());
        }
        let step = nominal_target
            .get("step")
            .and_then(Value::as_u64)
            .ok_or("nominal waste target has invalid step")?;
        if step != inventory.step {
            return Err("nominal waste target step does not match activity map".into());
        }
        let target_time = nominal_target
            .get("t_s")
            .and_then(Value::as_f64)
            .filter(|time| time.is_finite() && *time >= 0.0)
            .ok_or("nominal waste target has invalid time")?;
        if target_time != inventory.t_s {
            return Err("nominal waste target time does not match activity map".into());
        }
        let nominal_class: WasteClass = serde_json::from_value(
            nominal_target
                .get("class")
                .cloned()
                .ok_or("nominal waste target has no class")?,
        )
        .map_err(|error| format!("invalid nominal waste class: {error}"))?;
        let evaluated_target = evaluate_intrusion_target(
            &draft_rules,
            IntrusionTargetInput {
                part61_rules: &part61_rules,
                waste_type: component.waste_type,
                geometry,
                activation_activities_bq: &inventory.activation_activities_bq,
                external_tritium_activity_bq: inventory.external_tritium_activity_bq,
                nuclide_properties: &embedded.nuclide_properties,
                nominal_class,
                declarations: &validated_declarations,
            },
        )?;
        let mut target_value = serde_json::to_value(evaluated_target)
            .map_err(|error| format!("cannot serialize intrusion target: {error}"))?;
        let target_object = target_value
            .as_object_mut()
            .ok_or("intrusion target serialization was not an object")?;
        target_object.insert("step".into(), json!(step));
        target_object.insert("t_s".into(), json!(inventory.t_s));
        target_screens.push(target_value);
    }

    let (site_wac_meta, dot_rq_meta) = declaration_metadata(&outer);
    let pack_identity = draft_rules.identity();
    let screen = json!({
        "draft_pack": pack_identity,
        "interpretations": screen_interpretations(),
        "package_basis": outer.package_basis,
        "waste_form": outer.waste_form,
        "inventory_coverage": outer.inventory_coverage,
        "unbounded_inventory_reasons": outer.unbounded_inventory_reasons,
        "site_wac": site_wac_meta,
        "dot_rq": dot_rq_meta,
        "targets": target_screens,
        "disposal_acceptance": "not_assessed",
        "intrusion_dose": "not_calculated",
        "legal_compliance": "not_determined",
    });
    let generated_waste_spec_sha256 = sha256(inner_json.as_bytes());
    let result = json!({
        "schema": RESULT_SCHEMA,
        "input_sha256": sha256(&outer_bytes),
        "generated_waste_spec_json": inner_json,
        "generated_waste_spec_sha256": generated_waste_spec_sha256,
        "classification": evaluated.document,
        "draft_intrusion_screen": screen,
    });
    write_screen(&result, output)?;
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    struct Scratch(PathBuf);

    impl Scratch {
        fn new() -> Self {
            static NEXT: AtomicUsize = AtomicUsize::new(0);
            let path = std::env::temp_dir().join(format!(
                "actinv-waste-intrusion-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            fs::create_dir_all(&path).unwrap();
            Self(path)
        }

        fn path(&self, name: &str) -> PathBuf {
            self.0.join(name)
        }

        fn write(&self, name: &str, bytes: &[u8]) -> PathBuf {
            let path = self.path(name);
            fs::write(&path, bytes).unwrap();
            path
        }
    }

    impl Drop for Scratch {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    fn valid_spec() -> Value {
        json!({
            "schema": OUTER_SCHEMA,
            "draft_rules": DRAFT_PACK_ID,
            "waste_spec": {
                "schema":"actinv-waste-spec-1",
                "rules":"us-nrc-10cfr61.55-v1",
                "input":"run.json",
                "targets":[1],
                "nuclide_properties":{"C14":{"z":6,"half_life_s":1.8e11,"alpha_emitting":false}},
                "components":[{"id":"whole-container","mass_g":1.0,"displaced_volume_cm3":1.0,
                    "waste_type":"general","external_tritium":{"status":"not_applicable"}}]
            },
            "package_basis":"single_component_container",
            "waste_form":"equipment",
            "inventory_coverage":"complete",
            "unbounded_inventory_reasons":[]
        })
    }

    #[test]
    fn recursive_duplicate_keys_are_rejected_before_value_conversion() {
        assert!(parse_unique_json(br#"{"outer":{"x":1,"x":2}}"#, "test").is_err());
        assert!(parse_unique_json(br#"{"outer":{"x":1,"y":2}}"#, "test").is_ok());
        let mesh = b"{\"record\":\"header\",\"schema\":\"actinv-mesh-result-1\",\"cell_count\":1}\n{\"record\":\"cell\",\"id\":\"c1\",\"id\":\"c2\"}\n";
        assert!(validate_unique_result_json(mesh).is_err());
    }

    #[test]
    fn intrusion_screen_reuses_nominal_result_and_writes_one_complete_report() {
        let scratch = Scratch::new();
        let run_result = json!({"entry_point":"test","mode":"coupled","steps":[
            {"step":1,"t_s":10.0,"activity_Bq_per_g":{"C14":1.0}}
        ]});
        scratch.write("run.json", run_result.to_string().as_bytes());
        let spec = scratch.write(
            "screen.json",
            serde_json::to_string(&valid_spec()).unwrap().as_bytes(),
        );
        let out = scratch.path("screen-out.json");
        let report = run(spec.to_str().unwrap(), Some(out.to_str().unwrap())).unwrap();
        assert_eq!(report["schema"], RESULT_SCHEMA);
        assert_eq!(report["classification"]["schema"], "actinv-waste-result-1");
        assert_eq!(report["draft_intrusion_screen"]["targets"][0]["step"], 1);
        assert_eq!(
            report["draft_intrusion_screen"]["targets"][0]["inventory_activity_bq"]["C14"],
            1.0
        );
        assert_eq!(
            report["draft_intrusion_screen"]["targets"][0]["external_tritium_activity_bq"],
            0.0
        );
        assert_eq!(
            report["draft_intrusion_screen"]["disposal_acceptance"],
            "not_assessed"
        );
        let persisted: Value = serde_json::from_slice(&fs::read(out).unwrap()).unwrap();
        assert_eq!(persisted, report);
    }

    #[test]
    fn invalid_late_target_preserves_existing_output_sentinel() {
        let scratch = Scratch::new();
        let run_result = json!({"entry_point":"test","mode":"coupled","steps":[
            {"step":1,"t_s":10.0,"activity_Bq_per_g":{"C14":1.0}}
        ]});
        scratch.write("run.json", run_result.to_string().as_bytes());
        let mut spec = valid_spec();
        spec["waste_spec"]["targets"] = json!([1, 2]);
        let spec_path = scratch.write(
            "screen.json",
            serde_json::to_string(&spec).unwrap().as_bytes(),
        );
        let output = scratch.write("out.json", b"sentinel");
        assert!(run(spec_path.to_str().unwrap(), Some(output.to_str().unwrap())).is_err());
        assert_eq!(fs::read(output).unwrap(), b"sentinel");
    }
}
