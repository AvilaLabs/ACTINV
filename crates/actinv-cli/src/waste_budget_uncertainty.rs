//! `actinv waste budget uncertainty` — impurity budgets under nuclear-data uncertainty (P146).
//!
//! The nominal budget (`actinv waste budget`) is computed unchanged. Each correlated draw of the sampled
//! channels is then applied to every element solve of the budget at once, so the element responses of one
//! sample share one data set, and the nominal interval algorithm gives that sample's supremum for each
//! impurity and for the joint specification scale factor. The budget at confidence `gamma` is the k-th
//! smallest per-sample supremum, k being the largest index for which the Clopper-Pearson lower end of
//! `(n - k + 1) / n` reaches `gamma`: an order-statistic bound, exact for any distribution of the supremum.
//! See `COVERAGE_STATEMENT` and `NOT_CLAIMED` for what it does not claim.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use actinv_core::run::PreparedCache;
use actinv_core::sample_stats::{min_trials_all_successes, order_index};
use actinv_core::spec::{LibraryRef, Spec};
use actinv_core::study::{self, FactorBlock, RobustChannels, MAX_ROBUSTNESS_SAMPLES};
use actinv_core::waste::{evaluate_component, Coverage};
use serde::Deserialize;
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};

use crate::budget;
use crate::waste;
use crate::waste_budget::{
    self, basis_elements, class_at_most, element_basis, external_at, external_basis,
    full_composition, intervals_from_basis, merge_activity, step_activities, Basis, ConstraintKey,
    Interval, Setup,
};

const SPEC_SCHEMA: &str = "actinv-waste-budget-uncertainty-spec-1";
const RESULT_SCHEMA: &str = "actinv-waste-budget-uncertainty-1";
/// Fewest samples a sampling block may ask for.
pub(crate) const MIN_SAMPLES: u64 = 20;
const DEFAULT_CONFIDENCE: f64 = 0.95;
/// Name of the joint specification scale factor among the quantities.
const JOINT: &str = "joint_scale_factor";
/// Sorted ranks around the reported rank whose samples get verification points: `k - 2` to `k + 2`.
const RANK_WINDOW: usize = 2;
const BELOW: f64 = 0.99;
const ABOVE: f64 = 1.01;

const COVERAGE_STATEMENT: &str = "The budgets are conditional on the sampled channels listed here. They exclude MF=32 and MF=40 covariance, cross-channel correlation, composition, geometry, model and flux-systematic uncertainty, except where a listed channel samples it, and they assume the activation response is linear in composition for each sample, as the nominal budget does. Measured coverage of these channels on the FNS corpus is below nominal (docs/DATA_LIMITATIONS.md), so a budget can be optimistic.";

const NOT_CLAIMED: [&str; 4] = [
    "linearity in composition assumed per sample",
    "composition, geometry and model uncertainty not sampled",
    "two-sided budgets not claimed",
    "not a composition tolerance, procurement specification, package class or disposal acceptance",
];

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct UncertaintySpec {
    schema: String,
    /// path (relative to the spec) of an `actinv-waste-budget-1` document, or the document itself
    budget: Value,
    #[serde(default)]
    confidence_level: Option<f64>,
    sampling: SamplingSpec,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct SamplingSpec {
    #[serde(default)]
    pub(crate) channels: RobustChannels,
    #[serde(default)]
    pub(crate) covariance: Option<LibraryRef>,
    pub(crate) samples: u64,
    pub(crate) seed: u64,
    #[serde(default)]
    pub(crate) keep_samples: Option<String>,
}

/// A spec that passed every check that needs no solve.
struct Validated {
    spec: UncertaintySpec,
    samples: u32,
    confidence: f64,
    /// index of the reported order statistic
    k: usize,
    /// fewest samples for which any budget exists at the confidence level
    minimum_samples: u64,
    /// the covariance sidecar with its path resolved against the spec's folder
    covariance: Option<LibraryRef>,
    budget_text: String,
    /// folder the budget document's own relative paths resolve against
    budget_dir: PathBuf,
}

/// What one quantity (an impurity, or the joint scale factor) came to in one sample.
#[derive(Clone, Debug)]
struct SampleValue {
    /// bound, capped, failed, infeasible or no_response
    status: &'static str,
    lower: Option<f64>,
    /// the interval's own upper edge, as the nominal algorithm computes it
    raw_supremum: Option<f64>,
    /// the supremum as it enters the order statistic: zero for a failed or infeasible sample, the cap for a
    /// capped one, none for a quantity with no response
    effective: Option<f64>,
    binding: Option<ConstraintKey>,
    positive_lower: bool,
}

impl SampleValue {
    fn failed() -> Self {
        SampleValue {
            status: "failed",
            lower: None,
            raw_supremum: None,
            effective: Some(0.0),
            binding: None,
            positive_lower: false,
        }
    }

    fn from_interval(interval: &Interval) -> Self {
        let (status, effective) = if !interval.feasible {
            ("infeasible", Some(0.0))
        } else {
            match interval.status {
                "no_response" => ("no_response", None),
                "composition_bound" => ("capped", Some(interval.upper.value)),
                _ => ("bound", Some(interval.upper.value)),
            }
        };
        SampleValue {
            status,
            lower: Some(interval.lower.value),
            raw_supremum: Some(interval.upper.value),
            effective,
            binding: interval.upper.binding,
            positive_lower: interval.feasible && interval.lower.value > 0.0,
        }
    }

    /// The interval as a kept sample file records it.
    fn json(&self) -> Value {
        json!({
            "lower": self.lower,
            "supremum": self.raw_supremum,
            "status": self.status,
            "binding": self.binding.as_ref().map(|key| json!({"step": key.step, "table": key.table, "column": key.column})),
        })
    }
}

/// One sample's solves.
struct SampleRun {
    /// per quantity: the impurities, then [`JOINT`]
    values: BTreeMap<String, SampleValue>,
    /// the kept-sample document
    keep: Value,
    /// (element, error); the element is none for a failure after the element solves
    failures: Vec<(Option<String>, String)>,
}

/// The result of one quantity over all samples.
struct QuantityResult {
    report: Value,
    /// the reported budget; none unless the quantity is established
    budget: Option<f64>,
    /// ascending (supremum, sample, capped); a quantity with no response sorts last as infinity
    sorted: Vec<(f64, usize, bool)>,
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
        .map_err(|error| format!("cannot read waste budget uncertainty spec {path}: {error}"))?;
    let result = run_doc(&text, base)?;
    waste::output_doc(&result, output)?;
    Ok(result)
}

/// The process exit code for an uncertainty result: 3 when a verification point failed (the result is still
/// written), 0 otherwise.
pub fn exit_code(doc: &Value) -> i32 {
    if doc["verification_pass"] == true {
        0
    } else {
        3
    }
}

/// Remove the keys that start with an underscore (comments), at the top level and in the sampling block.
fn strip_comment_keys(value: &mut Value) {
    if let Value::Object(map) = value {
        map.retain(|key, _| !key.starts_with('_'));
        if let Some(Value::Object(sampling)) = map.get_mut("sampling") {
            sampling.retain(|key, _| !key.starts_with('_'));
        }
    }
}

/// The covariance sidecar as the sampler reads it: catalog references resolved, a relative path taken against
/// the spec's folder.
pub(crate) fn resolve_covariance(
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

/// Every refusal of the spec itself, before anything is solved, drawn or written. The budget document's own
/// refusals follow in `waste_budget::setup`.
fn validate(text: &str, base: &Path) -> Result<Validated, String> {
    let mut value: Value = serde_json::from_str(text)
        .map_err(|error| format!("cannot parse waste budget uncertainty spec: {error}"))?;
    strip_comment_keys(&mut value);
    let spec: UncertaintySpec = serde_json::from_value(value)
        .map_err(|error| format!("cannot parse waste budget uncertainty spec: {error}"))?;
    if spec.schema != SPEC_SCHEMA {
        return Err(format!(
            "unsupported waste budget uncertainty schema '{}'",
            spec.schema
        ));
    }
    if !spec.sampling.channels.composition_rel_std.is_empty() {
        return Err(
            "composition channel is refused: composition is what the budget solves for".into(),
        );
    }
    let samples = spec.sampling.samples;
    if !(MIN_SAMPLES..=u64::from(MAX_ROBUSTNESS_SAMPLES)).contains(&samples) {
        return Err(format!(
            "samples must be between {MIN_SAMPLES} and {MAX_ROBUSTNESS_SAMPLES}"
        ));
    }
    let confidence = spec.confidence_level.unwrap_or(DEFAULT_CONFIDENCE);
    if !(confidence > 0.0 && confidence < 1.0) {
        return Err("confidence_level must be in (0, 1)".into());
    }
    let covariance = resolve_covariance(spec.sampling.covariance.as_ref(), base)?;
    // the fission-yield declaration lives in the base spec; its check is repeated once the spec is built
    study::validate_channels(
        &spec.sampling.channels,
        covariance.as_ref(),
        true,
        "sampling",
    )?;
    let minimum_samples = min_trials_all_successes(confidence);
    let Some(k) = order_index(samples, confidence) else {
        return Err(format!(
            "{samples} samples cannot establish any budget at confidence {confidence}; at least {minimum_samples} are needed"
        ));
    };
    let (budget_text, budget_dir) = match &spec.budget {
        Value::String(relative) => {
            let path = base.join(relative);
            let text = std::fs::read_to_string(&path)
                .map_err(|error| format!("cannot read waste budget {}: {error}", path.display()))?;
            let dir = path.parent().unwrap_or(base).to_path_buf();
            (text, dir)
        }
        document @ Value::Object(_) => (
            serde_json::to_string(document).map_err(|error| error.to_string())?,
            base.to_path_buf(),
        ),
        _ => return Err("budget must be a path string or an embedded object".into()),
    };
    Ok(Validated {
        samples: samples as u32,
        confidence,
        k: k as usize,
        minimum_samples,
        covariance,
        budget_text,
        budget_dir,
        spec,
    })
}

/// Refusals about the base spec itself: what the sampler cannot draw for, or would overwrite.
fn check_base_spec(spec: &Spec, validated: &Validated) -> Result<(), String> {
    if spec.options.gas {
        return Err(
            "the base spec enables options.gas, which waste budget uncertainty sampling does not support"
                .into(),
        );
    }
    if spec.options.rate_scale.is_some()
        || spec.options.decay_scale.is_some()
        || spec.options.yield_scale.is_some()
    {
        return Err("the base spec declares options.rate_scale, decay_scale or yield_scale, which the sampler writes itself".into());
    }
    for (index, step) in spec.schedule.iter().enumerate() {
        let number = index + 1;
        if step.library.is_some() || step.projectile.is_some() {
            return Err(format!(
                "schedule step {number} switches library or projectile, which waste budget uncertainty sampling does not support"
            ));
        }
        if step.wall_loading_MW_m2.is_some() || step.partition.is_some() {
            return Err(format!(
                "schedule step {number} declares wall_loading_MW_m2 or partition, which waste budget uncertainty sampling does not support"
            ));
        }
        if step.spectrum.is_some() {
            return Err(format!(
                "schedule step {number} declares its own spectrum; the sampler perturbs only the base spectrum"
            ));
        }
    }
    study::validate_channels(
        &validated.spec.sampling.channels,
        validated.covariance.as_ref(),
        !spec.fission_yields.files.is_empty(),
        "sampling",
    )
}

/// Read the interval of a quantity from the nominal budget result: its supremum when it has one, and whether
/// its lower bound is positive.
fn nominal_view(interval: &Value, scale_factor: bool) -> (Option<f64>, bool) {
    let (lower_key, upper_key) = if scale_factor {
        ("lower_scale_factor", "upper_supremum_scale_factor")
    } else {
        ("lower_wt_pct", "upper_supremum_wt_pct")
    };
    let feasible = interval["feasible"].as_bool().unwrap_or(false);
    let responds = feasible && interval["status"].as_str() != Some("no_response");
    let supremum = if responds {
        interval[upper_key].as_f64()
    } else {
        None
    };
    let positive_lower = feasible
        && interval[lower_key]
            .as_f64()
            .is_some_and(|lower| lower > 0.0);
    (supremum, positive_lower)
}

/// Collect one quantity over the samples: the order statistic, the status, and the tallies.
fn quantity_result(
    scale_factor: bool,
    values: &[SampleValue],
    nominal: &Value,
    k: usize,
    confidence: f64,
    class: &str,
) -> QuantityResult {
    let (nominal_supremum, nominal_positive) = nominal_view(nominal, scale_factor);
    let mut sorted: Vec<(f64, usize, bool)> = values
        .iter()
        .enumerate()
        .map(|(sample, value)| {
            (
                value.effective.unwrap_or(f64::INFINITY),
                sample,
                value.status == "capped",
            )
        })
        .collect();
    sorted.sort_by(|a, b| a.0.total_cmp(&b.0).then(a.1.cmp(&b.1)));
    let positive_samples = values.iter().filter(|value| value.positive_lower).count();
    let mut counts: BTreeMap<&str, u64> = BTreeMap::from([
        ("bound", 0),
        ("capped", 0),
        ("failed", 0),
        ("infeasible", 0),
        ("no_response", 0),
    ]);
    let mut tally: BTreeMap<ConstraintKey, u64> = BTreeMap::new();
    for value in values {
        *counts.entry(value.status).or_default() += 1;
        if let Some(key) = value.binding {
            *tally.entry(key).or_default() += 1;
        }
    }
    let at_k = sorted.get(k - 1).map_or(f64::INFINITY, |entry| entry.0);
    let (status, reason) = if positive_samples > 0 || nominal_positive {
        ("not_established", Some("positive_lower_bound"))
    } else if !at_k.is_finite() {
        ("unbounded", Some("no_response"))
    } else {
        ("established", None)
    };
    let budget = (status == "established").then_some(at_k);
    let by_sample: Vec<Value> = values
        .iter()
        .map(|value| value.effective.map_or(Value::Null, |x| json!(x)))
        .collect();
    let ascending: Vec<Value> = sorted
        .iter()
        .map(|entry| {
            if entry.0.is_finite() {
                json!(entry.0)
            } else {
                Value::Null
            }
        })
        .collect();
    let binding_tally: Vec<Value> = tally
        .iter()
        .map(|(key, count)| json!({"step": key.step, "table": key.table, "column": key.column, "count": count}))
        .collect();
    let report = json!({
        "unit": if scale_factor { "scale_factor" } else { "wt_pct" },
        "status": status,
        "reason": reason,
        "positive_lower_bound_samples": positive_samples,
        "nominal_positive_lower_bound": nominal_positive,
        "nominal_supremum": nominal_supremum,
        "budget": budget,
        "suprema_by_sample": by_sample,
        "suprema_sorted": ascending,
        "sample_status": counts,
        "binding_tally": binding_tally,
        "keep_probability_statement": format!("x below the budget keeps the class at most {class} in at least {confidence} of data draws, at confidence {confidence}"),
    });
    QuantityResult {
        report,
        budget,
        sorted,
    }
}

/// The class of a full-material run at every target, and whether every one is at most the target class.
fn classify_run(setup: &Setup, run: &Value) -> Result<(BTreeMap<String, Value>, bool), String> {
    let b = &setup.b;
    let mut classes = BTreeMap::new();
    let mut inside = true;
    for &step in &b.targets {
        let row = budget::step_of(run, step)?;
        let mut activity_bq: BTreeMap<String, f64> = step_activities(row)?
            .into_iter()
            .map(|(nuclide, value)| (nuclide, value * b.mass_g))
            .collect();
        let (h3, unknown) = external_at(&b.external_tritium, step)?;
        if unknown {
            return Err("class budget refuses required-but-undeclared external tritium".into());
        }
        if h3 > 0.0 {
            merge_activity(&mut activity_bq, "H3", h3)?;
        }
        let evaluation = evaluate_component(
            &setup.rules,
            b.waste_type,
            setup.geometry,
            &activity_bq,
            &b.nuclide_properties,
        )?;
        if evaluation.coverage != Coverage::Complete {
            return Err(format!(
                "verification point has incomplete coverage at step {step}"
            ));
        }
        inside &= class_at_most(evaluation.class, b.target_class);
        classes.insert(
            step.to_string(),
            serde_json::to_value(evaluation.class).map_err(|error| error.to_string())?,
        );
    }
    Ok((classes, inside))
}

/// The composition of a verification point: the impurity alone at `value` (the others at specification), or
/// every impurity scaled by `value` for the joint scale factor.
fn point_composition(
    setup: &Setup,
    quantity: &str,
    value: f64,
) -> Result<BTreeMap<String, f64>, String> {
    let b = &setup.b;
    let mut impurities = b.impurities.clone();
    if quantity == JOINT {
        impurities.values_mut().for_each(|weight| *weight *= value);
    } else {
        impurities.insert(quantity.to_string(), value);
    }
    full_composition(&b.balance, &b.matrix, &impurities)
}

/// One verification point: the full material at `value` of `quantity`, solved with the sample's own factors.
#[allow(clippy::too_many_arguments)]
fn verify_point(
    setup: &Setup,
    block: &FactorBlock,
    cache: &mut PreparedCache,
    quantity: &str,
    sample: usize,
    rank: usize,
    point: &str,
    value: f64,
) -> Result<Option<(Value, bool)>, String> {
    // a point beyond the balance bound is no composition: it cannot be classified
    let composition = match point_composition(setup, quantity, value) {
        Ok(composition) => composition,
        Err(_) => return Ok(None),
    };
    let title = format!("waste budget uncertainty verification {quantity} sample {sample}");
    let mut spec = budget::element_spec(&setup.base, &composition, &title)?;
    study::apply_factor_block(block, &mut spec);
    let (run, _ms) = budget::solve_spec(&spec, &title, cache)?;
    let (classes, inside) = classify_run(setup, &run)?;
    Ok(Some((
        json!({"quantity": quantity, "sample": sample, "rank": rank, "point": point, "value": value, "classes": classes, "inside": inside}),
        inside,
    )))
}

pub(crate) fn run_doc(text: &str, base: &Path) -> Result<Value, String> {
    let validated = validate(text, base)?;
    let setup = waste_budget::setup(&validated.budget_text, &validated.budget_dir)?;
    let elements = basis_elements(&setup.b);
    // the full material at specification: the plan the factors are drawn from, and the template of each
    // element solve
    let full = budget::element_spec(
        &setup.base,
        &full_composition(&setup.b.balance, &setup.b.matrix, &setup.b.impurities)?,
        "waste budget uncertainty full material",
    )?;
    check_base_spec(&full, &validated)?;
    let mut element_specs: BTreeMap<String, Spec> = BTreeMap::new();
    for element in &elements {
        element_specs.insert(
            element.clone(),
            budget::element_spec(
                &setup.base,
                &BTreeMap::from([(element.clone(), 100.0)]),
                &format!("waste budget uncertainty basis {element}"),
            )?,
        );
    }
    let (blocks, mut coverage) = study::draw_factor_blocks(
        &validated.spec.sampling.channels,
        validated.covariance.as_ref(),
        &full,
        Path::new("."),
        validated.samples,
        validated.spec.sampling.seed,
    )?;
    let external = external_basis(&setup)?;
    let nominal = waste_budget::run_doc(&validated.budget_text, &validated.budget_dir)?;

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

    let mut cache = PreparedCache::new();
    let mut quantity_names: Vec<String> = setup.b.impurities.keys().cloned().collect();
    quantity_names.push(JOINT.to_string());
    let mut per_quantity: BTreeMap<String, Vec<SampleValue>> = quantity_names
        .iter()
        .map(|name| (name.clone(), Vec::with_capacity(blocks.len())))
        .collect();
    let mut failed_samples: Vec<Value> = Vec::new();
    for (index, block) in blocks.iter().enumerate() {
        let sample = solve_sample(
            index,
            block,
            &setup,
            &element_specs,
            &external,
            &mut cache,
            &quantity_names,
        );
        for (element, error) in &sample.failures {
            failed_samples.push(json!({"sample": index, "element": element, "error": error}));
        }
        for (name, value) in sample.values {
            per_quantity
                .get_mut(&name)
                .expect("every quantity has a sample list")
                .push(value);
        }
        if let Some(dir) = &keep_dir {
            let path = dir.join(format!("sample_{index:04}.json"));
            let text =
                serde_json::to_string_pretty(&sample.keep).map_err(|error| error.to_string())?;
            std::fs::write(&path, format!("{text}\n"))
                .map_err(|error| format!("cannot write {}: {error}", path.display()))?;
        }
    }

    let class = serde_json::to_value(setup.b.target_class)
        .ok()
        .and_then(|value| value.as_str().map(str::to_string))
        .unwrap_or_default();
    let mut results: Vec<(String, QuantityResult)> = Vec::with_capacity(quantity_names.len());
    for name in &quantity_names {
        let scale_factor = name == JOINT;
        let nominal_interval = if scale_factor {
            &nominal["joint_specification_margin"]
        } else {
            &nominal["impurities"][name.as_str()]["others_at_spec"]
        };
        results.push((
            name.clone(),
            quantity_result(
                scale_factor,
                &per_quantity[name],
                nominal_interval,
                validated.k,
                validated.confidence,
                &class,
            ),
        ));
    }

    let mut verification: Vec<Value> = Vec::new();
    let mut skipped: Vec<Value> = Vec::new();
    let mut verification_pass = true;
    for (name, result) in &results {
        let Some(budget) = result.budget else {
            continue;
        };
        let n = result.sorted.len();
        // (rank from one, point, value): the samples around the reported rank at their own suprema, then
        // every sample at the reported rank or above at 0.99 times the budget
        let mut points: Vec<(usize, &str, f64)> = Vec::new();
        let first = validated.k.saturating_sub(RANK_WINDOW).max(1);
        for rank in first..=(validated.k + RANK_WINDOW).min(n) {
            let (supremum, sample, capped) = result.sorted[rank - 1];
            if supremum.is_finite() && supremum > 0.0 {
                points.push((rank, "0.99_supremum", BELOW * supremum));
                if !capped {
                    points.push((rank, "1.01_supremum", ABOVE * supremum));
                } else {
                    // no constraint binds a capped supremum and the point above it leaves no balance
                    skipped.push(json!({"quantity": name, "sample": sample, "rank": rank, "point": "1.01_supremum", "value": ABOVE * supremum, "reason": "capped supremum: the balance limit binds, not a class constraint"}));
                }
            }
        }
        if budget > 0.0 {
            for rank in validated.k..=n {
                points.push((rank, "0.99_budget", BELOW * budget));
            }
        }
        for (rank, point, value) in points {
            let sample = result.sorted[rank - 1].1;
            match verify_point(
                &setup,
                &blocks[sample],
                &mut cache,
                name,
                sample,
                rank,
                point,
                value,
            ) {
                Ok(Some((row, inside))) => {
                    verification_pass &= inside == (point != "1.01_supremum");
                    verification.push(row);
                }
                Ok(None) => skipped.push(json!({"quantity": name, "sample": sample, "rank": rank, "point": point, "value": value, "reason": "the point leaves no positive balance"})),
                Err(error) => {
                    verification_pass = false;
                    verification.push(json!({"quantity": name, "sample": sample, "rank": rank, "point": point, "value": value, "classes": null, "inside": false, "error": error}));
                }
            }
        }
    }

    let quantities: Map<String, Value> = results
        .into_iter()
        .map(|(name, result)| (name, result.report))
        .collect();
    if let Value::Object(map) = &mut coverage {
        map.insert("statement".into(), json!(COVERAGE_STATEMENT));
    }
    Ok(json!({
        "schema": RESULT_SCHEMA,
        "spec_sha256": sha256(text.as_bytes()),
        "rule_pack_sha256": setup.rules.sha256,
        "target_class": class,
        "confidence_level": validated.confidence,
        "samples": validated.samples,
        "seed": validated.spec.sampling.seed,
        "order_statistic": {"k": validated.k, "n": validated.samples, "minimum_samples": validated.minimum_samples},
        "nominal": nominal,
        "quantities": quantities,
        "failed_samples": failed_samples,
        "verification": verification,
        "verification_skipped": skipped,
        "verification_pass": verification_pass,
        "coverage": coverage,
        "not_claimed": NOT_CLAIMED,
    }))
}

/// Solve every element of the budget for one sample, each with the sample's factor block, and run the nominal
/// interval algorithm on the resulting basis. A sample with a failed element solve (or a failed interval
/// computation) has every quantity failed: it counts as a supremum of zero.
fn solve_sample(
    index: usize,
    block: &FactorBlock,
    setup: &Setup,
    element_specs: &BTreeMap<String, Spec>,
    external: &BTreeMap<ConstraintKey, f64>,
    cache: &mut PreparedCache,
    quantity_names: &[String],
) -> SampleRun {
    let mut basis: BTreeMap<String, Basis> = BTreeMap::new();
    let mut elements = Map::new();
    let mut failures: Vec<(Option<String>, String)> = Vec::new();
    for (element, template) in element_specs {
        let mut spec = template.clone();
        study::apply_factor_block(block, &mut spec);
        // the factors as the solved spec carries them
        let applied = FactorBlock::from_spec(&spec, block.flux_factor);
        let title = format!("waste budget uncertainty sample {index} element {element}");
        let outcome = budget::solve_spec(&spec, &title, cache)
            .and_then(|(run, _ms)| element_basis(setup, element, &run));
        match outcome {
            Ok((pure, record)) => {
                let mut steps = Map::new();
                for (step, activity) in record["steps"].as_object().into_iter().flatten() {
                    steps.insert(
                        step.clone(),
                        json!({"t_s": record["times_s"][step.as_str()], "activity_bq_per_g": activity}),
                    );
                }
                elements.insert(
                    element.clone(),
                    json!({
                        "factors": applied,
                        "error": null,
                        "steps": steps,
                        "class_constraint_normalized_sums": record["class_constraint_normalized_sums"],
                    }),
                );
                basis.insert(element.clone(), pure);
            }
            Err(error) => {
                elements.insert(
                    element.clone(),
                    json!({"factors": applied, "error": error, "steps": null, "class_constraint_normalized_sums": null}),
                );
                failures.push((Some(element.clone()), error));
            }
        }
    }
    let mut values: BTreeMap<String, SampleValue> = BTreeMap::new();
    if failures.is_empty() {
        match intervals_from_basis(setup, &basis, external, false) {
            Ok(intervals) => {
                for (name, _kind, interval) in &intervals.by_impurity {
                    values.insert(name.clone(), SampleValue::from_interval(interval));
                }
                values.insert(
                    JOINT.to_string(),
                    SampleValue::from_interval(&intervals.joint),
                );
            }
            Err(error) => failures.push((None, format!("interval computation: {error}"))),
        }
    }
    if values.is_empty() {
        for name in quantity_names {
            values.insert(name.clone(), SampleValue::failed());
        }
    }
    let intervals: Map<String, Value> = values
        .iter()
        .map(|(name, value)| (name.clone(), value.json()))
        .collect();
    let keep = json!({
        "sample": index,
        "factors": block,
        "elements": elements,
        "intervals": intervals,
    });
    SampleRun {
        values,
        keep,
        failures,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use actinv_core::sample_stats::clopper_pearson;

    #[test]
    fn failed_verification_exits_three_and_a_pass_exits_zero() {
        assert_eq!(exit_code(&json!({"verification_pass":true})), 0);
        assert_eq!(exit_code(&json!({"verification_pass":false})), 3);
        assert_eq!(exit_code(&json!({})), 3);
    }
    use std::sync::atomic::{AtomicUsize, Ordering};

    struct Scratch(PathBuf);

    impl Scratch {
        fn new() -> Self {
            static NEXT: AtomicUsize = AtomicUsize::new(0);
            let path = std::env::temp_dir().join(format!(
                "actinv-waste-budget-uncertainty-{}-{}",
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

    fn budget_doc() -> Value {
        json!({
            "schema": "actinv-waste-budget-1",
            "base_spec": {
                "spec": "actinv-spec-1",
                "library": {"path": "activation.npz"},
                "decay": {"primary": "decay.endf"},
                "material": {"basis": "wt_percent", "composition": {"FE": 100.0}},
                "spectrum": {"structure": "custom", "boundaries_eV": [1.0, 2.0], "flux_per_group": [1.0]},
                "schedule": [{"dt": "1 s", "flux": 1.0}, {"dt": "1 d", "flux": 0.0}],
            },
            "balance": "Fe",
            "matrix": {"Cr": 18.0},
            "impurities": {"Co": 0.01},
            "targets": [2],
            "rules": "us-nrc-10cfr61.55-v1",
            "target_class": "A",
            "mass_g": 2.5,
            "displaced_volume_cm3": 1.0,
            "waste_type": "activated_metal",
            "nuclide_properties": {},
            "external_tritium": {"status": "not_applicable"},
        })
    }

    fn spec() -> Value {
        json!({
            "schema": SPEC_SCHEMA,
            "budget": budget_doc(),
            "sampling": {"channels": {"decay_constants": true}, "samples": 100, "seed": 7},
        })
    }

    fn refused(spec: &Value) -> String {
        run_doc(&spec.to_string(), Path::new(".")).unwrap_err()
    }

    fn bound(x: f64, step: u64, table: u8, column: Option<u8>) -> SampleValue {
        SampleValue {
            status: "bound",
            lower: Some(0.0),
            raw_supremum: Some(x),
            effective: Some(x),
            binding: Some(ConstraintKey {
                step,
                table,
                column,
            }),
            positive_lower: false,
        }
    }

    fn nominal(supremum: f64) -> Value {
        json!({
            "feasible": true, "status": "limit", "no_response": false,
            "lower_wt_pct": 0.0, "upper_supremum_wt_pct": supremum,
            "lower_scale_factor": 0.0, "upper_supremum_scale_factor": supremum,
        })
    }

    #[test]
    fn the_order_index_is_one_at_72_and_absent_at_71() {
        assert_eq!(min_trials_all_successes(0.95), 72);
        assert_eq!(order_index(72, 0.95), Some(1));
        assert_eq!(order_index(71, 0.95), None);
        assert_eq!(order_index(20, 0.95), None);
        // a larger sample allows a larger index, never a smaller one
        let mut previous = 1;
        for n in 72..=300 {
            let k = order_index(n, 0.95).unwrap();
            assert!(k >= previous, "n = {n}");
            previous = k;
        }
        assert!(previous > 1);
    }

    #[test]
    fn the_order_index_agrees_with_a_brute_force_loop() {
        for (n, confidence) in [
            (200u64, 0.95),
            (1024, 0.95),
            (300, 0.9),
            (1024, 0.99),
            (528, 0.99),
        ] {
            // the largest k whose success count n - k + 1 has a lower end of at least the level
            let brute = (1..=n)
                .filter(|&k| clopper_pearson(n - k + 1, n, confidence).0 >= confidence)
                .max();
            assert_eq!(
                order_index(n, confidence),
                brute,
                "n = {n}, level {confidence}"
            );
        }
        assert_eq!(order_index(527, 0.99), None);
        assert_eq!(order_index(528, 0.99), Some(1));
    }

    #[test]
    fn the_budget_is_the_kth_smallest_supremum() {
        let n = 200usize;
        let k = order_index(n as u64, 0.95).unwrap() as usize;
        assert!(k > 1);
        // suprema 1..=200 in a scrambled order
        let values: Vec<SampleValue> = (0..n)
            .map(|i| bound(((i * 37) % n + 1) as f64, 12, 2, Some(1)))
            .collect();
        let result = quantity_result(false, &values, &nominal(100.0), k, 0.95, "A");
        assert_eq!(result.budget, Some(k as f64));
        assert_eq!(result.report["status"], "established");
        assert!(result.report["reason"].is_null());
        assert_eq!(result.report["budget"], k as f64);
        assert_eq!(result.report["unit"], "wt_pct");
        assert_eq!(result.report["nominal_supremum"], 100.0);
        let sorted = result.report["suprema_sorted"].as_array().unwrap();
        assert_eq!(sorted.len(), n);
        assert_eq!(sorted[0], 1.0);
        assert_eq!(sorted[n - 1], 200.0);
        // by sample keeps the draw order
        assert_eq!(result.report["suprema_by_sample"][0], 1.0);
        assert_eq!(result.report["suprema_by_sample"][1], 38.0);
        assert_eq!(result.report["sample_status"]["bound"], 200);
        assert_eq!(result.report["binding_tally"][0]["count"], 200);
        // the sample at the reported rank is the one the verification samples
        assert_eq!(result.sorted[k - 1].0, k as f64);
        let joint = quantity_result(true, &values, &nominal(100.0), k, 0.95, "B");
        assert_eq!(joint.report["unit"], "scale_factor");
        assert_eq!(
            joint.report["keep_probability_statement"],
            "x below the budget keeps the class at most B in at least 0.95 of data draws, at confidence 0.95"
        );
    }

    #[test]
    fn failed_and_infeasible_samples_count_as_zero_and_only_lower_the_budget() {
        let mut values: Vec<SampleValue> = (0..72)
            .map(|i| bound(10.0 + i as f64, 3, 1, None))
            .collect();
        let clean = quantity_result(false, &values, &nominal(10.0), 1, 0.95, "A");
        assert_eq!(clean.budget, Some(10.0));
        values[5] = SampleValue::failed();
        let failed = quantity_result(false, &values, &nominal(10.0), 1, 0.95, "A");
        assert_eq!(failed.budget, Some(0.0));
        assert_eq!(failed.report["status"], "established");
        assert_eq!(failed.report["sample_status"]["failed"], 1);
        assert_eq!(failed.report["suprema_by_sample"][5], 0.0);
        assert_eq!(failed.sorted[0], (0.0, 5, false));
        // an infeasible interval enters at zero too, with its own status
        let infeasible =
            SampleValue::from_interval(&interval(false, "infeasible", 0.0, 7.0, false));
        assert_eq!(infeasible.status, "infeasible");
        assert_eq!(infeasible.effective, Some(0.0));
        values[5] = infeasible;
        let result = quantity_result(false, &values, &nominal(10.0), 1, 0.95, "A");
        assert_eq!(result.budget, Some(0.0));
        assert_eq!(result.report["sample_status"]["infeasible"], 1);
        assert_eq!(result.report["sample_status"]["failed"], 0);
        // the failure appears in the sorted suprema as the smallest value
        assert_eq!(result.report["suprema_sorted"][0], 0.0);
    }

    fn interval(
        feasible: bool,
        status: &'static str,
        lower: f64,
        upper: f64,
        bound: bool,
    ) -> Interval {
        use crate::waste_budget::Edge;
        let edge = |value: f64, binding: Option<ConstraintKey>| Edge {
            value,
            open: true,
            binding,
            contributors: Vec::new(),
            contributor_count: 0,
        };
        Interval {
            lower: edge(lower, None),
            upper: edge(
                upper,
                bound.then_some(ConstraintKey {
                    step: 2,
                    table: 1,
                    column: None,
                }),
            ),
            feasible,
            status,
            no_response: status == "no_response",
        }
    }

    #[test]
    fn interval_statuses_map_to_sample_statuses() {
        let limit = SampleValue::from_interval(&interval(true, "limit", 0.0, 4.0, true));
        assert_eq!((limit.status, limit.effective), ("bound", Some(4.0)));
        assert!(limit.binding.is_some() && !limit.positive_lower);
        let capped =
            SampleValue::from_interval(&interval(true, "composition_bound", 0.0, 80.0, false));
        assert_eq!((capped.status, capped.effective), ("capped", Some(80.0)));
        assert!(capped.binding.is_none());
        let none = SampleValue::from_interval(&interval(true, "no_response", 0.0, 80.0, false));
        assert_eq!((none.status, none.effective), ("no_response", None));
        let singleton = SampleValue::from_interval(&interval(true, "singleton", 0.0, 0.0, true));
        assert_eq!(
            (singleton.status, singleton.effective),
            ("bound", Some(0.0))
        );
        let lower = SampleValue::from_interval(&interval(true, "limit", 0.5, 4.0, true));
        assert!(lower.positive_lower);
        // an infeasible interval has no usable lower bound
        let infeasible =
            SampleValue::from_interval(&interval(false, "infeasible", 0.5, 0.2, false));
        assert!(!infeasible.positive_lower);
        let kept = limit.json();
        assert_eq!(kept["status"], "bound");
        assert_eq!(kept["supremum"], 4.0);
        assert_eq!(kept["binding"]["table"], 1);
        assert!(kept["binding"]["column"].is_null());
        assert!(SampleValue::failed().json()["supremum"].is_null());
    }

    #[test]
    fn a_positive_lower_bound_is_not_established() {
        let mut values: Vec<SampleValue> =
            (0..72).map(|i| bound(5.0 + i as f64, 3, 1, None)).collect();
        values[10].positive_lower = true;
        values[40].positive_lower = true;
        let result = quantity_result(false, &values, &nominal(5.0), 1, 0.95, "A");
        assert_eq!(result.budget, None);
        assert_eq!(result.report["status"], "not_established");
        assert_eq!(result.report["reason"], "positive_lower_bound");
        assert_eq!(result.report["positive_lower_bound_samples"], 2);
        assert!(result.report["budget"].is_null());
        // the nominal alone is enough
        let values: Vec<SampleValue> = (0..72).map(|i| bound(5.0 + i as f64, 3, 1, None)).collect();
        let mut positive = nominal(5.0);
        positive["lower_wt_pct"] = json!(0.2);
        let result = quantity_result(false, &values, &positive, 1, 0.95, "A");
        assert_eq!(result.report["status"], "not_established");
        assert_eq!(result.report["positive_lower_bound_samples"], 0);
        assert_eq!(result.report["nominal_positive_lower_bound"], true);
        // the joint scale factor reads its own keys
        let mut joint = nominal(5.0);
        joint["lower_scale_factor"] = json!(0.2);
        let result = quantity_result(true, &values, &joint, 1, 0.95, "A");
        assert_eq!(result.report["status"], "not_established");
        let result = quantity_result(false, &values, &joint, 1, 0.95, "A");
        assert_eq!(result.report["status"], "established");
    }

    #[test]
    fn no_response_everywhere_is_unbounded_and_a_cap_enters_at_the_cap() {
        let none = SampleValue::from_interval(&interval(true, "no_response", 0.0, 80.0, false));
        let values = vec![none.clone(); 72];
        let mut flat = nominal(0.0);
        flat["status"] = json!("no_response");
        let result = quantity_result(false, &values, &flat, 1, 0.95, "A");
        assert_eq!(result.report["status"], "unbounded");
        assert!(result.report["budget"].is_null());
        assert!(result.report["nominal_supremum"].is_null());
        assert!(result.report["suprema_by_sample"][0].is_null());
        assert!(result.report["suprema_sorted"][71].is_null());
        assert_eq!(result.report["sample_status"]["no_response"], 72);
        // a capped supremum enters at the cap, and the cap can be the budget
        let capped =
            SampleValue::from_interval(&interval(true, "composition_bound", 0.0, 80.0, false));
        let result = quantity_result(false, &vec![capped; 72], &nominal(80.0), 1, 0.95, "A");
        assert_eq!(result.budget, Some(80.0));
        assert_eq!(result.report["sample_status"]["capped"], 72);
        assert_eq!(result.sorted[0], (80.0, 0, true));
        assert_eq!(result.report["binding_tally"], json!([]));
        // unbounded samples sort after every bound one, so they do not set a small budget
        let mut mixed = vec![none; 72];
        mixed[3] = bound(2.0, 1, 1, None);
        let result = quantity_result(false, &mixed, &nominal(2.0), 1, 0.95, "A");
        assert_eq!(result.budget, Some(2.0));
    }

    #[test]
    fn binding_constraints_are_tallied_by_step_table_and_column() {
        let mut values = Vec::new();
        for (count, step, table, column) in [
            (3, 12, 2, Some(1)),
            (2, 10, 2, Some(3)),
            (4, 10, 1, None),
            (1, 10, 2, None),
            (2, 12, 2, None),
        ] {
            for _ in 0..count {
                values.push(bound(1.0, step, table, column));
            }
        }
        values.push(SampleValue::failed());
        let result = quantity_result(false, &values, &nominal(1.0), 1, 0.95, "A");
        let tally = result.report["binding_tally"].as_array().unwrap();
        let rows: Vec<(u64, u64, Option<u64>, u64)> = tally
            .iter()
            .map(|row| {
                (
                    row["step"].as_u64().unwrap(),
                    row["table"].as_u64().unwrap(),
                    row["column"].as_u64(),
                    row["count"].as_u64().unwrap(),
                )
            })
            .collect();
        // sorted by step, table, then column with the all-column entry first
        assert_eq!(
            rows,
            vec![
                (10, 1, None, 4),
                (10, 2, None, 1),
                (10, 2, Some(3), 2),
                (12, 2, None, 2),
                (12, 2, Some(1), 3),
            ]
        );
        // a failed sample binds nothing
        assert_eq!(rows.iter().map(|row| row.3).sum::<u64>(), 12);
        assert_eq!(result.report["sample_status"]["failed"], 1);
        assert!(tally[0]["column"].is_null());
    }

    #[test]
    fn spec_refusals_have_their_own_messages() {
        let mut seen = Vec::new();
        let mut check = |spec: Value, needle: &str| {
            let message = refused(&spec);
            assert!(message.contains(needle), "{message}");
            assert!(!seen.contains(&message), "repeated refusal: {message}");
            seen.push(message);
        };

        let mut schema = spec();
        schema["schema"] = json!("actinv-waste-budget-uncertainty-spec-0");
        check(
            schema,
            "unsupported waste budget uncertainty schema 'actinv-waste-budget-uncertainty-spec-0'",
        );

        let mut composition = spec();
        composition["sampling"]["channels"] = json!({"composition_rel_std": {"Fe": 0.1}});
        check(
            composition,
            "composition channel is refused: composition is what the budget solves for",
        );
        let mut zero_composition = spec();
        zero_composition["sampling"]["channels"] =
            json!({"decay_constants": true, "composition_rel_std": {"Co": 0.0}});
        assert!(refused(&zero_composition).contains("composition channel is refused"));

        let mut none = spec();
        none["sampling"]["channels"] = json!({});
        check(none, "sampling enables no channel");
        let mut zero_flux = spec();
        zero_flux["sampling"]["channels"] = json!({"flux_rel_std": 0.0});
        assert!(refused(&zero_flux).contains("sampling enables no channel"));

        let mut few = spec();
        few["sampling"]["samples"] = json!(19);
        check(few, "samples must be between 20 and 4096");
        let mut many = spec();
        many["sampling"]["samples"] = json!(MAX_ROBUSTNESS_SAMPLES + 1);
        assert!(refused(&many).contains("samples must be between 20 and 4096"));

        for level in [json!(0.0), json!(1.0), json!(-0.5), json!(1.5)] {
            let mut bad = spec();
            bad["confidence_level"] = level;
            assert!(refused(&bad).contains("confidence_level must be in (0, 1)"));
        }
        let mut level = spec();
        level["confidence_level"] = json!(0.0);
        check(level, "confidence_level must be in (0, 1)");

        let mut small = spec();
        small["sampling"]["samples"] = json!(71);
        check(
            small,
            "71 samples cannot establish any budget at confidence 0.95; at least 72 are needed",
        );
        let mut small_99 = spec();
        small_99["sampling"]["samples"] = json!(400);
        small_99["confidence_level"] = json!(0.99);
        check(
            small_99,
            "400 samples cannot establish any budget at confidence 0.99; at least 528 are needed",
        );

        let mut no_covariance = spec();
        no_covariance["sampling"]["channels"] = json!({"cross_section_mf33": true});
        check(no_covariance, "sampling.covariance");

        let mut extra = spec();
        extra["surprise"] = json!(1);
        check(extra, "unknown field `surprise`");
        let mut extra_sampling = spec();
        extra_sampling["sampling"]["surprise"] = json!(1);
        assert!(refused(&extra_sampling).contains("unknown field `surprise`"));

        let mut wrong_budget = spec();
        wrong_budget["budget"] = json!(5);
        check(
            wrong_budget,
            "budget must be a path string or an embedded object",
        );
        let mut missing_budget = spec();
        missing_budget["budget"] = json!("no-such-budget.json");
        check(missing_budget, "cannot read waste budget");

        // everything the nominal budget refuses is refused with its own message
        let mut no_impurities = spec();
        no_impurities["budget"]["impurities"] = json!({});
        check(no_impurities, "impurities must name at least one element");
        let mut budget_schema = spec();
        budget_schema["budget"]["schema"] = json!("actinv-waste-budget-0");
        check(budget_schema, "unsupported waste budget schema");
        let mut power = spec();
        power["budget"]["base_spec"]["schedule"][0]["power_W_per_g"] = json!(1.0);
        check(power, "budget refuses power-driven steps");
    }

    #[test]
    fn comment_keys_are_ignored() {
        let mut commented = spec();
        commented["_note"] = json!("not a field");
        commented["sampling"]["_note"] = json!("neither");
        assert!(validate(&commented.to_string(), Path::new(".")).is_ok());
        let validated = validate(&spec().to_string(), Path::new(".")).unwrap();
        assert_eq!(validated.samples, 100);
        assert_eq!(validated.minimum_samples, 72);
        assert_eq!(validated.k as u64, order_index(100, 0.95).unwrap());
        assert_eq!(validated.confidence, 0.95);
    }

    #[test]
    fn base_spec_refusals_have_their_own_messages() {
        let validated = validate(&spec().to_string(), Path::new(".")).unwrap();
        let base = || -> Spec { Spec::from_json(&budget_doc()["base_spec"].to_string()).unwrap() };
        assert!(check_base_spec(&base(), &validated).is_ok());
        let mut gas = base();
        gas.options.gas = true;
        assert!(check_base_spec(&gas, &validated)
            .unwrap_err()
            .contains("options.gas"));
        let mut scaled = base();
        scaled.options.decay_scale = Some(BTreeMap::new());
        assert!(check_base_spec(&scaled, &validated)
            .unwrap_err()
            .contains("rate_scale, decay_scale or yield_scale"));
        let mut library = base();
        library.schedule[0].library = Some(LibraryRef {
            path: "other.npz".into(),
            sha256: None,
        });
        assert!(check_base_spec(&library, &validated)
            .unwrap_err()
            .contains("switches library or projectile"));
        let mut wall = base();
        wall.schedule[1].wall_loading_MW_m2 = Some(1.0);
        assert!(check_base_spec(&wall, &validated)
            .unwrap_err()
            .contains("wall_loading_MW_m2 or partition"));
        let mut own = base();
        own.schedule[0].spectrum = Some(own.spectrum.clone());
        assert!(check_base_spec(&own, &validated)
            .unwrap_err()
            .contains("its own spectrum"));
        let mut yields = spec();
        yields["sampling"]["channels"] = json!({"fission_yields": true});
        let validated = validate(&yields.to_string(), Path::new(".")).unwrap();
        assert!(check_base_spec(&base(), &validated)
            .unwrap_err()
            .contains("fission_yields.files"));
    }

    #[test]
    fn a_refused_run_writes_nothing() {
        let scratch = Scratch::new();
        let path = scratch.0.join("spec.json");
        let output = scratch.0.join("out.json");
        let edits: [fn(&mut Value); 4] = [
            |spec| spec["sampling"]["samples"] = json!(5),
            |spec| spec["sampling"]["samples"] = json!(71),
            |spec| spec["budget"]["impurities"] = json!({}),
            |spec| spec["confidence_level"] = json!(2.0),
        ];
        for edit in edits {
            let mut bad = spec();
            bad["sampling"]["keep_samples"] = json!("kept");
            edit(&mut bad);
            std::fs::write(&path, bad.to_string()).unwrap();
            assert!(run(path.to_str().unwrap(), output.to_str()).is_err());
            assert!(!scratch.0.join("kept").exists());
            assert!(!output.exists());
        }
        // a spec naming a budget file that is missing refuses the same way
        let mut missing = spec();
        missing["budget"] = json!("absent.json");
        missing["sampling"]["keep_samples"] = json!("kept");
        std::fs::write(&path, missing.to_string()).unwrap();
        let error = run(path.to_str().unwrap(), output.to_str()).unwrap_err();
        assert!(error.contains("cannot read waste budget"), "{error}");
        assert!(!scratch.0.join("kept").exists());
        assert!(!output.exists());
    }
}
