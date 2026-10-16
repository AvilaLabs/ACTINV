//! Material: the heading and navigation around the shared material view.
use crate::{
    app::{heading, Desktop},
    nav::Page,
    views::{material, Marks},
};
use eframe::egui;

impl Desktop {
    pub(super) fn material(&mut self, ui: &mut egui::Ui) {
        heading(ui,"Define the material","Use element symbols for natural composition, or individual isotope names for enrichment.");
        let mut marks = Marks::default();
        material::show(ui, &mut self.document, &mut self.material_view, &mut marks);
        for (anchor, rect) in marks.0 {
            self.tour.mark(anchor, rect);
        }
        ui.add_space(20.);
        if ui.button("Continue to irradiation & cooling >").clicked() {
            self.page = Page::Schedule;
        }
    }
}
