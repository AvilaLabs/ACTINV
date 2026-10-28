//! Check & run, the last setup step: the problem read back as sentences with a way to fix each
//! part, the title, the nuclear data and solver settings, and the Run button.
use crate::{
    app::{heading, path_field, Desktop, Job, JobKind, JobOutput, BLUE},
    model,
    nav::Page,
    platform::Dialog,
};
use actinv_data::catalog::{embedded_catalog, ArtifactRole, DataCatalog};
use eframe::egui::{self, Color32, RichText};
use serde_json::{json, Value};
use std::{
    path::{Path, PathBuf},
    sync::mpsc,
    time::Instant,
};

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

    /// Download the standard bundle into `<chosen folder>/actinv-data` in the background.
    pub(super) fn download_standard_data(&mut self, ctx: &egui::Context) {
        let Some(folder) = self
            .platform
            .pick_folder(Dialog::new().title("Choose the parent folder for actinv-data"))
        else {
            return;
        };
        let (tx, rx) = mpsc::channel();
        let ctx = ctx.clone();
        std::thread::spawn(move || {
            let output = actinv_cli::fetch_bundle(None, folder.join("actinv-data"), false)
                .map(|s| JobOutput::Data(s.problem_fragment));
            let _ = tx.send(output);
            ctx.request_repaint();
        });
        self.job = Some(Job {
            rx: Some(rx),
            handle: None,
            started: Instant::now(),
            kind: JobKind::Data,
            cancelled: false,
        });
        self.status = "Downloading and verifying standard neutron data… Files are checked before installation; cancellation is unavailable while files are being verified.".into();
        self.error = false;
    }

    /// Whether the standard bundle is installed where `catalog:` references look for it.
    pub(super) fn standard_data_installed(&self) -> Result<PathBuf, String> {
        let catalog = embedded_catalog()?;
        let bundle = catalog.bundle(None)?;
        let references: Vec<String> = bundle
            .artifacts
            .iter()
            .map(|id| format!("catalog:{id}"))
            .collect();
        let root = actinv_cli::workflow::catalog_data_root(&self.base);
        match catalog_status(&references, &catalog, &root, &|path| path.exists()) {
            CatalogStatus::Installed(_) => Ok(root.join(format!("v{}", catalog.catalog_version))),
            CatalogStatus::Missing { .. } => Err(root.display().to_string()),
        }
    }

    /// Library and decay files the problem names that are not on disk, as resolved for the run.
    fn missing_data_files(&self) -> Vec<String> {
        let Ok(spec) = model::resolve_inputs(&self.document, &self.base) else {
            return Vec::new();
        };
        [
            Some(&spec.library.path),
            Some(&spec.decay.primary),
            spec.decay.fallback.as_ref(),
        ]
        .into_iter()
        .flatten()
        .filter(|path| {
            !path.is_empty() && !path.starts_with("catalog:") && !Path::new(path).exists()
        })
        .cloned()
        .collect()
    }

    /// Point the problem's library and decay data at the installed standard bundle, by catalog
    /// name, so it follows whatever catalog version is installed.
    fn use_standard_data(&mut self) -> Result<(), String> {
        let catalog = embedded_catalog()?;
        let bundle = catalog.bundle(None)?;
        let id = |role: ArtifactRole| -> Result<String, String> {
            bundle
                .artifacts
                .iter()
                .filter_map(|id| catalog.artifact(id).ok())
                .find(|artifact| artifact.role == role)
                .map(|artifact| format!("catalog:{}", artifact.id))
                .ok_or_else(|| format!("the standard bundle has no {}", role.name()))
        };
        self.document["library"] = json!({"path": id(ArtifactRole::ActivationLibrary)?});
        self.document["decay"] = json!({
            "primary": id(ArtifactRole::DecayPrimary)?,
            "fallback": id(ArtifactRole::DecayFallback)?,
        });
        self.document["projectile"] = json!(bundle.projectile);
        Ok(())
    }

    pub(super) fn check_run(&mut self, ui: &mut egui::Ui) {
        heading(
            ui,
            "Check & run",
            "Read the problem back, fix anything marked, then run it.",
        );
        ui.label("Calculation title");
        crate::visuals::wide_text_field(ui, &mut self.document["title"]);
        ui.add_space(12.);
        let checks = egui::Frame::group(ui.style())
            .inner_margin(16.)
            .show(ui, |ui| {
                ui.heading("The problem");
                crate::views::check::show(ui, &self.document)
            });
        self.tour
            .mark("check", checks.response.rect.intersect(ui.clip_rect()));
        if let crate::views::check::CheckAction::Open(target) = checks.inner {
            self.page = super::page_for(target);
        }
        ui.add_space(12.);
        let response=egui::Frame::group(ui.style()).inner_margin(16.).show(ui,|ui| {
            ui.set_width(ui.available_width());
            ui.heading("Nuclear data");
            self.catalog_line(ui);
            let missing = self.missing_data_files();
            let installed = self.standard_data_installed();
            if !missing.is_empty() {
                ui.colored_label(
                    ui.visuals().error_fg_color,
                    format!("Not found: {}.", missing.join(", ")),
                );
                if let Ok(dir) = &installed {
                    ui.label(format!("The standard data are installed in {}.", dir.display()));
                    if ui.button("Use the installed standard data").clicked() {
                        let used = self.use_standard_data().map(|()| "The problem now uses the installed standard data.".into());
                        self.report(used);
                    }
                }
            } else if self.document["library"]["path"].as_str().is_some_and(|p| !p.starts_with("catalog:")) {
                ui.label("Library and decay files found.");
            }
            if let Some(fragment)=&self.downloaded {
                ui.label("Verified neutron data are ready. Applying paths keeps your material and spectrum; use a compatible neutron 709-group spectrum.");
                if ui.button("Use downloaded library & decay paths").clicked(){
                    self.document["library"]=fragment["library"].clone();self.document["decay"]=fragment["decay"].clone();
                    self.document["projectile"]=fragment["projectile"].clone();
                }
            }
            ui.collapsing("Data files and input folder",|ui|{
                ui.label("Relative paths are resolved against the input folder. `catalog:` names point into the installed standard data.");
                ui.horizontal(|ui|{ui.label("Input folder");let mut base=self.base.to_string_lossy().into_owned();if ui.add(egui::TextEdit::singleline(&mut base).desired_width((ui.available_width()-220.).max(150.))).changed(){self.base=PathBuf::from(base);}
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
                self.fission_yield_files(ui);
            });
            if installed.is_err() {
                ui.horizontal_wrapped(|ui| {
                    if ui.add_enabled(self.job.is_none(),egui::Button::new(format!("Download standard neutron data · {}…", standard_data_sizes().0))).clicked() {
                        self.download_standard_data(ui.ctx());
                    }
                    ui.label("or run");
                    ui.code("actinv data fetch");
                    ui.label("in a terminal.");
                });
            }
        });
        self.tour
            .mark("inputs", response.response.rect.intersect(ui.clip_rect()));
        ui.add_space(12.);
        egui::CollapsingHeader::new("Solver settings").show(ui, |ui| {
            let mut mode=self.document["options"]["mode"].as_str().unwrap_or("auto").to_owned();
            ui.horizontal(|ui|{ui.label("Solver mode");egui::ComboBox::from_id_salt("mode").selected_text(&mode).show_ui(ui,|ui|{for m in ["auto","trace","coupled"]{if ui.selectable_value(&mut mode,m.into(),m).changed(){self.document["options"]["mode"]=mode.clone().into();}}});});
            ui.label("Trace mode supports ranked production pathways. Auto chooses an appropriate mode based on the problem.");
            let mut temperature=self.document["options"]["temperature_K"].as_f64().unwrap_or(293.6);
            ui.horizontal(|ui|{ui.label("Temperature (K)");if ui.add(egui::DragValue::new(&mut temperature).custom_parser(model::parse_finite).speed(1.).range(0.0..=f64::MAX)).changed(){self.document["options"]["temperature_K"]=json!(temperature);}});
        });
        ui.add_space(16.);
        ui.horizontal_wrapped(|ui| {
            let run = ui.add_enabled(
                self.job.is_none(),
                egui::Button::new(RichText::new("▶ Run calculation").color(Color32::WHITE)).fill(BLUE),
            );
            self.tour.reveal("run", &run);
            if run.clicked() {
                self.run(ui.ctx());
            }
            if ui.button("Validate only").clicked() {
                let r = self.validate().map(|_| "Specification and input locations are valid. File hashes and evaluated data are checked during the solve.".into());
                self.report(r);
            }
            if ui.button("< Back").clicked() {
                self.page = Page::Outputs;
            }
        });
        ui.label(RichText::new("Run solves in the background and opens the result on its Summary. A first run with new data also prepares a cache, so it takes longer.").weak());
    }
}

/// Download and installed sizes of the catalog's standard bundle, in whole MiB, so the text
/// follows the embedded catalog instead of a release that has passed.
pub(super) fn standard_data_sizes() -> (String, String) {
    let mib = |bytes: u64| format!("{:.0} MiB", bytes as f64 / 1_048_576.0);
    let Ok(catalog) = embedded_catalog() else {
        return ("?".into(), "?".into());
    };
    let Ok(bundle) = catalog.bundle(None) else {
        return ("?".into(), "?".into());
    };
    let installed = bundle
        .artifacts
        .iter()
        .filter_map(|id| catalog.artifact(id).ok())
        .map(|artifact| artifact.bytes)
        .sum();
    (
        catalog
            .source_download_bytes(bundle)
            .map_or("?".into(), mib),
        mib(installed),
    )
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
