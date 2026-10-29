//! Compare: the shared view sets up the grid and ranks the cases; this page runs `actinv study run`
//! in the isolated worker and loads the record it writes.
use crate::{
    app::{heading, Desktop, JobKind},
    jobs::Command,
    views::compare::{self, CompareAction, Found},
};
use eframe::egui;
use serde_json::Value;
use std::path::Path;

/// A finished comparison: its record and, beside it, the study it ran.
fn load(record: &Path) -> Result<Found, String> {
    let read = |path: &Path| -> Result<Value, String> {
        let text = std::fs::read_to_string(path).map_err(|e| format!("{}: {e}", path.display()))?;
        serde_json::from_str(&text).map_err(|e| format!("{}: {e}", path.display()))
    };
    Ok(Found {
        path: record.to_owned(),
        study: read(&record.with_file_name("input.json"))?,
        record: read(record)?,
    })
}

impl Desktop {
    pub(super) fn compare_page(&mut self, ui: &mut egui::Ui) {
        heading(ui, "Compare", compare::QUESTION);
        if self.capture.is_some() && self.compare.found.is_none() {
            if let Some(path) = std::env::var_os("ACTINV_GUI_CAPTURE_COMPARE") {
                if let Ok(found) = load(Path::new(&path)) {
                    self.compare.show_study(&found.study);
                    self.compare.found = Some(found);
                }
            }
        }
        // load the record the last comparison wrote, once
        if let Some(done) = self.command_result(Command::StudyRun) {
            if let Some(path) = done.primary.clone().filter(|path| {
                done.exit == 0
                    && self
                        .compare
                        .found
                        .as_ref()
                        .is_none_or(|found| &found.path != path)
            }) {
                match load(&path) {
                    Ok(found) => self.compare.found = Some(found),
                    Err(error) => {
                        // remember the path so a broken record is reported once
                        self.compare.found = Some(Found {
                            path,
                            record: Value::Null,
                            study: Value::Null,
                        });
                        self.report(Err(format!("Cannot read the comparison: {error}")));
                    }
                }
            }
        }
        let busy = self.job.is_some();
        let running = self
            .job
            .as_ref()
            .is_some_and(|job| job.kind == JobKind::Command(Command::StudyRun));
        match compare::show(ui, &self.document, &mut self.compare, running || busy, true) {
            CompareAction::None => {}
            CompareAction::Open(target) => self.page = super::page_for(target),
            CompareAction::Compare => {
                let started = self.validate().and_then(|spec| {
                    let run_spec = serde_json::to_value(spec).map_err(|e| e.to_string())?;
                    let study = compare::study_spec(&run_spec, &self.document, &self.compare)?;
                    self.start_command(
                        Command::StudyRun,
                        study,
                        serde_json::json!({}),
                        "Comparing: every combination is one solve; the status bar counts them.",
                    )
                });
                if let Err(error) = started {
                    self.report(Err(error));
                }
            }
        }
    }
}
