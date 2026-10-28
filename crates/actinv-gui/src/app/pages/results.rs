//! The result bar shared by every RESULTS page, and the empty state those pages show without a
//! result.
use crate::{
    app::{Desktop, BLUE},
    model,
    nav::Page,
};
use eframe::egui::{self, RichText};

impl Desktop {
    /// File actions and the computed-step slider. One bar serves every RESULTS page, so the step
    /// chosen on one page is the step every other page shows.
    pub(super) fn result_bar(&mut self, ui: &mut egui::Ui) {
        ui.horizontal_wrapped(|ui| {
            let open = ui.button("Open result");
            self.tour.mark("result_open", open.rect);
            if open.clicked() {
                self.open_result(ui.ctx(), false);
            }
            if ui
                .add_enabled(self.result.is_some(), egui::Button::new("Compare with…"))
                .clicked()
            {
                self.open_result(ui.ctx(), true);
            }
            if self.comparison.is_some() && ui.button("Remove comparison").clicked() {
                self.comparison = None;
            }
            let export = ui.add_enabled(self.result.is_some(), egui::Button::new("Export JSON"));
            self.tour.mark("export", export.rect);
            if export.clicked() {
                self.export(false);
            }
            if ui
                .add_enabled(
                    self.result.is_some(),
                    egui::Button::new("Export inventory CSV"),
                )
                .clicked()
            {
                self.export(true);
            }
        });
        let Some(result) = &self.result else {
            return;
        };
        let steps = result.steps().len();
        self.step = self.step.min(steps.saturating_sub(1));
        // one line names the result: its title, then the file it came from
        ui.horizontal_wrapped(|ui| {
            match result.value["spec_title"].as_str() {
                Some(title) => {
                    ui.label(RichText::new(title).strong());
                    ui.label(RichText::new(format!("· {}", result.label)).weak());
                }
                None => {
                    ui.label(RichText::new(&result.label).strong());
                }
            }
            if self.result_unsaved {
                ui.colored_label(BLUE, "· not saved yet")
                    .on_hover_text("Export JSON preserves every step and its evidence.");
            }
        })
        .response
        .on_hover_text("Results describe the inputs at the time of the calculation. Editing the problem does not update this result.");
        if let Some(other) = &self.comparison {
            ui.label(format!("Comparison: {} · absolute physical time; normalization is as recorded in each result",other.label));
        }
        ui.horizontal(|ui| {
            ui.label("Computed step");
            ui.add(
                egui::Slider::new(&mut self.step, 0..=steps - 1)
                    .custom_formatter(|v, _| format!("{}", v as usize + 1)),
            );
            // the time in words; the exact time on hover
            ui.label(format!(
                "of {steps} · {}",
                crate::views::summary::when(result, self.step)
            ))
            .on_hover_text(format!(
                "t = {:.6e} s from the start of the schedule",
                model::number(&result.steps()[self.step]["t_s"])
            ));
        });
        ui.add_space(8.);
    }

    /// The card a RESULTS page shows while no result is loaded. It carries the page's tour
    /// anchors so a walkthrough still has something to point at.
    pub(super) fn no_result(&mut self, ui: &mut egui::Ui, anchors: &[&'static str]) {
        let card = ui.group(|ui| {
            ui.add_space(30.);
            ui.heading("Your results will appear here");
            ui.label("Run a calculation or open a saved result to explore activity, decay heat, dose and the nuclide inventory.");
            ui.horizontal_wrapped(|ui| {
                if ui.button("Open result…").clicked() {
                    self.open_result(ui.ctx(), false);
                }
                if ui.button("Try the results tutorial").clicked() {
                    self.tutorial();
                }
                if ui.button("Check & run >").clicked() {
                    self.page = Page::CheckRun;
                }
            });
            ui.add_space(30.);
        });
        for &anchor in anchors {
            self.tour.mark(anchor, card.response.rect);
        }
    }
}
