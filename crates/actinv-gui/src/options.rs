//! Structured access to optional scientific inputs, preserving the core schema.
use eframe::egui;
use serde_json::{json, Value};

fn reference(ui: &mut egui::Ui, value: &mut Value) {
    if !value.is_object() {
        *value = json!({"path":"","sha256":""});
    }
    ui.horizontal(|ui| {
        ui.label("File");
        let mut path = value["path"].as_str().unwrap_or("").to_owned();
        if ui.text_edit_singleline(&mut path).changed() {
            value["path"] = path.into();
        }
        if ui.button("Choose and pin file").clicked() {
            if let Some(p) = rfd::FileDialog::new().pick_file() {
                match actinv_core::flux::sha256_file(&p) {
                    Ok(hash) => {
                        value["path"] = p.to_string_lossy().into_owned().into();
                        value["sha256"] = hash.into();
                    }
                    Err(e) => {
                        ui.label(e);
                    }
                }
            }
        }
    });
    let mut hash = value["sha256"].as_str().unwrap_or("").to_owned();
    ui.horizontal(|ui| {
        ui.label("SHA-256");
        if ui.text_edit_singleline(&mut hash).changed() {
            value["sha256"] = hash.into();
        }
    });
}
fn selectors(ui: &mut egui::Ui, value: &mut Value) {
    let key = ui.id().with(("response-draft", value.to_string()));
    let initial = value
        .as_array()
        .map(|a| {
            a.iter()
                .filter_map(Value::as_str)
                .collect::<Vec<_>>()
                .join(", ")
        })
        .unwrap_or_default();
    let mut text = ui
        .ctx()
        .data_mut(|d| d.get_temp::<String>(key))
        .unwrap_or(initial);
    ui.text_edit_singleline(&mut text);
    if ui.button("Apply response IDs").clicked() {
        *value = json!(text
            .split(',')
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .collect::<Vec<_>>());
    }
    ui.ctx().data_mut(|d| d.insert_temp(key, text));
}
fn number(ui: &mut egui::Ui, label: &str, value: &mut Value, default: f64) {
    let mut v = value.as_f64().unwrap_or(default);
    ui.horizontal(|ui| {
        ui.label(label);
        if ui
            .add(
                egui::DragValue::new(&mut v)
                    .custom_parser(crate::model::parse_finite)
                    .speed(0.1),
            )
            .changed()
        {
            *value = json!(v);
        }
    });
}
pub fn show(ui: &mut egui::Ui, doc: &mut Value) {
    ui.collapsing("Uncertainty (MF=33)",|ui| {
        let mut enabled=doc["uncertainty"].is_object();
        if ui.checkbox(&mut enabled,"Calculate cross-section uncertainty").changed() { doc["uncertainty"]=if enabled { json!({"covariance":{"path":"","sha256":""},"responses":[],"confidence_level":0.95,"require_complete":false}) } else { Value::Null }; }
        if enabled {
            reference(ui,&mut doc["uncertainty"]["covariance"]);
            ui.label("Responses, comma separated: heat.total, heat.gamma, activity:Mn56, activity:* (empty = all)");
            selectors(ui,&mut doc["uncertainty"]["responses"]);
            number(ui,"Confidence level (0–1)",&mut doc["uncertainty"]["confidence_level"],0.95);
            strict(ui,&mut doc["uncertainty"]);
        }
    });
    ui.collapsing("Radiological response table",|ui| {
        let mut enabled=doc["radiological"].is_object();
        if ui.checkbox(&mut enabled,"Use a supplied response table").changed() { doc["radiological"]=if enabled { json!({"table":{"path":"","sha256":""},"responses":[],"require_complete":false}) } else { Value::Null }; }
        if enabled {
            reference(ui,&mut doc["radiological"]["table"]);
            ui.label("Response IDs from your table, comma separated (empty = all). ACTINV does not select a jurisdiction or scenario.");
            selectors(ui,&mut doc["radiological"]["responses"]);
            strict(ui,&mut doc["radiological"]);
        }
    });
    ui.collapsing("Photon response and energy groups", |ui| {
        let mut enabled = doc["photon"]["response"].is_object();
        if ui
            .checkbox(&mut enabled, "Use photon response data")
            .changed()
        {
            doc["photon"]["response"] = if enabled {
                json!({"path":"","sha256":""})
            } else {
                Value::Null
            };
        }
        if enabled {
            reference(ui, &mut doc["photon"]["response"]);
        }
        number(
            ui,
            "Build-up factor",
            &mut doc["photon"]["build_up_factor"],
            2.,
        );
        number(
            ui,
            "Gamma cutoff (eV)",
            &mut doc["photon"]["gamma_constant_cutoff_eV"],
            20000.,
        );
        let mut custom = doc["photon"]["group_structure"].as_str() == Some("custom");
        if ui
            .checkbox(&mut custom, "Custom photon energy boundaries")
            .changed()
        {
            doc["photon"]["group_structure"] = if custom { "custom" } else { "fispact-24" }.into();
            if !custom {
                doc["photon"]["group_boundaries_eV"] = Value::Null;
            }
        }
        if custom {
            boundaries(
                ui,
                &mut doc["photon"]["group_boundaries_eV"],
                "photon-boundaries",
            );
        }
    });
    ui.collapsing("Fission yield files", |ui| {
        if !doc["fission_yields"].is_object() {
            doc["fission_yields"] = json!({"files":[],"energy":"spectrum_average"});
        }
        let files = doc["fission_yields"]["files"]
            .as_array_mut()
            .expect("decoded yield files");
        let mut remove = None;
        for (i, file) in files.iter_mut().enumerate() {
            ui.push_id(i, |ui| {
                reference(ui, file);
                if ui.button("Remove yield file").clicked() {
                    remove = Some(i);
                }
            });
        }
        if let Some(i) = remove {
            files.remove(i);
        }
        if ui.button("Add yield file").clicked() {
            files.push(json!({"path":"","sha256":""}));
        }
        let mut fixed = doc["fission_yields"]["energy"].as_str() == Some("fixed");
        if ui
            .checkbox(&mut fixed, "Use fixed incident energy")
            .changed()
        {
            doc["fission_yields"]["energy"] =
                if fixed { "fixed" } else { "spectrum_average" }.into();
            doc["fission_yields"]["fixed_energy_eV"] =
                if fixed { json!(0.0253) } else { Value::Null };
        }
        if fixed {
            number(
                ui,
                "Incident energy (eV)",
                &mut doc["fission_yields"]["fixed_energy_eV"],
                0.0253,
            );
        }
    });
}
fn strict(ui: &mut egui::Ui, value: &mut Value) {
    let mut enabled = value["require_complete"].as_bool().unwrap_or(false);
    if ui
        .checkbox(
            &mut enabled,
            "Require complete coverage (stop if data are missing)",
        )
        .changed()
    {
        value["require_complete"] = enabled.into();
    }
}
pub fn boundaries(ui: &mut egui::Ui, value: &mut Value, id: &str) {
    ui.label("Ascending boundaries in eV, comma or whitespace separated. Apply explicitly; invalid input keeps the existing boundaries.");
    let key = egui::Id::new((id, value.to_string()));
    let mut text = ui
        .ctx()
        .data_mut(|d| d.get_temp::<String>(key))
        .unwrap_or_else(|| {
            value
                .as_array()
                .map(|a| {
                    a.iter()
                        .map(Value::to_string)
                        .collect::<Vec<_>>()
                        .join(", ")
                })
                .unwrap_or_default()
        });
    ui.add(egui::TextEdit::multiline(&mut text).desired_rows(3));
    if ui.button("Apply boundaries").clicked() {
        let parsed: Result<Vec<f64>, _> = text
            .split(|c: char| c == ',' || c.is_whitespace())
            .filter(|s| !s.is_empty())
            .map(str::parse)
            .collect();
        match parsed {
            Ok(v)
                if v.len() > 1
                    && v.iter().all(|v| v.is_finite() && *v >= 0.)
                    && v.windows(2).all(|w| w[0] < w[1]) =>
            {
                *value = json!(v);
            }
            _ => {
                ui.colored_label(
                    ui.visuals().error_fg_color,
                    "Use at least two finite, nonnegative, strictly increasing boundaries.",
                );
            }
        }
    }
    ui.ctx().data_mut(|d| d.insert_temp(key, text));
}
