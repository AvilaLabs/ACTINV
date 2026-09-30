//! `actinv decide` — the decision loop as one command (P64).
//!
//! A decision spec names a banded run spec plus declared response
//! constraints at band edges. `decide` executes the run (requesting the
//! P61 audit and P60 design blocks when absent), evaluates every
//! constraint through the identical `select_step`/`response_edge`
//! machinery as the optimizer, and emits `actinv-decision-1`: margins,
//! the nominal-overcertify accounting, the completeness verdict, the
//! top measurement targets, and the run's sha256 — the whole decision
//! record in one document.

use std::path::{Path, PathBuf};

use actinv_core::spec::Spec;
use serde::Deserialize;
use serde_json::{json, Map, Value};

use crate::optimize::{response_edge, select_step, sha256_hex, Edge};

const TINY: f64 = 1e-30;
const BINDING_TOL: f64 = 1e-9;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecideSpec {
    pub schema: String,
    /// Path (relative to this file) to an actinv run spec, or an
    /// embedded spec object.
    pub run_spec: Value,
    pub decision: DecisionBlock,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionBlock {
    pub constraints: Vec<DecisionConstraint>,
    /// Cap on each emitted design table per constraint response;
    /// default 5.
    #[serde(default = "default_top")]
    pub measurement_top: usize,
}

fn default_top() -> usize {
    5
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionConstraint {
    pub name: String,
    pub response: String,
    pub time_s: f64,
    pub edge: Edge,
    pub sense: String,
    pub limit: f64,
}

pub fn run_decide(spec_path: &str, out_arg: Option<&str>) -> Result<Value, String> {
    let dpath = Path::new(spec_path);
    let dir = dpath
        .parent()
        .map(Path::to_path_buf)
        .unwrap_or_else(|| PathBuf::from("."));
    let dtext = std::fs::read_to_string(dpath)
        .map_err(|e| format!("cannot read decision spec {}: {e}", dpath.display()))?;
    let doc_out = run_decide_doc(&dtext, &dir, spec_path)?;
    let text =
        serde_json::to_string_pretty(&doc_out).map_err(|e| format!("serialise decision: {e}"))?;
    if let Some(out) = out_arg {
        std::fs::write(out, format!("{text}\n")).map_err(|e| format!("cannot write {out}: {e}"))?;
    } else {
        println!("{text}");
    }
    Ok(doc_out)
}

/// The decision loop over spec *text* — the `decide` CLI command and the
/// Python binding share this entry point. `base_dir` resolves relative
/// `run_spec` paths; `spec_path` is only the recorded label.
pub fn run_decide_doc(dtext: &str, base_dir: &Path, spec_path: &str) -> Result<Value, String> {
    let dir = base_dir;
    let dspec: DecideSpec =
        serde_json::from_str(dtext).map_err(|e| format!("cannot parse decision spec: {e}"))?;
    if dspec.schema != "actinv-decide-1" {
        return Err(format!(
            "decision schema must be actinv-decide-1, got '{}'",
            dspec.schema
        ));
    }
    if dspec.decision.constraints.is_empty() {
        return Err("decision.constraints must name at least one constraint".into());
    }
    for c in &dspec.decision.constraints {
        if c.sense != "le" && c.sense != "ge" {
            return Err(format!(
                "constraint '{}' sense must be 'le' or 'ge', got '{}'",
                c.name, c.sense
            ));
        }
        if !c.limit.is_finite() {
            return Err(format!("constraint '{}' limit must be finite", c.name));
        }
    }

    // Resolve the run spec: embedded object or a path relative to the
    // decision spec's directory (same convention as optimize base_spec).
    let (run_text, run_path_label) = match &dspec.run_spec {
        Value::String(rel) => {
            let p = dir.join(rel);
            let text = std::fs::read_to_string(&p)
                .map_err(|e| format!("cannot read run spec {}: {e}", p.display()))?;
            (text, rel.clone())
        }
        v @ Value::Object(_) => (
            serde_json::to_string(v).map_err(|e| format!("embedded run spec: {e}"))?,
            "<embedded>".to_string(),
        ),
        _ => {
            return Err("run_spec must be a path string or an embedded object".into());
        }
    };
    let authored_sha = sha256_hex(run_text.as_bytes());
    let mut doc: Value =
        serde_json::from_str(&run_text).map_err(|e| format!("cannot parse run spec: {e}"))?;

    // The decision needs a banded run: refuse to certify a point estimate.
    if doc.get("uncertainty").and_then(Value::as_object).is_none() {
        return Err("decision requires a banded run: run spec has no 'uncertainty' block".into());
    }
    // Request the audit and design blocks when the spec did not already.
    {
        let root = doc.as_object_mut().unwrap();
        let options = root
            .entry("options")
            .or_insert_with(|| json!({}))
            .as_object_mut()
            .ok_or("run spec 'options' must be an object")?;
        let outputs = options
            .entry("outputs")
            .or_insert_with(|| json!([]))
            .as_array_mut()
            .ok_or("run spec options.outputs must be an array")?;
        if !outputs.iter().any(|v| v == "audit") {
            outputs.push(json!("audit"));
        }
        let unc = root
            .get_mut("uncertainty")
            .and_then(Value::as_object_mut)
            .unwrap();
        unc.entry("design").or_insert_with(|| json!({}));
    }
    let eff_text = serde_json::to_string(&doc).map_err(|e| format!("effective spec: {e}"))?;
    let effective_sha = sha256_hex(eff_text.as_bytes());
    let spec = Spec::from_json(&eff_text).map_err(|e| format!("run spec invalid: {e}"))?;
    spec.validate()?;

    let result = actinv_core::run::run(&spec, "decide")?;
    // Provenance binds the deterministic content — `ms` is wall time.
    let mut rv = serde_json::to_value(&result).map_err(|e| format!("serialise run: {e}"))?;
    rv.as_object_mut()
        .ok_or("run result not an object")?
        .remove("ms");
    let result_sha = sha256_hex(
        serde_json::to_string(&rv)
            .map_err(|e| format!("serialise run: {e}"))?
            .as_bytes(),
    );

    // Evaluate each declared constraint at its band edge; nominal edge
    // recorded free (the overcertification accounting).
    let mut constraints = Vec::with_capacity(dspec.decision.constraints.len());
    let mut n_overcertify = 0usize;
    let mut binding: Vec<String> = Vec::new();
    let mut all_sat = true;
    for c in &dspec.decision.constraints {
        let step = select_step(&result.steps, c.time_s)
            .map_err(|e| format!("constraint '{}': {e}", c.name))?;
        let edge_v = response_edge(step, &c.response, c.edge)
            .map_err(|e| format!("constraint '{}': {e}", c.name))?
            .ok_or_else(|| {
                format!(
                    "constraint '{}': response '{}' not computable",
                    c.name, c.response
                )
            })?;
        let violation = if c.sense == "le" {
            (edge_v - c.limit) / c.limit.abs().max(TINY)
        } else {
            (c.limit - edge_v) / c.limit.abs().max(TINY)
        };
        let nominal_v = response_edge(step, &c.response, Edge::Nominal)
            .map_err(|e| format!("constraint '{}': {e}", c.name))?;
        let (nominal, nominal_violation, nominal_sat) = match nominal_v {
            Some(nv) => {
                let nv = if c.sense == "le" {
                    (nv - c.limit) / c.limit.abs().max(TINY)
                } else {
                    (c.limit - nv) / c.limit.abs().max(TINY)
                };
                (json!(nominal_v.unwrap()), json!(nv), nv <= 0.0)
            }
            None => (Value::Null, Value::Null, false),
        };
        let satisfied = violation <= 0.0;
        if !satisfied {
            all_sat = false;
        }
        if satisfied && c.edge != Edge::Nominal && !nominal_sat {
            // band-edge passes but nominal fails — cannot happen for upper
            // edges (band >= nominal), but guard the direction honestly
        }
        if !satisfied && nominal_sat && c.edge != Edge::Nominal {
            n_overcertify += 1;
        }
        if violation.abs() <= BINDING_TOL {
            binding.push(c.name.clone());
        }
        constraints.push(json!({
            "name": c.name,
            "response": c.response,
            "time_s": c.time_s,
            "step_t_s": step.t_s,
            "edge": edge_label(c.edge),
            "sense": c.sense,
            "limit": c.limit,
            "value": edge_v,
            "violation": violation,
            "satisfied": satisfied,
            "nominal": nominal,
            "nominal_violation": nominal_violation,
            "nominal_satisfied": nominal_sat,
        }));
    }

    let unc = spec.uncertainty.as_ref().unwrap();
    let covariance = unc.covariance.as_ref().ok_or(
        "decide requires uncertainty.covariance (P93 flux-only mode is not supported by decide)",
    )?;
    let u_cov = json!({
        "covariance_path": covariance.path,
        "covariance_sha256": covariance.sha256,
        "confidence_level": unc.confidence_level,
        "unmodeled_relative": unc.unmodeled_relative,
    });
    let u_label = unc
        .unmodeled_relative
        .map(|u| format!(" plus declared unmodeled_relative={u}"))
        .unwrap_or_default();
    let verdict_statement = if all_sat {
        format!(
            "certified: all {} constraints satisfied at their declared band edges \
             under covariance {} at confidence {:.2}{}",
            constraints.len(),
            covariance.sha256,
            unc.confidence_level,
            u_label
        )
    } else {
        format!(
            "not certified: {} of {} constraints fail at their declared band edges \
             under covariance {} at confidence {:.2}{}",
            constraints
                .iter()
                .filter(|c| !c["satisfied"].as_bool().unwrap())
                .count(),
            constraints.len(),
            covariance.sha256,
            unc.confidence_level,
            u_label
        )
    };

    // Measurement targets: per constraint response@step, the emitted
    // design block's top entries.
    let top = dspec.decision.measurement_top;
    let mut measurements = Map::new();
    for c in &dspec.decision.constraints {
        let key = format!("{}@{}", c.response, c.time_s);
        if measurements.contains_key(&key) {
            continue;
        }
        let step = select_step(&result.steps, c.time_s)
            .map_err(|e| format!("constraint '{}': {e}", c.name))?;
        if let Some(u) = &step.uncertainty {
            if let Some(resp) = u.responses.get(&c.response) {
                if let Some(design) = &resp.design {
                    let mut dv = serde_json::to_value(design)
                        .map_err(|e| format!("serialise design: {e}"))?;
                    for t in ["top_parameters", "top_reactions"] {
                        if let Some(a) = dv.get_mut(t).and_then(Value::as_array_mut) {
                            a.truncate(top);
                        }
                    }
                    measurements.insert(key, dv);
                }
            }
        }
    }

    let doc_out = json!({
        "schema": "actinv-decision-1",
        "decision_spec": {
            "path": spec_path,
            "sha256": sha256_hex(dtext.as_bytes()),
        },
        "run": {
            "run_spec": run_path_label,
            "authored_sha256": authored_sha,
            "effective_sha256": effective_sha,
            "result_sha256": result_sha,
        },
        "uncertainty_provenance": u_cov,
        "constraints": constraints,
        "verdict": {
            "certified": all_sat,
            "binding": binding,
            "nominal_would_overcertify": n_overcertify,
            "statement": verdict_statement,
        },
        "completeness": result.ledger.get("completeness").cloned().unwrap_or(Value::Null),
        "measurements": Value::Object(measurements),
    });

    Ok(doc_out)
}

fn edge_label(e: Edge) -> &'static str {
    match e {
        Edge::Nominal => "nominal",
        Edge::NormalLower => "normal_lower",
        Edge::NormalUpper => "normal_upper",
        Edge::ConservativeLower => "conservative_lower",
        Edge::ConservativeUpper => "conservative_upper",
    }
}
