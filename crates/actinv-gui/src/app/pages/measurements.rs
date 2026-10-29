//! Measurements: the shared view draws the two questions; this page runs the flux fit (`actinv
//! reverse` or `reverse-qualified`) through the job layer, reads what it wrote once it stops, puts
//! a fitted flux into the problem, and folds measured values into the loaded result's bands in
//! process (the same path `actinv assimilate` runs).
use crate::{
    app::{heading, job_folder, Desktop, JobKind},
    jobs::Command,
    model::ResultDocument,
    nav::Page,
    platform::Dialog,
    views::{
        certify,
        measurements::{self, Context, MeasurementsAction},
    },
};
use eframe::egui;
use serde_json::{json, Value};
use std::path::PathBuf;

impl Desktop {
    pub(super) fn measurements_page(&mut self, ui: &mut egui::Ui) {
        heading(
            ui,
            "Measurements",
            "Bring what you measured into the calculation: find the flux that explains measured activities, or narrow the result's uncertainty bands with a measured value.",
        );
        if self.job.is_none() {
            if let Some((command, folder)) = self.measure_pending.take() {
                let qualified = command == Command::ReverseQualified;
                let path = folder.join(if qualified {
                    "reverse_qualified.ndjson"
                } else {
                    "reverse.json"
                });
                if path.is_file() {
                    match measurements::read_fit(&path, qualified) {
                        Ok(fit) => self.measurements.fit = Some(fit),
                        Err(error) => self.report(Err(error)),
                    }
                }
            }
        }
        self.measurements_capture();
        let steps = certify::step_times(&self.document);
        let running = matches!(
            self.job.as_ref().map(|job| job.kind),
            Some(JobKind::Command(
                Command::Reverse | Command::ReverseQualified
            ))
        );
        let context = Context {
            steps: &steps,
            result: self.result.as_ref(),
            covariance: self.catalog_installed(measurements::COVARIANCE),
            running,
            busy: self.job.is_some(),
        };
        let done = match measurements::show(ui, &mut self.measurements, &context) {
            MeasurementsAction::None => Ok(()),
            MeasurementsAction::OpenData => {
                self.page = Page::Data;
                Ok(())
            }
            MeasurementsAction::OpenResult => {
                self.open_result(ui.ctx(), false);
                Ok(())
            }
            MeasurementsAction::Fit => self.fit_flux(),
            MeasurementsAction::UseFit => {
                if let Some(fit) = &self.measurements.fit {
                    self.document = measurements::use_fit(&self.document, fit);
                    self.report(Ok(
                        "The fitted flux is in the schedule now; run it from Check & run, or Undo edit to go back."
                            .into(),
                    ));
                }
                Ok(())
            }
            MeasurementsAction::LoadMeasurements => self.load_measurements(),
            MeasurementsAction::SaveMeasurements => self.save_measurements(),
            MeasurementsAction::LoadAssay => self.load_assay(),
            MeasurementsAction::Preview => self.preview_fold(),
            MeasurementsAction::Apply => {
                self.apply_fold();
                Ok(())
            }
        };
        if let Err(error) = done {
            self.report(Err(error));
        }
    }

    fn fit_flux(&mut self) -> Result<(), String> {
        let state = &self.measurements;
        let doc = measurements::measurements_doc(state)?;
        let spec = serde_json::to_value(self.validate()?).map_err(|e| e.to_string())?;
        let problem = measurements::fit_problem(&spec, state);
        let (command, args) = if state.qualified {
            (Command::ReverseQualified, json!({"measurements": doc}))
        } else {
            (
                Command::Reverse,
                json!({"measurements": doc, "segments": state.segments}),
            )
        };
        let folder: PathBuf = job_folder(command);
        self.start_command_in(
            command,
            problem,
            args,
            "Fitting the flux to the measurements…",
            folder.clone(),
        )?;
        self.measurements.fit = None;
        self.measure_pending = Some((command, folder));
        Ok(())
    }

    fn load_measurements(&mut self) -> Result<(), String> {
        let Some(path) = self.platform.pick_file(
            Dialog::new()
                .title("Load measurements")
                .filter("Measurements", &["json"]),
        ) else {
            return Ok(());
        };
        let text = std::fs::read_to_string(&path)
            .map_err(|e| format!("cannot read {}: {e}", path.display()))?;
        let doc: Value = serde_json::from_str(&text).map_err(|e| e.to_string())?;
        self.measurements.rows = measurements::rows_from(&doc)?;
        self.measurements.fit = None;
        Ok(())
    }

    fn save_measurements(&mut self) -> Result<(), String> {
        let doc = measurements::measurements_doc(&self.measurements)?;
        let Some(path) = self.platform.save_file(
            Dialog::new()
                .title("Save measurements")
                .filter("Measurements", &["json"])
                .file_name("measurements.json"),
        ) else {
            return Ok(());
        };
        crate::model::write_json(&path, &doc)?;
        self.report(Ok(format!(
            "Saved the measurements to {}; `actinv reverse PROBLEM.json {}` fits them on the command line.",
            path.display(),
            path.file_name().unwrap_or_default().to_string_lossy()
        )));
        Ok(())
    }

    fn load_assay(&mut self) -> Result<(), String> {
        let Some(path) = self.platform.pick_file(
            Dialog::new()
                .title("Load a measurement file")
                .filter("Measurement", &["json"]),
        ) else {
            return Ok(());
        };
        let text = std::fs::read_to_string(&path)
            .map_err(|e| format!("cannot read {}: {e}", path.display()))?;
        let doc: Value = serde_json::from_str(&text).map_err(|e| e.to_string())?;
        if doc["schema"] != "actinv-assay-1" {
            return Err("This is not a measurement file (actinv-assay-1).".into());
        }
        let name = path
            .file_name()
            .unwrap_or_default()
            .to_string_lossy()
            .into_owned();
        self.measurements.assay_file = Some((name, doc));
        self.measurements.pending = None;
        Ok(())
    }

    fn preview_fold(&mut self) -> Result<(), String> {
        let result = self.result.as_ref().ok_or("Open a result first.")?;
        let assay = measurements::assay_doc(&self.measurements, result)?;
        self.measurements.pending = None;
        let fused = actinv_cli::assimilate::fuse_document(&result.value, &assay, "workbench")?;
        self.measurements.pending = Some(fused);
        Ok(())
    }

    fn apply_fold(&mut self) {
        let Some((_, updated)) = self.measurements.pending.take() else {
            return;
        };
        let label = format!(
            "{} · measurement folded in",
            self.result
                .as_ref()
                .map(|r| r.label.as_str())
                .unwrap_or_default()
        );
        match ResultDocument::parse(updated, label) {
            Ok(doc) => {
                self.result = Some(doc);
                self.result_unsaved = true;
                self.report(Ok(
                    "The result's bands now carry the measurement; Save result keeps it.".into(),
                ));
            }
            Err(error) => self.report(Err(format!("The folded result is not valid: {error}"))),
        }
    }

    /// Screenshots: `ACTINV_GUI_CAPTURE_MEASURE=narrow` opens the second question with a reading;
    /// `=ROWS.json,FIT` loads measurements and a fit (`.ndjson` is a qualified fit).
    fn measurements_capture(&mut self) {
        if self.capture.is_none() || self.measurements.fit.is_some() {
            return;
        }
        let Ok(spec) = std::env::var("ACTINV_GUI_CAPTURE_MEASURE") else {
            return;
        };
        if spec == "narrow" {
            if self.measurements.question == measurements::Question::Narrow {
                return;
            }
            self.measurements.question = measurements::Question::Narrow;
            if let Some(result) = &self.result {
                self.measurements.reading_step = 1.min(result.steps().len().saturating_sub(1));
                let banded = measurements::banded(result, self.measurements.reading_step);
                if let Some(response) = banded.first() {
                    let nominal = result.steps()[self.measurements.reading_step]["uncertainty"]
                        ["responses"][response]["nominal"]
                        .as_f64()
                        .unwrap_or(1.);
                    self.measurements.readings = vec![measurements::Reading {
                        response: response.clone(),
                        value: nominal * 1.05,
                        sigma: nominal * 0.03,
                    }];
                    let _ = self.preview_fold();
                }
            }
            return;
        }
        let Some((rows, fit)) = spec.split_once(',') else {
            return;
        };
        if let Ok(doc) = std::fs::read_to_string(rows)
            .map_err(|e| e.to_string())
            .and_then(|t| serde_json::from_str::<Value>(&t).map_err(|e| e.to_string()))
        {
            self.measurements.rows = measurements::rows_from(&doc).unwrap_or_default();
        }
        let qualified = fit.ends_with(".ndjson");
        self.measurements.qualified = qualified;
        self.measurements.segments = qualified;
        self.measurements.fit = measurements::read_fit(std::path::Path::new(fit), qualified).ok();
    }
}
