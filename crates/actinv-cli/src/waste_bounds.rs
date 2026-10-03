//! Conservative classification over user-declared whole-component activity boxes.
//!
//! Bounds are deterministic inputs. This command does not infer probability,
//! covariance, upstream completeness, or activation/decay evolution.

use actinv_core::waste::{
    normalize_nuclide_key, Coverage, NuclideProperties, RulePack, WasteGeometry, WasteType,
};
use actinv_core::waste_bounds::ActivityInterval;
use serde::de::{self, MapAccess, Visitor};
use serde::{Deserialize, Deserializer};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::fmt;
use std::marker::PhantomData;

const RULE_ID: &str = "us-nrc-10cfr61.55-v1";
const RULE_SHA256: &str = "890268af81a53815b8e11c238e4a1694eabb79664a1ca087fd7fe22b9fabe5d7";

#[derive(Clone, Copy, Debug, Deserialize, serde::Serialize)]
#[serde(rename_all = "snake_case")]
enum InventoryCoverage {
    Complete,
    Incomplete,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct BoundsSpec {
    schema: String,
    rules: String,
    components: Vec<ComponentSpec>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ComponentSpec {
    id: String,
    mass_g: f64,
    #[serde(default, deserialize_with = "deserialize_present_f64")]
    displaced_volume_cm3: Option<f64>,
    #[serde(default, deserialize_with = "deserialize_present_f64")]
    density_g_cm3: Option<f64>,
    waste_type: WasteType,
    #[serde(deserialize_with = "deserialize_unique_map")]
    nuclide_properties: BTreeMap<String, NuclideProperties>,
    external_tritium: ExternalTritium,
    targets: Vec<TargetSpec>,
}

fn deserialize_present_f64<'de, D>(deserializer: D) -> Result<Option<f64>, D::Error>
where
    D: Deserializer<'de>,
{
    f64::deserialize(deserializer).map(Some)
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct TargetSpec {
    step: u64,
    t_s: f64,
    #[serde(deserialize_with = "deserialize_unique_map")]
    activity_bounds_bq: BTreeMap<String, ActivityInterval>,
    inventory_coverage: InventoryCoverage,
    unbounded_inventory_reasons: Vec<String>,
    bounds_source: String,
    bounds_assumptions: String,
}

#[derive(Debug, Deserialize, serde::Serialize)]
#[serde(tag = "status", rename_all = "snake_case", deny_unknown_fields)]
enum ExternalTritium {
    NotApplicable,
    Required,
    Bounded {
        source: String,
        excludes_activation: bool,
        #[serde(deserialize_with = "deserialize_unique_map")]
        activity_bounds_bq: BTreeMap<String, ActivityInterval>,
    },
}

fn deserialize_unique_map<'de, D, V>(deserializer: D) -> Result<BTreeMap<String, V>, D::Error>
where
    D: Deserializer<'de>,
    V: Deserialize<'de>,
{
    struct UniqueMapVisitor<V>(PhantomData<V>);
    impl<'de, V> Visitor<'de> for UniqueMapVisitor<V>
    where
        V: Deserialize<'de>,
    {
        type Value = BTreeMap<String, V>;

        fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
            formatter.write_str("an object with unique string keys")
        }

        fn visit_map<M>(self, mut map: M) -> Result<Self::Value, M::Error>
        where
            M: MapAccess<'de>,
        {
            let mut values = BTreeMap::new();
            while let Some((key, value)) = map.next_entry::<String, V>()? {
                if values.insert(key.clone(), value).is_some() {
                    return Err(de::Error::custom(format!("duplicate map key '{key}'")));
                }
            }
            Ok(values)
        }
    }
    deserializer.deserialize_map(UniqueMapVisitor(PhantomData))
}

fn sha256(text: &[u8]) -> String {
    Sha256::digest(text)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

fn normalize_unique<'a, V>(
    values: &'a BTreeMap<String, V>,
    label: &str,
) -> Result<BTreeMap<String, &'a V>, String> {
    let mut normalized = BTreeMap::new();
    for (raw, value) in values {
        let key = normalize_nuclide_key(raw)?;
        if normalized.insert(key.clone(), value).is_some() {
            return Err(format!(
                "duplicate {label} keys after identity normalization: '{key}'"
            ));
        }
    }
    Ok(normalized)
}

fn canonical_properties(
    properties: &BTreeMap<String, NuclideProperties>,
) -> Result<BTreeMap<String, NuclideProperties>, String> {
    Ok(normalize_unique(properties, "nuclide property")?
        .into_iter()
        .map(|(key, value)| (key, *value))
        .collect())
}

fn normalize_bounds(
    bounds: &BTreeMap<String, ActivityInterval>,
    label: &str,
) -> Result<BTreeMap<String, ActivityInterval>, String> {
    let mut normalized = BTreeMap::new();
    for (key, interval) in normalize_unique(bounds, label)? {
        validate_interval(interval, label, &key)?;
        normalized.insert(key, *interval);
    }
    Ok(normalized)
}

fn validate_interval(interval: &ActivityInterval, label: &str, key: &str) -> Result<(), String> {
    if !interval.lower_bq.is_finite()
        || !interval.upper_bq.is_finite()
        || interval.lower_bq < 0.0
        || interval.lower_bq > interval.upper_bq
    {
        return Err(format!(
            "{label} interval for '{key}' must be finite with 0 <= lower <= upper"
        ));
    }
    Ok(())
}

fn canonical_step_bounds(
    bounds: &BTreeMap<String, ActivityInterval>,
    label: &str,
) -> Result<BTreeMap<u64, ActivityInterval>, String> {
    let mut canonical = BTreeMap::new();
    for (raw_step, interval) in bounds {
        let step = raw_step.parse::<u64>().map_err(|_| {
            format!("{label} key '{raw_step}' must be a canonical decimal target step")
        })?;
        if step == 0 || step.to_string() != *raw_step {
            return Err(format!(
                "{label} key '{raw_step}' must be a canonical positive decimal target step"
            ));
        }
        validate_interval(interval, label, raw_step)?;
        if canonical.insert(step, *interval).is_some() {
            return Err(format!("duplicate {label} target step '{step}'"));
        }
    }
    Ok(canonical)
}

fn component_geometry(component: &ComponentSpec) -> Result<WasteGeometry, String> {
    if !component.mass_g.is_finite() || component.mass_g <= 0.0 {
        return Err(format!(
            "component '{}' mass_g must be positive and finite",
            component.id
        ));
    }
    let displaced_volume_cm3 = match (component.displaced_volume_cm3, component.density_g_cm3) {
        (Some(volume), None) if volume.is_finite() && volume > 0.0 => volume,
        (None, Some(density)) if density.is_finite() && density > 0.0 => component.mass_g / density,
        (Some(_), Some(_)) => {
            return Err(format!(
                "component '{}' cannot set both displaced_volume_cm3 and density_g_cm3",
                component.id
            ))
        }
        (None, None) => {
            return Err(format!(
                "component '{}' needs displaced_volume_cm3 or density_g_cm3",
                component.id
            ))
        }
        (Some(_), None) => {
            return Err(format!(
                "component '{}' displaced_volume_cm3 must be positive and finite",
                component.id
            ))
        }
        (None, Some(_)) => {
            return Err(format!(
                "component '{}' density_g_cm3 must be positive and finite",
                component.id
            ))
        }
    };
    if !displaced_volume_cm3.is_finite() || displaced_volume_cm3 <= 0.0 {
        return Err(format!(
            "component '{}' derived volume must be positive and finite",
            component.id
        ));
    }
    Ok(WasteGeometry {
        mass_g: component.mass_g,
        displaced_volume_cm3,
    })
}

fn merge_external_h3(
    activation: &BTreeMap<String, ActivityInterval>,
    external: Option<ActivityInterval>,
) -> Result<BTreeMap<String, ActivityInterval>, String> {
    let mut merged = activation.clone();
    if let Some(additional) = external {
        let h3 = normalize_nuclide_key("H3")?;
        let interval = merged.entry(h3).or_insert(ActivityInterval {
            lower_bq: 0.0,
            upper_bq: 0.0,
        });
        interval.lower_bq += additional.lower_bq;
        interval.upper_bq += additional.upper_bq;
        if !interval.lower_bq.is_finite() || !interval.upper_bq.is_finite() {
            return Err("combined H-3 activity bounds overflowed".into());
        }
    }
    Ok(merged)
}

fn validate_spec(spec: &BoundsSpec) -> Result<(), String> {
    if spec.schema != "actinv-waste-bounds-spec-1" {
        return Err(format!("unsupported waste bounds schema '{}'", spec.schema));
    }
    if spec.rules != RULE_ID {
        return Err(format!(
            "waste bounds CLI supports only bundled rule pack '{RULE_ID}'"
        ));
    }
    if spec.components.is_empty() {
        return Err("components must contain at least one component".into());
    }
    let mut component_ids = BTreeSet::new();
    for component in &spec.components {
        if component.id.trim().is_empty() || !component_ids.insert(&component.id) {
            return Err(format!(
                "component id '{}' is empty or duplicated",
                component.id
            ));
        }
        component_geometry(component)?;
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
                    "component '{}' target t_s must be finite and nonnegative",
                    component.id
                ));
            }
            normalize_bounds(&target.activity_bounds_bq, "activity")?;
            let complete = matches!(&target.inventory_coverage, InventoryCoverage::Complete);
            if complete && !target.unbounded_inventory_reasons.is_empty() {
                return Err(format!("complete coverage at step {} requires an empty unbounded_inventory_reasons list", target.step));
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
                    "target step {} needs nonempty bounds_source and bounds_assumptions",
                    target.step
                ));
            }
            match &component.external_tritium {
                ExternalTritium::NotApplicable | ExternalTritium::Required => {}
                ExternalTritium::Bounded {
                    source,
                    excludes_activation,
                    activity_bounds_bq,
                } => {
                    if source.trim().is_empty() || !excludes_activation {
                        return Err(format!("component '{}' bounded external_tritium requires source and excludes_activation:true", component.id));
                    }
                    let raw_step = target.step.to_string();
                    if !activity_bounds_bq.contains_key(&raw_step) {
                        return Err(format!(
                            "external_tritium is missing canonical target step '{raw_step}'"
                        ));
                    }
                }
            }
        }
        if let ExternalTritium::Bounded {
            activity_bounds_bq, ..
        } = &component.external_tritium
        {
            if activity_bounds_bq.len() != component.targets.len()
                || component
                    .targets
                    .iter()
                    .any(|target| !activity_bounds_bq.contains_key(&target.step.to_string()))
                || activity_bounds_bq.keys().any(|step| {
                    step.parse::<u64>().ok().map(|n| n.to_string()) != Some(step.clone())
                })
            {
                return Err(format!("component '{}' external_tritium bounds must exactly match selected canonical decimal target steps", component.id));
            }
            canonical_step_bounds(activity_bounds_bq, "external H-3 activity")?;
        }
    }
    Ok(())
}

pub fn run(path: &str, output: Option<&str>) -> Result<Value, String> {
    let text = std::fs::read_to_string(path)
        .map_err(|error| format!("cannot read waste bounds spec {path}: {error}"))?;
    run_doc(&text, output)
}

fn run_doc(text: &str, output: Option<&str>) -> Result<Value, String> {
    let spec: BoundsSpec = serde_json::from_str(text)
        .map_err(|error| format!("cannot parse waste bounds spec: {error}"))?;
    validate_spec(&spec)?;
    let rules = RulePack::bundled()?;
    if rules.id != RULE_ID || rules.sha256 != RULE_SHA256 {
        return Err(
            "bundled waste rule pack identity or SHA-256 does not match the P106-qualified pack"
                .into(),
        );
    }
    let mut components_out = Vec::with_capacity(spec.components.len());
    for component in &spec.components {
        let geometry = component_geometry(component)?;
        let properties = canonical_properties(&component.nuclide_properties)?;
        let mut targets_out = Vec::with_capacity(component.targets.len());
        for target in &component.targets {
            let activation = normalize_bounds(&target.activity_bounds_bq, "activity")?;
            let external = match &component.external_tritium {
                ExternalTritium::Bounded {
                    activity_bounds_bq, ..
                } => {
                    let step_key = target.step.to_string();
                    Some(*activity_bounds_bq.get(&step_key).ok_or_else(|| {
                        format!("external_tritium is missing target step '{step_key}'")
                    })?)
                }
                _ => None,
            };
            let merged = merge_external_h3(&activation, external)?;
            let coverage = if matches!(&target.inventory_coverage, InventoryCoverage::Incomplete)
                || matches!(&component.external_tritium, ExternalTritium::Required)
            {
                Coverage::Incomplete
            } else {
                Coverage::Complete
            };
            let evaluation = actinv_core::waste_bounds::evaluate_component_bounds(
                &rules,
                component.waste_type,
                geometry,
                &merged,
                &properties,
                coverage,
            )?;
            let eval = serde_json::to_value(&evaluation).map_err(|error| error.to_string())?;
            let mut coverage_reasons = target.unbounded_inventory_reasons.clone();
            if matches!(&component.external_tritium, ExternalTritium::Required) {
                coverage_reasons
                    .push("required external H-3 activity bounds were not supplied".into());
            }
            if let Some(unknown) = eval
                .get("upper")
                .and_then(|endpoint| endpoint.get("unknown_nuclides"))
                .and_then(Value::as_array)
            {
                for nuclide in unknown.iter().filter_map(Value::as_str) {
                    coverage_reasons.push(format!(
                        "positive upper activity for {nuclide} lacks nuclide properties"
                    ));
                }
            }
            let activation_json =
                serde_json::to_value(&activation).map_err(|error| error.to_string())?;
            let external_json = external
                .map(serde_json::to_value)
                .transpose()
                .map_err(|error| error.to_string())?
                .unwrap_or(Value::Null);
            let merged_json = serde_json::to_value(&merged).map_err(|error| error.to_string())?;
            targets_out.push(json!({
                "step": target.step,
                "t_s": target.t_s,
                "inventory_coverage": target.inventory_coverage,
                "coverage": eval.get("coverage").cloned().unwrap_or(Value::Null),
                "unbounded_inventory_reasons": target.unbounded_inventory_reasons,
                "coverage_reasons": coverage_reasons,
                "bounds_source": target.bounds_source,
                "bounds_assumptions": target.bounds_assumptions,
                "activation_activity_bounds_bq": activation_json,
                "external_tritium_activity_bounds_bq": external_json,
                "merged_activity_bounds_bq": merged_json,
                "lower": eval.get("lower").cloned().unwrap_or(Value::Null),
                "upper": eval.get("upper").cloned().unwrap_or(Value::Null),
                "row_fraction_ranges": eval.get("row_fraction_ranges").cloned().unwrap_or(Value::Null),
                "constraint_ranges": eval.get("constraint_ranges").cloned().unwrap_or(Value::Null),
                "class_envelope": eval.get("class_envelope").cloned().unwrap_or(Value::Null),
                "conservative_superset": eval.get("conservative_superset").cloned().unwrap_or(Value::Null),
                "class_is_stable": eval.get("class_is_stable").cloned().unwrap_or(Value::Null),
                "stable_class": eval.get("stable_class").cloned().unwrap_or(Value::Null),
            }));
        }
        let ext_decl =
            serde_json::to_value(&component.external_tritium).map_err(|error| error.to_string())?;
        components_out.push(json!({
            "id": component.id,
            "mass_g": component.mass_g,
            "displaced_volume_cm3": component.displaced_volume_cm3,
            "density_g_cm3": component.density_g_cm3,
            "geometry": geometry,
            "waste_type": component.waste_type,
            "nuclide_properties": properties,
            "external_tritium": ext_decl,
            "targets": targets_out,
        }));
    }
    let result = json!({
        "schema": "actinv-waste-bounds-result-1",
        "method": "declared_activity_box",
        "input_sha256": sha256(text.as_bytes()),
        "rules": {"id": rules.id, "version": rules.version, "source_url": rules.source_url,
            "source_as_of": rules.source_as_of, "sha256": rules.sha256},
        "components": components_out,
    });
    crate::waste::output_doc(&result, output)?;
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicUsize, Ordering};

    struct Scratch(PathBuf);

    impl Scratch {
        fn new() -> Self {
            static NEXT: AtomicUsize = AtomicUsize::new(0);
            let path = std::env::temp_dir().join(format!(
                "actinv-waste-bounds-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            std::fs::create_dir_all(&path).unwrap();
            Self(path)
        }
    }

    impl Drop for Scratch {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    fn component(target: Value, properties: Value, external: Value) -> Value {
        json!({
            "id":"fixture", "mass_g":1.0, "displaced_volume_cm3":1.0,
            "waste_type":"general", "nuclide_properties":properties,
            "external_tritium":external, "targets":[target]
        })
    }

    fn target(bounds: Value, coverage: &str, reasons: Value) -> Value {
        json!({
            "step":1, "t_s":0.0, "activity_bounds_bq":bounds,
            "inventory_coverage":coverage, "unbounded_inventory_reasons":reasons,
            "bounds_source":"artificial fixture", "bounds_assumptions":"declared rectangular enclosure"
        })
    }

    fn spec(components: Vec<Value>) -> Value {
        json!({"schema":"actinv-waste-bounds-spec-1", "rules":RULE_ID, "components":components})
    }

    fn run_value(value: Value) -> Result<Value, String> {
        run_doc(&serde_json::to_string(&value).unwrap(), None)
    }

    #[test]
    fn duplicate_json_map_keys_and_canonical_activity_aliases_are_refused() {
        let duplicate = r#"{"schema":"actinv-waste-bounds-spec-1","rules":"us-nrc-10cfr61.55-v1","components":[{"id":"x","mass_g":1,"displaced_volume_cm3":1,"waste_type":"general","nuclide_properties":{},"external_tritium":{"status":"not_applicable"},"targets":[{"step":1,"t_s":0,"activity_bounds_bq":{"C14":{"lower_bq":0,"upper_bq":1},"C14":{"lower_bq":0,"upper_bq":2}},"inventory_coverage":"complete","unbounded_inventory_reasons":[],"bounds_source":"x","bounds_assumptions":"x"}]}]}"#;
        assert!(serde_json::from_str::<BoundsSpec>(duplicate)
            .unwrap_err()
            .to_string()
            .contains("duplicate map key"));

        let aliases = spec(vec![component(
            target(
                json!({
                    "C14":{"lower_bq":0,"upper_bq":1},
                    "C-14":{"lower_bq":0,"upper_bq":2}
                }),
                "complete",
                json!([]),
            ),
            json!({"C14":{"z":6,"half_life_s":315576000,"alpha_emitting":false}}),
            json!({"status":"not_applicable"}),
        )]);
        assert!(run_value(aliases)
            .unwrap_err()
            .contains("duplicate activity keys after identity normalization"));
    }

    #[test]
    fn bounded_external_h3_merges_lower_and_upper_once_with_activation() {
        let value = spec(vec![component(
            target(
                json!({"H3":{"lower_bq":1.0,"upper_bq":2.0}}),
                "complete",
                json!([]),
            ),
            json!({"H-3":{"z":1,"half_life_s":315576000,"alpha_emitting":false}}),
            json!({"status":"bounded","source":"artificial retention box","excludes_activation":true,
                "activity_bounds_bq":{"1":{"lower_bq":3.0,"upper_bq":5.0}}}),
        )]);
        let result = run_value(value).unwrap();
        let t = &result["components"][0]["targets"][0];
        assert_eq!(t["activation_activity_bounds_bq"]["H3"]["lower_bq"], 1.0);
        assert_eq!(t["external_tritium_activity_bounds_bq"]["lower_bq"], 3.0);
        assert_eq!(t["merged_activity_bounds_bq"]["H3"]["lower_bq"], 4.0);
        assert_eq!(t["merged_activity_bounds_bq"]["H3"]["upper_bq"], 7.0);
    }

    #[test]
    fn missing_positive_upper_properties_and_incomplete_coverage_are_unknown() {
        let no_properties = spec(vec![component(
            target(
                json!({"C14":{"lower_bq":0.0,"upper_bq":10.0}}),
                "complete",
                json!([]),
            ),
            json!({}),
            json!({"status":"not_applicable"}),
        )]);
        let result = run_value(no_properties).unwrap();
        let t = &result["components"][0]["targets"][0];
        assert_eq!(t["class_envelope"], json!(["unknown"]));
        assert_eq!(t["class_is_stable"], false);
        assert_eq!(t["stable_class"], Value::Null);
        assert_eq!(t["upper"]["class"], "unknown");
        assert_eq!(t["upper"]["calculated_only_class"], "A");

        let incomplete = spec(vec![component(
            target(
                json!({}),
                "incomplete",
                json!(["unbounded source nuclides"]),
            ),
            json!({}),
            json!({"status":"not_applicable"}),
        )]);
        assert_eq!(
            run_value(incomplete).unwrap()["components"][0]["targets"][0]["conservative_superset"],
            Value::Null
        );
    }

    #[test]
    fn required_external_h3_forces_unknown_even_when_inventory_is_declared_complete() {
        let value = spec(vec![component(
            target(json!({}), "complete", json!([])),
            json!({}),
            json!({"status":"required"}),
        )]);
        let result = run_value(value).unwrap();
        let target = &result["components"][0]["targets"][0];
        assert_eq!(target["inventory_coverage"], "complete");
        assert_eq!(target["coverage"], "incomplete");
        assert_eq!(target["class_envelope"], json!(["unknown"]));
        assert!(target["coverage_reasons"][0]
            .as_str()
            .unwrap()
            .contains("required external H-3"));
    }

    #[test]
    fn invalid_later_component_does_not_publish_partial_output() {
        let scratch = Scratch::new();
        let destination = scratch.0.join("result.json");
        std::fs::write(&destination, "sentinel\n").unwrap();
        let good = component(
            target(json!({}), "complete", json!([])),
            json!({}),
            json!({"status":"not_applicable"}),
        );
        let mut bad = good.clone();
        bad["id"] = json!("later-invalid");
        bad["nuclide_properties"] =
            json!({"C14":{"z":7,"half_life_s":315576000,"alpha_emitting":false}});
        let text = serde_json::to_string(&spec(vec![good, bad])).unwrap();
        assert!(run_doc(&text, Some(destination.to_str().unwrap()))
            .unwrap_err()
            .contains("does not match"));
        assert_eq!(std::fs::read_to_string(destination).unwrap(), "sentinel\n");
    }

    #[test]
    fn rejects_nonbundled_pack_and_null_geometry_presence() {
        let good = component(
            target(json!({}), "complete", json!([])),
            json!({}),
            json!({"status":"not_applicable"}),
        );
        let mut wrong_pack = spec(vec![good.clone()]);
        wrong_pack["rules"] = json!("custom-pack.json");
        assert!(run_value(wrong_pack)
            .unwrap_err()
            .contains("supports only bundled rule pack"));

        let mut null_geometry = good;
        null_geometry["density_g_cm3"] = Value::Null;
        let raw = serde_json::to_string(&spec(vec![null_geometry])).unwrap();
        assert!(serde_json::from_str::<BoundsSpec>(&raw).is_err());
    }
}
