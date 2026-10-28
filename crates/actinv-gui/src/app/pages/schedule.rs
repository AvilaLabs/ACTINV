//! Irradiation & cooling: the heading and navigation around the shared step editor.
use super::page_for;
use crate::{
    app::{heading, Desktop},
    options::PlatformPicker,
    views::{
        steps::{self, StepsAction},
        Marks,
    },
};
use eframe::egui;

impl Desktop {
    pub(super) fn schedule(&mut self, ui: &mut egui::Ui) {
        heading(ui,"Irradiation & cooling","Durations accept units such as 5 min, 2 h, or 10 y. Each step is driven by a flux multiplier, a wall loading or a power; a zero flux multiplier is cooling.");
        let mut marks = Marks::default();
        let mut picker = PlatformPicker(&*self.platform);
        let action = steps::show(
            ui,
            &mut self.document,
            &mut self.steps,
            &mut picker,
            &mut marks,
        );
        for (anchor, rect) in marks.0 {
            self.tour.mark(anchor, rect);
        }
        if let StepsAction::Open(target) = action {
            self.page = page_for(target);
        }
    }
}
