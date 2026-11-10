//! Maintenance: the shared view plans in process; this page reads the files it adds and saves its
//! answer.
use crate::{
    app::{heading, Desktop},
    platform::Dialog,
    views::maintenance::{self, MaintenanceAction},
};
use eframe::egui;

impl Desktop {
    pub(super) fn maintenance_page(&mut self, ui: &mut egui::Ui) {
        heading(ui, "Maintenance", maintenance::QUESTION);
        if self.capture.is_some() && self.maintenance.places.is_empty() {
            if let Some(path) = std::env::var_os("ACTINV_GUI_CAPTURE_MAINTENANCE") {
                let opened = self.maintenance.load_plan(std::path::Path::new(&path));
                self.report(opened);
            }
        }
        match maintenance::show(ui, &mut self.maintenance, self.result.as_ref()) {
            MaintenanceAction::None => {}
            MaintenanceAction::OpenResult => self.open_result(ui.ctx(), false),
            MaintenanceAction::GoTo(target) => self.page = super::page_for(target),
            MaintenanceAction::AddFile => {
                if let Some(path) = self
                    .platform
                    .pick_file(Dialog::new().filter("Run result or dose-rate table", &["json"]))
                {
                    let added = std::fs::read(&path)
                        .map_err(|e| format!("cannot read {}: {e}", path.display()))
                        .and_then(|bytes| {
                            self.maintenance
                                .add_file(&path.display().to_string(), &bytes)
                        });
                    self.report(added);
                }
            }
            MaintenanceAction::SaveResult => {
                let Some(result) = maintenance::result_document(&self.maintenance) else {
                    return;
                };
                let Some(path) = self.platform.save_file(
                    Dialog::new()
                        .filter("Maintenance plan result", &["json"])
                        .file_name("maintenance-result.json"),
                ) else {
                    return;
                };
                let written = serde_json::to_string_pretty(&result)
                    .map_err(|e| e.to_string())
                    .and_then(|text| {
                        std::fs::write(&path, format!("{text}\n"))
                            .map_err(|e| format!("cannot write {}: {e}", path.display()))
                    })
                    .map(|()| format!("Saved the plan's answer to {}.", path.display()));
                self.report(written);
            }
        }
    }
}
