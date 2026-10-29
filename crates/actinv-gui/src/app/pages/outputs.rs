//! Outputs: what ACTINV should compute. The body is the shared view; this page supplies the
//! file chooser, passes the view's tour anchors on, and runs the library-disagreement estimate.
use super::page_for;
use crate::options::PlatformPicker;
use crate::{
    app::{heading, job_folder, Desktop, JobKind},
    jobs::Command,
    views::{
        outputs::{self, OutputsAction},
        Marks,
    },
};
use eframe::egui;

impl Desktop {
    pub(super) fn outputs(&mut self, ui: &mut egui::Ui) {
        heading(
            ui,
            "What to compute",
            "Activity, decay heat and inventory are always computed. Add what else this problem should answer.",
        );
        self.spread_finished();
        self.outputs.spread.can_run = true;
        self.outputs.spread.running = matches!(
            self.job.as_ref().map(|job| job.kind),
            Some(JobKind::Command(Command::EvalSpread))
        );
        let mut marks = Marks::default();
        let mut picker = PlatformPicker(&*self.platform);
        let shown = ui.scope(|ui| {
            outputs::show(
                ui,
                &mut self.document,
                &mut self.outputs,
                &mut picker,
                &mut marks,
            )
        });
        for (anchor, rect) in marks.0 {
            self.tour.mark(anchor, rect);
        }
        // The walkthrough points at the first card; fall back to the page when the view did not
        // mark one.
        if !self.tour.anchors.contains_key("outputs") {
            self.tour
                .mark("outputs", shown.response.rect.intersect(ui.clip_rect()));
        }
        match shown.inner {
            OutputsAction::None => {}
            OutputsAction::Open(target) => self.page = page_for(target),
            OutputsAction::EstimateSpread => {
                if let Err(error) = self.estimate_spread() {
                    self.report(Err(error));
                }
            }
        }
    }

    /// Solve with both decay libraries (`actinv eval-spread`); `spread_finished` reads the answer.
    fn estimate_spread(&mut self) -> Result<(), String> {
        let spec = serde_json::to_value(self.validate()?).map_err(|e| e.to_string())?;
        let folder = job_folder(Command::EvalSpread);
        self.start_command_in(
            Command::EvalSpread,
            spec,
            serde_json::json!({}),
            "Solving with both decay libraries…",
            folder.clone(),
        )?;
        self.outputs.spread.note = None;
        self.spread_pending = Some(folder);
        Ok(())
    }

    /// Once the estimate stops, put its suggestion into the problem and say what it was. A capture
    /// with `ACTINV_GUI_CAPTURE_SPREAD=FILE` reads a finished estimate instead.
    fn spread_finished(&mut self) {
        if self.job.is_some() {
            return;
        }
        let path = match self.spread_pending.take() {
            Some(folder) => folder.join("eval_spread.json"),
            None if self.capture.is_some() && self.outputs.spread.note.is_none() => {
                match std::env::var("ACTINV_GUI_CAPTURE_SPREAD") {
                    Ok(file) => file.into(),
                    Err(_) => return,
                }
            }
            None => return,
        };
        let Ok(text) = std::fs::read_to_string(path) else {
            return;
        };
        let doc: serde_json::Value = match serde_json::from_str(&text) {
            Ok(doc) => doc,
            Err(error) => return self.report(Err(format!("eval_spread.json: {error}"))),
        };
        let Some((u, note)) = outputs::spread_estimate(&doc) else {
            return self.report(Err("eval_spread.json has no suggestion".into()));
        };
        outputs::set_unmodeled(&mut self.document, u);
        self.outputs.spread.note = Some(note);
    }
}
