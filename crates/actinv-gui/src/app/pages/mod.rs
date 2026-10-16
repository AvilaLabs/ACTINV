//! One module per page of the desktop shell. Each adds its renderer to `Desktop`;
//! `page_body` is the single place that maps a `Page` to its renderer.
mod advanced;
mod assimilation;
mod charts;
mod ledger;
mod material;
mod outputs;
mod overview;
mod pathways;
mod photons;
mod results;
mod schedule;
mod spectrum;
mod sweep;
mod tables;

use super::Desktop;
use crate::{nav::Page, views::Target};
use eframe::egui;

/// The setup page a view asked to open.
pub(super) fn page_for(target: Target) -> Page {
    match target {
        Target::Overview => Page::Overview,
        Target::Material => Page::Material,
        Target::Schedule => Page::Schedule,
        Target::Spectrum => Page::Spectrum,
        Target::Outputs => Page::Outputs,
    }
}

impl Desktop {
    /// Render the body of the current page.
    pub(super) fn page_body(&mut self, ui: &mut egui::Ui) {
        match self.page {
            Page::Overview => self.setup(ui),
            Page::Material => self.material(ui),
            Page::Schedule => self.schedule(ui),
            Page::Spectrum => self.spectrum(ui),
            Page::Outputs => self.outputs(ui),
            Page::Charts => self.charts(ui),
            Page::Tables => self.tables(ui),
            Page::Photons => self.photons(ui),
            Page::Pathways => self.pathways(ui),
            Page::Ledger => self.ledger(ui),
            Page::Sweep => self.sweep_page(ui),
            Page::Assimilation => self.assimilation(ui),
            Page::Advanced => self.advanced(ui),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_target_opens_its_setup_page() {
        for (target, page) in [
            (Target::Overview, Page::Overview),
            (Target::Material, Page::Material),
            (Target::Schedule, Page::Schedule),
            (Target::Spectrum, Page::Spectrum),
            (Target::Outputs, Page::Outputs),
        ] {
            assert_eq!(page_for(target), page);
            assert_eq!(page_for(target).group(), crate::nav::Group::Setup);
        }
    }
}
