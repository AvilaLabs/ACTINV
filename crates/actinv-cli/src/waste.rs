//! `actinv waste` — nominal single-component 10 CFR 61.55 classification.

use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

use actinv_core::waste::{
    evaluate_component, NuclideProperties, RulePack, WasteGeometry, WasteType,
};
use serde::Deserialize;
use serde_json::{json, Value};

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct WasteSpec {
    schema: String,
    rules: String,
    input: String,
    targets: Vec<u64>,
    nuclide_properties: BTreeMap<String, NuclideProperties>,
    components: Vec<ComponentSpec>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct ComponentSpec {
    pub(crate) id: String,
    pub(crate) mass_g: f64,
    #[serde(default)]
    pub(crate) displaced_volume_cm3: Option<f64>,
    #[serde(default)]
    pub(crate) density_g_cm3: Option<f64>,
    pub(crate) waste_type: WasteType,
    #[serde(default)]
    pub(crate) cells: Vec<ComponentCell>,
    pub(crate) external_tritium: ExternalTritium,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct ComponentCell {
    pub(crate) id: String,
    pub(crate) mass_g: f64,
}

#[derive(Debug, Deserialize)]
#[serde(tag = "status", rename_all = "snake_case", deny_unknown_fields)]
pub(crate) enum ExternalTritium {
    NotApplicable,
    Declared {
        source: String,
        excludes_activation: bool,
        activity_bq: BTreeMap<String, f64>,
    },
    Required,
}

#[derive(Clone)]
pub(crate) struct CellResult {
    pub(crate) steps: BTreeMap<u64, Value>,
}

pub(crate) struct InputResults {
    pub(crate) kind: &'static str,
    pub(crate) cells: BTreeMap<String, CellResult>,
    pub(crate) source_sha256: String,
}

fn sha256(bytes: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    Sha256::digest(bytes)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

fn one_based_targets(targets: &[u64]) -> Result<(), String> {
    if targets.is_empty() || targets.contains(&0) {
        return Err("targets must contain positive one-based step numbers".into());
    }
    let unique: BTreeSet<_> = targets.iter().copied().collect();
    if unique.len() != targets.len() {
        return Err("targets must not contain duplicate step numbers".into());
    }
    Ok(())
}

fn waste_geometry(component: &ComponentSpec) -> Result<WasteGeometry, String> {
    if !(component.mass_g.is_finite() && component.mass_g > 0.0) {
        return Err(format!(
            "component {} mass_g must be finite and positive",
            component.id
        ));
    }
    let volume = match (component.displaced_volume_cm3, component.density_g_cm3) {
        (Some(v), None) if v.is_finite() && v > 0.0 => v,
        (None, Some(d)) if d.is_finite() && d > 0.0 => component.mass_g / d,
        (Some(_), Some(_)) => {
            return Err(format!(
                "component {} declares both displaced_volume_cm3 and density_g_cm3",
                component.id
            ))
        }
        (None, None) => {
            return Err(format!(
                "component {} needs displaced_volume_cm3 or density_g_cm3",
                component.id
            ))
        }
        (Some(_), None) => {
            return Err(format!(
                "component {} displaced_volume_cm3 must be finite and positive",
                component.id
            ))
        }
        (None, Some(_)) => {
            return Err(format!(
                "component {} density_g_cm3 must be finite and positive",
                component.id
            ))
        }
    };
    if !(volume.is_finite() && volume > 0.0) {
        return Err(format!(
            "component {} derived displaced volume is not finite and positive",
            component.id
        ));
    }
    Ok(WasteGeometry {
        mass_g: component.mass_g,
        displaced_volume_cm3: volume,
    })
}

fn parse_steps(result: &Value, where_: &str) -> Result<BTreeMap<u64, Value>, String> {
    if result.get("schema").is_some() {
        return Err(format!(
            "{where_}: native run results have no schema field; unsupported explicit schema"
        ));
    }
    if !result.get("entry_point").and_then(Value::as_str).is_some()
        || !result.get("mode").and_then(Value::as_str).is_some()
    {
        return Err(format!(
            "{where_}: not an ACTINV RunResult (entry_point/mode missing)"
        ));
    }
    let rows = result
        .get("steps")
        .and_then(Value::as_array)
        .ok_or_else(|| format!("{where_}: missing steps array"))?;
    let mut steps = BTreeMap::new();
    for row in rows {
        let step = row
            .get("step")
            .and_then(Value::as_u64)
            .filter(|step| *step > 0)
            .ok_or_else(|| format!("{where_}: invalid step number"))?;
        if row
            .get("t_s")
            .and_then(Value::as_f64)
            .is_none_or(|t| !t.is_finite() || t < 0.0)
        {
            return Err(format!("{where_}: step {step} has invalid t_s"));
        }
        let act = row
            .get("activity_Bq_per_g")
            .and_then(Value::as_object)
            .ok_or_else(|| format!("{where_}: step {step} lacks activity_Bq_per_g"))?;
        for (name, value) in act {
            let a = value
                .as_f64()
                .ok_or_else(|| format!("{where_}: activity for {name} is not numeric"))?;
            if !a.is_finite() || a < 0.0 {
                return Err(format!(
                    "{where_}: activity for {name} must be finite and nonnegative"
                ));
            }
        }
        if steps.insert(step, row.clone()).is_some() {
            return Err(format!("{where_}: duplicate step {step}"));
        }
    }
    Ok(steps)
}

fn load_input(path: &Path) -> Result<InputResults, String> {
    let bytes = std::fs::read(path)
        .map_err(|error| format!("cannot read input {}: {error}", path.display()))?;
    let digest = sha256(&bytes);
    if let Ok(value) = serde_json::from_slice::<Value>(&bytes) {
        let steps = parse_steps(&value, "input")?;
        return Ok(InputResults {
            kind: "run",
            cells: BTreeMap::from([("__single__".into(), CellResult { steps })]),
            source_sha256: digest,
        });
    }
    let text =
        std::str::from_utf8(&bytes).map_err(|error| format!("mesh input is not UTF-8: {error}"))?;
    let mut header = false;
    let mut header_count = None;
    let mut footer_count = None;
    let mut cells = BTreeMap::new();
    for (index, line) in text.lines().enumerate() {
        if line.trim().is_empty() {
            continue;
        }
        let row: Value = serde_json::from_str(line)
            .map_err(|error| format!("mesh line {}: {error}", index + 1))?;
        match row.get("record").and_then(Value::as_str) {
            Some("header") => {
                if header
                    || row.get("schema").and_then(Value::as_str) != Some("actinv-mesh-result-1")
                {
                    return Err("mesh input has duplicate or unsupported header".into());
                }
                header = true;
                header_count = row.get("cell_count").and_then(Value::as_u64);
            }
            Some("cell") if header && footer_count.is_none() => {
                let id = row
                    .get("id")
                    .and_then(Value::as_str)
                    .ok_or("mesh cell has no id")?
                    .to_string();
                if id.is_empty() {
                    return Err("mesh cell id must not be empty".into());
                }
                let steps = parse_steps(
                    row.get("result").ok_or("mesh cell has no result")?,
                    &format!("cell {id}"),
                )?;
                if cells.insert(id.clone(), CellResult { steps }).is_some() {
                    return Err(format!("mesh input repeats cell id {id}"));
                }
            }
            Some("footer") if header && footer_count.is_none() => {
                footer_count = Some(
                    row.get("cell_count")
                        .and_then(Value::as_u64)
                        .ok_or("mesh footer has no cell_count")?,
                );
            }
            _ => {
                return Err(format!(
                    "mesh line {} has an invalid record/order",
                    index + 1
                ))
            }
        }
    }
    if !header {
        return Err("input is neither a native run result nor a mesh result".into());
    }
    if footer_count != Some(cells.len() as u64) || header_count != footer_count {
        return Err(format!(
            "mesh footer cell_count {:?} does not match {} cell records",
            footer_count,
            cells.len()
        ));
    }
    Ok(InputResults {
        kind: "mesh",
        cells,
        source_sha256: digest,
    })
}

fn selected_rules(rules: &str, base: &Path) -> Result<RulePack, String> {
    if rules == "us-nrc-10cfr61.55-v1" {
        RulePack::bundled()
    } else {
        let path = base.join(rules);
        let text = std::fs::read_to_string(&path)
            .map_err(|error| format!("cannot read rule pack {}: {error}", path.display()))?;
        RulePack::parse(&text)
    }
}

type ComponentActivities = BTreeMap<u64, (f64, BTreeMap<String, f64>)>;

fn activity_for_component(
    input: &InputResults,
    component: &ComponentSpec,
    targets: &[u64],
) -> Result<ComponentActivities, String> {
    let mut out = BTreeMap::new();
    if input.kind == "run" {
        if !component.cells.is_empty() {
            return Err(format!(
                "native run component {} must not declare cells",
                component.id
            ));
        }
        if component.id.is_empty() {
            return Err("component id must not be empty".into());
        }
        let cell = input
            .cells
            .get("__single__")
            .expect("single result inserted");
        for &step in targets {
            let row = cell.steps.get(&step).ok_or_else(|| {
                format!(
                    "component {} input has no selected step {step}",
                    component.id
                )
            })?;
            let mut activities = BTreeMap::new();
            for (name, value) in row["activity_Bq_per_g"].as_object().unwrap() {
                let bq = value.as_f64().unwrap() * component.mass_g;
                activities.insert(name.clone(), bq);
            }
            let t = row["t_s"].as_f64().unwrap();
            out.insert(step, (t, activities));
        }
        return Ok(out);
    }
    if component.cells.is_empty() {
        return Err(format!(
            "mesh component {} must declare cells",
            component.id
        ));
    }
    let mut seen = BTreeSet::new();
    let mut mass_sum = 0.0;
    for cell in &component.cells {
        if !seen.insert(cell.id.as_str()) {
            return Err(format!(
                "component {} repeats cell {}",
                component.id, cell.id
            ));
        }
        if !(cell.mass_g.is_finite() && cell.mass_g > 0.0) {
            return Err(format!(
                "cell {} mass_g must be finite and positive",
                cell.id
            ));
        }
        mass_sum += cell.mass_g;
        if !mass_sum.is_finite() {
            return Err(format!(
                "component {} cell-mass sum overflowed",
                component.id
            ));
        }
        if !input.cells.contains_key(&cell.id) {
            return Err(format!(
                "component {} references missing mesh cell {}",
                component.id, cell.id
            ));
        }
    }
    let rel = (mass_sum - component.mass_g).abs() / component.mass_g;
    if rel > 1e-9 {
        return Err(format!(
            "component {} cell masses do not close to component mass (relative error {rel})",
            component.id
        ));
    }
    for &step in targets {
        let mut aggregate = BTreeMap::<String, f64>::new();
        let mut time = None;
        for cell in &component.cells {
            let result = &input.cells[&cell.id];
            let row = result
                .steps
                .get(&step)
                .ok_or_else(|| format!("mesh cell {} has no selected step {step}", cell.id))?;
            let t = row["t_s"].as_f64().unwrap();
            if time.is_some_and(|prior: f64| prior != t) {
                return Err(format!(
                    "component {} cells have unequal timestamps at step {step}",
                    component.id
                ));
            }
            time = Some(t);
            for (name, value) in row["activity_Bq_per_g"].as_object().unwrap() {
                *aggregate.entry(name.clone()).or_default() +=
                    value.as_f64().unwrap() * cell.mass_g;
            }
        }
        out.insert(
            step,
            (time.ok_or("mesh component has no cells")?, aggregate),
        );
    }
    Ok(out)
}

pub(crate) fn load_rule_pack(rules: &str, base: &Path) -> Result<RulePack, String> {
    selected_rules(rules, base)
}

pub fn run(path: &str, output: Option<&str>) -> Result<Value, String> {
    let source = Path::new(path);
    let base = source.parent().unwrap_or_else(|| Path::new("."));
    let text = std::fs::read_to_string(source)
        .map_err(|error| format!("cannot read waste spec {path}: {error}"))?;
    run_doc(&text, base, output)
}

pub(crate) fn run_doc(text: &str, base: &Path, output: Option<&str>) -> Result<Value, String> {
    let spec: WasteSpec =
        serde_json::from_str(text).map_err(|error| format!("cannot parse waste spec: {error}"))?;
    if spec.schema != "actinv-waste-spec-1" {
        return Err(format!("unsupported waste schema '{}'", spec.schema));
    }
    one_based_targets(&spec.targets)?;
    if spec.components.is_empty() {
        return Err("components must contain at least one component".into());
    }
    let rules = selected_rules(&spec.rules, base)?;
    let input_path = base.join(&spec.input);
    let input = load_input(&input_path)?;
    let mut global_cells = BTreeSet::new();
    let mut results = Vec::new();
    let mut component_ids = BTreeSet::new();
    for component in &spec.components {
        if component.id.is_empty() || !component_ids.insert(component.id.as_str()) {
            return Err(format!(
                "component id '{}' is empty or duplicated",
                component.id
            ));
        }
    }
    if input.kind == "run" && (spec.components.len() != 1 || !spec.components[0].cells.is_empty()) {
        return Err("native run input requires exactly one component and no cells".into());
    }
    let mut all_nominal = true;
    for component in &spec.components {
        let geometry = waste_geometry(component)?;
        for cell in &component.cells {
            if !global_cells.insert(cell.id.as_str()) {
                return Err(format!(
                    "mesh cell {} overlaps component membership",
                    cell.id
                ));
            }
        }
        let activity = activity_for_component(&input, component, &spec.targets)?;
        let mut target_results = Vec::new();
        let mut component_nominal = true;
        for &step in &spec.targets {
            let (t_s, mut activities) =
                activity.get(&step).expect("target validated above").clone();
            let calculated_only_activities = activities.clone();
            let external = match &component.external_tritium {
                ExternalTritium::NotApplicable => 0.0,
                ExternalTritium::Declared {
                    source,
                    excludes_activation,
                    activity_bq,
                } => {
                    if source.trim().is_empty() || !excludes_activation {
                        return Err(
                            "declared external_tritium needs a source and excludes_activation:true"
                                .into(),
                        );
                    }
                    let value = *activity_bq.get(&step.to_string()).ok_or_else(|| {
                        format!("external_tritium has no activity_bq for selected step {step}")
                    })?;
                    if !value.is_finite() || value < 0.0 {
                        return Err(format!(
                            "external H-3 activity at step {step} must be finite and nonnegative"
                        ));
                    }
                    let h3 = actinv_core::waste::normalize_nuclide_key("H3")?;
                    let matching = activities
                        .keys()
                        .find(|key| {
                            actinv_core::waste::normalize_nuclide_key(key)
                                .ok()
                                .as_deref()
                                == Some(h3.as_str())
                        })
                        .cloned();
                    if let Some(key) = matching {
                        let old = activities.remove(&key).unwrap_or(0.0);
                        let sum = old + value;
                        if !sum.is_finite() {
                            return Err(format!("combined H-3 activity at step {step} overflowed"));
                        }
                        activities.insert(h3, sum);
                    } else {
                        activities.insert(h3, value);
                    }
                    value
                }
                ExternalTritium::Required => 0.0,
            };
            let external_unknown = matches!(&component.external_tritium, ExternalTritium::Required);
            let calculated_only = evaluate_component(
                &rules,
                component.waste_type,
                geometry,
                &calculated_only_activities,
                &spec.nuclide_properties,
            )?;
            let evaluation = evaluate_component(
                &rules,
                component.waste_type,
                geometry,
                &activities,
                &spec.nuclide_properties,
            )?;
            let value = serde_json::to_value(&evaluation).map_err(|error| error.to_string())?;
            let class = value.get("class").cloned().unwrap_or(Value::Null);
            let coverage = value.get("coverage").cloned().unwrap_or(Value::Null);
            let target_unknown =
                external_unknown || evaluation.coverage != actinv_core::waste::Coverage::Complete;
            component_nominal &= !target_unknown;
            target_results.push(json!({
                "step": step,
                "t_s": t_s,
                "inventory_activity_bq": activities,
                "external_tritium_activity_bq": external,
                "calculated_only_class": calculated_only.class,
                "class": if target_unknown { json!("unknown") } else { class },
                "coverage": coverage,
                "external_tritium_status": external_status(&component.external_tritium),
                "status": if target_unknown { "unknown" } else { "nominal" },
                "unknown_reason": if external_unknown { json!("required external H-3 not declared") } else if evaluation.coverage != actinv_core::waste::Coverage::Complete { json!(evaluation.unknown_nuclides) } else { Value::Null },
                "evaluation": value,
            }));
        }
        results.push(json!({
            "id": component.id,
            "mass_g": geometry.mass_g,
            "displaced_volume_cm3": geometry.displaced_volume_cm3,
            "waste_type": component.waste_type,
            "external_tritium": {"status": external_status(&component.external_tritium)},
            "status": if component_nominal { "nominal" } else { "conditional" },
            "targets": target_results,
        }));
        all_nominal &= component_nominal;
    }
    let doc = json!({
        "schema": "actinv-waste-result-1",
        "waste_sha256": sha256(text.as_bytes()),
        "input": spec.input,
        "input_sha256": input.source_sha256,
        "input_kind": input.kind,
        "rules": spec.rules,
        "rule_pack": {"id": rules.id, "version": rules.version, "source_url": rules.source_url, "source_as_of": rules.source_as_of, "sha256": rules.sha256},
        "rule_pack_sha256": rules.sha256,
        "targets": spec.targets,
        "status": if all_nominal { "nominal" } else { "conditional" },
        "components": results,
    });
    write_output(&doc, output)?;
    Ok(doc)
}

fn write_output(value: &Value, output: Option<&str>) -> Result<(), String> {
    let pretty = serde_json::to_string_pretty(value).map_err(|error| error.to_string())?;
    match output {
        Some(path) => std::fs::write(path, format!("{pretty}\n"))
            .map_err(|error| format!("cannot write {path}: {error}")),
        None => {
            println!("{pretty}");
            Ok(())
        }
    }
}

pub(crate) fn output_doc(value: &Value, output: Option<&str>) -> Result<(), String> {
    write_output(value, output)
}

pub(crate) fn validate_targets(targets: &[u64]) -> Result<(), String> {
    one_based_targets(targets)
}

pub(crate) fn external_tritium_at(
    external: &ExternalTritium,
    step: u64,
) -> Result<(f64, bool), String> {
    match external {
        ExternalTritium::NotApplicable => Ok((0.0, false)),
        ExternalTritium::Declared {
            source,
            excludes_activation,
            activity_bq,
        } => {
            if source.trim().is_empty() || !*excludes_activation {
                return Err(
                    "declared external_tritium needs a source and excludes_activation:true".into(),
                );
            }
            let value = *activity_bq.get(&step.to_string()).ok_or_else(|| {
                format!("external_tritium has no activity_bq for selected step {step}")
            })?;
            if !value.is_finite() || value < 0.0 {
                return Err(format!(
                    "external H-3 activity at step {step} must be finite and nonnegative"
                ));
            }
            Ok((value, false))
        }
        ExternalTritium::Required => Ok((0.0, true)),
    }
}

pub(crate) fn external_status(external: &ExternalTritium) -> &'static str {
    match external {
        ExternalTritium::NotApplicable => "not_applicable",
        ExternalTritium::Declared { .. } => "declared",
        ExternalTritium::Required => "required",
    }
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
                "actinv-waste-cli-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            std::fs::create_dir_all(&path).unwrap();
            Self(path)
        }
        fn write(&self, name: &str, contents: &str) -> String {
            let path = self.0.join(name);
            std::fs::write(&path, contents).unwrap();
            path.to_string_lossy().into_owned()
        }
    }

    impl Drop for Scratch {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    fn native_run(activity: Value) -> Value {
        json!({"entry_point":"test","mode":"coupled","steps":[{"step":1,"t_s":10.0,"activity_Bq_per_g":activity}]})
    }

    fn component(external: Value) -> Value {
        json!({"id":"test-component","mass_g":2.5,"displaced_volume_cm3":1.0,"waste_type":"activated_metal","external_tritium":external})
    }

    fn spec(input: &str, component: Value, properties: Value) -> Value {
        json!({"schema":"actinv-waste-spec-1","rules":"us-nrc-10cfr61.55-v1","input":input,"targets":[1],"nuclide_properties":properties,"components":[component]})
    }

    fn run_with(scratch: &Scratch, spec: Value) -> Result<Value, String> {
        let text = serde_json::to_string(&spec).unwrap();
        run_doc(
            &text,
            &scratch.0,
            Some(scratch.0.join("out.json").to_str().unwrap()),
        )
    }

    #[test]
    fn rejects_invalid_schema_and_ambiguous_geometry() {
        let scratch = Scratch::new();
        scratch.write("run.json", &native_run(json!({})).to_string());
        let mut bad_schema = spec(
            "run.json",
            component(json!({"status":"not_applicable"})),
            json!({}),
        );
        bad_schema["schema"] = json!("actinv-waste-spec-0");
        assert!(run_with(&scratch, bad_schema)
            .unwrap_err()
            .contains("unsupported waste schema"));

        let mut bad_geometry = component(json!({"status":"not_applicable"}));
        bad_geometry["density_g_cm3"] = json!(7.8);
        assert!(
            run_with(&scratch, spec("run.json", bad_geometry, json!({})))
                .unwrap_err()
                .contains("both displaced_volume_cm3 and density_g_cm3")
        );
    }

    #[test]
    fn rejects_mesh_component_membership_for_missing_cell() {
        let scratch = Scratch::new();
        let mesh = concat!(
            "{\"record\":\"header\",\"schema\":\"actinv-mesh-result-1\",\"cell_count\":0}\n",
            "{\"record\":\"footer\",\"cell_count\":0}\n"
        );
        scratch.write("mesh.jsonl", mesh);
        let mut mesh_component = component(json!({"status":"not_applicable"}));
        mesh_component["cells"] = json!([{"id":"missing","mass_g":2.5}]);
        let error = run_with(&scratch, spec("mesh.jsonl", mesh_component, json!({}))).unwrap_err();
        assert!(error.contains("references missing mesh cell"));
    }

    #[test]
    fn required_external_tritium_keeps_nominal_class_unknown() {
        let scratch = Scratch::new();
        scratch.write("run.json", &native_run(json!({})).to_string());
        let result = run_with(
            &scratch,
            spec(
                "run.json",
                component(json!({"status":"required"})),
                json!({}),
            ),
        )
        .unwrap();
        let target = &result["components"][0]["targets"][0];
        assert_eq!(target["class"], "unknown");
        assert_eq!(target["status"], "unknown");
        assert_ne!(target["calculated_only_class"], "unknown");
    }

    #[test]
    fn external_tritium_merges_with_activation_h3_alias() {
        let scratch = Scratch::new();
        scratch.write("run.json", &native_run(json!({"H-3":3.0})).to_string());
        let properties = json!({"H3":{"z":1,"half_life_s":3.887e8,"alpha_emitting":false}});
        let external = json!({"status":"declared","source":"retention inventory","excludes_activation":true,"activity_bq":{"1":4.0}});
        let result = run_with(&scratch, spec("run.json", component(external), properties)).unwrap();
        let inventory = &result["components"][0]["targets"][0]["inventory_activity_bq"];
        assert_eq!(inventory.as_object().unwrap().len(), 1);
        assert!((inventory["H3"].as_f64().unwrap() - 11.5).abs() < 1e-12);
    }
}
