//! Classification for caller-declared affine natural-element composition ranges.

use std::collections::{BTreeMap, BTreeSet};

use actinv_core::waste::{normalize_nuclide_key, NuclideProperties, RulePack, WasteType};
use actinv_core::waste_bounds::ActivityInterval;
use actinv_core::waste_composition::{project_response, ResponseProjection, WeightInterval};
use actinv_data::composition::{material_key, MaterialKey};
use serde::Deserialize;
use serde_json::{json, Value};

const RULE_ID: &str = "us-nrc-10cfr61.55-v1";
const RULE_SHA256: &str = "890268af81a53815b8e11c238e4a1694eabb79664a1ca087fd7fe22b9fabe5d7";
const SPEC_SCHEMA: &str = "actinv-waste-composition-spec-1";
const METHOD: &str = "declared_affine_composition_polytope";
const RESPONSE_MODEL: &str = "fixed_rate_affine_activity";

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct CompositionSpec {
    schema: String,
    rules: String,
    response_model: String,
    components: Vec<CompositionComponent>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct CompositionComponent {
    id: String,
    mass_g: f64,
    #[serde(default, deserialize_with = "present_f64")]
    displaced_volume_cm3: Option<f64>,
    #[serde(default, deserialize_with = "present_f64")]
    density_g_cm3: Option<f64>,
    waste_type: WasteType,
    #[serde(deserialize_with = "crate::waste_bounds::deserialize_unique_map")]
    nuclide_properties: BTreeMap<String, NuclideProperties>,
    external_tritium: crate::waste_bounds::ExternalTritium,
    #[serde(deserialize_with = "crate::waste_bounds::deserialize_unique_map")]
    composition_wt_percent_bounds: BTreeMap<String, WeightInterval>,
    targets: Vec<CompositionTarget>,
}

#[derive(Debug, Deserialize)]
#[serde(transparent)]
struct UniqueNuclideMap(
    #[serde(deserialize_with = "crate::waste_bounds::deserialize_unique_map")]
    BTreeMap<String, f64>,
);

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct CompositionTarget {
    step: u64,
    t_s: f64,
    #[serde(deserialize_with = "crate::waste_bounds::deserialize_unique_map")]
    element_activity_bq_per_g: BTreeMap<String, UniqueNuclideMap>,
    inventory_coverage: crate::waste_bounds::InventoryCoverage,
    unbounded_inventory_reasons: Vec<String>,
    bounds_source: String,
    bounds_assumptions: String,
}

fn present_f64<'de, D>(deserializer: D) -> Result<Option<f64>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    f64::deserialize(deserializer).map(Some)
}

fn canonical_element(raw: &str) -> Result<String, String> {
    match material_key(raw)? {
        MaterialKey::Element(symbol) if raw == symbol => Ok(symbol),
        MaterialKey::Element(symbol) => Err(format!(
            "composition key '{raw}' is not canonical natural-element symbol '{symbol}'"
        )),
        MaterialKey::Nuclide { .. } => Err(format!(
            "composition key '{raw}' is an isotope; natural-element symbols are required"
        )),
    }
}

fn canonical_weights(
    raw: &BTreeMap<String, WeightInterval>,
) -> Result<BTreeMap<String, WeightInterval>, String> {
    if !(1..=64).contains(&raw.len()) {
        return Err("composition must contain between 1 and 64 natural elements".into());
    }
    let mut weights = BTreeMap::new();
    for (key, bound) in raw {
        let element = canonical_element(key)?;
        if weights.insert(element.clone(), *bound).is_some() {
            return Err(format!(
                "duplicate composition element after normalization: '{element}'"
            ));
        }
        if !bound.lower_wt_percent.is_finite()
            || !bound.upper_wt_percent.is_finite()
            || bound.lower_wt_percent < 0.0
            || bound.upper_wt_percent > 100.0
            || bound.lower_wt_percent > bound.upper_wt_percent
        {
            return Err(format!("invalid wt-percent interval for '{element}'"));
        }
    }
    Ok(weights)
}

fn canonical_properties(
    raw: &BTreeMap<String, NuclideProperties>,
) -> Result<BTreeMap<String, NuclideProperties>, String> {
    let mut properties = BTreeMap::new();
    for (key, property) in raw {
        let nuclide = normalize_nuclide_key(key)?;
        if property.z <= 0 || !property.half_life_s.is_finite() || property.half_life_s <= 0.0 {
            return Err(format!("nuclide properties for '{nuclide}' are invalid"));
        }
        if properties.insert(nuclide.clone(), *property).is_some() {
            return Err(format!(
                "duplicate nuclide property after normalization: '{nuclide}'"
            ));
        }
    }
    Ok(properties)
}

fn canonical_responses(
    target: &CompositionTarget,
    weights: &BTreeMap<String, WeightInterval>,
) -> Result<BTreeMap<String, BTreeMap<String, f64>>, String> {
    if target.element_activity_bq_per_g.len() != weights.len() {
        return Err(
            "element_activity_bq_per_g must contain every composition constituent exactly once"
                .into(),
        );
    }
    let mut responses = BTreeMap::new();
    for (raw_element, raw_map) in &target.element_activity_bq_per_g {
        let element = canonical_element(raw_element)?;
        if !weights.contains_key(&element) {
            return Err(format!(
                "response supplied for undeclared composition element '{element}'"
            ));
        }
        let mut nuclides = BTreeMap::new();
        for (raw_nuclide, response) in &raw_map.0 {
            let nuclide = normalize_nuclide_key(raw_nuclide)?;
            if !response.is_finite() || *response < 0.0 {
                return Err(format!(
                    "response for '{element}/{nuclide}' must be finite and nonnegative"
                ));
            }
            if nuclides.insert(nuclide.clone(), *response).is_some() {
                return Err(format!(
                    "duplicate response nuclide after normalization: '{nuclide}'"
                ));
            }
            // The union is formed from these maps during projection below.
        }
        if responses.insert(element.clone(), nuclides).is_some() {
            return Err(format!(
                "duplicate response element after normalization: '{element}'"
            ));
        }
    }
    if responses.keys().ne(weights.keys()) {
        return Err("element_activity_bq_per_g keys must match composition constituents".into());
    }
    // The protocol defines omitted products within a declared element map as exact zero.
    // Return maps containing only supplied nonzero/explicit coefficients; absent entries
    // are zero-filled when each nuclide's projection vector is built.
    Ok(responses)
}

fn validate_component_geometry(component: &CompositionComponent) -> Result<(), String> {
    if !component.mass_g.is_finite() || component.mass_g <= 0.0 {
        return Err(format!(
            "component '{}' mass_g must be positive and finite",
            component.id
        ));
    }
    let volume = match (component.displaced_volume_cm3, component.density_g_cm3) {
        (Some(value), None) if value.is_finite() && value > 0.0 => value,
        (None, Some(density)) if density.is_finite() && density > 0.0 => component.mass_g / density,
        (Some(_), Some(_)) => {
            return Err(format!(
                "component '{}' may declare displaced volume or density, not both",
                component.id
            ))
        }
        (None, None) => {
            return Err(format!(
                "component '{}' requires displaced_volume_cm3 or density_g_cm3",
                component.id
            ))
        }
        _ => {
            return Err(format!(
                "component '{}' geometry must be positive and finite",
                component.id
            ))
        }
    };
    if !volume.is_finite() || volume <= 0.0 {
        return Err(format!(
            "component '{}' derived volume must be positive and finite",
            component.id
        ));
    }
    Ok(())
}

fn validate_spec(spec: &CompositionSpec) -> Result<(), String> {
    if spec.schema != SPEC_SCHEMA {
        return Err(format!(
            "unsupported waste composition schema '{}'",
            spec.schema
        ));
    }
    if spec.rules != RULE_ID {
        return Err(format!(
            "waste composition supports only bundled rule pack '{RULE_ID}'"
        ));
    }
    if spec.response_model != RESPONSE_MODEL {
        return Err(format!("response_model must be '{RESPONSE_MODEL}'"));
    }
    if spec.components.is_empty() {
        return Err("components must contain at least one component".into());
    }
    let mut ids = BTreeSet::new();
    for component in &spec.components {
        if component.id.trim().is_empty() || !ids.insert(&component.id) {
            return Err(format!(
                "component id '{}' is empty or duplicated",
                component.id
            ));
        }
        validate_component_geometry(component)?;
        let weights = canonical_weights(&component.composition_wt_percent_bounds)?;
        let zero_response: BTreeMap<String, f64> = weights
            .keys()
            .map(|element| (element.clone(), 0.0))
            .collect();
        project_response(&weights, &zero_response, component.mass_g)?;
        canonical_properties(&component.nuclide_properties)?;
        if component.targets.is_empty() {
            return Err(format!(
                "component '{}' must declare at least one target",
                component.id
            ));
        }
        let mut steps = BTreeSet::new();
        for target in &component.targets {
            if target.step == 0 || !steps.insert(target.step) {
                return Err(format!(
                    "component '{}' target steps must be positive and unique",
                    component.id
                ));
            }
            if !target.t_s.is_finite() || target.t_s < 0.0 {
                return Err(format!(
                    "component '{}' target time must be finite and nonnegative",
                    component.id
                ));
            }
            canonical_responses(target, &weights)?;
            let complete = matches!(
                target.inventory_coverage,
                crate::waste_bounds::InventoryCoverage::Complete
            );
            if complete && !target.unbounded_inventory_reasons.is_empty() {
                return Err(format!(
                    "complete coverage at step {} requires no unbounded reasons",
                    target.step
                ));
            }
            if !complete
                && (target.unbounded_inventory_reasons.is_empty()
                    || target
                        .unbounded_inventory_reasons
                        .iter()
                        .any(|reason| reason.trim().is_empty()))
            {
                return Err(format!(
                    "incomplete coverage at step {} requires nonempty reasons",
                    target.step
                ));
            }
            if target.bounds_source.trim().is_empty() || target.bounds_assumptions.trim().is_empty()
            {
                return Err(format!(
                    "target step {} requires nonempty bounds_source and bounds_assumptions",
                    target.step
                ));
            }
        }
    }
    Ok(())
}

fn projection_value(projection: &ResponseProjection) -> Value {
    json!({
        "lower_bq": projection.lower_bq,
        "upper_bq": projection.upper_bq,
        "min_witness_wt_percent": projection.min_witness_wt_percent,
        "max_witness_wt_percent": projection.max_witness_wt_percent,
        "lower_dual_lambda": projection.lower_dual_lambda,
        "upper_dual_lambda": projection.upper_dual_lambda,
    })
}

fn sha256(bytes: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    Sha256::digest(bytes)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

pub fn run(path: &str, output: Option<&str>) -> Result<Value, String> {
    let text = std::fs::read_to_string(path)
        .map_err(|error| format!("cannot read waste composition spec {path}: {error}"))?;
    run_doc(&text, output)
}

pub(crate) fn run_doc(text: &str, output: Option<&str>) -> Result<Value, String> {
    let spec: CompositionSpec = serde_json::from_str(text)
        .map_err(|error| format!("cannot parse waste composition spec: {error}"))?;
    validate_spec(&spec)?;
    let rules = RulePack::bundled()?;
    if rules.id != RULE_ID || rules.sha256 != RULE_SHA256 {
        return Err(
            "bundled waste rule pack identity or SHA-256 differs from the qualified pack".into(),
        );
    }

    let mut projected_components = Vec::new();
    let mut auxiliary =
        BTreeMap::<String, (BTreeMap<String, WeightInterval>, BTreeMap<u64, Value>)>::new();
    for component in &spec.components {
        let weights = canonical_weights(&component.composition_wt_percent_bounds)?;
        let properties = canonical_properties(&component.nuclide_properties)?;
        let mut projected_targets = Vec::new();
        let mut target_aux = BTreeMap::new();
        for target in &component.targets {
            let responses = canonical_responses(target, &weights)?;
            let nuclides: BTreeSet<String> = responses
                .values()
                .flat_map(|map| map.keys().cloned())
                .collect();
            let mut activation = BTreeMap::<String, ActivityInterval>::new();
            let mut projections = BTreeMap::<String, Value>::new();
            for nuclide in nuclides {
                let coefficients: BTreeMap<String, f64> = weights
                    .keys()
                    .map(|element| {
                        (
                            element.clone(),
                            responses[element].get(&nuclide).copied().unwrap_or(0.0),
                        )
                    })
                    .collect();
                let projection = project_response(&weights, &coefficients, component.mass_g)?;
                activation.insert(
                    nuclide.clone(),
                    ActivityInterval {
                        lower_bq: projection.lower_bq,
                        upper_bq: projection.upper_bq,
                    },
                );
                projections.insert(nuclide, projection_value(&projection));
            }
            // Preserve explicitly empty per-element coefficient maps in the output.
            let coefficient_map: BTreeMap<String, Value> = weights
                .keys()
                .map(|element| (element.clone(), json!(responses[element])))
                .collect();
            let target_doc = json!({
                "step": target.step,
                "t_s": target.t_s,
                "activity_bounds_bq": activation,
                "inventory_coverage": target.inventory_coverage,
                "unbounded_inventory_reasons": target.unbounded_inventory_reasons,
                "bounds_source": target.bounds_source,
                "bounds_assumptions": target.bounds_assumptions,
            });
            projected_targets.push(target_doc);
            let target_metadata = json!({
                "element_activity_bq_per_g": coefficient_map,
                "projection_records": projections,
            });
            target_aux.insert(target.step, target_metadata);
        }
        let mut component_doc = json!({
            "id": component.id,
            "mass_g": component.mass_g,
            "waste_type": component.waste_type,
            "nuclide_properties": properties,
            "external_tritium": component.external_tritium,
            "targets": projected_targets,
        });
        if let Some(volume) = component.displaced_volume_cm3 {
            component_doc["displaced_volume_cm3"] = json!(volume);
        }
        if let Some(density) = component.density_g_cm3 {
            component_doc["density_g_cm3"] = json!(density);
        }
        projected_components.push(component_doc);
        auxiliary.insert(component.id.clone(), (weights, target_aux));
    }

    let projected_spec = json!({
        "schema": "actinv-waste-bounds-spec-1",
        "rules": RULE_ID,
        "components": projected_components,
    });
    let projected_text =
        serde_json::to_string(&projected_spec).map_err(|error| error.to_string())?;
    let projected_input_sha256 = sha256(projected_text.as_bytes());
    // This validates/evaluates the complete projected P107 document without writing.
    let mut result = crate::waste_bounds::run_doc(&projected_text, None)?;
    let components_out = result["components"]
        .as_array_mut()
        .ok_or("bounds result has no components")?;
    for component_out in components_out {
        let id = component_out["id"]
            .as_str()
            .ok_or("bounds component has no id")?
            .to_string();
        let (weights, target_aux) = auxiliary
            .get(&id)
            .ok_or("composition metadata missing after bounds evaluation")?;
        component_out["composition_wt_percent_bounds"] =
            serde_json::to_value(weights).map_err(|error| error.to_string())?;
        component_out["composition_sum_constraint"] =
            json!({"operator":"equal","value_wt_percent":100.0});
        for target_out in component_out["targets"]
            .as_array_mut()
            .ok_or("bounds component has no targets")?
        {
            let step = target_out["step"]
                .as_u64()
                .ok_or("bounds target has invalid step")?;
            let target_model = target_aux
                .get(&step)
                .ok_or("composition target metadata missing")?;
            target_out["element_activity_bq_per_g"] =
                target_model["element_activity_bq_per_g"].clone();
            target_out["projection_records"] = target_model["projection_records"].clone();
        }
    }
    result["schema"] = json!("actinv-waste-composition-result-1");
    result["method"] = json!(METHOD);
    result["input_sha256"] = json!(sha256(text.as_bytes()));
    result["projected_input_sha256"] = json!(projected_input_sha256);
    result["response_model"] = json!(RESPONSE_MODEL);
    result["response_unit"] = json!("Bq/g");
    result["activity_unit"] = json!("Bq");
    result["composition_unit"] = json!("wt_percent");
    crate::waste::output_doc(&result, output)?;
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::run_doc;
    use serde_json::{json, Value};
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicUsize, Ordering};

    static NEXT: AtomicUsize = AtomicUsize::new(0);

    struct Scratch(PathBuf);
    impl Scratch {
        fn new() -> Self {
            let path = std::env::temp_dir().join(format!(
                "actinv-waste-composition-test-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            std::fs::create_dir(&path).unwrap();
            Self(path)
        }
    }
    impl Drop for Scratch {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    fn base_spec() -> Value {
        json!({
            "schema":"actinv-waste-composition-spec-1",
            "rules":"us-nrc-10cfr61.55-v1",
            "response_model":"fixed_rate_affine_activity",
            "components":[{
                "id":"c1", "mass_g":1.0, "displaced_volume_cm3":1.0,
                "waste_type":"general",
                "nuclide_properties":{"C14":{"z":6,"half_life_s":315576000.0,"alpha_emitting":false}},
                "external_tritium":{"status":"not_applicable"},
                "composition_wt_percent_bounds":{"Nb":{"lower_wt_percent":20.0,"upper_wt_percent":60.0},"Fe":{"lower_wt_percent":40.0,"upper_wt_percent":80.0}},
                "targets":[{
                    "step":1,"t_s":0.0,
                    "element_activity_bq_per_g":{"Nb":{"C14":1.0},"Fe":{}},
                    "inventory_coverage":"complete","unbounded_inventory_reasons":[],
                    "bounds_source":"artificial fixture","bounds_assumptions":"fixed affine response"
                }]
            }]
        })
    }

    #[test]
    fn projects_bounds_and_reuses_flat_p107_result_fields() {
        let result = run_doc(&serde_json::to_string(&base_spec()).unwrap(), None).unwrap();
        assert_eq!(result["schema"], "actinv-waste-composition-result-1");
        assert_eq!(result["method"], "declared_affine_composition_polytope");
        assert_eq!(result["response_unit"], "Bq/g");
        assert_eq!(result["activity_unit"], "Bq");
        assert_eq!(result["composition_unit"], "wt_percent");
        let component = &result["components"][0];
        assert_eq!(
            component["composition_sum_constraint"]["value_wt_percent"],
            100.0
        );
        let target = &component["targets"][0];
        assert_eq!(target["element_activity_bq_per_g"]["Nb"]["C14"], 1.0);
        let projection = &target["projection_records"]["C14"];
        assert!((projection["lower_bq"].as_f64().unwrap() - 0.2).abs() < 1e-12);
        assert!((projection["upper_bq"].as_f64().unwrap() - 0.6).abs() < 1e-12);
        assert_eq!(projection["min_witness_wt_percent"]["Nb"], 20.0);
        assert_eq!(projection["max_witness_wt_percent"]["Nb"], 60.0);
        assert!(target.get("lower").is_some());
        assert!(target.get("upper").is_some());
        assert!(target.get("constraint_ranges").is_some());
        assert!(target.get("class_envelope").is_some());
        assert_eq!(
            component["targets"][0]["activation_activity_bounds_bq"]["C14"]["lower_bq"],
            projection["lower_bq"]
        );
    }

    #[test]
    fn bounded_external_h3_is_merged_once_by_the_bounds_evaluator() {
        let mut spec = base_spec();
        spec["components"][0]["nuclide_properties"]["H3"] = json!({
            "z":1,"half_life_s":315576000.0,"alpha_emitting":false
        });
        spec["components"][0]["external_tritium"] = json!({
            "status":"bounded","source":"synthetic external inventory","excludes_activation":true,
            "activity_bounds_bq":{"1":{"lower_bq":3.0,"upper_bq":4.0}}
        });
        spec["components"][0]["composition_wt_percent_bounds"] =
            json!({"Nb":{"lower_wt_percent":100.0,"upper_wt_percent":100.0}});
        spec["components"][0]["targets"][0]["element_activity_bq_per_g"] = json!({"Nb":{"H3":2.0}});
        let result = run_doc(&serde_json::to_string(&spec).unwrap(), None).unwrap();
        let target = &result["components"][0]["targets"][0];
        let activation = &target["activation_activity_bounds_bq"]["H3"];
        assert_eq!(
            target["external_tritium_activity_bounds_bq"]["lower_bq"],
            3.0
        );
        assert!(activation["lower_bq"].as_f64().unwrap() <= 2.0);
        assert!(activation["upper_bq"].as_f64().unwrap() >= 2.0);
        let merged = &target["merged_activity_bounds_bq"]["H3"];
        assert_eq!(
            merged["lower_bq"],
            json!(activation["lower_bq"].as_f64().unwrap() + 3.0)
        );
        assert_eq!(
            merged["upper_bq"],
            json!(activation["upper_bq"].as_f64().unwrap() + 4.0)
        );
        assert!((merged["lower_bq"].as_f64().unwrap() - 5.0).abs() < 1e-12);
        assert!((merged["upper_bq"].as_f64().unwrap() - 6.0).abs() < 1e-12);
    }

    #[test]
    fn rejects_aliases_instead_of_merging_coordinates() {
        let mut spec = base_spec();
        spec["components"][0]["composition_wt_percent_bounds"]["fe"] =
            json!({"lower_wt_percent":0.0,"upper_wt_percent":0.0});
        assert!(run_doc(&serde_json::to_string(&spec).unwrap(), None).is_err());
        let duplicate_raw = r#"{"schema":"actinv-waste-composition-spec-1","rules":"us-nrc-10cfr61.55-v1","response_model":"fixed_rate_affine_activity","components":[{"id":"x","mass_g":1,"displaced_volume_cm3":1,"waste_type":"general","nuclide_properties":{},"external_tritium":{"status":"not_applicable"},"composition_wt_percent_bounds":{"Fe":{"lower_wt_percent":100,"upper_wt_percent":100},"Fe":{"lower_wt_percent":0,"upper_wt_percent":0}},"targets":[]}]}"#;
        assert!(run_doc(duplicate_raw, None).is_err());
    }

    #[test]
    fn empty_response_maps_still_require_an_exactly_feasible_polytope() {
        let mut spec = base_spec();
        spec["components"][0]["composition_wt_percent_bounds"] = json!({
            "Si":{"lower_wt_percent":34.0,"upper_wt_percent":34.0},
            "Nb":{"lower_wt_percent":34.0,"upper_wt_percent":34.0},
            "Fe":{"lower_wt_percent":34.0,"upper_wt_percent":34.0}
        });
        spec["components"][0]["targets"][0]["element_activity_bq_per_g"] =
            json!({"Si":{},"Nb":{},"Fe":{}});
        let error = run_doc(&serde_json::to_string(&spec).unwrap(), None).unwrap_err();
        assert!(error.contains("100 wt-percent"));
    }

    #[test]
    fn invalid_later_component_does_not_replace_existing_output() {
        let scratch = Scratch::new();
        let output = scratch.0.join("result.json");
        std::fs::write(&output, b"sentinel").unwrap();
        let mut spec = base_spec();
        let mut second = spec["components"][0].clone();
        second["id"] = json!("later");
        second["composition_wt_percent_bounds"]["Fe"]["lower_wt_percent"] = json!(90.0);
        second["composition_wt_percent_bounds"]["Fe"]["upper_wt_percent"] = json!(100.0);
        spec["components"].as_array_mut().unwrap().push(second);
        assert!(run_doc(&serde_json::to_string(&spec).unwrap(), output.to_str()).is_err());
        assert_eq!(std::fs::read(output).unwrap(), b"sentinel");
    }
}
