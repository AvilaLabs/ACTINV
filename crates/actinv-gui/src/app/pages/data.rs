//! Data: the nuclear data the problem uses, whether they are installed, and where to get or
//! build them. Check & run shows one line about them; this page is where they are set.
use crate::app::{heading, path_field, Desktop};
use crate::platform::Dialog;
use eframe::egui::{self, RichText};
use std::path::PathBuf;

use super::check_run::standard_data_sizes;

impl Desktop {
    pub(super) fn data_page(&mut self, ui: &mut egui::Ui) {
        heading(
            ui,
            "Data",
            "The nuclear data this problem uses: the standard data ACTINV installs, or files of your own.",
        );
        egui::Frame::group(ui.style()).inner_margin(16.).show(ui, |ui| {
            ui.set_width(ui.available_width());
            ui.heading("This problem's data");
                ui.set_width(ui.available_width());
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
        ui.add_space(12.);
        egui::Frame::group(ui.style()).inner_margin(16.).show(ui, |ui| {
            ui.set_width(ui.available_width());
            ui.heading("Check the installed data");
            let root = actinv_cli::workflow::catalog_data_root(&self.base);
            ui.label(format!(
                "Reads every installed standard file under {} and compares it with the SHA-256 the catalog records. A file that was changed or cut short is named.",
                root.display()
            ));
            if ui
                .add_enabled(self.job.is_none(), egui::Button::new("Verify the data"))
                .clicked()
            {
                let started = self.start_command(
                    crate::jobs::Command::DataVerify,
                    serde_json::Value::Null,
                    serde_json::json!({"output": root.display().to_string()}),
                    "Verifying the installed data: every file is read and hashed.",
                );
                if let Err(e) = started {
                    self.report(Err(e));
                }
            }
            if let Some(done) = self.command_result(crate::jobs::Command::DataVerify) {
                if done.exit == 0 {
                    ui.label(&done.message);
                } else {
                    ui.colored_label(
                        ui.visuals().error_fg_color,
                        format!("Verification failed: {}", done.message),
                    );
                    ui.label("Fetch the data again to replace a changed file: actinv data fetch --force.");
                }
            }
        });
        ui.add_space(12.);
        egui::Frame::group(ui.style()).inner_margin(16.).show(ui, |ui| {
            ui.set_width(ui.available_width());
            ui.heading("Build your own data");
            ui.label("Libraries are built from evaluated nuclear data files with the command line. Building changes every result made with the library, so it is done there, where each step is recorded.");
            ui.add_space(4.);
            for (command, what) in [
                ("actinv build-library", "an activation library from ENDF-6 files"),
                ("actinv build-damage", "a damage (dpa) table"),
                ("actinv build-shielding", "self-shielding factors"),
                ("actinv build-covariance", "a covariance file for uncertainty"),
            ] {
                ui.horizontal_wrapped(|ui| {
                    ui.code(command);
                    ui.label(what);
                });
            }
            ui.add_space(4.);
            ui.label(RichText::new("`actinv help build-library` and the others print each command's inputs and an example. Point the problem at what you built under Data files above.").weak());
        });
    }

    /// The one line Check & run shows about the data: installed and found, or what is missing and
    /// the way to fix it.
    pub(super) fn data_status(&mut self, ui: &mut egui::Ui) {
        let missing = self.missing_data_files();
        let frame = egui::Frame::group(ui.style())
            .inner_margin(16.)
            .show(ui, |ui| {
                ui.set_width(ui.available_width());
                ui.horizontal(|ui| {
                    let text_width = (ui.available_width() - 90.).max(160.);
                    ui.allocate_ui_with_layout(
                        egui::vec2(text_width, 0.),
                        egui::Layout::top_down(egui::Align::Min),
                        |ui| {
                            ui.set_width(text_width);
                            ui.strong("Nuclear data");
                            self.catalog_line(ui);
                            if !missing.is_empty() {
                                ui.colored_label(
                                    ui.visuals().error_fg_color,
                                    format!("Not found: {}.", missing.join(", ")),
                                );
                            } else if self.document["library"]["path"]
                                .as_str()
                                .is_some_and(|p| !p.is_empty() && !p.starts_with("catalog:"))
                            {
                                ui.label("Your own library and decay files, found.");
                            }
                        },
                    );
                    ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                        if ui.button("Edit").on_hover_text("Open Data").clicked() {
                            self.page = crate::nav::Page::Data;
                        }
                    });
                });
            });
        self.tour
            .mark("inputs", frame.response.rect.intersect(ui.clip_rect()));
    }
}
