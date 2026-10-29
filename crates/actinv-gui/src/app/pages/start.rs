//! Start: the home page. Four ways to begin a problem, the problem and result already open, and
//! whether the standard nuclear data are installed, since nothing runs without them.
use super::check_run::standard_data_sizes;
use crate::{
    app::{heading, Desktop, Pending, ResultAction},
    model,
    nav::Page,
    views::{check, import::ImportState},
};
use eframe::egui::{self, RichText};

/// One way to begin: a title, a sentence saying what it does, and its button. True when clicked.
fn choice(ui: &mut egui::Ui, width: f32, title: &str, text: &str, button: &str) -> egui::Response {
    ui.group(|ui| {
        ui.set_width(width);
        ui.set_min_height(150.);
        ui.vertical(|ui| {
            ui.strong(title);
            // three lines of room for every description, so the buttons line up
            let lines = 3.2 * ui.text_style_height(&egui::TextStyle::Body);
            ui.scope(|ui| {
                ui.set_min_height(lines);
                ui.label(text);
            });
            ui.add_space(4.);
            ui.button(button)
        })
        .inner
    })
    .inner
}

impl Desktop {
    pub(super) fn start(&mut self, ui: &mut egui::Ui) {
        let accent = crate::visuals::accent(ui);
        heading(
            ui,
            "Welcome to ACTINV",
            "What a material becomes under irradiation, and how it cools: activity, decay heat, dose and waste class.",
        );
        ui.label(RichText::new("START A PROBLEM").small().color(accent));
        // each card's frame adds its margin and stroke (7 a side) to the width it is given
        let gap = ui.spacing().item_spacing.x;
        let width = ((ui.available_width() - 3. * gap) / 4. - 14.).clamp(180., 300.);
        ui.horizontal_wrapped(|ui| {
            let new = choice(
                ui,
                width,
                "New problem",
                "Start from nothing: pick a material, an irradiation and a source. Nothing is filled in for you.",
                "New problem",
            );
            if new.clicked() {
                self.request(Pending::New);
            }
            let example = choice(
                ui,
                width,
                "From the iron example",
                "1 g of iron, 5 minutes at the FNS 14 MeV neutron source, then an hour of cooling. Run it as it is, or change any step.",
                "Load iron example",
            );
            if example.clicked() {
                self.request(Pending::Example);
            }
            let open = choice(
                ui,
                width,
                "Open a problem",
                "An ACTINV problem you saved, or one written by `actinv new`.",
                "Open problem…",
            );
            self.tour.mark("open", open.rect);
            if open.clicked() {
                self.request(Pending::Open);
            }
            let import = choice(
                ui,
                width,
                "Import a FISPACT-II deck",
                "Convert an input deck. A report shows what was mapped and what you still need to add.",
                "Import deck…",
            );
            self.tour.mark("import", import.rect);
            if import.clicked() {
                self.import.get_or_insert_with(ImportState::default);
            }
        });
        ui.add_space(16.);
        ui.columns(2, |columns| {
            let ui = &mut columns[0];
            ui.label(RichText::new("CURRENT PROBLEM").small().color(accent));
            ui.group(|ui| {
                ui.set_width(ui.available_width());
                ui.strong(
                    self.document["title"]
                        .as_str()
                        .unwrap_or("Untitled problem"),
                );
                let lines = check::assess(&self.document);
                for line in &lines {
                    if line.ready {
                        ui.label(&line.text);
                    } else {
                        ui.colored_label(ui.visuals().error_fg_color, &line.text);
                    }
                }
                ui.horizontal_wrapped(|ui| {
                    let first_missing = lines.iter().find(|line| !line.ready);
                    let (label, page) = match first_missing {
                        Some(line) => ("Fix what is missing >", super::page_for(line.target)),
                        None => ("Check & run >", Page::CheckRun),
                    };
                    if ui.button(label).clicked() {
                        self.page = page;
                    }
                    if ui.button("Edit from step 1").clicked() {
                        self.page = Page::Material;
                    }
                });
            });
            let ui = &mut columns[1];
            ui.label(RichText::new("RESULTS").small().color(accent));
            ui.group(|ui| {
                ui.set_width(ui.available_width());
                match &self.result {
                    Some(result) => {
                        ui.strong(&result.label);
                        if let Some(title) = result.value["spec_title"].as_str() {
                            ui.label(title);
                        }
                        ui.horizontal(|ui| {
                            if ui.button("View summary >").clicked() {
                                self.page = Page::Summary;
                            }
                        });
                    }
                    None => {
                        ui.label("Open a result saved from ACTINV, or run the problem.");
                        ui.horizontal_wrapped(|ui| {
                            if ui.button("Open result…").clicked() {
                                self.open_result(ui.ctx(), false);
                            }
                            if ui
                                .button("Try the results tutorial")
                                .on_hover_text(
                                    "A fictional nuclide with teaching values; needs no data",
                                )
                                .clicked()
                            {
                                self.tutorial();
                            }
                        });
                    }
                }
            });
        });
        ui.add_space(16.);
        ui.label(RichText::new("NUCLEAR DATA").small().color(accent));
        ui.group(|ui| {
            ui.set_width(ui.available_width());
            match self.standard_data_installed() {
                Ok(dir) => {
                    ui.label(format!("Standard data installed in {}.", dir.display()));
                }
                Err(root) => {
                    let (download, installed) = standard_data_sizes();
                    ui.label(format!(
                        "The standard neutron data are not installed under {root}. They are needed to run calculations: about {download} to download, {installed} on disk."
                    ));
                    ui.horizontal_wrapped(|ui| {
                        if ui
                            .add_enabled(
                                self.job.is_none(),
                                egui::Button::new("Download standard data…"),
                            )
                            .clicked()
                        {
                            self.download_standard_data(ui.ctx());
                        }
                        ui.label("or run");
                        ui.code("actinv data fetch");
                        ui.label("in a terminal.");
                    });
                }
            }
        });
    }

    /// Show the offline teaching result on its Summary and start the results walkthrough.
    pub(super) fn tutorial(&mut self) {
        if self.result_unsaved {
            self.result_guard = Some(ResultAction::Tutorial);
            return;
        }
        self.result = Some(model::tutorial_result());
        self.step = 0;
        self.selected.clear();
        self.comparison = None;
        self.page = Page::Summary;
        self.reset_plot = true;
        self.tour.start(true);
    }
}
