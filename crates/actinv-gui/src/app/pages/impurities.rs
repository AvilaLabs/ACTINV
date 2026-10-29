//! Impurities: the shared view sets up the screen and draws its result; this page runs the screen
//! in the isolated worker and reads and writes the screen and result files.
use crate::{
    app::{heading, Desktop, Job, JobKind},
    platform::Dialog,
    views::impurities::{self, Context, ImpuritiesAction},
};
use eframe::egui;
use serde_json::Value;
use std::time::Instant;

impl Desktop {
    pub(super) fn impurities_page(&mut self, ui: &mut egui::Ui) {
        heading(
            ui,
            "Impurities",
            "Which trace elements drive activity, heat, dose or waste class, the worst case over their specified ranges, and how far each may rise before a limit is reached.",
        );
        if self.capture.is_some() && self.impurities.result.is_none() {
            if let Some(path) = std::env::var_os("ACTINV_GUI_CAPTURE_SCREEN") {
                self.install_screen(std::path::Path::new(&path));
            }
        }
        let running = self
            .job
            .as_ref()
            .is_some_and(|job| job.kind == JobKind::Screen);
        let context = Context {
            can_screen: self.job.is_none() || running,
            running,
        };
        match impurities::show(ui, &self.document, &mut self.impurities, &context) {
            ImpuritiesAction::None => {}
            ImpuritiesAction::Screen => self.screen(),
            ImpuritiesAction::SaveScreen => self.save_screen(),
            ImpuritiesAction::OpenResult => {
                if let Some(path) = self
                    .platform
                    .pick_file(Dialog::new().filter("Impurity screen result", &["json"]))
                {
                    self.install_screen(&path);
                }
            }
            ImpuritiesAction::SaveResult => self.save_screen_result(),
            ImpuritiesAction::GoTo(target) => self.page = super::page_for(target),
        }
    }

    /// The screen document for the open problem, its base spec embedded with resolved paths.
    fn screen_document(&self) -> Result<Value, String> {
        let spec = self.validate()?;
        let base = serde_json::to_value(&spec).map_err(|e| e.to_string())?;
        impurities::screen_document(
            &self.impurities,
            &self.document,
            impurities::without_nulls(base),
        )
    }

    fn screen(&mut self) {
        let screen = match self.screen_document() {
            Ok(s) => s,
            Err(e) => {
                self.impurities.message = Some(Err(e.clone()));
                self.report(Err(e));
                return;
            }
        };
        let cache = std::env::temp_dir().join(format!(
            "actinv-gui-screen-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_nanos()
        ));
        match crate::worker::spawn_impurities(&screen, cache) {
            Ok(handle) => {
                self.job = Some(Job {
                    rx: None,
                    handle: Some(handle),
                    started: Instant::now(),
                    kind: JobKind::Screen,
                    cancelled: false,
                });
                self.impurities.message = None;
                self.report(Ok(
                    "Screening impurities: one calculation per element, then every reported composition is re-solved.".into(),
                ));
            }
            Err(e) => self.report(Err(e)),
        }
    }

    /// Install a finished or loaded screen result and show it.
    pub(in crate::app) fn screen_finished(&mut self, label: String, value: Value) {
        match impurities::check_result(&value) {
            Ok(()) => {
                let verified = value["verification"]["verified"].as_bool();
                self.impurities.result = Some((label, value));
                self.impurities.showing_result = true;
                self.page = crate::nav::Page::Impurities;
                self.report(match verified {
                    Some(false) => Err("Impurity screen finished, but its verification failed: see the result before relying on it.".into()),
                    _ => Ok("Impurity screen ready. Save result… keeps it.".into()),
                });
            }
            Err(e) => self.report(Err(e)),
        }
    }

    fn install_screen(&mut self, path: &std::path::Path) {
        let loaded = std::fs::read_to_string(path)
            .map_err(|e| format!("cannot read {}: {e}", path.display()))
            .and_then(|s| serde_json::from_str::<Value>(&s).map_err(|e| e.to_string()));
        match loaded {
            Ok(value) => {
                let label = path.file_name().map_or_else(
                    || "screen result".into(),
                    |n| n.to_string_lossy().into_owned(),
                );
                self.screen_finished(label, value);
            }
            Err(e) => self.report(Err(e)),
        }
    }

    fn save_screen(&mut self) {
        let screen = match self.screen_document() {
            Ok(s) => s,
            Err(e) => {
                self.impurities.message = Some(Err(e.clone()));
                return;
            }
        };
        let Some(path) = self.platform.save_file(
            Dialog::new()
                .filter("Impurity screen", &["json"])
                .file_name("impurities.json"),
        ) else {
            return;
        };
        let written = serde_json::to_string_pretty(&screen)
            .map_err(|e| e.to_string())
            .and_then(|text| {
                std::fs::write(&path, format!("{text}\n"))
                    .map_err(|e| format!("cannot write {}: {e}", path.display()))
            })
            .map(|()| {
                format!(
                    "Saved the screen. Run it with: actinv impurities {} result.json",
                    path.display()
                )
            });
        self.impurities.message = Some(written.clone());
        self.report(written);
    }

    fn save_screen_result(&mut self) {
        let Some((label, value)) = &self.impurities.result else {
            return;
        };
        let name = if label.ends_with(".json") {
            label.clone()
        } else {
            "impurities-result.json".into()
        };
        let Some(path) = self.platform.save_file(
            Dialog::new()
                .filter("Impurity screen result", &["json"])
                .file_name(&name),
        ) else {
            return;
        };
        let written = serde_json::to_string_pretty(value)
            .map_err(|e| e.to_string())
            .and_then(|text| {
                std::fs::write(&path, format!("{text}\n"))
                    .map_err(|e| format!("cannot write {}: {e}", path.display()))
            })
            .map(|()| format!("Saved the screen result to {}.", path.display()));
        self.report(written);
    }
}
