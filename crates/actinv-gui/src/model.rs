use actinv_core::spec::Spec;
use serde_json::Value;
use std::path::{Path, PathBuf};

pub const EXAMPLE: &str = include_str!("../../../examples/fns_fe_5min.json");
// Windows' small default stacks are insufficient for the solver's data readers.
pub const SOLVER_STACK_BYTES: usize = 8 * 1024 * 1024;

pub fn decode_problem(text: &str) -> Result<Value, String> {
    // Deserialize the entire schema, including unknown-field rejection, without
    // requiring an unfinished editor document to be scientifically valid yet.
    let spec = serde_json::from_str::<Spec>(text).map_err(|e| e.to_string())?;
    // Materialize the solver's defaults before passing fields to mutable widgets.
    // Indexing an absent Value field mutably would otherwise insert JSON null.
    serde_json::to_value(spec).map_err(|e| e.to_string())
}

pub fn resolve_inputs(document: &Value, base: &Path) -> Result<Spec, String> {
    let mut spec = Spec::from_json(&document.to_string())?;
    actinv_cli::workflow::resolve_inputs(&mut spec, base);
    Ok(spec)
}

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

pub fn write_json(path: &Path, value: &Value) -> Result<(), String> {
    let text = serde_json::to_string_pretty(value).map_err(|e| e.to_string())?;
    std::fs::write(path, text + "\n").map_err(|e| format!("Cannot save {}: {e}", path.display()))
}
pub fn working_directory() -> PathBuf {
    std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."))
}

pub fn solve(spec: Spec) -> Result<Value, String> {
    actinv_core::run::run(&spec, "desktop")
        .and_then(|r| serde_json::to_value(r).map_err(|e| e.to_string()))
}

fn finite_nonnegative(v: &Value) -> bool {
    v.as_f64().is_some_and(|n| n.is_finite() && n >= 0.)
}

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

#[cfg(test)]
mod tests {
    use super::*;
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
            spec.uncertainty.unwrap().covariance.path,
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
