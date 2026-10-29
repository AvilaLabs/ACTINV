//! Certify: the shared view sets the limits and draws the answer; this page solves the banded run
//! through `actinv decide` in the isolated worker and loads the decision it writes.
use crate::{
    app::{heading, Desktop, JobKind},
    jobs::Command,
    views::certify::{self, CertifyAction},
};
use eframe::egui;

impl Desktop {
    pub(super) fn certify_page(&mut self, ui: &mut egui::Ui) {
        heading(
            ui,
            "Certify",
            "Does the design meet its limits once the nuclear-data uncertainty is counted, and which measurement would narrow the answer most?",
        );
        if self.capture.is_some() && self.certify.decision.is_none() {
            if let Some(path) = std::env::var_os("ACTINV_GUI_CAPTURE_DECISION") {
                let path = std::path::PathBuf::from(path);
                if let Some(decision) = std::fs::read_to_string(&path)
                    .ok()
                    .and_then(|text| serde_json::from_str(&text).ok())
                {
                    self.certify.decision = Some((path, decision));
                }
            }
        }
        // load the decision the last run wrote, once
        if let Some(done) = self.command_result(Command::Decide) {
            let fresh = done.exit == 0
                && done.primary.as_ref().is_some_and(|path| {
                    self.certify
                        .decision
                        .as_ref()
                        .is_none_or(|(shown, _)| shown != path)
                });
            if fresh {
                let path = done.primary.clone().expect("checked above");
                match std::fs::read_to_string(&path)
                    .map_err(|e| e.to_string())
                    .and_then(|text| serde_json::from_str(&text).map_err(|e| e.to_string()))
                {
                    Ok(decision) => self.certify.decision = Some((path, decision)),
                    Err(error) => self.report(Err(format!("Cannot read the decision: {error}"))),
                }
            }
        }
        let running = self
            .job
            .as_ref()
            .is_some_and(|job| job.kind == JobKind::Command(Command::Decide));
        let busy = self.job.is_some() && !running;
        match certify::show(ui, &self.document, &mut self.certify, running || busy, true) {
            CertifyAction::None => {}
            CertifyAction::Open(target) => self.page = super::page_for(target),
            CertifyAction::Certify => {
                let started = self.validate().and_then(|spec| {
                    let run_spec = serde_json::to_value(spec).map_err(|e| e.to_string())?;
                    let times = certify::step_times(&self.document);
                    let decision = certify::decide_spec(
                        run_spec,
                        &self.certify.limits,
                        &times,
                        self.certify.top,
                    )?;
                    self.start_command(
                        Command::Decide,
                        decision,
                        serde_json::json!({}),
                        "Certifying: one banded solve, then every limit at its band edge.",
                    )
                });
                if let Err(error) = started {
                    self.report(Err(error));
                }
            }
        }
    }
}
