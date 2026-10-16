//! Pathways: ranked first products and full production routes of the selected step. The body is
//! the shared view; this page adds the result bar and follows the links the view offers.
use super::page_for;
use crate::{
    app::{heading, Desktop},
    views::pathways::{self, PathwaysAction},
};
use eframe::egui;

impl Desktop {
    pub(super) fn pathways(&mut self, ui: &mut egui::Ui) {
        heading(
            ui,
            "Pathways",
            "How the selected nuclides are produced: first products and full routes.",
        );
        self.result_bar(ui);
        if self.result.is_none() {
            self.no_result(ui, &["pathways"]);
            return;
        }
        let result = self.result.as_ref().expect("checked");
        let shown = ui.scope(|ui| pathways::show(ui, result, self.step, &mut self.pathways));
        self.tour
            .mark("pathways", shown.response.rect.intersect(ui.clip_rect()));
        match shown.inner {
            PathwaysAction::None => {}
            PathwaysAction::Open(target) => self.page = page_for(target),
        }
    }
}
