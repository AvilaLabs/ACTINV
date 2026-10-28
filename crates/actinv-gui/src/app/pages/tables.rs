//! Tables: the report tables of the loaded result. The body is the shared view; this page adds
//! the result bar and carries out what the view asks for.
use crate::{
    app::{heading, Desktop},
    views::tables::{self, TablesAction},
};
use eframe::egui;
use serde_json::json;

impl Desktop {
    pub(super) fn tables(&mut self, ui: &mut egui::Ui) {
        heading(
            ui,
            "Nuclides",
            "The inventory, totals, dominant nuclides and the other report tables for the selected step.",
        );
        self.result_bar(ui);
        if self.result.is_none() {
            self.no_result(ui, &["tables"]);
            return;
        }
        let result = self.result.as_ref().expect("checked");
        let shown = ui.scope(|ui| tables::show(ui, result, self.step, &mut self.tables));
        self.tour
            .mark("tables", shown.response.rect.intersect(ui.clip_rect()));
        match shown.inner {
            TablesAction::None => {}
            TablesAction::EnableSummary => {
                // An empty object takes every summary default; the edit joins the undo history
                // like any other, then the problem is solved again.
                self.document["summary"] = json!({});
                self.run(ui.ctx());
            }
            TablesAction::Export {
                file_name,
                contents,
            } => self.save_text(&file_name, &contents),
        }
    }
}
