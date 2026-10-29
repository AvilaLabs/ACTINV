//! One module per page of the desktop shell. Each adds its renderer to `Desktop`;
//! `page_body` is the single place that maps a `Page` to its renderer.
mod advanced;
mod assimilation;
mod charts;
mod check_run;
mod export;
mod impurities;
mod ledger;
mod material;
mod outputs;
mod pathways;
mod photons;
mod results;
mod schedule;
mod spectrum;
mod start;
mod summary;
mod sweep;
mod tables;
pub(super) mod waste;

use super::Desktop;
use crate::{nav::Page, views::Target};
use eframe::egui;

/// The setup page a view asked to open.
pub(super) fn page_for(target: Target) -> Page {
    match target {
        Target::Material => Page::Material,
        Target::Schedule => Page::Schedule,
        Target::Spectrum => Page::Spectrum,
        Target::Outputs => Page::Outputs,
        Target::CheckRun => Page::CheckRun,
    }
}

impl Desktop {
    /// Render the body of the current page.
    pub(super) fn page_body(&mut self, ui: &mut egui::Ui) {
        match self.page {
            Page::Start => self.start(ui),
            Page::Material => self.material(ui),
            Page::Schedule => self.schedule(ui),
            Page::Spectrum => self.spectrum(ui),
            Page::Outputs => self.outputs(ui),
            Page::CheckRun => self.check_run(ui),
            Page::Summary => self.summary_page(ui),
            Page::Charts => self.charts(ui),
            Page::Tables => self.tables(ui),
            Page::Photons => self.photons(ui),
            Page::Pathways => self.pathways(ui),
            Page::Ledger => self.ledger(ui),
            Page::Export => self.export_page(ui),
            Page::Waste => self.waste_page(ui),
            Page::Impurities => self.impurities_page(ui),
            Page::Sweep => self.sweep_page(ui),
            Page::Assimilation => self.assimilation(ui),
            Page::Advanced => self.advanced(ui),
        }
        if self.page.group() == crate::nav::Group::Setup && self.page != Page::CheckRun {
            self.step_footer(ui);
        }
    }

    /// Back and Continue between the numbered setup steps.
    fn step_footer(&mut self, ui: &mut egui::Ui) {
        let Some(number) = self.page.step() else {
            return;
        };
        ui.add_space(20.);
        ui.separator();
        ui.horizontal_wrapped(|ui| {
            if number > 1 && ui.button("< Back").clicked() {
                self.page = Page::STEPS[number - 2];
            }
            if let Some(next) = self.page.next_step() {
                if ui
                    .button(format!("Continue to {} >", next.label().to_lowercase()))
                    .clicked()
                {
                    self.page = next;
                }
            }
            ui.label(egui::RichText::new(format!("Step {number} of {}", Page::STEPS.len())).weak());
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_target_opens_its_setup_page() {
        for (target, page) in [
            (Target::Material, Page::Material),
            (Target::Schedule, Page::Schedule),
            (Target::Spectrum, Page::Spectrum),
            (Target::Outputs, Page::Outputs),
            (Target::CheckRun, Page::CheckRun),
        ] {
            assert_eq!(page_for(target), page);
            assert_eq!(page_for(target).group(), crate::nav::Group::Setup);
        }
    }
}
