//! Native solver generated fixed-rate composition response basis and witness checks.

use actinv_core::run::{PreparedRun, RunResult};
use actinv_core::spec::{parse_duration, Spec};
use actinv_core::waste::{RulePack, WasteGeometry};
use actinv_core::waste_composition::{project_response, WeightInterval};
use actinv_data::composition::{material_key, MaterialKey};
use serde::de::{self, MapAccess, SeqAccess, Visitor};
use serde::{Deserialize, Deserializer};
use serde_json::{json, Number, Value};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::fmt;
use std::fs;
use std::io::Read;
use std::path::{Path, PathBuf};

const INPUT_SCHEMA: &str = "actinv-waste-composition-solve-spec-1";
const RULE_ID: &str = "us-nrc-10cfr61.55-v1";
const RULE_SHA256: &str = "890268af81a53815b8e11c238e4a1694eabb79664a1ca087fd7fe22b9fabe5d7";
const MAX_INPUT: u64 = 8 * 1024 * 1024;
const MAX_OUTPUT: usize = 32 * 1024 * 1024;
const MAX_COMPONENTS: usize = 4;
const MAX_ELEMENTS: usize = 8;
const MAX_TARGETS: usize = 4;
const MAX_BASIS: usize = 32;
const MAX_WITNESSES: usize = 64;
const ALLOWED_ELEMENTS: &[&str] = &[
    "Al", "B", "C", "Ca", "Co", "Cr", "Cu", "Eu", "Fe", "H", "K", "Mg", "Mn", "Mo", "N", "Na",
    "Nb", "Ni", "O", "P", "S", "Si", "Ta", "Ti", "V", "W",
];

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct SolveSpec {
    schema: String,
    rules: String,
    components: Vec<ComponentSpec>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ComponentSpec {
    id: String,
    base_spec: BaseSpec,
    mass_g: f64,
    #[serde(default, deserialize_with = "present_f64")]
    displaced_volume_cm3: Option<f64>,
    #[serde(default, deserialize_with = "present_f64")]
    density_g_cm3: Option<f64>,
    waste_type: actinv_core::waste::WasteType,
    external_tritium: crate::waste_bounds::ExternalTritium,
    composition_wt_percent_bounds: BTreeMap<String, WeightInterval>,
    targets: Vec<u64>,
    inventory_coverage: crate::waste_bounds::InventoryCoverage,
    unbounded_inventory_reasons: Vec<String>,
    model_source: String,
    model_assumptions: String,
}

#[derive(Debug, Deserialize)]
#[serde(untagged)]
enum BaseSpec {
    Path(String),
    Embedded(Value),
}

fn present_f64<'de, D>(deserializer: D) -> Result<Option<f64>, D::Error>
where
    D: Deserializer<'de>,
{
    f64::deserialize(deserializer).map(Some)
}

/// JSON value parser that rejects duplicate keys at every nesting depth before
/// serde_json::Value can collapse them.
#[derive(Debug)]
enum UniqueJson {
    Null,
    Bool(bool),
    Number(Number),
    String(String),
    Array(Vec<UniqueJson>),
    Object(BTreeMap<String, UniqueJson>),
}

impl<'de> Deserialize<'de> for UniqueJson {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        struct JsonVisitor;
        impl<'de> Visitor<'de> for JsonVisitor {
            type Value = UniqueJson;
            fn expecting(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                f.write_str("a JSON value with unique object keys")
            }
            fn visit_unit<E: de::Error>(self) -> Result<Self::Value, E> {
                Ok(UniqueJson::Null)
            }
            fn visit_none<E: de::Error>(self) -> Result<Self::Value, E> {
                Ok(UniqueJson::Null)
            }
            fn visit_bool<E: de::Error>(self, v: bool) -> Result<Self::Value, E> {
                Ok(UniqueJson::Bool(v))
            }
            fn visit_i64<E: de::Error>(self, v: i64) -> Result<Self::Value, E> {
                Ok(UniqueJson::Number(v.into()))
            }
            fn visit_u64<E: de::Error>(self, v: u64) -> Result<Self::Value, E> {
                Ok(UniqueJson::Number(v.into()))
            }
            fn visit_f64<E: de::Error>(self, v: f64) -> Result<Self::Value, E> {
                Number::from_f64(v)
                    .map(UniqueJson::Number)
                    .ok_or_else(|| E::custom("non-finite JSON number"))
            }
            fn visit_str<E: de::Error>(self, v: &str) -> Result<Self::Value, E> {
                Ok(UniqueJson::String(v.into()))
            }
            fn visit_string<E: de::Error>(self, v: String) -> Result<Self::Value, E> {
                Ok(UniqueJson::String(v))
            }
            fn visit_seq<A: SeqAccess<'de>>(self, mut seq: A) -> Result<Self::Value, A::Error> {
                let mut out = Vec::new();
                while let Some(value) = seq.next_element()? {
                    out.push(value);
                }
                Ok(UniqueJson::Array(out))
            }
            fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<Self::Value, A::Error> {
                let mut out = BTreeMap::new();
                while let Some((key, value)) = map.next_entry::<String, UniqueJson>()? {
                    if out.insert(key.clone(), value).is_some() {
                        return Err(de::Error::custom(format!(
                            "duplicate JSON object key '{key}'"
                        )));
                    }
                }
                Ok(UniqueJson::Object(out))
            }
        }
        deserializer.deserialize_any(JsonVisitor)
    }
}

impl UniqueJson {
    fn value(self) -> Value {
        match self {
            Self::Null => Value::Null,
            Self::Bool(v) => Value::Bool(v),
            Self::Number(v) => Value::Number(v),
            Self::String(v) => Value::String(v),
            Self::Array(v) => Value::Array(v.into_iter().map(Self::value).collect()),
            Self::Object(v) => Value::Object(v.into_iter().map(|(k, v)| (k, v.value())).collect()),
        }
    }
}

fn sha256(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

fn read_bounded(path: &Path) -> Result<String, String> {
    let metadata =
        fs::metadata(path).map_err(|e| format!("cannot stat {}: {e}", path.display()))?;
    if !metadata.is_file() {
        return Err(format!("{} is not a regular file", path.display()));
    }
    if metadata.len() > MAX_INPUT {
        return Err(format!("{} exceeds the 8 MiB input limit", path.display()));
    }
    let file = fs::File::open(path).map_err(|e| format!("cannot open {}: {e}", path.display()))?;
    let mut bytes = Vec::with_capacity((MAX_INPUT as usize).min(metadata.len() as usize + 1));
    file.take(MAX_INPUT + 1)
        .read_to_end(&mut bytes)
        .map_err(|e| format!("cannot read {}: {e}", path.display()))?;
    if bytes.len() as u64 > MAX_INPUT {
        return Err(format!("{} exceeds the 8 MiB input limit", path.display()));
    }
    String::from_utf8(bytes).map_err(|e| format!("{} is not UTF-8: {e}", path.display()))
}

fn parse_unique(text: &str, label: &str) -> Result<Value, String> {
    let mut de = serde_json::Deserializer::from_str(text);
    let value = UniqueJson::deserialize(&mut de)
        .map_err(|e| format!("cannot parse {label}: {e}"))?
        .value();
    de.end()
        .map_err(|e| format!("cannot parse trailing data in {label}: {e}"))?;
    Ok(value)
}

fn canonical_element(raw: &str) -> Result<String, String> {
    match material_key(raw)? {
        MaterialKey::Element(symbol) if symbol == raw => Ok(symbol),
        MaterialKey::Element(symbol) => {
            Err(format!("element key '{raw}' is not canonical '{symbol}'"))
        }
        MaterialKey::Nuclide { .. } => {
            Err(format!("composition key '{raw}' must be a natural element"))
        }
    }
}

fn canonical_weights(
    raw: &BTreeMap<String, WeightInterval>,
) -> Result<BTreeMap<String, WeightInterval>, String> {
    if !(1..=MAX_ELEMENTS).contains(&raw.len()) {
        return Err("composition requires one to eight elements".into());
    }
    let mut out = BTreeMap::new();
    for (key, interval) in raw {
        let element = canonical_element(key)?;
        if !ALLOWED_ELEMENTS.contains(&element.as_str()) {
            return Err(format!(
                "element '{element}' is outside the P75b-qualified natural-element set"
            ));
        }
        if !interval.lower_wt_percent.is_finite()
            || !interval.upper_wt_percent.is_finite()
            || interval.lower_wt_percent < 0.0
            || interval.upper_wt_percent > 100.0
            || interval.lower_wt_percent > interval.upper_wt_percent
        {
            return Err(format!("invalid wt-percent interval for '{element}'"));
        }
        if out.insert(element.clone(), *interval).is_some() {
            return Err(format!("duplicate element after normalization: {element}"));
        }
    }
    Ok(out)
}

struct PreparedComponent {
    input: ComponentSpec,
    base_source: String,
    base_sha256: String,
    base_spec_references: Value,
    path_rewrites: BTreeMap<String, String>,
    spec: Spec,
    prepared: Option<PreparedRun>,
    geometry: WasteGeometry,
    weights: BTreeMap<String, WeightInterval>,
    target_times: BTreeMap<u64, f64>,
}

type SelectedRun = (
    RunResult,
    Value,
    BTreeMap<u64, crate::waste_composition_verify::StepInventory>,
    crate::waste_composition_verify::CoverageScan,
);

fn load_spec(component: &ComponentSpec, wrapper: &Path) -> Result<(String, String), String> {
    match &component.base_spec {
        BaseSpec::Embedded(value) => Ok((
            serde_json::to_string(value).map_err(|e| e.to_string())?,
            "embedded".into(),
        )),
        BaseSpec::Path(path) => {
            if path.is_empty() {
                return Err("base_spec path must be nonempty".into());
            }
            let source = wrapper.parent().unwrap_or(Path::new(".")).join(path);
            Ok((read_bounded(&source)?, path.clone()))
        }
    }
}

fn collect_path_rewrites(
    original: &Value,
    resolved: &Value,
    key: &str,
    out: &mut BTreeMap<String, String>,
) {
    match (original, resolved) {
        (Value::Object(left), Value::Object(right)) => {
            for (name, old) in left {
                if let Some(new) = right.get(name) {
                    collect_path_rewrites(old, new, name, out);
                }
            }
        }
        (Value::Array(left), Value::Array(right)) => {
            for (old, new) in left.iter().zip(right) {
                collect_path_rewrites(old, new, key, out);
            }
        }
        (Value::String(old), Value::String(new))
            if matches!(key, "path" | "primary" | "fallback" | "overrides") && old != new =>
        {
            out.insert(new.clone(), old.clone());
        }
        _ => {}
    }
}

fn replace_paths(value: &mut Value, rewrites: &BTreeMap<String, String>) {
    match value {
        Value::String(text) => {
            if let Some(original) = rewrites.get(text) {
                *text = original.clone();
            }
        }
        Value::Array(items) => items
            .iter_mut()
            .for_each(|item| replace_paths(item, rewrites)),
        Value::Object(items) => items
            .values_mut()
            .for_each(|item| replace_paths(item, rewrites)),
        _ => {}
    }
}

fn validate_base(
    mut value: Value,
    component: &ComponentSpec,
) -> Result<(Spec, BTreeMap<u64, f64>), String> {
    let obj = value
        .as_object_mut()
        .ok_or("base_spec must be a JSON object")?;
    let projectile = obj
        .get("projectile")
        .and_then(Value::as_str)
        .unwrap_or("neutron");
    if !projectile.eq_ignore_ascii_case("neutron") {
        return Err("native composition solve requires a neutron projectile".into());
    }
    for key in ["uncertainty", "radiological", "damage", "self_shielding"] {
        if obj.get(key).is_some_and(|v| !v.is_null()) {
            return Err(format!(
                "base_spec.{key} is unsupported for native composition solve"
            ));
        }
    }
    let options = obj
        .entry("options")
        .or_insert_with(|| json!({}))
        .as_object_mut()
        .ok_or("base_spec.options must be an object")?;
    for key in ["screen", "rate_scale", "decay_scale", "yield_scale"] {
        if options.get(key).is_some_and(|v| !v.is_null()) {
            return Err(format!("base_spec.options.{key} is unsupported"));
        }
    }
    if let Some(gas) = options.get("gas") {
        match gas.as_bool() {
            Some(false) => {}
            Some(true) => return Err("base_spec.options.gas is unsupported".into()),
            None => return Err("base_spec.options.gas must be boolean".into()),
        }
    }
    if options
        .get("cram_order")
        .and_then(Value::as_u64)
        .is_some_and(|v| v != 16)
    {
        return Err("only CRAM order 16 is supported".into());
    }
    if options
        .get("require_shielding_complete")
        .and_then(Value::as_bool)
        .unwrap_or(false)
    {
        return Err("shielding completeness is unsupported".into());
    }
    if options
        .get("temperature_K")
        .and_then(Value::as_f64)
        .is_some_and(|v| !v.is_finite() || v <= 0.0)
    {
        return Err("temperature_K must be positive and finite".into());
    }
    options.insert("mode".into(), json!("coupled"));
    options.insert("prune".into(), json!("reach"));
    options.insert("bmin_atoms_per_g".into(), json!(0.0));
    options.insert("outputs".into(), json!(["ledger", "audit"]));
    options.insert("gas".into(), json!(false));
    let material = obj
        .get_mut("material")
        .and_then(Value::as_object_mut)
        .ok_or("base_spec.material must be an object")?;
    material.insert("basis".into(), json!("wt_percent"));
    material.insert("mass_g".into(), json!(component.mass_g));
    let share = 100.0 / component.composition_wt_percent_bounds.len() as f64;
    let source_composition: BTreeMap<String, f64> = component
        .composition_wt_percent_bounds
        .keys()
        .map(|element| (element.clone(), share))
        .collect();
    material.insert("composition".into(), json!(source_composition));
    if obj.get("fission_yields").is_some_and(|v| {
        v.get("files")
            .and_then(Value::as_array)
            .is_some_and(|a| !a.is_empty())
            || v.get("fixed_energy_eV").is_some_and(|x| !x.is_null())
            || v.get("energy")
                .and_then(Value::as_str)
                .is_some_and(|s| s != "spectrum_average")
    }) {
        return Err("fission-yield extensions are unsupported for native composition solve".into());
    }
    if obj.get("photon").is_some_and(|photon| {
        photon.get("response").is_some_and(|v| !v.is_null())
            || photon
                .get("group_boundaries_eV")
                .is_some_and(|v| !v.is_null())
            || photon
                .get("group_structure")
                .and_then(Value::as_str)
                .is_some_and(|v| v != "fispact-24")
            || photon
                .get("build_up_factor")
                .and_then(Value::as_f64)
                .is_some_and(|v| v != 2.0)
            || photon
                .get("gamma_constant_cutoff_eV")
                .and_then(Value::as_f64)
                .is_some_and(|v| v != 2.0e4)
    }) {
        return Err("non-default photon response/settings are unsupported".into());
    }
    let mut times = BTreeMap::new();
    let schedule = obj
        .get("schedule")
        .and_then(Value::as_array)
        .ok_or("base_spec.schedule must be an array")?;
    let mut total = 0.0;
    for (index, step) in schedule.iter().enumerate() {
        let dt = step
            .get("dt")
            .and_then(Value::as_str)
            .ok_or("schedule step requires dt")?;
        total += parse_duration(dt)?;
        if !total.is_finite() || total < 0.0 {
            return Err("cumulative schedule time is not finite and nonnegative".into());
        }
        if step.get("feed").is_some_and(|v| !v.is_null()) {
            return Err("schedule feed is unsupported".into());
        }
        if step.get("removal").is_some_and(|v| !v.is_null()) {
            return Err("schedule removal is unsupported".into());
        }
        if step.get("spectrum").is_some_and(|v| !v.is_null()) {
            return Err("per-step spectra are unsupported".into());
        }
        let step_no = u64::try_from(index + 1).map_err(|_| "schedule has too many steps")?;
        times.insert(step_no, total);
    }
    if component.targets.is_empty() || component.targets.len() > MAX_TARGETS {
        return Err("targets must contain one to four step numbers".into());
    }
    let mut seen = BTreeSet::new();
    for step in &component.targets {
        if *step == 0 || !seen.insert(*step) || !times.contains_key(step) {
            return Err(format!(
                "selected step {step} is duplicate, absent, or unaddressable"
            ));
        }
    }
    let spec = Spec::from_json(&value.to_string())?;
    Ok((spec, times))
}

fn preflight(component: ComponentSpec, wrapper: &Path) -> Result<PreparedComponent, String> {
    if component.id.trim().is_empty() {
        return Err("component id must be nonempty".into());
    }
    if !component.mass_g.is_finite() || component.mass_g <= 0.0 {
        return Err(format!(
            "component '{}' mass_g must be positive finite",
            component.id
        ));
    }
    let volume = match (component.displaced_volume_cm3, component.density_g_cm3) {
        (Some(v), None) if v.is_finite() && v > 0.0 => v,
        (None, Some(d)) if d.is_finite() && d > 0.0 => component.mass_g / d,
        (None, None) => {
            return Err(format!(
                "component '{}' needs a volume or density",
                component.id
            ))
        }
        (Some(_), Some(_)) => {
            return Err(format!(
                "component '{}' cannot set both volume and density",
                component.id
            ))
        }
        _ => {
            return Err(format!(
                "component '{}' geometry must be positive finite",
                component.id
            ))
        }
    };
    if !volume.is_finite() || volume <= 0.0 {
        return Err(format!(
            "component '{}' derived volume must be positive finite",
            component.id
        ));
    }
    let geometry = WasteGeometry {
        mass_g: component.mass_g,
        displaced_volume_cm3: volume,
    };
    let weights = canonical_weights(&component.composition_wt_percent_bounds)?;
    let zero: BTreeMap<String, f64> = weights.keys().map(|key| (key.clone(), 0.0)).collect();
    project_response(&weights, &zero, component.mass_g)?;
    if component.model_source.trim().is_empty() || component.model_assumptions.trim().is_empty() {
        return Err("model_source and model_assumptions must be nonempty".into());
    }
    validate_external(&component)?;
    let (base_text, base_source) = load_spec(&component, wrapper)?;
    let original_value = parse_unique(&base_text, "base spec")?;
    let mut path_rewrites = BTreeMap::new();
    let resolved_text = crate::resolve_catalog_json(&base_text)?;
    let resolved_value = parse_unique(&resolved_text, "catalog-resolved base spec")?;
    collect_path_rewrites(&original_value, &resolved_value, "", &mut path_rewrites);
    let base_spec_references = json!({
        "library":original_value.get("library").cloned().unwrap_or(Value::Null),
        "decay":original_value.get("decay").cloned().unwrap_or(Value::Null)
    });
    let (spec, target_times) = validate_base(resolved_value, &component)?;
    Ok(PreparedComponent {
        input: component,
        base_source,
        base_sha256: sha256(base_text.as_bytes()),
        base_spec_references,
        path_rewrites,
        spec,
        prepared: None,
        geometry,
        weights,
        target_times,
    })
}

pub fn run(path: &str, output: Option<&str>) -> Result<Value, String> {
    let wrapper_path = PathBuf::from(path);
    let text = read_bounded(&wrapper_path)?;
    let result = run_doc(&text, &wrapper_path)?;
    let rendered = serde_json::to_string_pretty(&result).map_err(|e| e.to_string())?;
    if rendered.len() > MAX_OUTPUT {
        return Err("final result exceeds 32 MiB".into());
    }
    crate::waste::output_doc(&result, output)?;
    Ok(result)
}

fn external_for_step(
    input: &crate::waste_bounds::ExternalTritium,
    step: u64,
) -> Result<Option<(f64, f64)>, String> {
    use crate::waste_bounds::ExternalTritium;
    match input {
        ExternalTritium::NotApplicable => Ok(None),
        ExternalTritium::Required => Ok(None),
        ExternalTritium::Bounded {
            source,
            excludes_activation,
            activity_bounds_bq,
        } => {
            if source.trim().is_empty() || !excludes_activation {
                return Err(
                    "bounded external_tritium requires source and excludes_activation:true".into(),
                );
            }
            let interval = activity_bounds_bq.get(&step.to_string()).ok_or_else(|| {
                format!("external_tritium is missing selected target step {step}")
            })?;
            if !interval.lower_bq.is_finite()
                || !interval.upper_bq.is_finite()
                || interval.lower_bq < 0.0
                || interval.lower_bq > interval.upper_bq
            {
                return Err(format!("external H-3 interval at step {step} is invalid"));
            }
            Ok(Some((interval.lower_bq, interval.upper_bq)))
        }
    }
}

fn validate_external(component: &ComponentSpec) -> Result<(), String> {
    use crate::waste_bounds::ExternalTritium;
    match &component.external_tritium {
        ExternalTritium::NotApplicable | ExternalTritium::Required => Ok(()),
        ExternalTritium::Bounded {
            source,
            excludes_activation,
            activity_bounds_bq,
        } => {
            if source.trim().is_empty() || !excludes_activation {
                return Err(
                    "bounded external_tritium requires source and excludes_activation:true".into(),
                );
            }
            let expected: BTreeSet<String> = component.targets.iter().map(u64::to_string).collect();
            if activity_bounds_bq.keys().cloned().collect::<BTreeSet<_>>() != expected {
                return Err(
                    "bounded external_tritium keys must exactly match selected targets".into(),
                );
            }
            for (step, interval) in activity_bounds_bq {
                let parsed = step.parse::<u64>().map_err(|_| {
                    format!("external H-3 key '{step}' must be canonical target step")
                })?;
                if parsed.to_string() != *step
                    || parsed == 0
                    || !interval.lower_bq.is_finite()
                    || !interval.upper_bq.is_finite()
                    || interval.lower_bq < 0.0
                    || interval.lower_bq > interval.upper_bq
                {
                    return Err(format!(
                        "external H-3 interval for step '{step}' is invalid"
                    ));
                }
            }
            Ok(())
        }
    }
}

fn positive_exposure(spec: &Spec) -> bool {
    spec.schedule.iter().any(|step| {
        let spectrum = step.spectrum.as_ref().unwrap_or(&spec.spectrum);
        let total = spectrum
            .total
            .unwrap_or_else(|| spectrum.flux_per_group.iter().sum());
        parse_duration(&step.dt).is_ok_and(|duration| duration > 0.0)
            && step.flux > 0.0
            && total > 0.0
    })
}

fn run_selected(
    prepared: &PreparedComponent,
    element: Option<&str>,
    composition: Option<&BTreeMap<String, f64>>,
    steps: &BTreeSet<u64>,
) -> Result<SelectedRun, String> {
    let mut spec = prepared.spec.clone();
    spec.material.mass_g = prepared.input.mass_g;
    spec.material.composition = if let Some(weights) = composition {
        weights.clone()
    } else {
        let element = element.ok_or("basis element is missing")?;
        BTreeMap::from([(element.to_string(), 100.0)])
    };
    let native = prepared
        .prepared
        .as_ref()
        .ok_or("component was not prepared")?;
    let run = native.run(&spec, "actinv waste composition solve")?;
    let mut scan = crate::waste_composition_verify::scan_coverage(
        native,
        &run,
        &spec.material.composition,
        positive_exposure(&spec),
    )?;
    replace_paths(&mut scan.evidence, &prepared.path_rewrites);
    let mut selected = BTreeMap::new();
    let mut targets = Vec::new();
    for step in steps {
        let inventory = crate::waste_composition_verify::selected_step(&run, *step)?;
        let expected_t = prepared.target_times[step];
        if inventory.t_s != expected_t {
            return Err(format!(
                "component '{}' step {step} result time differs from schedule endpoint",
                prepared.input.id
            ));
        }
        selected.insert(*step, inventory.clone());
        targets.push(json!({"step":step,"t_s":inventory.t_s,"activity_bq_per_g":inventory.activities_bq_per_g,"atoms_per_g":inventory.atoms_per_g}));
    }
    let mut cert = serde_json::to_value(&run.certificate).map_err(|e| e.to_string())?;
    replace_paths(&mut cert, &prepared.path_rewrites);
    let mut ledger = run.ledger.clone();
    replace_paths(&mut ledger, &prepared.path_rewrites);
    let evidence =
        json!({"ledger":ledger,"coverage":scan.evidence,"coverage_reasons":scan.reasons});
    let basis_composition = element.map(|e| BTreeMap::from([(e.to_string(), 100.0)]));
    let record = json!({
        "element":element,
        "composition_wt_percent":composition.or(basis_composition.as_ref()),
        "run_certificate":cert,
        "audit_evidence":evidence,
        "coverage_reasons":scan.reasons,
        "targets":targets
    });
    Ok((run, record, selected, scan))
}

fn build_declared_doc(
    prepared: &PreparedComponent,
    basis: &BTreeMap<String, BTreeMap<u64, crate::waste_composition_verify::StepInventory>>,
    properties: &BTreeMap<String, actinv_core::waste::NuclideProperties>,
    coverage_complete: bool,
    reasons: &[String],
) -> Result<String, String> {
    let mut targets = Vec::new();
    for step in &prepared.input.targets {
        let mut responses = BTreeMap::<String, Value>::new();
        for element in prepared.weights.keys() {
            let inv = basis
                .get(element)
                .and_then(|m| m.get(step))
                .ok_or("missing retained pure-element inventory")?;
            responses.insert(element.clone(), json!(inv.activities_bq_per_g));
        }
        targets.push(json!({
            "step":step,
            "t_s":prepared.target_times[step],
            "element_activity_bq_per_g":responses,
            "inventory_coverage":if coverage_complete {"complete"} else {"incomplete"},
            "unbounded_inventory_reasons":reasons,
            "bounds_source":prepared.input.model_source,
            "bounds_assumptions":prepared.input.model_assumptions
        }));
    }
    let external =
        serde_json::to_value(&prepared.input.external_tritium).map_err(|e| e.to_string())?;
    let mut comp = json!({
        "id":prepared.input.id,
        "mass_g":prepared.input.mass_g,
        "waste_type":prepared.input.waste_type,
        "nuclide_properties":properties,
        "external_tritium":external,
        "composition_wt_percent_bounds":prepared.weights,
        "targets":targets
    });
    if prepared.input.displaced_volume_cm3.is_some() {
        comp["displaced_volume_cm3"] = json!(prepared.input.displaced_volume_cm3);
    }
    if prepared.input.density_g_cm3.is_some() {
        comp["density_g_cm3"] = json!(prepared.input.density_g_cm3);
    }
    serde_json::to_string(&json!({"schema":"actinv-waste-composition-spec-1","rules":RULE_ID,"response_model":"fixed_rate_affine_activity","components":[comp]})).map_err(|e| e.to_string())
}

fn composition_key(weights: &BTreeMap<String, f64>) -> Result<String, String> {
    serde_json::to_string(weights).map_err(|e| e.to_string())
}

fn main_provenance(certificate: &Value, base_source: &str, original_references: &Value) -> Value {
    json!({
        "base_spec_source":base_source,
        "caller_data_references":original_references,
        "certificate_inputs":certificate.get("inputs").cloned().unwrap_or(Value::Null),
        "library":certificate.get("library").cloned().unwrap_or(Value::Null),
        "decay":certificate.get("decay").cloned().unwrap_or(Value::Null)
    })
}

fn run_doc(text: &str, wrapper_path: &Path) -> Result<Value, String> {
    let raw = parse_unique(text, "native composition solve spec")?;
    let spec: SolveSpec =
        serde_json::from_value(raw).map_err(|e| format!("invalid native composition spec: {e}"))?;
    if spec.schema != INPUT_SCHEMA || spec.rules != RULE_ID {
        return Err("unsupported schema or rules identifier".into());
    }
    if spec.components.is_empty() || spec.components.len() > MAX_COMPONENTS {
        return Err("components must contain one to four items".into());
    }
    let mut ids = BTreeSet::new();
    let mut prepared = Vec::new();
    let mut basis_count = 0usize;
    for component in spec.components {
        if !ids.insert(component.id.clone()) {
            return Err(format!("duplicate component id '{}'", component.id));
        }
        basis_count += component.composition_wt_percent_bounds.len();
        if basis_count > MAX_BASIS {
            return Err("request exceeds 32 pure-element basis solves".into());
        }
        let caller_complete = matches!(
            component.inventory_coverage,
            crate::waste_bounds::InventoryCoverage::Complete
        );
        if (caller_complete && !component.unbounded_inventory_reasons.is_empty())
            || (!caller_complete
                && (component.unbounded_inventory_reasons.is_empty()
                    || component
                        .unbounded_inventory_reasons
                        .iter()
                        .any(|reason| reason.trim().is_empty())))
        {
            return Err(format!(
                "component '{}' coverage declaration and reasons disagree",
                component.id
            ));
        }
        prepared.push(preflight(component, wrapper_path)?);
    }
    let rules = RulePack::bundled()?;
    if rules.id != RULE_ID || rules.sha256 != RULE_SHA256 {
        return Err("bundled rule pack identity does not match P109".into());
    }
    let original_sha = sha256(text.as_bytes());
    let mut native_components = Vec::new();
    let mut generated_specs = Vec::new();
    let mut total_witnesses = 0usize;
    let mut basis_total = 0usize;
    let mut full_total = 0usize;

    for item in &mut prepared {
        // The metadata and specifications for every component were preflighted first;
        // hold only this component's loaded data while executing its basis and witnesses.
        item.prepared = Some(PreparedRun::prepare(&item.spec)?);
        let steps: BTreeSet<u64> = item.input.targets.iter().copied().collect();
        let mut basis =
            BTreeMap::<String, BTreeMap<u64, crate::waste_composition_verify::StepInventory>>::new(
            );
        let mut basis_records = Vec::new();
        let mut coverage_reasons: BTreeSet<String> = item
            .input
            .unbounded_inventory_reasons
            .iter()
            .cloned()
            .collect();
        let mut coverage_evidence = Vec::new();
        let mut active = BTreeSet::new();
        let mut first_certificate = Value::Null;
        for element in item.weights.keys() {
            let (_run, record, inventories, scan) =
                run_selected(item, Some(element), None, &steps)?;
            if first_certificate.is_null() {
                first_certificate = record["run_certificate"].clone();
            }
            coverage_reasons.extend(scan.reasons.clone());
            coverage_evidence
                .push(json!({"element":element,"evidence":scan.evidence,"reasons":scan.reasons}));
            for (step, inventory) in &inventories {
                active.extend(
                    inventory
                        .activities_bq_per_g
                        .iter()
                        .filter(|(_, v)| **v > 0.0)
                        .map(|(k, _)| k.clone()),
                );
                basis
                    .entry(element.clone())
                    .or_default()
                    .insert(*step, inventory.clone());
            }
            basis_records.push(record);
            basis_total += 1;
        }
        let mut external_positive = false;
        for step in &item.input.targets {
            external_positive |= external_for_step(&item.input.external_tritium, *step)?
                .is_some_and(|(_, upper)| upper > 0.0);
        }
        if external_positive {
            active.insert("H3".into());
        }
        let properties = crate::waste_composition_verify::derive_properties(
            item.prepared.as_ref().ok_or("component was not prepared")?,
            &active,
        )?;
        let caller_complete = matches!(
            item.input.inventory_coverage,
            crate::waste_bounds::InventoryCoverage::Complete
        );
        let reasons: Vec<String> = coverage_reasons.iter().cloned().collect();
        let initial_complete = caller_complete && reasons.is_empty();
        let generated_text =
            build_declared_doc(item, &basis, &properties, initial_complete, &reasons)?;
        let projected = crate::waste_composition::evaluate_doc(&generated_text)?;
        let p108_component = projected["components"]
            .as_array()
            .and_then(|v| v.first())
            .cloned()
            .ok_or("P108 evaluator omitted component")?;
        let mut witness_map = BTreeMap::<String, (BTreeMap<String, f64>, Vec<Value>)>::new();
        // Every activity extremum witness is fully solved. Full maps are the identity.
        for target in p108_component["targets"]
            .as_array()
            .ok_or("P108 targets missing")?
        {
            let step = target["step"]
                .as_u64()
                .ok_or("projected target step invalid")?;
            if let Some(projections) = target["projection_records"].as_object() {
                for (nuclide, projection) in projections {
                    for (endpoint, field) in [
                        ("lower", "min_witness_wt_percent"),
                        ("upper", "max_witness_wt_percent"),
                    ] {
                        let weights: BTreeMap<String, f64> =
                            serde_json::from_value(projection[field].clone())
                                .map_err(|e| format!("invalid projected witness: {e}"))?;
                        let key = composition_key(&weights)?;
                        witness_map
                            .entry(key)
                            .or_insert_with(|| (weights, Vec::new()))
                            .1
                            .push(json!({"step":step,"nuclide":nuclide,"endpoint":endpoint}));
                    }
                }
            }
        }
        let zeros: BTreeMap<String, f64> =
            item.weights.keys().map(|key| (key.clone(), 0.0)).collect();
        let reference =
            project_response(&item.weights, &zeros, item.input.mass_g)?.min_witness_wt_percent;
        let reference_key = composition_key(&reference)?;
        witness_map
            .entry(reference_key)
            .or_insert_with(|| (reference.clone(), Vec::new()))
            .1
            .push(json!({"kind":"canonical_reference"}));
        if total_witnesses + witness_map.len() > MAX_WITNESSES {
            return Err("request exceeds 64 distinct full witness compositions".into());
        }
        total_witnesses += witness_map.len();

        let mut witness_records = Vec::new();
        for (_key, (weights, witness_ids)) in witness_map {
            let (actual_run, run_record, actual_by_step, scan) =
                run_selected(item, None, Some(&weights), &steps)?;
            full_total += 1;
            coverage_reasons.extend(scan.reasons.clone());
            coverage_evidence.push(json!({"composition_wt_percent":weights,"evidence":scan.evidence,"reasons":scan.reasons}));
            let mut checks = Vec::new();
            for step in &steps {
                let external = external_for_step(&item.input.external_tritium, *step)?;
                let check = crate::waste_composition_verify::verify_witness(
                    &rules,
                    item.input.waste_type,
                    item.geometry,
                    &properties,
                    &basis
                        .iter()
                        .map(|(element, map)| (element.clone(), map[step].clone()))
                        .collect(),
                    &weights,
                    item.input.mass_g,
                    *step,
                    &actual_run,
                    external,
                )?;
                if !check["pass"].as_bool().unwrap_or(false) {
                    return Err(format!(
                        "native verification failed for component '{}' step {step}",
                        item.input.id
                    ));
                }
                checks.push(check);
            }
            let cert = run_record["run_certificate"].clone();
            let evidence = run_record["audit_evidence"].clone();
            let _ = actual_by_step;
            witness_records.push(json!({"composition_wt_percent":weights,"witness_ids":witness_ids,"run_certificate":cert,"audit_evidence":evidence,"coverage_reasons":scan.reasons,"targets":checks}));
        }

        // Rebuild P108 using the final coverage downgrade after every witness audit.
        let final_reasons: Vec<String> = coverage_reasons.iter().cloned().collect();
        let final_complete = caller_complete && final_reasons.is_empty();
        let final_text =
            build_declared_doc(item, &basis, &properties, final_complete, &final_reasons)?;
        generated_specs.push(final_text);
        let provenance = main_provenance(
            &first_certificate,
            &item.base_source,
            &item.base_spec_references,
        );
        let native = json!({
            "base_spec_source":item.base_source,
            "base_spec_sha256":item.base_sha256,
            "solver_provenance":provenance,
            "solver_basis":basis_records,
            "coverage_evidence":coverage_evidence,
            "native_verification":{"tolerance":{"activity_relative":1e-6,"activity_absolute_bq_per_g":1e-12,"atoms_l1_relative":1e-6,"atoms_l1_absolute_per_g":1e-12,"class_relative":1e-6,"class_absolute":1e-12},"reference_composition_wt_percent":reference,"unique_witness_count":witness_records.len(),"verified_witnesses":witness_records,"passed":true}
        });
        let mut native = native;
        native["id"] = json!(item.input.id);
        native_components.push(native);
        item.prepared.take();
    }

    let mut generated_root = json!({"schema":"actinv-waste-composition-spec-1","rules":RULE_ID,"response_model":"fixed_rate_affine_activity","components":[]});
    for generated in &generated_specs {
        let value = parse_unique(generated, "generated P108 spec")?;
        generated_root["components"]
            .as_array_mut()
            .ok_or("generated components missing")?
            .push(value["components"][0].clone());
    }
    let generated = serde_json::to_string(&generated_root).map_err(|e| e.to_string())?;
    let mut result = crate::waste_composition::evaluate_doc(&generated)?;
    let components = result["components"]
        .as_array_mut()
        .ok_or("P108 result has no components")?;
    for component in components {
        let id = component["id"]
            .as_str()
            .ok_or("P108 component id missing")?;
        let native = native_components
            .iter()
            .find(|c| c["id"].as_str() == Some(id))
            .ok_or("native component record missing")?;
        let mut native = native.clone();
        native
            .as_object_mut()
            .ok_or("native record is not an object")?
            .remove("id");
        component["native"] = native;
    }
    result["schema"] = json!("actinv-waste-composition-solve-result-1");
    result["method"] = json!("native_fixed_rate_composition_polytope");
    result["response_model"] = json!("fixed_rate_affine_activity");
    result["input_sha256"] = json!(original_sha);
    result["generated_response_input_sha256"] = json!(sha256(generated.as_bytes()));
    result["generated_response_spec_json"] = json!(generated);
    result["rules"] = json!({"id":rules.id,"version":rules.version,"sha256":rules.sha256,"source_url":rules.source_url,"source_as_of":rules.source_as_of});
    result["response_unit"] = json!("Bq/g");
    result["activity_unit"] = json!("Bq");
    result["composition_unit"] = json!("wt_percent");
    result["atom_inventory_unit"] = json!("atoms/g");
    result["scientific_scope"] = json!({
        "activity_model":"fixed_rate_affine_activity",
        "solver_error_bounded":false,
        "nuclear_data_or_physical_model_error_bounded":false,
        "verification":"selected extremum and canonical reference compositions only",
        "probability_or_confidence_claim":false
    });
    result["solver_work"] = json!({"basis_solve_count":basis_total,"full_witness_solve_count":full_total,"unique_witness_count":total_witnesses,"max_basis_solves":MAX_BASIS,"max_full_witness_solves":MAX_WITNESSES});
    let bytes = serde_json::to_vec(&result).map_err(|e| e.to_string())?;
    if bytes.len() > MAX_OUTPUT {
        return Err("final result exceeds 32 MiB".into());
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::{parse_unique, run};
    use std::fs;
    use std::sync::atomic::{AtomicUsize, Ordering};

    static NEXT: AtomicUsize = AtomicUsize::new(0);

    #[test]
    fn recursively_rejects_duplicate_object_keys() {
        assert!(parse_unique(r#"{"a":{"x":1,"x":2}}"#, "fixture").is_err());
        assert!(parse_unique(r#"{"a":[{"x":1,"x":2}]}"#, "fixture").is_err());
        assert!(parse_unique(r#"{"a":{"x":1}}"#, "fixture").is_ok());
    }

    #[test]
    fn invalid_native_request_preserves_existing_output_sentinel() {
        let nonce = NEXT.fetch_add(1, Ordering::Relaxed);
        let root =
            std::env::temp_dir().join(format!("actinv-p109-cli-{}-{nonce}", std::process::id()));
        fs::create_dir_all(&root).unwrap();
        let input = root.join("input.json");
        let output = root.join("result.json");
        fs::write(
            &input,
            r#"{"schema":"wrong","rules":"us-nrc-10cfr61.55-v1","components":[]}"#,
        )
        .unwrap();
        fs::write(&output, b"sentinel").unwrap();
        assert!(run(input.to_str().unwrap(), output.to_str()).is_err());
        assert_eq!(fs::read(&output).unwrap(), b"sentinel");
        let _ = fs::remove_dir_all(root);
    }
}
