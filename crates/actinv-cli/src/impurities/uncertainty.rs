//! Nuclear-data uncertainty bands for the impurity screen (P158).
//!
//! The screen of `actinv impurities` is linear in composition for one data set. Each correlated draw of the
//! sampled channels is therefore applied to every element solve of that sample at once, and P157's algorithm runs
//! on the sample's element responses unchanged. The factor blocks come from the sampling plan of the full alloy at
//! the plan composition (the matrix, every impurity at its range maximum and the balance taking the remainder).
//!
//! Over the samples the bands are order statistics. With `k` the largest index for which the Clopper-Pearson
//! lower end of `(n - k + 1) / n` reaches the confidence level, a value is reported by its median and its k-th
//! largest per-sample value, and a limit by its k-th smallest per-sample limit. See `COVERAGE_STATEMENT` and
//! `NOT_CLAIMED` for what the bands do not claim.

use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

use actinv_core::photon::PhotonResponse;
use actinv_core::run::{PreparedCache, PreparedRun};
use actinv_core::sample_stats::{clopper_pearson, percentile_sorted, sorted};
use actinv_core::spec::Spec;
use actinv_core::study::{self, FactorBlock};
use actinv_core::waste::WasteClass;
use serde_json::{json, Map, Value};

use super::{
    analyse_scalar, class_basis, class_levels, class_name, comp_key, extract_target, linear_value,
    opt_num, ranking, value_dev, with_weight, ClassContext, ClassLevel, Comp, DoseModel, ElemRuns,
    Endpoint, EndpointKind, Evaluator, Frame, ImpurityResult, Limit, SamplingConfig, ScalarResult,
    INFEASIBLE, UNAVAILABLE, VERIFY_TOL,
};
use crate::{budget, waste_budget};

/// Sorted ranks around the reported rank whose samples get verification points: `k - 2` to `k + 2`.
const RANK_WINDOW: usize = 2;
const BELOW: f64 = 0.99;
const ABOVE: f64 = 1.01;
/// A value check's absolute floor, relative to the endpoint's scale in the sample (the largest of its
/// matrix-only, nominal and worst-case values at the step): P157's checker floor.
const VALUE_FLOOR: f64 = 1e-12;
/// Class names in the order the frequencies are reported.
const CLASS_NAMES: [&str; 6] = ["A", "B", "C", "above_class_c", "unknown", "failed"];

const COVERAGE_STATEMENT: &str = "The bands are conditional on the sampled channels listed here. They exclude MF=32 and MF=40 covariance, cross-channel correlation, composition, geometry, model and flux-systematic uncertainty, except where a listed channel samples it, and they assume the activation response is linear in composition for each sample, as the nominal screen does. Measured coverage of these channels on the FNS corpus is below nominal (docs/DATA_LIMITATIONS.md), so a bound can be optimistic.";

const NOT_CLAIMED: [&str; 5] = [
    "linearity in composition assumed per sample",
    "composition, geometry, flux-shape and model uncertainty not sampled",
    "the upper bound on a value and the lower bound on a limit are one-sided order-statistic tolerance bounds, not two-sided intervals and not a design margin",
    "measured coverage of the sampled channels is below nominal, so a bound can be optimistic",
    "not a composition tolerance, procurement specification, package class or disposal acceptance",
];

// ---------------------------------------------------------------------------------------------
// Band arithmetic
// ---------------------------------------------------------------------------------------------

/// What one limit came to in one sample.
#[derive(Clone, Debug)]
pub(super) struct LimSample {
    /// bound, capped, failed, infeasible, unavailable or no_response
    pub status: &'static str,
    /// The limit as it enters the order statistic: 0 for a failed, infeasible or unavailable sample, the cap
    /// for a capped one, none (infinity) for a limit with no response.
    pub effective: Option<f64>,
    /// The sample's lower bound is positive (the class needs at least some of the impurity).
    pub positive_lower: bool,
}

impl LimSample {
    pub fn failed() -> Self {
        LimSample {
            status: "failed",
            effective: Some(0.0),
            positive_lower: false,
        }
    }

    pub fn from_limit(l: &Limit) -> Self {
        let (status, effective) = match (l.value, l.capped_at) {
            (Some(x), _) => ("bound", Some(x)),
            (None, Some(cap)) => ("capped", Some(cap)),
            (None, None) if l.status == INFEASIBLE => ("infeasible", Some(0.0)),
            (None, None) if l.status == UNAVAILABLE => ("unavailable", Some(0.0)),
            (None, None) => ("no_response", None),
        };
        LimSample {
            status,
            effective,
            positive_lower: false,
        }
    }

    pub fn from_interval(i: &waste_budget::Interval) -> Self {
        let (status, effective) = if !i.feasible {
            ("infeasible", Some(0.0))
        } else {
            match i.status {
                "no_response" => ("no_response", None),
                "composition_bound" => ("capped", Some(i.upper.value)),
                _ => ("bound", Some(i.upper.value)),
            }
        };
        LimSample {
            status,
            effective,
            positive_lower: i.feasible && i.lower.value > 0.0,
        }
    }

    fn json(&self) -> Value {
        json!({
            "status": self.status,
            "effective": opt_num(self.effective),
            "positive_lower_bound": self.positive_lower,
        })
    }
}

/// A value over the samples: the median and the k-th largest.
pub(super) struct ValueBand {
    pub json: Value,
    /// the upper bound, also in `json`; the tests read it here
    #[cfg_attr(not(test), allow(dead_code))]
    pub upper: Option<f64>,
    /// the sample that holds the upper bound
    pub upper_sample: Option<usize>,
}

/// The median (nearest rank, over the samples that have a value) and the upper bound, the k-th largest of
/// all samples, a sample without a value counting as infinity (an unbounded sample can only widen the bound).
pub(super) fn value_band(values: &[Option<f64>], k: usize) -> ValueBand {
    let n = values.len();
    let mut order: Vec<(f64, usize)> = values
        .iter()
        .enumerate()
        .map(|(i, v)| (v.unwrap_or(f64::INFINITY), i))
        .collect();
    order.sort_by(|a, b| a.0.total_cmp(&b.0).then(a.1.cmp(&b.1)));
    let valid: Vec<f64> = values.iter().flatten().copied().collect();
    let median = (!valid.is_empty()).then(|| percentile_sorted(&sorted(&valid), 50.0));
    let at_k = n.checked_sub(k).and_then(|i| order.get(i)).copied();
    let (upper, upper_sample) = match at_k {
        Some((v, s)) if v.is_finite() => (Some(v), Some(s)),
        _ => (None, None),
    };
    let json = json!({
        "median": opt_num(median),
        "upper_bound": opt_num(upper),
        "status": if upper.is_some() { "bounded" } else { "unbounded" },
        "order_statistic_rank": k,
        "valid_samples": valid.len(),
        "upper_bound_sample": upper_sample,
        "by_sample": values.iter().map(|v| opt_num(*v)).collect::<Vec<_>>(),
    });
    ValueBand {
        json,
        upper,
        upper_sample,
    }
}

/// A limit over the samples: the k-th smallest.
pub(super) struct LimitBand {
    pub json: Value,
    /// the reported bound; none unless the limit is established
    pub bound: Option<f64>,
    /// ascending (effective limit, sample); a limit with no response sorts last as infinity
    pub sorted: Vec<(f64, usize)>,
    pub statuses: Vec<&'static str>,
}

/// The k-th smallest per-sample limit and its status: `not_established` when any sample (or the nominal data)
/// has a positive lower bound, `unbounded` when the k-th smallest has no response, else `established`.
pub(super) fn limit_band(
    samples: &[LimSample],
    k: usize,
    nominal_positive: bool,
    unit: &str,
) -> LimitBand {
    let mut order: Vec<(f64, usize)> = samples
        .iter()
        .enumerate()
        .map(|(i, s)| (s.effective.unwrap_or(f64::INFINITY), i))
        .collect();
    order.sort_by(|a, b| a.0.total_cmp(&b.0).then(a.1.cmp(&b.1)));
    let positive = samples.iter().filter(|s| s.positive_lower).count();
    let mut counts: BTreeMap<&str, u64> = BTreeMap::from([
        ("bound", 0),
        ("capped", 0),
        ("failed", 0),
        ("infeasible", 0),
        ("no_response", 0),
        ("unavailable", 0),
    ]);
    for s in samples {
        *counts.entry(s.status).or_default() += 1;
    }
    let at_k = k
        .checked_sub(1)
        .and_then(|i| order.get(i))
        .map_or(f64::INFINITY, |e| e.0);
    let (status, reason) = if positive > 0 || nominal_positive {
        ("not_established", Some("positive_lower_bound"))
    } else if !at_k.is_finite() {
        ("unbounded", Some("no_response"))
    } else {
        ("established", None)
    };
    let bound = (status == "established").then_some(at_k);
    let json = json!({
        "unit": unit,
        "status": status,
        "reason": reason,
        "bound": opt_num(bound),
        "order_statistic_rank": k,
        "bound_sample": k.checked_sub(1).and_then(|i| order.get(i)).filter(|e| e.0.is_finite()).map(|e| e.1),
        "positive_lower_bound_samples": positive,
        "nominal_positive_lower_bound": nominal_positive,
        "sample_status": counts,
        "by_sample": samples.iter().map(|s| opt_num(s.effective)).collect::<Vec<_>>(),
        "sorted": order.iter().map(|e| if e.0.is_finite() { json!(e.0) } else { Value::Null }).collect::<Vec<_>>(),
    });
    LimitBand {
        json,
        bound,
        sorted: order,
        statuses: samples.iter().map(|s| s.status).collect(),
    }
}

/// A count of `k` in `n` with its Clopper-Pearson interval at `confidence`.
pub(super) fn frequency_json(k: u64, n: u64, confidence: f64) -> Value {
    let (lower, upper) = clopper_pearson(k, n, confidence);
    json!({
        "count": k,
        "probability": if n == 0 { 0.0 } else { k as f64 / n as f64 },
        "lower": lower,
        "upper": upper,
    })
}

/// For each impurity, the number and fraction of the samples in which it is the largest contributor at
/// nominal; a failed sample (none) is in no count and in the denominator.
pub(super) fn ranking_json(largest: &[Option<String>], names: &[String]) -> Value {
    let n = largest.len();
    let mut fractions = Map::new();
    for e in names {
        let count = largest
            .iter()
            .filter(|x| x.as_deref() == Some(e.as_str()))
            .count();
        fractions.insert(
            e.clone(),
            json!({"count": count, "fraction": if n == 0 { 0.0 } else { count as f64 / n as f64 }}),
        );
    }
    json!({"samples": n, "fractions": fractions, "largest_by_sample": largest})
}

/// Class frequencies over the samples; a failed sample is its own entry and is at most no class.
pub(super) fn class_frequencies(classes: &[Option<WasteClass>], confidence: f64) -> Value {
    let n = classes.len() as u64;
    let mut counts: BTreeMap<&str, u64> = CLASS_NAMES.iter().map(|c| (*c, 0)).collect();
    for c in classes {
        *counts.entry(c.map_or("failed", class_name)).or_default() += 1;
    }
    let frequency: Map<String, Value> = CLASS_NAMES
        .iter()
        .map(|c| (c.to_string(), frequency_json(counts[c], n, confidence)))
        .collect();
    json!({
        "frequency": frequency,
        "by_sample": classes.iter().map(|c| c.map_or("failed", class_name)).collect::<Vec<_>>(),
    })
}

/// Samples whose class is at most the target class.
pub(super) fn meets_count(classes: &[Option<WasteClass>], target: WasteClass) -> u64 {
    classes
        .iter()
        .filter(|c| c.is_some_and(|c| waste_budget::class_at_most(c, target)))
        .count() as u64
}

// ---------------------------------------------------------------------------------------------
// One sample
// ---------------------------------------------------------------------------------------------

pub(super) struct ScreenInputs<'a> {
    pub frame: &'a Frame,
    pub endpoints: &'a [Endpoint],
    pub targets: &'a [u64],
    pub limits: &'a BTreeMap<String, f64>,
    pub response: Option<&'a PhotonResponse>,
    pub build_up: f64,
    pub class_cx: &'a [ClassContext],
}

/// P157's analysis of one scalar endpoint at one target, on any set of element runs.
fn scalar_at(
    inp: &ScreenInputs,
    ep: &Endpoint,
    runs: &ElemRuns,
    step: u64,
) -> Result<ScalarResult, String> {
    if ep.kind == EndpointKind::ContactProxy {
        let model = DoseModel {
            response: inp.response,
            build_up: inp.build_up,
            runs,
            step,
        };
        let f = |c: &Comp| model.value(c);
        analyse_scalar(inp.frame, &Evaluator::General(&f), ep.limit)
    } else {
        let r: BTreeMap<String, f64> = runs
            .iter()
            .map(|(e, m)| (e.clone(), linear_value(&ep.kind, &m[&step], inp.limits)))
            .collect();
        analyse_scalar(
            inp.frame,
            &Evaluator::Linear { r: &r, offset: 0.0 },
            ep.limit,
        )
    }
}

/// The limit samples of a class endpoint's per-impurity class limits: `<impurity>/alone`,
/// `<impurity>/others_at_nominal` and `joint_scale_factor`.
fn class_limit_samples(iv: &waste_budget::Intervals) -> BTreeMap<String, LimSample> {
    let mut out = BTreeMap::new();
    for (name, kind, interval) in &iv.by_impurity {
        let which = if *kind == "sole" {
            "alone"
        } else {
            "others_at_nominal"
        };
        out.insert(
            format!("{name}/{which}"),
            LimSample::from_interval(interval),
        );
    }
    out.insert(
        "joint_scale_factor".to_string(),
        LimSample::from_interval(&iv.joint),
    );
    out
}

/// P157's whole screen for one set of element runs.
pub(super) struct SampleScreen {
    /// (endpoint index, step) -> result, for the scalar endpoints
    pub scalar: BTreeMap<(usize, u64), ScalarResult>,
    /// (endpoint index, step) -> class levels
    pub class: BTreeMap<(usize, u64), ClassLevel>,
    /// endpoint index -> class limits
    pub class_limits: BTreeMap<usize, BTreeMap<String, LimSample>>,
}

pub(super) fn screen_runs(inp: &ScreenInputs, runs: &ElemRuns) -> Result<SampleScreen, String> {
    let mut out = SampleScreen {
        scalar: BTreeMap::new(),
        class: BTreeMap::new(),
        class_limits: BTreeMap::new(),
    };
    for (idx, ep) in inp.endpoints.iter().enumerate() {
        if let EndpointKind::WasteClass(_) = ep.kind {
            let cx = inp
                .class_cx
                .iter()
                .find(|c| c.endpoint == idx)
                .ok_or("missing class context")?;
            for &k in inp.targets {
                out.class
                    .insert((idx, k), class_levels(cx, inp.frame, runs, k)?);
            }
            let basis = class_basis(&cx.setup, runs)?;
            let iv = waste_budget::intervals_from_basis(&cx.setup, &basis, &cx.external, true)?;
            out.class_limits.insert(idx, class_limit_samples(&iv));
        } else {
            for &k in inp.targets {
                out.scalar.insert((idx, k), scalar_at(inp, ep, runs, k)?);
            }
        }
    }
    Ok(out)
}

fn limit_sample_json(l: &Limit) -> Value {
    json!({
        "value": opt_num(l.value),
        "status": l.status,
        "capped_at": opt_num(l.capped_at),
    })
}

fn scalar_sample_json(res: &ScalarResult) -> Value {
    let imps: Map<String, Value> = res
        .impurities
        .iter()
        .map(|(e, r)| {
            let mut m = json!({
                "gradient_per_wt_pct": opt_num(r.gradient),
                "contribution_nominal": opt_num(r.contribution_nominal),
            });
            if let (Some(a), Some(n), Some(w)) = (&r.alone, &r.others_nominal, &r.others_worst) {
                m["limits"] = json!({
                    "alone": limit_sample_json(a),
                    "others_at_nominal": limit_sample_json(n),
                    "others_at_worst": limit_sample_json(w),
                });
            }
            (e.clone(), m)
        })
        .collect();
    let mut out = json!({
        "matrix_only": opt_num(res.matrix_only),
        "nominal": opt_num(res.nominal),
        "worst_case": {"value": opt_num(res.worst), "weights_wt_pct": res.worst_weights},
        "impurities": imps,
    });
    if let Some(j) = &res.joint {
        out["joint_k"] = limit_sample_json(j);
    }
    out
}

/// A sample's screen as a kept sample file records it.
fn screen_json(screen: &SampleScreen, endpoints: &[Endpoint]) -> Value {
    let mut out = Map::new();
    for (idx, ep) in endpoints.iter().enumerate() {
        let mut steps = Map::new();
        for ((i, k), res) in &screen.scalar {
            if *i == idx {
                steps.insert(k.to_string(), scalar_sample_json(res));
            }
        }
        for ((i, k), level) in &screen.class {
            if *i == idx {
                steps.insert(
                    k.to_string(),
                    json!({
                        "nominal_class": class_name(level.nominal),
                        "worst_class": class_name(level.worst),
                        "contributions": level.contributions,
                    }),
                );
            }
        }
        let mut entry = json!({"steps": steps});
        if let Some(limits) = screen.class_limits.get(&idx) {
            let m: Map<String, Value> = limits.iter().map(|(k, v)| (k.clone(), v.json())).collect();
            entry["class_limits"] = Value::Object(m);
        }
        out.insert(ep.id.clone(), entry);
    }
    Value::Object(out)
}

fn responses_json(runs: &ElemRuns) -> Value {
    let mut out = Map::new();
    for (e, by_step) in runs {
        let mut steps = Map::new();
        for (k, t) in by_step {
            steps.insert(
                k.to_string(),
                json!({
                    "t_s": t.t_s,
                    "activity_Bq_per_g": t.activity,
                    "heat_W_per_g_total": t.heat_total,
                    "contact_proxy_Gy_h": t.photon.as_ref().and_then(|p| p.contact_proxy),
                }),
            );
        }
        out.insert(e.clone(), Value::Object(steps));
    }
    Value::Object(out)
}

struct SampleRun {
    /// the element runs; none when an element solve failed
    runs: Option<ElemRuns>,
    /// each element's applied factors and error, as the kept-sample document records them
    elements: Map<String, Value>,
    /// (element, error); the element is none for a failure after the element solves
    failures: Vec<(Option<String>, String)>,
}

/// Solve every element for one sample, each with the sample's factor block. A sample with a failed
/// solve has no runs: its limits enter at 0 and its values are unbounded.
fn solve_sample(
    index: usize,
    block: &FactorBlock,
    templates: &BTreeMap<String, Spec>,
    targets: &[u64],
    want_dose: bool,
    cache: &mut PreparedCache,
) -> SampleRun {
    let mut runs: ElemRuns = BTreeMap::new();
    let mut elements = Map::new();
    let mut failures: Vec<(Option<String>, String)> = Vec::new();
    for (element, template) in templates {
        let mut spec = template.clone();
        study::apply_factor_block(block, &mut spec);
        // the factors as the solved spec carries them
        let applied = FactorBlock::from_spec(&spec, block.flux_factor);
        let title = format!("impurities uncertainty sample {index} element {element}");
        let outcome = budget::solve_spec(&spec, &title, cache).and_then(|(res, _ms)| {
            let mut by_step = BTreeMap::new();
            for &k in targets {
                by_step.insert(k, extract_target(&res, k, want_dose)?);
            }
            Ok(by_step)
        });
        match outcome {
            Ok(by_step) => {
                elements.insert(element.clone(), json!({"factors": applied, "error": null}));
                runs.insert(element.clone(), by_step);
            }
            Err(error) => {
                elements.insert(
                    element.clone(),
                    json!({"factors": applied, "error": &error}),
                );
                failures.push((Some(element.clone()), error));
            }
        }
    }
    SampleRun {
        runs: failures.is_empty().then_some(runs),
        elements,
        failures,
    }
}

/// The class contexts for the sampled screens: each nominal context rebuilt with the properties of
/// every nuclide a sample makes active and the nominal run did not. The properties come from the
/// nominal decay data, so the Part 61 tables classify each nuclide as the nominal screen does.
fn sample_class_contexts(
    nominal: &[ClassContext],
    samples: &[SampleRun],
    prepared: Option<&PreparedRun>,
    dir: &Path,
) -> Result<Vec<ClassContext>, String> {
    let mut out = Vec::new();
    for cx in nominal {
        let missing: BTreeSet<String> = samples
            .iter()
            .filter_map(|s| s.runs.as_ref())
            .flat_map(|runs| runs.values())
            .flat_map(|by_step| by_step.values())
            .flat_map(|t| t.activity.keys())
            .filter(|n| !cx.setup.b.nuclide_properties.contains_key(*n))
            .cloned()
            .collect();
        let mut doc = cx.doc.clone();
        if !missing.is_empty() {
            let prepared =
                prepared.ok_or("no prepared nominal run for the sampled nuclide properties")?;
            let derived = crate::waste_composition_verify::derive_properties(prepared, &missing)?;
            let properties = doc["nuclide_properties"]
                .as_object_mut()
                .ok_or("class setup document without nuclide_properties")?;
            for (n, p) in derived {
                properties.insert(n, serde_json::to_value(p).map_err(|e| e.to_string())?);
            }
        }
        let setup = waste_budget::setup(&doc.to_string(), dir)?;
        let external = waste_budget::external_basis(&setup)?;
        out.push(ClassContext {
            endpoint: cx.endpoint,
            target: cx.target,
            setup,
            basis: cx.basis.clone(),
            external,
            doc,
        });
    }
    Ok(out)
}

// ---------------------------------------------------------------------------------------------
// Bands per endpoint and target
// ---------------------------------------------------------------------------------------------

fn column(
    col: &[Option<&ScalarResult>],
    f: impl Fn(&ScalarResult) -> Option<f64>,
) -> Vec<Option<f64>> {
    col.iter().map(|r| r.and_then(&f)).collect()
}

fn limit_column(
    col: &[Option<&ScalarResult>],
    element: &str,
    which: fn(&ImpurityResult) -> &Option<Limit>,
) -> Vec<LimSample> {
    col.iter()
        .map(|r| match r {
            Some(r) => which(&r.impurities[element])
                .as_ref()
                .map_or_else(LimSample::failed, LimSample::from_limit),
            None => LimSample::failed(),
        })
        .collect()
}

/// The impurity with the largest nominal contribution, when it has one.
fn largest_scalar(r: &ScalarResult) -> Option<String> {
    ranking(r)
        .into_iter()
        .next()
        .filter(|e| r.impurities[e].contribution_nominal.is_some())
}

/// The largest contributor of a class level, ties to the first name.
fn largest_class(level: &ClassLevel) -> Option<String> {
    let mut v: Vec<(&String, &f64)> = level.contributions.iter().collect();
    v.sort_by(|a, b| b.1.total_cmp(a.1).then(a.0.cmp(b.0)));
    v.first().map(|(e, _)| (*e).clone())
}

pub(super) struct ScalarBands {
    pub json: Value,
    /// role (`matrix_only`, `nominal`, `worst_case`) and its band
    pub values: Vec<(&'static str, ValueBand)>,
    pub contributions: BTreeMap<String, ValueBand>,
    /// (impurity, none for the joint factor), kind and band
    pub limits: Vec<(Option<String>, &'static str, LimitBand)>,
}

pub(super) fn scalar_bands(
    frame: &Frame,
    ep: &Endpoint,
    col: &[Option<&ScalarResult>],
    k: usize,
) -> ScalarBands {
    let names: Vec<String> = frame.imps.keys().cloned().collect();
    let values = vec![
        (
            "matrix_only",
            value_band(&column(col, |r| r.matrix_only), k),
        ),
        ("nominal", value_band(&column(col, |r| r.nominal), k)),
        ("worst_case", value_band(&column(col, |r| r.worst), k)),
    ];
    let mut contributions = BTreeMap::new();
    let mut limits: Vec<(Option<String>, &'static str, LimitBand)> = Vec::new();
    let mut imps = Map::new();
    for e in &names {
        let band = value_band(
            &column(col, |r| r.impurities[e.as_str()].contribution_nominal),
            k,
        );
        let mut entry = json!({"contribution_nominal": band.json.clone()});
        contributions.insert(e.clone(), band);
        if ep.limit.is_some() {
            let mut per = Map::new();
            for (kind, which) in [
                (
                    "alone",
                    (|r: &ImpurityResult| &r.alone) as fn(&ImpurityResult) -> &Option<Limit>,
                ),
                ("others_at_nominal", |r: &ImpurityResult| &r.others_nominal),
                ("others_at_worst", |r: &ImpurityResult| &r.others_worst),
            ] {
                let band = limit_band(&limit_column(col, e, which), k, false, "wt_pct");
                per.insert(kind.to_string(), band.json.clone());
                limits.push((Some(e.clone()), kind, band));
            }
            entry["limits"] = Value::Object(per);
        }
        imps.insert(e.clone(), entry);
    }
    let largest: Vec<Option<String>> = col.iter().map(|r| r.and_then(largest_scalar)).collect();
    let mut json = json!({
        "matrix_only": values[0].1.json.clone(),
        "nominal": values[1].1.json.clone(),
        "worst_case": values[2].1.json.clone(),
        "impurities": imps,
        "ranking": ranking_json(&largest, &names),
    });
    if ep.limit.is_some() {
        let samples: Vec<LimSample> = col
            .iter()
            .map(|r| match r.and_then(|r| r.joint.as_ref()) {
                Some(l) => LimSample::from_limit(l),
                None => LimSample::failed(),
            })
            .collect();
        let band = limit_band(&samples, k, false, "scale_factor");
        json["joint_k"] = band.json.clone();
        limits.push((None, "joint_k", band));
    }
    ScalarBands {
        json,
        values,
        contributions,
        limits,
    }
}

/// The class endpoint's bands at one target.
pub(super) fn class_bands(
    frame: &Frame,
    target: WasteClass,
    col: &[Option<&ClassLevel>],
    confidence: f64,
) -> Value {
    let names: Vec<String> = frame.imps.keys().cloned().collect();
    let nominal: Vec<Option<WasteClass>> = col.iter().map(|l| l.map(|l| l.nominal)).collect();
    let worst: Vec<Option<WasteClass>> = col.iter().map(|l| l.map(|l| l.worst)).collect();
    let n = col.len() as u64;
    let largest: Vec<Option<String>> = col.iter().map(|l| l.and_then(largest_class)).collect();
    json!({
        "target_class": class_name(target),
        "nominal_class": class_frequencies(&nominal, confidence),
        "worst_class": class_frequencies(&worst, confidence),
        "meets_target": {
            "nominal": frequency_json(meets_count(&nominal, target), n, confidence),
            "worst_case": frequency_json(meets_count(&worst, target), n, confidence),
        },
        "ranking": ranking_json(&largest, &names),
    })
}

/// The per-impurity class limits of a class endpoint over all targets.
fn class_limit_bands(
    nominal: &BTreeMap<String, LimSample>,
    screens: &[Option<&BTreeMap<String, LimSample>>],
    k: usize,
) -> Value {
    let mut impurities: BTreeMap<String, Map<String, Value>> = BTreeMap::new();
    let mut joint = Value::Null;
    for (key, nominal_sample) in nominal {
        let samples: Vec<LimSample> = screens
            .iter()
            .map(|s| {
                s.and_then(|m| m.get(key))
                    .map_or_else(LimSample::failed, |l| l.clone())
            })
            .collect();
        let unit = if key == "joint_scale_factor" {
            "scale_factor"
        } else {
            "wt_pct"
        };
        let band = limit_band(&samples, k, nominal_sample.positive_lower, unit);
        match key.rsplit_once('/') {
            Some((e, which)) => {
                impurities
                    .entry(e.to_string())
                    .or_default()
                    .insert(which.to_string(), band.json);
            }
            None => joint = band.json,
        }
    }
    json!({"impurities": impurities, "joint_scale_factor": joint})
}

// ---------------------------------------------------------------------------------------------
// Verification
// ---------------------------------------------------------------------------------------------

enum CheckKind {
    /// the limit's point below it (0.99 times) must meet the limit
    Below { key: String, limit: f64 },
    /// the point above it (1.01 times) must not
    Above { key: String, limit: f64 },
    Value {
        key: String,
        predicted: Option<f64>,
        /// `VALUE_FLOOR` times the endpoint's scale in the sample
        floor: f64,
    },
    Contribution {
        with: String,
        without: String,
        predicted: f64,
        scale: f64,
    },
}

struct Check {
    endpoint: usize,
    step: u64,
    sample: usize,
    role: String,
    x: Option<f64>,
    kind: CheckKind,
}

/// The full-alloy solves a verification needs, once each, and the checks that read them.
#[derive(Default)]
struct Planner {
    needs: BTreeMap<(usize, String), Comp>,
    checks: Vec<Check>,
    skipped: Vec<Value>,
}

impl Planner {
    fn need(&mut self, sample: usize, comp: Comp) -> String {
        let key = comp_key(&comp);
        self.needs.entry((sample, key.clone())).or_insert(comp);
        key
    }
}

/// The composition at which `kind` of `imp` takes the weight `x` in a sample: the impurity alone, the
/// others at nominal, the others at the sample's own worst case, or every nominal weight scaled by `x`.
fn limit_comp(
    frame: &Frame,
    res: &ScalarResult,
    imp: Option<&str>,
    kind: &str,
    x: f64,
) -> Result<Comp, String> {
    match (kind, imp) {
        ("alone", Some(e)) => frame.comp(&BTreeMap::from([(e.to_string(), x)])),
        ("others_at_nominal", Some(e)) => frame.comp(&with_weight(&frame.nominal(), e, x)),
        ("others_at_worst", Some(e)) => frame.comp(&with_weight(&res.worst_weights, e, x)),
        _ => {
            let scaled: BTreeMap<String, f64> = frame
                .nominal()
                .into_iter()
                .map(|(e, w)| (e, x * w))
                .collect();
            frame.comp(&scaled)
        }
    }
}

fn endpoint_value(
    ep: &Endpoint,
    t: &super::ElemTarget,
    limits: &BTreeMap<String, f64>,
) -> Option<f64> {
    if ep.kind == EndpointKind::ContactProxy {
        t.photon.as_ref().and_then(|p| p.contact_proxy)
    } else {
        Some(linear_value(&ep.kind, t, limits))
    }
}

type Solved = BTreeMap<(usize, u64), Option<f64>>;

fn solve_point(
    sample: usize,
    comp: &Comp,
    block: &FactorBlock,
    inp: &Inputs,
    cache: &mut PreparedCache,
) -> Result<Solved, String> {
    let title = format!("impurities uncertainty verify sample {sample}");
    let mut spec = budget::element_spec_with_outputs(inp.base, comp, &title, inp.outputs)?;
    study::apply_factor_block(block, &mut spec);
    let (res, _ms) = budget::solve_spec(&spec, &title, cache)?;
    let mut out = Solved::new();
    for &k in inp.screen.targets {
        let t = extract_target(&res, k, inp.want_dose)?;
        for (idx, ep) in inp.screen.endpoints.iter().enumerate() {
            if !matches!(ep.kind, EndpointKind::WasteClass(_)) {
                out.insert((idx, k), endpoint_value(ep, &t, inp.screen.limits));
            }
        }
    }
    Ok(out)
}

fn result_of(
    screens: &[Option<SampleScreen>],
    sample: usize,
    idx: usize,
    step: u64,
) -> Option<&ScalarResult> {
    screens[sample]
        .as_ref()
        .and_then(|s| s.scalar.get(&(idx, step)))
}

/// Verify the bands by full solves with the samples' own factors. Limits: the samples at sorted ranks
/// `k - 2` to `k + 2` of every established limit are solved at 0.99 and 1.01 times their own limit; the 0.99
/// point must meet the limit and the 1.01 point must not, unless the limit is capped. Values: the sample that
/// holds each upper bound is solved at the composition the value belongs to and must agree to
/// `VERIFY_TOL`, or within `VALUE_FLOOR` of the endpoint's scale in that sample.
fn verify(
    inp: &Inputs,
    screens: &[Option<SampleScreen>],
    scalar: &BTreeMap<(u64, usize), ScalarBands>,
) -> Result<Value, String> {
    let frame = inp.screen.frame;
    let (k, n) = (inp.cfg.k, screens.len());
    let mut plan = Planner::default();
    for ((step, idx), sb) in scalar {
        let ep = &inp.screen.endpoints[*idx];
        if let Some(limit) = ep.limit {
            for (imp, kind, band) in &sb.limits {
                if band.bound.is_none() {
                    continue;
                }
                let first = k.saturating_sub(RANK_WINDOW).max(1);
                for rank in first..=(k + RANK_WINDOW).min(n) {
                    let (x, sample) = band.sorted[rank - 1];
                    if !x.is_finite() || x <= 0.0 {
                        continue;
                    }
                    let Some(res) = result_of(screens, sample, *idx, *step) else {
                        continue;
                    };
                    let capped = band.statuses[sample] == "capped";
                    for (point, factor) in [("0.99", BELOW), ("1.01", ABOVE)] {
                        let role =
                            format!("limit_{}_{kind}_{point}", imp.as_deref().unwrap_or("joint"));
                        if capped && point == "1.01" {
                            plan.skipped.push(json!({"endpoint": ep.id, "step": step, "sample": sample, "role": role, "reason": "capped limit: the balance bound binds, the point above it leaves no balance"}));
                            continue;
                        }
                        let Ok(comp) = limit_comp(frame, res, imp.as_deref(), kind, factor * x)
                        else {
                            plan.skipped.push(json!({"endpoint": ep.id, "step": step, "sample": sample, "role": role, "reason": "the point leaves no positive balance"}));
                            continue;
                        };
                        let key = plan.need(sample, comp);
                        let kind = if point == "0.99" {
                            CheckKind::Below { key, limit }
                        } else {
                            CheckKind::Above { key, limit }
                        };
                        plan.checks.push(Check {
                            endpoint: *idx,
                            step: *step,
                            sample,
                            role,
                            x: Some(factor * x),
                            kind,
                        });
                    }
                }
            }
        }
        for (role, band) in &sb.values {
            let Some(sample) = band.upper_sample else {
                continue;
            };
            let Some(res) = result_of(screens, sample, *idx, *step) else {
                continue;
            };
            let (comp, predicted) = match *role {
                "matrix_only" => (frame.comp(&BTreeMap::new()), res.matrix_only),
                "nominal" => (frame.comp(&frame.nominal()), res.nominal),
                _ => (frame.comp(&res.worst_weights), res.worst),
            };
            let Ok(comp) = comp else {
                continue;
            };
            let key = plan.need(sample, comp);
            plan.checks.push(Check {
                endpoint: *idx,
                step: *step,
                sample,
                role: format!("value_{role}"),
                x: None,
                kind: CheckKind::Value {
                    key,
                    predicted,
                    floor: VALUE_FLOOR
                        * [res.matrix_only, res.nominal, res.worst]
                            .into_iter()
                            .flatten()
                            .fold(0.0_f64, |m, v| m.max(v.abs())),
                },
            });
        }
        for (e, band) in &sb.contributions {
            let Some(sample) = band.upper_sample else {
                continue;
            };
            let Some(res) = result_of(screens, sample, *idx, *step) else {
                continue;
            };
            let Some(c) = res.impurities[e].contribution_nominal else {
                continue;
            };
            let (Ok(with), Ok(without)) = (
                frame.comp(&frame.nominal()),
                frame.comp(&with_weight(&frame.nominal(), e, 0.0)),
            ) else {
                continue;
            };
            let scale = res.nominal.map_or(0.0, f64::abs).max(c.abs());
            let with = plan.need(sample, with);
            let without = plan.need(sample, without);
            plan.checks.push(Check {
                endpoint: *idx,
                step: *step,
                sample,
                role: format!("value_contribution_nominal_{e}"),
                x: None,
                kind: CheckKind::Contribution {
                    with,
                    without,
                    predicted: c,
                    scale,
                },
            });
        }
    }

    let mut cache = PreparedCache::new();
    let mut solved: BTreeMap<(usize, String), Result<Solved, String>> = BTreeMap::new();
    for ((sample, key), comp) in &plan.needs {
        let r = solve_point(*sample, comp, &inp.drawn.blocks[*sample], inp, &mut cache);
        solved.insert((*sample, key.clone()), r);
    }

    let mut points = Vec::new();
    let mut max_dev: f64 = 0.0;
    let mut all_pass = true;
    for c in &plan.checks {
        let ep = &inp.screen.endpoints[c.endpoint];
        let at = |key: &String| -> Result<Option<f64>, String> {
            match &solved[&(c.sample, key.clone())] {
                Ok(m) => Ok(m.get(&(c.endpoint, c.step)).copied().flatten()),
                Err(e) => Err(e.clone()),
            }
        };
        let mut row = json!({
            "endpoint": ep.id, "step": c.step, "sample": c.sample, "role": c.role,
        });
        let (pass, dev) = match &c.kind {
            CheckKind::Below { key, limit } | CheckKind::Above { key, limit } => {
                let below = matches!(c.kind, CheckKind::Below { .. });
                let v = at(key);
                row["limit"] = json!(limit);
                row["x"] = opt_num(c.x);
                row["solved"] = opt_num(v.clone().ok().flatten());
                let pass = match &v {
                    Ok(Some(v)) => {
                        if below {
                            v <= limit
                        } else {
                            v > limit
                        }
                    }
                    _ => false,
                };
                if let Err(e) = &v {
                    row["error"] = json!(e);
                }
                (pass, None)
            }
            CheckKind::Value {
                key,
                predicted,
                floor,
            } => {
                let v = at(key);
                let (dev, raw) = match (&v, predicted) {
                    (Ok(Some(s)), Some(p)) => value_dev(*p, *s, *floor),
                    (Ok(None), None) => (0.0, None),
                    _ => (1.0, None),
                };
                row["predicted"] = opt_num(*predicted);
                row["solved"] = opt_num(v.clone().ok().flatten());
                row["rel_dev"] = json!(dev);
                if let Some(raw) = raw {
                    row["rel_dev_before_floor"] = json!(raw);
                    row["floor"] = json!(floor);
                }
                if let Err(e) = &v {
                    row["error"] = json!(e);
                }
                (dev <= VERIFY_TOL, Some(dev))
            }
            CheckKind::Contribution {
                with,
                without,
                predicted,
                scale,
            } => {
                let (a, b) = (at(with), at(without));
                let (dev, solved_c) = match (&a, &b) {
                    (Ok(Some(a)), Ok(Some(b))) => {
                        let s = a - b;
                        let dev = if *scale == 0.0 {
                            0.0
                        } else {
                            (s - predicted).abs() / scale
                        };
                        (dev, Some(s))
                    }
                    _ => (1.0, None),
                };
                row["predicted"] = json!(predicted);
                row["solved"] = opt_num(solved_c);
                row["scale"] = json!(scale);
                row["rel_dev"] = json!(dev);
                (dev <= VERIFY_TOL, Some(dev))
            }
        };
        row["pass"] = json!(pass);
        if let Some(d) = dev {
            max_dev = max_dev.max(d);
        }
        all_pass &= pass;
        points.push(row);
    }
    Ok(json!({
        "tolerance_rel": VERIFY_TOL,
        "rank_window": RANK_WINDOW,
        "solves": plan.needs.len(),
        "points": points,
        "skipped": plan.skipped,
        "max_rel_dev": max_dev,
        "verified": all_pass && max_dev <= VERIFY_TOL,
    }))
}

// ---------------------------------------------------------------------------------------------
// The sampling run
// ---------------------------------------------------------------------------------------------

/// The draws of every sample, taken before any solve.
pub(super) struct Drawn {
    pub blocks: Vec<FactorBlock>,
    pub coverage: Value,
    /// the plan composition: the matrix, every impurity at its range maximum, the balance remainder
    pub plan_comp: Comp,
}

/// Draw the factor blocks of every sample from the sampling plan of the full alloy at the plan composition.
pub(super) fn draw(
    cfg: &SamplingConfig,
    frame: &Frame,
    base: &Value,
    outputs: &[&str],
) -> Result<Drawn, String> {
    let at_max: BTreeMap<String, f64> =
        frame.imps.iter().map(|(e, r)| (e.clone(), r.max)).collect();
    let plan_comp = frame.comp(&at_max)?;
    let plan =
        budget::element_spec_with_outputs(base, &plan_comp, "impurities sampling plan", outputs)?;
    let (blocks, mut coverage) = study::draw_factor_blocks(
        &cfg.block.channels,
        cfg.covariance.as_ref(),
        &plan,
        Path::new("."),
        cfg.samples,
        cfg.block.seed,
    )?;
    if let Value::Object(map) = &mut coverage {
        map.insert("statement".into(), json!(COVERAGE_STATEMENT));
    }
    Ok(Drawn {
        blocks,
        coverage,
        plan_comp,
    })
}

pub(super) struct Inputs<'a> {
    pub cfg: &'a SamplingConfig,
    pub drawn: &'a Drawn,
    pub screen: ScreenInputs<'a>,
    pub base: &'a Value,
    pub outputs: &'a [&'a str],
    pub dir: &'a Path,
    pub want_dose: bool,
    pub verify: bool,
    /// The nominal run's prepared data, for the properties of nuclides only a sample makes active.
    pub prepared: Option<&'a PreparedRun>,
}

pub(super) struct Uncertainty {
    /// the top-level `uncertainty` block
    pub summary: Value,
    /// (step, endpoint index) -> the endpoint entry's `uncertainty`
    pub bands: BTreeMap<(u64, usize), Value>,
    pub verification: Option<Value>,
}

pub(super) fn run(inp: &Inputs) -> Result<Uncertainty, String> {
    let cfg = inp.cfg;
    let frame = inp.screen.frame;
    let keep_dir = cfg.block.keep_samples.as_ref().map(|d| inp.dir.join(d));
    if let Some(dir) = &keep_dir {
        std::fs::create_dir_all(dir)
            .map_err(|e| format!("cannot create {}: {e}", dir.display()))?;
    }
    let elements: BTreeSet<String> = std::iter::once(frame.balance.clone())
        .chain(frame.matrix.keys().cloned())
        .chain(frame.imps.keys().cloned())
        .collect();
    let mut templates: BTreeMap<String, Spec> = BTreeMap::new();
    for e in &elements {
        templates.insert(
            e.clone(),
            budget::element_spec_with_outputs(
                inp.base,
                &BTreeMap::from([(e.clone(), 100.0)]),
                &format!("impurities uncertainty element {e}"),
                inp.outputs,
            )?,
        );
    }

    let mut cache = PreparedCache::new();
    let mut samples: Vec<SampleRun> = Vec::with_capacity(inp.drawn.blocks.len());
    for (index, block) in inp.drawn.blocks.iter().enumerate() {
        samples.push(solve_sample(
            index,
            block,
            &templates,
            inp.screen.targets,
            inp.want_dose,
            &mut cache,
        ));
    }
    let class_cx = sample_class_contexts(inp.screen.class_cx, &samples, inp.prepared, inp.dir)?;
    let screen_inp = ScreenInputs {
        class_cx: &class_cx,
        ..inp.screen
    };
    let mut screens: Vec<Option<SampleScreen>> = Vec::with_capacity(samples.len());
    let mut failed_samples: Vec<Value> = Vec::new();
    for (index, (sample, block)) in samples.iter_mut().zip(&inp.drawn.blocks).enumerate() {
        let screen = match &sample.runs {
            Some(runs) => match screen_runs(&screen_inp, runs) {
                Ok(s) => Some(s),
                Err(error) => {
                    sample.failures.push((None, format!("screen: {error}")));
                    None
                }
            },
            None => None,
        };
        for (element, error) in &sample.failures {
            failed_samples.push(json!({"sample": index, "element": element, "error": error}));
        }
        if let Some(dir) = &keep_dir {
            let empty = ElemRuns::new();
            let keep = json!({
                "sample": index,
                "factors": block,
                "elements": sample.elements,
                "element_responses": responses_json(sample.runs.as_ref().unwrap_or(&empty)),
                "screen": screen.as_ref().map(|s| screen_json(s, inp.screen.endpoints)),
                "error": sample.failures.first().map(|f| f.1.clone()),
            });
            let path = dir.join(format!("sample_{index:04}.json"));
            let text = serde_json::to_string_pretty(&keep).map_err(|e| e.to_string())?;
            std::fs::write(&path, format!("{text}\n"))
                .map_err(|e| format!("cannot write {}: {e}", path.display()))?;
        }
        screens.push(screen);
    }

    let (k, confidence) = (cfg.k, cfg.confidence);
    let mut bands: BTreeMap<(u64, usize), Value> = BTreeMap::new();
    let mut scalar: BTreeMap<(u64, usize), ScalarBands> = BTreeMap::new();
    let mut class_limits = Map::new();
    for (idx, ep) in inp.screen.endpoints.iter().enumerate() {
        if let EndpointKind::WasteClass(target) = ep.kind {
            let cx = inp
                .screen
                .class_cx
                .iter()
                .find(|c| c.endpoint == idx)
                .ok_or("missing class context")?;
            for &step in inp.screen.targets {
                let col: Vec<Option<&ClassLevel>> = screens
                    .iter()
                    .map(|s| s.as_ref().and_then(|s| s.class.get(&(idx, step))))
                    .collect();
                bands.insert((step, idx), class_bands(frame, target, &col, confidence));
            }
            let basis = &cx.basis;
            let nominal_iv =
                waste_budget::intervals_from_basis(&cx.setup, basis, &cx.external, true)?;
            let cols: Vec<Option<&BTreeMap<String, LimSample>>> = screens
                .iter()
                .map(|s| s.as_ref().and_then(|s| s.class_limits.get(&idx)))
                .collect();
            class_limits.insert(
                ep.id.clone(),
                class_limit_bands(&class_limit_samples(&nominal_iv), &cols, k),
            );
        } else {
            for &step in inp.screen.targets {
                let col: Vec<Option<&ScalarResult>> = screens
                    .iter()
                    .map(|s| s.as_ref().and_then(|s| s.scalar.get(&(idx, step))))
                    .collect();
                let sb = scalar_bands(frame, ep, &col, k);
                bands.insert((step, idx), sb.json.clone());
                scalar.insert((step, idx), sb);
            }
        }
    }

    let verification = if inp.verify {
        Some(verify(inp, &screens, &scalar)?)
    } else {
        None
    };
    let verification_pass = verification.as_ref().map(|v| v["verified"] == true);
    let summary = json!({
        "confidence_level": confidence,
        "order_statistic": {"k": k, "n": cfg.samples, "minimum_samples": cfg.minimum_samples},
        "sampling": {
            "channels": &cfg.block.channels,
            "covariance": &cfg.block.covariance,
            "samples": cfg.block.samples,
            "seed": cfg.block.seed,
            "keep_samples": &cfg.block.keep_samples,
        },
        "plan_composition_wt_pct": inp.drawn.plan_comp,
        "coverage": inp.drawn.coverage,
        "not_claimed": NOT_CLAIMED,
        "failed_samples": failed_samples,
        "impurity_class_limits": class_limits,
        "verification_pass": verification_pass,
    });
    Ok(Uncertainty {
        summary,
        bands,
        verification,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use actinv_core::sample_stats::order_index;

    fn bound(x: f64) -> LimSample {
        LimSample {
            status: "bound",
            effective: Some(x),
            positive_lower: false,
        }
    }

    #[test]
    fn a_value_band_is_the_median_and_the_kth_largest() {
        let n = 200usize;
        let k = order_index(n as u64, 0.95).unwrap() as usize;
        assert!(k > 1);
        // 1..=200 in a scrambled order
        let values: Vec<Option<f64>> = (0..n).map(|i| Some(((i * 37) % n + 1) as f64)).collect();
        let band = value_band(&values, k);
        assert_eq!(band.upper, Some((n - k + 1) as f64));
        assert_eq!(band.json["upper_bound"], (n - k + 1) as f64);
        // nearest rank: the 100th smallest of 200
        assert_eq!(band.json["median"], 100.0);
        assert_eq!(band.json["status"], "bounded");
        assert_eq!(band.json["by_sample"][1], 38.0);
        // the sample holding the bound is the one with that value
        let s = band.upper_sample.unwrap();
        assert_eq!(values[s], band.upper);
    }

    #[test]
    fn a_sample_without_a_value_widens_the_upper_bound() {
        let mut values: Vec<Option<f64>> = (0..72).map(|i| Some(1.0 + i as f64)).collect();
        assert_eq!(value_band(&values, 1).upper, Some(72.0));
        values[3] = None;
        let band = value_band(&values, 1);
        assert_eq!(band.upper, None);
        assert_eq!(band.json["status"], "unbounded");
        assert_eq!(band.json["valid_samples"], 71);
        assert!(band.json["by_sample"][3].is_null());
        // a bound further down the order is still finite
        assert_eq!(value_band(&values, 2).upper, Some(72.0));
        assert_eq!(value_band(&[None, None], 1).json["median"], Value::Null);
    }

    #[test]
    fn a_limit_band_is_the_kth_smallest_with_p146_statuses() {
        let n = 200usize;
        let k = order_index(n as u64, 0.95).unwrap() as usize;
        let samples: Vec<LimSample> = (0..n).map(|i| bound(((i * 37) % n + 1) as f64)).collect();
        let band = limit_band(&samples, k, false, "wt_pct");
        assert_eq!(band.bound, Some(k as f64));
        assert_eq!(band.json["status"], "established");
        assert_eq!(band.json["sorted"][0], 1.0);
        assert_eq!(band.json["sample_status"]["bound"], 200);
        assert_eq!(band.sorted[k - 1].0, k as f64);
        // a failed sample enters at 0 and only lowers the bound
        let mut samples: Vec<LimSample> = (0..72).map(|i| bound(10.0 + i as f64)).collect();
        assert_eq!(limit_band(&samples, 1, false, "wt_pct").bound, Some(10.0));
        samples[5] = LimSample::failed();
        let band = limit_band(&samples, 1, false, "wt_pct");
        assert_eq!(band.bound, Some(0.0));
        assert_eq!(band.json["sample_status"]["failed"], 1);
        assert_eq!(band.sorted[0], (0.0, 5));
        // a positive lower bound anywhere, or at nominal, withdraws the bound
        samples[5].positive_lower = true;
        let band = limit_band(&samples, 1, false, "wt_pct");
        assert_eq!(band.bound, None);
        assert_eq!(band.json["status"], "not_established");
        assert_eq!(band.json["positive_lower_bound_samples"], 1);
        let none_positive: Vec<LimSample> = (0..72).map(|i| bound(1.0 + i as f64)).collect();
        assert_eq!(
            limit_band(&none_positive, 1, true, "wt_pct").json["status"],
            "not_established"
        );
        // no response sorts last; when k reaches one the limit is unbounded
        let mut open: Vec<LimSample> = (0..72).map(|i| bound(1.0 + i as f64)).collect();
        open[0].effective = None;
        open[0].status = "no_response";
        assert_eq!(limit_band(&open, 1, false, "wt_pct").bound, Some(2.0));
        let all_open: Vec<LimSample> = (0..72)
            .map(|_| LimSample {
                status: "no_response",
                effective: None,
                positive_lower: false,
            })
            .collect();
        let band = limit_band(&all_open, 1, false, "wt_pct");
        assert_eq!(band.bound, None);
        assert_eq!(band.json["status"], "unbounded");
    }

    #[test]
    fn scalar_limits_map_to_sample_statuses() {
        let found = LimSample::from_limit(&Limit::found(0.5));
        assert_eq!((found.status, found.effective), ("bound", Some(0.5)));
        let capped = LimSample::from_limit(&Limit::beyond(3.0, "beyond"));
        assert_eq!((capped.status, capped.effective), ("capped", Some(3.0)));
        let infeasible = LimSample::from_limit(&Limit::none(INFEASIBLE, "x"));
        assert_eq!(
            (infeasible.status, infeasible.effective),
            ("infeasible", Some(0.0))
        );
        let unavailable = LimSample::from_limit(&Limit::none(UNAVAILABLE, "x"));
        assert_eq!(unavailable.effective, Some(0.0));
        let none = LimSample::from_limit(&Limit::none(super::super::NO_RESPONSE, "x"));
        assert_eq!((none.status, none.effective), ("no_response", None));
    }

    #[test]
    fn class_probabilities_carry_clopper_pearson_lower_bounds() {
        let confidence = 0.95;
        let mut classes: Vec<Option<WasteClass>> = vec![Some(WasteClass::C); 72];
        let f = class_frequencies(&classes, confidence);
        assert_eq!(f["frequency"]["C"]["count"], 72);
        assert_eq!(f["frequency"]["A"]["count"], 0);
        assert_eq!(f["by_sample"][0], "C");
        // all 72 meet class C: the lower bound is the closed form and reaches the level
        assert_eq!(meets_count(&classes, WasteClass::C), 72);
        let all = frequency_json(72, 72, confidence);
        assert_eq!(all["probability"], 1.0);
        assert!(all["lower"].as_f64().unwrap() >= 0.95);
        assert_eq!(all["lower"], clopper_pearson(72, 72, confidence).0);
        // a failed sample counts in n and meets nothing
        classes[0] = None;
        classes[1] = Some(WasteClass::AboveClassC);
        assert_eq!(meets_count(&classes, WasteClass::C), 70);
        assert_eq!(meets_count(&classes, WasteClass::B), 0);
        let f = class_frequencies(&classes, confidence);
        assert_eq!(f["frequency"]["failed"]["count"], 1);
        assert_eq!(f["frequency"]["above_class_c"]["count"], 1);
        let p = frequency_json(70, 72, confidence);
        assert_eq!(p["probability"], 70.0 / 72.0);
        assert_eq!(p["lower"], clopper_pearson(70, 72, confidence).0);
        assert!(p["lower"].as_f64().unwrap() < p["probability"].as_f64().unwrap());
    }

    #[test]
    fn ranking_fractions_count_the_largest_contributor_per_sample() {
        let names: Vec<String> = ["Co", "Nb", "Al"].iter().map(|s| s.to_string()).collect();
        let mut largest: Vec<Option<String>> = Vec::new();
        for i in 0..10 {
            largest.push(match i {
                0..=5 => Some("Nb".to_string()),
                6..=8 => Some("Co".to_string()),
                _ => None,
            });
        }
        let r = ranking_json(&largest, &names);
        assert_eq!(r["fractions"]["Nb"]["count"], 6);
        assert_eq!(r["fractions"]["Nb"]["fraction"], 0.6);
        assert_eq!(r["fractions"]["Co"]["count"], 3);
        assert_eq!(r["fractions"]["Al"]["count"], 0);
        assert_eq!(r["samples"], 10);
        assert_eq!(r["largest_by_sample"][9], Value::Null);
        assert_eq!(r["largest_by_sample"][0], "Nb");
    }

    #[test]
    fn the_class_largest_contributor_breaks_ties_by_name() {
        let level = |pairs: &[(&str, f64)]| ClassLevel {
            nominal: WasteClass::A,
            worst: WasteClass::A,
            contributions: pairs.iter().map(|(k, v)| (k.to_string(), *v)).collect(),
        };
        assert_eq!(
            largest_class(&level(&[("Nb", 2.0), ("Co", 5.0), ("Al", 5.0)])),
            Some("Al".to_string())
        );
        assert_eq!(largest_class(&level(&[])), None);
    }
}
