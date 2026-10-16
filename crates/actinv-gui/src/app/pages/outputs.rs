//! Outputs: what ACTINV should compute. The body is the shared view; this page supplies the
//! file chooser and passes the view's tour anchors on.
use super::page_for;
use crate::options::PlatformPicker;
use crate::{
    app::{heading, Desktop},
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
            "Outputs",
            "Choose what ACTINV should compute besides the inventory, activity and decay heat.",
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
        }
    }
}
