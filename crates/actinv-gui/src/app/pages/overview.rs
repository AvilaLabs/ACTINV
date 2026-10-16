//! Overview & data: nuclear data locations, projectile and solver settings. Requested outputs
//! (photons, pathways, step summary, regulatory tables, self-shielding, uncertainty) are on Outputs.
use crate::{
    app::{heading, path_field, text_field, Desktop, Job, JobKind, JobOutput, ResultAction},
    model,
    nav::Page,
    platform::Dialog,
};
use actinv_data::catalog::{embedded_catalog, DataCatalog};
use eframe::egui;
use serde_json::{json, Value};
use std::{
    path::{Path, PathBuf},
    sync::mpsc,
    time::Instant,
};

/// Projectiles the picker offers. Triton, helion and alpha stay in the JSON; a document that
/// already carries one shows it read-only.
const PROJECTILES: [&str; 4] = ["neutron", "proton", "deuteron", "gamma"];

/// Every `catalog:<artifact-id>` string in the document, once each.
fn catalog_references(value: &Value, found: &mut Vec<String>) {
    match value {
        Value::String(text) if text.starts_with("catalog:") => {
            if !found.contains(text) {
                found.push(text.clone());
            }
        }
        Value::Array(items) => items.iter().for_each(|v| catalog_references(v, found)),
        Value::Object(map) => map
            .iter()
            .filter(|(key, _)| key.as_str() != "title")
            .for_each(|(_, v)| catalog_references(v, found)),
        _ => {}
    }
}

/// Whether the files behind a document's `catalog:` references are on disk.
#[derive(Debug, PartialEq, Eq)]
enum CatalogStatus {
    Installed(usize),
    Missing {
        absent: Vec<String>,
        unknown: Vec<String>,
    },
}

/// Look each reference up in the catalog and ask `exists` about the file it installs to. The
/// location is the one the solver's input resolution uses: `<data root>/v<version>/<artifact path>`.
fn catalog_status(
    references: &[String],
    catalog: &DataCatalog,
    data_root: &Path,
    exists: &dyn Fn(&Path) -> bool,
) -> CatalogStatus {
    let mut absent = Vec::new();
    let mut unknown = Vec::new();
    for reference in references {
        let id = reference.strip_prefix("catalog:").unwrap_or(reference);
        match catalog.artifact(id) {
            Ok(artifact) => {
                let file = data_root
                    .join(format!("v{}", catalog.catalog_version))
                    .join(&artifact.path);
                if !exists(&file) {
                    absent.push(id.to_owned());
                }
            }
            Err(_) => unknown.push(id.to_owned()),
        }
    }
    if absent.is_empty() && unknown.is_empty() {
        CatalogStatus::Installed(references.len())
    } else {
        CatalogStatus::Missing { absent, unknown }
    }
}

impl Desktop {
    /// The "catalog data: installed / missing" line; nothing when the document has no references.
    fn catalog_line(&self, ui: &mut egui::Ui) {
        let mut references = Vec::new();
        catalog_references(&self.document, &mut references);
        if references.is_empty() {
            return;
        }
        let root = actinv_cli::workflow::catalog_data_root(&self.base);
        let status = match embedded_catalog() {
            Ok(catalog) => catalog_status(&references, &catalog, &root, &|path| path.exists()),
            Err(error) => {
                ui.colored_label(ui.visuals().error_fg_color, error);
                return;
            }
        };
        match status {
            CatalogStatus::Installed(count) => {
                ui.label(format!(
                    "Catalog data: installed ({count} referenced file{} found under {}).",
                    if count == 1 { "" } else { "s" },
                    root.display()
                ));
            }
            CatalogStatus::Missing { absent, unknown } => {
                if !absent.is_empty() {
                    ui.colored_label(
                        ui.visuals().error_fg_color,
                        format!(
                            "Catalog data: missing ({}). Download the standard neutron data below, or run `actinv data fetch`; the files are looked for under {}.",
                            absent.join(", "),
                            root.display()
                        ),
                    );
                }
                if !unknown.is_empty() {
                    ui.colored_label(
                        ui.visuals().error_fg_color,
                        format!(
                            "Catalog data: {} not in the data catalog of this version.",
                            unknown.join(", ")
                        ),
                    );
                }
            }
        }
    }

    /// Fission yield files are input data (evaluated yields), so they stay with the data paths.
    fn fission_yield_files(&mut self, ui: &mut egui::Ui) {
        ui.collapsing("Fission yield files (optional)", |ui| {
            if self.document["projectile"].as_str().unwrap_or("neutron") != "neutron" {
                ui.label("Fission-yield files are supported for neutron activation only.");
            }
            let count = self.document["fission_yields"]["files"]
                .as_array()
                .map_or(0, Vec::len);
            let mut remove = None;
            for i in 0..count {
                ui.push_id(i, |ui| {
                    crate::options::reference(
                        ui,
                        &mut self.document["fission_yields"]["files"][i],
                        &*self.platform,
                    );
                    if ui.button("Remove yield file").clicked() {
                        remove = Some(i);
                    }
                });
            }
            if let Some(i) = remove {
                if let Some(files) = self.document["fission_yields"]["files"].as_array_mut() {
                    files.remove(i);
                }
            }
            if ui.button("Add yield file").clicked() {
                if !self.document["fission_yields"]["files"].is_array() {
                    self.document["fission_yields"]["files"] = json!([]);
                }
                if let Some(files) = self.document["fission_yields"]["files"].as_array_mut() {
                    files.push(json!({"path": "", "sha256": ""}));
                }
            }
            let mut fixed = self.document["fission_yields"]["energy"].as_str() == Some("fixed");
            if ui
                .checkbox(&mut fixed, "Use fixed incident energy")
                .changed()
            {
                self.document["fission_yields"]["energy"] =
                    if fixed { "fixed" } else { "spectrum_average" }.into();
                self.document["fission_yields"]["fixed_energy_eV"] =
                    if fixed { json!(0.0253) } else { Value::Null };
            }
            if fixed {
                let mut energy = self.document["fission_yields"]["fixed_energy_eV"]
                    .as_f64()
                    .unwrap_or(0.0253);
                ui.horizontal(|ui| {
                    ui.label("Incident energy (eV)");
                    if ui
                        .add(
                            egui::DragValue::new(&mut energy)
                                .custom_parser(model::parse_finite)
                                .speed(0.1),
                        )
                        .changed()
                    {
                        self.document["fission_yields"]["fixed_energy_eV"] = json!(energy);
                    }
                });
            }
        });
    }

    pub(super) fn setup(&mut self, ui: &mut egui::Ui) {
        heading(
            ui,
            "Set up a calculation",
            "Choose your evaluated data, then define the material and irradiation history.",
        );
        ui.group(|ui| {
            ui.strong("Welcome to ACTINV");
            ui.label("Explore a small teaching result offline, or use the complete iron problem below for your first real calculation.");
            if ui.button("Try offline results tutorial").clicked() {
                if self.result_unsaved { self.result_guard = Some(ResultAction::Tutorial); }
                else { self.result = Some(model::tutorial_result()); self.step = 0; self.selected.clear(); self.comparison = None; self.page = Page::Charts; self.reset_plot = true; self.tour.start(true); }
            }
            ui.label("Standard data: about 139 MiB download, 229 MiB installed. Each calculation run uses a private cache (about 282 MiB for the iron example).");
        });
        ui.label("Calculation title");
        text_field(ui, &mut self.document["title"]);
        ui.add_space(12.);
        let response=egui::Frame::group(ui.style()).inner_margin(16.).show(ui,|ui| {
            ui.heading("Nuclear data");
            ui.label("Paths are resolved against the input base folder. Existing CLI examples commonly use the repository root.");
            ui.horizontal(|ui|{ui.label("Input base");let mut base=self.base.to_string_lossy().into_owned();if ui.add(egui::TextEdit::singleline(&mut base).desired_width((ui.available_width()-220.).max(150.))).changed(){self.base=PathBuf::from(base);}
if ui.button("Choose folder").clicked(){if let Some(p)=self.platform.pick_folder(Dialog::new()){self.base=p;}}});
            path_field(ui,"Activation library (.npz)",&mut self.document["library"]["path"],&*self.platform);
            ui.collapsing("Activation library SHA-256 pin",|ui|{
                ui.label("A declared hash must match the chosen library. Update or remove the pin when intentionally switching libraries. The solver always records the computed hash.");
                let mut hash=self.document["library"]["sha256"].as_str().unwrap_or("").to_owned();
                if ui.add(egui::TextEdit::singleline(&mut hash).desired_width(500.).hint_text("Optional SHA-256")).changed(){if hash.trim().is_empty(){self.document["library"].as_object_mut().unwrap().remove("sha256");}else{self.document["library"]["sha256"]=hash.into();}}
            });
            path_field(ui,"Primary decay data",&mut self.document["decay"]["primary"],&*self.platform);
            if self.document["decay"]["fallback"].is_string(){path_field(ui,"Fallback decay data",&mut self.document["decay"]["fallback"],&*self.platform);if ui.small_button("Remove fallback").clicked(){self.document["decay"].as_object_mut().unwrap().remove("fallback");}}
            else if ui.small_button("Add fallback decay data").clicked(){self.document["decay"]["fallback"]="".into();}
            self.catalog_line(ui);
            self.fission_yield_files(ui);
            if ui.add_enabled(self.job.is_none(),egui::Button::new("Download standard neutron data · 139 MiB…")).clicked() {
                if let Some(folder)=self.platform.pick_folder(Dialog::new().title("Choose the parent folder for actinv-data")) {
                    let (tx,rx)=mpsc::channel();let ctx=ui.ctx().clone();
                    std::thread::spawn(move||{let output=actinv_cli::fetch_bundle(None,folder.join("actinv-data"),false).map(|s|JobOutput::Data(s.problem_fragment));let _=tx.send(output);ctx.request_repaint();});
                    self.job=Some(Job{rx:Some(rx),handle:None,started:Instant::now(),kind:JobKind::Data,cancelled:false});self.status="Downloading and verifying standard neutron data… Files are checked before installation; cancellation is unavailable while files are being verified.".into();self.error=false;
                }
            }
            if let Some(fragment)=&self.downloaded {
                ui.label("Verified neutron data are ready. Applying paths keeps your material and spectrum; use a compatible neutron 709-group spectrum.");
                if ui.button("Use downloaded library & decay paths").clicked(){
                    self.document["library"]=fragment["library"].clone();self.document["decay"]=fragment["decay"].clone();
                    self.document["projectile"]=fragment["projectile"].clone();
                }
            }
            ui.collapsing("Need the standard nuclear data?",|ui|{ui.label("Run this once in a terminal, then select the downloaded files above:");ui.code("actinv data fetch");ui.label("The standard download includes neutron activation and decay data. Data are versioned separately from the application.");});
        });
        self.tour
            .mark("inputs", response.response.rect.intersect(ui.clip_rect()));
        ui.add_space(16.);
        ui.horizontal(|ui| {
            ui.label("Projectile");
            let p = self.document["projectile"]
                .as_str()
                .unwrap_or("neutron")
                .to_owned();
            if PROJECTILES.contains(&p.as_str()) {
                egui::ComboBox::from_id_salt("projectile")
                    .selected_text(&p)
                    .show_ui(ui, |ui| {
                        for v in PROJECTILES {
                            if ui.selectable_label(p == v, v).clicked() {
                                self.document["projectile"] = v.into();
                            }
                        }
                    });
            } else {
                ui.label(format!("{p} (set in Advanced JSON)"));
            }
        });
        if self.document["projectile"].as_str().unwrap_or("neutron") != "neutron" {
            ui.label("Charged-particle and photonuclear problems need a library for that projectile and a temperature of 0 K.");
        }
        ui.add_space(16.);
        ui.group(|ui| {
            ui.strong("Solver");
            let mut mode=self.document["options"]["mode"].as_str().unwrap_or("auto").to_owned();
            ui.horizontal(|ui|{ui.label("Solver mode");egui::ComboBox::from_id_salt("mode").selected_text(&mode).show_ui(ui,|ui|{for m in ["auto","trace","coupled"]{if ui.selectable_value(&mut mode,m.into(),m).changed(){self.document["options"]["mode"]=mode.clone().into();}}});});
            ui.label("Trace mode supports ranked production pathways. Auto chooses an appropriate mode based on the problem.");
            let mut temperature=self.document["options"]["temperature_K"].as_f64().unwrap_or(293.6);
            ui.horizontal(|ui|{ui.label("Temperature (K)");if ui.add(egui::DragValue::new(&mut temperature).custom_parser(model::parse_finite).speed(1.).range(0.0..=f64::MAX)).changed(){self.document["options"]["temperature_K"]=json!(temperature);}});
        });
        ui.add_space(8.);
        ui.label("Photons and dose, pathways, step summary, regulatory tables, self-shielding and uncertainty are chosen on the Outputs page.");
        if ui.small_button("Open Outputs").clicked() {
            self.page = Page::Outputs;
        }
        ui.add_space(8.);
        if ui.button("Continue to material >").clicked() {
            self.page = Page::Material;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{cell::RefCell, collections::BTreeSet};

    fn references(value: &Value) -> Vec<String> {
        let mut found = Vec::new();
        catalog_references(value, &mut found);
        found
    }

    #[test]
    fn catalog_references_are_found_anywhere_once_and_not_in_the_title() {
        let document = json!({
            "title": "catalog:not-a-reference",
            "library": {"path": "catalog:tendl-2025-neutron-709g"},
            "decay": {"primary": "catalog:endfb-viii-0-decay", "fallback": "plain/file.dat"},
            "uncertainty": {"covariance": {"path": "catalog:tendl-2025-neutron-709g-covariance"}},
            "schedule": [{"library": {"path": "catalog:tendl-2025-neutron-709g"}}],
        });
        let mut found = references(&document);
        found.sort();
        assert_eq!(
            found,
            [
                "catalog:endfb-viii-0-decay",
                "catalog:tendl-2025-neutron-709g",
                "catalog:tendl-2025-neutron-709g-covariance",
            ]
        );
        assert!(references(&json!({"library": {"path": "a.npz"}})).is_empty());
    }

    #[test]
    fn catalog_status_reports_installed_missing_and_unknown() {
        let catalog = embedded_catalog().expect("embedded catalog");
        let root = Path::new("/data/actinv-data");
        let references = vec![
            "catalog:tendl-2025-neutron-709g".to_owned(),
            "catalog:endfb-viii-0-decay".to_owned(),
        ];
        let installed = |_: &Path| true;
        assert_eq!(
            catalog_status(&references, &catalog, root, &installed),
            CatalogStatus::Installed(2)
        );
        let library = catalog.artifact("tendl-2025-neutron-709g").unwrap();
        let wanted = root
            .join(format!("v{}", catalog.catalog_version))
            .join(&library.path);
        let asked = RefCell::new(BTreeSet::new());
        let without_library = |path: &Path| {
            asked.borrow_mut().insert(path.to_path_buf());
            path != wanted
        };
        let mut with_unknown = references.clone();
        with_unknown.push("catalog:no-such-artifact".to_owned());
        assert_eq!(
            catalog_status(&with_unknown, &catalog, root, &without_library),
            CatalogStatus::Missing {
                absent: vec!["tendl-2025-neutron-709g".to_owned()],
                unknown: vec!["no-such-artifact".to_owned()],
            }
        );
        assert!(asked.borrow().contains(&wanted));
    }
}
