use actinv_core::spec::Spec;
use serde_json::Value;
#[cfg(not(target_arch = "wasm32"))]
use std::path::{Path, PathBuf};

pub const EXAMPLE: &str = include_str!("../../../examples/fns_fe_5min.json");
// Windows' small default stacks are insufficient for the solver's data readers.
#[cfg(not(target_arch = "wasm32"))]
pub const SOLVER_STACK_BYTES: usize = 8 * 1024 * 1024;

pub fn decode_problem(text: &str) -> Result<Value, String> {
    // Deserialize the entire schema, including unknown-field rejection, without
    // requiring an unfinished editor document to be scientifically valid yet.
    let spec = serde_json::from_str::<Spec>(text).map_err(|e| e.to_string())?;
    // Materialize the solver's defaults before passing fields to mutable widgets.
    // Indexing an absent Value field mutably would otherwise insert JSON null.
    serde_json::to_value(spec).map_err(|e| e.to_string())
}

/// The iron example as a new problem. On the desktop its data are `catalog:` references, as
/// `actinv new` writes them, so it finds whatever catalog version is installed; the bundled file
/// names the release it was frozen with. The browser never reads data and keeps the file.
pub fn example() -> Value {
    #[cfg(not(target_arch = "wasm32"))]
    if let Ok(spec) = actinv_cli::workflow::new_example(Path::new("actinv-data")) {
        if let Ok(value) = serde_json::to_value(spec) {
            return value;
        }
    }
    decode_problem(EXAMPLE).expect("bundled problem")
}

/// A new problem: the example's data references and solver defaults, with the material, the
/// irradiation and the source left empty, so every setup step starts missing rather than
/// carrying the example's choices forward unseen.
pub fn blank() -> Value {
    let mut doc = example();
    let groups = doc["spectrum"]["flux_per_group"]
        .as_array()
        .map_or(709, Vec::len);
    doc["title"] = "".into();
    doc["material"]["composition"] = serde_json::json!({});
    doc["material"]["basis"] = "wt_percent".into();
    doc["material"]["mass_g"] = serde_json::json!(1.0);
    doc["schedule"] = serde_json::json!([]);
    if let Some(spectrum) = doc["spectrum"].as_object_mut() {
        spectrum.remove("total");
        spectrum.insert(
            "flux_per_group".into(),
            serde_json::json!(vec![0.0; groups]),
        );
    }
    if let Some(options) = doc["options"].as_object_mut() {
        options.remove("outputs");
    }
    doc
}

/// A duration for reading: the largest unit that keeps the number at least 1, three significant
/// figures. Exact seconds stay available beside it wherever precision matters.
pub fn readable_time(seconds: f64) -> String {
    const UNITS: [(f64, &str); 6] = [
        (365.25 * 86400., "y"),
        (86400., "d"),
        (3600., "h"),
        (60., "min"),
        (1., "s"),
        (1e-3, "ms"),
    ];
    if !seconds.is_finite() || seconds <= 0. {
        return "0 s".into();
    }
    let (size, unit) = UNITS
        .iter()
        .find(|(size, _)| seconds >= *size)
        .copied()
        .unwrap_or((1e-3, "ms"));
    let value = seconds / size;
    let digits = if value >= 100. {
        0
    } else if value >= 10. {
        1
    } else {
        2
    };
    let text = format!("{value:.digits$}");
    let text = if text.contains('.') {
        text.trim_end_matches('0').trim_end_matches('.').to_owned()
    } else {
        text
    };
    format!("{text} {unit}")
}

/// A quantity with three significant figures in scientific notation, e.g. `1.23e9`.
pub fn sci(value: f64) -> String {
    if value == 0. {
        "0".into()
    } else {
        format!("{value:.2e}")
    }
}

/// Total activity of a result step, Bq/g.
pub fn total_activity(step: &Value) -> f64 {
    metric(step, 0, "")
}

#[cfg(not(target_arch = "wasm32"))]
pub fn resolve_inputs(document: &Value, base: &Path) -> Result<Spec, String> {
    let mut spec = Spec::from_json(&document.to_string())?;
    actinv_cli::workflow::resolve_inputs(&mut spec, base);
    Ok(spec)
}

#[cfg(not(target_arch = "wasm32"))]
pub fn check_files(spec: &Spec) -> Result<(), String> {
    actinv_cli::workflow::check_files(spec, false)
}

/// Offline teaching data, deliberately separate from the scientific solver and inputs.
pub fn tutorial_result() -> ResultDocument {
    let lambda = std::f64::consts::LN_2 / 3600.;
    let steps: Vec<Value> = [0., 900., 1800., 3600., 7200., 14400., 28800.].into_iter().enumerate().map(|(i,t)| {
        let atoms = 1e6 * (-lambda * t).exp();
        serde_json::json!({"step":i+1,"t_s":t,"inventory":[{"nuclide":"Example","atoms_per_g":atoms}],"activity_Bq_per_g":{"Example":lambda*atoms},"heat_W_per_g":{"total":0.}})
    }).collect();
    ResultDocument::parse(serde_json::json!({"spec_title":"Teaching example: fictional one-hour half-life; no heat model","steps":steps,"ledger":{"teaching_only":"Analytic exponential decay of 1,000,000 atoms/g. Example is fictional; this is not evaluated nuclear data or a solver calculation."},"certificate":{"source":"Built-in offline tutorial; N(t)=N(0)*exp(-ln(2)*t/3600)."}}),"OFFLINE TUTORIAL · fictional nuclide".into()).expect("valid teaching result")
}

/// A pasted vector, or a one-column CSV with an optional `flux` header.
pub fn parse_group_values(text: &str, count: usize) -> Result<Vec<f64>, String> {
    let mut values = Vec::new();
    for (i, token) in text
        .split(|c: char| c == ',' || c.is_whitespace())
        .filter(|s| !s.is_empty())
        .enumerate()
    {
        if i == 0
            && matches!(
                token.to_ascii_lowercase().as_str(),
                "flux" | "flux_per_group"
            )
        {
            continue;
        }
        let v: f64 = token.parse().map_err(|_| {
            format!(
                "Value {} ('{token}') is not a number. Paste only one flux column.",
                values.len() + 1
            )
        })?;
        if !v.is_finite() || v < 0. {
            return Err(format!(
                "Group {} must be finite and nonnegative.",
                values.len() + 1
            ));
        }
        values.push(v);
    }
    if values.len() != count {
        return Err(format!("Expected {count} group values, found {}. Check the group structure and selected column.", values.len()));
    }
    if !values.iter().any(|v| *v > 0.) {
        return Err("The spectrum must contain a positive group value.".into());
    }
    Ok(values)
}

pub struct ResultDocument {
    pub value: Value,
    pub label: String,
}
impl ResultDocument {
    pub fn parse(value: Value, label: String) -> Result<Self, String> {
        let steps = value["steps"]
            .as_array()
            .filter(|s| !s.is_empty())
            .ok_or("Result needs a nonempty steps array")?;
        let mut previous = 0.0;
        for step in steps {
            let t = step["t_s"]
                .as_f64()
                .filter(|t| t.is_finite() && *t >= previous)
                .ok_or("Result times must be finite and ordered")?;
            previous = t;
            if !step["inventory"].is_array()
                || !step["activity_Bq_per_g"].is_object()
                || step["heat_W_per_g"]["total"].as_f64().is_none()
            {
                return Err("Result step is missing inventory, activity, or heat".into());
            }
            let inventory = step["inventory"].as_array().unwrap();
            let mut names = std::collections::BTreeSet::new();
            for row in inventory {
                let name = row["nuclide"]
                    .as_str()
                    .filter(|s| !s.is_empty())
                    .ok_or("Inventory row needs a nuclide name")?;
                if !names.insert(name) || !finite_nonnegative(&row["atoms_per_g"]) {
                    return Err(
                        "Inventory needs unique nuclides and nonnegative finite atoms_per_g".into(),
                    );
                }
            }
            if !finite_nonnegative(&step["heat_W_per_g"]["total"])
                || step["activity_Bq_per_g"]
                    .as_object()
                    .unwrap()
                    .iter()
                    .any(|(name, v)| name.is_empty() || !finite_nonnegative(v))
            {
                return Err("Heat and activity must be nonnegative finite numbers".into());
            }
        }
        Ok(Self { value, label })
    }
    pub fn steps(&self) -> &[Value] {
        self.value["steps"]
            .as_array()
            .map(Vec::as_slice)
            .unwrap_or(&[])
    }
}

/// `DragValue` text parser that refuses non-finite input. egui's default accepts
/// "nan"/"inf" (`f64::from_str` does), clamps them to the range end (1.8e308 for
/// `0.0..=f64::MAX`) and serde_json stores an unranged non-finite value as `null`.
pub fn parse_finite(text: &str) -> Option<f64> {
    let cleaned: String = text
        .chars()
        .filter(|character| !character.is_whitespace())
        .map(|character| {
            if character == '\u{2212}' {
                '-'
            } else {
                character
            }
        })
        .collect();
    cleaned
        .parse::<f64>()
        .ok()
        .filter(|value| value.is_finite())
}

pub fn number(v: &Value) -> f64 {
    v.as_f64().unwrap_or(0.0)
}
pub fn metric(step: &Value, metric: usize, selected: &str) -> f64 {
    match metric {
        0 if selected.is_empty() => step["activity_Bq_per_g"]
            .as_object()
            .map(|a| a.values().map(number).sum())
            .unwrap_or(0.0),
        0 => number(&step["activity_Bq_per_g"][selected]),
        1 => number(&step["heat_W_per_g"]["total"]),
        _ => step["inventory"]
            .as_array()
            .map(|rows| {
                rows.iter()
                    .filter(|r| selected.is_empty() || r["nuclide"].as_str() == Some(selected))
                    .map(|r| number(&r["atoms_per_g"]))
                    .sum()
            })
            .unwrap_or(0.0),
    }
}

#[cfg(not(target_arch = "wasm32"))]
pub fn write_json(path: &Path, value: &Value) -> Result<(), String> {
    let text = serde_json::to_string_pretty(value).map_err(|e| e.to_string())?;
    std::fs::write(path, text + "\n").map_err(|e| format!("Cannot save {}: {e}", path.display()))
}
#[cfg(not(target_arch = "wasm32"))]
pub fn working_directory() -> PathBuf {
    std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."))
}

#[cfg(not(target_arch = "wasm32"))]
pub fn solve(spec: Spec) -> Result<Value, String> {
    actinv_core::run::run(&spec, "desktop")
        .and_then(|r| serde_json::to_value(r).map_err(|e| e.to_string()))
}

fn finite_nonnegative(v: &Value) -> bool {
    v.as_f64().is_some_and(|n| n.is_finite() && n >= 0.)
}

/// The selected step's inventory as CSV; the desktop smoke test checks a result through it.
#[cfg(not(target_arch = "wasm32"))]
pub fn inventory_csv(result: &ResultDocument, index: usize) -> Result<String, String> {
    let step = result
        .steps()
        .get(index)
        .ok_or("Selected result step does not exist")?;
    let mut csv = String::from("step,time_s,nuclide,atoms_per_g,activity_Bq_per_g\n");
    for row in step["inventory"]
        .as_array()
        .ok_or("Result inventory is missing")?
    {
        let name = row["nuclide"]
            .as_str()
            .ok_or("Result nuclide name is missing")?;
        csv.push_str(&format!(
            "{},{},\"{}\",{},{}\n",
            index + 1,
            number(&step["t_s"]),
            name.replace('"', "\"\""),
            number(&row["atoms_per_g"]),
            number(&step["activity_Bq_per_g"][name])
        ));
    }
    Ok(csv)
}

/// What drives a schedule step's irradiation. A valid step carries exactly one of these keys (a
/// power step echoed by the solver also carries `flux: 0`, which `drive_of` ignores).
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Drive {
    Flux,
    WallLoading,
    PowerPerCm3,
    PowerPerGram,
}

impl Drive {
    pub const ALL: [Drive; 4] = [
        Drive::Flux,
        Drive::WallLoading,
        Drive::PowerPerCm3,
        Drive::PowerPerGram,
    ];
    /// The schedule-step field this drive writes.
    pub fn key(self) -> &'static str {
        match self {
            Drive::Flux => "flux",
            Drive::WallLoading => "wall_loading_MW_m2",
            Drive::PowerPerCm3 => "power_W_cm3",
            Drive::PowerPerGram => "power_W_per_g",
        }
    }
    pub fn label(self) -> &'static str {
        match self {
            Drive::Flux => "Flux multiplier",
            Drive::WallLoading => "Wall loading",
            Drive::PowerPerCm3 => "Power W/cm3",
            Drive::PowerPerGram => "Power W/g",
        }
    }
    /// Unit shown beside the value; empty for the dimensionless flux multiplier.
    pub fn unit(self) -> &'static str {
        match self {
            Drive::Flux => "",
            Drive::WallLoading => "MW/m2",
            Drive::PowerPerCm3 => "W/cm3",
            Drive::PowerPerGram => "W/g",
        }
    }
    pub fn is_power(self) -> bool {
        matches!(self, Drive::PowerPerCm3 | Drive::PowerPerGram)
    }
}

fn declares(step: &Value, key: &str) -> bool {
    step.get(key).is_some_and(|v| !v.is_null())
}

/// The drive a schedule step declares. A power or wall key wins over `flux`, so the `flux: 0` that
/// the solver echoes beside a power target does not hide it.
pub fn drive_of(step: &Value) -> Drive {
    if declares(step, "power_W_cm3") {
        Drive::PowerPerCm3
    } else if declares(step, "power_W_per_g") {
        Drive::PowerPerGram
    } else if declares(step, "wall_loading_MW_m2") {
        Drive::WallLoading
    } else {
        Drive::Flux
    }
}

/// The number the step's drive carries (0 when it is missing).
pub fn drive_value(step: &Value) -> f64 {
    number(&step[drive_of(step).key()])
}

/// Make `drive` the step's only driver, set to `value`. Only the four driver keys change: the
/// step object is edited in place, never rebuilt, so `dt`, `partition`, `feed`, `removal`,
/// `spectrum`, `library` and `projectile` survive.
pub fn set_drive(step: &mut Value, drive: Drive, value: f64) {
    if !step.is_object() {
        *step = Value::Object(Default::default());
    }
    let map = step.as_object_mut().expect("step is an object");
    for other in Drive::ALL {
        if other != drive {
            map.remove(other.key());
        }
    }
    let value = if value.is_finite() { value } else { 0. };
    map.insert(drive.key().into(), Value::from(value));
}

/// A step is cooling when it irradiates nothing: a flux multiplier or a wall loading of zero. A
/// power target is validated positive, so a power step always irradiates.
pub fn is_cooling(step: &Value) -> bool {
    match drive_of(step) {
        Drive::Flux | Drive::WallLoading => drive_value(step) == 0.,
        Drive::PowerPerCm3 | Drive::PowerPerGram => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_new_problem_starts_with_material_irradiation_and_source_missing() {
        let doc = blank();
        let lines = crate::views::check::assess(&doc);
        assert!(
            !lines[0].ready && !lines[1].ready && !lines[2].ready,
            "{lines:?}"
        );
        assert!(lines[3].ready);
        assert_eq!(doc["library"], example()["library"]);
        assert_eq!(doc["title"], "");
        // the solver refuses it until the three steps are filled in
        assert!(actinv_core::spec::Spec::from_json(&doc.to_string()).is_err());
    }

    #[test]
    fn readable_times_pick_the_unit_a_person_would() {
        for (seconds, text) in [
            (0., "0 s"),
            (0.25, "250 ms"),
            (66., "1.1 min"),
            (300., "5 min"),
            (3600., "1 h"),
            (5400., "1.5 h"),
            (86400. * 3., "3 d"),
            (365.25 * 86400. * 10., "10 y"),
            (365.25 * 86400. * 1234., "1234 y"),
        ] {
            assert_eq!(readable_time(seconds), text, "{seconds}");
        }
    }

    #[test]
    fn the_example_loads_as_a_problem() {
        let example = example();
        assert!(decode_problem(&example.to_string()).is_ok());
        assert_eq!(example["material"]["composition"]["FE"], 100.0);
        #[cfg(not(target_arch = "wasm32"))]
        assert_eq!(
            example["library"]["path"],
            "catalog:tendl-2025-neutron-709g"
        );
    }

    #[test]
    fn numeric_editors_refuse_non_finite_text() {
        for text in ["nan", "NaN", "inf", "-inf", "infinity", ""] {
            assert_eq!(super::parse_finite(text), None, "{text}");
        }
        assert_eq!(super::parse_finite(" 1 000.5 "), Some(1000.5));
        assert_eq!(super::parse_finite("\u{2212}2e3"), Some(-2000.0));
    }
    #[test]
    fn pasted_spectra_reject_bad_counts_negative_and_multiple_columns() {
        assert_eq!(
            parse_group_values("flux\n1e3\n2e3", 2).unwrap(),
            vec![1000., 2000.]
        );
        assert!(parse_group_values("1,2\n3,4", 2).is_err());
        assert!(parse_group_values("1,-2", 2).is_err());
        assert!(parse_group_values("NaN,2", 2).is_err());
        assert!(parse_group_values("0,0", 2).is_err());
    }
    #[test]
    fn tutorial_halves_inventory_in_one_hour_and_is_explicitly_labelled() {
        let result = tutorial_result();
        assert!((metric(&result.steps()[3], 2, "") - 500_000.).abs() < 1e-8);
        assert!(result.label.contains("TUTORIAL"));
        assert!(result.value["ledger"]["teaching_only"]
            .as_str()
            .unwrap()
            .contains("fictional"));
    }
    #[test]
    fn editor_preserves_optional_fields_and_rejects_unknowns() {
        let mut doc = decode_problem(EXAMPLE).unwrap();
        doc["photon"] =
            serde_json::json!({"response":{"path":"response.json","sha256":"a".repeat(64)}});
        doc["title"] = "Edited".into();
        assert_eq!(
            decode_problem(&doc.to_string()).unwrap()["photon"]["response"],
            doc["photon"]["response"]
        );
        doc["typo"] = true.into();
        assert!(decode_problem(&doc.to_string()).is_err());
    }
    #[test]
    fn omitted_optional_fields_use_solver_defaults_in_the_editor() {
        let mut document: Value = serde_json::from_str(EXAMPLE).unwrap();
        document.as_object_mut().unwrap().remove("title");
        document.as_object_mut().unwrap().remove("options");
        document["material"]
            .as_object_mut()
            .unwrap()
            .remove("mass_g");
        document["material"]
            .as_object_mut()
            .unwrap()
            .remove("basis");
        let loaded = decode_problem(&document.to_string()).unwrap();
        assert_eq!(loaded["title"], "");
        assert_eq!(loaded["material"]["mass_g"], 1.);
        assert_eq!(loaded["material"]["basis"], "wt_percent");
        assert_eq!(loaded["options"]["temperature_K"], 293.6);
        assert!(Spec::from_json(&loaded.to_string()).is_ok());
    }
    #[test]
    fn paths_resolve_without_mutating_saved_document() {
        let doc = decode_problem(EXAMPLE).unwrap();
        let spec = resolve_inputs(&doc, Path::new("/project")).unwrap();
        assert!(Path::new(&spec.library.path).starts_with(Path::new("/project")));
        assert!(doc["library"]["path"]
            .as_str()
            .unwrap()
            .starts_with("actinv-data/"));
    }
    #[test]
    fn malformed_inventory_is_not_silently_plotted_as_zero() {
        let value = serde_json::json!({"steps":[{"t_s":1.,"inventory":[{"nuclide":"Fe56","atoms_per_g":"bad"}],"activity_Bq_per_g":{},"heat_W_per_g":{"total":0.}}]});
        assert!(ResultDocument::parse(value, "bad".into()).is_err());
    }
    #[test]
    fn all_optional_file_references_resolve_and_hashes_survive() {
        let mut doc = decode_problem(EXAMPLE).unwrap();
        let reference = serde_json::json!({"path":"extra.json","sha256":"a".repeat(64)});
        doc["photon"] = serde_json::json!({"response":reference});
        doc["uncertainty"] = serde_json::json!({"covariance":reference});
        doc["radiological"] = serde_json::json!({"table":reference,"responses":["clearance"]});
        doc["fission_yields"] = serde_json::json!({"files":[reference]});
        let original = doc.clone();
        let spec = resolve_inputs(&doc, Path::new("/project")).unwrap();
        assert_eq!(
            spec.photon.response.unwrap().path,
            Path::new("/project").join("extra.json").to_string_lossy()
        );
        assert_eq!(
            spec.uncertainty.unwrap().covariance.unwrap().path,
            Path::new("/project").join("extra.json").to_string_lossy()
        );
        assert_eq!(
            spec.radiological.unwrap().table.path,
            Path::new("/project").join("extra.json").to_string_lossy()
        );
        assert_eq!(
            spec.fission_yields.files[0].path,
            Path::new("/project").join("extra.json").to_string_lossy()
        );
        assert_eq!(spec.fission_yields.files[0].sha256, "a".repeat(64));
        assert_eq!(doc, original);
    }
    #[test]
    fn metrics_link_selection_and_reject_invalid_results() {
        let step = serde_json::json!({"t_s":1.,"inventory":[{"nuclide":"Fe55","atoms_per_g":3.}],"activity_Bq_per_g":{"Fe55":2.,"Co60":4.},"heat_W_per_g":{"total":5.}});
        assert_eq!(metric(&step, 0, ""), 6.);
        assert_eq!(metric(&step, 0, "Fe55"), 2.);
        assert_eq!(metric(&step, 2, "Fe55"), 3.);
        assert!(ResultDocument::parse(serde_json::json!({"steps":[step]}), "test".into()).is_ok());
        assert!(ResultDocument::parse(serde_json::json!({"steps":[]}), "test".into()).is_err());
    }
}

#[cfg(test)]
mod integration_control {
    use super::*;
    #[test]
    #[ignore = "requires the generated P11 fixture; run controls/check_desktop.py"]
    fn desktop_worker_preserves_solver_output() {
        let path =
            std::env::var("ACTINV_DESKTOP_CONTROL_SPEC").expect("control specification path");
        let output = std::env::var("ACTINV_DESKTOP_CONTROL_RESULT").expect("control output path");
        let document = decode_problem(&std::fs::read_to_string(&path).unwrap()).unwrap();
        let spec = resolve_inputs(&document, Path::new(&path).parent().unwrap()).unwrap();
        check_files(&spec).unwrap();
        let result = solve(spec).unwrap();
        let result = ResultDocument::parse(result, "control".into()).unwrap();
        write_json(Path::new(&output), &result.value).unwrap();
        std::fs::write(
            Path::new(&output).with_extension("csv"),
            inventory_csv(&result, 0).unwrap(),
        )
        .unwrap();
        let reloaded: Value =
            serde_json::from_str(&std::fs::read_to_string(output).unwrap()).unwrap();
        assert_eq!(result.value, reloaded);
    }
}

#[cfg(test)]
mod drive_tests {
    use super::*;
    use serde_json::json;

    fn rich_step() -> Value {
        json!({
            "dt": "1 h",
            "flux": 1.5,
            "partition": {"Fe": 0.5},
            "feed": {"Co60": 1e3},
            "removal": {"Mn": 1e-4},
            "spectrum": {"structure": "fispact-709", "flux_per_group": [1.0, 2.0]},
            "library": {"path": "other.npz", "sha256": "a".repeat(64)},
            "projectile": "neutron"
        })
    }

    fn drivers(step: &Value) -> Vec<&'static str> {
        Drive::ALL
            .iter()
            .map(|d| d.key())
            .filter(|k| step.get(*k).is_some())
            .collect()
    }

    #[test]
    fn drive_of_reads_the_declared_key_and_ignores_the_echoed_zero_flux() {
        assert_eq!(drive_of(&json!({"dt": "1 s", "flux": 2.})), Drive::Flux);
        assert_eq!(
            drive_of(&json!({"dt": "1 s", "wall_loading_MW_m2": 1.})),
            Drive::WallLoading
        );
        assert_eq!(
            drive_of(&json!({"dt": "1 s", "flux": 0., "power_W_cm3": 7.})),
            Drive::PowerPerCm3
        );
        assert_eq!(
            drive_of(&json!({"dt": "1 s", "flux": 0., "power_W_per_g": 7.})),
            Drive::PowerPerGram
        );
        assert_eq!(
            drive_of(&json!({"dt": "1 s", "flux": 0., "power_W_per_g": null})),
            Drive::Flux
        );
        assert_eq!(drive_value(&json!({"flux": 0., "power_W_cm3": 7.})), 7.);
    }

    #[test]
    fn every_drive_transition_keeps_one_driver_and_every_other_field() {
        for from in Drive::ALL {
            for to in Drive::ALL {
                let mut step = rich_step();
                set_drive(&mut step, from, 2.);
                let before = step.clone();
                set_drive(&mut step, to, 3.);
                assert_eq!(drivers(&step), vec![to.key()], "{from:?} -> {to:?}");
                assert_eq!(drive_of(&step), to);
                assert_eq!(drive_value(&step), 3.);
                for key in [
                    "dt",
                    "partition",
                    "feed",
                    "removal",
                    "spectrum",
                    "library",
                    "projectile",
                ] {
                    assert_eq!(step[key], before[key], "{key} survives {from:?} -> {to:?}");
                }
            }
        }
    }

    #[test]
    fn set_drive_edits_in_place_and_never_stores_a_non_finite_number() {
        let mut step = rich_step();
        let keys_before: Vec<_> = step.as_object().unwrap().keys().cloned().collect();
        set_drive(&mut step, Drive::Flux, 4.);
        assert_eq!(
            step.as_object()
                .unwrap()
                .keys()
                .cloned()
                .collect::<Vec<_>>(),
            keys_before
        );
        set_drive(&mut step, Drive::WallLoading, f64::NAN);
        assert_eq!(step["wall_loading_MW_m2"], json!(0.));
    }

    #[test]
    fn cooling_follows_the_drive_not_only_the_flux_key() {
        assert!(is_cooling(&json!({"dt": "1 s", "flux": 0.})));
        assert!(!is_cooling(&json!({"dt": "1 s", "flux": 0.1})));
        assert!(is_cooling(&json!({"dt": "1 s", "wall_loading_MW_m2": 0.})));
        assert!(!is_cooling(
            &json!({"dt": "1 s", "wall_loading_MW_m2": 1.5})
        ));
        assert!(!is_cooling(
            &json!({"dt": "1 s", "flux": 0., "power_W_cm3": 7.})
        ));
        assert!(!is_cooling(&json!({"dt": "1 s", "power_W_per_g": 1.})));
    }

    #[test]
    fn exactly_one_driver_survives_the_core_parser_for_every_drive() {
        for drive in Drive::ALL {
            let mut doc = decode_problem(EXAMPLE).unwrap();
            doc["kerma"] = json!({"table": {"path": "kerma.json", "sha256": "0".repeat(64)}});
            doc["material"]["density_g_cm3"] = json!(7.874);
            doc["schedule"][0]["partition"] = json!({"Fe": 0.5});
            // start from a different drive each time so the old driver must be removed
            let start = if drive == Drive::Flux {
                Drive::WallLoading
            } else {
                Drive::Flux
            };
            set_drive(&mut doc["schedule"][0], start, 1.);
            set_drive(&mut doc["schedule"][0], drive, 1.);
            assert_eq!(drivers(&doc["schedule"][0]), vec![drive.key()]);
            let spec = Spec::from_json(&doc.to_string())
                .unwrap_or_else(|e| panic!("{drive:?} step is refused: {e}"));
            assert!(
                spec.schedule[0].has_partition(),
                "{drive:?} keeps the partition"
            );
            assert_eq!(spec.schedule[0].is_power_driven(), drive.is_power());
            // the solver's own echo reads back to the same driver
            let echoed = serde_json::to_value(&spec).unwrap();
            assert_eq!(drive_of(&echoed["schedule"][0]), drive);
        }
    }
}
