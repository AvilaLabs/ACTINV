//! The questions ACTINV answers, each leading to the page that answers it. The Start page lists them
//! so a newcomer can begin from what they want to know instead of from the menu.
use crate::nav::Page;
use eframe::egui::{self, RichText};

/// A question, a short note on what it needs or gives, and the page that answers it.
const QUESTIONS: [(&str, &str, Page); 8] = [
    (
        "What does the material become, and how fast does it cool?",
        "Activity, heat, dose and nuclides over time",
        Page::CheckRun,
    ),
    (
        "What waste class will it be?",
        "From a run's result, against the class limits",
        Page::Waste,
    ),
    (
        "Which trace elements matter, and how much of each is allowed?",
        "Ranks impurities and sets a limit for each",
        Page::Impurities,
    ),
    (
        "Does the design meet its limits once data uncertainty is counted?",
        "A certified yes or no, and what to measure next",
        Page::Certify,
    ),
    (
        "Which composition or schedule does best?",
        "Searches the ranges you give, within your limits",
        Page::Optimize,
    ),
    (
        "How do materials, spectra or irradiation lengths compare?",
        "Solves every combination and ranks them",
        Page::Compare,
    ),
    (
        "How does the answer change as one input moves?",
        "A curve over one element, the flux or a step length",
        Page::Sweep,
    ),
    (
        "Does the calculation agree with what was measured?",
        "Fits the flux to measured activities",
        Page::Assimilation,
    ),
];

/// The questions whose page `has` offers, in two columns. Returns the page of the question clicked.
pub fn show(ui: &mut egui::Ui, has: impl Fn(Page) -> bool) -> Option<Page> {
    let questions: Vec<_> = QUESTIONS.iter().filter(|q| has(q.2)).collect();
    let mut chosen = None;
    let accent = crate::visuals::accent(ui);
    ui.label(
        RichText::new("WHAT DO YOU WANT TO FIND OUT?")
            .small()
            .color(accent),
    );
    ui.group(|ui| {
        ui.set_width(ui.available_width());
        let half = questions.len().div_ceil(2).max(1);
        ui.columns(2, |columns| {
            for (i, (question, note, page)) in questions.iter().enumerate() {
                let ui = &mut columns[i / half];
                let row = ui.add(
                    egui::Button::new(RichText::new(*question).color(accent))
                        .frame(false)
                        .wrap(),
                );
                ui.weak(format!("{note} · {}", page.label()));
                ui.add_space(6.);
                if row
                    .on_hover_cursor(egui::CursorIcon::PointingHand)
                    .on_hover_text(format!("Open {}", page.label()))
                    .clicked()
                {
                    chosen = Some(*page);
                }
            }
        });
    });
    chosen
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_question_leads_to_a_page_in_the_sidebar() {
        for (_, _, page) in QUESTIONS {
            assert!(Page::ALL.contains(&page));
        }
    }
}
