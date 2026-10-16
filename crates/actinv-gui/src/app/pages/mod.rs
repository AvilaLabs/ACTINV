//! One module per page of the desktop shell. Each adds its renderer to `Desktop`;
//! `page_body` is the single place that maps a `Page` to its renderer.
mod advanced;
mod assimilation;
mod details;
mod ledger;
mod material;
mod overview;
mod results;
mod schedule;
mod spectrum;
mod sweep;

use super::Desktop;
use crate::nav::Page;
use eframe::egui;

impl Desktop {
    /// Render the body of the current page.
    pub(super) fn page_body(&mut self, ui: &mut egui::Ui) {
        match self.page {
            Page::Overview => self.setup(ui),
            Page::Material => self.material(ui),
            Page::Schedule => self.schedule(ui),
            Page::Spectrum => self.spectrum(ui),
            Page::Results => self.results(ui),
            Page::Details => self.details(ui),
            Page::Ledger => self.ledger(ui),
            Page::Advanced => self.advanced(ui),
        }
    }
}
