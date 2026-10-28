//! Charts: the history plot and the dominant contributors of the selected step.
use crate::{
    app::{heading, Desktop},
    model,
};
use eframe::egui::{self, Color32};
use egui_plot::{Line, Plot};

impl Desktop {
    pub(super) fn charts(&mut self, ui: &mut egui::Ui) {
        let accent = crate::visuals::accent(ui);
        heading(
            ui,
            "Time history",
            "Follow activity, decay heat and inventory through time, and pick the step to inspect.",
        );
        self.result_bar(ui);
        if self.result.is_none() {
            self.no_result(ui, &["chart"]);
            return;
        }
        let result = self.result.as_ref().expect("checked");
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
            if ui.checkbox(&mut self.log_time, "Log time").changed() {
                self.reset_plot = true;
            }
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
            ui.label("Shaded band: recorded MF=33 normal interval. Confidence and coverage are in the uncertainty record on Ledger & certificate.");
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
    }
}
