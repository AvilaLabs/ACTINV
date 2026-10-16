//! Results: history chart, dominant contributors and the inventory table.
use crate::{
    app::{heading, Desktop, BLUE},
    model,
};
use eframe::egui::{self, Color32, RichText};
use egui_extras::{Column, TableBuilder};
use egui_plot::{Line, Plot};
use serde_json::Value;

impl Desktop {
    pub(super) fn results(&mut self, ui: &mut egui::Ui) {
        let accent = crate::visuals::accent(ui);
        heading(
            ui,
            "Explore the inventory",
            "Select a time and a nuclide to connect the table with its history.",
        );
        ui.horizontal(|ui| {
            let open = ui.button("Open result");
            self.tour.mark("result_open", open.rect);
            if open.clicked() {
                self.open_result(ui.ctx(), false);
            }
            if ui
                .add_enabled(self.result.is_some(), egui::Button::new("Add comparison"))
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
            let r=ui.group(|ui|{ui.add_space(30.);ui.heading("Your results will appear here");ui.label("Run a calculation or open a result JSON to explore activity, decay heat, and nuclide inventory.");ui.add_space(30.);});
            self.tour.mark("chart", r.response.rect);
            self.tour.mark("inventory", r.response.rect);
            return;
        };
        ui.label(RichText::new(&result.label).strong());
        if self.result_unsaved {
            ui.colored_label(
                BLUE,
                "Unsaved result — Export JSON preserves every step and its evidence.",
            );
        }
        ui.label("Results describe the inputs at the time of the calculation. Editing the problem does not update this result.");
        if let Some(title) = result.value["spec_title"].as_str() {
            ui.label(title);
        }
        if let Some(other) = &self.comparison {
            ui.label(format!("Comparison: {} · absolute physical time; normalization is as recorded in each result",other.label));
        }
        ui.horizontal(|ui| {
            for (i, label) in ["Activity (Bq/g)", "Total heat (W/g)", "Inventory (atoms/g)"]
                .iter()
                .enumerate()
            {
                if ui.selectable_value(&mut self.metric, i, *label).changed() {
                    self.reset_plot = true;
                }
            }
            if ui.small_button("Reset view").clicked() {
                self.reset_plot = true;
            }
        });
        ui.horizontal(|ui| {
            ui.label("Computed step");
            if ui
                .checkbox(&mut self.log_time, "Logarithmic time")
                .changed()
            {
                self.reset_plot = true;
            }
            ui.add(
                egui::Slider::new(&mut self.step, 0..=result.steps().len() - 1)
                    .custom_formatter(|v, _| format!("{}", v as usize + 1)),
            );
            ui.label(format!(
                "t = {:.5e} s",
                model::number(&result.steps()[self.step]["t_s"])
            ));
            if !self.selected.is_empty() {
                ui.label(format!("Selected: {}", self.selected));
                if ui.small_button("Clear selection").clicked() {
                    self.selected.clear();
                    self.reset_plot = true;
                }
            }
        });
        let log_time = self.log_time;
        let plot_time = |t: f64| if log_time { t.log10() } else { t };
        if log_time {
            ui.label("Time axis shows log10(seconds); zero-time points are omitted. Tables and exports retain physical time.");
        }
        let primary: Vec<[f64; 2]> = result
            .steps()
            .iter()
            .filter(|s| !log_time || model::number(&s["t_s"]) > 0.)
            .map(|s| {
                [
                    plot_time(model::number(&s["t_s"])),
                    model::metric(s, self.metric, &self.selected),
                ]
            })
            .collect();
        let mut plot = Plot::new("history")
            .height(240.)
            .allow_scroll(false)
            .x_axis_label(if log_time {
                "log10(Time / s)"
            } else {
                "Time (s)"
            })
            .legend(egui_plot::Legend::default());
        if self.reset_plot {
            plot = plot.reset();
            self.reset_plot = false;
        }
        let response_key = match self.metric {
            1 => Some("heat.total".to_owned()),
            0 if !self.selected.is_empty() => Some(format!("activity:{}", self.selected)),
            _ => None,
        };
        let band = response_key.as_ref().and_then(|key| {
            result
                .steps()
                .iter()
                .filter(|s| !log_time || model::number(&s["t_s"]) > 0.)
                .map(|s| {
                    let b = &s["uncertainty"]["responses"][key]["normal_interval"];
                    Some((
                        plot_time(s["t_s"].as_f64()?),
                        b[0].as_f64()?,
                        b[1].as_f64()?,
                    ))
                })
                .collect::<Option<Vec<_>>>()
        });
        if band.is_some() {
            ui.label("Shaded band: recorded MF=33 normal interval. Confidence and coverage are in Spectra & pathways.");
        }
        let chart = plot.show(ui, |p| {
            if let Some(band) = &band {
                let xs: Vec<_> = band.iter().map(|b| b.0).collect();
                let lower: Vec<_> = band.iter().map(|b| b.1).collect();
                let upper: Vec<_> = band.iter().map(|b| b.2).collect();
                p.add(
                    egui_plot::FilledArea::new("MF=33 normal interval", &xs, &lower, &upper)
                        .fill_color(accent.gamma_multiply(0.2)),
                );
            }

            p.line(
                Line::new(
                    if self.selected.is_empty() || self.metric == 1 {
                        "Whole material"
                    } else {
                        &self.selected
                    },
                    primary,
                )
                .color(accent)
                .width(2.),
            );
            if let Some(other) = &self.comparison {
                let points: Vec<[f64; 2]> = other
                    .steps()
                    .iter()
                    .filter(|s| !log_time || model::number(&s["t_s"]) > 0.)
                    .map(|s| {
                        [
                            plot_time(model::number(&s["t_s"])),
                            model::metric(s, self.metric, &self.selected),
                        ]
                    })
                    .collect();
                p.line(
                    Line::new(&other.label, points)
                        .color(Color32::from_rgb(192, 96, 25))
                        .style(egui_plot::LineStyle::dashed_dense()),
                );
            }
            if !log_time || model::number(&result.steps()[self.step]["t_s"]) > 0. {
                p.vline(
                    egui_plot::VLine::new(
                        "Selected step",
                        plot_time(model::number(&result.steps()[self.step]["t_s"])),
                    )
                    .color(Color32::GRAY),
                );
            }
            if p.response().clicked() {
                p.pointer_coordinate().map(|c| c.x)
            } else {
                None
            }
        });
        self.tour.mark("chart", chart.response.rect);
        if let Some(time) = chart.inner {
            self.step = result
                .steps()
                .iter()
                .enumerate()
                .filter(|(_, s)| !log_time || model::number(&s["t_s"]) > 0.)
                .min_by(|(_, a), (_, b)| {
                    (plot_time(model::number(&a["t_s"])) - time)
                        .abs()
                        .total_cmp(&(plot_time(model::number(&b["t_s"])) - time).abs())
                })
                .map(|(i, _)| i)
                .unwrap_or(0);
        }
        let step = &result.steps()[self.step];
        egui::CollapsingHeader::new("Which nuclides dominate activity?")
            .open(self.capture.as_ref().map(|_| true))
            .show(ui, |ui| {
                crate::visuals::contributors(ui, step, &mut self.selected)
            });
        ui.horizontal(|ui| {
            ui.label(format!("Activity  {:.4e} Bq/g", model::metric(step, 0, "")));
            ui.separator();
            ui.label(format!("Heat  {:.4e} W/g", model::metric(step, 1, "")));
            ui.separator();
            ui.label(format!(
                "Inventory  {:.4e} atoms/g",
                model::metric(step, 2, "")
            ));
        });
        let table = ui.scope(|ui| {
            ui.horizontal(|ui| {
                ui.add(egui::TextEdit::singleline(&mut self.filter).hint_text("Filter nuclides…"));
                ui.checkbox(&mut self.descending, "Highest activity first");
            });
            // This runs every frame: lower-case the filter once and compare borrowed names.
            let filter = self.filter.to_lowercase();
            let mut rows: Vec<_> = step["inventory"]
                .as_array()
                .map(|a| {
                    a.iter()
                        .filter(|r| {
                            r["nuclide"]
                                .as_str()
                                .unwrap_or("")
                                .to_lowercase()
                                .contains(&filter)
                        })
                        .collect()
                })
                .unwrap_or_default();
            rows.sort_by(|a, b| {
                fn name(v: &Value) -> &str {
                    v["nuclide"].as_str().unwrap_or("")
                }
                if self.descending {
                    model::number(&step["activity_Bq_per_g"][name(b)])
                        .total_cmp(&model::number(&step["activity_Bq_per_g"][name(a)]))
                } else {
                    name(a).cmp(name(b))
                }
            });
            TableBuilder::new(ui)
                .id_salt("inventory-table")
                .striped(true)
                .resizable(true)
                .column(Column::initial(220.).at_least(120.))
                .columns(Column::remainder().at_least(165.), 2)
                .max_scroll_height(250.)
                .header(28., |mut h| {
                    for label in ["Nuclide", "Atoms / g", "Activity (Bq / g)"] {
                        h.col(|ui| {
                            ui.strong(label);
                        });
                    }
                })
                .body(|body| {
                    body.rows(27., rows.len(), |mut row| {
                        let v = rows[row.index()];
                        let name = v["nuclide"].as_str().unwrap_or("");
                        row.set_selected(self.selected == name);
                        row.col(|ui| {
                            if ui.selectable_label(self.selected == name, name).clicked() {
                                self.selected = name.into();
                                self.reset_plot = true;
                            }
                        });
                        row.col(|ui| {
                            ui.monospace(format!("{:.6e}", model::number(&v["atoms_per_g"])));
                        });
                        row.col(|ui| {
                            ui.monospace(format!(
                                "{:.6e}",
                                model::number(&step["activity_Bq_per_g"][name])
                            ));
                        });
                    });
                });
        });
        self.tour
            .mark("inventory", table.response.rect.intersect(ui.clip_rect()));
        self.sweep_panel(ui);
    }
}
