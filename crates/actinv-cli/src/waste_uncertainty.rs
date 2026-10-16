//! `actinv waste uncertainty` — Part 61 class probabilities under nuclear-data uncertainty.
//!
//! The problem is solved once at its nominal inputs and once per correlated draw of the sampled channels, through
//! the `study.robustness` sampler (`actinv_core::study::run_samples`). Every solve is classified at every target
//! step with the nominal waste evaluator, and the class frequencies carry Clopper-Pearson intervals. The
//! probabilities are conditional on the sampled channels; see `COVERAGE_STATEMENT`.

use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

use actinv_core::run::PreparedRun;
use actinv_core::sample_stats::{
    beta_quantile, clopper_pearson, min_trials_all_successes, percentile_sorted, sorted,
};
use actinv_core::spec::{LibraryRef, Spec};
use actinv_core::study::{self, RobustChannels, MAX_ROBUSTNESS_SAMPLES};
use actinv_core::waste::{NuclideProperties, RulePack};
use serde::Deserialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use crate::waste::{self, ComponentSpec};

const SPEC_SCHEMA: &str = "actinv-waste-uncertainty-spec-1";
const RESULT_SCHEMA: &str = "actinv-waste-uncertainty-result-1";
const ENTRY_POINT: &str = "actinv waste uncertainty";
/// Fewest samples a sampling block may ask for.
const MIN_SAMPLES: u64 = 20;
const DEFAULT_CONFIDENCE: f64 = 0.95;
/// Largest sample count the not-established report searches.
const NEEDED_SAMPLES_CAP: u64 = 1_000_000;
/// How far past the crossing of the continuous bound the integer search looks for the first sample count.
const NEEDED_SAMPLES_WINDOW: u64 = 50_000;
/// Reported percentiles of every normalized table sum.
const SUM_PERCENTILES: [(f64, &str); 3] = [(5.0, "5"), (50.0, "50"), (95.0, "95")];
const FROM_DECAY_DATA: &str = "from_decay_data";

/// Part 61 classes in the order the established-class rule walks them, then the class of a sample that has none.
const CLASSES: [&str; 5] = ["A", "B", "C", "above_class_c", "unknown"];
const UNKNOWN: usize = 4;

const COVERAGE_STATEMENT: &str = "The probabilities are conditional on the sampled channels listed here. They exclude MF=32 and MF=40 covariance, cross-channel correlation, composition, geometry, model and flux-systematic uncertainty, except where a listed channel samples it. Measured coverage of these channels on the FNS corpus is below nominal (docs/DATA_LIMITATIONS.md), so the intervals can be overconfident. They are not tolerance limits, a budget at a confidence level, a package class or a disposal acceptance.";

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct UncertaintySpec {
    schema: String,
    problem: String,
    rules: String,
    targets: Vec<u64>,
    nuclide_properties: Value,
    components: Vec<Value>,
    sampling: SamplingSpec,
    #[serde(default)]
    confidence_level: Option<f64>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct SamplingSpec {
    #[serde(default)]
    channels: RobustChannels,
    #[serde(default)]
    covariance: Option<LibraryRef>,
    samples: u64,
    seed: u64,
    #[serde(default)]
    keep_samples: Option<String>,
}

enum Properties {
    Declared(BTreeMap<String, NuclideProperties>),
    FromDecayData,
}

/// A spec that passed every check that needs no data.
struct Validated {
    spec: UncertaintySpec,
    samples: u32,
    confidence: f64,
    rules: RulePack,
    component: ComponentSpec,
    properties: Properties,
    /// the covariance sidecar with its path resolved against the spec's folder
    covariance: Option<LibraryRef>,
}

/// What became of one sample.
enum Outcome {
    /// the target steps of its result, in the shape the nominal waste command reads
    Solved(Value),
    Failed(String),
}

/// Class counts and normalized table sums of one component at one target step.
#[derive(Default)]
struct Tally {
    counts: [u64; 5],
    failed: Vec<(usize, String)>,
    /// normalized sum of every constraint, keyed by (target class A/B/C = 0/1/2, table, column or 0)
    sums: BTreeMap<(usize, u8, u8), Vec<f64>>,
}

impl Tally {
    /// A sample without a classification counts in every denominator as class `unknown`, so a failure can only
    /// weaken an established class.
    fn fail(&mut self, sample: usize, error: &str) {
        self.counts[UNKNOWN] += 1;
        self.failed.push((sample, error.to_string()));
    }

    /// Record one sample's classification, a target of the nominal waste result's shape.
    fn add(&mut self, sample: usize, target: &Value) {
        let Some(class) = target["class"].as_str().and_then(class_index) else {
            self.fail(sample, "the classification has no recognised class");
            return;
        };
        self.counts[class] += 1;
        let constraints = target["evaluation"]["constraints"]
            .as_array()
            .into_iter()
            .flatten();
        for constraint in constraints {
            let target_class = constraint["target_class"].as_str().and_then(class_index);
            let (Some(target_class), Some(table), Some(sum)) = (
                target_class.filter(|rank| *rank < 3),
                constraint["table"].as_u64(),
                constraint["normalized_sum"].as_f64(),
            ) else {
                continue;
            };
            let column = constraint["column"].as_u64().unwrap_or(0);
            self.sums
                .entry((target_class, table as u8, column as u8))
                .or_default()
                .push(sum);
        }
    }
}

fn class_index(label: &str) -> Option<usize> {
    CLASSES.iter().position(|class| *class == label)
}

fn sha256(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

pub fn run(path: &str, output: Option<&str>) -> Result<Value, String> {
    let source = Path::new(path);
    let base = source.parent().unwrap_or_else(|| Path::new("."));
    let text = std::fs::read_to_string(source)
        .map_err(|error| format!("cannot read waste uncertainty spec {path}: {error}"))?;
    let result = run_doc(&text, base)?;
    waste::output_doc(&result, output)?;
    Ok(result)
}

/// Every refusal that needs no data, before anything is read, solved or written.
fn validate(text: &str, base: &Path) -> Result<Validated, String> {
    let spec: UncertaintySpec = serde_json::from_str(text)
        .map_err(|error| format!("cannot parse waste uncertainty spec: {error}"))?;
    if spec.schema != SPEC_SCHEMA {
        return Err(format!(
            "unsupported waste uncertainty schema '{}'",
            spec.schema
        ));
    }
    let samples = spec.sampling.samples;
    if samples < MIN_SAMPLES {
        return Err(format!(
            "sampling.samples must be at least {MIN_SAMPLES}, got {samples}; a class is established only \
             when the Clopper-Pearson lower bound reaches the confidence level, which takes far more \
             samples than that (72 at confidence 0.95)"
        ));
    }
    if samples > u64::from(MAX_ROBUSTNESS_SAMPLES) {
        return Err(format!(
            "robustness_too_large: {samples} samples exceeds the {MAX_ROBUSTNESS_SAMPLES} cap"
        ));
    }
    let confidence = spec.confidence_level.unwrap_or(DEFAULT_CONFIDENCE);
    if !confidence.is_finite() || confidence <= 0.0 || confidence >= 1.0 {
        return Err("confidence_level must be strictly between zero and one".into());
    }
    let covariance = resolve_covariance(spec.sampling.covariance.as_ref(), base)?;
    // the fission-yield declaration lives in the problem; its check is repeated once the problem is read
    study::validate_channels(
        &spec.sampling.channels,
        covariance.as_ref(),
        true,
        "sampling",
    )?;
    waste::validate_targets(&spec.targets)?;
    let rules = waste::load_rule_pack(&spec.rules, base)?;
    if spec.components.len() != 1 {
        return Err(
            "components must contain exactly one component: the problem is a single native run"
                .into(),
        );
    }
    let component: ComponentSpec = serde_json::from_value(spec.components[0].clone())
        .map_err(|error| format!("invalid component: {error}"))?;
    if !component.cells.is_empty() {
        return Err(format!(
            "component {} must not declare cells: the problem is a single native run",
            component.id
        ));
    }
    if component.id.is_empty() {
        return Err("component id must not be empty".into());
    }
    waste::waste_geometry(&component)?;
    for &step in &spec.targets {
        waste::external_tritium_at(&component.external_tritium, step)?;
    }
    let properties = parse_properties(&spec.nuclide_properties)?;
    Ok(Validated {
        samples: samples as u32,
        confidence,
        rules,
        component,
        properties,
        covariance,
        spec,
    })
}

fn parse_properties(value: &Value) -> Result<Properties, String> {
    match value {
        Value::String(name) if name == FROM_DECAY_DATA => Ok(Properties::FromDecayData),
        Value::String(name) => Err(format!(
            "nuclide_properties '{name}' is not an object or the string '{FROM_DECAY_DATA}'"
        )),
        Value::Object(_) => serde_json::from_value(value.clone())
            .map(Properties::Declared)
            .map_err(|error| format!("invalid nuclide_properties: {error}")),
        _ => Err(format!(
            "nuclide_properties must be an object or the string '{FROM_DECAY_DATA}'"
        )),
    }
}

/// The covariance sidecar as the sampler reads it: catalog references resolved, a relative path taken against
/// the spec's folder.
fn resolve_covariance(
    reference: Option<&LibraryRef>,
    base: &Path,
) -> Result<Option<LibraryRef>, String> {
    let Some(reference) = reference else {
        return Ok(None);
    };
    let text = serde_json::to_string(reference).map_err(|error| error.to_string())?;
    let mut resolved: LibraryRef = serde_json::from_str(&crate::resolve_catalog_json(&text)?)
        .map_err(|error| format!("invalid sampling.covariance: {error}"))?;
    if !Path::new(&resolved.path).is_absolute() {
        resolved.path = base.join(&resolved.path).to_string_lossy().into_owned();
    }
    Ok(Some(resolved))
}

/// Refusals about the problem itself: what `study.robustness` and the `uncertainty` block cannot sample.
fn check_problem(problem: &Spec, validated: &Validated) -> Result<(), String> {
    if problem.options.gas {
        return Err(
            "the problem enables options.gas, which waste uncertainty sampling does not support"
                .into(),
        );
    }
    if problem.uncertainty.is_some() {
        return Err("the problem declares an uncertainty block; waste uncertainty samples the channels of its own sampling block".into());
    }
    if problem.options.rate_scale.is_some()
        || problem.options.decay_scale.is_some()
        || problem.options.yield_scale.is_some()
    {
        return Err("the problem declares options.rate_scale, decay_scale or yield_scale, which the sampler writes itself".into());
    }
    for (index, step) in problem.schedule.iter().enumerate() {
        let number = index + 1;
        if step.library.is_some() || step.projectile.is_some() {
            return Err(format!(
                "schedule step {number} switches library or projectile, which waste uncertainty sampling does not support"
            ));
        }
        if step.is_power_driven() {
            return Err(format!(
                "schedule step {number} is power-driven, which waste uncertainty sampling does not support"
            ));
        }
        if step.wall_loading_MW_m2.is_some() || step.partition.is_some() {
            return Err(format!(
                "schedule step {number} declares wall_loading_MW_m2 or partition, which waste uncertainty sampling does not support"
            ));
        }
        if step.spectrum.is_some() {
            return Err(format!(
                "schedule step {number} declares its own spectrum; the sampler perturbs only the base spectrum"
            ));
        }
    }
    for &step in &validated.spec.targets {
        if step as usize > problem.schedule.len() {
            return Err(format!(
                "target step {step} is not a step of the problem, whose schedule has {} steps",
                problem.schedule.len()
            ));
        }
    }
    study::validate_channels(
        &validated.spec.sampling.channels,
        validated.covariance.as_ref(),
        !problem.fission_yields.files.is_empty(),
        "sampling",
    )
}

/// The target steps of a solved run in the shape the nominal waste command reads.
fn compact_run(result: &Value, targets: &[u64]) -> Result<Value, String> {
    let rows = result
        .get("steps")
        .and_then(Value::as_array)
        .ok_or("the result has no steps array")?;
    let mut kept = Vec::with_capacity(targets.len());
    for &step in targets {
        let row = rows
            .iter()
            .find(|row| row.get("step").and_then(Value::as_u64) == Some(step))
            .ok_or_else(|| format!("the result has no step {step}"))?;
        kept.push(json!({
            "step": step,
            "t_s": row["t_s"],
            "activity_Bq_per_g": row["activity_Bq_per_g"],
        }));
    }
    Ok(json!({
        "entry_point": result["entry_point"],
        "mode": result["mode"],
        "steps": kept,
    }))
}

/// Nuclides with positive activity at a target step of `compact`, in the run's own spelling.
fn add_active(compact: &Value, active: &mut BTreeSet<String>) {
    for row in compact["steps"].as_array().into_iter().flatten() {
        for (name, value) in row["activity_Bq_per_g"].as_object().into_iter().flatten() {
            if value.as_f64().is_some_and(|activity| activity > 0.0) {
                active.insert(name.clone());
            }
        }
    }
}

fn waste_spec_text(
    validated: &Validated,
    properties: &BTreeMap<String, NuclideProperties>,
) -> Result<String, String> {
    serde_json::to_string(&json!({
        "schema": "actinv-waste-spec-1",
        "rules": validated.spec.rules,
        "input": "sampled run",
        "targets": validated.spec.targets,
        "nuclide_properties": properties,
        "components": validated.spec.components,
    }))
    .map_err(|error| error.to_string())
}

/// Classify one run's target steps with the nominal waste command's own evaluator.
fn classify(waste_text: &str, base: &Path, compact: &Value) -> Result<Value, String> {
    let bytes = serde_json::to_vec(compact).map_err(|error| error.to_string())?;
    waste::evaluate_doc_with_input_details(waste_text, base, &bytes)
        .map(|evaluated| evaluated.document)
}

pub(crate) fn run_doc(text: &str, base: &Path) -> Result<Value, String> {
    let validated = validate(text, base)?;
    let targets = &validated.spec.targets;
    let problem_path = base.join(&validated.spec.problem);
    let problem_text = std::fs::read_to_string(&problem_path)
        .map_err(|error| format!("cannot read problem {}: {error}", problem_path.display()))?;
    let problem = Spec::from_json(&crate::resolve_catalog_json(&problem_text)?)
        .map_err(|error| format!("problem {}: {error}", problem_path.display()))?;
    check_problem(&problem, &validated)?;

    let prepared = PreparedRun::prepare(&problem)?;
    let nominal_run = prepared.run(&problem, ENTRY_POINT)?;
    let nominal = serde_json::to_value(&nominal_run)
        .map_err(|error| format!("cannot serialize the nominal result: {error}"))?;
    let nominal_compact =
        compact_run(&nominal, targets).map_err(|error| format!("nominal run: {error}"))?;
    let mut active = BTreeSet::new();
    add_active(&nominal_compact, &mut active);
    // declared properties can be checked against the nominal inventory before any sample is solved
    if let Properties::Declared(properties) = &validated.properties {
        classify(
            &waste_spec_text(&validated, properties)?,
            base,
            &nominal_compact,
        )?;
    }

    let keep_dir = validated
        .spec
        .sampling
        .keep_samples
        .as_ref()
        .map(|dir| base.join(dir));
    if let Some(dir) = &keep_dir {
        std::fs::create_dir_all(dir)
            .map_err(|error| format!("cannot create {}: {error}", dir.display()))?;
    }
    let mut outcomes: Vec<Outcome> = Vec::with_capacity(validated.samples as usize);
    let sampled_inputs = study::run_samples(
        &validated.spec.sampling.channels,
        validated.covariance.as_ref(),
        &problem,
        Path::new("."),
        validated.samples,
        validated.spec.sampling.seed,
        |index, sample_spec, result| {
            if let Some(dir) = &keep_dir {
                let spec_path = dir.join(format!("rob_{index}.json"));
                std::fs::write(&spec_path, study::sample_spec_text(sample_spec))
                    .map_err(|error| format!("cannot write {}: {error}", spec_path.display()))?;
                if let Ok(value) = &result {
                    let out_path = dir.join(format!("rob_{index}.out.json"));
                    std::fs::write(&out_path, study::sample_result_text(value))
                        .map_err(|error| format!("cannot write {}: {error}", out_path.display()))?;
                }
            }
            outcomes.push(match result {
                Ok(value) => match compact_run(&value, targets) {
                    Ok(compact) => {
                        add_active(&compact, &mut active);
                        Outcome::Solved(compact)
                    }
                    Err(error) => Outcome::Failed(error),
                },
                Err(error) => Outcome::Failed(error),
            });
            Ok(())
        },
    )?;

    let properties = match &validated.properties {
        Properties::Declared(properties) => properties.clone(),
        Properties::FromDecayData => {
            for &step in targets {
                let (external, _) =
                    waste::external_tritium_at(&validated.component.external_tritium, step)?;
                if external > 0.0 {
                    active.insert("H3".to_string());
                }
            }
            crate::waste_composition_verify::derive_properties(&prepared, &active)?
        }
    };
    let waste_text = waste_spec_text(&validated, &properties)?;
    let nominal_doc = classify(&waste_text, base, &nominal_compact)?;

    let mut tallies: Vec<Tally> = targets.iter().map(|_| Tally::default()).collect();
    for (index, outcome) in outcomes.iter().enumerate() {
        match outcome {
            Outcome::Failed(error) => {
                tallies
                    .iter_mut()
                    .for_each(|tally| tally.fail(index, error));
            }
            Outcome::Solved(compact) => match classify(&waste_text, base, compact) {
                Ok(document) => {
                    for (position, tally) in tallies.iter_mut().enumerate() {
                        tally.add(index, &document["components"][0]["targets"][position]);
                    }
                }
                Err(error) => {
                    let error = format!("classification failed: {error}");
                    tallies
                        .iter_mut()
                        .for_each(|tally| tally.fail(index, &error));
                }
            },
        }
    }

    let n = u64::from(validated.samples);
    let confidence = validated.confidence;
    let nominal_component = &nominal_doc["components"][0];
    let mut target_reports = Vec::with_capacity(targets.len());
    let mut established: Vec<(u64, Option<usize>)> = Vec::with_capacity(targets.len());
    for (position, (&step, tally)) in targets.iter().zip(&tallies).enumerate() {
        let nominal_target = &nominal_component["targets"][position];
        established.push((step, established_class(&tally.counts, confidence)));
        target_reports.push(target_report(nominal_target, tally, n, confidence));
    }
    let samples_failed = outcomes
        .iter()
        .filter(|outcome| matches!(outcome, Outcome::Failed(_)))
        .count();
    let completeness = nominal["ledger"]
        .get("completeness")
        .cloned()
        .unwrap_or_else(|| {
            json!({
                "status": "not_recorded",
                "reason": "the problem does not list \"audit\" in options.outputs",
            })
        });
    let rules = &validated.rules;
    let properties_source = match validated.properties {
        Properties::Declared(_) => "declared",
        Properties::FromDecayData => FROM_DECAY_DATA,
    };
    Ok(json!({
        "schema": RESULT_SCHEMA,
        "waste_uncertainty_sha256": sha256(text.as_bytes()),
        "problem": validated.spec.problem,
        "problem_sha256": sha256(problem_text.as_bytes()),
        "rules": validated.spec.rules,
        "rule_pack": {"id": rules.id, "version": rules.version, "source_url": rules.source_url, "source_as_of": rules.source_as_of, "sha256": rules.sha256},
        "rule_pack_sha256": rules.sha256,
        "targets": validated.spec.targets,
        "confidence_level": confidence,
        "seed": validated.spec.sampling.seed,
        "samples": n,
        "samples_failed": samples_failed,
        "minimum_samples_for_establishment": min_trials_all_successes(confidence),
        "nuclide_properties_source": properties_source,
        "nuclide_properties": properties,
        "components": [{
            "id": nominal_component["id"],
            "mass_g": nominal_component["mass_g"],
            "displaced_volume_cm3": nominal_component["displaced_volume_cm3"],
            "waste_type": nominal_component["waste_type"],
            "external_tritium": nominal_component["external_tritium"],
            "nominal_status": nominal_component["status"],
            "targets": target_reports,
            "earliest_established_step": earliest_established(&established),
        }],
        "coverage": {
            "channels_sampled": validated.spec.sampling.channels.enabled_names(),
            "sampled_inputs": sampled_inputs,
            "nominal_completeness_ledger": completeness,
            "statement": COVERAGE_STATEMENT,
        },
    }))
}

/// Number of samples in `counts` at most `class` (index into [`CLASSES`]).
fn cumulative(counts: &[u64; 5], class: usize) -> u64 {
    counts[..=class].iter().sum()
}

/// The first class, in the order A, B, C, above Class C, for which the lower end of the Clopper-Pearson
/// interval of P(class at most X) reaches `confidence`. A sample that is unknown or failed counts in `n` and is
/// at most no class.
fn established_class(counts: &[u64; 5], confidence: f64) -> Option<usize> {
    let n: u64 = counts.iter().sum();
    (0..UNKNOWN)
        .find(|&class| clopper_pearson(cumulative(counts, class), n, confidence).0 >= confidence)
}

/// Smallest `n'` (at most [`NEEDED_SAMPLES_CAP`]) for which `floor(k n' / n)` successes in `n'` trials would
/// have a Clopper-Pearson lower end of at least `confidence`: the observed frequency `k / n` held at a larger
/// sample. The lower end increases with the success count, so no `n'` below the crossing of the continuous
/// bound (a fractional success count `k n' / n`) can qualify; the integer search starts there.
fn samples_needed(k: u64, n: u64, confidence: f64) -> Option<u64> {
    let half = (1.0 - confidence) / 2.0;
    let continuous = |trials: u64| -> f64 {
        let successes = (u128::from(k) * u128::from(trials)) as f64 / n as f64;
        if successes <= 0.0 {
            return 0.0;
        }
        beta_quantile(half, successes, trials as f64 - successes + 1.0)
    };
    if continuous(NEEDED_SAMPLES_CAP) < confidence {
        return None;
    }
    let (mut below, mut above) = (0u64, NEEDED_SAMPLES_CAP);
    while above - below > 1 {
        let middle = below + (above - below) / 2;
        if continuous(middle) >= confidence {
            above = middle;
        } else {
            below = middle;
        }
    }
    let last = NEEDED_SAMPLES_CAP.min(above + NEEDED_SAMPLES_WINDOW);
    (above..=last).find(|&trials| {
        let successes = (u128::from(k) * u128::from(trials) / u128::from(n)) as u64;
        clopper_pearson(successes, trials, confidence).0 >= confidence
    })
}

/// What a result with no established class says about the samples it would need.
fn needed_samples_report(counts: &[u64; 5], confidence: f64) -> Value {
    let n: u64 = counts.iter().sum();
    for (class, name) in CLASSES.iter().enumerate().take(UNKNOWN) {
        let k = cumulative(counts, class);
        if k as f64 / n as f64 > confidence {
            let observed = k as f64 / n as f64;
            return match samples_needed(k, n, confidence) {
                Some(needed) => json!({
                    "status": "needs_more_samples",
                    "class": name,
                    "observed_frequency": observed,
                    "samples_needed": needed,
                }),
                None => json!({
                    "status": "not_reachable_within_cap",
                    "class": name,
                    "observed_frequency": observed,
                    "cap": NEEDED_SAMPLES_CAP,
                }),
            };
        }
    }
    json!({"status": "observed_frequency_cannot_establish_a_class"})
}

fn frequency(k: u64, n: u64, confidence: f64) -> Value {
    let (lower, upper) = clopper_pearson(k, n, confidence);
    json!({
        "count": k,
        "probability": k as f64 / n as f64,
        "lower": lower,
        "upper": upper,
    })
}

/// Percentiles of one table constraint's normalized sum over the samples that have an inventory, and how many
/// exceed the class boundary at 1.
fn constraint_report(key: &(usize, u8, u8), values: &[f64]) -> Value {
    let ordered = sorted(values);
    let percentiles: serde_json::Map<String, Value> = SUM_PERCENTILES
        .iter()
        .map(|(p, label)| (label.to_string(), json!(percentile_sorted(&ordered, *p))))
        .collect();
    let above = values.iter().filter(|sum| **sum > 1.0).count();
    json!({
        "target_class": CLASSES[key.0],
        "table": key.1,
        "column": if key.2 == 0 { Value::Null } else { json!(key.2) },
        "samples": values.len(),
        "percentiles": percentiles,
        "count_above_1": above,
        "fraction_above_1": above as f64 / values.len() as f64,
    })
}

fn target_report(nominal: &Value, tally: &Tally, n: u64, confidence: f64) -> Value {
    let classes: serde_json::Map<String, Value> = CLASSES
        .iter()
        .zip(tally.counts)
        .map(|(label, count)| (label.to_string(), frequency(count, n, confidence)))
        .collect();
    let at_most: serde_json::Map<String, Value> = CLASSES[..UNKNOWN]
        .iter()
        .enumerate()
        .map(|(class, label)| {
            (
                label.to_string(),
                frequency(cumulative(&tally.counts, class), n, confidence),
            )
        })
        .collect();
    let established = established_class(&tally.counts, confidence);
    let failed: Vec<Value> = tally
        .failed
        .iter()
        .map(|(sample, error)| json!({"sample": sample, "error": error}))
        .collect();
    let constraints: Vec<Value> = tally
        .sums
        .iter()
        .filter(|(_, values)| !values.is_empty())
        .map(|(key, values)| constraint_report(key, values))
        .collect();
    json!({
        "step": nominal["step"],
        "t_s": nominal["t_s"],
        "nominal": nominal,
        "samples": n,
        "failed_samples": failed,
        "classes": classes,
        "at_most_class": at_most,
        "established_class": established.map_or("not_established", |class| CLASSES[class]),
        "needed_samples": if established.is_some() { Value::Null } else { needed_samples_report(&tally.counts, confidence) },
        "constraints": constraints,
    })
}

/// For each class, the earliest target step (smallest step number) at which it is the established class.
fn earliest_established(established: &[(u64, Option<usize>)]) -> Value {
    let mut earliest = serde_json::Map::new();
    for (class, label) in CLASSES[..UNKNOWN].iter().enumerate() {
        let step = established
            .iter()
            .filter(|(_, found)| *found == Some(class))
            .map(|(step, _)| *step)
            .min();
        earliest.insert(label.to_string(), json!(step));
    }
    Value::Object(earliest)
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
                "actinv-waste-uncertainty-{}-{}",
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

    fn spec() -> Value {
        json!({
            "schema": SPEC_SCHEMA,
            "problem": "problem.json",
            "rules": "us-nrc-10cfr61.55-v1",
            "targets": [1],
            "nuclide_properties": "from_decay_data",
            "components": [{
                "id": "part", "mass_g": 2.5, "displaced_volume_cm3": 1.0,
                "waste_type": "activated_metal",
                "external_tritium": {"status": "not_applicable"},
            }],
            "sampling": {"channels": {"decay_constants": true}, "samples": 100, "seed": 7},
        })
    }

    fn refused(spec: &Value) -> String {
        run_doc(&spec.to_string(), Path::new(".")).unwrap_err()
    }

    fn problem() -> Spec {
        Spec::from_json(
            &json!({
                "spec": "actinv-spec-1",
                "library": {"path": "activation.npz"},
                "decay": {"primary": "decay.endf"},
                "material": {"basis": "wt_percent", "composition": {"FE": 100.0}},
                "spectrum": {"structure": "custom", "boundaries_eV": [1.0, 2.0], "flux_per_group": [1.0]},
                "schedule": [{"dt": "1 s", "flux": 1.0}, {"dt": "1 d", "flux": 0.0}],
            })
            .to_string(),
        )
        .unwrap()
    }

    fn problem_error(edit: impl FnOnce(&mut Spec)) -> String {
        let validated = validate(&spec().to_string(), Path::new(".")).unwrap();
        let mut problem = problem();
        edit(&mut problem);
        check_problem(&problem, &validated).unwrap_err()
    }

    #[test]
    fn all_samples_in_class_a_establish_it_at_72_not_71() {
        assert_eq!(established_class(&[72, 0, 0, 0, 0], 0.95), Some(0));
        assert_eq!(established_class(&[71, 0, 0, 0, 0], 0.95), None);
        assert_eq!(min_trials_all_successes(0.95), 72);
        // a class is the first of A, B, C, above Class C whose cumulative lower bound reaches the level
        assert_eq!(established_class(&[0, 80, 0, 0, 0], 0.95), Some(1));
        assert_eq!(established_class(&[0, 0, 0, 80, 0], 0.95), Some(3));
        assert_eq!(established_class(&[40, 40, 0, 0, 0], 0.95), Some(1));
        // a higher level needs more samples
        assert_eq!(established_class(&[100, 0, 0, 0, 0], 0.99), None);
        assert_eq!(established_class(&[528, 0, 0, 0, 0], 0.99), Some(0));
    }

    #[test]
    fn failed_and_unknown_samples_only_weaken_an_established_class() {
        let mut tally = Tally::default();
        tally.counts[0] = 71;
        tally.fail(71, "solve failed");
        assert_eq!(tally.counts, [71, 0, 0, 0, 1]);
        assert_eq!(tally.failed, vec![(71, "solve failed".to_string())]);
        // 71 of 72 in class A: not established, and not established as any class either
        assert_eq!(established_class(&tally.counts, 0.95), None);
        let all_good = [72, 0, 0, 0, 0];
        assert_eq!(established_class(&all_good, 0.95), Some(0));
        // an unknown sample is in the denominator but in no cumulative count
        let report = target_report(&json!({"step": 1, "t_s": 5.0}), &tally, 72, 0.95);
        assert_eq!(report["classes"]["unknown"]["count"], 1);
        assert_eq!(report["at_most_class"]["above_class_c"]["count"], 71);
        assert_eq!(report["established_class"], "not_established");
        assert_eq!(report["failed_samples"][0]["sample"], 71);
        assert_eq!(report["failed_samples"][0]["error"], "solve failed");
    }

    #[test]
    fn a_recorded_classification_is_counted_with_its_constraints() {
        let mut tally = Tally::default();
        let target = json!({
            "class": "B",
            "evaluation": {"constraints": [
                {"target_class": "A", "table": 1, "column": null, "normalized_sum": 2.5},
                {"target_class": "B", "table": 2, "column": 3, "normalized_sum": 0.4},
            ]},
        });
        tally.add(0, &target);
        tally.add(
            1,
            &json!({"class": "unknown", "evaluation": {"constraints": []}}),
        );
        tally.add(2, &json!({"class": "nonsense"}));
        assert_eq!(tally.counts, [0, 1, 0, 0, 2]);
        assert_eq!(tally.failed.len(), 1);
        assert_eq!(tally.sums[&(0, 1, 0)], vec![2.5]);
        assert_eq!(tally.sums[&(1, 2, 3)], vec![0.4]);
    }

    #[test]
    fn needed_samples_are_the_smallest_that_would_establish() {
        // every sample agrees: the 72-sample minimum
        assert_eq!(samples_needed(71, 71, 0.95), Some(72));
        // a frequency of 0.97 needs far more; the answer is the first count that works
        let needed = samples_needed(97, 100, 0.95).unwrap();
        let qualifies = |trials: u64| clopper_pearson(97 * trials / 100, trials, 0.95).0 >= 0.95;
        assert!(qualifies(needed));
        assert!((1..needed).all(|trials| !qualifies(trials)));
        // a frequency at or below the level never establishes
        assert_eq!(samples_needed(95, 100, 0.95), None);
        let report = needed_samples_report(&[90, 0, 0, 0, 10], 0.95);
        assert_eq!(
            report["status"],
            "observed_frequency_cannot_establish_a_class"
        );
        let report = needed_samples_report(&[0, 70, 0, 0, 1], 0.95);
        assert_eq!(report["status"], "needs_more_samples");
        assert_eq!(report["class"], "B");
        assert_eq!(
            report["samples_needed"],
            samples_needed(70, 71, 0.95).unwrap()
        );
        // an established class reports no sample need
        let tally = Tally {
            counts: [72, 0, 0, 0, 0],
            ..Tally::default()
        };
        let report = target_report(&json!({"step": 2, "t_s": 1.0}), &tally, 72, 0.95);
        assert_eq!(report["established_class"], "A");
        assert!(report["needed_samples"].is_null());
        assert_eq!(report["classes"]["A"]["probability"], 1.0);
        assert_eq!(report["classes"]["A"]["upper"], 1.0);
        assert_eq!(report["at_most_class"]["C"]["count"], 72);
    }

    #[test]
    fn constraint_percentiles_are_nearest_rank_and_count_the_exceedances() {
        let values: Vec<f64> = (1..=40).rev().map(|i| i as f64 / 20.0).collect();
        let report = constraint_report(&(2, 2, 0), &values);
        assert_eq!(report["target_class"], "C");
        assert_eq!(report["table"], 2);
        assert!(report["column"].is_null());
        assert_eq!(report["samples"], 40);
        // rank ceil(40 p / 100): 2, 20 and 38 of the ascending values i / 20
        assert_eq!(report["percentiles"]["5"], 0.1);
        assert_eq!(report["percentiles"]["50"], 1.0);
        assert_eq!(report["percentiles"]["95"], 1.9);
        // values above 1: i = 21..=40
        assert_eq!(report["count_above_1"], 20);
        assert_eq!(report["fraction_above_1"], 0.5);
        let report = constraint_report(&(0, 1, 2), &[0.5]);
        assert_eq!(report["column"], 2);
        assert_eq!(report["count_above_1"], 0);
    }

    #[test]
    fn earliest_step_per_established_class() {
        let found = earliest_established(&[(4, Some(1)), (2, Some(1)), (9, Some(0)), (1, None)]);
        assert_eq!(found["A"], 9);
        assert_eq!(found["B"], 2);
        assert!(found["C"].is_null());
        assert!(found["above_class_c"].is_null());
    }

    #[test]
    fn intervals_use_the_confidence_level() {
        let tally = Tally {
            counts: [15, 5, 0, 0, 0],
            ..Tally::default()
        };
        let at_95 = target_report(&json!({"step": 1}), &tally, 20, 0.95);
        let at_99 = target_report(&json!({"step": 1}), &tally, 20, 0.99);
        // 5 of 20 in class B at 95 %: (0.0865715, 0.4910459)
        let b = &at_95["classes"]["B"];
        assert!((b["lower"].as_f64().unwrap() - 0.0865715).abs() < 1e-6);
        assert!((b["upper"].as_f64().unwrap() - 0.4910459).abs() < 1e-6);
        assert!(at_99["classes"]["B"]["lower"].as_f64() < b["lower"].as_f64());
        assert_eq!(at_95["classes"]["A"]["probability"], 0.75);
        assert_eq!(at_95["at_most_class"]["B"]["count"], 20);
    }

    #[test]
    fn compact_run_keeps_only_target_steps() {
        let result = json!({
            "entry_point": "study", "mode": "coupled", "ms": 3.0,
            "steps": [
                {"step": 1, "t_s": 1.0, "activity_Bq_per_g": {"Fe55": 2.0}, "inventory": []},
                {"step": 2, "t_s": 9.0, "activity_Bq_per_g": {"Fe55": 1.0, "Co60": 0.0}},
            ],
        });
        let compact = compact_run(&result, &[2]).unwrap();
        assert_eq!(compact["steps"].as_array().unwrap().len(), 1);
        assert_eq!(compact["steps"][0]["step"], 2);
        assert_eq!(compact["steps"][0]["activity_Bq_per_g"]["Fe55"], 1.0);
        assert_eq!(compact["entry_point"], "study");
        let mut active = BTreeSet::new();
        add_active(&compact, &mut active);
        assert_eq!(active, BTreeSet::from(["Fe55".to_string()]));
        assert!(compact_run(&result, &[3])
            .unwrap_err()
            .contains("no step 3"));
        assert!(compact_run(&json!({}), &[1])
            .unwrap_err()
            .contains("no steps"));
    }

    #[test]
    fn spec_refusals_have_their_own_messages() {
        let mut few = spec();
        few["sampling"]["samples"] = json!(19);
        let message = refused(&few);
        assert!(
            message.contains("sampling.samples must be at least 20"),
            "{message}"
        );
        assert!(message.contains("got 19"), "{message}");

        let mut many = spec();
        many["sampling"]["samples"] = json!(MAX_ROBUSTNESS_SAMPLES + 1);
        assert!(refused(&many).contains("robustness_too_large"));

        for level in [json!(0.0), json!(1.0), json!(-0.5), json!(1.5)] {
            let mut bad = spec();
            bad["confidence_level"] = level;
            assert!(
                refused(&bad).contains("confidence_level must be strictly between zero and one")
            );
        }

        let mut none = spec();
        none["sampling"]["channels"] = json!({});
        assert!(refused(&none).contains("sampling enables no channel"));
        let mut zero = spec();
        zero["sampling"]["channels"] = json!({"flux_rel_std": 0.0});
        assert!(refused(&zero).contains("sampling enables no channel"));

        let mut negative = spec();
        negative["sampling"]["channels"] = json!({"flux_rel_std": -1.0});
        assert!(refused(&negative).contains("sampling.channels.flux_rel_std"));

        let mut no_covariance = spec();
        no_covariance["sampling"]["channels"] = json!({"cross_section_mf33": true});
        assert!(refused(&no_covariance).contains("sampling.covariance"));

        let mut extra = spec();
        extra["surprise"] = json!(1);
        assert!(refused(&extra).contains("unknown field `surprise`"));
        let mut extra_sampling = spec();
        extra_sampling["sampling"]["surprise"] = json!(1);
        assert!(refused(&extra_sampling).contains("unknown field `surprise`"));

        let mut schema = spec();
        schema["schema"] = json!("actinv-waste-uncertainty-spec-0");
        assert!(refused(&schema).contains("unsupported waste uncertainty schema"));

        let mut properties = spec();
        properties["nuclide_properties"] = json!("from_a_guess");
        assert!(refused(&properties).contains("from_a_guess"));

        let mut targets = spec();
        targets["targets"] = json!([1, 1]);
        assert!(refused(&targets).contains("duplicate"));

        let mut components = spec();
        components["components"] = json!([]);
        assert!(refused(&components).contains("exactly one component"));
        let mut cells = spec();
        cells["components"][0]["cells"] = json!([{"id": "a", "mass_g": 2.5}]);
        assert!(refused(&cells).contains("must not declare cells"));

        let mut tritium = spec();
        tritium["components"][0]["external_tritium"] = json!({
            "status": "declared", "source": "retention", "excludes_activation": true,
            "activity_bq": {"2": 1.0},
        });
        assert!(refused(&tritium).contains("external_tritium has no activity_bq"));
    }

    #[test]
    fn problem_refusals_have_their_own_messages() {
        assert!(problem_error(|p| p.options.gas = true).contains("options.gas"));
        assert!(problem_error(|p| {
            p.schedule[0].library = Some(LibraryRef {
                path: "other.npz".into(),
                sha256: None,
            })
        })
        .contains("switches library or projectile"));
        assert!(problem_error(|p| p.schedule[1].power_W_per_g = Some(1.0)).contains("power-driven"));
        assert!(
            problem_error(|p| p.schedule[1].wall_loading_MW_m2 = Some(1.0))
                .contains("wall_loading_MW_m2 or partition")
        );
        assert!(
            problem_error(|p| p.options.rate_scale = Some(BTreeMap::new()))
                .contains("rate_scale, decay_scale or yield_scale")
        );
        assert!(
            problem_error(|p| p.schedule[0].spectrum = Some(p.spectrum.clone()))
                .contains("its own spectrum")
        );
        let mut far = spec();
        far["targets"] = json!([3]);
        let validated = validate(&far.to_string(), Path::new(".")).unwrap();
        let message = check_problem(&problem(), &validated).unwrap_err();
        assert!(message.contains("target step 3"), "{message}");
        assert!(message.contains("2 steps"), "{message}");

        let mut yields = spec();
        yields["sampling"]["channels"] = json!({"fission_yields": true});
        let validated = validate(&yields.to_string(), Path::new(".")).unwrap();
        let message = check_problem(&problem(), &validated).unwrap_err();
        assert!(message.contains("fission_yields.files"), "{message}");
    }

    #[test]
    fn a_refused_run_writes_nothing() {
        let scratch = Scratch::new();
        let mut bad = spec();
        bad["sampling"]["samples"] = json!(5);
        bad["sampling"]["keep_samples"] = json!("kept");
        let path = scratch.0.join("spec.json");
        std::fs::write(&path, bad.to_string()).unwrap();
        let output = scratch.0.join("out.json");
        let error = run(path.to_str().unwrap(), output.to_str()).unwrap_err();
        assert!(error.contains("at least 20"));
        assert!(!scratch.0.join("kept").exists());
        assert!(!output.exists());
        // a spec that passes the data-free checks but names a missing problem also writes nothing
        let mut missing = spec();
        missing["sampling"]["keep_samples"] = json!("kept");
        std::fs::write(&path, missing.to_string()).unwrap();
        let error = run(path.to_str().unwrap(), output.to_str()).unwrap_err();
        assert!(error.contains("cannot read problem"), "{error}");
        assert!(!scratch.0.join("kept").exists());
        assert!(!output.exists());
    }
}
