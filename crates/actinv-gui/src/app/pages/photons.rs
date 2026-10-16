//! Photons & dose: decay photon groups, dose rates and bremsstrahlung of the selected step. The
//! body is the shared view; this page adds the result bar and carries out what the view asks for.
use super::page_for;
use crate::{
    app::{heading, Desktop},
    views::photons::{self, PhotonsAction},
};
use eframe::egui;

impl Desktop {
    pub(super) fn photons(&mut self, ui: &mut egui::Ui) {
        heading(
            ui,
            "Photons & dose",
            "Decay photon groups and dose rates for the selected step, and dose against cooling time.",
        );
        self.result_bar(ui);
        if self.result.is_none() {
            self.no_result(ui, &["photons"]);
            return;
        }
        let result = self.result.as_ref().expect("checked");
        let shown = ui.scope(|ui| photons::show(ui, result, self.step, &mut self.photons));
        self.tour
            .mark("photons", shown.response.rect.intersect(ui.clip_rect()));
        match shown.inner {
            PhotonsAction::None => {}
            PhotonsAction::Open(target) => self.page = page_for(target),
            PhotonsAction::Export {
                file_name,
                contents,
            } => self.save_text(&file_name, &contents),
        }
    }
}
