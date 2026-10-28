//! Summary: the page a result opens on. The body is the shared view; this page applies what the
//! user picked (a step, a page, a nuclide to follow).
use crate::{
    app::{heading, Desktop},
    nav::Page,
    views::summary::{self, SummaryAction},
};
use eframe::egui;

impl Desktop {
    pub(super) fn summary_page(&mut self, ui: &mut egui::Ui) {
        heading(
            ui,
            "Summary",
            "The main answers at the selected time, how they change as the material cools, and what dominates them.",
        );
        self.result_bar(ui);
        let Some(result) = &self.result else {
            self.no_result(ui, &["summary", "export"]);
            return;
        };
        let shown = ui.scope(|ui| summary::show(ui, result, self.comparison.as_ref(), self.step));
        self.tour
            .mark("summary", shown.response.rect.intersect(ui.clip_rect()));
        match shown.inner {
            SummaryAction::None => {}
            SummaryAction::Step(step) => self.step = step,
            SummaryAction::Open(page) => self.page = page,
            SummaryAction::Follow(nuclide) => {
                self.selected = nuclide;
                self.metric = 0;
                self.reset_plot = true;
                self.page = Page::Charts;
            }
        }
    }
}
