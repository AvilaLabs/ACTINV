//! Advanced JSON: the full-document editor.
use crate::{
    app::{heading, Desktop},
    model,
};
use eframe::egui;

impl Desktop {
    pub(super) fn advanced(&mut self, ui: &mut egui::Ui) {
        heading(ui,"Advanced specification","Edit every ACTINV option. Apply checks the schema; Validate checks the scientific inputs.");
        if !self.raw_dirty && self.raw_source != self.document {
            self.raw = serde_json::to_string_pretty(&self.document).unwrap_or_default();
            self.raw_source = self.document.clone();
        }
        ui.horizontal(|ui| {
            if ui
                .add_enabled(self.raw_dirty, egui::Button::new("Apply JSON edits"))
                .clicked()
            {
                match model::decode_problem(&self.raw) {
                    Ok(v) => {
                        self.document = v;
                        self.raw_dirty = false;
                        self.report(Ok("JSON edits applied.".into()));
                    }
                    Err(e) => self.report(Err(e)),
                }
            }
            if ui
                .add_enabled(self.raw_dirty, egui::Button::new("Discard JSON edits"))
                .clicked()
            {
                self.raw_dirty = false;
            }
            ui.label("Other editors pause while JSON edits are pending.");
        });
        egui::ScrollArea::both().show(ui, |ui| {
            let theme =
                egui_extras::syntax_highlighting::CodeTheme::from_memory(ui.ctx(), ui.style());
            let mut layouter = |ui: &egui::Ui, text: &dyn egui::TextBuffer, wrap_width: f32| {
                let mut job = egui_extras::syntax_highlighting::highlight(
                    ui.ctx(),
                    ui.style(),
                    &theme,
                    text.as_str(),
                    "json",
                );
                job.wrap.max_width = wrap_width;
                ui.fonts_mut(|f| f.layout_job(job))
            };
            if ui
                .add(
                    egui::TextEdit::multiline(&mut self.raw)
                        .font(egui::TextStyle::Monospace)
                        .code_editor()
                        .desired_rows(28)
                        .desired_width(f32::INFINITY)
                        .layouter(&mut layouter),
                )
                .changed()
            {
                self.raw_dirty = true;
            }
        });
    }
}
