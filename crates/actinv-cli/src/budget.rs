//! `actinv budget` — impurity budgets for the clearance index (P79).
//!
//! At fixed flux, coupled-mode activation is exactly linear in the initial
//! composition (P75b), so the IAEA clearance index `CI = Σ A_n/L_n` of any
//! composition is `Σ_e w_e r_e / 100`, where `r_e` is the CI of pure element
//! `e`. One solve per element therefore gives every impurity limit by linear
//! algebra. The balance element takes up the remainder to 100 wt%, so the
//! gradient with respect to an impurity is `(r_i − r_balance)/100`.
//!
//! Every limit the command emits is re-checked by a full solve at that
//! composition unless `--no-verify` is given; the result carries the
//! predicted and solved CI side by side.

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

use actinv_core::clearance::{table_key, LimitsTable};
use serde::Deserialize;
use serde_json::{json, Map, Value};

use crate::optimize::sha256_hex;

/// Relative agreement required between composed and solved CI.
pub const VERIFY_TOL: f64 = 1e-6;
const TOP_N: usize = 6;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BudgetSpec {
    pub schema: String,
    /// Path (relative to this file) to an actinv run spec, or an embedded
    /// spec object. Its material is replaced; everything else is used.
    pub base_spec: Value,
    /// Element that takes up the remainder to 100 wt%.
    pub balance: String,
    /// Fixed constituents, wt%.
    #[serde(default)]
    pub matrix: BTreeMap<String, f64>,
    /// Declared impurity specification, wt%.
    pub impurities: BTreeMap<String, f64>,
    /// One-based result step numbers at which CI ≤ 1 is required.
    pub targets: Vec<u64>,
    /// Optional `actinv-clearance-limits-1` file (relative to this file);
    /// default: the bundled IAEA table.
    #[serde(default)]
    pub limits: Option<String>,
}

/// The budget algebra at one target step. `r` maps element -> CI of the
/// pure element (per 100 wt%).
#[derive(Debug, Clone, PartialEq)]
pub struct TargetAnalysis {
    pub ci_matrix_only: f64,
    pub ci_at_spec: f64,
    pub gradient: BTreeMap<String, f64>,
    pub contribution: BTreeMap<String, f64>,
    pub margin_factor: Option<f64>,
    pub margin_status: &'static str,
    pub single_limit: BTreeMap<String, Option<f64>>,
    pub single_status: BTreeMap<String, &'static str>,
}

/// Full composition: matrix plus impurities, balance to 100 wt%.
pub fn full_composition(
    balance: &str,
    matrix: &BTreeMap<String, f64>,
    impurities: &BTreeMap<String, f64>,
) -> Result<BTreeMap<String, f64>, String> {
    let mut comp: BTreeMap<String, f64> = matrix.clone();
    for (e, w) in impurities {
        *comp.entry(e.clone()).or_insert(0.0) += w;
    }
    let others: f64 = comp.values().sum();
    let rest = 100.0 - others;
    if rest.is_nan() || rest <= 0.0 {
        return Err(format!(
            "matrix and impurities sum to {others} wt%; the balance element {balance} needs a positive remainder"
        ));
    }
    comp.insert(balance.to_string(), rest);
    Ok(comp)
}

/// CI of a composition from per-element CI (per 100 wt%).
pub fn composed_ci(r: &BTreeMap<String, f64>, comp: &BTreeMap<String, f64>) -> f64 {
    comp.iter()
        .map(|(e, w)| w / 100.0 * r.get(e).copied().unwrap_or(0.0))
        .sum()
}

pub fn analyse_target(
    r: &BTreeMap<String, f64>,
    balance: &str,
    matrix: &BTreeMap<String, f64>,
    impurities: &BTreeMap<String, f64>,
) -> Result<TargetAnalysis, String> {
    let ci_matrix_only = composed_ci(r, &full_composition(balance, matrix, &BTreeMap::new())?);
    let ci_at_spec = composed_ci(r, &full_composition(balance, matrix, impurities)?);
    let r_bal = r.get(balance).copied().unwrap_or(0.0);
    let mut gradient = BTreeMap::new();
    let mut contribution = BTreeMap::new();
    let mut single_limit = BTreeMap::new();
    let mut single_status = BTreeMap::new();
    for (e, &w) in impurities {
        let g = (r.get(e).copied().unwrap_or(0.0) - r_bal) / 100.0;
        gradient.insert(e.clone(), g);
        contribution.insert(e.clone(), w * g);
        // others held at spec: w_e* = w_e + (1 − CI_at_spec)/g
        let (lim, status) = if g <= 0.0 {
            (None, "no clearance-index response")
        } else {
            let lim = w + (1.0 - ci_at_spec) / g;
            if lim > 0.0 {
                (Some(lim), "limit")
            } else {
                (
                    None,
                    "infeasible: the other constituents alone exceed CI = 1",
                )
            }
        };
        single_limit.insert(e.clone(), lim);
        single_status.insert(e.clone(), status);
    }
    let imp_ci: f64 = contribution.values().sum();
    let (margin_factor, margin_status) = if imp_ci > 0.0 {
        let k = (1.0 - ci_matrix_only) / imp_ci;
        if k > 0.0 {
            (Some(k), "feasible")
        } else {
            (
                Some(k),
                "infeasible: the matrix without impurities exceeds CI = 1",
            )
        }
    } else {
        (None, "impurities at spec do not raise the clearance index")
    };
    Ok(TargetAnalysis {
        ci_matrix_only,
        ci_at_spec,
        gradient,
        contribution,
        margin_factor,
        margin_status,
        single_limit,
        single_status,
    })
}

/// Refuse base specs whose response is not linear in the initial
/// composition.
pub fn check_linear_base(base: &Value) -> Result<(), String> {
    if base.get("self_shielding").is_some_and(|v| !v.is_null()) {
        return Err("budget refuses self_shielding: shielding factors depend on composition, so CI is not linear in it".into());
    }
    if base.get("uncertainty").is_some_and(|v| !v.is_null()) {
        return Err(
            "budget refuses uncertainty: bands do not compose linearly across element runs".into(),
        );
    }
    if base
        .pointer("/options/screen")
        .is_some_and(|v| !v.is_null())
    {
        return Err("budget refuses options.screen: the screened tier prunes by composition-dependent bounds".into());
    }
    if let Some(steps) = base.get("schedule").and_then(Value::as_array) {
        for (i, s) in steps.iter().enumerate() {
            if s.get("feed").is_some_and(|v| !v.is_null()) {
                return Err(format!(
                    "budget refuses schedule step {}: a feed adds atoms independent of composition (affine, not linear)",
                    i + 1
                ));
            }
        }
    }
    Ok(())
}

struct ElementStep {
    ci: f64,
    uncovered: f64,
    total: f64,
    parts: BTreeMap<String, f64>,
    uncovered_parts: BTreeMap<String, f64>,
    t_s: f64,
}

fn ci_of_step(step: &Value, limits: &BTreeMap<String, f64>) -> Result<ElementStep, String> {
    let act = step
        .get("activity_Bq_per_g")
        .and_then(Value::as_object)
        .ok_or("result step carries no activity_Bq_per_g")?;
    let mut out = ElementStep {
        ci: 0.0,
        uncovered: 0.0,
        total: 0.0,
        parts: BTreeMap::new(),
        uncovered_parts: BTreeMap::new(),
        t_s: step.get("t_s").and_then(Value::as_f64).unwrap_or(f64::NAN),
    };
    for (n, v) in act {
        let a = v.as_f64().unwrap_or(0.0);
        if !(a.is_finite() && a > 0.0) {
            continue;
        }
        out.total += a;
        match table_key(n).and_then(|k| limits.get(&k).copied()) {
            Some(l) => {
                let c = a / l;
                out.ci += c;
                out.parts.insert(n.clone(), c);
            }
            None => {
                out.uncovered += a;
                out.uncovered_parts.insert(n.clone(), a);
            }
        }
    }
    Ok(out)
}

fn solve_doc(
    base: &Value,
    composition: &BTreeMap<String, f64>,
    title: &str,
    cache: &mut actinv_core::run::PreparedCache,
) -> Result<(Value, u64), String> {
    let mut doc = base.clone();
    let mass = base
        .pointer("/material/mass_g")
        .and_then(Value::as_f64)
        .unwrap_or(1.0);
    doc["title"] = Value::from(title);
    doc["material"] = json!({"mass_g": mass, "basis": "wt_percent", "composition": composition});
    doc["options"]["mode"] = Value::from("coupled");
    doc["options"]["prune"] = Value::from("reach");
    doc["options"]["outputs"] = json!(["ledger"]);
    let text = serde_json::to_string(&doc).map_err(|e| e.to_string())?;
    let spec = actinv_core::spec::Spec::from_json(&crate::resolve_catalog_json(&text)?)
        .map_err(|e| format!("{title}: {e}"))?;
    let t0 = std::time::Instant::now();
    let result = actinv_core::run::run_with_cache(&spec, "budget", cache)
        .map_err(|e| format!("{title}: {e}"))?;
    let ms = t0.elapsed().as_millis() as u64;
    Ok((serde_json::to_value(result).map_err(|e| e.to_string())?, ms))
}

fn step_of(result: &Value, step: u64) -> Result<&Value, String> {
    result
        .get("steps")
        .and_then(Value::as_array)
        .and_then(|s| {
            s.iter()
                .find(|x| x.get("step").and_then(Value::as_u64) == Some(step))
        })
        .ok_or_else(|| format!("result has no step {step}"))
}

fn top(map: &BTreeMap<String, f64>) -> Vec<Value> {
    let mut v: Vec<(&String, &f64)> = map.iter().collect();
    v.sort_by(|a, b| b.1.total_cmp(a.1).then(a.0.cmp(b.0)));
    v.into_iter()
        .take(TOP_N)
        .map(|(n, x)| json!([n, x]))
        .collect()
}

fn opt_num(x: Option<f64>) -> Value {
    x.map(Value::from).unwrap_or(Value::Null)
}

pub fn run_budget(path: &str, out: Option<&str>, verify: bool) -> Result<Value, String> {
    let bpath = Path::new(path);
    let dir = bpath
        .parent()
        .map(Path::to_path_buf)
        .unwrap_or_else(|| PathBuf::from("."));
    let text = std::fs::read_to_string(bpath)
        .map_err(|e| format!("cannot read budget spec {}: {e}", bpath.display()))?;
    let doc = run_budget_doc(&text, &dir, verify)?;
    let pretty = serde_json::to_string_pretty(&doc).map_err(|e| e.to_string())?;
    match out {
        Some(o) => std::fs::write(o, format!("{pretty}\n"))
            .map_err(|e| format!("cannot write {o}: {e}"))?,
        None => println!("{pretty}"),
    }
    Ok(doc)
}

pub fn run_budget_doc(text: &str, dir: &Path, verify: bool) -> Result<Value, String> {
    let started = std::time::Instant::now();
    let b: BudgetSpec =
        serde_json::from_str(text).map_err(|e| format!("cannot parse budget spec: {e}"))?;
    if b.schema != "actinv-budget-1" {
        return Err(format!(
            "budget schema must be actinv-budget-1, got '{}'",
            b.schema
        ));
    }
    if b.impurities.is_empty() {
        return Err("impurities must name at least one element".into());
    }
    if b.targets.is_empty() || b.targets.contains(&0) {
        return Err("targets must list one-based result step numbers".into());
    }
    for (e, w) in b.matrix.iter().chain(b.impurities.iter()) {
        if !(w.is_finite() && *w >= 0.0) {
            return Err(format!("{e}: wt% must be finite and nonnegative"));
        }
        if e.eq_ignore_ascii_case(&b.balance) {
            return Err(format!(
                "the balance element {} cannot also be listed in matrix or impurities",
                b.balance
            ));
        }
    }
    if let Some(e) = b.matrix.keys().find(|e| b.impurities.contains_key(*e)) {
        return Err(format!("{e} is listed in both matrix and impurities"));
    }
    full_composition(&b.balance, &b.matrix, &b.impurities)?;

    let (base_text, base_label) = match &b.base_spec {
        Value::String(rel) => {
            let p = dir.join(rel);
            let t = std::fs::read_to_string(&p)
                .map_err(|e| format!("cannot read base spec {}: {e}", p.display()))?;
            (t, rel.clone())
        }
        v @ Value::Object(_) => (
            serde_json::to_string(v).map_err(|e| e.to_string())?,
            "<embedded>".to_string(),
        ),
        _ => return Err("base_spec must be a path string or an embedded object".into()),
    };
    let base: Value =
        serde_json::from_str(&base_text).map_err(|e| format!("cannot parse base spec: {e}"))?;
    check_linear_base(&base)?;

    let table = match &b.limits {
        Some(rel) => {
            let p = dir.join(rel);
            LimitsTable::parse(
                std::fs::read_to_string(&p)
                    .map_err(|e| format!("cannot read limits {}: {e}", p.display()))?,
            )?
        }
        None => LimitsTable::bundled()?,
    };
    let limits: BTreeMap<String, f64> = table.limits.iter().cloned().collect();

    let elements: BTreeSet<String> = std::iter::once(b.balance.clone())
        .chain(b.matrix.keys().cloned())
        .chain(b.impurities.keys().cloned())
        .collect();
    let mut cache = actinv_core::run::PreparedCache::new();
    let mut per_element: BTreeMap<String, BTreeMap<u64, ElementStep>> = BTreeMap::new();
    let mut solves = Vec::new();
    for e in &elements {
        let comp = BTreeMap::from([(e.clone(), 100.0)]);
        let (res, ms) = solve_doc(&base, &comp, &format!("budget element {e}"), &mut cache)?;
        let mut by_step = BTreeMap::new();
        for &k in &b.targets {
            by_step.insert(k, ci_of_step(step_of(&res, k)?, &limits)?);
        }
        solves.push(json!({"element": e, "ms": ms, "mode": res["mode"]}));
        per_element.insert(e.clone(), by_step);
    }

    let spec_comp = full_composition(&b.balance, &b.matrix, &b.impurities)?;
    let mut targets_out = Vec::new();
    let mut analyses = BTreeMap::new();
    for &k in &b.targets {
        let r: BTreeMap<String, f64> = per_element
            .iter()
            .map(|(e, s)| (e.clone(), s[&k].ci))
            .collect();
        let a = analyse_target(&r, &b.balance, &b.matrix, &b.impurities)?;
        let mut parts = BTreeMap::new();
        let mut uparts = BTreeMap::new();
        let (mut unc, mut tot) = (0.0, 0.0);
        for (e, w) in &spec_comp {
            let s = &per_element[e][&k];
            unc += w / 100.0 * s.uncovered;
            tot += w / 100.0 * s.total;
            for (n, c) in &s.parts {
                *parts.entry(n.clone()).or_insert(0.0) += w / 100.0 * c;
            }
            for (n, c) in &s.uncovered_parts {
                *uparts.entry(n.clone()).or_insert(0.0) += w / 100.0 * c;
            }
        }
        let imp: Map<String, Value> = b
            .impurities
            .iter()
            .map(|(e, w)| {
                (
                    e.clone(),
                    json!({
                        "spec_wt_pct": w,
                        "dci_per_wt_pct": a.gradient[e],
                        "ci_contribution_at_spec": a.contribution[e],
                        "single_limit_wt_pct_others_at_spec": opt_num(a.single_limit[e]),
                        "single_limit_status": a.single_status[e],
                    }),
                )
            })
            .collect();
        let t_s = per_element[&b.balance][&k].t_s;
        targets_out.push(json!({
            "step": k,
            "t_s": t_s,
            "ci_matrix_only": a.ci_matrix_only,
            "ci_at_spec": a.ci_at_spec,
            "feasible_with_zero_impurities": a.ci_matrix_only < 1.0,
            "spec_margin_factor_k": opt_num(a.margin_factor),
            "spec_margin_status": a.margin_status,
            "uncovered_activity_share_at_spec": if tot > 0.0 { unc / tot } else { 0.0 },
            "impurities": imp,
            "top_nuclides_ci_at_spec": top(&parts),
            "top_uncovered_nuclides_Bq_g_at_spec": top(&uparts),
        }));
        analyses.insert(k, (a, r));
    }

    // Tightest limit per impurity across targets; a target without a limit
    // decides the status.
    let summary: Map<String, Value> = b
        .impurities
        .keys()
        .map(|e| {
            let mut tight: Option<(f64, u64)> = None;
            let mut blocked: Option<(u64, &'static str)> = None;
            for (&k, (a, _)) in &analyses {
                match a.single_limit[e] {
                    Some(l) if tight.is_none_or(|(t, _)| l < t) => tight = Some((l, k)),
                    Some(_) => {}
                    None if a.single_status[e] != "no clearance-index response" => {
                        blocked.get_or_insert((k, a.single_status[e]));
                    }
                    None => {}
                }
            }
            let v = match (blocked, tight) {
                (Some((k, s)), _) => json!({"limit_wt_pct": null, "binding_step": k, "status": s}),
                (None, Some((l, k))) => json!({"limit_wt_pct": l, "binding_step": k, "status": "limit"}),
                (None, None) => json!({"limit_wt_pct": null, "binding_step": null, "status": "no clearance-index response"}),
            };
            (e.clone(), v)
        })
        .collect();

    let verification = if verify {
        let mut points: Vec<(String, BTreeMap<String, f64>)> =
            vec![("at_spec".into(), spec_comp.clone())];
        for (&k, (a, _)) in &analyses {
            if let (Some(kf), "feasible") = (a.margin_factor, a.margin_status) {
                let scaled = b
                    .impurities
                    .iter()
                    .map(|(e, w)| (e.clone(), kf * w))
                    .collect();
                points.push((
                    format!("spec_x_k@{k}"),
                    full_composition(&b.balance, &b.matrix, &scaled)?,
                ));
            }
            for (e, l) in &a.single_limit {
                if let Some(l) = l {
                    let mut imp = b.impurities.clone();
                    imp.insert(e.clone(), *l);
                    points.push((
                        format!("limit_{e}@{k}"),
                        full_composition(&b.balance, &b.matrix, &imp)?,
                    ));
                }
            }
        }
        let mut out_points = Vec::new();
        let mut worst: f64 = 0.0;
        for (id, comp) in &points {
            let (res, ms) = solve_doc(&base, comp, &format!("budget verify {id}"), &mut cache)?;
            let mut pred = Map::new();
            let mut solved = Map::new();
            let mut dev: f64 = 0.0;
            for (&k, (_, r)) in &analyses {
                let p = composed_ci(r, comp);
                let s = ci_of_step(step_of(&res, k)?, &limits)?.ci;
                dev = dev.max((s - p).abs() / p.abs().max(1e-300));
                pred.insert(k.to_string(), Value::from(p));
                solved.insert(k.to_string(), Value::from(s));
            }
            worst = worst.max(dev);
            out_points.push(json!({
                "id": id, "composition_wt_pct": comp, "predicted_ci": pred,
                "solved_ci": solved, "max_rel_dev": dev, "pass": dev <= VERIFY_TOL, "ms": ms,
            }));
        }
        json!({
            "tolerance_rel": VERIFY_TOL,
            "points": out_points,
            "max_rel_dev": worst,
            "verified": worst <= VERIFY_TOL,
        })
    } else {
        json!({"skipped": true, "verified": false})
    };

    Ok(json!({
        "schema": "actinv-budget-result-1",
        "budget_sha256": sha256_hex(text.as_bytes()),
        "base_spec": base_label,
        "base_spec_sha256": sha256_hex(base_text.as_bytes()),
        "limits": {"source": table.source, "sha256": table.sha256},
        "solver": {"mode": "coupled", "prune": "reach", "outputs": ["ledger"]},
        "balance": b.balance,
        "matrix": b.matrix,
        "impurities": b.impurities,
        "element_solves": solves,
        "targets": targets_out,
        "summary": summary,
        "verification": verification,
        "elapsed_ms": started.elapsed().as_millis() as u64,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn m(pairs: &[(&str, f64)]) -> BTreeMap<String, f64> {
        pairs.iter().map(|(k, v)| (k.to_string(), *v)).collect()
    }

    #[test]
    fn budget_algebra_on_a_synthetic_table() {
        let r = m(&[("Fe", 0.01), ("Ni", 10.0), ("Co", 1000.0)]);
        let matrix = m(&[("Ni", 1.0)]);
        let imp = m(&[("Co", 0.0005)]);
        let a = analyse_target(&r, "Fe", &matrix, &imp).unwrap();
        let ci0 = (99.0 * 0.01 + 1.0 * 10.0) / 100.0;
        assert!((a.ci_matrix_only - ci0).abs() < 1e-15);
        let g = (1000.0 - 0.01) / 100.0;
        assert!((a.gradient["Co"] - g).abs() < 1e-12);
        let ci_spec = ((99.0 - 0.0005) * 0.01 + 10.0 + 0.0005 * 1000.0) / 100.0;
        assert!((a.ci_at_spec - ci_spec).abs() < 1e-14);
        let k = a.margin_factor.unwrap();
        assert_eq!(a.margin_status, "feasible");
        // the edge compositions solve to CI = 1 under the composed model
        let edge = full_composition("Fe", &matrix, &m(&[("Co", 0.0005 * k)])).unwrap();
        assert!((composed_ci(&r, &edge) - 1.0).abs() < 1e-12);
        let lim = a.single_limit["Co"].unwrap();
        let at_lim = full_composition("Fe", &matrix, &m(&[("Co", lim)])).unwrap();
        assert!((composed_ci(&r, &at_lim) - 1.0).abs() < 1e-12);
    }

    #[test]
    fn budget_statuses_for_no_response_and_infeasible() {
        // Al below the balance's CI: no response; matrix Ni pushes CI over 1.
        let r = m(&[("Fe", 0.5), ("Al", 0.1), ("Ni", 200.0), ("Co", 50.0)]);
        let a = analyse_target(
            &r,
            "Fe",
            &m(&[("Ni", 1.0)]),
            &m(&[("Al", 0.01), ("Co", 0.01)]),
        )
        .unwrap();
        assert!(a.ci_matrix_only > 1.0);
        assert_eq!(a.single_limit["Al"], None);
        assert_eq!(a.single_status["Al"], "no clearance-index response");
        assert_eq!(a.single_limit["Co"], None);
        assert!(a.single_status["Co"].starts_with("infeasible"));
        assert!(a.margin_status.starts_with("infeasible"));
    }

    #[test]
    fn budget_refuses_nonlinear_inputs() {
        let ok = json!({"schedule": [{"dt": "1 s", "flux": 1.0}], "options": {}});
        assert!(check_linear_base(&ok).is_ok());
        for bad in [
            json!({"self_shielding": {"table": {"path": "x", "sha256": "y"}}}),
            json!({"uncertainty": {"covariance": {"path": "x", "sha256": "y"}}}),
            json!({"options": {"screen": {"bmin_atoms_per_g": 1e-4}}}),
            json!({"schedule": [{"dt": "1 s", "flux": 1.0, "feed": {"Fe56": 1.0}}]}),
        ] {
            assert!(check_linear_base(&bad).is_err(), "accepted {bad}");
        }
    }

    #[test]
    fn budget_rejects_bad_compositions() {
        assert!(full_composition("Fe", &m(&[("Cr", 90.0)]), &m(&[("Ni", 10.0)])).is_err());
        let text = json!({
            "schema": "actinv-budget-1", "base_spec": {}, "balance": "Fe",
            "matrix": {"Fe": 1.0}, "impurities": {"Co": 0.01}, "targets": [1]
        })
        .to_string();
        let err = run_budget_doc(&text, Path::new("."), false).unwrap_err();
        assert!(err.contains("balance"), "{err}");
    }
}
