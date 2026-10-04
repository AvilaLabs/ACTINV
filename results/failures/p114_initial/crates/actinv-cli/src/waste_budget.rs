//! Class-specific impurity budgets under the P103 nominal Part 61 contract.

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

use actinv_core::waste::{
    evaluate_component, NuclideProperties, RulePack, WasteClass, WasteGeometry, WasteType,
};
use serde::Deserialize;
use serde_json::{json, Map, Value};

use crate::{budget, waste};

const INSET_REL: f64 = 5.0e-7;
const VERIFY_REL: f64 = 1e-6;
const VERIFY_ABS: f64 = 1e-12;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct WasteBudgetSpec {
    schema: String,
    base_spec: Value,
    balance: String,
    #[serde(default)]
    matrix: BTreeMap<String, f64>,
    impurities: BTreeMap<String, f64>,
    targets: Vec<u64>,
    rules: String,
    target_class: WasteClass,
    mass_g: f64,
    #[serde(default)]
    displaced_volume_cm3: Option<f64>,
    #[serde(default)]
    density_g_cm3: Option<f64>,
    waste_type: WasteType,
    nuclide_properties: BTreeMap<String, NuclideProperties>,
    external_tritium: waste::ExternalTritium,
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
struct ConstraintKey {
    step: u64,
    table: u8,
    column: Option<u8>,
}

#[derive(Clone, Debug, PartialEq)]
struct Edge {
    value: f64,
    open: bool,
    binding: Option<ConstraintKey>,
    contributors: Vec<String>,
    contributor_count: usize,
}

#[derive(Clone, Debug)]
struct Interval {
    lower: Edge,
    upper: Edge,
    feasible: bool,
    status: &'static str,
    no_response: bool,
}

#[derive(Clone)]
struct Basis {
    /// Per-nuclide activity for a pure element in Bq/g, indexed by selected step.
    activities: BTreeMap<u64, BTreeMap<String, f64>>,
    /// Class-constraint normalized sums for a 100 wt% pure element.
    sums: BTreeMap<ConstraintKey, f64>,
}

fn sha256(bytes: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    Sha256::digest(bytes)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

fn geometry(b: &WasteBudgetSpec) -> Result<WasteGeometry, String> {
    if !b.mass_g.is_finite() || b.mass_g <= 0.0 {
        return Err("mass_g must be finite and positive".into());
    }
    let volume = match (b.displaced_volume_cm3, b.density_g_cm3) {
        (Some(value), None) if value.is_finite() && value > 0.0 => value,
        (None, Some(value)) if value.is_finite() && value > 0.0 => b.mass_g / value,
        (Some(_), Some(_)) => {
            return Err("provide displaced_volume_cm3 or density_g_cm3, not both".into())
        }
        (None, None) => return Err("displaced_volume_cm3 or density_g_cm3 is required".into()),
        (Some(_), None) => return Err("displaced_volume_cm3 must be finite and positive".into()),
        (None, Some(_)) => return Err("density_g_cm3 must be finite and positive".into()),
    };
    if !volume.is_finite() || volume <= 0.0 {
        return Err("derived displaced volume must be finite and positive".into());
    }
    Ok(WasteGeometry {
        mass_g: b.mass_g,
        displaced_volume_cm3: volume,
    })
}

fn canonical_element(raw: &str) -> Result<String, String> {
    match actinv_data::composition::material_key(raw)? {
        actinv_data::composition::MaterialKey::Element(symbol) => Ok(symbol),
        actinv_data::composition::MaterialKey::Nuclide { .. } => Err(format!(
            "budget constituent '{raw}' must be a natural-element symbol"
        )),
    }
}

fn canonical_weights(
    raw: BTreeMap<String, f64>,
    label: &str,
) -> Result<BTreeMap<String, f64>, String> {
    let mut values = BTreeMap::new();
    for (name, value) in raw {
        let element = canonical_element(&name).map_err(|error| format!("{label}: {error}"))?;
        if values.insert(element.clone(), value).is_some() {
            return Err(format!(
                "{label} contains duplicate canonical element {element}"
            ));
        }
    }
    Ok(values)
}

fn canonicalize_constituents(b: &mut WasteBudgetSpec) -> Result<(), String> {
    b.balance = canonical_element(&b.balance).map_err(|error| format!("balance: {error}"))?;
    b.matrix = canonical_weights(std::mem::take(&mut b.matrix), "matrix")?;
    b.impurities = canonical_weights(std::mem::take(&mut b.impurities), "impurities")?;
    let mut seen = BTreeSet::from([b.balance.clone()]);
    for element in b.matrix.keys().chain(b.impurities.keys()) {
        if !seen.insert(element.clone()) {
            return Err(format!(
                "canonical element {element} is duplicated across balance, matrix and impurities"
            ));
        }
    }
    Ok(())
}

fn full_composition(
    balance: &str,
    matrix: &BTreeMap<String, f64>,
    impurities: &BTreeMap<String, f64>,
) -> Result<BTreeMap<String, f64>, String> {
    let mut composition = matrix.clone();
    for (element, weight) in impurities {
        *composition.entry(element.clone()).or_default() += weight;
    }
    let sum: f64 = composition.values().sum();
    let rest = 100.0 - sum;
    if !sum.is_finite() || !rest.is_finite() || rest <= 0.0 {
        return Err(format!(
            "fixed constituents sum to {sum} wt%; balance element must remain positive"
        ));
    }
    composition.insert(balance.to_string(), rest);
    Ok(composition)
}

fn base_spec(value: &Value, dir: &Path) -> Result<(String, String), String> {
    match value {
        Value::String(path) => {
            let full = dir.join(path);
            let text = std::fs::read_to_string(&full)
                .map_err(|error| format!("cannot read base spec {}: {error}", full.display()))?;
            Ok((text, path.clone()))
        }
        Value::Object(_) => Ok((
            serde_json::to_string(value).map_err(|error| error.to_string())?,
            "<embedded>".into(),
        )),
        _ => Err("base_spec must be a path string or embedded object".into()),
    }
}

fn constraints(
    evaluation: &actinv_core::waste::Evaluation,
    target: WasteClass,
    step: u64,
) -> Result<BTreeMap<ConstraintKey, f64>, String> {
    let mut out = BTreeMap::new();
    for constraint in evaluation.constraints_for_class(target)? {
        let key = ConstraintKey {
            step,
            table: constraint.table,
            column: constraint.column,
        };
        if out.insert(key, constraint.normalized_sum).is_some() {
            return Err(format!("duplicate class constraint at step {step}"));
        }
    }
    Ok(out)
}

fn step_activities(step: &Value) -> Result<BTreeMap<String, f64>, String> {
    let values = step
        .get("activity_Bq_per_g")
        .and_then(Value::as_object)
        .ok_or("run step lacks activity_Bq_per_g")?;
    let mut result = BTreeMap::new();
    for (key, value) in values {
        let a = value
            .as_f64()
            .ok_or_else(|| format!("activity {key} is not numeric"))?;
        if !a.is_finite() || a < 0.0 {
            return Err(format!("activity {key} must be finite and nonnegative"));
        }
        result.insert(key.clone(), a);
    }
    Ok(result)
}

fn scaled_activities(
    basis: &BTreeMap<String, Basis>,
    composition: &BTreeMap<String, f64>,
    step: u64,
    mass_g: f64,
) -> Result<BTreeMap<String, f64>, String> {
    let mut result = BTreeMap::new();
    for (element, weight) in composition {
        let source = basis
            .get(element)
            .ok_or_else(|| format!("no elemental solve for {element}"))?
            .activities
            .get(&step)
            .ok_or_else(|| format!("no elemental activity for {element} at step {step}"))?;
        for (nuclide, activity_bq_per_g) in source {
            *result.entry(nuclide.clone()).or_default() +=
                weight / 100.0 * activity_bq_per_g * mass_g;
        }
    }
    Ok(result)
}

fn external_at(external: &waste::ExternalTritium, step: u64) -> Result<(f64, bool), String> {
    waste::external_tritium_at(external, step)
}

fn interval_for<F>(
    base_sums: &BTreeMap<ConstraintKey, f64>,
    slope_sums: &BTreeMap<ConstraintKey, f64>,
    cap: f64,
    mut endpoint_open: F,
) -> Result<Interval, String>
where
    F: FnMut(&ConstraintKey, f64) -> Result<(bool, Vec<String>, usize), String>,
{
    let mut interval = Interval {
        lower: Edge {
            value: 0.0,
            open: false,
            binding: None,
            contributors: Vec::new(),
            contributor_count: 0,
        },
        upper: Edge {
            value: cap,
            open: true,
            binding: None,
            contributors: Vec::new(),
            contributor_count: 0,
        },
        feasible: cap > 0.0,
        status: "limit",
        no_response: true,
    };
    for (key, base) in base_sums {
        let slope = slope_sums.get(key).copied().unwrap_or(0.0);
        if slope == 0.0 {
            if *base > 1.0 {
                interval.feasible = false;
                interval.status = "infeasible";
            } else if *base == 1.0 {
                let zero_boundary = endpoint_open(key, 0.0)?;
                if zero_boundary.0 {
                    interval.feasible = false;
                    interval.status = "infeasible";
                } else if cap > 0.0 && endpoint_open(key, cap * 0.5)?.0 {
                    // The arithmetic sum is unchanged, but contributor count can jump as soon as
                    // a zero-activity material receives positive mass. Only x=0 remains admissible.
                    interval.upper = Edge {
                        value: 0.0,
                        open: false,
                        binding: Some(*key),
                        contributors: zero_boundary.1,
                        contributor_count: zero_boundary.2,
                    };
                    interval.status = "singleton";
                }
            }
            continue;
        }
        interval.no_response = false;
        let edge = (1.0 - base) / slope;
        if !edge.is_finite() {
            interval.feasible = false;
            interval.status = "nonfinite_boundary";
            continue;
        }
        if slope > 0.0 {
            if edge < 0.0 {
                interval.feasible = false;
                interval.status = "infeasible";
            } else if edge <= interval.upper.value {
                let (open, contributors, contributor_count) = if edge >= cap {
                    (true, Vec::new(), 0)
                } else {
                    endpoint_open(key, edge)?
                };
                if edge < interval.upper.value {
                    interval.upper = Edge {
                        value: edge,
                        open,
                        binding: Some(*key),
                        contributors,
                        contributor_count,
                    };
                } else {
                    if open && !interval.upper.open {
                        interval.upper.binding = Some(*key);
                        interval.upper.contributors = contributors.clone();
                        interval.upper.contributor_count = contributor_count;
                    }
                    interval.upper.open |= open;
                    if interval.upper.binding.is_none() {
                        interval.upper.binding = Some(*key);
                        interval.upper.contributors = contributors;
                        interval.upper.contributor_count = contributor_count;
                    }
                }
            }
        } else if edge > interval.upper.value {
            interval.feasible = false;
            interval.status = "infeasible";
        } else if edge >= interval.lower.value {
            let (open, contributors, contributor_count) = if edge >= cap {
                (true, Vec::new(), 0)
            } else {
                endpoint_open(key, edge)?
            };
            if edge > interval.lower.value {
                interval.lower = Edge {
                    value: edge,
                    open,
                    binding: Some(*key),
                    contributors,
                    contributor_count,
                };
            } else {
                if open && !interval.lower.open {
                    interval.lower.binding = Some(*key);
                    interval.lower.contributors = contributors.clone();
                    interval.lower.contributor_count = contributor_count;
                }
                interval.lower.open |= open;
                if interval.lower.binding.is_none() {
                    interval.lower.binding = Some(*key);
                    interval.lower.contributors = contributors;
                    interval.lower.contributor_count = contributor_count;
                }
            }
        }
    }
    if interval.upper.value < interval.lower.value
        || (interval.upper.value == interval.lower.value
            && (interval.upper.open || interval.lower.open))
    {
        interval.feasible = false;
        interval.status = "infeasible";
    }
    if interval.feasible && interval.upper.binding.is_none() && interval.upper.value == cap {
        interval.status = "composition_bound";
    }
    if interval.no_response && interval.feasible && interval.status != "singleton" {
        interval.status = "no_response";
    }
    Ok(interval)
}

// Keep the endpoint inputs explicit: this routine performs a full core evaluation at a proposed
// composition boundary and must have every input needed to reproduce that composition and its rule result.
#[allow(clippy::too_many_arguments)]
fn endpoint_open(
    key: &ConstraintKey,
    target_class: WasteClass,
    edge_value: f64,
    matrix: &BTreeMap<String, f64>,
    balance: &str,
    base_impurities: &BTreeMap<String, f64>,
    variable: &str,
    rules: &RulePack,
    waste_type: WasteType,
    geometry: WasteGeometry,
    properties: &BTreeMap<String, NuclideProperties>,
    external: &waste::ExternalTritium,
    basis: &BTreeMap<String, Basis>,
    targets: &[u64],
    joint_scale: bool,
) -> Result<(bool, Vec<String>, usize), String> {
    let mut impurities = base_impurities.clone();
    if joint_scale {
        for value in impurities.values_mut() {
            *value *= edge_value;
        }
    } else {
        impurities.insert(variable.to_string(), edge_value);
    }
    let composition = full_composition(balance, matrix, &impurities)?;
    let mut boundary = (false, Vec::new(), 0);
    for &step in targets {
        if step != key.step {
            continue;
        }
        let mut activities = scaled_activities(basis, &composition, step, geometry.mass_g)?;
        let (h3, unknown) = external_at(external, step)?;
        if unknown {
            return Err("class budget refuses required-but-undeclared external tritium".into());
        }
        if h3 > 0.0 {
            merge_activity(&mut activities, "H3", h3)?;
        }
        let evaluation = evaluate_component(rules, waste_type, geometry, &activities, properties)?;
        let constraint = evaluation
            .constraints_for_class(target_class)?
            .into_iter()
            .find(|constraint| constraint.table == key.table && constraint.column == key.column)
            .ok_or("endpoint has no matching class constraint")?;
        if (constraint.normalized_sum - 1.0).abs() > 1e-8 {
            return Err(format!(
                "computed endpoint does not meet its normalized class boundary: {}",
                constraint.normalized_sum
            ));
        }
        boundary = (
            constraint.strict,
            constraint.contributors,
            constraint.contributor_count,
        );
    }
    Ok(boundary)
}

fn inset_point(interval: &Interval, variable_cap: f64) -> Result<Option<f64>, String> {
    if !interval.feasible {
        return Ok(None);
    }
    if interval.status == "singleton"
        && interval.lower.value == 0.0
        && interval.upper.value == 0.0
        && !interval.lower.open
        && !interval.upper.open
    {
        return Ok(Some(0.0));
    }
    let mut point = interval.upper.value;
    if interval.upper.open || interval.upper.binding.is_some() {
        let span = interval.upper.value - interval.lower.value;
        let scale = if interval.lower.value > 0.0 {
            span
        } else {
            interval.upper.value.abs()
        };
        let distance = scale * INSET_REL;
        point = interval.upper.value - distance;
    }
    if point <= interval.lower.value && interval.lower.value >= 0.0 {
        let gap = interval.upper.value - interval.lower.value;
        if gap <= 0.0 {
            return Ok(None);
        }
        point = interval.lower.value + gap * INSET_REL;
    }
    if point >= variable_cap {
        let gap = interval.upper.value - interval.lower.value;
        if gap <= 0.0 {
            return Ok(None);
        }
        point = interval.upper.value - gap * INSET_REL;
    }
    if point < 0.0 || point >= variable_cap {
        return Ok(None);
    }
    Ok(
        (point > interval.lower.value && point < interval.upper.value
            || (!interval.upper.open && !interval.lower.open && point == interval.upper.value))
            .then_some(point),
    )
}

fn normalized_compare(predicted: f64, solved: f64) -> (f64, bool) {
    let difference = (predicted - solved).abs();
    let relative = difference / predicted.abs().max(solved.abs()).max(1e-300);
    (relative, difference <= VERIFY_ABS || relative <= VERIFY_REL)
}

pub fn run(path: &str, output: Option<&str>) -> Result<Value, String> {
    let spec_path = PathBuf::from(path);
    let text = std::fs::read_to_string(&spec_path)
        .map_err(|error| format!("cannot read waste budget {path}: {error}"))?;
    let dir = spec_path.parent().unwrap_or_else(|| Path::new("."));
    let result = run_doc(&text, dir)?;
    waste::output_doc(&result, output)?;
    Ok(result)
}

fn run_doc(text: &str, dir: &Path) -> Result<Value, String> {
    let mut b: WasteBudgetSpec = serde_json::from_str(text)
        .map_err(|error| format!("cannot parse waste budget: {error}"))?;
    canonicalize_constituents(&mut b)?;
    if b.schema != "actinv-waste-budget-1" {
        return Err(format!("unsupported waste budget schema '{}'", b.schema));
    }
    waste::validate_targets(&b.targets)?;
    if !matches!(
        b.target_class,
        WasteClass::A | WasteClass::B | WasteClass::C
    ) {
        return Err("target_class must be A, B or C".into());
    }
    if b.impurities.is_empty() {
        return Err("impurities must name at least one element".into());
    }
    for (element, weight) in b.matrix.iter().chain(&b.impurities) {
        if !weight.is_finite() || *weight < 0.0 {
            return Err(format!("{element}: wt% must be finite and nonnegative"));
        }
        if element.eq_ignore_ascii_case(&b.balance) {
            return Err(format!(
                "balance element {} cannot be listed in matrix/impurities",
                b.balance
            ));
        }
    }
    if let Some(element) = b
        .matrix
        .keys()
        .find(|element| b.impurities.contains_key(*element))
    {
        return Err(format!("{element} occurs in both matrix and impurities"));
    }
    let matrix_total: f64 = b.matrix.values().sum();
    let impurity_total: f64 = b.impurities.values().sum();
    if !matrix_total.is_finite() || !impurity_total.is_finite() || matrix_total >= 100.0 {
        return Err("matrix leaves no positive balance-element composition".into());
    }
    full_composition(&b.balance, &b.matrix, &b.impurities)?;
    let geometry = geometry(&b)?;
    let (base_text, base_label) = base_spec(&b.base_spec, dir)?;
    let base: Value = serde_json::from_str(&base_text)
        .map_err(|error| format!("cannot parse base spec: {error}"))?;
    budget::check_linear_base(&base)?;
    let rules = waste::load_rule_pack(&b.rules, dir)?;
    for &step in &b.targets {
        let (_, unknown) = external_at(&b.external_tritium, step)?;
        if unknown {
            return Err("class budget refuses required-but-undeclared external tritium".into());
        }
    }

    let elements: BTreeSet<String> = std::iter::once(b.balance.clone())
        .chain(b.matrix.keys().cloned())
        .chain(b.impurities.keys().cloned())
        .collect();
    let mut cache = actinv_core::run::PreparedCache::new();
    let mut basis: BTreeMap<String, Basis> = BTreeMap::new();
    let mut solve_records = Vec::new();
    for element in &elements {
        let composition = BTreeMap::from([(element.clone(), 100.0)]);
        let (run, _ms) = budget::solve_doc(
            &base,
            &composition,
            &format!("waste budget basis {element}"),
            &mut cache,
        )?;
        let mut activities = BTreeMap::new();
        let mut sums = BTreeMap::new();
        let mut times = BTreeMap::new();
        for &step in &b.targets {
            let row = budget::step_of(&run, step)?;
            let activity = step_activities(row)?;
            let time = row
                .get("t_s")
                .and_then(Value::as_f64)
                .ok_or("element solve step lacks t_s")?;
            let total_bq: BTreeMap<String, f64> = activity
                .iter()
                .map(|(key, value)| (key.clone(), value * b.mass_g))
                .collect();
            let evaluation = evaluate_component(
                &rules,
                b.waste_type,
                geometry,
                &total_bq,
                &b.nuclide_properties,
            )?;
            if evaluation.coverage != actinv_core::waste::Coverage::Complete {
                return Err(format!(
                    "basis {element} has incomplete property coverage at step {step}: {:?}",
                    evaluation.unknown_nuclides
                ));
            }
            let cs = constraints(&evaluation, b.target_class, step)?;
            sums.extend(cs);
            activities.insert(step, activity);
            times.insert(step, time);
        }
        let normalized_sums: BTreeMap<String, f64> = sums
            .iter()
            .map(|(key, value)| {
                (
                    format!(
                        "{}:{}:{}",
                        key.step,
                        key.table,
                        key.column
                            .map_or_else(|| "all".into(), |column| column.to_string())
                    ),
                    *value,
                )
            })
            .collect();
        solve_records.push(json!({"element":element,"mode":run.get("mode"),"steps":activities,"times_s":times,"class_constraint_normalized_sums":normalized_sums}));
        basis.insert(element.clone(), Basis { activities, sums });
    }

    // External retained/fuel tritium is a fixed affine activity, not an element-basis response.
    // Store it as a separate evaluated offset in each class constraint.
    let mut external_basis = BTreeMap::new();
    for &step in &b.targets {
        let (h3, unknown) = external_at(&b.external_tritium, step)?;
        if unknown {
            return Err("class budget refuses required-but-undeclared external tritium".into());
        }
        let mut activities = BTreeMap::new();
        if h3 > 0.0 {
            activities.insert("H-3".to_string(), h3);
        }
        let evaluation = evaluate_component(
            &rules,
            b.waste_type,
            geometry,
            &activities,
            &b.nuclide_properties,
        )?;
        if evaluation.coverage != actinv_core::waste::Coverage::Complete {
            return Err(format!(
                "external H-3 lacks required nuclide properties at step {step}"
            ));
        }
        external_basis.extend(constraints(&evaluation, b.target_class, step)?);
    }
    // Include the external offset only once in each composed prediction. The external-basis evaluation above
    // is kept distinct from all pure-element bases so no elemental weighting can duplicate it.
    let matrix_comp = full_composition(&b.balance, &b.matrix, &BTreeMap::new())?;
    let at_spec_comp = full_composition(&b.balance, &b.matrix, &b.impurities)?;
    let no_external = |composition: &BTreeMap<String, f64>| -> BTreeMap<ConstraintKey, f64> {
        let mut out = BTreeMap::new();
        for step in &b.targets {
            for key in basis[&b.balance].sums.keys() {
                if key.step == *step {
                    let sum = composition
                        .iter()
                        .map(|(element, weight)| {
                            weight / 100.0 * basis[element].sums.get(key).copied().unwrap_or(0.0)
                        })
                        .sum();
                    out.insert(*key, sum);
                }
            }
        }
        out
    };
    let matrix_sums = no_external(&matrix_comp);
    let add_external = |values: &mut BTreeMap<ConstraintKey, f64>| {
        for (key, value) in &external_basis {
            *values.entry(*key).or_default() += value;
        }
    };
    let mut matrix_sums = matrix_sums;
    add_external(&mut matrix_sums);

    // For linear budgets the point response of a composition is composed from the basis sums, then checked
    // against a fresh full run at every target. Endpoint strictness is read from the shared core evaluator.
    let mut all_intervals = Vec::new();
    for impurity in b.impurities.keys() {
        let others: BTreeMap<String, f64> = b
            .impurities
            .iter()
            .filter(|(key, _)| *key != impurity)
            .map(|(key, value)| (key.clone(), *value))
            .collect();
        let base_comp = full_composition(&b.balance, &b.matrix, &others)?;
        let base_sums = no_external(&base_comp);
        let mut base_sums = base_sums;
        add_external(&mut base_sums);
        let bal_basis = &basis[&b.balance].sums;
        let imp_basis = &basis[impurity].sums;
        let slopes: BTreeMap<ConstraintKey, f64> = base_sums
            .keys()
            .map(|key| {
                (
                    *key,
                    (imp_basis.get(key).copied().unwrap_or(0.0)
                        - bal_basis.get(key).copied().unwrap_or(0.0))
                        / 100.0,
                )
            })
            .collect();
        let cap = 100.0 - b.matrix.values().sum::<f64>() - others.values().sum::<f64>();
        let interval = interval_for(&base_sums, &slopes, cap, |key, edge| {
            endpoint_open(
                key,
                b.target_class,
                edge,
                &b.matrix,
                &b.balance,
                &others,
                impurity,
                &rules,
                b.waste_type,
                geometry,
                &b.nuclide_properties,
                &b.external_tritium,
                &basis,
                &b.targets,
                false,
            )
        })?;
        all_intervals.push((impurity.clone(), "others_at_spec", interval));

        let sole_base = full_composition(&b.balance, &b.matrix, &BTreeMap::new())?;
        let sole_sums_raw = no_external(&sole_base);
        let mut sole_sums = sole_sums_raw;
        add_external(&mut sole_sums);
        let sole_slopes: BTreeMap<ConstraintKey, f64> = sole_sums
            .keys()
            .map(|key| {
                (
                    *key,
                    (imp_basis.get(key).copied().unwrap_or(0.0)
                        - bal_basis.get(key).copied().unwrap_or(0.0))
                        / 100.0,
                )
            })
            .collect();
        let sole_cap = 100.0 - b.matrix.values().sum::<f64>();
        let sole = interval_for(&sole_sums, &sole_slopes, sole_cap, |key, edge| {
            endpoint_open(
                key,
                b.target_class,
                edge,
                &b.matrix,
                &b.balance,
                &BTreeMap::new(),
                impurity,
                &rules,
                b.waste_type,
                geometry,
                &b.nuclide_properties,
                &b.external_tritium,
                &basis,
                &b.targets,
                false,
            )
        })?;
        all_intervals.push((impurity.clone(), "sole", sole));
    }

    let mut joint_slope = BTreeMap::new();
    for key in matrix_sums.keys() {
        let total = b
            .impurities
            .iter()
            .map(|(element, weight)| {
                weight
                    * (basis[element].sums.get(key).copied().unwrap_or(0.0)
                        - basis[&b.balance].sums.get(key).copied().unwrap_or(0.0))
                    / 100.0
            })
            .sum();
        joint_slope.insert(*key, total);
    }
    let joint_cap = if impurity_total > 0.0 {
        (100.0 - matrix_total) / impurity_total
    } else {
        0.0
    };
    let joint = if impurity_total > 0.0 {
        interval_for(&matrix_sums, &joint_slope, joint_cap, |key, edge| {
            endpoint_open(
                key,
                b.target_class,
                edge,
                &b.matrix,
                &b.balance,
                &b.impurities,
                "",
                &rules,
                b.waste_type,
                geometry,
                &b.nuclide_properties,
                &b.external_tritium,
                &basis,
                &b.targets,
                true,
            )
        })?
    } else {
        Interval {
            lower: Edge {
                value: 0.0,
                open: false,
                binding: None,
                contributors: Vec::new(),
                contributor_count: 0,
            },
            upper: Edge {
                value: 0.0,
                open: false,
                binding: None,
                contributors: Vec::new(),
                contributor_count: 0,
            },
            feasible: true,
            status: "no_response",
            no_response: true,
        }
    };

    // Verification points are inward of every upper edge (including the positive-balance bound); an
    // interval with a lower bound is inset inward from that side to stay inside narrow feasible windows.
    let mut verification_inputs = vec![("at_spec".to_string(), at_spec_comp)];
    for (element, kind, interval) in &all_intervals {
        if !interval.feasible {
            continue;
        }
        let cap = if *kind == "sole" {
            100.0 - matrix_total
        } else {
            100.0 - matrix_total - impurity_total + b.impurities[element]
        };
        if let Some(x) = inset_point(interval, cap)? {
            let mut impurities = if *kind == "sole" {
                BTreeMap::new()
            } else {
                b.impurities
                    .iter()
                    .filter(|(key, _)| *key != element)
                    .map(|(key, value)| (key.clone(), *value))
                    .collect()
            };
            impurities.insert(element.clone(), x);
            verification_inputs.push((
                format!("{kind}_{element}_interior"),
                full_composition(&b.balance, &b.matrix, &impurities)?,
            ));
        }
    }
    if joint.feasible {
        if let Some(k) = inset_point(&joint, joint_cap)? {
            let scaled = b
                .impurities
                .iter()
                .map(|(element, weight)| (element.clone(), weight * k))
                .collect();
            verification_inputs.push((
                "joint_interior".into(),
                full_composition(&b.balance, &b.matrix, &scaled)?,
            ));
        }
    }

    let mut verification_rows = Vec::new();
    let mut all_verified = true;
    for (id, composition) in &verification_inputs {
        let (run, _ms) = budget::solve_doc(
            &base,
            composition,
            &format!("waste class verify {id}"),
            &mut cache,
        )?;
        let mut per_target = Vec::new();
        for &step in &b.targets {
            let row = budget::step_of(&run, step)?;
            let activity_per_g = step_activities(row)?;
            let activity_bq: BTreeMap<String, f64> = activity_per_g
                .iter()
                .map(|(key, value)| (key.clone(), value * b.mass_g))
                .collect();
            let (h3, unknown) = external_at(&b.external_tritium, step)?;
            if unknown {
                return Err("class budget refuses required-but-undeclared external tritium".into());
            }
            let mut activity_bq = activity_bq;
            if h3 > 0.0 {
                merge_activity(&mut activity_bq, "H3", h3)?;
            }
            let evaluation = evaluate_component(
                &rules,
                b.waste_type,
                geometry,
                &activity_bq,
                &b.nuclide_properties,
            )?;
            if evaluation.coverage != actinv_core::waste::Coverage::Complete {
                return Err(format!(
                    "verification point {id} has incomplete coverage at step {step}"
                ));
            }
            let mut predicted_activity = scaled_activities(&basis, composition, step, b.mass_g)?;
            if h3 > 0.0 {
                merge_activity(&mut predicted_activity, "H3", h3)?;
            }
            let predicted = evaluate_component(
                &rules,
                b.waste_type,
                geometry,
                &predicted_activity,
                &b.nuclide_properties,
            )?;
            let predicted_constraints = constraints(&predicted, b.target_class, step)?;
            let solved_constraints = constraints(&evaluation, b.target_class, step)?;
            let mut sums = Vec::new();
            let mut pass = true;
            for (key, pred) in predicted_constraints {
                let solved = solved_constraints.get(&key).copied().unwrap_or(0.0);
                let (rel, agrees) = normalized_compare(pred, solved);
                pass &= agrees;
                sums.push(json!({"table":key.table,"column":key.column,"predicted_normalized_sum":pred,"solved_normalized_sum":solved,"absolute_deviation":(pred-solved).abs(),"relative_deviation":rel,"pass":agrees}));
            }
            let class_passes = evaluation.class == b.target_class
                || class_at_most(evaluation.class, b.target_class);
            if id != "at_spec" {
                pass &= class_passes;
            }
            all_verified &= pass;
            per_target.push(json!({"step":step,"t_s":row["t_s"],"verification_inventory_activity_bq":activity_bq,"external_tritium_activity_bq":h3,"class":evaluation.class,"class_passes":class_passes,"coverage":evaluation.coverage,"row_fractions":evaluation.row_fractions,"constraint_sums":sums,"pass":pass}));
        }
        let joint_scale_factor = if id == "joint_interior" {
            Some(
                b.impurities
                    .keys()
                    .map(|element| composition.get(element).copied().unwrap_or(0.0))
                    .sum::<f64>()
                    / impurity_total,
            )
        } else {
            None
        };
        verification_rows.push(json!({"id":id,"composition_wt_pct":composition,"scale_factor":joint_scale_factor,"targets":per_target}));
    }

    let mut impurity_output = Map::new();
    for element in b.impurities.keys() {
        let other = all_intervals
            .iter()
            .find(|(name, kind, _)| name == element && *kind == "others_at_spec")
            .unwrap();
        let sole = all_intervals
            .iter()
            .find(|(name, kind, _)| name == element && *kind == "sole")
            .unwrap();
        let others_id = format!("others_at_spec_{element}_interior");
        let sole_id = format!("sole_{element}_interior");
        impurity_output.insert(element.clone(), json!({"spec_wt_pct":b.impurities[element],"others_at_spec":interval_json(&other.2,"wt_pct",&others_id,&verification_rows,Some(element)),"sole":interval_json(&sole.2,"wt_pct",&sole_id,&verification_rows,Some(element))}));
    }
    let doc = json!({
        "schema":"actinv-waste-budget-result-1",
        "budget_sha256":sha256(text.as_bytes()),
        "base_spec":base_label,
        "base_spec_sha256":sha256(base_text.as_bytes()),
        "rules":{"id":rules.id,"version":rules.version,"sha256":rules.sha256},
        "target_class":b.target_class,
        "geometry":geometry,
        "waste_type":b.waste_type,
        "solver":{"mode":"coupled","prune":"reach"},
        "external_tritium":{"status":waste::external_status(&b.external_tritium),"activity_bq_by_step":external_map(&b.external_tritium,&b.targets)?},
        "basis_solves":solve_records,
        "impurities":impurity_output,
        "joint_specification_margin":interval_json(&joint,"scale_factor","joint_interior",&verification_rows,None),
        "verification":{"tolerance_relative":VERIFY_REL,"tolerance_absolute_near_zero":VERIFY_ABS,"inset_relative":INSET_REL,"points":verification_rows,"verified":all_verified},
    });
    if !all_verified {
        return Err("waste class budget verification failed".into());
    }
    Ok(doc)
}

fn class_at_most(value: WasteClass, target: WasteClass) -> bool {
    use WasteClass::*;
    matches!((value, target), (A, A | B | C) | (B, B | C) | (C, C))
}

fn interval_json(
    i: &Interval,
    quantity: &str,
    point_id: &str,
    points: &[Value],
    variable: Option<&str>,
) -> Value {
    let point = points
        .iter()
        .find(|point| point.get("id").and_then(Value::as_str) == Some(point_id));
    let verified_value = point.and_then(|point| {
        if quantity == "scale_factor" {
            point.get("scale_factor").and_then(Value::as_f64)
        } else {
            point.get("composition_wt_pct").and_then(|composition| {
                variable
                    .and_then(|name| composition.get(name))
                    .and_then(Value::as_f64)
            })
        }
    });
    let (lower_key, upper_key) = if quantity == "scale_factor" {
        ("lower_scale_factor", "upper_supremum_scale_factor")
    } else {
        ("lower_wt_pct", "upper_supremum_wt_pct")
    };
    let mut out = json!({"status":i.status,"feasible":i.feasible,"no_response":i.no_response,"lower_open":i.lower.open,"supremum_attained":!i.upper.open,"verified_value":verified_value,"verification_point_id":if verified_value.is_some(){json!(point_id)}else{Value::Null},"binding_constraint":i.upper.binding.as_ref().map(|key|json!({"step":key.step,"table":key.table,"column":key.column,"contributor_count":i.upper.contributor_count,"strict":i.upper.open,"nuclides":i.upper.contributors})),"lower_binding_constraint":i.lower.binding.as_ref().map(|key|json!({"step":key.step,"table":key.table,"column":key.column,"contributor_count":i.lower.contributor_count,"strict":i.lower.open,"nuclides":i.lower.contributors}))});
    out[lower_key] = json!(i.lower.value);
    out[upper_key] = json!(i.upper.value);
    out
}

fn merge_activity(
    activity: &mut BTreeMap<String, f64>,
    raw_key: &str,
    addition: f64,
) -> Result<(), String> {
    let canonical = actinv_core::waste::normalize_nuclide_key(raw_key)?;
    let existing = activity
        .keys()
        .find(|key| {
            actinv_core::waste::normalize_nuclide_key(key)
                .ok()
                .as_deref()
                == Some(&canonical)
        })
        .cloned();
    if let Some(existing) = existing {
        let old = activity.remove(&existing).unwrap_or(0.0);
        let sum = old + addition;
        if !sum.is_finite() {
            return Err(format!("combined activity for {canonical} overflowed"));
        }
        activity.insert(canonical, sum);
    } else {
        activity.insert(canonical, addition);
    }
    Ok(())
}

fn external_map(
    external: &waste::ExternalTritium,
    targets: &[u64],
) -> Result<BTreeMap<String, f64>, String> {
    let mut result = BTreeMap::new();
    for step in targets {
        let (value, unknown) = external_at(external, *step)?;
        if unknown {
            return Err("class budget refuses required-but-undeclared external tritium".into());
        }
        result.insert(step.to_string(), value);
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn key() -> ConstraintKey {
        ConstraintKey {
            step: 1,
            table: 1,
            column: None,
        }
    }

    #[test]
    fn negative_gradient_creates_a_lower_composition_bound() {
        let interval = interval_for(
            &BTreeMap::from([(key(), 2.0)]),
            &BTreeMap::from([(key(), -1.0)]),
            3.0,
            |_, _| Ok((false, Vec::new(), 0)),
        )
        .unwrap();
        assert!(interval.feasible);
        assert_eq!(interval.lower.value, 1.0);
        assert_eq!(interval.upper.value, 3.0);
    }

    #[test]
    fn infeasible_constant_and_strict_boundary_are_rejected() {
        let over = interval_for(
            &BTreeMap::from([(key(), 1.1)]),
            &BTreeMap::new(),
            3.0,
            |_, _| Ok((false, Vec::new(), 0)),
        )
        .unwrap();
        assert!(!over.feasible);
        let strict = interval_for(
            &BTreeMap::from([(key(), 1.0)]),
            &BTreeMap::new(),
            3.0,
            |_, _| Ok((true, Vec::new(), 1)),
        )
        .unwrap();
        assert!(!strict.feasible);
    }

    #[test]
    fn constant_threshold_with_positive_composition_strictness_is_a_closed_zero_singleton() {
        let interval = interval_for(
            &BTreeMap::from([(key(), 1.0)]),
            &BTreeMap::from([(key(), 0.0)]),
            10.0,
            |_, value| Ok((value > 0.0, Vec::new(), if value > 0.0 { 1 } else { 0 })),
        )
        .unwrap();
        assert!(interval.feasible);
        assert_eq!(interval.status, "singleton");
        assert_eq!(interval.lower.value, 0.0);
        assert_eq!(interval.upper.value, 0.0);
        assert!(!interval.upper.open);
        assert_eq!(inset_point(&interval, 10.0).unwrap(), Some(0.0));
    }

    #[test]
    fn no_response_and_positive_balance_cap_keep_distinct_statuses() {
        let flat = interval_for(
            &BTreeMap::from([(key(), 0.25)]),
            &BTreeMap::from([(key(), 0.0)]),
            2.0,
            |_, _| Ok((false, Vec::new(), 0)),
        )
        .unwrap();
        assert!(flat.feasible && flat.no_response);
        assert_eq!(flat.status, "no_response");
        let cap = interval_for(
            &BTreeMap::from([(key(), 0.0)]),
            &BTreeMap::from([(key(), -0.1)]),
            2.0,
            |_, _| Ok((false, Vec::new(), 0)),
        )
        .unwrap();
        assert!(cap.feasible);
        assert_eq!(cap.status, "composition_bound");
        assert_eq!(cap.upper.value, 2.0);
        assert!(cap.upper.open);
    }
}
