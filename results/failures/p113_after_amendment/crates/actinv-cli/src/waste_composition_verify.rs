//! Solver-backed verification helpers for native fixed-rate composition responses.

use std::collections::{BTreeMap, BTreeSet};

use actinv_core::run::{PreparedRun, RunResult};
use actinv_core::waste::{
    evaluate_component, normalize_nuclide_key, ClassConstraint, Evaluation, NuclideProperties,
    RulePack, WasteGeometry, WasteType,
};
use actinv_data::composition::{material_key, symbol_of, MaterialKey};
use actinv_data::decay::Nuclide;
use serde::Serialize;
use serde_json::{json, Value};

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct StepInventory {
    pub t_s: f64,
    pub activities_bq_per_g: BTreeMap<String, f64>,
    pub atoms_per_g: BTreeMap<String, f64>,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct CoverageScan {
    pub complete: bool,
    pub reasons: Vec<String>,
    pub evidence: Value,
}

fn nuclide_name(za: i32, liso: i32) -> String {
    let symbol = symbol_of(za / 1000);
    if liso > 0 {
        format!("{symbol}{}m{liso}", za % 1000)
    } else {
        format!("{symbol}{}", za % 1000)
    }
}

fn rtyp_has_alpha(rtyp: f64) -> bool {
    let encoded = format!("{rtyp:.6}");
    encoded
        .trim_end_matches('0')
        .trim_end_matches('.')
        .bytes()
        .any(|digit| digit == b'4')
}

fn alpha_emitting(nuclide: &Nuclide) -> bool {
    nuclide
        .modes
        .iter()
        .any(|mode| mode.br > 0.0 && rtyp_has_alpha(mode.rtyp))
}

/// Derive properties only for nuclides that can contribute positive activity.
/// Missing decay records stay absent so the waste evaluator reports unknown coverage.
pub fn derive_properties(
    prepared: &PreparedRun,
    active_nuclides: &BTreeSet<String>,
) -> Result<BTreeMap<String, NuclideProperties>, String> {
    let decays = prepared.decay_nuclides();
    let mut properties = BTreeMap::new();
    let mut seen = BTreeSet::new();
    for raw in active_nuclides {
        let canonical = normalize_nuclide_key(raw)?;
        if !seen.insert(canonical.clone()) {
            return Err(format!(
                "duplicate active nuclide identity after normalization: {canonical}"
            ));
        }
        let (za, liso) = match material_key(&canonical)? {
            MaterialKey::Nuclide { za, liso, .. } => (za, liso),
            MaterialKey::Element(_) => {
                return Err(format!("active nuclide key '{raw}' is an element"));
            }
        };
        let Some(nuclide) = decays.get(&(za, liso)) else {
            continue;
        };
        let half_life_s = nuclide.half_life;
        let lambda = nuclide.lambda();
        if !lambda.is_finite()
            || lambda <= 0.0
            || !half_life_s.is_finite()
            || half_life_s <= 0.0
            || nuclide.nst == 1
        {
            return Err(format!(
                "positive activity for stable or invalid decay state {}",
                nuclide_name(za, liso)
            ));
        }
        let property_key = nuclide_name(za, liso);
        if properties.contains_key(&property_key) {
            return Err(format!(
                "duplicate active nuclide identity after normalization: {property_key}"
            ));
        }
        properties.insert(
            property_key,
            NuclideProperties {
                z: nuclide.z(),
                half_life_s,
                alpha_emitting: alpha_emitting(nuclide),
            },
        );
    }
    Ok(properties)
}

fn canonical_nonnegative_map<I>(items: I, label: &str) -> Result<BTreeMap<String, f64>, String>
where
    I: IntoIterator<Item = (String, f64)>,
{
    let mut out = BTreeMap::new();
    for (raw, value) in items {
        if !value.is_finite() || value < 0.0 {
            return Err(format!(
                "{label} value for '{raw}' must be finite and nonnegative"
            ));
        }
        let key = normalize_nuclide_key(&raw)?;
        if out.insert(key.clone(), value).is_some() {
            return Err(format!(
                "duplicate {label} identity after normalization: {key}"
            ));
        }
    }
    Ok(out)
}

/// Extract one selected step, validating canonical identities and all numeric values.
pub fn selected_step(run: &RunResult, step: u64) -> Result<StepInventory, String> {
    let mut matches = run.steps.iter().filter(|row| row.step as u64 == step);
    let row = matches
        .next()
        .ok_or_else(|| format!("run has no selected step {step}"))?;
    if matches.next().is_some() {
        return Err(format!("run contains duplicate selected step {step}"));
    }
    if !row.t_s.is_finite() || row.t_s < 0.0 {
        return Err(format!("run step {step} has invalid time"));
    }
    let activities = canonical_nonnegative_map(
        row.activity_Bq_per_g
            .iter()
            .map(|(key, value)| (key.clone(), *value)),
        "activity",
    )?;
    let mut atoms = BTreeMap::new();
    for item in &row.inventory {
        if !item.atoms_per_g.is_finite() || item.atoms_per_g < 0.0 {
            return Err(format!(
                "run step {step} inventory for {} must be finite and nonnegative",
                item.nuclide
            ));
        }
        let key = normalize_nuclide_key(&item.nuclide)?;
        if atoms.insert(key.clone(), item.atoms_per_g).is_some() {
            return Err(format!(
                "duplicate inventory identity after normalization: {key}"
            ));
        }
    }
    Ok(StepInventory {
        t_s: row.t_s,
        activities_bq_per_g: activities,
        atoms_per_g: atoms,
    })
}

#[derive(Clone, Debug)]
struct TargetMetadata {
    convergence_flag: Option<String>,
    limitations: Vec<String>,
}

fn target_metadata(index: &Value) -> Result<BTreeMap<(i32, i32), TargetMetadata>, String> {
    let targets = index
        .get("targets")
        .and_then(Value::as_array)
        .ok_or("prepared activation index has no targets array")?;
    let mut out = BTreeMap::new();
    for target in targets {
        let za = target
            .get("za")
            .and_then(Value::as_i64)
            .and_then(|value| i32::try_from(value).ok())
            .ok_or("prepared activation target has invalid ZA")?;
        let liso = target
            .get("liso")
            .and_then(Value::as_i64)
            .and_then(|value| i32::try_from(value).ok())
            .ok_or("prepared activation target has invalid LISO")?;
        if za <= 0 || za / 1000 <= 0 || za % 1000 <= 0 || liso < 0 {
            return Err(format!(
                "prepared activation target has invalid physical identity {za}/{liso}"
            ));
        }
        let canonical_identity = nuclide_name(za, liso);
        match material_key(&canonical_identity)? {
            MaterialKey::Nuclide {
                za: parsed_za,
                liso: parsed_liso,
                ..
            } if (parsed_za, parsed_liso) == (za, liso) => {}
            _ => {
                return Err(format!(
                    "prepared activation target identity is noncanonical: {za}/{liso}"
                ))
            }
        }
        let convergence_flag = match target.get("convergence_flag") {
            None | Some(Value::Null) => None,
            Some(Value::String(flag)) => Some(flag.clone()),
            Some(_) => {
                return Err(format!(
                    "prepared target {canonical_identity} convergence_flag is malformed"
                ))
            }
        };
        let limitations = match target.get("ledger") {
            None | Some(Value::Null) => Vec::new(),
            Some(Value::Array(entries)) => entries
                .iter()
                .map(|entry| {
                    entry
                        .as_str()
                        .map(str::to_owned)
                        .ok_or("prepared target ledger contains a non-string entry")
                })
                .collect::<Result<Vec<_>, _>>()?,
            Some(_) => return Err("prepared target ledger is not an array".into()),
        };
        if out
            .insert(
                (za, liso),
                TargetMetadata {
                    convergence_flag,
                    limitations,
                },
            )
            .is_some()
        {
            return Err(format!(
                "prepared activation index repeats target {za}/{liso}"
            ));
        }
    }
    Ok(out)
}

fn audit_reasons(ledger: &Value, reasons: &mut BTreeSet<String>) {
    let Some(completeness) = ledger.get("completeness") else {
        reasons.insert("run_audit_missing".into());
        return;
    };
    if completeness.get("status").and_then(Value::as_str) != Some("complete") {
        reasons.insert("run_audit_incomplete".into());
    }
    for channel in ["reaction_channel", "decay_channel"] {
        if completeness
            .get(channel)
            .and_then(|value| value.get("defects"))
            .and_then(Value::as_array)
            .is_none()
        {
            reasons.insert(format!("run_audit_malformed_{channel}"));
        }
    }
    if completeness
        .get("unquantified")
        .and_then(Value::as_array)
        .is_none()
    {
        reasons.insert("run_audit_malformed_unquantified".into());
    }
    for channel in ["reaction_channel", "decay_channel"] {
        if let Some(defects) = completeness
            .get(channel)
            .and_then(|value| value.get("defects"))
            .and_then(Value::as_array)
        {
            for defect in defects {
                if let Some(class) = defect.get("class").and_then(Value::as_str) {
                    reasons.insert(format!("audit_{class}"));
                } else {
                    reasons.insert(format!("audit_{channel}_malformed_defect"));
                }
            }
        }
    }
    if let Some(items) = completeness.get("unquantified").and_then(Value::as_array) {
        for item in items {
            if let Some(class) = item.get("class").and_then(Value::as_str) {
                reasons.insert(format!("audit_{class}"));
            } else {
                reasons.insert("audit_unquantified_malformed".into());
            }
        }
    }
}

/// Inspect every positive inventory state against the exact prepared activation index.
pub fn scan_coverage(
    prepared: &PreparedRun,
    run: &RunResult,
    initial_composition_wt_percent: &BTreeMap<String, f64>,
    positive_neutron_exposure: bool,
) -> Result<CoverageScan, String> {
    let mut reasons = BTreeSet::new();
    audit_reasons(&run.ledger, &mut reasons);
    let targets = target_metadata(prepared.activation_index())?;
    for (element, weight) in initial_composition_wt_percent {
        if !weight.is_finite() || *weight < 0.0 {
            return Err(format!(
                "initial composition weight for {element} is invalid"
            ));
        }
    }
    let (initial_atoms, _) = actinv_data::composition::material_atoms_per_gram(
        initial_composition_wt_percent,
        "wt_percent",
        prepared.decay_nuclides(),
    )?;
    let mut initial_positive = Vec::<Value>::new();
    for ((za, liso), atoms_per_g) in initial_atoms {
        if !atoms_per_g.is_finite() || atoms_per_g < 0.0 {
            return Err("initial inventory contains invalid atoms/g".into());
        }
        if atoms_per_g == 0.0 {
            continue;
        }
        let canonical = nuclide_name(za, liso);
        let target = note_inventory_target(
            (za, liso),
            &canonical,
            &targets,
            positive_neutron_exposure,
            &mut reasons,
        );
        let has_decay = prepared.decay_nuclides().contains_key(&(za, liso));
        if !has_decay {
            reasons.insert(format!(
                "positive_initial_inventory_missing_decay_record:{canonical}"
            ));
        }
        initial_positive.push(json!({
            "nuclide":canonical,
            "atoms_per_g":atoms_per_g,
            "decay_record_present":has_decay,
            "target":target,
        }));
    }
    let mut reached = Vec::<Value>::new();
    for step in &run.steps {
        if !step.t_s.is_finite() || step.t_s < 0.0 {
            return Err(format!("run has invalid time on step {}", step.step));
        }
        for item in &step.inventory {
            if !item.atoms_per_g.is_finite() || item.atoms_per_g < 0.0 {
                return Err(format!("run has invalid atoms/g for {}", item.nuclide));
            }
            if item.atoms_per_g == 0.0 {
                continue;
            }
            let za = item
                .Z
                .checked_mul(1000)
                .and_then(|base| base.checked_add(item.A))
                .ok_or_else(|| format!("inventory ZA overflows for {}", item.nuclide))?;
            let key = (za, item.LISO);
            let canonical = normalize_nuclide_key(&item.nuclide)?;
            match material_key(&canonical)? {
                MaterialKey::Nuclide {
                    za: named_za,
                    liso: named_liso,
                    ..
                } if (named_za, named_liso) == key => {}
                _ => {
                    return Err(format!(
                        "inventory name/identity mismatch for {}",
                        item.nuclide
                    ))
                }
            }
            let target_evidence = note_inventory_target(
                key,
                &canonical,
                &targets,
                positive_neutron_exposure,
                &mut reasons,
            );
            reached.push(json!({
                "step":step.step,
                "nuclide":canonical,
                "atoms_per_g": item.atoms_per_g,
                "target":target_evidence,
            }));
        }
    }
    for (key, value) in [
        "composition_isotopes_absent_from_decay_library",
        "composition_elements_unknown",
        "targets_absent_from_decay_library",
        "products_no_evaluated_decay_data",
        "products_unmapped_to_leakage",
        "fission_no_yields_to_leakage",
        "fission_yield_products_to_leakage",
        "isomer_state_absent_from_decay_library_used_ground",
        "bulk_production_dropped",
        "decay_daughters_missing",
        "spontaneous_fission_branches_to_leakage",
        "decay_nuclides_from_fallback",
    ]
    .into_iter()
    .filter_map(|key| run.ledger.get(key).map(|value| (key, value)))
    {
        if !is_empty_or_zero(value) {
            reasons.insert(format!("run_ledger_{key}"));
        }
    }
    match run
        .ledger
        .get("negative_atoms_zeroed_per_step")
        .and_then(Value::as_array)
    {
        Some(values)
            if values
                .iter()
                .any(|value| value.as_f64().is_none_or(|x| !x.is_finite() || x != 0.0)) =>
        {
            reasons.insert("run_ledger_negative_atoms_zeroed".into());
        }
        Some(_) => {}
        None => {
            reasons.insert("run_ledger_negative_atoms_zeroed_missing_or_malformed".into());
        }
    }
    if let Some(flags) = run
        .ledger
        .get("library_convergence_flags")
        .and_then(Value::as_array)
    {
        if !flags.is_empty() {
            reasons.insert("run_ledger_library_convergence_flags".into());
        }
    }
    if let Some(limitations) = run
        .ledger
        .get("library_target_limitations")
        .and_then(Value::as_array)
    {
        if !limitations.is_empty() {
            reasons.insert("run_ledger_library_target_limitations".into());
        }
    }
    reached.sort_by(|left, right| {
        left["step"]
            .as_u64()
            .cmp(&right["step"].as_u64())
            .then(left["nuclide"].as_str().cmp(&right["nuclide"].as_str()))
    });
    let reasons = reasons.into_iter().collect::<Vec<_>>();
    Ok(CoverageScan {
        complete: reasons.is_empty(),
        evidence: json!({
            "audit":run.ledger.get("completeness").cloned().unwrap_or(Value::Null),
            "reached_positive_inventory":reached,
        "positive_neutron_exposure":positive_neutron_exposure,
        "initial_positive_inventory":initial_positive,
            "library_convergence_flags":run.ledger.get("library_convergence_flags").cloned().unwrap_or_else(|| json!([])),
            "library_target_limitations":run.ledger.get("library_target_limitations").cloned().unwrap_or_else(|| json!([])),
            "omission_ledger":run.ledger,
        }),
        reasons,
    })
}

fn note_inventory_target(
    key: (i32, i32),
    canonical: &str,
    targets: &BTreeMap<(i32, i32), TargetMetadata>,
    positive_neutron_exposure: bool,
    reasons: &mut BTreeSet<String>,
) -> Value {
    let metadata = targets.get(&key);
    if positive_neutron_exposure {
        match metadata {
            None => {
                reasons.insert(format!(
                    "positive_inventory_missing_activation_target:{canonical}"
                ));
            }
            Some(target) => {
                if target.convergence_flag.is_some() {
                    reasons.insert(format!("activation_target_convergence_flag:{canonical}"));
                }
                if !target.limitations.is_empty() {
                    reasons.insert(format!("activation_target_builder_limitations:{canonical}"));
                }
            }
        }
    }
    json!({
        "activation_target_present":metadata.is_some(),
        "target_convergence_flag":metadata.and_then(|value| value.convergence_flag.clone()),
        "target_builder_limitations":metadata.map(|value| value.limitations.clone()).unwrap_or_default(),
    })
}

fn is_empty_or_zero(value: &Value) -> bool {
    match value {
        Value::Null => true,
        Value::Bool(value) => !value,
        Value::Number(value) => value.as_f64().is_some_and(|number| number == 0.0),
        Value::String(value) => value.is_empty(),
        Value::Array(value) => value.is_empty(),
        Value::Object(value) => value.is_empty(),
    }
}

fn close(actual: f64, predicted: f64, relative: f64, absolute: f64) -> bool {
    actual.is_finite()
        && predicted.is_finite()
        && (actual - predicted).abs() <= absolute.max(relative * actual.abs().max(predicted.abs()))
}

fn value_map_close(
    left: &BTreeMap<String, f64>,
    right: &BTreeMap<String, f64>,
    relative: f64,
    absolute: f64,
) -> (bool, BTreeMap<String, f64>) {
    let keys: BTreeSet<String> = left.keys().chain(right.keys()).cloned().collect();
    let mut errors = BTreeMap::new();
    let mut pass = true;
    for key in keys {
        let a = left.get(&key).copied().unwrap_or(0.0);
        let b = right.get(&key).copied().unwrap_or(0.0);
        let error = (a - b).abs();
        pass &= close(a, b, relative, absolute);
        errors.insert(key, error);
    }
    (pass, errors)
}

fn l1(map: &BTreeMap<String, f64>) -> f64 {
    map.values().copied().sum()
}

fn l1_disagreement(errors: &BTreeMap<String, f64>) -> f64 {
    errors.values().copied().sum()
}

fn class_constraints(
    evaluation: &Evaluation,
) -> BTreeMap<(String, u8, Option<u8>), ClassConstraint> {
    evaluation
        .constraints
        .iter()
        .map(|constraint| {
            (
                (
                    format!("{:?}", constraint.target_class),
                    constraint.table,
                    constraint.column,
                ),
                constraint.clone(),
            )
        })
        .collect()
}

fn evaluation_agrees(left: &Evaluation, right: &Evaluation) -> (bool, Value) {
    let mut pass = left.class == right.class
        && left.calculated_only_class == right.calculated_only_class
        && left.coverage == right.coverage
        && left.unknown_nuclides == right.unknown_nuclides
        && left.unlisted_nuclides == right.unlisted_nuclides
        && close(
            left.unlisted_activity_bq,
            right.unlisted_activity_bq,
            1e-6,
            1e-12,
        );
    let lrows: BTreeMap<_, _> = left
        .row_fractions
        .iter()
        .map(|row| {
            (
                (
                    row.table,
                    row.column,
                    row.row_id.clone(),
                    row.nuclide.clone(),
                ),
                row,
            )
        })
        .collect();
    let rrows: BTreeMap<_, _> = right
        .row_fractions
        .iter()
        .map(|row| {
            (
                (
                    row.table,
                    row.column,
                    row.row_id.clone(),
                    row.nuclide.clone(),
                ),
                row,
            )
        })
        .collect();
    pass &= lrows.len() == left.row_fractions.len()
        && rrows.len() == right.row_fractions.len()
        && lrows.keys().eq(rrows.keys());
    let mut row_diffs = Vec::new();
    for key in lrows.keys().chain(rrows.keys()).collect::<BTreeSet<_>>() {
        let (Some(l), Some(r)) = (lrows.get(key), rrows.get(key)) else {
            pass = false;
            continue;
        };
        let limit_pass = match (l.limit, r.limit) {
            (None, None) => true,
            (Some(a), Some(b)) => close(a, b, 1e-6, 1e-12),
            _ => false,
        };
        let fraction_pass = match (l.fraction, r.fraction) {
            (None, None) => true,
            (Some(a), Some(b)) => close(a, b, 1e-6, 1e-12),
            _ => false,
        };
        let same = l.unit == r.unit
            && close(l.concentration, r.concentration, 1e-6, 1e-12)
            && limit_pass
            && fraction_pass;
        pass &= same;
        row_diffs.push(
            json!({"table":key.0,"column":key.1,"row_id":&key.2,"nuclide":&key.3,"pass":same}),
        );
    }
    let lc = class_constraints(left);
    let rc = class_constraints(right);
    pass &= lc.len() == left.constraints.len()
        && rc.len() == right.constraints.len()
        && lc.keys().eq(rc.keys());
    let mut constraint_diffs = Vec::new();
    for key in lc.keys().chain(rc.keys()).collect::<BTreeSet<_>>() {
        let (Some(l), Some(r)) = (lc.get(key), rc.get(key)) else {
            pass = false;
            continue;
        };
        let same = l.strict == r.strict
            && l.passes == r.passes
            && l.contributor_count == r.contributor_count
            && l.contributors == r.contributors
            && close(l.source_sum_fraction, r.source_sum_fraction, 1e-6, 1e-12)
            && close(l.normalized_sum, r.normalized_sum, 1e-6, 1e-12)
            && close(l.normalized_margin, r.normalized_margin, 1e-6, 1e-12);
        pass &= same;
        constraint_diffs
            .push(json!({"target_class":&key.0,"table":key.1,"column":key.2,"pass":same}));
    }
    (
        pass,
        json!({
            "class":left.class,
            "calculated_only_class":left.calculated_only_class,
            "coverage":left.coverage,
            "unknown_nuclides":left.unknown_nuclides,
            "unlisted_nuclides":left.unlisted_nuclides,
            "unlisted_activity_bq":left.unlisted_activity_bq,
            "row_comparisons":row_diffs,
            "constraint_comparisons":constraint_diffs,
            "pass":pass,
        }),
    )
}

fn add_external_h3(
    activity_per_g: &BTreeMap<String, f64>,
    mass_g: f64,
    h3_bq: f64,
) -> Result<BTreeMap<String, f64>, String> {
    if !mass_g.is_finite() || mass_g <= 0.0 || !h3_bq.is_finite() || h3_bq < 0.0 {
        return Err("witness mass or external H-3 activity is invalid".into());
    }
    let mut total = BTreeMap::new();
    for (key, value) in activity_per_g {
        let activity = value * mass_g;
        if !activity.is_finite() {
            return Err(format!("whole-component activity overflows for {key}"));
        }
        total.insert(key.clone(), activity);
    }
    if h3_bq > 0.0 {
        let entry = total.entry("H3".into()).or_insert(0.0);
        *entry += h3_bq;
        if !entry.is_finite() {
            return Err("whole-component H-3 activity overflows".into());
        }
    }
    Ok(total)
}

/// Compare a full composition run against its weighted basis and classify both external-H3 endpoints.
#[allow(clippy::too_many_arguments)]
pub fn verify_witness(
    pack: &RulePack,
    waste_type: WasteType,
    geometry: WasteGeometry,
    properties: &BTreeMap<String, NuclideProperties>,
    basis_steps: &BTreeMap<String, StepInventory>,
    composition: &BTreeMap<String, f64>,
    mass_g: f64,
    step: u64,
    actual_run: &RunResult,
    external_h3_bounds: Option<(f64, f64)>,
) -> Result<Value, String> {
    if !close(geometry.mass_g, mass_g, 0.0, 0.0) {
        return Err("witness mass differs from classification geometry".into());
    }
    let external_h3_bounds = external_h3_bounds.unwrap_or((0.0, 0.0));
    if !external_h3_bounds.0.is_finite()
        || !external_h3_bounds.1.is_finite()
        || external_h3_bounds.0 < 0.0
        || external_h3_bounds.1 < external_h3_bounds.0
    {
        return Err("external H-3 bounds must be finite and ordered".into());
    }
    let direct = selected_step(actual_run, step)?;
    for (element, basis) in basis_steps {
        if !basis.t_s.is_finite() || basis.t_s.to_bits() != direct.t_s.to_bits() {
            return Err(format!(
                "basis and witness times differ at step {step} for {element}"
            ));
        }
    }
    let mut predicted_activity = BTreeMap::<String, f64>::new();
    let mut predicted_atoms = BTreeMap::<String, f64>::new();
    for (element, weight) in composition {
        if !weight.is_finite() || *weight < 0.0 {
            return Err(format!("witness has invalid weight for {element}"));
        }
        let basis = basis_steps
            .get(element)
            .ok_or_else(|| format!("witness has no pure-element basis for {element}"))?;
        for (key, value) in &basis.activities_bq_per_g {
            *predicted_activity.entry(key.clone()).or_default() += weight / 100.0 * value;
        }
        for (key, value) in &basis.atoms_per_g {
            *predicted_atoms.entry(key.clone()).or_default() += weight / 100.0 * value;
        }
    }
    if predicted_activity.values().any(|value| !value.is_finite())
        || predicted_atoms.values().any(|value| !value.is_finite())
    {
        return Err("weighted native basis response overflowed".into());
    }
    let (activity_pass, activity_errors) = value_map_close(
        &predicted_activity,
        &direct.activities_bq_per_g,
        1e-6,
        1e-12,
    );
    let predicted_activity_l1 = l1(&predicted_activity);
    let direct_activity_l1 = l1(&direct.activities_bq_per_g);
    if !predicted_activity_l1.is_finite() || !direct_activity_l1.is_finite() {
        return Err("activity L1 total overflowed".into());
    }
    let activity_l1_error = l1_disagreement(&activity_errors);
    if !activity_l1_error.is_finite() {
        return Err("activity L1 disagreement overflowed".into());
    }
    let activity_l1_pass =
        activity_l1_error <= 1e-12_f64.max(1e-6 * predicted_activity_l1.max(direct_activity_l1));
    let (_, atom_errors) =
        value_map_close(&predicted_atoms, &direct.atoms_per_g, 0.0, f64::INFINITY);
    let atom_l1_error: f64 = atom_errors.values().sum();
    let predicted_atom_l1 = l1(&predicted_atoms);
    let direct_atom_l1 = l1(&direct.atoms_per_g);
    if !predicted_atom_l1.is_finite() || !direct_atom_l1.is_finite() || !atom_l1_error.is_finite() {
        return Err("atom inventory L1 total overflowed".into());
    }
    let atom_l1_limit = 1e-12_f64.max(1e-6 * predicted_atom_l1.max(direct_atom_l1));
    let atom_l1_pass = atom_l1_error <= atom_l1_limit;
    let mut external_checks = Vec::new();
    let mut external_pass = true;
    for (endpoint, h3) in [
        ("lower", external_h3_bounds.0),
        ("upper", external_h3_bounds.1),
    ] {
        let predicted_total = add_external_h3(&predicted_activity, mass_g, h3)?;
        let direct_total = add_external_h3(&direct.activities_bq_per_g, mass_g, h3)?;
        let predicted_eval =
            evaluate_component(pack, waste_type, geometry, &predicted_total, properties)?;
        let direct_eval =
            evaluate_component(pack, waste_type, geometry, &direct_total, properties)?;
        let (eval_pass, eval_comparison) = evaluation_agrees(&predicted_eval, &direct_eval);
        external_pass &= eval_pass;
        external_checks.push(json!({
            "endpoint":endpoint,
            "external_h3_activity_bq":h3,
            "predicted_activity_bq":predicted_total,
            "solved_activity_bq":direct_total,
            "predicted_evaluation":predicted_eval,
            "solved_evaluation":direct_eval,
            "comparison":eval_comparison,
            "pass":eval_pass,
        }));
    }
    let pass = activity_pass && activity_l1_pass && atom_l1_pass && external_pass;
    Ok(json!({
        "step":step,
        "t_s":direct.t_s,
        "composition_wt_percent":composition,
        "predicted_activity_bq_per_g":predicted_activity,
        "solved_activity_bq_per_g":direct.activities_bq_per_g,
        "activity_absolute_errors_bq_per_g":activity_errors,
        "activity_per_nuclide_pass":activity_pass,
        "predicted_activity_l1_bq_per_g":predicted_activity_l1,
        "solved_activity_l1_bq_per_g":direct_activity_l1,
        "activity_l1_absolute_error_bq_per_g":activity_l1_error,
        "activity_l1_pass":activity_l1_pass,
        "predicted_atoms_per_g":predicted_atoms,
        "solved_atoms_per_g":direct.atoms_per_g,
        "atom_absolute_errors_per_g":atom_errors,
        "atom_componentwise_error_status":"diagnostic_only_not_a_gate",
        "predicted_atom_l1_per_g":predicted_atom_l1,
        "solved_atom_l1_per_g":direct_atom_l1,
        "atom_l1_absolute_error_per_g":atom_l1_error,
        "atom_l1_limit_per_g":atom_l1_limit,
        "atom_l1_pass":atom_l1_pass,
        "external_h3_checks":external_checks,
        "activity_tolerance":{"relative":1e-6,"absolute_bq_per_g":1e-12},
        "atom_tolerance":{"criterion":"L1","relative":1e-6,"absolute_atoms_per_g":1e-12},
        "pass":pass,
    }))
}

#[cfg(test)]
mod tests {
    use super::{
        add_external_h3, alpha_emitting, audit_reasons, close, evaluation_agrees, l1,
        l1_disagreement, note_inventory_target, target_metadata,
    };
    use actinv_core::waste::{
        evaluate_component, NuclideProperties, RulePack, WasteGeometry, WasteType,
    };
    use actinv_data::decay::{Mode, Nuclide};
    use serde_json::json;
    use std::collections::{BTreeMap, BTreeSet};

    fn nuclide(modes: Vec<(f64, f64)>) -> Nuclide {
        Nuclide {
            mat: 1,
            za: 95_241,
            awr: 241.0,
            liso: 0,
            nst: 0,
            half_life: 1.0,
            d_half_life: 0.0,
            energies: Vec::new(),
            modes: modes
                .into_iter()
                .map(|(rtyp, br)| Mode {
                    rtyp,
                    rfs: 0.0,
                    q: 0.0,
                    dq: 0.0,
                    br,
                    dbr: 0.0,
                })
                .collect(),
            spectra: Vec::new(),
        }
    }

    #[test]
    fn alpha_metadata_uses_positive_branch_composite_rtyp_digits() {
        assert!(alpha_emitting(&nuclide(vec![(1.0, 0.4), (1.4, 0.6)])));
        assert!(!alpha_emitting(&nuclide(vec![(1.4, 0.0), (1.0, 1.0)])));
        assert!(!alpha_emitting(&nuclide(vec![(1.0, 1.0)])));
    }

    #[test]
    fn activation_index_requires_unique_targets_and_retains_reached_metadata() {
        let index = json!({"targets":[{"za":26056,"liso":0,"convergence_flag":"partial","ledger":["warning"]}]});
        let targets = target_metadata(&index).unwrap();
        assert_eq!(
            targets[&(26_056, 0)].convergence_flag.as_deref(),
            Some("partial")
        );
        assert_eq!(targets[&(26_056, 0)].limitations, vec!["warning"]);
        let mut reasons = BTreeSet::new();
        let evidence = note_inventory_target((26_056, 0), "Fe56", &targets, true, &mut reasons);
        assert!(evidence["activation_target_present"].as_bool().unwrap());
        assert!(reasons.contains("activation_target_convergence_flag:Fe56"));
        assert!(reasons.contains("activation_target_builder_limitations:Fe56"));
        let mut zero_exposure_reasons = BTreeSet::new();
        note_inventory_target(
            (26_056, 0),
            "Fe56",
            &targets,
            false,
            &mut zero_exposure_reasons,
        );
        assert!(zero_exposure_reasons.is_empty());
        let mut missing_reasons = BTreeSet::new();
        note_inventory_target((42_094, 0), "Mo94", &targets, true, &mut missing_reasons);
        assert!(missing_reasons.contains("positive_inventory_missing_activation_target:Mo94"));
        let duplicate = json!({"targets":[{"za":26056,"liso":0},{"za":26056,"liso":0}]});
        assert!(target_metadata(&duplicate).is_err());
        let malformed_flag = json!({"targets":[{"za":26056,"liso":0,"convergence_flag":false}]});
        assert!(target_metadata(&malformed_flag).is_err());
        let malformed_za = json!({"targets":[{"za":26056.5,"liso":0}]});
        assert!(target_metadata(&malformed_za).is_err());
    }

    #[test]
    fn tolerance_is_symmetric_and_classification_has_no_tolerance_path() {
        assert!(close(1.0 + 1e-7, 1.0, 1e-6, 1e-12));
        assert!(!close(1.0 + 2e-6, 1.0, 1e-6, 1e-12));
    }

    #[test]
    fn activity_l1_disagreement_does_not_cancel_opposite_sign_errors() {
        let predicted = BTreeMap::from([("A1".to_string(), 100.0), ("B1".to_string(), 0.0)]);
        let solved = BTreeMap::from([("A1".to_string(), 0.0), ("B1".to_string(), 100.0)]);
        let (_, errors) = super::value_map_close(&predicted, &solved, 1e-6, 1e-12);
        assert!(close(l1_disagreement(&errors), 200.0, 0.0, 0.0));
        assert!(close(l1(&predicted), l1(&solved), 0.0, 0.0));
    }

    #[test]
    fn native_evaluation_comparison_rejects_predicate_metadata_drift() {
        let pack = RulePack::bundled().unwrap();
        let properties = BTreeMap::from([(
            "Co60".to_string(),
            NuclideProperties {
                z: 27,
                half_life_s: 5.2714 * 365.25 * 86_400.0,
                alpha_emitting: false,
            },
        )]);
        let activities = BTreeMap::from([("Co60".to_string(), 1.0e6)]);
        let evaluation = evaluate_component(
            &pack,
            WasteType::ActivatedMetal,
            WasteGeometry {
                mass_g: 1.0,
                displaced_volume_cm3: 1.0,
            },
            &activities,
            &properties,
        )
        .unwrap();
        assert!(evaluation_agrees(&evaluation, &evaluation).0);
        let mut changed = evaluation.clone();
        changed.constraints[0].strict = !changed.constraints[0].strict;
        assert!(!evaluation_agrees(&evaluation, &changed).0);
        let mut changed = evaluation.clone();
        changed.constraints[0].passes = !changed.constraints[0].passes;
        assert!(!evaluation_agrees(&evaluation, &changed).0);
    }

    #[test]
    fn external_tritium_is_added_once_after_activation_mass_scaling() {
        let activation = BTreeMap::from([("H3".to_string(), 2.0)]);
        let total = add_external_h3(&activation, 3.0, 5.0).unwrap();
        assert!(close(total["H3"], 11.0, 0.0, 0.0));
        assert!(add_external_h3(&activation, f64::MAX, f64::MAX).is_err());
    }

    #[test]
    fn incomplete_or_missing_audit_always_downgrades_coverage() {
        let mut missing = BTreeSet::new();
        audit_reasons(&json!({}), &mut missing);
        assert!(missing.contains("run_audit_missing"));
        let mut incomplete = BTreeSet::new();
        audit_reasons(
            &json!({"completeness":{"status":"incomplete","reaction_channel":{"defects":[{"class":"x"}]},"decay_channel":{"defects":[]},"unquantified":[]}}),
            &mut incomplete,
        );
        assert!(incomplete.contains("run_audit_incomplete"));
        assert!(incomplete.contains("audit_x"));
    }
}
